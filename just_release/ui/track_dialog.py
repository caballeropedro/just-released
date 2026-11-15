import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio
import threading
import os
import re
from pathlib import Path
from ..github import get_latest_release, match_asset, format_size, normalize_repo

class TrackDialog(Adw.Window):
    def __init__(self, parent_window, config_manager, tool_id=None, on_saved=None):
        super().__init__()
        self.set_transient_for(parent_window)
        self.set_modal(True)
        self.set_default_size(650, 750)
        self.config_manager = config_manager
        self.tool_id = tool_id
        self.on_saved = on_saved
        self.fetched_assets = []
        self.asset_row_widgets = []

        is_edit = tool_id is not None
        title = "Edit Tracked Tool" if is_edit else "Track New GitHub Release"
        self.set_title(title)

        existing_data = {}
        if is_edit:
            existing_data = config_manager.get_tools().get(tool_id, {})

        # Toolbar View
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header_bar = Adw.HeaderBar()
        toolbar_view.add_top_bar(header_bar)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: self.close())
        header_bar.pack_start(cancel_btn)

        save_btn = Gtk.Button(label="Save" if is_edit else "Track Tool")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self.on_save_clicked)
        header_bar.pack_end(save_btn)

        # Content Preferences Page inside ScrolledWindow
        prefs_page = Adw.PreferencesPage()
        toolbar_view.set_content(prefs_page)

        # Group 1: Repository
        repo_group = Adw.PreferencesGroup(
            title="GitHub Repository",
            description="Public or private repository to track releases from"
        )
        prefs_page.add(repo_group)

        self.name_row = Adw.EntryRow(title="Display Name")
        self.name_row.set_text(existing_data.get("name", ""))
        repo_group.add(self.name_row)

        self.repo_row = Adw.EntryRow(title="Repository (owner/repo)")
        self.repo_row.set_text(existing_data.get("repo", ""))
        repo_group.add(self.repo_row)

        fetch_row = Adw.ActionRow(
            title="Inspect Upstream Release",
            subtitle="Fetch all published assets and tags directly from GitHub"
        )
        self.fetch_btn = Gtk.Button(label="Fetch Assets", valign=Gtk.Align.CENTER)
        self.fetch_btn.add_css_class("suggested-action")
        self.fetch_btn.connect("clicked", self.on_fetch_clicked)
        fetch_row.add_suffix(self.fetch_btn)
        self.release_status_lbl = Gtk.Label(label="Not queried yet", css_classes=["dim-label"], valign=Gtk.Align.CENTER)
        fetch_row.add_suffix(self.release_status_lbl)
        repo_group.add(fetch_row)

        # Group 2: All Available Assets (Dynamically shown)
        self.assets_list_group = Adw.PreferencesGroup(
            title="Available Release Assets",
            description="Click 'Select' on any asset below to target it for auto-updates"
        )
        prefs_page.add(self.assets_list_group)
        self.assets_list_group.set_visible(False)

        # Group 3: Asset Filter & Target
        self.asset_filter_group = Adw.PreferencesGroup(
            title="Release Asset Pattern",
            description="Pattern used by the background service to match future releases"
        )
        prefs_page.add(self.asset_filter_group)

        self.pattern_row = Adw.EntryRow(title="Asset Pattern / Glob")
        self.pattern_row.set_text(existing_data.get("asset_pattern", ""))
        self.pattern_row.connect("changed", self.on_pattern_changed)
        self.asset_filter_group.add(self.pattern_row)

        self.asset_preview_row = Adw.ActionRow(title="Currently Matched Asset")
        self.asset_preview_lbl = Gtk.Label(label="None", css_classes=["dim-label"])
        self.asset_preview_row.add_suffix(self.asset_preview_lbl)
        self.asset_filter_group.add(self.asset_preview_row)

        # Group 4: Installation Location
        dest_group = Adw.PreferencesGroup(
            title="Filesystem Destination",
            description="Where this software should live on your machine"
        )
        prefs_page.add(dest_group)

        self.dest_row = Adw.EntryRow(title="Install Directory")
        self.dest_row.set_text(existing_data.get("install_to", ""))
        dest_group.add(self.dest_row)

        browse_row = Adw.ActionRow(title="Browse Folder")
        browse_btn = Gtk.Button(label="Choose Directory...", valign=Gtk.Align.CENTER)
        browse_btn.connect("clicked", self.on_browse_clicked)
        browse_row.add_suffix(browse_btn)
        dest_group.add(browse_row)

        # Group 5: Strategy
        strat_group = Adw.PreferencesGroup(
            title="Update Strategy",
            description="How new releases are applied to your destination folder"
        )
        prefs_page.add(strat_group)

        self.strat_model = Gtk.StringList()
        self.strat_model.append("Smart In-Place Sync (Preserve user files) [Default]")
        self.strat_model.append("Symlink Strategy (versions/<tag>/ + current symlink)")
        self.strat_model.append("Clean Wipe (Delete destination and extract fresh)")

        self.strat_row = Adw.ComboRow(
            title="Strategy",
            subtitle="Smart Sync keeps your personal scripts/plugins safe",
            model=self.strat_model
        )
        strategy_val = existing_data.get("strategy", "smart-sync")
        if strategy_val == "symlink":
            self.strat_row.set_selected(1)
        elif strategy_val == "wipe":
            self.strat_row.set_selected(2)
        else:
            self.strat_row.set_selected(0)
        strat_group.add(self.strat_row)

        # Group 6: Scripts & Hooks
        hooks_group = Adw.PreferencesGroup(
            title="Custom Scripts and Lifecycle Hooks",
            description="Run custom shell scripts before or after updates"
        )
        prefs_page.add(hooks_group)

        self.hooks_expander = Adw.ExpanderRow(
            title="User Scripts (Advanced)",
            subtitle="Supports $JUST_RELEASE_TOOL, $JUST_RELEASE_NEW_VERSION, etc."
        )
        hooks_group.add(self.hooks_expander)

        existing_hooks = existing_data.get("hooks", {})
        self.pre_hook_row = Adw.EntryRow(title="Pre-Update Script")
        self.pre_hook_row.set_text(existing_hooks.get("pre_update", ""))
        self.hooks_expander.add_row(self.pre_hook_row)

        self.post_hook_row = Adw.EntryRow(title="Post-Update Script")
        self.post_hook_row.set_text(existing_hooks.get("post_update", ""))
        self.hooks_expander.add_row(self.post_hook_row)

    def on_fetch_clicked(self, btn):
        repo = normalize_repo(self.repo_row.get_text())
        if not repo:
            self.repo_row.add_css_class("error")
            self.release_status_lbl.set_text("Enter a repo as owner/repo")
            return
        self.repo_row.remove_css_class("error")

        self.fetch_btn.set_sensitive(False)
        self.release_status_lbl.set_text("Querying GitHub...")

        def worker():
            token = self.config_manager.get_settings().get("github_token", "")
            res = get_latest_release(repo, token=token if token else None)
            GLib.idle_add(self.update_fetch_ui, res)

        threading.Thread(target=worker, daemon=True).start()

    def update_fetch_ui(self, res):
        self.fetch_btn.set_sensitive(True)
        if not res.get("success"):
            self.release_status_lbl.set_text(f"Error: {res.get('error', 'Failed')}")
            self.asset_preview_lbl.set_text("None")
            return

        tag = res.get("tag_name", "Unknown")
        assets = res.get("assets", [])
        self.fetched_assets = assets
        self.release_status_lbl.set_text(f"Release: {tag}")

        # Clear existing rows in assets list
        for r in self.asset_row_widgets:
            self.assets_list_group.remove(r)
        self.asset_row_widgets.clear()

        # Populate with all available assets
        if assets:
            self.assets_list_group.set_visible(True)
            self.assets_list_group.set_title(f"Available Assets in {tag} ({len(assets)} files)")
            for asset in assets:
                row = Adw.ActionRow(
                    title=asset["name"],
                    subtitle=f"Size: {format_size(asset.get('size', 0))}  •  {asset.get('content_type', 'archive')}"
                )
                
                # Pick icon
                name_lower = asset["name"].lower()
                if name_lower.endswith((".zip", ".tar.gz", ".tgz", ".7z", ".tar.xz")):
                    row.set_icon_name("package-x-generic-symbolic")
                elif name_lower.endswith((".sha256", ".asc", ".sig", ".md5")):
                    row.set_icon_name("security-high-symbolic")
                else:
                    row.set_icon_name("application-x-executable-symbolic")

                select_btn = Gtk.Button(label="Select", valign=Gtk.Align.CENTER)
                select_btn.add_css_class("flat")
                select_btn.connect("clicked", lambda b, a=asset: self.on_select_asset(a))
                row.add_suffix(select_btn)

                self.assets_list_group.add(row)
                self.asset_row_widgets.append(row)
        else:
            self.assets_list_group.set_visible(True)
            empty_row = Adw.ActionRow(
                title="No attached binary assets found",
                subtitle="This release only provides source code archives."
            )
            self.assets_list_group.add(empty_row)
            self.asset_row_widgets.append(empty_row)

        self.update_asset_match()

    def on_select_asset(self, asset):
        filename = asset["name"]
        # Generate smart glob: e.g. ghidra_11.3.1_PUBLIC_20250219.zip -> ghidra_*_PUBLIC_*.zip
        # or ripgrep-14.1.0-x86_64-unknown-linux-musl.tar.gz -> ripgrep-*-x86_64*linux*.tar.gz
        smart_pattern = self.make_smart_pattern(filename)
        self.pattern_row.set_text(smart_pattern)
        self.update_asset_match()

    def make_smart_pattern(self, filename: str) -> str:
        # If filename has version numbers or dates, replace with *
        # Example: ghidra_11.3.1_PUBLIC_20250219.zip -> ghidra_*_PUBLIC_*.zip
        pat = re.sub(r'(\d+\.\d+(\.\d+)?)', '*', filename)
        pat = re.sub(r'\d{8}', '*', pat) # 8-digit dates like 20250219
        pat = re.sub(r'\*+', '*', pat)   # consolidate multiple *
        return pat

    def on_pattern_changed(self, entry):
        self.update_asset_match()

    def update_asset_match(self):
        pattern = self.pattern_row.get_text().strip()
        matched = match_asset(self.fetched_assets, pattern)
        if matched:
            size_str = format_size(matched.get("size", 0))
            self.asset_preview_lbl.set_text(f"{matched.get('name')} ({size_str})")
        else:
            if self.fetched_assets:
                self.asset_preview_lbl.set_text(f"No asset matched '{pattern}'")
            else:
                self.asset_preview_lbl.set_text(pattern or "None")

    def on_browse_clicked(self, btn):
        file_dialog = Gtk.FileDialog(title="Select Destination Directory")
        file_dialog.select_folder(self, None, self.on_folder_selected)

    def on_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                self.dest_row.set_text(folder.get_path())
        except Exception as e:
            pass

    def on_save_clicked(self, btn):
        repo = normalize_repo(self.repo_row.get_text())
        install_to = self.dest_row.get_text().strip()
        for row, valid in ((self.repo_row, bool(repo)), (self.dest_row, bool(install_to))):
            if valid:
                row.remove_css_class("error")
            else:
                row.add_css_class("error")
        if not repo:
            self.release_status_lbl.set_text("Enter a repo as owner/repo")
            return
        if not install_to:
            return

        name = self.name_row.get_text().strip()
        if not name:
            name = repo.split("/")[-1]

        # A new tool never reuses an existing id, which would overwrite that tool.
        tool_id = self.tool_id or self.config_manager.unique_tool_id(name)

        strat_idx = self.strat_row.get_selected()
        strat_str = "smart-sync"
        if strat_idx == 1:
            strat_str = "symlink"
        elif strat_idx == 2:
            strat_str = "wipe"

        existing_tools = self.config_manager.get_tools()
        cur_tool = existing_tools.get(tool_id, {})

        # Start from the stored entry so keys this dialog does not edit survive.
        tool_data = dict(cur_tool)
        tool_data.update({
            "name": name,
            "repo": repo,
            "install_to": install_to,
            "asset_pattern": self.pattern_row.get_text().strip(),
            "strategy": strat_str,
            "current_version": cur_tool.get("current_version", ""),
            "latest_version": cur_tool.get("latest_version", ""),
            "last_checked": cur_tool.get("last_checked", ""),
            "hooks": {
                "pre_update": self.pre_hook_row.get_text(),
                "post_update": self.post_hook_row.get_text()
            }
        })

        self.config_manager.save_tool(tool_id, tool_data)
        if self.on_saved:
            self.on_saved(tool_id)
        self.close()
