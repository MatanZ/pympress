# -*- coding: utf-8 -*-
#
#       pointer.py
#
#       Copyright 2017 Cimbali <me@cimba.li>
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
:mod:`pympress.scribble` -- Manage user drawings on the current slide
---------------------------------------------------------------------
"""

from __future__ import print_function, unicode_literals

import logging
logger = logging.getLogger(__name__)

import math
import os
import sympy
import io
import copy

import gi
import cairo
gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gdk, Pango, PangoCairo, GLib, GdkPixbuf

from pympress import builder, extras, evdev_pad

def ccw(A, B, C):
    """ Returns True if triangle ABC is counter clockwise
    """
    return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])

def segments_intersect(A, B, C, D):
    """ Return true if line segments AB and CD intersect
    """
    return ccw(A, C, D) != ccw(B, C, D) and ccw(A, B, C) != ccw(A, B, D)

def point_in_rect_ordered(point, rect):
    """ rect is a pair of coordinates pairs. First pair must be the smaller numbers
    """
    return rect[0][0] <= point[0] <= rect[1][0] and rect[0][1] <= point[1] <= rect[1][1]

def add_point_rect_ordered(point, rect):
    """ Modify rect to include point, if necessary
    """
    if point[0] < rect[0][0]:
        rect[0][0] = point[0]
    elif point[0] > rect[1][0]:
        rect[1][0] = point[0]
    if point[1] < rect[0][1]:
        rect[0][1] = point[1]
    elif point[1] > rect[1][1]:
        rect[1][1] = point[1]

def intersects(p0, p1, scribble):
    """ Returns true if the line segment intersect the scribble
    """
    if scribble[0] == 'segment' and p0 and (point_in_rect_ordered(p1, scribble[4])
        or point_in_rect_ordered(p0, scribble[4])):
        for i in range(len(scribble[3]) - 1):
            if segments_intersect(p1, p0, scribble[3][i], scribble[3][i + 1]):
                return True
    elif scribble[0] in ("box", "text", "ellipse", "image", "latex"):
        if min(scribble[4][0][0], scribble[4][1][0]) <= p1[0] <= \
           max(scribble[4][0][0], scribble[4][1][0]) and \
           min(scribble[4][0][1], scribble[4][1][1]) <= p1[1] <= \
           max(scribble[4][0][1], scribble[4][1][1]):
            return True
    return False

def adjust_points(pts_l, dx, dy):
    for i in range(len(pts_l)):
        pts_l[i] = [pts_l[i][0] + dx, pts_l[i][1] + dy]

def adjust_scribbles(scribbles, dx, dy):
    for s in scribbles:
        adjust_points(s[3], dx, dy)
        if s[0] in ("segment", "box", "text", "ellipse"):
            adjust_points(s[4], dx, dy)

def has_fill(scribble):
    try:
        return scribble[0] in ["box", "ellipse"]
    except:
        return False

def is_text(scribble):
    try:
        return scribble[0] in ["latex", "text"]
    except:
        return False

def rgba_to_tuple(obj):
    return (obj.red, obj.green, obj.blue, obj.alpha)

class Scribbler(builder.Builder):
    """ UI that allows to draw free-hand on top of the current slide.

    Args:
        config (:class:`~pympress.config.Config`): A config object containing preferences
        builder (:class:`~pympress.builder.Builder`): A builder from which to load widgets
        notes_mode (`bool`): The current notes mode, i.e. whether we display the notes on second slide
    """
    #: `list` of scribbles to be drawn, as tuples of type `string`, color `tuple`, width `int`, and a `list` of points.
    scribble_list = []
    #: Whether the current mouse movements are drawing strokes or should be ignored
    scribble_drawing = False
    #: `tuple` current color of the scribbling tool
    scribble_color = (0,0,0,0)
    #: `int` current stroke width of the scribbling tool
    scribble_width = 1
    #: :class:`tuple` current fill color of the scribbling tool
    fill_color = (0,0,0,0)

    #: :class:`~Gtk.EventBox` for the scribbling in the Content window, captures freehand drawing
    scribble_c_eb = None
    #: :class:`~Gtk.EventBox` for the scribbling in the Presenter window, captures freehand drawing
    scribble_p_eb = None

    #: :class:`~Gtk.Box` in the Presenter window, where we insert scribbling.
    p_central = None
    p_da_cur = None
    c_da = None

    #: :class:`~Gtk.Button` that is clicked to stop zooming, unsensitive when there is no zooming
    zoom_stop_button = None

    #: callback, to be connected to :func:`~pympress.surfacecache.SurfaceCache.resize_widget`
    resize_cache = lambda: None
    #: callback, to be connected to :func:`~pympress.ui.UI.on_draw`
    on_draw = lambda: None
    #: callback, to be connected to :func:`~pympress.ui.UI.track_motions`
    track_motions = lambda: None
    #: callback, to be connected to :func:`~pympress.ui.UI.track_clicks`
    track_clicks = lambda: None

    #: callback, to be connected to :func:`~pympress.ui.UI.redraw_current_slide`
    redraw_current_slide = lambda: None

    #: callback, to be connected to :func:`~pympress.extras.Zoom.get_slide_point`
    get_slide_point = lambda: None
    #: callback, to be connected to :func:`~pympress.extras.Zoom.start_zooming`
    start_zooming = lambda: None
    #: callback, to be connected to :func:`~pympress.extras.Zoom.stop_zooming`
    stop_zooming = lambda: None

    #: save button used to drag, since this info is only given on first drag event
    drag_button = 0
    #: previous point in right button drag event
    last_del_point = None
    #: Indicates drawing mode: "draw", "erase", "box", "line", "select_t", "select_r", "move"
    drawing_mode = None

    #: position of the pen (writing pad) pointer (from UI class)
    pen_pointer = None
    #:
    pen_event = None

    #: Number of last set predefined pen attributes
    pen_num = -1
    #: Undo stack
    undo_stack = []
    #: Position in undo stack. Allows re-do
    undo_stack_pos = 0

    selected = []
    select_rect = [[],[]]
    clipboard = None

    min_distance = 0

    scribble_font = "serif 16"
    font_size = 16
    text_entry = False
    draw_blink = True
    text_alignment = 0
    show_text_frames = False
    latex_dict = {}
    latex_prefixes = set()

    stamps = {}
    stamp_names = []
    stamp = {}
    stamp_point = [-1, -1]

    def __init__(self, config, builder, notes_mode):
        super(Scribbler, self).__init__()

        builder.load_widgets(self)

        self.on_draw = builder.get_callback_handler('on_draw')
        self.track_motions = builder.get_callback_handler('track_motions')
        self.track_clicks = builder.get_callback_handler('track_clicks')
        self.redraw_current_slide = builder.get_callback_handler('redraw_current_slide')
        self.resize_cache = builder.get_callback_handler('cache.resize_widget')
        self.get_slide_point = builder.get_callback_handler('zoom.get_slide_point')
        self.start_zooming = builder.get_callback_handler('zoom.start_zooming')
        self.stop_zooming = builder.get_callback_handler('zoom.stop_zooming')

        self.connect_signals(self)

        color = Gdk.RGBA()
        color.parse(config.get('scribble', 'color'))
        self.scribble_color = rgba_to_tuple(color)
        self.scribble_width = config.getfloat('scribble', 'width')
        color.parse(config.get('scribble', 'fill_color'))
        self.fill_color = rgba_to_tuple(color)

        self.config = config

        self.pen_event = evdev_pad.PenEventLoop(self)
        if self.pen_event.pen_thread:
            self.pen_pointer = builder.pen_pointer
        else:
            self.pen_event = None
        self.min_distance = builder.min_distance

        self.read_stamps(config)

    def latex_to_pixbuf(self, text, size, color, png=False):
        # Rudimentary check for legal latex string:
        if text.count("$") % 2 or \
           text.count("\\begin") != text.count("\\end"):
            return None
        preamble = """ \\documentclass[varwidth,12pt]{standalone}
            \\usepackage{amsmath,amsfonts,xcolor}
            \\begin{document}
            \\color[rgb]{%f,%f,%f}
        """ % (color[0], color[1], color[2])
        fn = "/tmp/ppl.png"
        try:
            #buf = io.BytesIO()
            #sympy.preview(text, output='png', viewer='BytesIO', outputbuffer=buf,
            #              dvioptions=["-T", "tight", "-z", "0", "--truecolor", "-D " + str(size)])
            sympy.preview("\\color[rgb]{%f,%f,%f}\n%s" % (color[0], color[1], color[2], text),
                          output='png', viewer="file", filename=fn, euler=False,
                          preamble=preamble,
                          dvioptions=["-T", "tight", "-z", "0", "--truecolor", "-D " + str(size)])
            if png:
                buf = open(fn,"rb").read()
            else:
                buf = GdkPixbuf.Pixbuf.new_from_file(fn)
        except Exception as e:
            return None
        return buf


    def evdev_callback_buttons(self, name):
        self.nav_scribble(name, False, command=name)
        return False

    def evdev_callback_pen(self, point):
        self.track_scribble(point, (False, 0))
        return False

    def evdev_callback_pointer(self, point):
        self.set_pointer(point)
        return False

    def evdev_callback_track(self, data):
        self.toggle_scribble(None, *data, always=True)
        return False

    def nav_scribble(self, name, ctrl_pressed, command=None):
        """ Handles an key press event: undo or disable scribbling.

        Args:
            name (`str`): The name of the key pressed
            ctrl_pressed (`bool`): whether the ctrl modifier key was pressed
            command (`str`): the name of the command in case this function is called by on_navigation

        Returns:
            `bool`: whether the event was consumed
        """
        if command == 'undo':
            self.undo()
        elif command == 'redo':
            self.redo()
        elif command == 'clear_all':
            self.clear_scribble()
        elif command == 'scribble':
            self.enable_scribbling()
        elif command == 'toggle_erase':
            self.switch_erasing()
        elif command == 'draw':
            self.enable_draw()
        elif command == 'erase':
            self.enable_erase()
        elif command == 'latex':
            self.enable_latex()
        elif command == 'ellipse':
            self.enable_ellipse()
        elif command == 'box':
            self.enable_box()
        elif command == 'line':
            self.enable_line()
        elif command == 'text':
            self.enable_text(ctrl_pressed)
        elif command == 'stamp':
            self.enable_stamp()
        elif command == 'select_t':
            self.enable_select_touch()
        elif command == 'select_r':
            self.enable_select_rect()
        elif command == 'cancel':
            self.disable_scribbling()
        elif command == 'pen':
            self.set_pen(name)
        elif command == 'fill_copy':
            self.fill_color = self.scribble_color
        elif command == 'copy':
            self.copy()
        elif command == 'paste':
            self.paste()
        elif command == 'move':
            self.enable_move()
        elif command == 'del_selected':
            self.del_selected()
        elif command == 'select_all':
            self.select_all()
        elif command == 'select_none':
            self.select_none()
        elif command == 'select_toggle':
            self.select_toggle()
        elif command == 'next_tool':
            self.next_tool()
        elif command == 'next_stamp':
            self.next_stamp()
        elif command == 'BTN_0':
            # Next pen
            self.pen_num = self.pen_num % 8 + 1
            self.set_pen(str(self.pen_num))
        elif command == 'BTN_1':
            self.next_tool()
        elif command == 'BTN_7':
            self.enable_draw()
        elif command == 'BTN_6':
            self.enable_erase()
        elif command == 'BTN_5':
            self.enable_box()
        elif command == 'BTN_4':
            self.enable_line()
        elif command == 'BTN_3':
            self.enable_select_touch()
        elif command == 'BTN_2':
            self.enable_select_rect()
        else:
            return False
        return True

    def key_entered(self, val, s, state):
        if not self.text_entry or not self.scribble_list or not is_text(self.text_entry):
            return False
        mode = self.text_entry[0]
        macros = mode
        if mode == "text" and self.text_entry[5] and self.text_entry[5][0] == '\0':
            macros = "markup"
        print(f"unknown key, {val=}, {s=}, name={Gdk.keyval_name(val)} {state=}   {mode=} {macros=}")
        shortcuts = mode == "text"
        pos = self.text_pos
        # ctrl-2 is special:
        if val in (50, 64) and state & Gdk.ModifierType.CONTROL_MASK:
            s = chr(val)
        if val in (Gdk.KEY_Escape, Gdk.KEY_Page_Down, Gdk. KEY_Page_Up):
            self.text_entry = False
            return val in (Gdk.KEY_Escape, )
        elif val == Gdk.KEY_BackSpace:
            self.text_entry[5] = self.text_entry[5][:pos - 1] + self.text_entry[5][pos:]
            pos = pos - 1
            self.text_entry[4] = [[0, 0], [0, 0]]
        elif val == Gdk.KEY_End:
            if state & Gdk.ModifierType.CONTROL_MASK:
                pos = len(self.text_entry[5])
            else:
                if pos > 0:
                    i = self.text_entry[5].find('\r', pos)
                    pos = len(self.text_entry[5]) if i == -1 else i
        elif val == Gdk.KEY_Home:
            if state & Gdk.ModifierType.CONTROL_MASK:
                pos = 0
            else:
                if pos > 0:
                    i = self.text_entry[5].rfind('\r', 0, pos)
                    pos = i + 1
        elif val == Gdk.KEY_Left and pos > 0:
            pos = pos - 1
        elif val == Gdk.KEY_Right and pos < len(self.text_entry[5]):
            pos = pos + 1
        elif val == Gdk.KEY_Delete and pos < len(self.text_entry[5]):
            self.text_entry[5] = self.text_entry[5][:pos] + self.text_entry[5][pos + 1:]
        elif (31 < val < 65280 or val in (Gdk.KEY_Return, )) and s and state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
            mod = "alt" if state & Gdk.ModifierType.MOD1_MASK else ""
            mod += "ctrl" if state & Gdk.ModifierType.CONTROL_MASK else ""
            try:
                s, p = self.latex_macros[macros][mod][chr(val)]
                self.text_entry[5] = self.text_entry[5][:pos] + s + self.text_entry[5][pos:]
                pos += p
            except:
                pass

        elif (31 < val < 65280 or val in (Gdk.KEY_Return, )) and s and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
            self.text_entry[5] = self.text_entry[5][:pos] + s + self.text_entry[5][pos:]
            pos = pos + 1
            i = self.text_entry[5].rfind('\\', 0, pos - 1) if shortcuts else -2
            if i > -1:
                if self.text_entry[5][i+1:pos] in self.latex_dict:
                    if self.text_entry[5][i+1:pos] not in self.latex_prefixes:
                        self.text_entry[5] = self.text_entry[5][:i] + self.latex_dict[self.text_entry[5][i+1:pos]] + self.text_entry[5][pos:]
                        pos = i + 1
                elif not self.text_entry[5][pos-1].isalpha() and self.text_entry[5][i+1:pos-1] in self.latex_dict:
                    self.text_entry[5] = self.text_entry[5][:i] + self.latex_dict[self.text_entry[5][i+1:pos-1]] + self.text_entry[5][pos-1:]
                    pos = i + 2
                elif i < len(self.text_entry[5]) - 3 and self.text_entry[5][i+1] == 'u':
                    if self.text_entry[5][-1].lower() not in "0123456789abcdef":
                        try:
                            h = int(self.text_entry[5][i+2:pos-1],16)
                            self.text_entry[5] = self.text_entry[5][:i] + chr(h) + self.text_entry[5][pos:]
                            pos = i + 1
                        except ValueError:
                            pass
                    elif i == pos - 7:
                        try:
                            h = int(self.text_entry[5][i+2:pos],16)
                            self.text_entry[5] = self.text_entry[5][:i] + chr(h) + self.text_entry[5][pos:]
                            pos = i + 1
                        except ValueError:
                            pass
            self.text_entry[4] = [[0, 0], [0, 0]]
        else:
            #logger.debug(f"unknown key, {val=}, {s=}, name={Gdk.keyval_name(val)}")
            pass
        if mode == "latex" and self.text_entry[7] != self.text_entry[5]:
            self.text_entry[6] = self.latex_to_pixbuf(self.text_entry[5], 6 * self.font_size, self.scribble_color)
            self.text_entry[7] = self.text_entry[5]
        self.redraw_current_slide()
        self.text_pos = pos
        return True

    def read_stamps(self, config):
        if 'stamps' in config:
            iconw, iconh = 32, 32
            for name in config['stamps']:
                self.stamp_names.append(name)
                stamp_str = self.config.get('stamps', name)
                p = stamp_str.split(':')
                if stamp_str.startswith('/') and os.path.isfile(stamp_str):
                    self.stamps[name] = {
                        'name': name,
                        'type': 'image',
                        'color': self.scribble_color,
                    }
                    self.stamps[name]['image'] = Gtk.Image().new_from_file(stamp_str)
                    self.stamps[name]['pixbuf'] = self.stamps[name]['image'].get_pixbuf()
                    w, h = self.stamps[name]['pixbuf'].get_width(), self.stamps[name]['pixbuf'].get_height()
                    if w > iconw or h > iconh:
                        scale = min(iconw/w, iconh/h)
                        self.stamps[name]['image'].set_from_pixbuf(
                            self.stamps[name]['pixbuf'].scale_simple(w*scale, h*scale, GdkPixbuf.InterpType.BILINEAR) )
                elif len(p) == 3:
                    color = Gdk.RGBA()
                    color.parse(p[0])
                    self.stamps[name] = {
                        'name': name,
                        'type': 'text',
                        'color': rgba_to_tuple(color),
                        'font': p[1],
                        'str': p[2],
                    }
                    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, iconw, iconh)
                    cairo_ctx = cairo.Context(surface)
                    cairo_ctx.set_source_rgb(1.0, 1.0, 1.0)
                    cairo_ctx.paint()
                    layout = PangoCairo.create_layout(cairo_ctx)
                    layout.set_text(self.stamps[name]['str'])
                    desc = Pango.FontDescription(self.stamps[name]['font'])
                    layout.set_font_description(desc)
                    cairo_ctx.set_source_rgba(*self.stamps[name]['color'])
                    PangoCairo.update_layout(cairo_ctx, layout)
                    w = layout.get_size()[0] / Pango.SCALE
                    h = layout.get_size()[1] / Pango.SCALE
                    cairo_ctx.move_to((iconw - w) / 2, (iconh - h) / 2)
                    PangoCairo.show_layout(cairo_ctx, layout)
                    self.stamps[name]['surface'] = surface
                    b = surface.get_data()
                    # Convert from BGRA (Cairo) to RGBA (Gdk)
                    for i in range(0, len(b), 4):
                        b[i], b[i+2] = b[i+2],b[i]
                    pixbuf = GdkPixbuf.Pixbuf.new_from_data(b, GdkPixbuf.Colorspace.RGB, True, 8, iconw, iconh, 128)
                    self.stamps[name]['image'] = Gtk.Image.new_from_pixbuf(pixbuf)

    def set_stamp(self, name=None):
        if name not in self.stamps:
            self.stamp = self.stamps[self.stamp_names[0]]
        else:
            self.stamp = self.stamps[name]
        image = self.stamp['image']
        image.show()
        self.buttons["stamp"].set_icon_widget(image)

    def next_stamp(self):
        l=list(self.stamps.values())
        try:
            i=l.index(self.stamp)
            self.set_stamp(l[i + 1]['name'])
        except (ValueError, IndexError):
            self.set_stamp()
        self.redraw_current_slide()

    def stamp_scribble(self, point):
        if self.stamp['type'] == 'image':
            return ["image", self.stamp['color'], self.scribble_width, [point], [[0, 0], [0, 0]], self.stamp['pixbuf']]
        elif self.stamp['type'] == 'text':
            return ["text", self.stamp['color'], self.scribble_width, [point], [[0, 0], [0, 0]],
                                      self.stamp['str'], self.stamp['font'], 0]
        return None

    def set_pen(self, name):
        pen_str = self.config.get('pens', 'pen' + name)
        p = pen_str.split(':')
        if len(p) == 1 or p[1].strip() == "":
            self.scrible_width = 1.0
        else:
            self.scribble_width = float(p[1])
        color = Gdk.RGBA()
        if p[0].strip():
            color.parse(p[0])
            self.scribble_color = rgba_to_tuple(color)
        self.buttons["scribble_alpha"].set_value(color.alpha)
        self.buttons["scribble_width"].set_value(self.width_curve_r(self.scribble_width))
        self.buttons["color_button"].set_rgba(color)
        try:
            pen_num = int(name)
        except ValueError:
            pass

    def copy(self):
        self.clipboard = copy.deepcopy(self.selected)

    def paste(self):
        if self.clipboard:
            # Allows for pasting multiple times
            s = copy.deepcopy(self.clipboard)
            self.scribble_list.extend(s)
            self.add_undo(('a', s))

    def select_all(self):
        self.selected = self.scribble_list[:]
        self.redraw_current_slide()

    def select_none(self):
        self.selected = []
        self.redraw_current_slide()

    def select_toggle(self):
        if self.selected:
            self.selected = []
        else:
            self.selected = self.scribble_list[:]
        self.redraw_current_slide()

    def del_selected(self):
        self.add_undo(('d', self.selected))
        for scribble in self.selected:
            self.scribble_list.remove(scribble)
        self.selected = []
        self.redraw_current_slide()

    def set_pointer(self, point):
        if self.pen_pointer:
            # The event thread might start running a bit too early
            self.pen_pointer[0] = point
            self.redraw_current_slide()

    def track_scribble(self, point, button):
        """ Draw the scribble following the mouse's moves.

        Args:
            point: point on slide where event occured (self.zoom.get_slide_point(widget, event))
            button: button code (event.get_button())

        Returns:
            `bool`: whether the event was consumed
        """
        if self.scribble_drawing:
            if self.pen_pointer is not None:
                self.pen_pointer[0] = point
            if button[0]:
                self.drag_button = button[1]
            if self.drawing_mode == "draw" and self.drag_button == Gdk.BUTTON_PRIMARY:
                # Ignore small movements:
                if self.scribble_list[-1][3] and self.min_distance > 0 and self.min_distance > \
                    (self.scribble_list[-1][3][-1][0] - point[0]) * (self.scribble_list[-1][3][-1][0] - point[0]) + \
                    (self.scribble_list[-1][3][-1][1] - point[1]) * (self.scribble_list[-1][3][-1][1] - point[1]):
                    return False
                if self.scribble_list[-1][3]:
                    add_point_rect_ordered(point, self.scribble_list[-1][4])
                else:
                    self.scribble_list[-1][4]=[list(point),list(point)]
                self.scribble_list[-1][3].append(point)
                self.redraw_current_slide()
            elif self.drawing_mode == "erase" or (
                 self.drawing_mode == "draw" and self.drag_button == Gdk.BUTTON_SECONDARY):
                for scribble in self.scribble_list[:]:
                    if intersects(self.last_del_point, point, scribble):
                        self.add_undo(('d', [scribble]))
                        self.scribble_list.remove(scribble)
                self.last_del_point = point
                self.redraw_current_slide()
            elif self.drawing_mode in ("box", "line", "ellipse"):
                self.scribble_list[-1][3][1] = point
                add_point_rect_ordered(point, self.scribble_list[-1][4])
                self.redraw_current_slide()
            elif self.drawing_mode == "select_t":
                for scribble in self.scribble_list[:]:
                    if scribble not in self.stroke_selected and intersects(self.last_del_point, point, scribble):
                        self.stroke_selected.append(scribble)
                        if scribble in self.selected:
                            self.selected.remove(scribble)
                        else:
                            self.selected.append(scribble)
                self.last_del_point = point
                self.redraw_current_slide()
            elif self.drawing_mode == "select_r":
                self.select_rect[1] = list(point)
                self.selected = []
                for scribble in self.scribble_list[:]:
                    #TODO
                    if scribble[0] == 'segment':
                        for p in scribble[3]:
                            if (self.select_rect[1][0] <= p[0] <= self.select_rect[0][0] or
                                self.select_rect[0][0] <= p[0] <= self.select_rect[1][0]) and (
                                self.select_rect[1][1] <= p[1] <= self.select_rect[0][1] or
                                self.select_rect[0][1] <= p[1] <= self.select_rect[1][1]):
                                self.selected.append(scribble)
                                break
                    if scribble[0] in ("box", "text", "ellipse"):
                        for p in scribble[4]:
                            if (self.select_rect[1][0] <= p[0] <= self.select_rect[0][0] or
                                self.select_rect[0][0] <= p[0] <= self.select_rect[1][0]) and (
                                self.select_rect[1][1] <= p[1] <= self.select_rect[0][1] or
                                self.select_rect[0][1] <= p[1] <= self.select_rect[1][1]):
                                self.selected.append(scribble)
                                break
                self.redraw_current_slide()
            elif self.drawing_mode == "move":
                dx = point[0] - self.last_del_point[0]
                dy = point[1] - self.last_del_point[1]
                if dx == dy == 0:
                    return False
                self.last_del_point = point
                self.undo_stack[-1][2] = point[0] - self.move_from[0]
                self.undo_stack[-1][3] = point[1] - self.move_from[1]
                adjust_scribbles(self.selected, dx, dy)
                adjust_points(self.select_rect, dx, dy)
                self.redraw_current_slide()
        else:
            if self.drawing_mode == "stamp":
                self.stamp_point = point

        return False


    def toggle_scribble(self, widget, e_type, point, button, always=False, state=0):
        """ Start/stop drawing scribbles.

        Args:
            e_type: Gdk.event type (event.get_event_type())
            point: point on slide where event occured (self.zoom.get_slide_point(widget, event))
            button: button code (event.get_button())
            always: a boolean allowing scribbling when not in highlight mode

        Returns:
            `bool`: whether the event was consumed
        """
        if not always and not self.drawing_mode:
            # No tool selected.
            # Allow selecting text for edit
            for scribble in self.scribble_list[:]:
                if is_text(scribble) and intersects(point, point, scribble):
                    # Move scribble to end of list
                    self.scribble_list.remove(scribble)
                    self.scribble_list.append(scribble)
                    self.text_entry = self.scribble_list[-1]
                    self.text_pos = len(self.text_entry[5])
                    if scribble[0] == "text":
                        self.enable_text()
                    elif scribble[0] == "latex":
                        self.enable_latex()
                    self.scribble_drawing = True
                    self.redraw_current_slide()
                    return True
            return False

        if e_type == Gdk.EventType.BUTTON_PRESS:
            if self.drawing_mode == "draw" and button[1] == Gdk.BUTTON_PRIMARY:
                self.scribble_list.append(["segment", self.scribble_color, self.scribble_width, [],[]])
                self.add_undo(('a', self.scribble_list[-1]))
            elif self.drawing_mode in ("erase", "select_t") or (
                 self.drawing_mode == "draw" and button[1] == Gdk.BUTTON_SECONDARY):
                self.last_del_point = None
                self.stroke_selected = []
            elif self.drawing_mode == "box":
                fill_color = (0.0, 0.0, 0.0, 0.0) if button[1] == Gdk.BUTTON_SECONDARY else self.fill_color
                color = (0.0, 0.0, 0.0, 0.0) if button[1] == Gdk.BUTTON_MIDDLE else self.scribble_color
                self.scribble_list.append(["box", color, self.scribble_width, [point, point], [list(point), list(point)], fill_color])
                self.add_undo(('a', self.scribble_list[-1]))
            elif self.drawing_mode == "ellipse":
                fill_color = (0.0, 0.0, 0.0, 0.0) if button[1] == Gdk.BUTTON_SECONDARY else self.fill_color
                color = (0.0, 0.0, 0.0, 0.0) if button[1] == Gdk.BUTTON_MIDDLE else self.scribble_color
                self.scribble_list.append(["ellipse", color, self.scribble_width, [point, point], [list(point), list(point)], fill_color])
                self.add_undo(('a', self.scribble_list[-1]))
            elif self.drawing_mode == "line":
                self.scribble_list.append(["segment", self.scribble_color, self.scribble_width, [point, point], [list(point), list(point)]])
                if button[1] == Gdk.BUTTON_SECONDARY:
                    self.scribble_list[-1].append([])
                self.add_undo(('a', self.scribble_list[-1]))
            elif self.drawing_mode == "select_r":
                self.select_rect[0] = list(point)
            elif self.drawing_mode == "move":
                self.move_from = point
                self.last_del_point = point
                self.add_undo(['m', self.selected[:], 0, 0])
            elif self.drawing_mode == "text":
                if state & Gdk.ModifierType.CONTROL_MASK:
                    self.text_alignment = 1 if state & Gdk.ModifierType.SHIFT_MASK else 2
                    alignment = self.text_alignment
                elif state & Gdk.ModifierType.SHIFT_MASK:
                    self.text_alignment = 0
                    alignment = self.text_alignment
                elif button[1] == Gdk.BUTTON_SECONDARY:
                    alignment = 2
                elif button[1] == Gdk.BUTTON_MIDDLE:
                    alignment = 1
                else:
                    alignment = self.text_alignment
                p = list(point)
                p[1] = p[1] - 0.01 # On my screen and default font size, 0.01 aligns the bottom of the cursor with the text's baseline.
                self.scribble_list.append(["text", self.scribble_color, self.scribble_width, [p], [[0, 0], [0, 0]], "", self.scribble_font, alignment])
                self.text_entry = self.scribble_list[-1]
                self.text_pos = len(self.text_entry[5])
                self.add_undo(('a', self.scribble_list[-1]))
            elif self.drawing_mode == "latex":
                p = list(point)
                self.scribble_list.append(["latex", self.scribble_color, self.scribble_width, [p], [[0, 0], [0, 0]], "", None, ""])
                self.text_entry = self.scribble_list[-1]
                self.text_pos = len(self.text_entry[5])
                self.add_undo(('a', self.scribble_list[-1]))
            elif self.drawing_mode == "stamp":
                s = self.stamp_scribble(point)
                if s:
                    self.scribble_list.append(s)
                    self.add_undo(('a', self.scribble_list[-1]))
                    self.redraw_current_slide()
                return True
            self.scribble_drawing = True
            return self.track_scribble(point, button)

        elif e_type == Gdk.EventType.BUTTON_RELEASE:
            if self.drawing_mode == "line" and len(self.scribble_list[-1]) == 6:
                aspect = widget.get_allocated_width() / widget.get_allocated_height() if widget else 1
                line = (self.scribble_list[-1][3][0][0] - self.scribble_list[-1][3][1][0],
                       (self.scribble_list[-1][3][0][1] - self.scribble_list[-1][3][1][1]) / aspect)
                angle = math.atan2(line[1], line[0])
                self.scribble_list[-1][3].append([
                    self.scribble_list[-1][3][1][0] + 0.04 * math.cos(angle + math.pi/6),
                    self.scribble_list[-1][3][1][1] + 0.04 * math.sin(angle + math.pi/6) * aspect])
                self.scribble_list[-1][3].append([
                    self.scribble_list[-1][3][1][0] + 0.04 * math.cos(angle - math.pi/6),
                    self.scribble_list[-1][3][1][1] + 0.04 * math.sin(angle - math.pi/6) * aspect])
                self.scribble_list[-1][3].append([
                    self.scribble_list[-1][3][1][0], self.scribble_list[-1][3][1][1]])
            if self.drawing_mode in ["box", "ellipse"]:
                self.scribble_list[-1][4] = [x[:] for x in self.scribble_list[-1][3]]

            self.scribble_drawing = False
            if self.pen_pointer:
                self.pen_pointer[0] = []
            return True

        return False


    def draw_scribble(self, widget, cairo_context, draw_selected, pw):
        """ Perform the drawings by user.

        Args:
            widget (:class:`~Gtk.DrawingArea`): The widget where to draw the scribbles.
            cairo_context (:class:`~cairo.Context`): The canvas on which to render the drawings
        """
        ww, wh = widget.get_allocated_width(), widget.get_allocated_height()
        pixels_per_point = ww/pw

        cairo_context.set_line_cap(cairo.LINE_CAP_ROUND)

        if draw_selected or self.drawing_mode not in ("select_t", "select_r", "move"):
            scribbles_to_draw = self.scribble_list[:]
        else:
            scribbles_to_draw = [s for s in self.scribble_list if s not in self.selected]

        if self.drawing_mode == 'stamp' and widget is self.p_da_cur:
            s = self.stamp_scribble(self.stamp_point)
            if s:
                scribbles_to_draw.append(s)

        for scribble in scribbles_to_draw:
            stype, color, pwidth, points, rect, *extra = scribble
            width = pwidth * pixels_per_point
            if stype == "segment":
                points = [(p[0] * ww, p[1] * wh) for p in points]

                cairo_context.set_source_rgba(*color)
                cairo_context.set_line_width(width)
                cairo_context.set_dash([])
                if points:
                    cairo_context.move_to(*points[0])

                for p in points[1:]:
                    cairo_context.line_to(*p)
                cairo_context.stroke()
            elif stype == "box":
                points = [(p[0] * ww, p[1] * wh) for p in points]
                fill_color = extra[0] if extra else color
                x0, y0 = points[0]
                x1, y1 = points[1]
                cairo_context.move_to(x0, y0)
                cairo_context.line_to(x0, y1)
                cairo_context.line_to(x1, y1)
                cairo_context.line_to(x1, y0)
                cairo_context.close_path()
                cairo_context.set_source_rgba(*fill_color)
                cairo_context.fill_preserve()
                cairo_context.set_source_rgba(*color)
                cairo_context.set_line_width(width)
                cairo_context.stroke()
            elif stype == "ellipse":
                points = [(p[0] * ww, p[1] * wh) for p in points]
                fill_color = extra[0] if extra else color
                x0, y0 = points[0]
                x1, y1 = points[1]
                if x1 == x0 or y1 == y0:
                    continue
                cairo_context.save()
                mat = cairo_context.get_matrix()
                cairo_context.translate((x1+x0)/2, (y1+y0)/2)
                cairo_context.scale((x1-x0)/2, (y1-y0)/2)
                cairo_context.arc(0, 0, 1, 0, 2*math.pi)
                cairo_context.set_source_rgba(*fill_color)
                cairo_context.fill_preserve()
                cairo_context.set_source_rgba(*color)
                cairo_context.set_matrix(mat)
                cairo_context.set_line_width(2 * width / min(abs(x1-x0), abs(y1-y0)))
                cairo_context.set_line_width(width)
                cairo_context.stroke()
                cairo_context.restore()
            elif stype == "text" or (stype == "latex" and widget is self.p_da_cur):
                # This is really messed up.
                # There are currently three modes: latex and text differentiated by stype.
                # Markup mode is when text starts with '\0'.
                # The actual text is always displayed on presenter, but on content it is replaced by rendered latex, or pango markup.
                # Perhaps it is better to separate to three different paths (or more).
                layout = PangoCairo.create_layout(cairo_context)
                PangoCairo.context_set_resolution(layout.get_context(), 72 * pixels_per_point)
                font = extra[1] if stype == "text" else "Roboto Mono Bold 12"
                layout.set_font_description(Pango.FontDescription(font))
                if extra[0] and extra[0][0] == '\0':
                    markup = True
                    text = extra[0][1:]
                    if self.text_entry:
                        text_pos = max(0, self.text_pos - 1)
                else:
                    markup = False
                    text = extra[0]
                    if self.text_entry:
                        text_pos = self.text_pos
                if self.text_entry is scribble and widget is self.c_da:
                    # Don't show tex shortcut
                    i = text.rfind('\\', 0, -1)
                    if i > -1 and (i == len(text) - 1 or (i < len(text) - 1 and text[i+1].isalnum())):
                        text = text[:i]
                if markup:
                    if widget is self.c_da:
                        layout.set_markup(text)
                    else:
                        layout.set_text(text)
                else:
                    layout.set_text(text)
                if stype == "text":
                    cairo_context.set_source_rgba(*color)
                else:
                    cairo_context.set_source_rgba(0.5,0.5,0.5,0.5)
                if rect == [[0, 0], [0, 0]] and stype == "text":
                    _, ext = layout.get_extents()
                    rect[0] = [points[0][0] + ext.x / ww / Pango.SCALE, points[0][1] + ext.y / wh / Pango.SCALE]
                    rect[1][0] = rect[0][0] + ext.width / ww / Pango.SCALE
                    rect[1][1] = rect[0][1] + ext.height / wh / Pango.SCALE
                    if rect[0][0] > rect[1][0]:
                        rect[0][0], rect[1][0] = rect[1][0], rect[0][0]
                    if rect[0][1] > rect[1][1]:
                        rect[0][1], rect[1][1] = rect[1][1], rect[0][1]

                    if extra[2] == 2:
                        x = (2 * points[0][0] - rect[1][0])
                    elif extra[2] == 1:
                        x = (1.5 * points[0][0] - 0.5 * rect[1][0])
                    else:
                        x = points[0][0]
                    dx = x - points[0][0]
                    rect[0][0] += dx
                    rect[1][0] += dx

                x = rect[0][0] * ww if stype == "text" else points[0][0] * ww
                y = points[0][1] * wh if stype == "text" else points[0][1] * wh - 16
                cairo_context.move_to(x, y)
                PangoCairo.update_layout(cairo_context, layout)
                PangoCairo.show_layout(cairo_context, layout)

                if self.text_entry == scribble and widget is self.p_da_cur and self.draw_blink:
                    cursor = layout.get_cursor_pos(len(bytearray(extra[0][:text_pos],"utf8")))
                    cur_x = x + cursor.strong_pos.x / Pango.SCALE
                    cur_y = y + cursor.strong_pos.y / Pango.SCALE
                    cur_y1 = cur_y + cursor.strong_pos.height / Pango.SCALE
                    cairo_context.move_to(cur_x, cur_y)
                    cairo_context.line_to(cur_x, cur_y1)
                    cairo_context.set_source_rgba(*color)
                    cairo_context.set_line_width(2)
                    cairo_context.stroke()

                if self.show_text_frames and widget is self.p_da_cur:
                    # For debugging - frame
                    points = [(p[0] * ww, p[1] * wh) for p in rect]
                    x0, y0 = points[0]
                    x1, y1 = points[1]
                    cairo_context.move_to(x0, y0)
                    cairo_context.line_to(x0, y1)
                    cairo_context.line_to(x1, y1)
                    cairo_context.line_to(x1, y0)
                    cairo_context.close_path()
                    cairo_context.set_source_rgba(0,0,0,0.5)
                    cairo_context.set_line_width(1)
                    cairo_context.set_dash([4,2])
                    cairo_context.stroke()
            if stype in ["image", "latex"]:
                pixbuf = extra[0] if stype == "image" else extra[1]
                if not pixbuf and stype == "latex" and extra[0] != extra[2]:
                    scribble[6] = self.latex_to_pixbuf(extra[0], 6 * self.font_size, self.scribble_color)
                    scribble[7] = scribble[5]
                    pixbuf = scribble[6]
                if pixbuf:
                    if rect != [[0, 0], [0, 0]] and widget is self.p_da_cur:
                        w = int((rect[1][0] - rect[0][0]) * ww)
                        h = int((rect[1][1] - rect[0][1]) * wh)
                        pixbuf = pixbuf.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
                        if self.text_entry == scribble:
                            pixbuf = pixbuf.add_alpha(True, 255, 255, 255)
                    w, h = pixbuf.get_width(), pixbuf.get_height()
                    x, y = int(points[0][0]*ww), int(points[0][1]*wh)
                    cairo_context.rectangle(x, y, w, h)
                    Gdk.cairo_set_source_pixbuf(cairo_context, pixbuf, x, y)
                    cairo_context.paint()
                    cairo_context.reset_clip()
                    cairo_context.new_path()
                    if rect == [[0, 0], [0, 0]] and widget is self.c_da:
                        rect[0] = [points[0][0], points[0][1]]
                        rect[1] = [rect[0][0] + w/ww, rect[0][1] + h/wh]

        if widget is self.p_da_cur and self.select_rect[1]:
                points = [(p[0] * ww, p[1] * wh) for p in self.select_rect]
                x0, y0 = points[0]
                x1, y1 = points[1]
                cairo_context.move_to(x0, y0)
                cairo_context.line_to(x0, y1)
                cairo_context.line_to(x1, y1)
                cairo_context.line_to(x1, y0)
                cairo_context.close_path()
                cairo_context.set_source_rgba(0.3,0.3,0.3,0.8)
                cairo_context.set_line_width(2)
                cairo_context.set_dash([5,5,5])
                cairo_context.stroke()

    def update_font(self, widget):
        if widget.get_font():
            if self.text_entry and self.scribble_list and self.scribble_list[-1][0] == "text":
                # TODO: undo
                self.scribble_list[-1][6] = widget.get_font()
                self.scribble_list[-1][4] = [[0,0],[0,0]]

            if self.selected:
                # TODO: undo
                for s in self.selected:
                    if s[0] == "text":
                        s[6] = widget.get_font()
                        s[4] = [[0,0],[0,0]]
            self.scribble_font = widget.get_font()
            i = rfind(self.scribble_font, ' ')
            try:
               self.font_size = int(self.scribble_font[i:])
            except:
                self.font_size = 16
            widget.get_children()[0].get_children()[0].set_label("A")
            widget.set_use_size(widget.get_font_size() < 24576)


    def update_color(self, widget):
        """ Callback for the color chooser button, to set scribbling color.

        Args:
            widget (:class:`~Gtk.ColorButton`):  the clicked button to trigger this event, if any
        """
        color = widget.get_rgba()

        if self.selected:
            self.add_undo(('c', [[s, s[1], rgba_to_tuple(color)] for s in self.selected]))
            for s in self.selected:
                s[1] = rgba_to_tuple(color)
            widget.set_rgba(Gdk.RGBA(*self.scribble_color))
            return
        elif self.text_entry and self.scribble_list and self.scribble_list[-1][0] in ["text", "latex"]:
            self.scribble_list[-1][1] = rgba_to_tuple(color)
            self.scribble_list[-1][6] = None
            self.scribble_list[-1][7] = ''

        self.scribble_color = rgba_to_tuple(color)
        self.buttons["scribble_alpha"].set_value(color.alpha)
        self.config.set('scribble', 'color', color.to_string())

    def update_fill_color(self, widget):
        """ Callback for the color chooser button, to set scribbling color.

        Args:
            widget (:class:`~Gtk.ColorButton`):  the clicked button to trigger this event, if any
        """
        color = widget.get_rgba()

        if self.selected:
            self.add_undo(('cf', [[s, s[5], rgba_to_tuple(color)] for s in self.selected if has_fill(s)]))
            for s in self.selected:
                if has_fill(s):
                    s[5] = rgba_to_tuple(color)
            widget.set_rgba(*self.fill_color)
            return
        self.fill_color = rgba_to_tuple(color)
        self.buttons["fill_alpha"].set_value(color.alpha)
        self.config.set('scribble', 'fill_color', color.to_string())

    def update_alpha(self, widget, event, value):
        """ Callback for the alpha slider

        Args:
            widget (:class:`~Gtk.Scale`): The slider control used to select alpha channel value
            event (:class:`~Gdk.Event`):  the GTK event triggering this update.
            value (`int`): the width of the scribbles to be drawn
        """
        # It seems that values returned are not necessarily in range
        alpha = min(max(0, int(value * 100) / 100), 1)
        rgba = Gdk.RGBA(self.scribble_color[0], self.scribble_color[1], self.scribble_color[2], alpha)
        if self.selected:
            self.add_undo(('p', [[s, s[1].alpha, alpha] for s in self.selected]), True)
            for s in self.selected:
                s[1] = (s[1][0], s[1][1], s[1][2], alpha)
        else:
            self.scribble_color = rgba_to_tuple(rgba)
            self.buttons["color_button"].set_rgba(rgba)
            self.config.set('scribble', 'rgba', rgba.to_string())

    def update_fill_alpha(self, widget, event, value):
        """ Callback for the fill alpha slider

        Args:
            widget (:class:`~Gtk.Scale`): The slider control used to select alpha channel value
            event (:class:`~Gdk.Event`):  the GTK event triggering this update.
            value (`int`): the width of the scribbles to be drawn
        """
        # It seems that values returned are not necessarily in range
        alpha = min(max(0, int(value * 100) / 100), 1)
        rgba = Gdk.RGBA(self.fill_color[0], self.fill_color[1], self.fill_color[2], alpha)
        if self.selected:
            selected = [x for x in self.selected if has_fill(x)]
            if selected:
                self.add_undo(('pf', [[s, s[5].alpha, alpha] for s in selected]), True)
                for s in selected:
                    s[5] = (s[5][0], s[5][1], s[5][2], alpha)
        else:
            self.fill_color = rgba_to_tuple(rgba)
            self.buttons["fill_color_button"].set_rgba(rgba)
            self.config.set('scribble', 'rgba', rgba.to_string())

    def update_width(self, widget, event, value):
        """ Callback for the width chooser slider, to set scribbling width.

        Args:
            widget (:class:`~Gtk.Scale`): The slider control used to select the scribble width
            event (:class:`~Gdk.Event`):  the GTK event triggering this update.
            value (`int`): the width of the scribbles to be drawn
        """
        value = max(0, min(299, value))
        width = self.width_curve(value)
        if self.selected:
            self.add_undo(('w', [[s, s[2], width] for s in self.selected]), True)
            for s in self.selected:
                s[2] = width
        else:
            self.scribble_width = width
            self.config.set('scribble', 'width', str(self.scribble_width))

    def clean_scribble_list(self, scribbles):
        to_del = []
        for i in range(len(scribbles)):
            s = scribbles[i]
            if s[0] == 'segment':
                if len(s[3]) < 2:
                    to_del.append(i)
            elif s[0] == 'text':
                if not s[5]:
                    to_del.append(i)
        for i in sorted(to_del, reverse=True):
            del scribbles[i]

    def clear_scribble(self, *args, **kwargs):
        """ Callback for the scribble clear button, to remove all scribbles.
        """
        if 'page' in kwargs:
            # Clearing due to moving to a new page
            self.undo_stack = []
            self.undo_stack_pos = 0
            self.buttons["undo"].set_sensitive(False)
            self.buttons["redo"].set_sensitive(False)
            self.text_entry = False
        else:
            self.add_undo(('X', self.scribble_list[:]))

        del self.scribble_list[:]
        self.selected = []

        self.redraw_current_slide()


    def on_configure_da(self, widget, event):
        """ Transfer configure resize to the cache.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget which has been resized
            event (:class:`~Gdk.Event`):  the GTK event, which contains the new dimensions of the widget
        """
        # Don't trust those
        if not event.send_event:
            return

        self.resize_cache(widget.get_name(), event.width, event.height)


    def enable_scribbling(self):
        """ Enable the scribbling mode.

        Returns:
            `bool`: whether it was possible to enable (thus if it was not enabled already)
        """
        self.enable_draw()

        return True


    def disable_scribbling(self):
        """ Disable the scribbling mode.

        Returns:
            `bool`: whether it was possible to disable (thus if it was not disabled already)
        """
        if not self.drawing_mode:
            return False

        self.drawing_mode = None
        self.text_entry = False
        self.selected = []
        self.select_rect = [[],[]]
        self.show_button("")
        self.pen_pointer_p = Gdk.Cursor.new_for_display(Gdk.Display.get_default(), Gdk.CursorType.X_CURSOR).get_image()

        self.redraw_current_slide()
        extras.Cursor.set_cursor(self.p_central)

        return True

    def show_button(self, button):
        for b in self.buttons:
            opacity = 0.2 if b == button else 1
            self.buttons[b].set_opacity(opacity)

    def enable_tool(self, tool, *args):
        pointer_dict = {
            "erase": Gdk.CursorType.CENTER_PTR,
            "draw": Gdk.CursorType.PENCIL,
            "box": Gdk.CursorType.DOTBOX,
            "line": Gdk.CursorType.DRAFT_SMALL,
            "text":  Gdk.CursorType.XTERM,
            "ellipse": Gdk.CursorType.CIRCLE,
            "stamp": Gdk.CursorType.BLANK_CURSOR,
            "select_t": Gdk.CursorType.HAND1,
            "latex":  Gdk.CursorType.XTERM,
        }
        self.drawing_mode = tool
        self.show_button(tool)
        if tool != "select_t":
            self.selected = []
        self.select_rect = [[],[]]
        if tool not in ["text", "latex"]:
            self.text_entry = False
        self.pen_pointer_p = Gdk.Cursor.new_for_display(Gdk.Display.get_default(), pointer_dict[tool]).get_image()
        return True

    def enable_erase(self, *args):
        return self.enable_tool("erase")

    def enable_draw(self, *args):
        return self.enable_tool("draw")

    def enable_ellipse(self, *args):
        return self.enable_tool("ellipse")

    def enable_box(self, *args):
        return self.enable_tool("box")

    def enable_line(self, *args):
        return self.enable_tool("line")

    def enable_text(self, *args):
        return self.enable_tool("text")

    def enable_latex(self, *args):
        return self.enable_tool("latex")

    def enable_stamp(self, *args):
        if not self.stamps:
            return True
        if not self.stamp:
            self.set_stamp()
        return self.enable_tool("stamp")

    def enable_select_touch(self, *args):
        return self.enable_tool("select_t")

    def enable_select_rect(self, *args):
        self.drawing_mode = "select_r"
        self.show_button("select_r")
        self.text_entry = False
        self.pen_pointer_p = Gdk.Cursor.new_for_display(Gdk.Display.get_default(), Gdk.CursorType.CROSSHAIR).get_image()
        return True

    def next_tool(self, *args):
        tools = [None, "draw", "erase", "line", "box", "ellipse", "text", "latex", "stamp"]
        try:
            i = (tools.index(self.drawing_mode) + 1) % len(tools)
        except ValueError:
            i = 0
        if i == 0:
            self.disable_scribbling()
        else:
            method = getattr(self, "enable_" + tools[i])
            method()

    def enable_move(self, *args):
        if self.selected:
            self.drawing_mode = "move"
            self.show_button("move")
            self.select_rect = [list(self.selected[0][3][0]),list(self.selected[0][3][0])]
            for i in self.selected:
                pts = i[4]
                for p in pts:
                    add_point_rect_ordered(p, self.select_rect)
            self.pen_pointer_p = Gdk.Cursor.new_for_display(Gdk.Display.get_default(), Gdk.CursorType.FLEUR).get_image()
        return True

    def add_undo(self, operation, update=False):
        if self.undo_stack_pos < len(self.undo_stack):
            del self.undo_stack[self.undo_stack_pos:]
        if update and self.undo_stack:
            if self.undo_stack[-1][0] == operation[0] == 'w':
                if [x[0] for x in self.undo_stack[-1][1]] == [x[0] for x in operation[1]]:
                    for s in range(len(operation[1])):
                        operation[1][s][1] = self.undo_stack[-1][1][s][1]
                self.undo_stack.pop()
                self.undo_stack_pos = self.undo_stack_pos - 1
            if self.undo_stack[-1][0] == operation[0] == 'p':
                if [x[0] for x in self.undo_stack[-1][1]] == [x[0] for x in operation[1]]:
                    for s in range(len(operation[1])):
                        operation[1][s][1] = self.undo_stack[-1][1][s][1]
                self.undo_stack.pop()
                self.undo_stack_pos = self.undo_stack_pos - 1
        self.undo_stack.append(operation)
        self.undo_stack_pos = self.undo_stack_pos + 1
        self.buttons["redo"].set_sensitive(False)
        self.buttons["undo"].set_sensitive(True)
        return True

    def undo(self, *args):
        if self.undo_stack_pos > 0 and self.undo_stack:
            self.buttons["redo"].set_sensitive(True)
            self.undo_stack_pos = self.undo_stack_pos - 1
            if self.undo_stack_pos == 0:
                self.buttons["undo"].set_sensitive(False)
            op = self.undo_stack[self.undo_stack_pos]
            if op[0] == 'a':
                try:
                    self.scribble_list.remove(op[1])
                except ValueError:
                    pass
            elif op[0] == 'd':
                self.scribble_list.extend(op[1])
            elif op[0] == 'X':
                self.scribble_list.extend(op[1])
            elif op[0] == 'w':
                for s, ow, nw in op[1]:
                    s[2] = ow
            elif op[0] == 'c':
                for s, oc, nc in op[1]:
                    s[1] = oc
            elif op[0] == 'cf':
                for s, oc, nc in op[1]:
                    s[5] = oc
            elif op[0] == 'm':
                adjust_scribbles(op[1], -op[2], -op[3])

            self.redraw_current_slide()
        return True

    def redo(self, *args):
        if self.undo_stack and self.undo_stack_pos < len(self.undo_stack):
            op = self.undo_stack[self.undo_stack_pos]
            if op[0] == 'a':
                self.scribble_list.append(op[1])
            elif op[0] == 'd':
                for i in op[1]:
                    try:
                        self.scribble_list.remove(i)
                    except ValueError:
                        pass
            elif op[0] == 'X':
                self.scribble_list = []
                self.selected = []
            elif op[0] == 'w':
                for s, ow, nw in op[1]:
                    s[2] = nw
            elif op[0] == 'c':
                for s, oc, nc in op[1]:
                    s[1] = nc
            elif op[0] == 'cf':
                for s, oc, nc in op[1]:
                    s[5] = nc
            elif op[0] == 'm':
                adjust_scribbles(op[1], op[2], op[3])
            self.undo_stack_pos = self.undo_stack_pos + 1
            if self.undo_stack_pos == len(self.undo_stack):
                self.buttons["redo"].set_sensitive(False)
            self.buttons["undo"].set_sensitive(True)

            self.redraw_current_slide()
        return True

    def width_curve_r(self, value):
        return int(math.ceil(math.sqrt(value * 2990 - 299)))

    def width_curve(self, value):
        return ((value * value // 299) + 1) / 10
