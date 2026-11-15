"""
Download a release asset and apply it to a tool's install directory.

This module has no GTK dependency so it can run in a worker thread and be
tested on its own.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .github import DownloadError, download_asset, match_asset

MARKER_NAME = ".just-release.json"
STRATEGIES = ("smart-sync", "symlink", "wipe")
HOOK_TIMEOUT_SECONDS = 600
TAR_SUFFIXES = (
    ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar.zst"
)


class InstallError(Exception):
    pass


class UnmanagedDestinationError(InstallError):
    """
    The `wipe` strategy was asked to clear a non-empty directory that
    just-release did not create. The caller must confirm before retrying with
    allow_wipe_unmanaged=True.
    """

    def __init__(self, path):
        super().__init__(f"{path} already contains files that just-release did not install.")
        self.path = path


@dataclass
class InstallResult:
    version: str
    asset_name: str
    install_path: Path
    warnings: list = field(default_factory=list)


def install_release(tool_id, tool, release, *, token=None, allow_wipe_unmanaged=False,
                    progress=None, downloader=download_asset) -> InstallResult:
    """
    Install `release` (as returned by github.get_latest_release) for `tool`
    (one entry of the manifest's `tools` mapping).

    `progress`, if given, is called with (stage_text, fraction_or_None).
    """
    def report(stage, fraction=None):
        if progress:
            progress(stage, fraction)

    version = release.get("tag_name") or ""
    if not version:
        raise InstallError("The release has no tag name.")

    strategy = tool.get("strategy") or "smart-sync"
    if strategy not in STRATEGIES:
        raise InstallError(f"Unknown update strategy {strategy!r}.")

    install_to = (tool.get("install_to") or "").strip()
    if not install_to:
        raise InstallError("No install directory is configured.")
    dest = Path(install_to).expanduser().resolve()

    pattern = tool.get("asset_pattern") or ""
    asset = match_asset(release.get("assets") or [], pattern)
    if not asset:
        raise InstallError(f"No asset in release {version} matches {pattern!r}.")

    if strategy == "wipe":
        _check_wipe_allowed(dest, allow_wipe_unmanaged)

    hooks = tool.get("hooks") or {}
    hook_env = {
        "JUST_RELEASE_TOOL": str(tool_id),
        "JUST_RELEASE_NEW_VERSION": version,
        "JUST_RELEASE_OLD_VERSION": tool.get("current_version") or "",
        "JUST_RELEASE_INSTALL_DIR": str(dest),
    }

    work_dir = _make_work_dir(dest)
    try:
        archive = work_dir / "download"
        report("Downloading", 0.0)

        def on_bytes(done, total):
            report("Downloading", done / total if total else None)

        try:
            sha256 = downloader(asset, archive, token=token, progress=on_bytes)
        except DownloadError as e:
            raise InstallError(str(e)) from e
        _verify_digest(asset, sha256)

        report("Extracting")
        staging = work_dir / "extract"
        staging.mkdir()
        _extract(archive, asset["name"], staging)
        payload = _payload_root(staging)

        report("Running pre-update hook")
        _run_hook(hooks.get("pre_update"), "pre_update", hook_env, dest)

        report("Installing")
        dest.mkdir(parents=True, exist_ok=True)
        if strategy == "symlink":
            _apply_symlink(payload, dest, version)
        elif strategy == "wipe":
            _clear_directory(dest)
            _merge_tree(payload, dest)
        else:
            _merge_tree(payload, dest)
        _write_marker(dest, tool_id, version, asset["name"], strategy)
    except OSError as e:
        raise InstallError(f"Install failed: {e}") from e
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    result = InstallResult(version=version, asset_name=asset["name"], install_path=dest)
    report("Running post-update hook")
    try:
        _run_hook(hooks.get("post_update"), "post_update", hook_env, dest)
    except InstallError as e:
        # The files are already in place, so this is a warning, not a failure.
        result.warnings.append(str(e))
    return result


# --- preconditions ---------------------------------------------------------

def _check_wipe_allowed(dest: Path, allow_unmanaged: bool):
    home = Path.home().resolve()
    if dest == home or dest in home.parents or len(dest.parts) < 3:
        raise InstallError(
            f"Refusing to use the 'wipe' strategy on {dest}: it is too broad a directory."
        )
    if allow_unmanaged or not dest.exists():
        return
    if not dest.is_dir():
        raise InstallError(f"{dest} exists and is not a directory.")
    if (dest / MARKER_NAME).exists():
        return
    if any(dest.iterdir()):
        raise UnmanagedDestinationError(dest)


def _verify_digest(asset, sha256_hex):
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        return
    expected = digest.split(":", 1)[1].lower()
    if expected != sha256_hex.lower():
        raise InstallError(
            f"Checksum mismatch for {asset['name']}: expected {expected}, got {sha256_hex}."
        )


def _make_work_dir(dest: Path) -> Path:
    """
    Prefer a sibling of the install directory so the final move stays on one
    filesystem (and large archives do not land on a RAM-backed /tmp).
    """
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=".just-release-", dir=dest.parent))
    except OSError:
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "just-release"
        cache.mkdir(parents=True, exist_ok=True)
        return Path(os.path.realpath(tempfile.mkdtemp(prefix="work-", dir=cache)))


# --- extraction ------------------------------------------------------------

def _safe_join(base, name, root=None) -> Path:
    """Join `name` onto `base`, refusing anything that escapes `root`."""
    root = str(root or base)
    if os.path.isabs(name):
        raise InstallError(f"Archive contains an absolute path: {name!r}")
    full = os.path.normpath(os.path.join(str(base), name))
    if os.path.commonpath([root, full]) != root:
        raise InstallError(f"Archive contains a path outside the install directory: {name!r}")
    return Path(full)


def _extract(archive: Path, asset_name: str, out: Path):
    lower = asset_name.lower()
    try:
        if lower.endswith(".zip"):
            _extract_zip(archive, out)
        elif lower.endswith(TAR_SUFFIXES):
            _extract_tar(archive, out)
        else:
            # A bare binary (or any other single file): install it as-is.
            target = out / os.path.basename(asset_name)
            shutil.copyfile(archive, target)
            os.chmod(target, 0o755)
    except (zipfile.BadZipFile, tarfile.TarError) as e:
        raise InstallError(f"Could not extract {asset_name}: {e}") from e


def _extract_zip(archive: Path, out: Path):
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            target = _safe_join(out, info.filename)
            if target == out:
                continue
            mode = info.external_attr >> 16
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISLNK(mode):
                link = zf.read(info).decode("utf-8", "surrogateescape")
                _safe_join(target.parent, link, root=out)
                os.symlink(link, target)
                continue
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            perm = stat.S_IMODE(mode) & 0o777
            if perm:
                # zipfile drops permissions; restore them so binaries stay executable.
                os.chmod(target, perm | stat.S_IRUSR | stat.S_IWUSR)


def _extract_tar(archive: Path, out: Path):
    with tarfile.open(archive) as tf:
        if hasattr(tarfile, "data_filter"):
            tf.extractall(out, filter="data")
            return
        # Python builds without extraction filters: only take plain files and
        # directories, and check every path ourselves.
        for member in tf.getmembers():
            if not (member.isfile() or member.isdir()):
                continue
            if _safe_join(out, member.name) == out:
                continue
            tf.extract(member, out)


def _payload_root(staging: Path) -> Path:
    """
    Most release archives wrap everything in one versioned folder
    (`tool-1.2.3/...`). Install the contents of that folder rather than the
    folder itself, so each update lands on the same paths.
    """
    entries = list(staging.iterdir())
    if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink():
        return entries[0]
    return staging


# --- strategies ------------------------------------------------------------

def _remove(path: Path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _clear_directory(path: Path):
    for entry in path.iterdir():
        _remove(entry)


def _merge_tree(src: Path, dst: Path):
    """
    Move everything from src into dst, replacing files that exist in both and
    leaving files that only exist in dst untouched.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for entry in list(src.iterdir()):
        target = dst / entry.name
        if entry.is_dir() and not entry.is_symlink():
            if os.path.lexists(target) and not target.is_dir():
                target.unlink()
            _merge_tree(entry, target)
        else:
            if os.path.lexists(target):
                # Unlink first: this also allows replacing a running executable.
                _remove(target)
            shutil.move(str(entry), str(target))


def _safe_tag(tag: str) -> str:
    name = re.sub(r"[^\w.+-]", "_", tag).lstrip(".")
    return name or "unknown"


def _apply_symlink(payload: Path, dest: Path, version: str):
    current = dest / "current"
    if os.path.lexists(current) and not current.is_symlink():
        raise InstallError(f"{current} exists and is not a symlink; move it out of the way first.")

    versions = dest / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    name = _safe_tag(version)
    target = versions / name
    if os.path.lexists(target):
        _remove(target)
    shutil.move(str(payload), str(target))

    # Create the new link beside the old one, then rename over it atomically.
    tmp_link = dest / ".current.tmp"
    if os.path.lexists(tmp_link):
        tmp_link.unlink()
    os.symlink(os.path.join("versions", name), tmp_link)
    os.replace(tmp_link, current)


def _write_marker(dest: Path, tool_id, version, asset_name, strategy):
    marker = {
        "tool": str(tool_id),
        "version": version,
        "asset": asset_name,
        "strategy": strategy,
        "installed_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(dest / MARKER_NAME, "w", encoding="utf-8") as f:
        json.dump(marker, f, indent=2)
        f.write("\n")


# --- hooks -----------------------------------------------------------------

def _run_hook(script, hook_name, hook_env, dest: Path):
    if not script or not script.strip():
        return
    cwd = dest if dest.is_dir() else Path.home()
    try:
        proc = subprocess.run(
            ["/bin/sh", "-c", script],
            env={**os.environ, **hook_env},
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=HOOK_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        raise InstallError(f"The {hook_name} hook timed out after {HOOK_TIMEOUT_SECONDS}s.") from e
    if proc.returncode != 0:
        output = (proc.stderr or proc.stdout or "").strip()[-2000:]
        detail = f":\n{output}" if output else "."
        raise InstallError(f"The {hook_name} hook exited with status {proc.returncode}{detail}")
