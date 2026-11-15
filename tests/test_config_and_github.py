import stat
import tempfile
import unittest
from pathlib import Path

from just_release import config
from just_release.config import ConfigManager
from just_release.github import match_asset, normalize_repo


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "releases.yaml"

    def test_new_manifest_does_not_share_state_with_defaults(self):
        cm = ConfigManager(self.path)
        cm.remove_tool("ghidra")
        cm.update_settings({"github_token": "secret"})
        self.assertIn("ghidra", config.DEFAULT_MANIFEST_CONTENT["tools"])
        self.assertEqual(config.DEFAULT_SETTINGS["github_token"], "")

    def test_manifest_is_private(self):
        ConfigManager(self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_broken_manifest_is_kept_aside(self):
        self.path.write_text("tools: [unclosed")
        cm = ConfigManager(self.path)
        self.assertTrue(cm.load_warning)
        self.assertEqual(cm.get_tools(), {})
        backups = list(self.path.parent.glob("releases.yaml.broken-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "tools: [unclosed")

    def test_missing_settings_are_filled_in(self):
        self.path.write_text("settings:\n  poll_interval_hours: 9\n")
        cm = ConfigManager(self.path)
        self.assertEqual(cm.get_settings()["poll_interval_hours"], 9)
        self.assertIs(cm.get_settings()["desktop_notifications"], True)

    def test_unique_tool_id(self):
        cm = ConfigManager(self.path)
        self.assertEqual(cm.unique_tool_id("ripgrep"), "ripgrep-2")
        self.assertEqual(cm.unique_tool_id("My  Tool"), "my-tool")


class GithubHelpersTest(unittest.TestCase):
    def test_normalize_repo(self):
        self.assertEqual(normalize_repo(" BurntSushi/ripgrep/ "), "BurntSushi/ripgrep")
        self.assertEqual(normalize_repo("https://github.com/BurntSushi/ripgrep.git"), "BurntSushi/ripgrep")
        self.assertEqual(
            normalize_repo("https://github.com/BurntSushi/ripgrep/releases/tag/15.0.0"),
            "BurntSushi/ripgrep",
        )
        self.assertEqual(normalize_repo("ripgrep"), "")
        self.assertEqual(normalize_repo("a b/c"), "")

    def test_match_asset(self):
        assets = [{"name": "rg-1.0-aarch64.tar.gz"}, {"name": "rg-1.0-x86_64.tar.gz"}]
        self.assertEqual(match_asset(assets, "*x86_64*")["name"], "rg-1.0-x86_64.tar.gz")
        self.assertEqual(match_asset(assets, r"x86_64\.tar")["name"], "rg-1.0-x86_64.tar.gz")
        self.assertIsNone(match_asset(assets, "*.zip"))


if __name__ == "__main__":
    unittest.main()
