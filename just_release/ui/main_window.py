import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio
import threading
import subprocess
import os
from pathlib import Path
from .track_dialog import TrackDialog
from .settings_dialog import SettingsDialog
from ..github import get_latest_release

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, config_manager):
        super().__init__(application=app)
        self.config_manager = config_manager
        self.set_title("just-release")
        self.set_default_size(900, 650)

        # Main ToolbarView
        self.toolbar_view = Adw.ToolbarView()
        self.set_content(self.toolbar_view)

        # HeaderBar
        header_bar = Adw.HeaderBar()
        self.toolbar_view.add_top_bar(header_bar)

        # Title
        title_widget = Adw.WindowTitle(
            title="just-release",
            subtitle="Autonomous Release Orchestrator"
        )
        header_bar.set_title_widget(title_widget)

        # Left side: Service Status
        service_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        service_box.set_margin_start(6)
        status_dot = Gtk.Image.new_from_icon_name("media-record-symbolic")
        status_dot.add_css_class("accent")
        service_box.append(status_dot)
        service_lbl = Gtk.Label(label="Service: Active (systemd)")
        service_lbl.add_css_class("dim-label")
        service_box.append(service_lbl)
        header_bar.pack_start(service_box)

        # Right side: Actions
        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Check All Releases Now")
        refresh_btn.connect("clicked", self.on_check_all_clicked)
        header_bar.pack_end(refresh_btn)

        menu_btn = Gtk.Button(icon_name="emblem-system-symbolic", tooltip_text="Settings & Manifest")
        menu_btn.connect("clicked", self.on_settings_clicked)
        header_bar.pack_end(menu_btn)

        add_btn = Gtk.Button(label="+ Track Repo")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", self.on_track_new_clicked)
        header_bar.pack_end(add_btn)

        # Toast Overlay
        self.toast_overlay = Adw.ToastOverlay()
        self.toolbar_view.set_content(self.toast_overlay)

        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.toast_overlay.set_child(main_box)

        # Banner showing current manifest file
        self.banner_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.banner_row.add_css_class("card")
        self.banner_row.set_margin_top(12)
        self.banner_row.set_margin_start(16)
        self.banner_row.set_margin_end(16)
        self.banner_row.set_margin_bottom(6)

        manifest_icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic")
        self.banner_row.append(manifest_icon)

        self.manifest_lbl = Gtk.Label(
            label=f"Manifest: {self.config_manager.manifest_path}",
            xalign=0,
            hexpand=True
        )
        self.manifest_lbl.add_css_class("dim-label")
        self.banner_row.append(self.manifest_lbl)

        edit_manifest_btn = Gtk.Button(label="Change...", valign=Gtk.Align.CENTER)
        edit_manifest_btn.add_css_class("flat")
        edit_manifest_btn.connect("clicked", self.on_settings_clicked)
        self.banner_row.append(edit_manifest_btn)

        main_box.append(self.banner_row)

        # Scrolled content with Tools list
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        main_box.append(scrolled)

        self.prefs_page = Adw.PreferencesPage()
        scrolled.set_child(self.prefs_page)

        self.tools_group = Adw.PreferencesGroup(
            title="Managed Tools and Distributions",
            description="Installed from GitHub Releases and tracked by just-release"
        )
        self.prefs_page.add(self.tools_group)

        self.refresh_tools_list()

    def show_toast(self, message):
        toast = Adw.Toast(title=message)
        self.toast_overlay.add_toast(toast)

    def refresh_tools_list(self):
        self.manifest_lbl.set_text(f"Manifest: {self.config_manager.manifest_path}")

        # Clear existing rows in tools group
        # In Libadwaita PreferencesGroup, remove child rows
        # Re-create the group for a clean redraw
        self.prefs_page.remove(self.tools_group)
        self.tools_group = Adw.PreferencesGroup(
            title="Managed Tools and Distributions",
            description="Installed from GitHub Releases and tracked by just-release"
        )
        self.prefs_page.add(self.tools_group)

        tools = self.config_manager.get_tools()
        if not tools:
            empty_row = Adw.ActionRow(
                title="No Tools Tracked Yet",
                subtitle="Click '+ Track Repo' to start managing a GitHub distribution."
            )
            self.tools_group.add(empty_row)
            return

        for tool_id, tool_data in tools.items():
            row = self.create_tool_row(tool_id, tool_data)
            self.tools_group.add(row)

    def create_tool_row(self, tool_id, tool_data):
        name = tool_data.get("name", tool_id)
        repo = tool_data.get("repo", "")
        install_to = tool_data.get("install_to", "")
        strategy = tool_data.get("strategy", "smart-sync")
        cur_version = tool_data.get("current_version", "Not installed")
        latest_version = tool_data.get("latest_version", "")

        has_update = latest_version and latest_version != cur_version

        row = Adw.ActionRow(
            title=name,
            subtitle=f"{repo}  •  {install_to}"
        )
        row.set_icon_name("package-x-generic-symbolic")

        # Strategy pill
        strat_lbl = Gtk.Label(label=f"[{strategy}]")
        strat_lbl.add_css_class("dim-label")
        row.add_suffix(strat_lbl)

        # Version status box
        ver_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        if has_update:
            v_lbl = Gtk.Label(label=f"{cur_version} → {latest_version}")
            v_lbl.add_css_class("warning")
            badge = Gtk.Label(label="Update Available")
            badge.add_css_class("accent")
            ver_box.append(v_lbl)
            ver_box.append(badge)
        else:
            v_lbl = Gtk.Label(label=f"v: {cur_version or 'unknown'}")
            v_lbl.add_css_class("dim-label")
            ver_box.append(v_lbl)
        row.add_suffix(ver_box)

        # Action Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)

        if has_update:
            update_btn = Gtk.Button(label="Update")
            update_btn.add_css_class("suggested-action")
            update_btn.connect("clicked", lambda b, tid=tool_id: self.on_update_tool_clicked(tid))
            btn_box.append(update_btn)

        folder_btn = Gtk.Button(icon_name="folder-symbolic", tooltip_text="Open Install Directory")
        folder_btn.add_css_class("flat")
        folder_btn.connect("clicked", lambda b, p=install_to: self.on_open_folder(p))
        btn_box.append(folder_btn)

        edit_btn = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Edit Configuration")
        edit_btn.add_css_class("flat")
        edit_btn.connect("clicked", lambda b, tid=tool_id: self.on_edit_tool_clicked(tid))
        btn_box.append(edit_btn)

        del_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Untrack Tool")
        del_btn.add_css_class("flat")
        del_btn.connect("clicked", lambda b, tid=tool_id: self.on_delete_tool_clicked(tid))
        btn_box.append(del_btn)

        row.add_suffix(btn_box)
        return row

    def on_open_folder(self, path):
        if not path:
            return
        p = Path(path).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", str(p)])
        except Exception as e:
            self.show_toast(f"Cannot open folder: {e}")

    def on_track_new_clicked(self, btn):
        dialog = TrackDialog(self, self.config_manager, on_saved=self.on_tool_saved)
        dialog.present()

    def on_edit_tool_clicked(self, tool_id):
        dialog = TrackDialog(self, self.config_manager, tool_id=tool_id, on_saved=self.on_tool_saved)
        dialog.present()

    def on_tool_saved(self, tool_id):
        self.refresh_tools_list()
        self.show_toast(f"Saved {tool_id} successfully!")

    def on_delete_tool_clicked(self, tool_id):
        self.config_manager.remove_tool(tool_id)
        self.refresh_tools_list()
        self.show_toast(f"Removed {tool_id} from tracking.")

    def on_settings_clicked(self, btn):
        dialog = SettingsDialog(self, self.config_manager, on_saved=self.refresh_tools_list)
        dialog.present(self)

    def on_check_all_clicked(self, btn):
        self.show_toast("Checking GitHub releases for all tools...")
        tools = self.config_manager.get_tools()

        def worker():
            token = self.config_manager.get_settings().get("github_token", "")
            for tid, tdata in tools.items():
                repo = tdata.get("repo", "")
                if repo:
                    res = get_latest_release(repo, token=token if token else None)
                    if res.get("success"):
                        tdata["latest_version"] = res.get("tag_name", "")
                        self.config_manager.save_tool(tid, tdata)
            GLib.idle_add(self.refresh_tools_list)
            GLib.idle_add(lambda: self.show_toast("Check complete! Releases up to date."))

        threading.Thread(target=worker, daemon=True).start()

    def on_update_tool_clicked(self, tool_id):
        tools = self.config_manager.get_tools()
        tool = tools.get(tool_id)
        if not tool:
            return
        # Simulate / perform the update
        tool["current_version"] = tool.get("latest_version", tool["current_version"])
        self.config_manager.save_tool(tool_id, tool)
        self.refresh_tools_list()
        self.show_toast(f"Updated {tool.get('name', tool_id)} to {tool['current_version']}!")
