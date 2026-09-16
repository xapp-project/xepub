from __future__ import annotations

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("XApp", "1.0")
from gi.repository import GLib, Gtk, Pango, XApp
from xapp.util import l10n

_ = l10n("xepub")


def search_engine_default_name(engine_type):
    return {
        "dictionary": _("Dictionary"),
        "translate": _("Translate"),
        "encyclopedia": _("Encyclopedia"),
    }.get(engine_type, "")


def search_engine_name(engine_type, custom_name):
    if engine_type == "wikipedia":
        engine_type = "encyclopedia"
    return custom_name or search_engine_default_name(engine_type)


class PreferencesDialog(Gtk.Dialog):
    def __init__(self, parent, settings, preferences, defaults,
                 reading_changed, engines_changed):
        super().__init__(_("Preferences"), parent, Gtk.DialogFlags.MODAL)
        self.settings = settings
        self.preferences = preferences
        self.defaults = defaults
        self.reading_changed = reading_changed
        self.engines_changed = engines_changed
        self.set_default_size(680, 500)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                          transition_duration=150, vexpand=True)
        sidebar = XApp.StackSidebar(stack=pages, vexpand=True,
                                    valign=Gtk.Align.FILL, width_request=180)
        root.pack_start(sidebar, False, False, 0)
        root.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL),
                        False, False, 0)
        root.pack_start(pages, True, True, 0)

        reading = self._build_reading_page()
        engines = self._build_engines_page()
        pages.add_titled(reading, "reading", _("Reading"))
        pages.add_titled(engines, "search-engines", _("Search Engines"))
        pages.child_set_property(reading, "icon-name", "xsi-font-symbolic")
        pages.child_set_property(engines, "icon-name", "xsi-edit-find-symbolic")
        self.get_content_area().add(root)

    def _build_reading_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=16)
        general = Gtk.Grid(row_spacing=10, column_spacing=12)
        self.publisher = Gtk.Switch(active=self.preferences["publisher"])
        self.theme = Gtk.ComboBoxText()
        for value in (_("Light"), _("Sepia"), _("Dark")):
            self.theme.append_text(value)
        self.theme.set_active(["light", "sepia", "dark"].index(
            self.preferences["theme"]))
        self.margin = Gtk.SpinButton.new_with_range(20, 160, 4)
        self.margin.set_value(self.preferences["margin"])
        self.zoom = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 50, 200, 10)
        self.zoom.set_value(self.preferences["zoom"] * 100)
        self.zoom.set_value_pos(Gtk.PositionType.RIGHT)
        self.zoom.set_digits(0)
        for row, (label, widget) in enumerate(((_("Use book styling"), self.publisher),
                                               (_("Theme"), self.theme),
                                               (_("Page margins"), self.margin),
                                               (_("Zoom (%)"), self.zoom))):
            general.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            general.attach(widget, 1, row, 1, 1)
        page.pack_start(general, False, False, 0)

        typography = Gtk.Grid(row_spacing=10, column_spacing=12, margin=8)
        self.font = Gtk.FontButton(font=self.preferences["font"])
        self.size = Gtk.SpinButton.new_with_range(12, 40, 1)
        self.size.set_value(self.preferences["size"])
        self.line_height = Gtk.SpinButton.new_with_range(1.0, 2.5, .05)
        self.line_height.set_value(self.preferences["line_height"])
        self.align = Gtk.ComboBoxText()
        for value in (_("Left"), _("Justified")):
            self.align.append_text(value)
        self.align.set_active(1 if self.preferences["alignment"] == "justify" else 0)
        for row, (label, widget) in enumerate(((_("Font family"), self.font),
                                               (_("Font size"), self.size),
                                               (_("Line spacing"), self.line_height),
                                               (_("Alignment"), self.align))):
            typography.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            typography.attach(widget, 1, row, 1, 1)
        self.custom = Gtk.Expander(label=_("Custom typography"), expanded=True)
        self.custom.add(typography)
        self.custom.set_sensitive(not self.publisher.get_active())
        page.pack_start(self.custom, False, False, 0)

        self.publisher.connect("notify::active", self._publisher_changed)
        self.theme.connect("changed", self._reading_changed)
        self.margin.connect("value-changed", self._reading_changed)
        self.zoom.connect("value-changed", self._reading_changed)
        self.font.connect("font-set", self._reading_changed)
        self.size.connect("value-changed", self._reading_changed)
        self.line_height.connect("value-changed", self._reading_changed)
        self.align.connect("changed", self._reading_changed)

        reset = Gtk.Button.new_with_label(_("Reset to Defaults"))
        reset.set_halign(Gtk.Align.END)
        reset.connect("clicked", self._reset_reading)
        page.pack_end(reset, False, False, 0)
        return page

    def _publisher_changed(self, *_args):
        self.custom.set_sensitive(not self.publisher.get_active())
        self._reading_changed()

    def _reading_changed(self, *_args):
        values = {
            "font": self.font.get_font_family().get_name(),
            "size": int(self.size.get_value()),
            "line_height": self.line_height.get_value(),
            "margin": int(self.margin.get_value()),
            "zoom": self.zoom.get_value() / 100,
            "theme": ["light", "sepia", "dark"][self.theme.get_active()],
            "alignment": ["left", "justify"][self.align.get_active()],
            "publisher": self.publisher.get_active(),
        }
        self.reading_changed(values)

    def _reset_reading(self, _button):
        defaults = self.defaults
        self.theme.set_active(["light", "sepia", "dark"].index(defaults["theme"]))
        self.margin.set_value(defaults["margin"])
        self.zoom.set_value(defaults["zoom"] * 100)
        self.font.set_font(defaults["font"])
        self.size.set_value(defaults["size"])
        self.line_height.set_value(defaults["line_height"])
        self.align.set_active(1 if defaults["alignment"] == "justify" else 0)
        self.publisher.set_active(defaults["publisher"])
        self._reading_changed()

    def _build_engines_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=16)
        page.pack_start(Gtk.Label(
            label=_("Search engines used for selected words"), xalign=0),
            False, False, 0)
        self.engines_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        scroll.set_size_request(420, 150)
        scroll.add(self.engines_list)
        page.pack_start(scroll, True, True, 0)

        buttons = Gtk.ButtonBox(layout_style=Gtk.ButtonBoxStyle.END, spacing=6)
        add = Gtk.Button.new_from_icon_name("xsi-list-add-symbolic", Gtk.IconSize.BUTTON)
        self.edit = Gtk.Button.new_from_icon_name(
            "xsi-document-edit-symbolic", Gtk.IconSize.BUTTON)
        self.remove = Gtk.Button.new_from_icon_name(
            "xsi-list-remove-symbolic", Gtk.IconSize.BUTTON)
        add.set_tooltip_text(_("Add"))
        self.edit.set_tooltip_text(_("Edit"))
        self.remove.set_tooltip_text(_("Remove"))
        buttons.add(add)
        buttons.add(self.edit)
        buttons.add(self.remove)
        page.pack_start(buttons, False, False, 0)

        add.connect("clicked", lambda _button: self._edit_engine())
        self.edit.connect("clicked", lambda _button:
                          self._edit_engine(self._selected_engine_index()))
        self.remove.connect("clicked", lambda _button:
                            self._remove_engine(self._selected_engine_index()))
        self.engines_list.connect("row-selected", self._engine_selection_changed)
        self._refresh_engines()
        self._engine_selection_changed()
        return page

    def _selected_engine_index(self):
        row = self.engines_list.get_selected_row()
        return row.engine_index if row is not None else None

    def _engine_selection_changed(self, *_args):
        selected = self._selected_engine_index() is not None
        self.edit.set_sensitive(selected)
        self.remove.set_sensitive(selected)

    def _refresh_engines(self):
        for child in self.engines_list.get_children():
            self.engines_list.remove(child)
        for index, (engine_type, custom_name, url) in enumerate(
                self.settings.get_value("search-engines").unpack()):
            row = Gtk.ListBoxRow()
            row.engine_index = index
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, margin=8)
            box.pack_start(Gtk.Label(
                label=search_engine_name(engine_type, custom_name), xalign=0),
                False, False, 0)
            address = Gtk.Label(label=url, xalign=0,
                                ellipsize=Pango.EllipsizeMode.MIDDLE)
            address.get_style_context().add_class("dim-label")
            box.pack_start(address, False, False, 0)
            row.add(box)
            self.engines_list.add(row)
        self.engines_list.show_all()

    def _save_engines(self, engines):
        self.settings.set_value("search-engines", GLib.Variant("a(sss)", engines))
        self._refresh_engines()
        self._engine_selection_changed()
        self.engines_changed()

    def _remove_engine(self, index):
        if index is None:
            return
        engines = list(self.settings.get_value("search-engines").unpack())
        del engines[index]
        self._save_engines(engines)

    def _edit_engine(self, index=None):
        engines = list(self.settings.get_value("search-engines").unpack())
        if index is None:
            engine_type, custom_name, url_value = "other", "", ""
        else:
            engine_type, custom_name, url_value = engines[index]
        if engine_type == "custom":
            engine_type = "other"
        elif engine_type == "wikipedia":
            engine_type = "encyclopedia"

        title = _("Search Engine")
        dialog = Gtk.Dialog(title, self, Gtk.DialogFlags.MODAL,
                            (_("Cancel"), Gtk.ResponseType.CANCEL,
                             _("Save"), Gtk.ResponseType.OK))
        grid = Gtk.Grid(row_spacing=10, column_spacing=12, margin=12)
        engine_type_combo = Gtk.ComboBoxText()
        for type_id, label in (("dictionary", _("Dictionary")),
                               ("encyclopedia", _("Encyclopedia")),
                               ("translate", _("Translate")),
                               ("other", _("Other"))):
            engine_type_combo.append(type_id, label)
        engine_type_combo.set_active_id(engine_type)
        name = Gtk.Entry(text=custom_name)
        url = Gtk.Entry(text=url_value)
        url.set_placeholder_text("https://example.com/search?q={text}")
        grid.attach(Gtk.Label(label=_("Type"), xalign=0), 0, 0, 1, 1)
        grid.attach(engine_type_combo, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label=_("Name"), xalign=0), 0, 1, 1, 1)
        grid.attach(name, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label=_("URL"), xalign=0), 0, 2, 1, 1)
        grid.attach(url, 1, 2, 1, 1)
        hint = Gtk.Label(label=_("Use {text} where the selected text should appear."),
                         xalign=0)
        hint.get_style_context().add_class("dim-label")
        grid.attach(hint, 1, 3, 1, 1)
        dialog.get_content_area().add(grid)
        save = dialog.get_widget_for_response(Gtk.ResponseType.OK)

        def validate(*_args):
            selected_type = engine_type_combo.get_active_id()
            other = selected_type == "other"
            name.set_placeholder_text(
                search_engine_default_name(selected_type) if not other else "")
            save.set_sensitive((not other or bool(name.get_text().strip())) and
                               "{text}" in url.get_text() and
                               url.get_text().startswith(("http://", "https://")))

        engine_type_combo.connect("changed", validate)
        name.connect("changed", validate)
        url.connect("changed", validate)
        validate()
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            engine = (engine_type_combo.get_active_id(), name.get_text().strip(),
                      url.get_text().strip())
            if index is None:
                engines.append(engine)
            else:
                engines[index] = engine
            self._save_engines(engines)
        dialog.destroy()
