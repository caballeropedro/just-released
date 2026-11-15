import copy
import os
import re
import tempfile
import time
import yaml
from pathlib import Path

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "just-release"
DEFAULT_MANIFEST_PATH = DEFAULT_CONFIG_DIR / "releases.yaml"

DEFAULT_SETTINGS = {
    "poll_interval_hours": 4,
    "desktop_notifications": True,
    "github_token": ""
}

DEFAULT_MANIFEST_CONTENT = {
    "version": 1,
    "settings": DEFAULT_SETTINGS,
    "tools": {
        "ghidra": {
            "name": "Ghidra",
            "repo": "NationalSecurityAgency/ghidra",
            "install_to": str(Path.home() / "security" / "ghidra"),
            "asset_pattern": "ghidra_*_PUBLIC_*.zip",
            "strategy": "smart-sync",
            "current_version": "",
            "latest_version": "",
            "last_checked": "",
            "hooks": {
                "pre_update": "pkill -f ghidraRun || true",
                "post_update": "echo \"Ghidra updated to $JUST_RELEASE_NEW_VERSION\""
            }
        },
        "ripgrep": {
            "name": "ripgrep",
            "repo": "BurntSushi/ripgrep",
            # A dedicated directory: the wipe strategy deletes everything in it.
            "install_to": str(Path.home() / ".local" / "opt" / "ripgrep"),
            "asset_pattern": "*x86_64*linux*musl*.tar.gz",
            "strategy": "wipe",
            "current_version": "",
            "latest_version": "",
            "last_checked": "",
            "hooks": {
                "pre_update": "",
                "post_update": "ln -sf \"$JUST_RELEASE_INSTALL_DIR/rg\" \"$HOME/.local/bin/rg\""
            }
        }
    }
}


class ConfigManager:
    def __init__(self, manifest_path=None):
        if manifest_path:
            self.manifest_path = Path(manifest_path).expanduser().resolve()
        else:
            self.manifest_path = DEFAULT_MANIFEST_PATH
        # Set when an unreadable manifest was moved aside; the UI reports it once.
        self.load_warning = None
        self.ensure_loaded()

    def set_manifest_path(self, new_path):
        self.manifest_path = Path(new_path).expanduser().resolve()
        self.ensure_loaded()

    def ensure_loaded(self):
        if not self.manifest_path.exists():
            self.data = copy.deepcopy(DEFAULT_MANIFEST_CONTENT)
            self.save()
            return

        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                raise ValueError("the top level of the manifest must be a mapping")
        except Exception as e:
            # Keep the unreadable file: the next save would otherwise overwrite it.
            backup = self.manifest_path.with_name(
                f"{self.manifest_path.name}.broken-{time.strftime('%Y%m%d-%H%M%S')}"
            )
            try:
                os.replace(self.manifest_path, backup)
                self.load_warning = f"Could not read the manifest ({e}). It was kept as {backup.name}."
            except OSError:
                self.load_warning = f"Could not read the manifest ({e})."
            data = {"version": 1}

        if not isinstance(data.get("tools"), dict):
            data["tools"] = {}
        settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
        data["settings"] = {**DEFAULT_SETTINGS, **settings}
        self.data = data

    def save(self):
        """Write atomically, readable only by the owner (the file can hold a token)."""
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{self.manifest_path.name}.", dir=self.manifest_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.dump(self.data, f, sort_keys=False, default_flow_style=False)
            os.replace(tmp_path, self.manifest_path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    def get_tools(self):
        return self.data.get("tools", {})

    def get_settings(self):
        return self.data.get("settings", {})

    def save_tool(self, tool_id, tool_data):
        if "tools" not in self.data:
            self.data["tools"] = {}
        self.data["tools"][tool_id] = tool_data
        self.save()

    def remove_tool(self, tool_id):
        if "tools" in self.data and tool_id in self.data["tools"]:
            del self.data["tools"][tool_id]
            self.save()

    def update_settings(self, settings_data):
        self.data["settings"] = {**self.get_settings(), **settings_data}
        self.save()

    def unique_tool_id(self, name):
        """Derive a manifest key from a display name without clobbering another tool."""
        base = re.sub(r"[^a-z0-9._]+", "-", str(name).lower()).strip("-") or "tool"
        tool_id = base
        n = 2
        while tool_id in self.get_tools():
            tool_id = f"{base}-{n}"
            n += 1
        return tool_id
