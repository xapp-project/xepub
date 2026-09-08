from __future__ import annotations

import html as html_module
import json
import posixpath
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
gi.require_version("Soup", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Soup, WebKit2
from xapp.threading import run_idle
from xapp.util import l10n

from epub import CONTENT_SECURITY_POLICY, EpubBook, EpubError, xhtml_text
from paginator import command as paginator_command
from preferences import PreferencesDialog, search_engine_name
from state import StateStore

_ = l10n("xepub")


class ReaderWindow(Gtk.ApplicationWindow):
    DEFAULTS = {
        "font": "Serif", "size": 20, "line_height": 1.55, "margin": 64,
        "alignment": "left", "theme": "light", "publisher": True, "zoom": 1.0,
    }

    def __init__(self, application):
        super().__init__(application=application, title="Xepub")
        self.settings = Gio.Settings.new("org.x.xepub")
        self._sidebar_saved_page = self.settings.get_string("sidebar-page")
        self._building_ui = True
        width = self.settings.get_int("window-width")
        height = self.settings.get_int("window-height")
        self.set_default_size(width, height)
        self.set_position(Gtk.WindowPosition.CENTER)
        if self.settings.get_boolean("window-maximized"):
            self.maximize()
        self.book = None
        self.chapter = 0
        self.non_spine_resource = None
        self.pending_fraction = 0.0
        self.current_page = 1
        self.page_count = 1
        self._last_reader_size = None
        self._resize_source = 0
        self._resize_end_source = 0
        self._resize_fraction = 0.0
        self._resizing = False
        self._scroll_accumulator = 0.0
        self._zoom_scroll_accumulator = 0.0
        self._back_history = []
        self._search_source = 0
        self._preferences_refresh_source = 0
        self._preferences_fraction = 0.0
        self._pending_search_result = None
        self._last_content_click = None
        self._syncing_search_selection = False
        self.search_chapter_lists = []
        self.search_all_chapters = self.settings.get_boolean("search-all-chapters")
        self.search_case_sensitive = self.settings.get_boolean("search-case-sensitive")
        self.search_whole_words = self.settings.get_boolean("search-whole-words")
        self._page_turning = False
        self._layout_generation = 0
        self._syncing_toc = False
        self._syncing_sidebar_buttons = False
        self.store = StateStore()
        self.preferences = dict(self.DEFAULTS)
        self.preferences.update(self.store.preferences)
        self._build_webview()
        self._build_ui()
        self._building_ui = False
        self.connect("delete-event", self._on_close)
        self.connect("key-press-event", self._on_key)
        self.web.connect("size-allocate", self._reader_resized)
        self.web.connect("notify::scale-factor", self._reader_scale_changed)

    def _build_webview(self):
        context = WebKit2.WebContext.new_ephemeral()
        context.set_sandbox_enabled(True)
        context.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)
        proxy = WebKit2.NetworkProxySettings.new("http://127.0.0.1:9", None)
        for scheme in ("http", "https", "ftp", "ws", "wss"):
            proxy.add_proxy_for_scheme(scheme, "http://127.0.0.1:9")
        context.set_network_proxy_settings(WebKit2.NetworkProxyMode.CUSTOM, proxy)
        context.register_uri_scheme("xepub", self._serve_uri, None)
        self.web = WebKit2.WebView.new_with_context(context)
        self._install_network_filter()
        settings = self.web.get_settings()
        disabled = (
            "enable-javascript", "enable-javascript-markup", "enable-java", "enable-media",
            "enable-webaudio", "enable-webgl", "enable-webrtc", "enable-plugins",
            "enable-html5-local-storage", "enable-html5-database", "enable-offline-web-application-cache",
            "enable-developer-extras", "enable-dns-prefetching", "enable-page-cache",
            "enable-encrypted-media", "enable-media-stream", "enable-mediasource",
            "enable-hyperlink-auditing", "javascript-can-open-windows-automatically",
            "javascript-can-access-clipboard", "allow-file-access-from-file-urls",
            "allow-universal-access-from-file-urls", "allow-top-navigation-to-data-urls",
        )
        for name in disabled:
            if settings.find_property(name):
                settings.set_property(name, False)
        if settings.find_property("enable-private-browsing"):
            settings.set_property("enable-private-browsing", True)
        self.web.connect("decide-policy", self._decide_policy)
        self.web.connect("load-changed", self._load_changed)
        self.web.connect("context-menu", lambda *_args: True)
        self.web.connect("button-press-event", self._web_button)
        self.web.connect("button-release-event", self._web_button_released)
        self.web.connect("scroll-event", self._web_scroll)
        self.find_controller = self.web.get_find_controller()
        self.web.set_zoom_level(self.preferences["zoom"])

    def _install_network_filter(self):
        rules = [{"trigger": {"url-filter": "^%s:" % scheme},
                  "action": {"type": "block"}}
                 for scheme in ("http", "https", "ftp", "ws", "wss", "file")]
        self._filter_directory = tempfile.TemporaryDirectory(prefix="xepub-filter-")
        self._filter_store = WebKit2.UserContentFilterStore.new(self._filter_directory.name)
        self._filter_store.save(
            "offline", GLib.Bytes.new(json.dumps(rules).encode("utf-8")), None,
            self._network_filter_ready, None)

    def _network_filter_ready(self, store, result, _data):
        try:
            content_filter = store.save_finish(result)
            self.web.get_user_content_manager().add_filter(content_filter)
        except GLib.Error as error:
            GLib.warning("Could not install Xepub's network filter: %s", error.message)

    def _build_ui(self):
        builder = Gtk.Builder()
        builder.set_translation_domain("xepub")
        builder.add_from_file(str(Path(__file__).with_name("window.ui")))
        self.header = builder.get_object("header")
        self.set_titlebar(self.header)
        self.sidebar_switcher_slot = builder.get_object("sidebar_switcher_slot")
        builder.get_object("previous_button").connect("clicked", lambda _b: self.previous_page())
        builder.get_object("next_button").connect("clicked", lambda _b: self.next_page())
        menu_button = builder.get_object("menu_button")
        menu = Gio.Menu()
        for label, action in ((_("Preferences"), "win.preferences"),
                              (_("Book Information"), "win.info"),
                              (_("About"), "win.about"),
                              (_("Quit"), "app.quit")):
            menu.append(label, action)
        menu_button.set_menu_model(menu)

        self.sidebar = builder.get_object("sidebar")
        self.toc_model = Gtk.TreeStore(str, str)
        self.toc_view = Gtk.TreeView(model=self.toc_model, headers_visible=False)
        renderer = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
        self.toc_view.append_column(Gtk.TreeViewColumn(_("Contents"), renderer, text=0))
        self.toc_view.get_selection().connect("changed", self._toc_selection_changed)
        toc_scroll = Gtk.ScrolledWindow(); toc_scroll.add(self.toc_view)
        self.annotations_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.annotations_scroll = Gtk.ScrolledWindow()
        self.annotations_scroll.add(self.annotations_list)
        self.annotations_panel = Gtk.Stack()
        self.annotations_panel.add_named(self.annotations_scroll, "list")
        self.annotations_panel.add_named(
            self._empty_sidebar_panel("xsi-edit-symbolic", _("No annotations")), "empty")
        self.annotations_panel.set_visible_child_name("empty")
        self.bookmarks_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.bookmarks_list.connect("selected-rows-changed", self._bookmark_selection_changed)
        bookmarks_scroll = Gtk.ScrolledWindow(); bookmarks_scroll.add(self.bookmarks_list)
        self.bookmarks_panel = Gtk.Stack()
        self.bookmarks_panel.add_named(bookmarks_scroll, "list")
        self.bookmarks_panel.add_named(
            self._empty_sidebar_panel("xsi-user-bookmarks-symbolic", _("No bookmarks")), "empty")
        self.bookmarks_panel.set_visible_child_name("empty")
        self.search_entry = Gtk.Entry(margin=8)
        self.search_entry.set_placeholder_text(_("Search this book"))
        self.search_entry.connect("changed", self._search_changed)
        self.search_entry.connect("activate", self._search_next)
        self._build_search_options_menu()
        self.search_entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.PRIMARY, "xsi-edit-find-symbolic")
        self.search_entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.SECONDARY, "xsi-pan-down-symbolic")
        self.search_entry.set_icon_tooltip_text(
            Gtk.EntryIconPosition.SECONDARY, _("Search options"))
        self.search_entry.connect("icon-press", self._show_search_options)
        self.search_results_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        search_results_scroll = Gtk.ScrolledWindow(); search_results_scroll.add(self.search_results_list)
        self.search_results_panel = Gtk.Stack()
        self.search_results_panel.add_named(search_results_scroll, "results")
        self.search_results_panel.add_named(
            self._empty_sidebar_panel("xsi-edit-find-symbolic", _("Enter search terms")),
            "empty")
        self.search_results_panel.add_named(
            self._empty_sidebar_panel("xsi-edit-find-symbolic", _("No results")),
            "no-results")
        self.search_results_panel.set_visible_child_name("empty")
        search_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        search_panel.pack_start(self.search_entry, False, False, 0)
        search_panel.pack_start(self.search_results_panel, True, True, 0)
        self.sidebar_stack = Gtk.Stack()
        for child, name, title, icon in (
                (toc_scroll, "contents", _("Contents"), "view-list-symbolic"),
                (self.bookmarks_panel, "bookmarks", _("Bookmarks"), "xsi-user-bookmarks-symbolic"),
                (search_panel, "search", _("Search"), "xsi-edit-find-symbolic"),
                (self.annotations_panel, "annotations", _("Annotations"), "xsi-edit-symbolic")):
            self.sidebar_stack.add_titled(child, name, title)
            self.sidebar_stack.child_set_property(child, "icon-name", icon)
        self.sidebar.pack_start(self.sidebar_stack, True, True, 0)
        self.sidebar_buttons = {
            name: builder.get_object(name + "_button")
            for name in ("contents", "bookmarks", "search", "annotations")
        }
        self.sidebar_button_box = builder.get_object("sidebar_button_box")
        for name, button in self.sidebar_buttons.items():
            button.connect("clicked", self._sidebar_button_clicked, name)
        self.sidebar_stack.connect("notify::visible-child-name", self._sidebar_page_changed)

        self.reader_overlay = builder.get_object("reader_overlay")
        self.reader_overlay.add(self.web)
        self.transition_surface = builder.get_object("transition_surface")
        cover_style = Gtk.CssProvider()
        cover_style.load_from_data(b".chapter-loading-cover { background-color: @theme_bg_color; }")
        self.transition_surface.get_style_context().add_provider(cover_style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.reader_stack = builder.get_object("reader_stack")
        self.reader_stack.set_visible_child_name("placeholder")
        self.progress = builder.get_object("progress")
        self.add(builder.get_object("main_pane"))
        self._install_actions()
        self.show_all()
        # show_all() makes every child of nested stacks visible. Reassert the
        # initial empty states afterwards so a sidebar page can never expose
        # its blank list while no content has been populated yet.
        self.bookmarks_panel.set_visible_child_name("empty")
        self.annotations_panel.set_visible_child_name("empty")
        self.search_results_panel.set_visible_child_name("empty")
        self._build_word_menu()
        self._build_reader_context_menu()
        if self._sidebar_saved_page != "none":
            self._syncing_sidebar_buttons = True
            self.sidebar_stack.set_visible_child_name(self._sidebar_saved_page)
            self._syncing_sidebar_buttons = False
        self._set_sidebar_visible(False, persist=False)

    def _set_sidebar_visible(self, visible, persist=True):
        self.sidebar.set_visible(visible)
        if hasattr(self, "sidebar_buttons"):
            active = self.sidebar_stack.get_visible_child_name() if visible else None
            self._syncing_sidebar_buttons = True
            try:
                for name, button in self.sidebar_buttons.items():
                    button.set_active(name == active)
            finally:
                self._syncing_sidebar_buttons = False
        if persist:
            page = self.sidebar_stack.get_visible_child_name() if visible else "none"
            self._sidebar_saved_page = page
            self.settings.set_string("sidebar-page", page)

    def _sidebar_button_clicked(self, _button, name):
        if self._syncing_sidebar_buttons:
            return
        if (self.sidebar.get_visible() and
                self.sidebar_stack.get_visible_child_name() == name):
            self._set_sidebar_visible(False)
            return
        self.sidebar_stack.set_visible_child_name(name)
        self._set_sidebar_visible(True)
        if name == "search":
            self._focus_search_entry()

    @run_idle
    def _focus_search_entry(self):
        self.search_entry.grab_focus()

    @staticmethod
    def _empty_sidebar_panel(icon, message):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DIALOG)
        image.get_style_context().add_class("dim-label")
        label = Gtk.Label(label=message)
        label.get_style_context().add_class("dim-label")
        box.pack_start(image, False, False, 0); box.pack_start(label, False, False, 0)
        return box

    def _sidebar_page_changed(self, stack, _param):
        if self._building_ui:
            return
        self._set_sidebar_visible(True)
        if stack.get_visible_child_name() == "bookmarks":
            self._refresh_bookmarks_sidebar()
        elif stack.get_visible_child_name() == "annotations":
            self._refresh_annotations_sidebar()

    def _refresh_bookmarks_sidebar(self):
        for child in self.bookmarks_list.get_children():
            self.bookmarks_list.remove(child)
        if not self.book:
            self.bookmarks_panel.set_visible_child_name("empty")
            return
        marks = self.store.book(self.book.identifier).get("bookmarks", [])
        ordered_marks = sorted(enumerate(marks),
                               key=lambda item: (item[1]["chapter"],
                                                 item[1]["fraction"]))
        for index, mark in ordered_marks:
            row = Gtk.ListBoxRow()
            row.bookmark_index = index
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin=4)
            label = Gtk.Label(label=mark.get("label", _("Bookmark")), xalign=0,
                              ellipsize=Pango.EllipsizeMode.END, margin=6)
            label.set_hexpand(True)
            remove = Gtk.Button.new_from_icon_name("edit-delete-symbolic", Gtk.IconSize.BUTTON)
            remove.set_relief(Gtk.ReliefStyle.NONE)
            remove.set_tooltip_text(_("Remove bookmark"))
            remove.connect("clicked", self._remove_bookmark, index)
            content.pack_start(label, True, True, 0)
            content.pack_end(remove, False, False, 0)
            row.add(content); self.bookmarks_list.add(row)
        self.bookmarks_list.show_all()
        self.bookmarks_panel.set_visible_child_name("list" if marks else "empty")

    def _remove_bookmark(self, _button, index):
        if not self.book:
            return
        marks = self.store.book(self.book.identifier).get("bookmarks", [])
        if 0 <= index < len(marks):
            del marks[index]
            self.store.save()
            self._refresh_bookmarks_sidebar()

    def _bookmark_selection_changed(self, listbox):
        row = listbox.get_selected_row()
        if row is None:
            return
        marks = self.store.book(self.book.identifier).get("bookmarks", []) if self.book else []
        if row.bookmark_index < len(marks):
            mark = marks[row.bookmark_index]
            self._remember_location()
            self.chapter = mark["chapter"]
            self.pending_fraction = mark["fraction"]
            self.load_chapter()

    def _chapter_label(self, chapter):
        path = self.book.spine[chapter]
        return next((item.label for item in self._flat_toc(self.book.toc)
                     if posixpath.normpath(unquote(urlsplit(item.href).path)) == path),
                    Path(path).stem)

    def _refresh_annotations_sidebar(self):
        self._annotation_rows = {}
        self._annotation_lists = []
        for child in self.annotations_list.get_children():
            self.annotations_list.remove(child)
        if not self.book:
            self.annotations_panel.set_visible_child_name("empty")
            return
        annotations = self.store.book(self.book.identifier).get("annotations", [])
        colors = {"yellow": "#f5d90a", "green": "#5ec962",
                  "blue": "#4b9cff", "pink": "#f469aa"}
        for chapter in sorted({item.get("chapter", 0) for item in annotations}):
            expander = Gtk.Expander(label=self._chapter_label(chapter), expanded=True, margin=6)
            cards = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
            cards.set_activate_on_single_click(False)
            cards.connect("row-activated", self._annotation_row_activated)
            self._annotation_lists.append(cards)
            for annotation in (item for item in annotations if item.get("chapter") == chapter):
                row = Gtk.ListBoxRow(); row.annotation = annotation
                self._annotation_rows[annotation.get("id")] = (cards, row, expander)
                card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin=10)
                dot = Gtk.Label()
                dot.set_markup('<span size="large" foreground="%s">●</span>' %
                               colors.get(annotation.get("color"), colors["yellow"]))
                text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
                quote = Gtk.Label(label=annotation.get("text", ""), xalign=0,
                                  wrap=True, max_width_chars=30, lines=3,
                                  ellipsize=Pango.EllipsizeMode.END)
                text.pack_start(quote, False, False, 0)
                if annotation.get("note"):
                    note = Gtk.Label(label=annotation["note"], xalign=0, wrap=True,
                                     max_width_chars=30, lines=2,
                                     ellipsize=Pango.EllipsizeMode.END)
                    note.get_style_context().add_class("dim-label")
                    text.pack_start(note, False, False, 0)
                date = Gtk.Label(label=annotation.get("created", "").replace("T", " "), xalign=0)
                date.get_style_context().add_class("dim-label")
                text.pack_start(date, False, False, 0)
                remove = Gtk.Button.new_from_icon_name("edit-delete-symbolic", Gtk.IconSize.BUTTON)
                remove.set_relief(Gtk.ReliefStyle.NONE)
                remove.set_tooltip_text(_("Delete annotation"))
                remove.connect("clicked", self._remove_annotation, annotation.get("id"))
                card.pack_start(dot, False, False, 0); card.pack_start(text, True, True, 0)
                card.pack_end(remove, False, False, 0)
                row.add(card); cards.add(row)
            expander.add(cards)
            outer = Gtk.ListBoxRow(selectable=False, activatable=False); outer.add(expander)
            self.annotations_list.add(outer)
        self.annotations_list.show_all()
        self.annotations_panel.set_visible_child_name("list" if annotations else "empty")

    def _select_annotation_sidebar(self, annotation_id):
        self.sidebar_stack.set_visible_child_name("annotations")
        self._set_sidebar_visible(True)
        target = self._annotation_rows.get(annotation_id)
        if not target:
            return
        for cards in self._annotation_lists:
            cards.unselect_all()
        cards, row, expander = target
        expander.set_expanded(True)
        cards.select_row(row)
        row.grab_focus()
        GLib.idle_add(self._scroll_annotation_row_into_view, row)

    def _scroll_annotation_row_into_view(self, row):
        if not row.get_mapped():
            return GLib.SOURCE_REMOVE
        coordinates = row.translate_coordinates(self.annotations_list, 0, 0)
        if coordinates is None:
            return GLib.SOURCE_REMOVE
        _x, y = coordinates
        adjustment = self.annotations_scroll.get_vadjustment()
        top = adjustment.get_value()
        bottom = top + adjustment.get_page_size()
        row_bottom = y + row.get_allocated_height()
        if y < top:
            adjustment.set_value(y)
        elif row_bottom > bottom:
            adjustment.set_value(row_bottom - adjustment.get_page_size())
        return GLib.SOURCE_REMOVE

    def _remove_annotation(self, _button, annotation_id):
        annotations = self.store.book(self.book.identifier).setdefault("annotations", [])
        annotations[:] = [item for item in annotations if item.get("id") != annotation_id]
        self.store.save(); self._refresh_annotations_sidebar(); self._render_annotations()

    def _annotation_row_activated(self, _listbox, row):
        self.selection_annotation = row.annotation
        self.selection_text = row.annotation.get("text", "")
        self._edit_annotation_dialog()

    def _install_actions(self):
        actions = {
            "find": self.show_find, "bookmark": self.add_bookmark, "bookmarks": self.show_bookmarks,
            "preferences": self.show_preferences, "info": self.show_info,
            "fullscreen": self.toggle_fullscreen, "about": self.show_about,
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb()); self.add_action(action)

    def open_book(self, filename: str):
        try:
            candidate = EpubBook(filename)
        except EpubError as exc:
            self._error(_("Could not open book"), str(exc)); return
        self.save_position()
        if self.book: self.book.close()
        self.book = candidate
        self._back_history.clear()
        self.sidebar_button_box.set_sensitive(True)
        if self._sidebar_saved_page != "none":
            self._syncing_sidebar_buttons = True
            self.sidebar_stack.set_visible_child_name(self._sidebar_saved_page)
            self._syncing_sidebar_buttons = False
        self._set_sidebar_visible(self._sidebar_saved_page != "none", persist=False)
        self.search_entry.set_text("")
        self.find_controller.search_finish()
        self._show_search_results([], empty_state="empty")
        self.progress.show()
        state = self.store.book(candidate.identifier)
        self.chapter = min(int(state.get("chapter", 0)), len(candidate.spine) - 1)
        self.pending_fraction = float(state.get("fraction", 0.0))
        self.set_title(candidate.metadata.get("title", Path(filename).stem) + " — Xepub")
        self.header.set_title(candidate.metadata.get("title", Path(filename).stem))
        self.header.set_subtitle(candidate.metadata.get("creator", ""))
        self._populate_toc()
        self._refresh_bookmarks_sidebar()
        self._refresh_annotations_sidebar()
        resource = state.get("resource")
        if not resource or not self._load_non_spine_resource(resource, save_current=False):
            self.load_chapter()

    def _serve_uri(self, request, _data=None):
        if not self.book:
            request.finish_error(GLib.Error(message="No active book")); return
        uri = urlsplit(request.get_uri())
        if uri.scheme != "xepub" or uri.netloc != "book":
            request.finish_error(GLib.Error(message="Denied resource origin")); return
        path = posixpath.normpath(unquote(uri.path).lstrip("/"))
        try:
            data, mime = self.book.resource(path)
            stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
            response = WebKit2.URISchemeResponse.new(stream, len(data))
            response.set_content_type(mime)
            headers = Soup.MessageHeaders.new(Soup.MessageHeadersType.RESPONSE)
            headers.append("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            response.set_http_headers(headers)
            request.finish_with_response(response)
        except Exception as exc:
            request.finish_error(GLib.Error(message=str(exc)))

    def _decide_policy(self, _view, decision, decision_type):
        if decision_type != WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            return False
        uri = decision.get_request().get_uri()
        parts = urlsplit(uri)
        if parts.scheme == "xepub" and parts.netloc == "book":
            path = posixpath.normpath(unquote(parts.path).lstrip("/"))
            if self.book and path in self.book.entries:
                action = decision.get_navigation_action()
                if action.get_navigation_type() == WebKit2.NavigationType.LINK_CLICKED:
                    self._remember_location()
                if path in self.book.spine and path != self.book.spine[self.chapter]:
                    self.save_position(); self.chapter = self.book.spine.index(path)
                    self.non_spine_resource = None
                    self._sync_internal_navigation()
                return False
        decision.ignore()
        if parts.scheme in {"http", "https"}:
            self._confirm_external(uri)
        return True

    @run_idle
    def _sync_internal_navigation(self):
        self._select_current_toc()
        self._update_progress()

    def _confirm_external(self, uri):
        dialog = Gtk.MessageDialog(self, Gtk.DialogFlags.MODAL, Gtk.MessageType.QUESTION,
                                   Gtk.ButtonsType.NONE, _("Open external link?"))
        dialog.format_secondary_text(uri); dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Open in browser"), Gtk.ResponseType.OK)
        if dialog.run() == Gtk.ResponseType.OK: Gio.AppInfo.launch_default_for_uri(uri, None)
        dialog.destroy()

    def reading_css(self):
        p = self.preferences
        # Sepia uses a custom page palette but the surrounding GTK interface
        # belongs to the light theme variant.
        Gtk.Settings.get_default().set_property(
            "gtk-application-prefer-dark-theme", p["theme"] == "dark")
        if p["theme"] == "sepia":
            colors = ("#eee2c6", "#4a3826")
        else:
            # Ask GTK to load the selected variant, then use the theme's own
            # colors for the reading surface rather than maintaining a second
            # light/dark palette inside the application.
            context = self.get_style_context()
            bg_found, background = context.lookup_color("theme_bg_color")
            fg_found, foreground = context.lookup_color("theme_fg_color")
            if not bg_found:
                background = context.get_background_color(Gtk.StateFlags.NORMAL)
            if not fg_found:
                foreground = context.get_color(Gtk.StateFlags.NORMAL)
            colors = (background.to_string(), foreground.to_string())
        typography = "" if p["publisher"] else (
            f"font-family:{p['font']} !important; font-size:{p['size']}px !important; "
            f"line-height:{p['line_height']} !important; text-align:{p['alignment']} !important;")
        reader_style = "" if p["publisher"] else (
            "body * { font-family:inherit !important; font-size:inherit !important; "
            "line-height:inherit !important; text-align:inherit !important; "
            "color:inherit !important; background:transparent !important; }")
        direction = "rtl" if self.book and self.book.page_progression == "rtl" else "ltr"
        return f"""
html {{ margin:0 !important; padding:0 !important; width:100%; height:100%; overflow:hidden;
 background:{colors[0]} !important; color:{colors[1]} !important; direction:{direction}; }}
body {{ margin:0 {p['margin']}px !important; box-sizing:border-box;
 width:calc(100vw - {p['margin']*2}px); height:100vh; overflow:visible;
 column-width:calc(100vw - {p['margin']*2}px); column-gap:{p['margin']*2}px; column-fill:auto;
 padding:{max(24,p['margin']//2)}px 0 !important;
 {typography} }}
img, svg {{ max-width:100%; max-height:90vh; object-fit:contain; }} a {{ color:#5c6f91; }}
pre, table {{ max-width:100%; overflow-wrap:anywhere; }} {reader_style}
.xepub-annotation {{ border-radius:2px; box-decoration-break:clone; -webkit-box-decoration-break:clone; }}
.xepub-annotation[data-color="yellow"] {{ background:rgba(255,224,0,.58) !important; }}
.xepub-annotation[data-color="green"] {{ background:rgba(94,201,98,.48) !important; }}
.xepub-annotation[data-color="blue"] {{ background:rgba(75,156,255,.42) !important; }}
.xepub-annotation[data-color="pink"] {{ background:rgba(244,105,170,.44) !important; }}
"""

    def load_chapter(self, fragment=""):
        if not self.book: return
        self._layout_generation += 1
        self._page_turning = False
        self.non_spine_resource = None
        self._select_current_toc()
        # Fade the old page out fully. The WebView is then a hidden stack child
        # while the next chapter loads and moves to its requested page.
        self.reader_stack.set_visible_child_name("transition")
        self._apply_reading_styles()
        uri = "xepub://book/" + self.book.spine[self.chapter] + fragment
        GLib.timeout_add(self.reader_stack.get_transition_duration() + 20,
                         self._begin_chapter_load, uri, self._layout_generation)
        self._update_progress()

    def _apply_reading_styles(self):
        self.web.get_user_content_manager().remove_all_style_sheets()
        sheet = WebKit2.UserStyleSheet(self.reading_css(), WebKit2.UserContentInjectedFrames.ALL_FRAMES,
                                       WebKit2.UserStyleLevel.USER, None, None)
        self.web.get_user_content_manager().add_style_sheet(sheet)

    def _begin_chapter_load(self, uri, generation):
        if generation == self._layout_generation:
            self.web.load_uri(uri)
        return False

    def _load_changed(self, _view, event):
        if event == WebKit2.LoadEvent.FINISHED:
            GLib.timeout_add(120, self._restore_scroll, self._layout_generation)

    def _restore_scroll(self, generation):
        if generation != self._layout_generation:
            return False
        fraction = max(0.0, min(1.0, self.pending_fraction))
        self._paginate("restoreFraction", fraction, callback=self._restore_complete)
        self.pending_fraction = 0
        return False

    def _restore_complete(self, value):
        self._metrics_result(value)
        self._render_annotations()
        self.reader_stack.set_visible_child_name("reader")
        if self._pending_search_result:
            text, occurrence = self._pending_search_result
            self._pending_search_result = None
            self._queue_search_occurrence(text, occurrence)

    @run_idle
    def _queue_search_occurrence(self, text, occurrence):
        self._activate_search_occurrence(text, occurrence)

    def _reader_resized(self, _widget, allocation):
        size = (allocation.width, allocation.height)
        if size == self._last_reader_size:
            return
        self._last_reader_size = size
        if not self.book:
            return
        # Keep one position anchor for the complete resize gesture. Repeated
        # allocations must not reinterpret it using intermediate geometry.
        if not self._resize_end_source:
            self._resize_fraction = ((self.current_page - 1) /
                                     max(1, self.page_count - 1))
            # Hide the resizing WebKit surface immediately and without an
            # animation. Otherwise its old column offset exposes adjacent
            # content as the viewport expands.
            self.reader_stack.set_transition_type(Gtk.StackTransitionType.NONE)
            self.reader_stack.set_visible_child_name("transition")
        if self._resize_end_source:
            GLib.source_remove(self._resize_end_source)
        # Leave WebKit alone during the gesture. Reflow once after the final
        # allocation, behind the same short transition used for page turns.
        self._resizing = True
        self._resize_end_source = GLib.timeout_add(220, self._end_resize)

    def _reader_scale_changed(self, web, _param):
        # A move between normal and HiDPI monitors may leave the logical GTK
        # allocation unchanged, but WebKit's CSS viewport must be remeasured.
        self._last_reader_size = None
        self._reader_resized(web, web.get_allocation())

    @run_idle
    def _finish_resize(self):
        self._resize_source = 0
        if not self.book:
            return
        self._paginate("restoreAnchor", self._resize_fraction,
                       callback=self._resize_complete)

    def _end_resize(self):
        self._resize_end_source = 0
        self.reader_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.reader_stack.set_transition_duration(60)
        self._finish_resize()
        return False

    def _resize_complete(self, value):
        self._metrics_result(value)
        self.save_position()
        self.reader_stack.set_visible_child_name("reader")
        self._resizing = False

    def _trusted_eval(self, source, callback=None):
        """Run fixed application code; page scripts remain disabled at all other times."""
        settings = self.web.get_settings(); settings.set_property("enable-javascript", True)
        def finished(view, result, _data):
            try:
                value = view.evaluate_javascript_finish(result)
                if callback: callback(value.to_string())
            except GLib.Error:
                pass
            finally:
                settings.set_property("enable-javascript", False)
        self.web.evaluate_javascript(source, -1, None, None, None, finished, None)

    def _paginate(self, method, *arguments, callback=None):
        generation = self._layout_generation
        def current(value):
            if generation == self._layout_generation and callback:
                callback(value)
        self._trusted_eval(paginator_command(method, *arguments), current)

    def _metrics_result(self, value):
        try:
            metrics = json.loads(value)
            self.page_count = max(1, int(metrics["pages"]))
            self.current_page = min(self.page_count, int(metrics["index"]) + 1)
        except (ValueError, KeyError, TypeError):
            self.current_page = self.page_count = 1
        self._update_progress()

    def _move_page(self, amount):
        if not self.book or self._page_turning or self._resizing: return
        if self.non_spine_resource:
            if amount > 0:
                self.chapter = 0
                self.pending_fraction = 0.0
                self.load_chapter()
            return
        if (amount > 0 and self.current_page >= self.page_count) or (amount < 0 and self.current_page <= 1):
            (self.next_chapter if amount > 0 else self.previous_chapter)()
        else:
            self._page_turning = True
            self.reader_stack.set_visible_child_name("transition")
            GLib.timeout_add(self.reader_stack.get_transition_duration() + 10,
                             self._perform_page_move, amount)

    def _perform_page_move(self, amount):
        target_page = max(0, min(self.page_count - 1,
                                 self.current_page - 1 + amount))
        self._paginate("go", target_page, callback=self._page_move_complete)
        return False

    def _page_move_complete(self, value):
        self._metrics_result(value)
        self.save_position()
        self.reader_stack.set_visible_child_name("reader")
        self._page_turning = False

    def next_page(self): self._move_page(1)
    def previous_page(self): self._move_page(-1)
    def next_chapter(self): self._change_chapter(1)
    def previous_chapter(self): self._change_chapter(-1)

    def _change_chapter(self, delta):
        if self.book and 0 <= self.chapter + delta < len(self.book.spine):
            self.save_position(); self.chapter += delta
            self.pending_fraction = 0.0 if delta > 0 else 1.0; self.load_chapter()
        elif self.book and delta < 0 and self.chapter == 0:
            leading = self._leading_toc_resource()
            if leading:
                self._load_non_spine_resource(leading)

    def _leading_toc_resource(self):
        """Return a safe TOC resource placed before the first spine chapter."""
        first_spine = self.book.spine[0]
        allowed = {"application/xhtml+xml", "text/html", "image/jpeg", "image/png",
                   "image/gif", "image/webp", "image/svg+xml"}
        for entry in self._flat_toc(self.book.toc):
            path = posixpath.normpath(unquote(urlsplit(entry.href).path))
            if path == first_spine:
                break
            item = next((candidate for candidate in self.book.manifest.values()
                         if candidate.path == path), None)
            if path in self.book.entries and item and item.media_type in allowed:
                return path
        return None

    def _load_non_spine_resource(self, path, fragment="", save_current=True):
        item = next((entry for entry in self.book.manifest.values() if entry.path == path), None)
        allowed = {"application/xhtml+xml", "text/html", "image/jpeg", "image/png",
                   "image/gif", "image/webp", "image/svg+xml"}
        if path not in self.book.entries or not item or item.media_type not in allowed:
            return False
        if save_current:
            self.save_position()
        self._layout_generation += 1
        self.non_spine_resource = path
        self.pending_fraction = 0.0
        self._select_current_toc(path)
        self.reader_stack.set_visible_child_name("transition")
        self._apply_reading_styles()
        uri = "xepub://book/" + path
        if fragment:
            uri += "#" + fragment
        GLib.timeout_add(self.reader_stack.get_transition_duration() + 20,
                         self._begin_chapter_load, uri, self._layout_generation)
        return True

    def save_position(self):
        if not self.book: return
        overall = round((self.chapter + self.current_page / self.page_count) /
                        len(self.book.spine) * 100)
        state = self.store.book(self.book.identifier)
        state.update({"path": str(self.book.filename), "chapter": self.chapter,
                      "fraction": (self.current_page - 1) / max(1, self.page_count - 1),
                      "progress": overall})
        if self.non_spine_resource:
            state["resource"] = self.non_spine_resource
        else:
            state.pop("resource", None)
        self.store.preferences.update(self.preferences); self.store.save()
        epub_file = Gio.File.new_for_path(str(self.book.filename))
        for attribute, value in (
                ("metadata::xepub::progress", str(overall)),
                ("metadata::xepub::title", self.book.metadata.get("title", "")),
                ("metadata::xepub::author", self.book.metadata.get("creator", ""))):
            epub_file.set_attribute_string(
                attribute, value, Gio.FileQueryInfoFlags.NONE, None)

    def _remember_location(self):
        if not self.book:
            return
        location = {
            "chapter": self.chapter,
            "fraction": (self.current_page - 1) / max(1, self.page_count - 1),
            "resource": self.non_spine_resource,
        }
        if not self._back_history or self._back_history[-1] != location:
            self._back_history.append(location)
            del self._back_history[:-100]

    def go_back(self):
        if not self.book or not self._back_history:
            return
        location = self._back_history.pop()
        self.chapter = location["chapter"]
        self.pending_fraction = location["fraction"]
        resource = location["resource"]
        if resource:
            self._load_non_spine_resource(resource, save_current=False)
        else:
            self.load_chapter()

    def _update_progress(self):
        if not self.book: return
        pages, page = self.page_count, self.current_page
        overall = (self.chapter + page / pages) / len(self.book.spine) * 100
        chapter = next((x.label for x in self._flat_toc(self.book.toc)
                        if urlsplit(x.href).path == self.book.spine[self.chapter]), Path(self.book.spine[self.chapter]).stem)
        self.progress.set_text(_("%(chapter)s · Page %(page)d of %(pages)d · %(progress)d%%") %
                               {"chapter": chapter, "page": page, "pages": pages, "progress": overall})

    def _flat_toc(self, entries):
        for item in entries:
            yield item; yield from self._flat_toc(item.children)

    def _populate_toc(self):
        self.toc_model.clear()
        def add(entries, parent=None):
            for item in entries:
                row = self.toc_model.append(parent, [item.label, item.href]); add(item.children, row)
        add(self.book.toc)

    def _toc_selection_changed(self, selection):
        if self._syncing_toc or not self.book:
            return
        model, tree_iter = selection.get_selected()
        if tree_iter is None:
            return
        href = model[tree_iter][1]; parts = urlsplit(href); path = posixpath.normpath(unquote(parts.path))
        if path in self.book.spine:
            self._remember_location()
            self.save_position(); self.chapter = self.book.spine.index(path); self.pending_fraction = 0
            self.load_chapter("#" + parts.fragment if parts.fragment else "")
        elif path in self.book.entries:
            item = next((entry for entry in self.book.manifest.values() if entry.path == path), None)
            mime = item.media_type if item else ""
            if mime in {"application/xhtml+xml", "text/html", "image/jpeg", "image/png",
                        "image/gif", "image/webp", "image/svg+xml"}:
                self._remember_location()
                self._load_non_spine_resource(path, parts.fragment)

    def _select_current_toc(self, target=None):
        if not self.book or not self.book.toc:
            return
        target = target or self.book.spine[self.chapter]
        match = [None]

        def find_row(model, tree_path, tree_iter, _data):
            href_path = posixpath.normpath(unquote(urlsplit(model[tree_iter][1]).path))
            if href_path == target:
                match[0] = tree_iter.copy()
                return True
            return False

        self.toc_model.foreach(find_row, None)
        if match[0] is not None:
            self._syncing_toc = True
            selection = self.toc_view.get_selection()
            selection.select_iter(match[0])
            tree_path = self.toc_model.get_path(match[0])
            self.toc_view.expand_to_path(tree_path)
            self.toc_view.scroll_to_cell(tree_path, None, False, 0, 0)
            self._syncing_toc = False

    def _on_key(self, _widget, event):
        key = Gdk.keyval_name(event.keyval); ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if isinstance(self.get_focus(), Gtk.Entry) and key in (
                "Right", "Left", "Up", "Down", "Page_Up", "Page_Down",
                "space", "BackSpace", "Return", "KP_Enter"):
            return False
        if key in ("Right", "Page_Down", "space") and not ctrl: self.next_page(); return True
        if key in ("Left", "Page_Up", "BackSpace") and not ctrl: self.previous_page(); return True
        if key == "Down" and ctrl: self.next_chapter(); return True
        if key == "Up" and ctrl: self.previous_chapter(); return True
        if key in ("plus", "equal", "KP_Add") and ctrl:
            self._set_zoom(self.preferences["zoom"] + 0.1); return True
        if key in ("minus", "KP_Subtract") and ctrl:
            self._set_zoom(self.preferences["zoom"] - 0.1); return True
        if key in ("0", "KP_0") and ctrl:
            self._set_zoom(1.0); return True
        if key == "f" and ctrl: self.show_find(); return True
        if key == "b" and ctrl: self.add_bookmark(); return True
        if key == "F11": self.toggle_fullscreen(); return True
        if key == "Escape" and self.get_window().get_state() & Gdk.WindowState.FULLSCREEN:
            self.unfullscreen(); return True
        return False

    def _web_button(self, _web, event):
        if event.button == 8: self.previous_page(); return True
        if event.button == 9: self.next_page(); return True
        if event.button == 3 and self.book:
            self.go_back_menu_item.set_sensitive(bool(self._back_history))
            self.add_bookmark_menu_item.set_sensitive(not self._has_current_bookmark())
            self.reader_context_menu.popup_at_pointer(event)
            return True
        return False

    def _build_reader_context_menu(self):
        self.reader_context_menu = Gtk.Menu()
        self.go_back_menu_item = Gtk.ImageMenuItem.new_with_label(_("Go Back"))
        self.go_back_menu_item.set_image(Gtk.Image.new_from_icon_name(
            "go-previous-symbolic", Gtk.IconSize.MENU))
        self.go_back_menu_item.set_always_show_image(True)
        self.go_back_menu_item.connect("activate", lambda _item: self.go_back())
        self.reader_context_menu.append(self.go_back_menu_item)
        self.reader_context_menu.append(Gtk.SeparatorMenuItem())
        self.add_bookmark_menu_item = Gtk.ImageMenuItem.new_with_label(_("Add bookmark"))
        self.add_bookmark_menu_item.set_image(Gtk.Image.new_from_icon_name(
            "xsi-bookmark-new-symbolic", Gtk.IconSize.MENU))
        self.add_bookmark_menu_item.set_always_show_image(True)
        self.add_bookmark_menu_item.connect("activate", lambda _item: self.add_bookmark())
        self.reader_context_menu.append(self.add_bookmark_menu_item)
        self.reader_context_menu.show_all()

    def _build_word_menu(self):
        self.selection_text = ""
        self.selection_annotation = None
        if hasattr(self, "word_menu"):
            self.word_menu.destroy()
        self.word_menu = Gtk.Menu()
        for engine_type, custom_name, url in self.settings.get_value(
                "search-engines").unpack():
            item = Gtk.MenuItem.new_with_label(
                search_engine_name(engine_type, custom_name))
            item.connect("activate", self._lookup_with_engine, url)
            self.word_menu.append(item)
        self.word_menu.show_all()

    def _web_button_released(self, _web, event):
        if event.button != 1 or not self.book:
            return False
        settings = Gtk.Settings.get_default()
        double_time = settings.get_property("gtk-double-click-time")
        double_distance = settings.get_property("gtk-double-click-distance")
        previous = self._last_content_click
        annotation_double_click = bool(
            previous and event.time - previous[0] <= double_time and
            abs(event.x - previous[1]) <= double_distance and
            abs(event.y - previous[2]) <= double_distance)
        self._last_content_click = (event.time, event.x, event.y)
        self._inspect_selection(int(event.x), int(event.y), annotation_double_click)
        return False

    @run_idle
    def _inspect_selection(self, x, y, annotation_double_click):
        force_annotation = "true" if annotation_double_click else "false"
        zoom = self.web.get_zoom_level()
        hit_x = x / zoom
        hit_y = y / zoom
        self._trusted_eval(
            f"(()=>{{const s=getSelection();"
            f"const mark=document.elementFromPoint({hit_x},{hit_y})?.closest('.xepub-annotation');"
            "if(mark){"
            f"if({force_annotation}){{const selected=document.createRange();"
            "selected.selectNodeContents(mark);s.removeAllRanges();s.addRange(selected);}"
            "const r=mark.getBoundingClientRect();"
            f"return JSON.stringify({{annotation:mark.dataset.annotationId,edit:{force_annotation},"
            "x:r.left,y:r.top,w:r.width,h:r.height});}"
            "if(!s||s.isCollapsed)return '';"
            "const range=s.getRangeAt(0),text=s.toString();if(!text.trim())return '';"
            "const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);"
            "let n,offset=0,start=-1,end=-1;while(n=walker.nextNode()){"
            "if(n===range.startContainer)start=offset+range.startOffset;"
            "if(n===range.endContainer){end=offset+range.endOffset;break;}offset+=n.data.length;}"
            "if(start<0||end<=start)return '';"
            "const r=s.getRangeAt(0).getBoundingClientRect();"
            "return JSON.stringify({text:text.slice(0,16384),start:start,end:end,x:r.left,y:r.top,w:r.width,h:r.height});})()",
            lambda value: self._show_selection_popover(value, x, y))
        return False

    def _show_selection_popover(self, value, pointer_x, pointer_y):
        try:
            selection = json.loads(value)
            if selection.get("annotation"):
                annotation_id = selection["annotation"]
                annotation = next((item for item in self.store.book(
                    self.book.identifier).get("annotations", [])
                    if item.get("id") == annotation_id), None)
                if annotation:
                    self.selection_annotation = annotation
                    self.selection_text = annotation.get("text", "")
                    self._copy_text_to_clipboard(self.selection_text)
                    self._select_annotation_sidebar(annotation_id)
                    if selection.get("edit"):
                        self._edit_annotation_dialog(pointer_x, pointer_y)
                return
            text = selection["text"]
            if not text:
                return
            rect = Gdk.Rectangle()
            rect.x = pointer_x
            rect.y = pointer_y + 10
            rect.width = 1
            rect.height = 0
        except (TypeError, ValueError, KeyError):
            return
        self.selection_text = text
        self._copy_text_to_clipboard(text)
        if re.fullmatch(r"\w+(?:['’\-]\w+)*", text.strip(), re.UNICODE):
            self.selection_text = text.strip()
            self.word_menu.popup_at_rect(
                self.web.get_window(), rect,
                Gdk.Gravity.SOUTH, Gdk.Gravity.NORTH, None)
            return
        annotations = self.store.book(self.book.identifier).setdefault("annotations", [])
        annotation = next((item for item in annotations
                           if item.get("chapter") == self.chapter and
                           item.get("start") == selection["start"] and
                           item.get("end") == selection["end"]), None)
        new_annotation = annotation is None
        if new_annotation:
            annotation = {
                "id": uuid.uuid4().hex, "chapter": self.chapter,
                "start": selection["start"], "end": selection["end"],
                "text": text.strip(), "color": "yellow", "note": "",
                "created": datetime.now().isoformat(timespec="minutes"),
            }
            annotations.append(annotation)
            self.store.save()
            self._render_annotations()
            self._refresh_annotations_sidebar()
        self.selection_annotation = annotation
        self._edit_annotation_dialog(pointer_x, pointer_y, new_annotation)

    @staticmethod
    def _copy_text_to_clipboard(text):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)

    def _lookup_with_engine(self, _item, url):
        Gio.AppInfo.launch_default_for_uri(
            url.replace("{text}", quote(self.selection_text)), None)

    def _edit_annotation_dialog(self, pointer_x=None, pointer_y=None,
                                remove_on_cancel=False):
        dialog = Gtk.Dialog(_("Annotation"), self, Gtk.DialogFlags.MODAL,
                            (_("Cancel"), Gtk.ResponseType.CANCEL,
                             _("Save"), Gtk.ResponseType.OK))
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=12)
        color_values = (("yellow", _("Yellow"), "#f5d90a"),
                        ("pink", _("Pink"), "#f469aa"),
                        ("blue", _("Blue"), "#4b9cff"),
                        ("green", _("Green"), "#5ec962"))
        color_model = Gtk.ListStore(str, str)
        for color_id, label, value in color_values:
            color_model.append((color_id,
                                '<span foreground="%s">●</span>  %s' %
                                (value, GLib.markup_escape_text(label))))
        color = Gtk.ComboBox.new_with_model(color_model)
        color.set_id_column(0)
        color_renderer = Gtk.CellRendererText()
        color.pack_start(color_renderer, True)
        color.add_attribute(color_renderer, "markup", 1)
        color.set_active_id(self.selection_annotation.get("color", "yellow"))
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            left_margin=8, right_margin=8,
                            top_margin=8, bottom_margin=8)
        note_scroll = Gtk.ScrolledWindow()
        note_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        note_scroll.set_shadow_type(Gtk.ShadowType.IN)
        note_scroll.set_size_request(360, 140)
        note_scroll.add(view)
        view.get_buffer().set_text(self.selection_annotation.get("note", ""))
        content.pack_start(Gtk.Label(label=_("Highlight color"), xalign=0), False, False, 0)
        content.pack_start(color, False, False, 0)
        content.pack_start(Gtk.Label(label=_("Note"), xalign=0), False, False, 0)
        content.pack_start(note_scroll, True, True, 0)
        dialog.get_content_area().add(content); dialog.show_all()
        if pointer_x is not None and pointer_y is not None:
            _success, origin_x, origin_y = self.web.get_window().get_origin()
            dialog.move(origin_x + pointer_x, origin_y + pointer_y + 10)
        if dialog.run() == Gtk.ResponseType.OK:
            buffer = view.get_buffer()
            self.selection_annotation["color"] = color.get_active_id()
            self.selection_annotation["note"] = buffer.get_text(
                buffer.get_start_iter(), buffer.get_end_iter(), True).strip()
            self.store.save(); self._render_annotations(); self._refresh_annotations_sidebar()
        elif remove_on_cancel:
            annotations = self.store.book(self.book.identifier).setdefault("annotations", [])
            annotation_id = self.selection_annotation.get("id")
            annotations[:] = [item for item in annotations
                             if item.get("id") != annotation_id]
            self.selection_annotation = None
            self.store.save(); self._render_annotations(); self._refresh_annotations_sidebar()
        dialog.destroy()

    def _render_annotations(self):
        if not self.book or self.non_spine_resource:
            return
        annotations = [item for item in self.store.book(self.book.identifier).get("annotations", [])
                       if item.get("chapter") == self.chapter]
        payload = json.dumps(annotations).replace("</", "<\\/")
        self._trusted_eval(
            "(()=>{document.querySelectorAll('.xepub-annotation').forEach(span=>span.replaceWith(...span.childNodes));"
            "document.body.normalize();const items=" + payload + ";"
            "for(const item of items){const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);"
            "let node,offset=0,parts=[];while(node=walker.nextNode()){const a=Math.max(item.start-offset,0);"
            "const b=Math.min(item.end-offset,node.data.length);if(a<b)parts.push([node,a,b]);"
            "offset+=node.data.length;if(offset>=item.end)break;}"
            "for(let i=parts.length-1;i>=0;i--){const [text,a,b]=parts[i],range=document.createRange();"
            "range.setStart(text,a);range.setEnd(text,b);const span=document.createElement('span');"
            "span.className='xepub-annotation';span.dataset.annotationId=item.id;span.dataset.color=item.color;"
            "range.surroundContents(span);}}return '';})()")

    def _web_scroll(self, _web, event):
        if event.state & Gdk.ModifierType.CONTROL_MASK:
            if event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT):
                self._set_zoom(self.preferences["zoom"] + 0.1)
            elif event.direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT):
                self._set_zoom(self.preferences["zoom"] - 0.1)
            elif event.direction == Gdk.ScrollDirection.SMOOTH:
                _has_deltas, dx, dy = event.get_scroll_deltas()
                delta = dy if abs(dy) >= abs(dx) else dx
                if self._zoom_scroll_accumulator and delta * self._zoom_scroll_accumulator < 0:
                    self._zoom_scroll_accumulator = 0.0
                self._zoom_scroll_accumulator += delta
                if abs(self._zoom_scroll_accumulator) >= 0.75:
                    amount = -0.1 if self._zoom_scroll_accumulator > 0 else 0.1
                    self._set_zoom(self.preferences["zoom"] + amount)
                    self._zoom_scroll_accumulator = 0.0
            return True
        if event.direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT): self.next_page(); return True
        if event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT): self.previous_page(); return True
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            _has_deltas, dx, dy = event.get_scroll_deltas()
            delta = dy if abs(dy) >= abs(dx) else dx
            if self._scroll_accumulator and delta * self._scroll_accumulator < 0:
                self._scroll_accumulator = 0.0
            self._scroll_accumulator += delta
            if abs(self._scroll_accumulator) >= 0.75:
                if self._scroll_accumulator > 0: self.next_page()
                else: self.previous_page()
                self._scroll_accumulator = 0.0
            return True
        return False

    def show_find(self):
        self._set_sidebar_visible(True)
        self.sidebar_stack.set_visible_child_name("search")
        self.search_entry.grab_focus()

    def _build_search_options_menu(self):
        menu = Gtk.Menu()
        all_chapters = Gtk.RadioMenuItem.new_with_label(None, _("All chapters"))
        current_chapter = Gtk.RadioMenuItem.new_with_label_from_widget(
            all_chapters, _("Current chapter"))
        all_chapters.set_active(self.search_all_chapters)
        current_chapter.set_active(not self.search_all_chapters)
        all_chapters.connect("toggled", self._search_scope_changed)
        menu.append(all_chapters); menu.append(current_chapter); menu.append(Gtk.SeparatorMenuItem())
        for label, attribute, active in (
                (_("Case sensitive"), "search_case_sensitive", self.search_case_sensitive),
                (_("Whole words only"), "search_whole_words", self.search_whole_words)):
            item = Gtk.CheckMenuItem(label=label, active=active)
            item.connect("toggled", self._search_option_changed, attribute)
            menu.append(item)
        menu.show_all()
        self.search_options_menu = menu

    def _show_search_options(self, entry, position, _event):
        if position == Gtk.EntryIconPosition.SECONDARY:
            self.search_options_menu.popup_at_widget(
                entry, Gdk.Gravity.SOUTH_EAST, Gdk.Gravity.NORTH_EAST, None)

    def _search_scope_changed(self, item):
        if item.get_active():
            self.search_all_chapters = True
        else:
            self.search_all_chapters = False
        self.settings.set_boolean("search-all-chapters", self.search_all_chapters)
        self._rerun_search_for_options()

    def _search_option_changed(self, item, attribute):
        setattr(self, attribute, item.get_active())
        key = {"search_case_sensitive": "search-case-sensitive",
               "search_whole_words": "search-whole-words"}[attribute]
        self.settings.set_boolean(key, item.get_active())
        self._rerun_search_for_options()

    def _rerun_search_for_options(self):
        text = self.search_entry.get_text().strip()
        if text and self.book:
            self.find_controller.search_finish()
            self._collect_search_results(text)

    def _search_changed(self, entry):
        if self._search_source:
            GLib.source_remove(self._search_source)
            self._search_source = 0

    def _search_next(self, entry):
        text = entry.get_text().strip()
        if text and self.book:
            self._collect_search_results(text)
        else:
            self._show_search_results([], empty_state="empty")

    def _collect_search_results(self, text):
        self._search_source = 0
        results = []
        def normalized(value):
            return value if self.search_case_sensitive else value.casefold()

        needle = normalized(text)
        toc_labels = {posixpath.normpath(unquote(urlsplit(item.href).path)): item.label
                      for item in self._flat_toc(self.book.toc)}
        chapters = range(len(self.book.spine)) if self.search_all_chapters else (self.chapter,)
        for chapter in chapters:
            path = self.book.spine[chapter]
            try:
                content = xhtml_text(self.book.read(path))
            except EpubError:
                continue
            folded = normalized(content); offset = 0; occurrence = 0
            while len(results) < 500:
                found = folded.find(needle, offset)
                if found < 0:
                    break
                before_ok = found == 0 or not (folded[found - 1].isalnum() or folded[found - 1] == "_")
                after_at = found + len(needle)
                after_ok = after_at == len(folded) or not (folded[after_at].isalnum() or folded[after_at] == "_")
                if self.search_whole_words and not (before_ok and after_ok):
                    offset = found + max(1, len(needle)); continue
                start, end = max(0, found - 48), min(len(content), found + len(text) + 48)
                matched_text = content[found:found + len(text)]
                controller_haystack = content if self.search_case_sensitive else content.casefold()
                controller_needle = matched_text if self.search_case_sensitive else matched_text.casefold()
                find_occurrence = controller_haystack[:found].count(controller_needle)
                results.append({"chapter": chapter, "chapter_label": toc_labels.get(path, Path(path).stem),
                                "occurrence": find_occurrence, "search_text": matched_text,
                                "snippet": content[start:end],
                                "match_start": found - start, "match_length": len(text),
                                "prefix": start > 0, "suffix": end < len(content)})
                occurrence += 1; offset = found + max(1, len(needle))
        self._show_search_results(results)
        return False

    def _show_search_results(self, results, empty_state="no-results"):
        for child in self.search_results_list.get_children():
            self.search_results_list.remove(child)
        self.search_chapter_lists = []
        current_chapter = None
        chapter_list = None
        for result in results:
            if result["chapter"] != current_chapter:
                current_chapter = result["chapter"]
                expander = Gtk.Expander(label=result["chapter_label"], expanded=True,
                                        margin_start=6, margin_end=6)
                chapter_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
                chapter_list.connect("selected-rows-changed", self._search_result_selected)
                self.search_chapter_lists.append(chapter_list)
                expander.add(chapter_list)
                self.search_results_list.add(expander)
            row = Gtk.ListBoxRow()
            row.result_chapter = result["chapter"]
            row.result_index = result["occurrence"]
            row.result_search_text = result["search_text"]
            snippet = result["snippet"]; at = result["match_start"]; end = at + result["match_length"]
            markup = (("…" if result["prefix"] else "") + html_module.escape(snippet[:at]) +
                      "<b>" + html_module.escape(snippet[at:end]) + "</b>" +
                      html_module.escape(snippet[end:]) + ("…" if result["suffix"] else ""))
            label = Gtk.Label(xalign=0, margin=10, wrap=True,
                              max_width_chars=32, lines=3)
            label.set_markup(markup)
            row.add(label); chapter_list.add(row)
        self.search_results_list.show_all()
        self.search_results_panel.set_visible_child_name(
            "results" if results or empty_state == "blank" else empty_state)

    def _search_result_selected(self, listbox):
        if self._syncing_search_selection:
            return
        row = listbox.get_selected_row()
        text = getattr(row, "result_search_text", "") if row is not None else ""
        if row is None or not text or not self.book:
            return
        self._syncing_search_selection = True
        try:
            for other in self.search_chapter_lists:
                if other is not listbox:
                    other.unselect_all()
        finally:
            self._syncing_search_selection = False
        if not hasattr(row, "result_index"):
            return
        self._remember_location()
        if row.result_chapter != self.chapter:
            self.chapter = row.result_chapter
            self.pending_fraction = 0.0
            self._pending_search_result = (text, row.result_index)
            self.load_chapter()
            return
        self._activate_search_occurrence(text, row.result_index)

    def _activate_search_occurrence(self, text, occurrence):
        options = WebKit2.FindOptions.WRAP_AROUND
        if not self.search_case_sensitive:
            options |= WebKit2.FindOptions.CASE_INSENSITIVE
        self.find_controller.search(text, options, 1000)
        for _index in range(occurrence):
            self.find_controller.search_next()
        GLib.timeout_add(80, self._snap_after_search)
        return False

    def _snap_after_search(self):
        self._paginate("snap", callback=self._metrics_result)
        return False

    def add_bookmark(self):
        if not self.book: return
        if self._has_current_bookmark():
            return
        mark = {"chapter": self.chapter, "fraction": (self.current_page-1)/max(1,self.page_count-1),
                "label": self.progress.get_text()}
        self.store.book(self.book.identifier).setdefault("bookmarks", []).append(mark); self.store.save()
        self._refresh_bookmarks_sidebar()

    def _has_current_bookmark(self):
        if not self.book:
            return False
        fraction = (self.current_page - 1) / max(1, self.page_count - 1)
        return any(mark.get("chapter") == self.chapter and
                   abs(float(mark.get("fraction", -1)) - fraction) < 1e-9
                   for mark in self.store.book(self.book.identifier).get("bookmarks", []))

    def show_bookmarks(self):
        self._set_sidebar_visible(True)
        self.sidebar_stack.set_visible_child_name("bookmarks")

    def show_preferences(self):
        dialog = PreferencesDialog(
            self, self.settings, self.preferences, self.DEFAULTS,
            self._preferences_changed, self._build_word_menu)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def _preferences_changed(self, values):
        fraction = (self.current_page - 1) / max(1, self.page_count - 1)
        self.preferences.update(values)
        self.store.preferences.update(self.preferences)
        self.store.save()
        if self.book:
            if not self._preferences_refresh_source:
                self._preferences_fraction = fraction
            else:
                GLib.source_remove(self._preferences_refresh_source)
            self._preferences_refresh_source = GLib.timeout_add(
                180, self._refresh_after_preferences)
        else:
            self.web.set_zoom_level(self.preferences["zoom"])

    def _refresh_after_preferences(self):
        self._preferences_refresh_source = 0
        self.web.set_zoom_level(self.preferences["zoom"])
        if self.book:
            self.pending_fraction = self._preferences_fraction
            self.load_chapter()
        return False

    def _set_zoom(self, zoom):
        zoom = min(2.0, max(0.5, round(zoom, 1)))
        if zoom == self.preferences["zoom"]:
            return
        fraction = (self.current_page - 1) / max(1, self.page_count - 1)
        self.preferences["zoom"] = zoom
        self.store.preferences.update(self.preferences); self.store.save()
        self.web.set_zoom_level(zoom)
        if self.book:
            self.pending_fraction = fraction
            self.load_chapter()

    def show_info(self):
        if not self.book: return
        labels = {"title": _("Title"), "creator": _("Author"), "language": _("Language"),
                  "publisher": _("Publisher"), "identifier": _("Identifier"), "description": _("Description")}
        text = "\n\n".join(f"{labels[k]}: {self.book.metadata[k]}" for k in labels if k in self.book.metadata)
        dialog = Gtk.MessageDialog(self, Gtk.DialogFlags.MODAL, Gtk.MessageType.INFO, Gtk.ButtonsType.CLOSE, _("Book information"))
        dialog.format_secondary_text(text); dialog.run(); dialog.destroy()

    def show_about(self):
        dialog = Gtk.AboutDialog(transient_for=self, modal=True)
        dialog.set_program_name("Xepub")
        dialog.set_version("__PROJECT_VERSION__")
        dialog.set_comments(_("Book Reader"))
        dialog.set_website("https://github.com/xapp-project/xepub")
        dialog.set_logo_icon_name("xepub")
        dialog.set_license_type(Gtk.License.GPL_3_0)
        dialog.run(); dialog.destroy()

    def toggle_fullscreen(self):
        if self.get_window().get_state() & Gdk.WindowState.FULLSCREEN: self.unfullscreen()
        else: self.fullscreen()

    def _error(self, title, detail):
        dialog = Gtk.MessageDialog(self, Gtk.DialogFlags.MODAL, Gtk.MessageType.ERROR, Gtk.ButtonsType.CLOSE, title)
        dialog.format_secondary_text(detail); dialog.run(); dialog.destroy()

    def _on_close(self, *_args):
        self.save_position()
        self.settings.set_boolean("window-maximized", self.is_maximized())
        if not self.is_maximized():
            width, height = self.get_size()
            self.settings.set_int("window-width", width)
            self.settings.set_int("window-height", height)
        return False
