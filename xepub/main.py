#!/usr/bin/python3
from __future__ import annotations
import sys
from pathlib import Path
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gio, Gtk, GLib
from xapp.util import l10n
from window import ReaderWindow
from setproctitle import setproctitle

APP_ID = "org.x.Xepub"
_ = l10n("xepub")


class XepubApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.windows_by_path = {}
        setproctitle("xepub")
        GLib.set_prgname("xepub")
        GLib.set_application_name(_("Book Reader"))

    def do_startup(self):
        Gtk.Application.do_startup(self)
        for name, callback in (("open-dialog", self.open_dialog), ("quit", lambda *_: self.quit())):
            action = Gio.SimpleAction.new(name, None); action.connect("activate", callback); self.add_action(action)
        self.set_accels_for_action("app.open-dialog", ["<Primary>o"])
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def do_activate(self):
        windows = self.get_windows()
        if windows:
            windows[0].present()
        else:
            ReaderWindow(self).present()

    def do_open(self, files, _count, _hint):
        for item in files:
            if item.get_path(): self.open_path(item.get_path())

    def open_path(self, filename):
        path = str(Path(filename).resolve())
        existing = self.windows_by_path.get(path)
        if existing:
            existing.present(); return
        blank = next((w for w in self.get_windows() if getattr(w, "book", None) is None), None)
        window = blank or ReaderWindow(self)
        before = window.book
        window.open_book(path)
        if window.book is not before and window.book is not None:
            self.windows_by_path[path] = window
            window.connect("destroy", lambda _w, p=path: self.windows_by_path.pop(p, None))
        window.present()

    def open_dialog(self, *_args):
        parent = self.get_active_window()
        dialog = Gtk.FileChooserNative.new(_("Open a Book…"), parent, Gtk.FileChooserAction.OPEN,
                                           _("Open"), _("Cancel"))
        epub_filter = Gtk.FileFilter(); epub_filter.set_name(_("EPUB books")); epub_filter.add_mime_type("application/epub+zip"); epub_filter.add_pattern("*.epub")
        dialog.add_filter(epub_filter)
        if dialog.run() == Gtk.ResponseType.ACCEPT: self.open_path(dialog.get_filename())
        dialog.destroy()


def main(): return XepubApplication().run(sys.argv)
if __name__ == "__main__": raise SystemExit(main())
