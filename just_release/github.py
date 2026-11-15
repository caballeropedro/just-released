import fnmatch
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

GITHUB_API_BASE = "https://api.github.com"
USER_AGENT = "just-release-desktop/1.0"

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class DownloadError(Exception):
    pass


def normalize_repo(text: str) -> str:
    """
    Turn user input into `owner/repo`. Accepts plain `owner/repo` as well as
    pasted GitHub URLs. Returns "" when the input is not a valid repository.
    """
    repo = (text or "").strip()
    repo = re.sub(r"^(https?://)?(www\.)?github\.com/", "", repo, flags=re.IGNORECASE)
    repo = re.sub(r"^git@github\.com:", "", repo, flags=re.IGNORECASE)
    repo = repo.strip("/")
    parts = repo.split("/")
    if len(parts) > 2:
        # e.g. owner/repo/releases/tag/v1.0
        repo = "/".join(parts[:2])
    if repo.endswith(".git"):
        repo = repo[:-4]
    return repo if _REPO_RE.match(repo) else ""


class _StripAuthOnCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    """
    GitHub redirects asset downloads to a storage host with a pre-signed URL.
    urllib would forward the Authorization header there, which leaks the token
    to another host and makes the signed request fail.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None and urlparse(newurl).hostname != urlparse(req.full_url).hostname:
            new_req.headers.pop("Authorization", None)
            new_req.unredirected_hdrs.pop("Authorization", None)
        return new_req


_opener = urllib.request.build_opener(_StripAuthOnCrossHostRedirect)


def _http_error_message(e: urllib.error.HTTPError) -> str:
    if e.code == 404:
        return "Repository or latest release not found."
    if e.code == 401:
        return "GitHub rejected the access token. Check it in Settings."
    if e.code in (403, 429):
        if e.headers.get("X-RateLimit-Remaining") == "0":
            reset = e.headers.get("X-RateLimit-Reset", "")
            when = ""
            if reset.isdigit():
                when = time.strftime(" until %H:%M", time.localtime(int(reset)))
            return f"GitHub API rate limit exceeded{when}. Add a token in Settings to raise it."
        return "Access forbidden by GitHub."
    return f"HTTP {e.code}: {e.reason}"


def get_latest_release(repo: str, token: str = None) -> dict:
    """
    Fetch the latest release information from GitHub API.
    Returns dict with tag_name, name, published_at, html_url, and assets.
    """
    repo = repo.strip().strip("/")
    url = f"{GITHUB_API_BASE}/repos/{repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with _opener.open(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            assets = []
            for a in data.get("assets", []):
                assets.append({
                    "name": a.get("name"),
                    "size": a.get("size", 0),
                    "download_url": a.get("browser_download_url"),
                    "api_url": a.get("url"),
                    "digest": a.get("digest"),
                    "content_type": a.get("content_type"),
                    "created_at": a.get("created_at")
                })
            return {
                "success": True,
                "tag_name": data.get("tag_name"),
                "name": data.get("name") or data.get("tag_name"),
                "published_at": data.get("published_at"),
                "html_url": data.get("html_url"),
                "body": data.get("body", ""),
                "assets": assets
            }
    except urllib.error.HTTPError as e:
        return {"success": False, "error": _http_error_message(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def download_asset(asset: dict, dest_path, token: str = None, progress=None) -> str:
    """
    Download a release asset to dest_path and return its SHA-256 hex digest.
    `progress`, if given, is called with (bytes_done, bytes_total).
    """
    headers = {"User-Agent": USER_AGENT}
    if token and asset.get("api_url"):
        # Private repositories only serve assets through the API endpoint.
        url = asset["api_url"]
        headers["Accept"] = "application/octet-stream"
        headers["Authorization"] = f"Bearer {token}"
    else:
        url = asset.get("download_url")
    if not url:
        raise DownloadError(f"Asset {asset.get('name')!r} has no download URL.")

    part_path = f"{dest_path}.part"
    sha = hashlib.sha256()
    req = urllib.request.Request(url, headers=headers)
    try:
        with _opener.open(req, timeout=30) as response, open(part_path, "wb") as out:
            total = int(response.headers.get("Content-Length") or asset.get("size") or 0)
            done = 0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except urllib.error.HTTPError as e:
        _remove_quietly(part_path)
        raise DownloadError(f"Download failed: {_http_error_message(e)}") from e
    except OSError as e:
        _remove_quietly(part_path)
        raise DownloadError(f"Download failed: {e}") from e

    expected_size = asset.get("size") or 0
    if expected_size and done != expected_size:
        _remove_quietly(part_path)
        raise DownloadError(f"Download incomplete: got {done} of {expected_size} bytes.")

    os.replace(part_path, dest_path)
    return sha.hexdigest()


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def match_asset(assets: list, pattern: str) -> dict:
    """
    Find matching asset from assets list using glob pattern or fallback regex.
    """
    if not assets:
        return None

    if not pattern:
        # Default fallback: pick first archive or binary
        for a in assets:
            name = a["name"].lower()
            if name.endswith((".zip", ".tar.gz", ".tgz")):
                return a
        return assets[0]

    # Try glob match
    for a in assets:
        if fnmatch.fnmatch(a["name"], pattern):
            return a

    # Try regex match
    try:
        reg = re.compile(pattern, re.IGNORECASE)
        for a in assets:
            if reg.search(a["name"]):
                return a
    except re.error:
        pass

    return None


def format_size(bytes_num: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} TB"
