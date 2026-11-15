import io
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from just_release import installer
from just_release.installer import (
    InstallError, UnmanagedDestinationError, MARKER_NAME, install_release,
)


def make_tar(path, files, mode=0o755):
    with tarfile.open(path, "w:gz") as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = mode
            tf.addfile(info, io.BytesIO(content))


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            zf.writestr(info, content)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.dest = self.tmp / "apps" / "tool"
        self.addCleanup(self._tmp.cleanup)

    def tool(self, **overrides):
        tool = {
            "name": "Tool",
            "install_to": str(self.dest),
            "asset_pattern": "tool-*.tar.gz",
            "strategy": "smart-sync",
            "current_version": "v1",
            "hooks": {},
        }
        tool.update(overrides)
        return tool

    def release(self, tag, files, name=None, kind="tar", digest=None):
        name = name or f"tool-{tag}.tar.gz"
        src = self.tmp / f"src-{tag}-{name}"
        if kind == "tar":
            make_tar(src, files)
        elif kind == "zip":
            make_zip(src, files)
        else:
            src.write_bytes(files)
        self.sources = getattr(self, "sources", {})
        self.sources[name] = src
        return {"tag_name": tag, "assets": [{"name": name, "size": src.stat().st_size, "digest": digest}]}

    def downloader(self, asset, dest_path, token=None, progress=None):
        data = self.sources[asset["name"]].read_bytes()
        Path(dest_path).write_bytes(data)
        return "0" * 64

    def install(self, tool, release, **kw):
        return install_release("tool", tool, release, downloader=self.downloader, **kw)

    def leftovers(self):
        return [p.name for p in self.dest.parent.iterdir() if p.name.startswith(".just-release-")]

    def test_smart_sync_strips_top_dir_and_preserves_user_files(self):
        self.install(self.tool(), self.release("v1", {"tool-v1/bin/run": b"one", "tool-v1/old.txt": b"old"}))
        (self.dest / "plugins").mkdir()
        (self.dest / "plugins" / "mine.py").write_text("keep me")

        result = self.install(self.tool(), self.release("v2", {"tool-v2/bin/run": b"two"}))

        self.assertEqual(result.version, "v2")
        self.assertEqual((self.dest / "bin" / "run").read_bytes(), b"two")
        self.assertTrue(os.access(self.dest / "bin" / "run", os.X_OK))
        self.assertEqual((self.dest / "plugins" / "mine.py").read_text(), "keep me")
        self.assertTrue((self.dest / "old.txt").exists())
        self.assertTrue((self.dest / MARKER_NAME).exists())
        self.assertEqual(self.leftovers(), [])

    def test_wipe_removes_old_files(self):
        tool = self.tool(strategy="wipe")
        self.install(tool, self.release("v1", {"tool-v1/run": b"one", "tool-v1/old.txt": b"old"}))
        self.install(tool, self.release("v2", {"tool-v2/run": b"two"}))
        self.assertEqual((self.dest / "run").read_bytes(), b"two")
        self.assertFalse((self.dest / "old.txt").exists())

    def test_wipe_refuses_unmanaged_directory_until_confirmed(self):
        self.dest.mkdir(parents=True)
        (self.dest / "precious.txt").write_text("mine")
        tool = self.tool(strategy="wipe")
        release = self.release("v1", {"tool-v1/run": b"one"})

        with self.assertRaises(UnmanagedDestinationError):
            self.install(tool, release)
        self.assertEqual((self.dest / "precious.txt").read_text(), "mine")

        self.install(tool, release, allow_wipe_unmanaged=True)
        self.assertFalse((self.dest / "precious.txt").exists())
        self.assertTrue((self.dest / "run").exists())

    def test_wipe_refuses_home_directory(self):
        tool = self.tool(strategy="wipe", install_to=str(Path.home()))
        with self.assertRaises(InstallError) as ctx:
            self.install(tool, self.release("v1", {"run": b"x"}), allow_wipe_unmanaged=True)
        self.assertNotIsInstance(ctx.exception, UnmanagedDestinationError)

    def test_symlink_strategy_switches_current(self):
        tool = self.tool(strategy="symlink")
        self.install(tool, self.release("v1", {"tool-v1/run": b"one"}))
        self.install(tool, self.release("v2", {"tool-v2/run": b"two"}))
        self.assertTrue((self.dest / "current").is_symlink())
        self.assertEqual(os.readlink(self.dest / "current"), os.path.join("versions", "v2"))
        self.assertEqual((self.dest / "current" / "run").read_bytes(), b"two")
        self.assertEqual((self.dest / "versions" / "v1" / "run").read_bytes(), b"one")

    def test_zip_keeps_executable_bit(self):
        tool = self.tool(asset_pattern="*.zip")
        self.install(tool, self.release("v1", {"tool/run": b"x"}, name="tool.zip", kind="zip"))
        self.assertTrue(os.access(self.dest / "run", os.X_OK))

    def test_zip_slip_is_rejected(self):
        tool = self.tool(asset_pattern="*.zip")
        release = self.release("v1", {"../../evil": b"x"}, name="tool.zip", kind="zip")
        with self.assertRaises(InstallError):
            self.install(tool, release)
        self.assertFalse((self.tmp / "evil").exists())
        self.assertEqual(self.leftovers(), [])

    def test_bare_binary_is_installed_executable(self):
        tool = self.tool(asset_pattern="tool-linux")
        self.install(tool, self.release("v1", b"\x7fELF", name="tool-linux", kind="raw"))
        self.assertTrue(os.access(self.dest / "tool-linux", os.X_OK))

    def test_no_matching_asset(self):
        with self.assertRaises(InstallError):
            self.install(self.tool(asset_pattern="nope-*"), self.release("v1", {"a": b"x"}))

    def test_checksum_mismatch_aborts(self):
        release = self.release("v1", {"tool-v1/run": b"one"}, digest="sha256:" + "f" * 64)
        with self.assertRaises(InstallError):
            self.install(self.tool(), release)
        self.assertFalse(self.dest.exists())

    def test_hooks_receive_environment(self):
        out = self.tmp / "hook.txt"
        tool = self.tool(hooks={
            "pre_update": f'echo "pre $JUST_RELEASE_TOOL $JUST_RELEASE_OLD_VERSION" > "{out}"',
            "post_update": f'echo "post $JUST_RELEASE_NEW_VERSION $JUST_RELEASE_INSTALL_DIR" >> "{out}"',
        })
        self.install(tool, self.release("v2", {"tool-v2/run": b"two"}))
        self.assertEqual(out.read_text().splitlines(), ["pre tool v1", f"post v2 {self.dest}"])

    def test_failing_pre_hook_aborts_before_touching_files(self):
        tool = self.tool(hooks={"pre_update": "echo nope >&2; exit 3"})
        with self.assertRaises(InstallError) as ctx:
            self.install(tool, self.release("v2", {"tool-v2/run": b"two"}))
        self.assertIn("nope", str(ctx.exception))
        self.assertFalse((self.dest / "run").exists())

    def test_failing_post_hook_is_a_warning(self):
        tool = self.tool(hooks={"post_update": "exit 1"})
        result = self.install(tool, self.release("v2", {"tool-v2/run": b"two"}))
        self.assertEqual(len(result.warnings), 1)
        self.assertTrue((self.dest / "run").exists())


if __name__ == "__main__":
    unittest.main()
