import os
import yaml
from pathlib import Path

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "just-release"
DEFAULT_MANIFEST_PATH = DEFAULT_CONFIG_DIR / "releases.yaml"

DEFAULT_MANIFEST_CONTENT = {
    "version": 1,
    "settings": {
        "poll_interval_hours": 4,
        "desktop_notifications": True,
        "github_token": ""
    },
    "tools": {
        "ghidra": {
            "name": "Ghidra",
            "repo": "NationalSecurityAgency/ghidra",
            "install_to": str(Path.home() / "security" / "ghidra"),
            "asset_pattern": "ghidra_*_PUBLIC_*.zip",
            "strategy": "smart-sync",
            "current_version": "Ghidra_11.2.1_build",
            "latest_version": "",
            "last_checked": "",
            "hooks": {
                "pre_update": "# Stop any running instances\npkill -f ghidraRun || true",
                "post_update": "# Create desktop entry or sync plugins\necho 'Ghidra updated to' $JUST_RELEASE_NEW_VERSION"
            }
        },
        "ripgrep": {
            "name": "ripgrep",
            "repo": "BurntSushi/ripgrep",
            "install_to": str(Path.home() / ".local" / "bin"),
            "asset_pattern": "*x86_64*linux*musl*.tar.gz",
            "strategy": "wipe",
            "current_version": "14.1.0",
            "latest_version": "14.1.0",
            "last_checked": "2026-09-14",
            "hooks": {
                "pre_update": "",
                "post_update": ""
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
        self.ensure_loaded()

    def set_manifest_path(self, new_path):
        self.manifest_path = Path(new_path).expanduser().resolve()
        self.ensure_loaded()

    def ensure_loaded(self):
        if not self.manifest_path.exists():
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self.data = DEFAULT_MANIFEST_CONTENT.copy()
            self.save()
        else:
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    self.data = yaml.safe_load(f) or {}
                if "tools" not in self.data:
                    self.data["tools"] = {}
                if "settings" not in self.data:
                    self.data["settings"] = DEFAULT_MANIFEST_CONTENT["settings"].copy()
            except Exception as e:
                print(f"Error reading manifest {self.manifest_path}: {e}")
                self.data = DEFAULT_MANIFEST_CONTENT.copy()

    def save(self):
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            yaml.dump(self.data, f, sort_keys=False, default_flow_style=False)

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
        self.data["settings"] = settings_data
        self.save()
