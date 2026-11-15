import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw
from pathlib import Path

class SettingsDialog(Adw.PreferencesDialog):
    def __init__(self, parent_window, config_manager, on_saved=None):
        super().__init__()
        self.set_title("Settings & Preferences")
        self.config_manager = config_manager
        self.on_saved = on_saved
        settings = config_manager.get_settings()

        page = Adw.PreferencesPage()
        self.add(page)

        # Group 1: Storage & Manifest
        storage_group = Adw.PreferencesGroup(
            title="Manifest Storage",
            description="Choose where your releases tracking file is saved"
        )
        page.add(storage_group)

        self.manifest_row = Adw.EntryRow(title="Releases File Path")
        self.manifest_row.set_text(str(config_manager.manifest_path))
        storage_group.add(self.manifest_row)

        manifest_browse_row = Adw.ActionRow(title="Select File...")
        browse_btn = Gtk.Button(label="Browse")
        browse_btn.set_valign(Gtk.Align.CENTER)
        browse_btn.connect("clicked", self.on_browse_manifest)
        manifest_browse_row.add_suffix(browse_btn)
        storage_group.add(manifest_browse_row)

        # Group 2: Background Service
        service_group = Adw.PreferencesGroup(
            title="Automatic Checks",
            description="Periodic checks for new releases while just-release is open"
        )
        page.add(service_group)

        self.interval_row = Adw.SpinRow(
            title="Check Interval (Hours)",
            subtitle="How frequently just-release checks GitHub",
            adjustment=Gtk.Adjustment(
                value=float(settings.get("poll_interval_hours", 4)),
                lower=1.0,
                upper=72.0,
                step_increment=1.0,
                page_increment=4.0
            )
        )
        service_group.add(self.interval_row)

        self.notif_row = Adw.SwitchRow(
            title="Desktop Notifications",
            subtitle="Show a native notification when an update is available or installed"
        )
        self.notif_row.set_active(bool(settings.get("desktop_notifications", True)))
        service_group.add(self.notif_row)

        # Group 3: GitHub API Authentication
        auth_group = Adw.PreferencesGroup(
            title="GitHub Authentication (Optional)",
            description="Allows private repo releases and increases rate limit from 60 to 5,000 requests/hr"
        )
        page.add(auth_group)

        self.token_row = Adw.PasswordEntryRow(title="GitHub Token (PAT)")
        self.token_row.set_text(settings.get("github_token", ""))
        auth_group.add(self.token_row)

        # Save Button in footer/header or on close
        save_group = Adw.PreferencesGroup()
        page.add(save_group)
        save_btn = Gtk.Button(label="Apply Settings")
        save_btn.add_css_class("suggested-action")
        save_btn.set_halign(Gtk.Align.CENTER)
        save_btn.connect("clicked", self.on_save_clicked)
        save_group.add(save_btn)

    def on_browse_manifest(self, btn):
        file_dialog = Gtk.FileDialog(title="Select or Create Releases Manifest")
        file_dialog.save(self, None, self.on_file_selected)

    def on_file_selected(self, dialog, result):
        try:
            f = dialog.save_finish(result)
            if f:
                self.manifest_row.set_text(f.get_path())
        except Exception:
            pass

    def on_save_clicked(self, btn):
        new_manifest = self.manifest_row.get_text().strip()
        new_settings = {
            "poll_interval_hours": int(self.interval_row.get_value()),
            "desktop_notifications": self.notif_row.get_active(),
            "github_token": self.token_row.get_text().strip()
        }
        # Switch manifests first, so the settings are written to the active file
        # rather than to the one being left behind.
        if new_manifest and new_manifest != str(self.config_manager.manifest_path):
            try:
                self.config_manager.set_manifest_path(new_manifest)
            except OSError as e:
                self.add_toast(Adw.Toast(title=f"Cannot use that manifest: {e}"))
                return
        self.config_manager.update_settings(new_settings)

        if self.on_saved:
            self.on_saved()
        self.close()
