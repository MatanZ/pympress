# -*- coding: utf-8 -*-
#
#       ui.py
#
#       Copyright 2010 Thomas Jost <thomas.jost@gmail.com>
#       Copyright 2015 Cimbali <me@cimba.li>
#
#       This program is free software; you can redistribute it and/or modify
#       it under the terms of the GNU General Public License as published by
#       the Free Software Foundation; either version 2 of the License, or
#       (at your option) any later version.
#
#       This program is distributed in the hope that it will be useful,
#       but WITHOUT ANY WARRANTY; without even the implied warranty of
#       MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#       GNU General Public License for more details.
#
#       You should have received a copy of the GNU General Public License
#       along with this program; if not, write to the Free Software
#       Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#       MA 02110-1301, USA.
"""
:mod:`pympress.ui` -- GUI management
------------------------------------

This module contains the whole graphical user interface of pympress, which is
made of two separate windows: the Content window, which displays only the
current page in full size, and the Presenter window, which displays both the
current and the next page, as well as a time counter and a clock.

Both windows are managed by the :class:`~pympress.ui.UI` class.
"""

from __future__ import print_function, unicode_literals

import logging
logger = logging.getLogger(__name__)

import os.path
import sys
import json

import gi
import cairo
gi.require_version('Gtk', '3.0')
from gi.repository import GObject, Gtk, Gdk, GLib, GdkPixbuf


from pympress import document, surfacecache, util, pointer, scribble, config, builder, talk_time, extras, editable_label

