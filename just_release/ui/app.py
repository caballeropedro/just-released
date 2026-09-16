import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio
from .main_window import MainWindow
from ..config import ConfigManager

class JustReleaseApp(Adw.Application):
    def __init__(self, manifest_path=None):
        super().__init__(
            application_id="io.github.justrelease.Desktop",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )
        self.manifest_path = manifest_path

    def do_activate(self):
        Adw.init()
        config_manager = ConfigManager(self.manifest_path)
        win = self.props.active_window
        if not win:
            win = MainWindow(self, config_manager)
        win.present()
