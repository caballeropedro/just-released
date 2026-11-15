import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio
import copy
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from .track_dialog import TrackDialog
from .settings_dialog import SettingsDialog
from ..github import get_latest_release
from ..installer import install_release, InstallError, UnmanagedDestinationError

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, config_manager):
        super().__init__(application=app)
        self.config_manager = config_manager
        self.set_title("just-release")
        self.set_default_size(900, 650)

        # tool_id -> status text, for tools with an install in progress
        self._busy = {}
        self._status_labels = {}
        self._checking = False
        self._auto_check_source = 0

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

        # Right side: Actions
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Check All Releases Now")
        self.refresh_btn.connect("clicked", self.on_check_all_clicked)
        header_bar.pack_end(self.refresh_btn)

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
        manifest_icon.set_margin_start(12)
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

        self.tools_group = Adw.PreferencesGroup()
        self.prefs_page.add(self.tools_group)

        self.connect("close-request", self.on_close_request)

        self.refresh_tools_list()
        self.schedule_auto_check()

        if self.config_manager.load_warning:
            self.show_message("Manifest problem", self.config_manager.load_warning)
            self.config_manager.load_warning = None

        # Quiet check shortly after startup, so the list is current.
        GLib.timeout_add_seconds(1, self._startup_check)

    def show_toast(self, message):
        toast = Adw.Toast(title=message, use_markup=False)
        self.toast_overlay.add_toast(toast)

    def show_message(self, heading, body):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", "Close")
        dialog.present(self)

    def refresh_tools_list(self):
        self.manifest_lbl.set_text(f"Manifest: {self.config_manager.manifest_path}")

        # Re-create the group for a clean redraw
        self.prefs_page.remove(self.tools_group)
        self.tools_group = Adw.PreferencesGroup(
            title="Managed Tools and Distributions",
            description="Installed from GitHub Releases and tracked by just-release"
        )
        self.prefs_page.add(self.tools_group)
        self._status_labels = {}

        tools = self.config_manager.get_tools()
        if not tools:
            empty_row = Adw.ActionRow(
                title="No Tools Tracked Yet",
                subtitle="Click '+ Track Repo' to start managing a GitHub distribution."
            )
            self.tools_group.add(empty_row)
            return

        for tool_id, tool_data in tools.items():
            if not isinstance(tool_data, dict):
                continue
            row = self.create_tool_row(tool_id, tool_data)
            self.tools_group.add(row)

    def create_tool_row(self, tool_id, tool_data):
        name = tool_data.get("name") or tool_id
        repo = tool_data.get("repo", "")
        install_to = tool_data.get("install_to", "")
        strategy = tool_data.get("strategy", "smart-sync")
        cur_version = tool_data.get("current_version", "")
        latest_version = tool_data.get("latest_version", "")

        has_update = bool(latest_version) and latest_version != cur_version
        busy = tool_id in self._busy

        row = Adw.ActionRow(
            title=GLib.markup_escape_text(str(name)),
            subtitle=GLib.markup_escape_text(f"{repo}  •  {install_to}")
        )
        row.set_icon_name("package-x-generic-symbolic")

        # Strategy pill
        strat_lbl = Gtk.Label(label=f"[{strategy}]")
        strat_lbl.add_css_class("dim-label")
        row.add_suffix(strat_lbl)

        # Version status box
        ver_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        if busy:
            status_lbl = Gtk.Label(label=self._busy[tool_id])
            status_lbl.add_css_class("accent")
            ver_box.append(status_lbl)
            self._status_labels[tool_id] = status_lbl
        elif has_update:
            if cur_version:
                v_lbl = Gtk.Label(label=f"{cur_version} → {latest_version}")
                badge = Gtk.Label(label="Update Available")
            else:
                v_lbl = Gtk.Label(label=str(latest_version))
                badge = Gtk.Label(label="Not Installed")
            v_lbl.add_css_class("warning")
            badge.add_css_class("accent")
            ver_box.append(v_lbl)
            ver_box.append(badge)
        else:
            v_lbl = Gtk.Label(label=f"v: {cur_version}" if cur_version else "Not installed")
            v_lbl.add_css_class("dim-label")
            ver_box.append(v_lbl)
        row.add_suffix(ver_box)

        # Action Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)

        if busy:
            spinner = Gtk.Spinner(spinning=True)
            btn_box.append(spinner)
        elif has_update:
            update_btn = Gtk.Button(label="Update" if cur_version else "Install")
            update_btn.add_css_class("suggested-action")
            update_btn.connect("clicked", lambda b, tid=tool_id: self.on_update_tool_clicked(tid))
            btn_box.append(update_btn)

        folder_btn = Gtk.Button(icon_name="folder-symbolic", tooltip_text="Open Install Directory")
        folder_btn.add_css_class("flat")
        folder_btn.connect("clicked", lambda b, p=install_to: self.on_open_folder(p))
        btn_box.append(folder_btn)

        edit_btn = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Edit Configuration")
        edit_btn.add_css_class("flat")
        edit_btn.set_sensitive(not busy)
        edit_btn.connect("clicked", lambda b, tid=tool_id: self.on_edit_tool_clicked(tid))
        btn_box.append(edit_btn)

        del_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Untrack Tool")
        del_btn.add_css_class("flat")
        del_btn.set_sensitive(not busy)
        del_btn.connect("clicked", lambda b, tid=tool_id: self.on_delete_tool_clicked(tid))
        btn_box.append(del_btn)

        row.add_suffix(btn_box)
        return row

    def on_close_request(self, window):
        if self._busy:
            self.show_toast("An install is still running. Wait for it to finish before closing.")
            return True
        return False

    def on_open_folder(self, path):
        if not path:
            return
        p = Path(path).expanduser()
        if not p.is_dir():
            self.show_toast(f"{p} does not exist yet.")
            return
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
        tool = self.config_manager.get_tools().get(tool_id) or {}
        name = tool.get("name") or tool_id
        dialog = Adw.AlertDialog(
            heading=f"Stop tracking {name}?",
            body="It is removed from the manifest. Installed files are left in place."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("untrack", "Untrack")
        dialog.set_response_appearance("untrack", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(dlg, response):
            if response != "untrack":
                return
            self.config_manager.remove_tool(tool_id)
            self.refresh_tools_list()
            self.show_toast(f"Removed {name} from tracking.")

        dialog.connect("response", on_response)
        dialog.present(self)

    def on_settings_clicked(self, btn):
        dialog = SettingsDialog(self, self.config_manager, on_saved=self.on_settings_saved)
        dialog.present(self)

    def on_settings_saved(self):
        self.refresh_tools_list()
        self.schedule_auto_check()

    # --- release checks ---

    def schedule_auto_check(self):
        if self._auto_check_source:
            GLib.source_remove(self._auto_check_source)
        try:
            hours = int(self.config_manager.get_settings().get("poll_interval_hours", 4))
        except (TypeError, ValueError):
            hours = 4
        hours = max(1, min(hours, 72))
        self._auto_check_source = GLib.timeout_add_seconds(hours * 3600, self._auto_check)

    def _auto_check(self):
        self.check_all(quiet=True)
        return GLib.SOURCE_CONTINUE

    def _startup_check(self):
        self.check_all(quiet=True)
        return GLib.SOURCE_REMOVE

    def on_check_all_clicked(self, btn):
        self.check_all(quiet=False)

    def check_all(self, quiet=False):
        if self._checking:
            return
        # Snapshot on the main thread; the worker only does network I/O.
        targets = [
            (tid, tdata.get("repo", ""))
            for tid, tdata in self.config_manager.get_tools().items()
            if isinstance(tdata, dict) and tdata.get("repo")
        ]
        if not targets:
            if not quiet:
                self.show_toast("No repositories to check.")
            return

        self._checking = True
        self.refresh_btn.set_sensitive(False)
        if not quiet:
            self.show_toast("Checking GitHub releases for all tools...")
        token = self.config_manager.get_settings().get("github_token") or None
        manifest_path = self.config_manager.manifest_path

        def worker():
            results = [(tid, get_latest_release(repo, token=token)) for tid, repo in targets]
            GLib.idle_add(self._on_check_finished, results, manifest_path, quiet)

        threading.Thread(target=worker, daemon=True).start()

    def _on_check_finished(self, results, manifest_path, quiet):
        self._checking = False
        self.refresh_btn.set_sensitive(True)
        if manifest_path != self.config_manager.manifest_path:
            # The manifest was switched while the check was running.
            return False

        tools = self.config_manager.get_tools()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        errors = []
        new_updates = []
        pending = 0
        changed = False
        for tid, res in results:
            tool = tools.get(tid)
            if not isinstance(tool, dict):
                continue
            name = tool.get("name") or tid
            if not res.get("success"):
                errors.append(f"{name}: {res.get('error', 'failed')}")
                continue
            latest = res.get("tag_name") or ""
            if latest and latest != tool.get("current_version", ""):
                pending += 1
                if latest != tool.get("latest_version", ""):
                    new_updates.append(f"{name} {latest}")
            tool["latest_version"] = latest
            tool["last_checked"] = now
            changed = True

        if changed:
            self.config_manager.save()
        self.refresh_tools_list()

        if errors:
            if not quiet:
                self.show_message("Some checks failed", "\n".join(errors))
        elif not quiet:
            if pending:
                self.show_toast(f"{pending} update{'s' if pending != 1 else ''} available.")
            else:
                self.show_toast("Everything is up to date.")

        if new_updates and quiet:
            self.show_toast(f"New release: {', '.join(new_updates)}")
            self._notify("Updates available", ", ".join(new_updates))
        return False

    def _notify(self, title, body):
        if not self.config_manager.get_settings().get("desktop_notifications", True):
            return
        if self.is_active():
            return
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        self.get_application().send_notification("just-release-status", notification)

    # --- installs ---

    def _set_status(self, tool_id, text):
        if tool_id not in self._busy:
            return False
        self._busy[tool_id] = text
        label = self._status_labels.get(tool_id)
        if label:
            label.set_text(text)
        return False

    def on_update_tool_clicked(self, tool_id, allow_wipe_unmanaged=False):
        tool = self.config_manager.get_tools().get(tool_id)
        if not isinstance(tool, dict) or tool_id in self._busy:
            return
        tool = copy.deepcopy(tool)
        token = self.config_manager.get_settings().get("github_token") or None

        self._busy[tool_id] = "Starting..."
        self.refresh_tools_list()

        last_text = [None]

        def progress(stage, fraction):
            text = f"{stage} {int(fraction * 100)}%" if fraction is not None else f"{stage}..."
            if text != last_text[0]:
                last_text[0] = text
                GLib.idle_add(self._set_status, tool_id, text)

        def worker():
            try:
                release = get_latest_release(tool.get("repo", ""), token=token)
                if not release.get("success"):
                    raise InstallError(release.get("error", "Could not fetch the release."))
                result = install_release(
                    tool_id, tool, release,
                    token=token,
                    allow_wipe_unmanaged=allow_wipe_unmanaged,
                    progress=progress,
                )
            except UnmanagedDestinationError as e:
                GLib.idle_add(self._confirm_wipe, tool_id, str(e.path))
            except Exception as e:
                GLib.idle_add(self._on_install_failed, tool_id, str(e))
            else:
                GLib.idle_add(self._on_install_done, tool_id, result)

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_wipe(self, tool_id, path):
        self._busy.pop(tool_id, None)
        self.refresh_tools_list()
        dialog = Adw.AlertDialog(
            heading="Delete existing files?",
            body=(
                f"{path} already contains files that just-release did not install. "
                "The Clean Wipe strategy deletes everything in that directory before "
                "extracting the release."
            )
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("wipe", "Delete and Install")
        dialog.set_response_appearance("wipe", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(dlg, response):
            if response == "wipe":
                self.on_update_tool_clicked(tool_id, allow_wipe_unmanaged=True)

        dialog.connect("response", on_response)
        dialog.present(self)
        return False

    def _on_install_failed(self, tool_id, message):
        self._busy.pop(tool_id, None)
        self.refresh_tools_list()
        tool = self.config_manager.get_tools().get(tool_id) or {}
        name = tool.get("name") or tool_id
        self.show_message(f"Could not update {name}", message)
        self._notify(f"Could not update {name}", message)
        return False

    def _on_install_done(self, tool_id, result):
        self._busy.pop(tool_id, None)
        # Re-read: the manifest may have changed while the install was running.
        tool = self.config_manager.get_tools().get(tool_id)
        name = tool_id
        if isinstance(tool, dict):
            name = tool.get("name") or tool_id
            tool["current_version"] = result.version
            tool["latest_version"] = result.version
            tool["last_checked"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.config_manager.save_tool(tool_id, tool)
        self.refresh_tools_list()

        if result.warnings:
            self.show_message(
                f"{name} updated to {result.version}, with warnings",
                "\n\n".join(result.warnings)
            )
        else:
            self.show_toast(f"Updated {name} to {result.version}!")
        self._notify(f"{name} updated", f"Now at {result.version}")
        return False