class UI(builder.Builder):
    """ Pympress GUI management.
    """
    #: Content window, as a :class:`~Gtk.Window` instance.
    c_win = None
    #: :class:`~Gtk.AspectFrame` for the Content window.
    c_frame = None
    #: :class:`~Gtk.DrawingArea` for the Content window.
    c_da = None
    #: :class:`~Gtk.CheckMenuItem` that shows whether the c_win is fullscreen
    pres_fullscreen = None

    #: :class:`~Gtk.Box` for all the Presenter window
    bigvbox = None
    #: Presenter window, as a :class:`~Gtk.Window` instance.
    p_win = None
    #: :class:`~Gtk.Box` for the Presenter window.
    p_central = None
    #: :class:`~Gtk.AspectFrame` for the current slide in the Presenter window.
    p_frame_notes = None
    #: :class:`~Gtk.DrawingArea` for the current slide in the Presenter window.
    p_da_notes = None

    #: :class:`~Gtk.AspectFrame` for the next slide in the Presenter window.
    p_frame_next = None
    #: :class:`~Gtk.DrawingArea` for the next slide in the Presenter window.
    p_da_next = None
    #: :class:`~Gtk.AspectFrame` for the current slide copy in the Presenter window.
    p_frame_cur = None
    #: :class:`~Gtk.DrawingArea` for the current slide copy in the Presenter window.
    p_da_cur = None

    #: :class:`~Gtk.Frame` for the annotations in the Presenter window.
    p_frame_annot = None
    #: :class:`~Gtk.CheckMenuItem` that shows whether the annotations are toggled
    pres_annot = None

    #: Indicates whether we should delay redraws on some drawing areas to fluidify resizing gtk.paned
    resize_panes = False
    #: Tracks return values of GLib.timeout_add to cancel gtk.paned's redraw callbacks
    redraw_timeout = 0

    #: Whether to use notes mode or not
    notes_mode = document.PdfPage.NONE
    #: Current choice of mode to toggle notes
    chosen_notes_mode = document.PdfPage.RIGHT
    #: :class:`~Gtk.CheckMenuItem` that shows whether the annotations are toggled
    pres_notes = None

    #: Whether to display annotations or not
    show_annotations = True
    #: Whether to display big buttons or not
    show_bigbuttons = True
    #: :class:`~Gtk.ToolButton` big button for touch screens, go to previous slide
    prev_button = None
    #: :class:`~Gtk.ToolButton` big button for touch screens, go to next slide
    next_button = None
    #: :class:`~Gtk.ToolButton` big button for touch screens, go toggle the pointer
    laser_button = None
    #: :class:`~Gtk.ToolButton` big button for touch screens, go to scribble on screen
    highlight_button = None

    #: number of page currently displayed in Controller window's miniatures
    page_preview_nb = -1

    #: track whether we blank the screen
    blanked = False
    #: :class:`~Gtk.CheckMenuItem` that shows whether the blank mode is toggled
    pres_blank = None

    #: Dictionary of :class:`~Gtk.Widget` from the presenter window that can be dynamically rearranged
    placeable_widgets = {}
    #: Map of :class:`~Gtk.Paned` to the relative position (`float` between 0 and 1) of its handle
    pane_handle_pos = {}

    #: :class:`~pympress.config.Config` to remember preferences
    config = config.Config()

    #: :class:`~pympress.surfacecache.SurfaceCache` instance.
    cache = None

    #: Current :class:`~pympress.document.Document` instance.
    doc = document.EmptyDocument()

    #: Class :class:`~pympress.scribble.Scribble` managing drawing by the user on top of the current slide.
    scribbler = None
    #: Class :class:`~pympress.extras.Annotations` managing the display of annotations
    annotations = None
    #: Class :class:`~pympress.extras.Media` managing keeping track of and callbacks on media overlays
    medias = None
    #: Class :class:`~pympress.extras.Zoom` managing the zoom level of the current slide.
    zoom = None

    #: Software-implemented laser pointer, :class:`~pympress.pointer.Pointer`
    laser = None

    #: :class:`~pympress.editable_label.PageNumber` displaying and setting current page numbers
    page_number = None

    #: :class:`~pympress.editable_label.EstimatedTalkTime` to set estimated/remaining talk time
    est_time = None
    #: :class:`~pympress.talk_time.TimeCounter` clock tracking talk time (elapsed, and remaining)
    talk_time = None
    #: :class:`~pympress.extras.TimingReport` popup to show how much time was spent on which part
    timing = None

    #: A :class:`~Gtk.ShortcutsWindow` to show the shortcuts
    shortcuts_window = None

    #: A :class:`Gtk.AccelGroup` to store the shortcuts
    accel_group = None

    #: highlight_mode: "clear" for clearing highlights on each page change (old behaviour),
    #                  "single" for highlights not affected by page change,
    #                  "page" for highlights saved and restored per page.
    #                  "autopage" as page, but also save to file with .pymp extension.
    highlight_mode = "clear"

    pen_pointer = [()]

    hlines = 0
    vlines = 0

    #: Minimum distance (square of the distance, actually, in page units) for segments in
    #  scribbles. Reduces number of points in scribbles made by writing pads.
    min_distance = 0

    show_highlights = True

    selected_timeout = None

    p_central = None

    ##############################################################################
    #############################      UI setup      #############################
    ##############################################################################

    def __init__(self, highlight_mode, config_override):
        super(UI, self).__init__()
        self.config.read_dict(config_override)
        self.blanked = self.config.getboolean('content', 'start_blanked')
        if highlight_mode:
            self.highlight_mode = highlight_mode
        else:
            self.highlight_mode = self.config.get('presenter', 'highlight_mode', fallback='clear')

        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            util.load_style_provider(Gtk.CssProvider()),
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.show_annotations = self.config.getboolean('presenter', 'show_annotations')
        self.min_distance = self.config.getfloat('presenter', 'min_distance')

        # Surface cache
        self.cache = surfacecache.SurfaceCache(self.doc, self.config.getint('cache', 'maxpages'))

        # Make and populate windows
        self.load_ui('presenter')
        self.load_ui('content')

        self.zoom = extras.Zoom(self)
        self.scribbler = scribble.Scribbler(self.config, self, self.notes_mode)
        self.annotations = extras.Annotations(self)
        self.medias = extras.Media(self, self.config)
        self.laser = pointer.Pointer(self.config, self)
        self.est_time = editable_label.EstimatedTalkTime(self)
        self.page_number = editable_label.PageNumber(self, self.config.getboolean('presenter', 'scroll_number'))
        self.talk_time = talk_time.TimeCounter(self, self.est_time)
        self.timing = extras.TimingReport(self)

        # Get placeable widgets. NB, get the highlight one manually from the scribbler class
        self.placeable_widgets = {
            name: self.get_object(widget_name) for name, widget_name in self.config.placeable_widgets.items()
        }

        # Initialize windows and screens
        self.setup_screens()
        self.c_win.show_now()
        self.p_win.show_now()

        self.make_cwin()
        self.make_pwin()

        self.connect_signals(self)

        # Common to both windows
        self.load_icons()

        # Show all windows
        self.c_win.show_all()
        self.p_win.show_all()

        extras.FileWatcher.start_daemon()

        # Queue some redraws
        self.c_da.queue_draw()
        self.redraw_panes()
        self.on_page_change(False)

        # solve circular creation-time dependency
        self.est_time.delayed_callback_connection(self)
        self.zoom.delayed_callback_connection(self.scribbler)

        # Adjust default visibility of items
        self.prev_button.set_visible(self.show_bigbuttons)
        self.next_button.set_visible(self.show_bigbuttons)
        self.laser_button.set_visible(self.show_bigbuttons)
        self.highlight_button.set_visible(self.show_bigbuttons)
        self.p_frame_annot.set_visible(self.show_annotations)
        self.laser.activate_pointermode()
        self.pen_pointer_c = GdkPixbuf.Pixbuf.new_from_file(util.get_icon_path('pointer_green' + '.png'))
        self.scribbler.pen_pointer_p = Gdk.Cursor.new_from_name(self.p_win.get_display(), "help").get_image()
        self.hlines = self.config.getfloat('presenter', 'horizontal_lines')
        self.vlines = self.config.getfloat('presenter', 'vertical_lines')

        self.scribbler.latex_dict = json.load(open(util.get_latex_dict()))
        self.scribbler.latex_macros = {
            "latex": { "ctrl": {}, "alt": {}, "altctrl": {}, },
            "markup": { "ctrl": {}, "alt": {}, "altctrl": {}, },
            "text": { "ctrl": {}, "alt": {}, "altctrl": {}, },
        }
        try:
            personal_dict = json.load(open(util.get_personal_dict()))
            self.scribbler.latex_dict.update(personal_dict['shortcuts'])
            for dicts in self.scribbler.latex_macros.keys():
                for m in personal_dict[dicts].keys():
                    for k, v in personal_dict[dicts][m].items():
                        p = v.find("\f")
                        if p == -1:
                            p = len(v)
                        self.scribbler.latex_macros[dicts][m][k] = (v.replace("\f", ""), p)
        except FileNotFoundError:
            pass
        except KeyError:
            pass
        keys = sorted(self.scribbler.latex_dict.keys())
        self.scribbler.latex_prefixes = {keys[x] for x in range(len(keys)-1) if keys[x+1].startswith(keys[x])}

        self.scribbler.laser = self.laser


    def load_icons(self):
        """ Set the icon list for both windows.
        """
        try:
            icon_list = [GdkPixbuf.Pixbuf.new_from_file(i) for i in util.list_icons()]
        except Exception:
            logger.exception('Error loading icons')
            return

        self.c_win.set_icon_list(icon_list)
        self.p_win.set_icon_list(icon_list)


    def make_cwin(self):
        """ Initializes the content window.
        """
        self.c_frame.set_property("yalign", self.config.getfloat('content', 'yalign'))
        self.c_frame.set_property("xalign", self.config.getfloat('content', 'xalign'))

        page_type = self.notes_mode.complement()

        self.cache.add_widget(self.c_da, page_type)
        self.cache.add_widget(self.c_da, page_type, zoomed = True)
        self.c_frame.set_property("ratio", self.doc.current_page().get_aspect_ratio(page_type))


    def make_pwin(self):
        """ Initializes the presenter window.
        """
        layout = self.config.get_layout('notes' if self.notes_mode else 'plain')
        pane_handles = self.replace_layout(layout, self.p_central, self.placeable_widgets, self.on_pane_event)
        self.pane_handle_pos.update(pane_handles)

        self.show_bigbuttons = self.config.getboolean('presenter', 'show_bigbuttons')

        init_checkstates = {
            'pres_pause':            True,
            'pres_fullscreen':       bool(self.c_win.get_window().get_state() & Gdk.WindowState.FULLSCREEN),
            'pres_notes':            bool(self.notes_mode),
            'pres_blank':            self.blanked,
            'pres_annot':            self.show_annotations,
            'pres_buttons':          self.show_bigbuttons,

            'start_blanked':         self.config.getboolean('content', 'start_blanked'),
            'start_cwin_fullscreen': self.config.getboolean('content', 'start_fullscreen'),
            'start_pwin_fullscreen': self.config.getboolean('presenter', 'start_fullscreen'),
            'portable_config':       self.config.using_portable_config(),
        }

        for n in init_checkstates:
            self.get_object(n).set_active(init_checkstates[n])

        default = 'notes_' + self.chosen_notes_mode.name.lower()
        for radio_name in ['notes_right', 'notes_left', 'notes_top', 'notes_bottom']:
            radio = self.get_object(radio_name)
            radio.set_name(radio_name)
            radio.set_active(radio_name == default)

        slide_type = self.notes_mode.complement()
        self.cache.add_widget(self.p_da_cur, slide_type)
        #self.cache.add_widget(self.scribbler.scribble_p_da, slide_type, prerender_enabled = False)
        self.cache.add_widget(self.p_da_cur, slide_type, zoomed = True)
        self.cache.add_widget(self.p_da_next, slide_type)
        self.cache.add_widget(self.p_da_notes, self.notes_mode, prerender_enabled = bool(self.notes_mode))

        # set default value
        self.page_number.set_last(self.doc.pages_number())

        # Enable dropping files onto the window
        self.p_win.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.p_win.drag_dest_add_text_targets()

        colourclass = 'white' if self.config.getboolean('content', 'white_blanking') else 'black'
        self.c_da.get_style_context().add_class(colourclass)
        self.c_win.get_style_context().add_class(colourclass)

        icons = util.list_toolbar_icons()

        buttons = {
            "operations": [
                ("export_xopp", "toolbar-export-xopp", "Export"),
                ("export_pdf", "toolbar-export-pdf", "Export PDF"),
                ("", "", ""),
                ("unzoom", "zoom-fit-best", "No zoom"),
                ("zoom", "zoom-in", "Zoom in"),
                ("", "", ""),
                ("undo", "edit-undo", "Undo"),
                ("redo", "edit-redo", "Redo"),
                ("clear_all", "edit-clear-all", "Clear"),
            ],
            "tools": [
                ("cancel", "process-stop", "Disable scribbling"),
                ("draw", "toolbar-pen", "Draw freehand"),
                ("erase", "toolbar-erase", "Erase"),
                ("line", "toolbar-line", "Draw line"),
                ("box", "toolbar-rect", "Draw rectangle"),
                ("ellipse", "toolbar-ellipse", "Draw ellipse"),
                ("text", "toolbar-text", "Enter text"),
                ("latex", "toolbar-latex", "Enter LaTeX text"),
                ("stamp", "toolbar-stamp", "Stamp"),
                ("", "", ""),
                ("select_t", "toolbar-select", "Select freehand"),
                ("select_r", "toolbar-select_r", "Select rectangle"),
                ("move", "toolbar-move", "Move selected"),
            ],
            "attributes": [
                ("", "", ""),
            ]
        }
        toolbar_names = buttons.keys()

        if self.config.get("presenter", "toolbar_orientation", fallback="auto")[0].lower() == 'v':
            orientation = Gtk.Orientation.VERTICAL
            orientation_perp = Gtk.Orientation.HORIZONTAL
            hsize = 32
            vsize = 96
            value_pos = Gtk.PositionType.RIGHT
        elif self.config.get("presenter", "toolbar_orientation", fallback="auto")[0].lower() == 'h':
            orientation_perp = Gtk.Orientation.VERTICAL
            orientation = Gtk.Orientation.HORIZONTAL
            hsize = 96
            vsize = 32
            value_pos = Gtk.PositionType.BOTTOM
        else:
            orientation = Gtk.Orientation.VERTICAL
            orientation_perp = Gtk.Orientation.HORIZONTAL
            hsize = 32
            vsize = 96
            value_pos = Gtk.PositionType.RIGHT

        self.scribbler.buttons = {}
        toolbars = {}
        for tn in toolbar_names:
            toolbar = Gtk.Toolbar()
            toolbars[tn] = toolbar
            toolbar.set_name(tn)
            toolbar.set_style(Gtk.ToolbarStyle.ICONS)
            toolbar.set_orientation(orientation)
            toolbar.set_icon_size(Gtk.IconSize.DND)

            for name, icon, label in buttons[tn]:
                if name == "":
                    toolbar.insert(Gtk.SeparatorToolItem.new(), 999)
                else:
                    if icon in icons:
                        image = Gtk.Image.new_from_file(icons[icon])
                    else:
                        image = Gtk.Image.new_from_icon_name(icon, hsize)
                    button = Gtk.ToolButton.new(image, None)
                    button.set_name(name)
                    button.set_label(label)
                    button.set_tooltip_text(label)
                    button.connect("clicked", self.toolbar_click)
                    button.connect("button-press-event", self.on_navigation)
                    toolbar.insert(button, 999)
                    self.scribbler.buttons[name] = button
            if orientation == Gtk.Orientation.HORIZONTAL:
                self.bigvbox.pack_start(toolbar, False, False, 1)
                self.bigvbox.reorder_child(toolbar, 1)
            else:
                self.p_central.pack_start(toolbar, False, False, 1)
                self.p_central.reorder_child(toolbar, 0)

        font_button = Gtk.FontButton.new()
        font_button.connect("font-set", self.scribbler.update_font)
        font_button.set_name("scribble_font")
        font_button.set_size_request(48,48)
        font_button.set_show_style(False)
        font_button.set_show_size(False)
        font_button.set_use_font(True)
        #font_button.set_font_name(self.scribbler.scribble_font)
        Gtk.FontChooser.set_font(font_button, self.scribbler.scribble_font)
        font_button.set_use_size(font_button.get_font_size() < 24576)
        toolbar_font_button = Gtk.ToolItem.new()
        toolbar_font_button.add(font_button)
        toolbar.insert(toolbar_font_button, 999)
        self.scribbler.buttons["font_button"] = font_button
        font_button.get_children()[0].get_children()[0].set_label("A")

        toolbar.insert(Gtk.SeparatorToolItem.new(), 999)

        toolbar = toolbars["attributes"]
        width_scale = Gtk.Scale.new_with_range(orientation, 0, 299, 5)
        width_scale.set_name("scribble_width")
        width_scale.set_digits(0)
        width_scale.set_draw_value(True)
        width_scale.set_has_origin(True)
        width_scale.set_value_pos(value_pos)
        width_scale.set_size_request(hsize,vsize)
        width_scale.connect("change-value", self.scribbler.update_width)
        width_scale.connect("format-value", format_width, self.scribbler.width_curve)
        toolbar_width_scale = Gtk.ToolItem.new()
        toolbar_width_scale.add(width_scale)
        toolbar.insert(toolbar_width_scale, 999)
        self.scribbler.buttons["scribble_width"] = width_scale

        toolbar.insert(Gtk.SeparatorToolItem.new(), 999)

        alpha_scale = Gtk.Scale.new_with_range(orientation, 0, 1, 0.1)
        alpha_scale.set_name("scribble_alpha")
        alpha_scale.set_digits(2)
        alpha_scale.set_draw_value(True)
        alpha_scale.set_has_origin(True)
        alpha_scale.set_value_pos(value_pos)
        alpha_scale.set_size_request(hsize,vsize)
        alpha_scale.connect("change-value", self.scribbler.update_alpha)
        toolbar_alpha_scale = Gtk.ToolItem.new()
        toolbar_alpha_scale.add(alpha_scale)
        toolbar.insert(toolbar_alpha_scale, 999)
        self.scribbler.buttons["scribble_alpha"] = alpha_scale

        color_button = Gtk.ColorButton.new_with_rgba(Gdk.RGBA(*self.scribbler.scribble_color))
        color_button.connect("color-set", self.scribbler.update_color)
        color_button.set_name("scribble_color")
        color_button.set_size_request(48,48)
        Gtk.ColorChooser.set_use_alpha(color_button, True)
        toolbar_color_button = Gtk.ToolItem.new()
        toolbar_color_button.add(color_button)
        toolbar.insert(toolbar_color_button, 999)
        self.scribbler.buttons["color_button"] = color_button

        toolbar.insert(Gtk.SeparatorToolItem.new(), 999)
        toolbar.insert(Gtk.SeparatorToolItem.new(), 999)

        fill_alpha_scale = Gtk.Scale.new_with_range(orientation, 0, 1, 0.1)
        fill_alpha_scale.set_name("fill_alpha")
        fill_alpha_scale.set_digits(2)
        fill_alpha_scale.set_draw_value(True)
        fill_alpha_scale.set_has_origin(True)
        fill_alpha_scale.set_value_pos(value_pos)
        fill_alpha_scale.set_size_request(hsize,vsize)
        fill_alpha_scale.connect("change-value", self.scribbler.update_fill_alpha)
        toolbar_fill_alpha_scale = Gtk.ToolItem.new()
        toolbar_fill_alpha_scale.add(fill_alpha_scale)
        toolbar.insert(toolbar_fill_alpha_scale, 999)
        self.scribbler.buttons["fill_alpha"] = fill_alpha_scale

        fill_color_button = Gtk.ColorButton.new_with_rgba(Gdk.RGBA(*self.scribbler.fill_color))
        fill_color_button.connect("color-set", self.scribbler.update_fill_color)
        fill_color_button.set_name("fill_color")
        fill_color_button.set_size_request(48,48)
        Gtk.ColorChooser.set_use_alpha(fill_color_button, True)
        toolbar_fill_color_button = Gtk.ToolItem.new()
        toolbar_fill_color_button.add(fill_color_button)
        toolbar.insert(toolbar_fill_color_button, 999)
        self.scribbler.buttons["fill_color_button"] = fill_color_button

        alpha_scale.set_value(self.scribbler.scribble_color[3])
        fill_alpha_scale.set_value(self.scribbler.fill_color[3])
        width_scale.set_value(self.scribbler.width_curve_r(self.scribbler.scribble_width))

    def setup_screens(self):
        """ Sets up the position of the windows.
        """
        # If multiple monitors, apply windows to monitors according to config
        screen = self.p_win.get_display()
        if screen.get_n_monitors() > 1:
            c_monitor = self.config.getint('content', 'monitor')
            p_monitor = self.config.getint('presenter', 'monitor')
            p_full = self.config.getboolean('presenter', 'start_fullscreen')
            c_full = self.config.getboolean('content', 'start_fullscreen')

            if c_monitor == p_monitor and (c_full or p_full):
                warning = _('Content and presenter window must not be on the same monitor if you start full screen!')
                logger.warning(warning)
                p_monitor = 0 if c_monitor > 0 else 1
        else:
            c_monitor = 0
            p_monitor = 0
            c_full = False
            p_full = False

            if self.config.getboolean('presenter', 'start_fullscreen') \
                    or self.config.getboolean('content', 'start_fullscreen'):
                logger.warning(_('Not starting content or presenter window full screen ' +
                                 'because there is only one monitor'))

        p_bounds = screen.get_monitor(p_monitor).get_geometry()
        self.p_win.move(p_bounds.x, p_bounds.y)
        self.p_win.resize(p_bounds.width, p_bounds.height)
        if p_full:
            self.p_win.fullscreen()
        else:
            self.p_win.maximize()

        c_bounds = screen.get_monitor(c_monitor).get_geometry()
        self.c_win.move(c_bounds.x, c_bounds.y)
        self.c_win.resize(c_bounds.width, c_bounds.height)
        if c_full:
            self.c_win.fullscreen()
            GLib.idle_add(lambda: util.set_screensaver(True, self.c_win.get_window()))


    def show_shortcuts(self, *args):
        """ Display the shortcuts window.
        """
        # Use a different builder to load and be able to release the shortcuts window
        shortcuts_builder = builder.Builder()
        shortcuts_builder.load_ui('shortcuts')
        self.shortcuts_window = shortcuts_builder.get_object('shortcuts_window')

        for command, shortcut_list in self.config.items('shortcuts'):
            display_shortcut = shortcuts_builder.get_object('shortcut_' + command)
            if display_shortcut is not None:
                display_shortcut.props.accelerator = shortcut_list

        self.shortcuts_window.set_transient_for(self.p_win)
        self.shortcuts_window.show_all()
        self.shortcuts_window.present()


    def close_shortcuts(self, *args):
        """ Destroy the shortcuts window once it is hidden.
        """
        self.shortcuts_window.destroy()
        self.shortcuts_window = None


    ##############################################################################
    ############################   Dynamic resize   ##############################
    ##############################################################################

    def on_configure_da(self, widget, event):
        """ Manage "configure" events for all drawing areas, e.g. resizes.

        We tell the local :class:`~pympress.surfacecache.SurfaceCache` cache about it, so that it can
        invalidate its internal cache for the specified widget and pre-render next pages at a correct size.

        Warning: Some not-explicitly sent signals contain wrong values! Just don't resize in that case,
        since these always seem to happen after a correct signal that was sent explicitly.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget which has been resized
            event (:class:`~Gdk.Event`):  the GTK event, which contains the new dimensions of the widget
        """
        # Don't trust those
        if not event.send_event:
            return

        self.cache.resize_widget(widget.get_name(), event.width, event.height)

        if widget is self.c_da:
            self.medias.resize('content')
        elif widget is self.p_da_cur:
            self.medias.resize('presenter')


    def on_configure_win(self, widget, event):
        """ Manage "configure" events for both window widgets.

        Args:
            widget (:class:`~Gtk.Widget`):  the window which has been moved or resized
            event (:class:`~Gdk.Event`):  the GTK event, which contains the new dimensions of the widget
        """
        if widget is self.p_win:
            cw = self.p_central.get_allocated_width()
            ch = self.p_central.get_allocated_height()

        elif widget is self.c_win:
            pass


    def redraw_selected(self):
        self.scribbler.draw_blink = not self.scribbler.draw_blink
        self.p_da_cur.queue_draw()
        return True

    def redraw_panes(self):
        """ Handler for :class:`~Gtk.Paned`'s resizing signal.

        Used for delayed drawing events of drawing areas inside the panes.

        This is very useful on windows where resizing gets sluggish if we try to redraw while resizing.
        """
        self.resize_panes = False
        self.p_da_cur.queue_draw()
        self.p_da_next.queue_draw()
        if self.notes_mode:
            self.p_da_notes.queue_draw()
        if self.redraw_timeout:
            self.redraw_timeout = 0


    def on_pane_event(self, widget, evt):
        """ Signal handler for gtk.paned events.

        This function allows one to delay drawing events when resizing, and to speed up redrawing when
        moving the middle pane is done (which happens at the end of a mouse resize)

        Args:
            widget (:class:`~Gtk.Widget`):  the widget in which the event occurred (ignored)
            evt (:class:`~Gdk.Event`):  the event that occurred
        """
        if type(evt) == Gdk.EventButton and evt.type == Gdk.EventType.BUTTON_RELEASE:
            self.redraw_panes()
        elif type(evt) == GObject.GParamSpec and evt.name == "position":
            self.resize_panes = True
            if self.redraw_timeout:
                GLib.Source.remove(self.redraw_timeout)
            self.redraw_timeout = GLib.timeout_add(200, self.redraw_panes)


    ############################################################################
    ############################  Program lifetime  ############################
    ############################################################################

    def run(self):
        """ Run the GTK main loop.
        """
        Gtk.main()


    def save_and_quit(self, *args):
        """ Save configuration and exit the main loop.
        """
        if self.p_win.in_destruction() or self.c_win.in_destruction():
            return

        self.scribbler.disable_scribbling()
        self.medias.hide_all()

        extras.FileWatcher.stop_daemon()
        self.doc.cleanup_media_files()

        if self.p_central.get_children():
            self.config.update_layout('notes' if self.notes_mode else 'plain', self.p_central.get_children()[-1], self.pane_handle_pos)

        if bool(self.c_win.get_window().get_state() & Gdk.WindowState.FULLSCREEN):
            util.set_screensaver(False, self.c_win.get_window())

        if self.highlight_mode in ('autopage') and self.doc.cur_page >= 0:
            self.doc.scribbles[self.doc.cur_page] = self.scribbler.scribble_list[:]
        self.doc.save_scribbles()
        self.config.save_config()
        self.p_win.destroy()
        self.c_win.destroy()

        script = self.config.get('penpad', "quit_script", fallback=None)
        if script:
            os.system(script)

        Gtk.main_quit()


    def menu_about(self, *args):
        """ Display the "About pympress" dialog.

        Handles clicks on the "about" menu.
        """
        about = Gtk.AboutDialog(transient_for = self.p_win)
        pympress = util.get_pympress_meta()
        about.set_program_name('pympress')
        about.set_version(pympress['version'])
        about.set_copyright(_('Contributors:') + '\n' + pympress['contributors'])
        about.set_comments(_('pympress is a little PDF reader written in Python ' +
                             'using Poppler for PDF rendering and GTK for the GUI.\n') +
                           _('Some preferences are saved in ') + self.config.path_to_config() + '\n' +
                           _('Resources are loaded from ') + os.path.dirname(util.get_locale_dir()) + '\n' +
                           _('The log is written to ') + util.get_log_path() + '\n\n' +
                           _('Media support uses {}.').format(self.medias.backend_version()) + '\n' +
                           _('Python version {}').format(sys.version))
        about.set_website('https://github.com/Cimbali/pympress')
        try:
            about.set_logo(GdkPixbuf.Pixbuf.new_from_file(util.get_icon_path('pympress-128.png')))
        except Exception:
            logger.exception(_('Error loading icon for about window'))
        about.run()
        about.destroy()


    ##############################################################################
    ############################ Document manangement ############################
    ##############################################################################

    def swap_document(self, docpath, page = 0, reloading = False):
        """ Replace the currently open document with a new one.

        The new document is possibly and EmptyDocument if docpath is None.
        The state of the ui and cache are updated accordingly.

        Args:
            docpath (`str`): the absolute path to the new document
            page (`int`): the page at which to start the presentation
            reloading (`bool`): whether we are reloading or detecting stuff from the document
        """
        try:
            if reloading:
                self.doc = document.Document.create(self, docpath, old_doc=self.doc)
            else:
                self.doc = document.Document.create(self, docpath)

            if not reloading and docpath:
                Gtk.RecentManager.get_default().add_item(self.doc.get_uri())
                extras.FileWatcher.watch_file(docpath, self.reload_document)

        except GLib.Error:
            if reloading:
                return
            self.doc = document.Document.create(self, None)
            self.error_opening_file(docpath)
            extras.FileWatcher.stop_watching()

        # Guess notes mode by default if the document has notes
        if not reloading:
            hpref = self.config.get('notes position', 'horizontal')
            vpref = self.config.get('notes position', 'vertical')
            self.chosen_notes_mode = target_mode = self.doc.guess_notes(hpref, vpref)

            # don't toggle from NONE to NONE
            if self.chosen_notes_mode == document.PdfPage.NONE:
                self.chosen_notes_mode = document.PdfPage.RIGHT

            if self.notes_mode != target_mode:
                self.switch_mode('swap_document', docpath, target_mode = target_mode)

        # Some things that need updating
        self.cache.swap_document(self.doc)
        self.page_number.set_last(self.doc.pages_number())
        self.page_number.enable_labels(self.doc.has_labels())
        self.doc.goto(page, reloading=reloading)
        self.medias.purge_media_overlays()

        # Draw the new page(s)
        if not reloading:
            self.talk_time.pause()
            self.timing.reset(int(self.talk_time.delta))
            self.talk_time.reset_timer()

        self.on_page_change(False, reloading=reloading)
        page_type = self.notes_mode.complement()
        self.c_frame.set_property("ratio", self.doc.current_page().get_aspect_ratio(page_type))


    def reload_document(self):
        """ Reload the current document.
        """
        self.swap_document(self.doc.path, page = self.doc.cur_page, reloading = True)


    def recent_document(self, recent_menu):
        """ Callback for the recent document menu.

        Gets the URI and requests the document swap.

        Args:
            recent_menu (:class:`~Gtk.RecentChooserMenu`): the recent docs menu
        """
        self.swap_document(recent_menu.get_current_uri())


    def on_drag_drop(self, widget, drag_context, x, y, data, info, time):
        """ Receive the drag-drops (as text only). If a file is dropped, open it.

        Args:
            widget (:class:`~Gtk.Widget`): The widget on which the dragged item was dropped
            drag_context (:class:`~Gdk.DragContext`): Context object of the dragging
            x (`float`): position of the drop
            y (`float`):  position of the drop
            data (:class:`~Gtk.SelectionData`): container for the dropped data
            info (`int`): info on the target
            time (`int`): time of the drop
        """
        received = data.get_text()
        if received.startswith('file://'):
            received = received[len('file://'):]

        if os.path.isfile(received) and received.lower().endswith('.pdf'):
            self.swap_document(os.path.abspath(received))

    def export_xopp(self, *args):
        if self.highlight_mode in ('autopage') and self.doc.cur_page >= 0:
            self.doc.scribbles[self.doc.cur_page] = self.scribbler.scribble_list[:]
        self.doc.export_xopp()

    def export_pdf(self, *args):
        # Make sure the current scribbles are also exported
        if self.highlight_mode in ('autopage') and self.doc.cur_page >= 0:
            self.doc.scribbles[self.doc.cur_page] = self.scribbler.scribble_list[:]
        self.doc.export_pdf()

    def pick_file(self, *args):
        """ Ask the user which file he means to open.
        """
        # Use a GTK file dialog to choose file
        dialog = Gtk.FileChooserDialog(title = _('Open...'), transient_for = self.p_win,
                                       action = Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.set_position(Gtk.WindowPosition.CENTER)

        filter = Gtk.FileFilter()
        filter.set_name(_('PDF files'))
        filter.add_mime_type('application/pdf')
        filter.add_pattern('*.pdf')
        dialog.add_filter(filter)

        filter = Gtk.FileFilter()
        filter.set_name(_('All files'))
        filter.add_pattern('*')
        dialog.add_filter(filter)

        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            self.swap_document(os.path.abspath(dialog.get_filename()))

        dialog.destroy()


    def error_opening_file(self, filename):
        """ Remove the current document.
        """
        # Check if the path is valid
        if not os.path.exists(filename):
            msg = _('Could not find the file "{}"').format(filename)
        else:
            msg = _('Error opening the file "{}"').format(filename)
        dialog = Gtk.MessageDialog(transient_for = self.p_win, flags = Gtk.DialogFlags.MODAL,
                                   message_type = Gtk.MessageType.ERROR, message_format = msg)
        dialog.add_buttons(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        dialog.set_position(Gtk.WindowPosition.CENTER)
        dialog.run()
        dialog.destroy()


    def close_file(self, *args):
        """ Remove the current document.
        """
        self.swap_document(None)


    def get_notes_mode(self):
        """ Simple getter.

        Returns (:class:`~pympress.document.PdfPage`):
            Truthy when we split slides in content + notes
        """
        return self.notes_mode

    def page_inserted(self, num):
        """ This page was inserted mid document. Push scribbles one page forward.
        """
        for p in range(self.doc.nb_pages, num, -1):
            if p - 1 in self.doc.scribbles:
                self.doc.scribbles[p] = self.doc.scribbles[p - 1]
                del self.doc.scribbles[p - 1]
        self.redraw_current_slide()

    def insert_page(self, num):
        """ Insert an empty page before page num in document
        """
        self.doc.insert_page(num)
        self.page_number.set_last(self.doc.pages_number())

        # Clear all widget caches
        self.cache.clear_cache(self.c_da.get_name())
        self.cache.clear_cache(self.p_da_cur.get_name())
        self.cache.clear_cache(self.c_da.get_name() + '_zoomed')
        self.cache.clear_cache(self.p_da_cur.get_name() + '_zoomed')

        # Page numbers might not match now
        self.page_number.enable_labels(self.doc.has_labels())


    ##############################################################################
    ############################  Displaying content  ############################
    ##############################################################################

    def page_preview(self, widget, *args):
        """ Switch to another page and display it.

        This is a kind of event which is supposed to be called only from the spin_cur spinner as a callback

        Args:
            widget (:class:`~Gtk.SpinButton`): The spinner button widget calling page_preview
        """
        try:
            widget.set_value(int(widget.get_buffer().get_text()))
        except Exception:
            pass

        page_nb = int(widget.get_value()) - 1
        if page_nb >= self.doc.pages_number() or page_nb < 0:
            return

        page_cur = self.doc.page(page_nb)
        page_next = self.doc.page(page_nb + 1)

        self.page_preview_nb = page_nb

        # Aspect ratios and queue redraws
        if self.notes_mode:
            self.p_frame_notes.set_property('ratio', page_cur.get_aspect_ratio(self.notes_mode))
            self.p_da_notes.queue_draw()

        page_type = self.notes_mode.complement()
        self.p_frame_cur.set_property('ratio', page_cur.get_aspect_ratio(page_type))
        self.p_da_cur.queue_draw()

        if page_next is not None:
            pr = page_next.get_aspect_ratio(page_type)
            self.p_frame_next.set_property('ratio', pr)

        self.p_da_next.queue_draw()

        self.annotations.add_annotations(page_cur.get_annotations())

        # Update display
        self.page_number.update_jump_label(page_cur.label())

        # Prerender the 4 next pages and the 2 previous ones
        cur = self.doc.cur_page
        page_max = min(self.doc.pages_number(), cur + 5)
        page_min = max(0, cur - 2)
        for p in list(range(cur + 1, page_max)) + list(range(cur, page_min, -1)):
            self.cache.prerender(p)


    def on_page_change(self, unpause=True, keep_scribbles=False, reloading=False):
        """ Switch to another page and display it.

        This is a kind of event which is supposed to be called only from the
        :class:`~pympress.document.Document` class.

        Args:
            unpause (`bool`):  `True` if the page change should unpause the timer, `False` otherwise
        """
        page_cur = self.doc.current_page()
        page_next = self.doc.next_page()

        self.annotations.add_annotations(page_cur.get_annotations())

        # Page change: resynchronize miniatures
        self.page_preview_nb = self.doc.cur_page

        # Aspect ratios and queue redraws
        self.p_frame_notes.set_property('ratio', page_cur.get_aspect_ratio(self.notes_mode))
        self.p_da_notes.queue_draw()

        page_type = self.notes_mode.complement()
        pr = page_cur.get_aspect_ratio(page_type)

        self.c_frame.set_property('ratio', pr)
        self.c_da.queue_draw()

        self.p_frame_cur.set_property('ratio', pr)
        self.p_da_cur.queue_draw()

        if page_next is not None:
            pr = page_next.get_aspect_ratio(page_type)
            self.p_frame_next.set_property('ratio', pr)

        self.p_da_next.queue_draw()

        # Remove scribbling/zooming modes
        self.scribbler.disable_scribbling()
        self.zoom.stop_zooming()
        self.scribbler.clean_scribble_list(self.scribbler.scribble_list)

        # Only on real page change
        if not reloading:
            # Handle saved scribbles
            if self.highlight_mode in ('autopage', 'page') and len(self.doc.history) > 1:
                self.doc.scribbles[self.doc.history[-2]] = self.scribbler.scribble_list[:]
            if self.highlight_mode in ('autopage', 'clear', 'page') and not keep_scribbles:
                self.scribbler.clear_scribble(page=True)
            if self.highlight_mode in ('autopage', 'page'):
                try:
                    if self.doc and self.page_preview_nb in self.doc.scribbles:
                        self.scribbler.scribble_list += self.doc.scribbles[self.page_preview_nb][:]
                except AttributeError:
                    pass

        # Start counter if needed
        if unpause:
            self.talk_time.unpause()
        self.timing.transition(self.page_preview_nb, int(self.talk_time.delta))

        # Update display
        self.page_number.update_page_numbers(self.page_preview_nb, page_cur.label())

        # Prerender the 4 next pages and the 2 previous ones
        page_max = min(self.doc.pages_number(), self.page_preview_nb + 5)
        page_min = max(0, self.page_preview_nb - 2)
        for p in list(range(self.page_preview_nb + 1, page_max)) + list(range(self.page_preview_nb, page_min, -1)):
            self.cache.prerender(p)

        self.medias.replace_media_overlays(self.doc.current_page(), page_type)


    def on_draw(self, widget, cairo_context):
        """ Manage draw events for both windows.

        This callback may be called either directly on a page change or as an
        event handler by GTK. In both cases, it determines which widget needs to
        be updated, and updates it, using the
        :class:`~pympress.surfacecache.SurfaceCache` if possible.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget to update
            cairo_context (:class:`~cairo.Context`):  the Cairo context (or `None` if called directly)
        """
        if widget is self.c_da:
            # Current page
            if self.blanked:
                return
            page = self.doc.page(self.doc.cur_page)
            nb = self.doc.cur_page
        elif widget is self.p_da_notes or widget is self.p_da_cur:
            # Current page 'preview'
            page = self.doc.page(self.page_preview_nb)
            nb = self.page_preview_nb
        else:
            page = self.doc.page(self.page_preview_nb + 1)
            nb = self.page_preview_nb + 1
            # No next page: just return so we won't draw anything
            if page is None:
                return

        if not page.can_render():
            return

        name = widget.get_name()
        wtype = self.cache.get_widget_type(name)
        ww, wh = widget.get_allocated_width(), widget.get_allocated_height()

        if self.zoom.scale != 1. and (widget is self.p_da_cur or widget is self.c_da):
            zoom_matrix = self.zoom.get_matrix(ww, wh)
            name += '_zoomed'
        else:
            zoom_matrix = cairo.Matrix()

        pb = self.cache.get(name, nb)
        if pb is None:
            if self.resize_panes and widget in (self.p_da_next, self.p_da_cur, self.p_da_notes):
                # too slow to render here when resize_panes things
                return

            # Cache miss: render the page, and save it to the cache
            try:
                # In some pygtk versions this call always fails the first time
                pb = widget.get_window().create_similar_image_surface(cairo.Format.RGB24, ww, wh, 0)
            except:
                pb = widget.get_window().create_similar_image_surface(cairo.Format.RGB24, ww, wh, 0)

            cairo_prerender = cairo.Context(pb)
            cairo_prerender.transform(zoom_matrix)
            page.render_cairo(cairo_prerender, ww, wh, wtype)

            self.cache.set(name, nb, pb)

            cairo_context.set_source_surface(pb, 0, 0)
            cairo_context.paint()
        else:
            # Cache hit: draw the surface from the cache to the widget
            cairo_context.set_source_surface(pb, 0, 0)
            cairo_context.paint()

        if widget is self.c_da or widget is self.p_da_cur:
            cairo_context.save()
            cairo_context.transform(zoom_matrix)

            if self.show_highlights or (self.scribbler.drawing_mode and widget is self.p_da_cur):
                self.scribbler.draw_scribble(widget, cairo_context,
                                             self.scribbler.draw_blink or widget is not self.p_da_cur, page.pw)
                if widget is self.p_da_cur and self.scribbler.selected and \
                    not self.selected_timeout:
                    self.selected_timeout = GLib.timeout_add(500, self.redraw_selected)
                elif widget is self.p_da_cur and self.scribbler.text_entry and not self.selected_timeout:
                    self.selected_timeout = GLib.timeout_add(500, self.redraw_selected)


            self.zoom.draw_zoom_target(widget, cairo_context)

            cairo_context.restore()

        if widget is self.c_da:
            # do not use the zoom matrix for the pointer, it is relative to the screen not the slide
            self.laser.render_pointer(cairo_context, ww, wh)

            if self.pen_pointer[0]:
                x = self.pen_pointer[0][0] * ww - self.pen_pointer_c.get_width() / 2
                y = self.pen_pointer[0][1] * wh - self.pen_pointer_c.get_height() / 2
                Gdk.cairo_set_source_pixbuf(cairo_context, self.pen_pointer_c, x, y)
                cairo_context.paint()

        elif widget is self.p_da_cur:
            pos = self.pen_pointer[0] if self.pen_pointer[0] else \
                  self.laser.pointer_pos if self.laser.show_pointer else None
            if pos and self.scribbler.pen_pointer_p:
                x = pos[0] * ww - int(self.scribbler.pen_pointer_p.get_option('x_hot'))
                y = pos[1] * wh - int(self.scribbler.pen_pointer_p.get_option('y_hot'))
                Gdk.cairo_set_source_pixbuf(cairo_context, self.scribbler.pen_pointer_p, x, y)
                cairo_context.paint()
            if self.vlines > 1:
                for i in range(1, int(self.vlines) + 1):
                    cairo_context.set_source_rgba(0.5, 0.5, 0.5, 0.5)
                    cairo_context.set_line_width(1)
                    cairo_context.move_to(ww * i / self.hlines, 0)
                    cairo_context.line_to(ww * i / self.hlines, wh - 1)
                cairo_context.stroke()
            if self.hlines > 1:
                for i in range(1, int(self.hlines) + 1):
                    cairo_context.set_source_rgba(0.5, 0.5, 0.5, 0.5)
                    cairo_context.set_line_width(1)
                    cairo_context.move_to(0, wh * i / self.hlines)
                    cairo_context.line_to(ww - 1, wh * i / self.hlines)
                cairo_context.stroke()


    def clear_zoom_cache(self):
        """ Callback to clear the cache of zoomed widgets.
        """
        self.cache.clear_cache(self.c_da.get_name() + '_zoomed')
        self.cache.clear_cache(self.p_da_cur.get_name() + '_zoomed')


    def redraw_current_slide(self):
        """ Callback to queue a redraw of the current slides (in both winows).
        """
        self.c_da.queue_draw()
        self.p_da_cur.queue_draw()


    ##############################################################################
    ############################     User inputs      ############################
    ##############################################################################

    def on_navigation(self, widget, event, command=None):
        """ Manage key presses for both windows.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget in which the event occurred (ignored)
            event (:class:`~Gdk.Event`):  the event that occurred

        Returns:
            `bool`: whether the event was consumed
        """
        if not command:
            if event.type != Gdk.EventType.KEY_PRESS:
                return

            if self.scribbler.text_entry and self.scribbler.key_entered(event.keyval, event.string, event.get_state()):
                return True

            name = Gdk.keyval_name(event.keyval)
            ctrl_pressed = event.get_state() & Gdk.ModifierType.CONTROL_MASK
            shift_pressed = event.get_state() & Gdk.ModifierType.SHIFT_MASK
            meta_pressed = event.get_state() & Gdk.ModifierType.MOD1_MASK

            command = self.config.shortcuts.get((event.keyval, ctrl_pressed | shift_pressed | meta_pressed), None)
        else:
            name = command
            ctrl_pressed = False
            shift_pressed = False
            meta_pressed = False

        # Try passing events to special-behaviour widgets (spinner, ett, zooming, scribbler) in case they are enabled
        if self.page_number.on_keypress(widget, event, name, command):
            return True
        elif self.est_time.on_keypress(widget, event, name, command):
            return True
        elif self.zoom.nav_zoom(name, ctrl_pressed, command):
            return True
        elif self.scribbler.nav_scribble(name, ctrl_pressed, command):
            return True

        # first key unpauses, next advance by one page
        elif command in {'next', 'next_label'} and self.talk_time.unpause():
            pass
        elif command == 'next':
            self.doc.goto_next(keep_scribbles=shift_pressed)
        elif command == 'next_label':
            self.doc.label_next()
        elif command == 'prev':
            self.doc.goto_prev()
        elif command == 'prev_label':
            self.doc.label_prev()
        elif command == 'hist_back':
            self.doc.hist_prev()
        elif command == 'hist_forward':
            self.doc.hist_next()
        elif command == 'first':
            self.doc.goto_home()
        elif command == 'last':
            self.doc.goto_end()
        elif command == 'fullscreen_content':
            self.switch_fullscreen(self.c_win)
        elif command == 'fullscreen_presenter':
            self.switch_fullscreen(self.p_win)
        elif command == 'quit':
            self.save_and_quit()
        elif command == 'pause_timer':
            self.talk_time.switch_pause(widget, event)
        elif command == 'reset_timer':
            self.timing.reset(int(self.talk_time.delta))
            self.talk_time.reset_timer()
        elif command == 'highlight':
            return self.scribbler.enable_draw(widget, event)
        elif command == 'zoom':
            return self.zoom.start_zooming(widget, event)
        elif command == 'unzoom':
            return self.zoom.stop_zooming(widget, event)
        elif command in {'goto_page', 'jumpto_label'}:
            return self.page_number.on_label_event(widget, event, command)
        elif command == 'talk_time':
            return self.est_time.on_label_event(widget, event, command)
        elif command == 'notes_mode':
            self.switch_mode(widget, event)
        elif command == 'annotations':
            self.switch_annotations(widget, event)
        elif command == 'swap_screens':
            self.swap_screens()
        elif command == 'blank_screen':
            self.switch_blanked(widget, event)
        elif command == 'close_file':
            self.close_file()
        elif command == 'open_file':
            self.pick_file()
        elif command == 'toggle_pointermode':
            self.laser.toggle_pointermode()
        elif command == 'toggle_highlights':
            self.show_highlights = not self.show_highlights
            self.redraw_current_slide()
        elif command == 'show_highlights':
            self.show_highlights = True
            self.redraw_current_slide()
        elif command == 'hide_highlights':
            self.show_highlights = False
            self.redraw_current_slide()
        elif command == "insert_blank":
            self.insert_page(self.doc.cur_page + 1)
        elif command == "page_inserted":
            self.page_inserted(self.doc.cur_page + 1)
        elif command == "export_pdf":
            self.export_pdf()
        elif command == "export_xopp":
            self.export_xopp()
        else:
            if command:
                logger.error('ERROR: missing command "{}" for {}{}{}{}'
                             .format(command, 'ctrl + ' if ctrl_pressed else '', 'shift + ' if shift_pressed else '',
                                     'meta + ' if meta_pressed else '', name))
            return False

        return True

    def toolbar_click(self, widget):
        self.on_navigation(widget, None, command=widget.get_name())
        pass

    def on_scroll(self, widget, event):
        """ Manage scroll events.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget in which the event occurred (ignored)
            event (:class:`~Gdk.Event`):  the event that occurred

        Returns:
            `bool`: whether the event was consumed
        """
        if event.type != Gdk.EventType.SCROLL:
            return False

        # send to spinner if it is active
        elif self.page_number.on_scroll(widget, event):
            return True
        elif self.annotations.on_scroll(widget, event):
            return True
        else:
            return False


    def track_enter_leave(self, widget, event):
        if event.type == Gdk.EventType.LEAVE_NOTIFY and widget is self.p_da_cur:
            self.scribbler.stamp_point = [-1, -1]
        return self.laser.track_enter_leave(widget, event)


    def track_motions(self, widget, event):
        """ Track mouse motion events.

        Handles mouse motions on the "about" menu.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget that received the mouse motion
            event (:class:`~Gdk.Event`):  the GTK event containing the mouse position

        Returns:
            `bool`: whether the event was consumed
        """
        if self.zoom.track_zoom_target(widget, event):
            return True
        elif self.scribbler.track_scribble(self.zoom.get_slide_point(widget, event), event.get_button()):
            return True
        elif self.laser.track_pointer(widget, event):
            return True
        else:
            return self.hover_link(widget, event)


    def track_clicks(self, widget, event):
        """ Track mouse press and release events.

        Handles clicks on the slides.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget that received the click
            event (:class:`~Gdk.Event`):  the GTK event containing the click position

        Returns:
            `bool`: whether the event was consumed
        """
        if self.zoom.toggle_zoom_target(widget, event):
            return True
        elif self.scribbler.toggle_scribble(widget, event.get_event_type(), self.zoom.get_slide_point(widget, event),
                                            event.get_button(), state=event.get_state()):
            return True
        elif self.laser.toggle_pointer(widget, event):
            return True
        else:
            return self.click_link(widget, event)


    def click_link(self, widget, event):
        """ Check whether a link was clicked and follow it.

        Handles a click on a slide.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget in which the event occurred
            event (:class:`~Gdk.Event`):  the event that occurred

        Returns:
            `bool`: whether the event was consumed
        """
        if event.type == Gdk.EventType.BUTTON_RELEASE:
            return False

        # Where did the event occur?
        if widget is self.p_da_next:
            page = self.doc.next_page()
            if page is None:
                return False
        else:
            page = self.doc.current_page()

        x, y = self.zoom.get_slide_point(widget, event)
        page_mode = self.notes_mode if widget is self.p_da_notes else self.notes_mode.complement()
        link = page.get_link_at(x, y, page_mode)

        if event.type == Gdk.EventType.BUTTON_PRESS and link is not None:
            link.follow()
            return True
        else:
            return False


    def hover_link(self, widget, event):
        """ Manage events related to hyperlinks, setting the cursor to a pointer if the hovered region is clickable.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget in which the event occurred
            event (:class:`~Gdk.Event`):  the event that occurred

        Returns:
            `bool`: whether the event was consumed
        """
        if event.type != Gdk.EventType.MOTION_NOTIFY:
            return False

        # Where did the event occur?
        if widget is self.p_da_next:
            page = self.doc.next_page()
            if page is None:
                return
        else:
            page = self.doc.current_page()

        x, y = self.zoom.get_slide_point(widget, event)
        page_mode = self.notes_mode if widget is self.p_da_notes else self.notes_mode.complement()

        if page.get_link_at(x, y, page_mode):
            extras.Cursor.set_cursor(widget, 'pointer')
            return False
        else:
            extras.Cursor.set_cursor(widget, 'parent')
            return True


    def switch_fullscreen(self, widget):
        """ Switch the Content window to fullscreen (if in normal mode) or to normal mode (if fullscreen).

        Screensaver will be disabled when entering fullscreen mode, and enabled
        when leaving fullscreen mode.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget in which the event occurred

        Returns:
            `bool`: whether some window's full screen status got toggled
        """
        if isinstance(widget, Gtk.CheckMenuItem):
            # Called from menu -> use c_win
            toggle_to = widget.get_active()
            widget = self.c_win
        else:
            toggle_to = None

        if widget != self.c_win and widget != self.p_win:
            logger.error(_("Unknow widget {} to be fullscreened, aborting.").format(widget))
            return False

        cur_state = (widget.get_window().get_state() & Gdk.WindowState.FULLSCREEN)

        if cur_state == toggle_to:
            return
        elif cur_state:
            widget.unfullscreen()
        else:
            widget.fullscreen()

        if widget == self.c_win:
            self.pres_fullscreen.set_active(not cur_state)

        return True


    def on_window_state_event(self, widget, event):
        """ Track whether the preview window is maximized.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget in which the event occurred
            event (:class:`~Gtk.Event`):  the event that occurred

        Returns:
            `bool`: whether the event was consumed.
        """
        if widget.get_name() == self.c_win.get_name():
            fullscreen = (Gdk.WindowState.FULLSCREEN & event.new_window_state) != 0
            util.set_screensaver(fullscreen, self.c_win.get_window())
        return False


    def update_frame_position(self, widget, user_data):
        """ Callback to preview the frame alignment, called from the Gtk.SpinButton.

        Args:
            widget (:class:`~Gtk.SpinButton`): The button updating the slide alignment in the drawing area widget
            user_data (`str`): The property being set, either the x or y alignment (resp. xalign and yalign).
        """
        self.c_frame.set_property(user_data, widget.get_value())


    def adjust_frame_position(self, *args):
        """ Select how to align the frame on screen.
        """
        win_aspect_ratio = float(self.c_win.get_allocated_width()) / self.c_win.get_allocated_height()

        if win_aspect_ratio <= float(self.c_frame.get_property("ratio")):
            prop = "yalign"
        else:
            prop = "xalign"

        val = self.c_frame.get_property(prop)

        button = Gtk.SpinButton()
        button.set_adjustment(Gtk.Adjustment(lower=0.0, upper=1.0, step_increment=0.01))
        button.set_digits(2)
        button.set_value(val)
        button.connect("value-changed", self.update_frame_position, prop)

        popup = Gtk.Dialog(title = _("Adjust alignment of slides in projector screen"), transient_for = self.p_win)
        popup.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK)

        box = popup.get_content_area()
        box.add(button)
        popup.show_all()
        response = popup.run()
        popup.destroy()

        # revert if we cancelled
        if response == Gtk.ResponseType.CANCEL:
            self.c_frame.set_property(prop, val)
        else:
            self.config.set('content', prop, str(button.get_value()))


    def show_timing_report(self, *args):
        """ Show the popup with information on timing of the talk.

        Gather current time, document structure, page labels etc. and pass it to timing popup for display.
        """
        self.timing.show(int(self.talk_time.delta), self.doc.get_structure(), self.doc.page_labels)


    ##############################################################################
    ############################    Option toggles    ############################
    ##############################################################################

    def swap_screens(self, *args):
        """ Swap the monitors on which each window is displayed (if there are 2 monitors at least).
        """
        screen = self.p_win.get_screen()

        # Though Gtk.Window is a Gtk.Widget get_parent_window() actually returns None on self.{c,p}_win
        p_monitor = screen.get_monitor_at_window(self.p_central.get_parent_window())
        c_monitor = screen.get_monitor_at_window(self.c_frame.get_parent_window())

        if screen.get_n_monitors() == 1 or p_monitor == c_monitor:
            return

        p_win_state = self.p_win.get_window().get_state()
        c_win_state = self.c_win.get_window().get_state()
        if (c_win_state & Gdk.WindowState.FULLSCREEN) != 0:
            self.c_win.unfullscreen()
        if (p_win_state & Gdk.WindowState.FULLSCREEN) != 0:
            self.p_win.unfullscreen()
        if (c_win_state & Gdk.WindowState.MAXIMIZED) != 0:
            self.c_win.unmaximize()
        if (p_win_state & Gdk.WindowState.MAXIMIZED) != 0:
            self.p_win.unmaximize()

        p_monitor, c_monitor = c_monitor, p_monitor

        cx, cy, cw, ch = self.c_win.get_position() + self.c_win.get_size()
        px, py, pw, ph = self.p_win.get_position() + self.p_win.get_size()

        c_bounds = screen.get_monitor_geometry(c_monitor)
        p_bounds = screen.get_monitor_geometry(p_monitor)
        self.c_win.move(c_bounds.x + max(0, c_bounds.width - cw) / 2, c_bounds.y + max(0, c_bounds.height - ch) / 2)
        self.p_win.move(p_bounds.x + max(0, p_bounds.width - pw) / 2, p_bounds.y + max(0, p_bounds.height - ph) / 2)

        if (c_win_state & Gdk.WindowState.MAXIMIZED) != 0:
            self.c_win.maximize()
        if (p_win_state & Gdk.WindowState.MAXIMIZED) != 0:
            self.p_win.maximize()
        if (c_win_state & Gdk.WindowState.FULLSCREEN) != 0:
            self.c_win.fullscreen()
        if (p_win_state & Gdk.WindowState.FULLSCREEN) != 0:
            self.p_win.fullscreen()


    def switch_blanked(self, widget, event = None):
        """ Switch the blanked mode of the content screen.

        Returns:
            `bool`: whether the mode has been toggled.
        """
        if issubclass(type(widget), Gtk.CheckMenuItem) and widget.get_active() == self.blanked:
            return False

        self.blanked = not self.blanked
        self.c_da.queue_draw()
        self.pres_blank.set_active(self.blanked)

        return True


    def swap_layout(self, old, new):
        """ Save the old layout in the prefs, load the new layout.

        Args:
            old (`str`): the name of the layout to save, `None` to use plain or notes automatically
            new (`str`): the name of the layout to load, `None` to use plain or notes automatically
        """
        if old is None: old = 'notes' if self.notes_mode else 'plain'
        if new is None: new = 'notes' if self.notes_mode else 'plain'

        self.config.update_layout(old, self.p_central.get_children()[-1], self.pane_handle_pos)
        pane_handles = self.replace_layout(self.config.get_layout(new), self.p_central,
                                           self.placeable_widgets, self.on_pane_event)
        self.pane_handle_pos.update(pane_handles)

        # queue visibility of all newly added widgets, make sure visibility is right
        self.p_central.show_all()
        self.p_frame_annot.set_visible(self.show_annotations)


    def change_notes_pos(self, widget, event = None, force_change = False):
        """ Switch the position of the nodes in the slide.

        Returns:
            `bool`: whether the mode has been toggled.
        """
        if issubclass(type(widget), Gtk.CheckMenuItem):
            # if this widget is not the active one do nothing
            if not widget.get_active():
                return False
            target_mode = document.PdfPage[widget.get_name()[len('notes_'):].upper()]
        elif issubclass(type(widget), document.PdfPage):
            target_mode = widget
        else:
            return False

        # Redundant toggle, do nothing
        if target_mode == self.chosen_notes_mode:
            return False

        # Update the choice, except for NONE
        if target_mode:
            self.chosen_notes_mode = target_mode
            self.get_object('notes_' + target_mode.name.lower()).set_active(True)
            self.config.set('notes position', target_mode.direction(), target_mode.name.lower())

        # Change the notes arrangement if they are enabled or if we are forced to
        if self.notes_mode or force_change:
            self.switch_mode('changed notes position', target_mode = target_mode)

        return True


    def switch_mode(self, widget, event = None, target_mode = None):
        """ Switch the display mode to "Notes mode" or "Normal mode" (without notes).

        Returns:
            `bool`: whether the mode has been toggled.
        """
        if issubclass(type(widget), Gtk.CheckMenuItem) and widget.get_active() == bool(self.notes_mode):
            # We toggle the menu item which brings us here, but it is somehow already in sync with notes mode.
            # Exit to not risk double-toggling. Button is now in sync and can be toggled again correctly.
            return False

        if target_mode is None:
            target_mode = document.PdfPage.NONE if self.notes_mode else self.chosen_notes_mode

        if target_mode == self.notes_mode:
            return False

        self.scribbler.disable_scribbling()

        if target_mode and not self.notes_mode:
            self.swap_layout('plain', 'notes')
        elif not target_mode and self.notes_mode:
            self.swap_layout('notes', 'plain')

        self.notes_mode = target_mode
        page_type = self.notes_mode.complement()

        self.cache.set_widget_type('c_da', page_type)
        self.cache.set_widget_type('c_da_zoomed', page_type)
        self.cache.set_widget_type('p_da_next', page_type)
        self.cache.set_widget_type('p_da_cur', page_type)
        self.cache.set_widget_type('p_da_cur_zoomed', page_type)
        self.cache.set_widget_type('p_da_notes', self.notes_mode)

        if self.notes_mode:
            self.cache.enable_prerender('p_da_notes')
            self.cache.disable_prerender('p_da_cur')
        else:
            self.cache.disable_prerender('p_da_notes')
            self.cache.enable_prerender('p_da_cur')

        self.medias.adjust_margins_for_mode(page_type)
        self.on_page_change(False)
        self.pres_notes.set_active(self.notes_mode)

        return True


    def switch_annotations(self, widget, event = None):
        """ Switch the display to show annotations or to hide them.

        Returns:
            `bool`: whether the mode has been toggled.
        """
        if issubclass(type(widget), Gtk.CheckMenuItem) and widget.get_active() == self.show_annotations:
            return False

        self.show_annotations = not self.show_annotations

        self.p_frame_annot.set_visible(self.show_annotations)
        self.config.set('presenter', 'show_annotations', 'on' if self.show_annotations else 'off')

        if self.show_annotations:
            parent = self.p_frame_annot.get_parent()
            if issubclass(type(parent), Gtk.Paned):
                if parent.get_orientation() == Gtk.Orientation.HORIZONTAL:
                    size = parent.get_parent().get_allocated_width()
                else:
                    size = parent.get_parent().get_allocated_height()
                parent.set_position(self.pane_handle_pos[parent] * size)

        self.annotations.add_annotations(self.doc.current_page().get_annotations())
        self.pres_annot.set_active(self.show_annotations)

        return True


    def switch_bigbuttons(self, *args):
        """ Toggle the display of big buttons (nice for touch screens).
        """
        self.show_bigbuttons = not self.show_bigbuttons

        self.prev_button.set_visible(self.show_bigbuttons)
        self.next_button.set_visible(self.show_bigbuttons)
        self.laser_button.set_visible(self.show_bigbuttons)
        self.highlight_button.set_visible(self.show_bigbuttons)
        self.config.set('presenter', 'show_bigbuttons', 'on' if self.show_bigbuttons else 'off')

def format_width(widget, value, curve):
    """
    """
    return str(curve(value))


##
# Local Variables:
# mode: python
# indent-tabs-mode: nil
# py-indent-offset: 4
# fill-column: 80
# end:
