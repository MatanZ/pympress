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

import gi
import cairo
gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gdk, Pango, PangoCairo, GLib

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
    elif scribble[0] in ("box", "text"):
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
        if s[0] in ("segment", "box", "text"):
            adjust_points(s[4], dx, dy)

class Scribbler(builder.Builder):
    """ UI that allows to draw free-hand on top of the current slide.

    Args:
        config (:class:`~pympress.config.Config`): A config object containing preferences
        builder (:class:`~pympress.builder.Builder`): A builder from which to load widgets
        notes_mode (`bool`): The current notes mode, i.e. whether we display the notes on second slide
    """
    #: `list` of scribbles to be drawn, as tuples of color :class:`~Gdk.RGBA`, width `int`, and a `list` of points.
    scribble_list = []
    #: Whether the current mouse movements are drawing strokes or should be ignored
    scribble_drawing = False
    #: :class:`~Gdk.RGBA` current color of the scribbling tool
    scribble_color = Gdk.RGBA()
    #: `int` current stroke width of the scribbling tool
    scribble_width = 1
    #: :class:`~Gdk.RGBA` current fill color of the scribbling tool
    fill_color = Gdk.RGBA()

    #: :class:`~Gtk.EventBox` for the scribbling in the Content window, captures freehand drawing
    scribble_c_eb = None
    #: :class:`~Gtk.EventBox` for the scribbling in the Presenter window, captures freehand drawing
    scribble_p_eb = None

    #: :class:`~Gtk.Box` in the Presenter window, where we insert scribbling.
    p_central = None
    p_da_cur = None

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

    min_distance = 0

    laser = None

    scribble_font = "serif 16"
    text_entry = False
    draw_blink = True
    text_alignment = 0
    show_text_frames = True
    latex_dict = {}
    latex_prefixes = set()

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

        self.scribble_color = Gdk.RGBA()
        self.scribble_color.parse(config.get('scribble', 'color'))
        self.scribble_width = config.getfloat('scribble', 'width')
        self.fill_color.parse(config.get('scribble', 'fill_color'))

        self.config = config

        self.pen_event = evdev_pad.PenEventLoop(self)
        if self.pen_event.pen_thread:
            self.pen_pointer = builder.pen_pointer
        else:
            self.pen_event = None
        self.min_distance = builder.min_distance

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
        self.toggle_scribble(*data, always=True)
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
        elif command == 'box':
            self.enable_box()
        elif command == 'line':
            self.enable_line()
        elif command == 'text':
            self.enable_text(ctrl_pressed)
        elif command == 'select_t':
            self.enable_select_touch()
        elif command == 'select_r':
            self.enable_select_rect()
        elif command == 'cancel':
            self.disable_scribbling()
        elif command == 'pen':
            self.set_pen(name)
        elif command == 'fill_copy':
            self.fill_color = self.scribble_color.copy()
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
        elif command == 'BTN_0':
            # Next pen
            self.pen_num = self.pen_num % 8 + 1
            self.set_pen(str(self.pen_num))
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
        if not self.text_entry or not self.scribble_list or self.scribble_list[-1][0] != "text":
            return
        if val in (Gdk.KEY_Return, Gdk.KEY_Escape):
            self.text_entry = False
        elif val == Gdk.KEY_BackSpace:
            self.scribble_list[-1][5] = self.scribble_list[-1][5][:-1]
            self.scribble_list[-1][4] = [[0, 0], [0, 0]]
        elif 31 < val < 65280 and s and not state & Gdk.ModifierType.CONTROL_MASK:
            self.scribble_list[-1][5] = self.scribble_list[-1][5] + s
            i = self.scribble_list[-1][5].rfind('\\', 0, -1)
            if i > -1 and self.scribble_list[-1][5][i+1:] in self.latex_dict:
                if self.scribble_list[-1][5][i+1:] not in self.latex_prefixes:
                    self.scribble_list[-1][5] = self.scribble_list[-1][5][:i] + self.latex_dict[self.scribble_list[-1][5][i+1:]]
            elif i > -1 and not self.scribble_list[-1][5][-1].isalpha() and self.scribble_list[-1][5][i+1:-1] in self.latex_dict:
                    self.scribble_list[-1][5] = self.scribble_list[-1][5][:i] + self.latex_dict[self.scribble_list[-1][5][i+1:-1]] + self.scribble_list[-1][5][-1]
            self.scribble_list[-1][4] = [[0, 0], [0, 0]]
        else:
            logger.debug(f"unknown key, {val=}, {s=}")
        self.redraw_current_slide()

    def set_pen(self, name):
        pen_str = self.config.get('pens', 'pen' + name)
        p = pen_str.split(':')
        if len(p) == 1 or p[1].strip() == "":
            self.scrible_width = 1.0
        else:
            self.scribble_width = float(p[1])
        self.scribble_color = Gdk.RGBA()
        if p[0].strip():
            self.scribble_color.parse(p[0])
        self.buttons["scribble_alpha"].set_value(self.scribble_color.alpha)
        self.buttons["scribble_width"].set_value(self.width_curve_r(self.scribble_width))
        self.buttons["color_button"].set_rgba(self.scribble_color)
        try:
            pen_num = int(name)
        except ValueError:
            pass

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
            if self.drawing_mode == "scribble" and self.drag_button == Gdk.BUTTON_PRIMARY:
                # Ignore small movements:
                if self.scribble_list[-1][3] and self.min_distance > 0 and self.min_distance > \
                    (self.scribble_list[-1][3][-1][0] - point[0]) * (self.scribble_list[-1][3][-1][0] - point[0]) + \
                    (self.scribble_list[-1][3][-1][1] - point[1]) * (self.scribble_list[-1][3][-1][1] - point[1]):
                    return True
                if self.scribble_list[-1][3]:
                    add_point_rect_ordered(point, self.scribble_list[-1][4])
                else:
                    self.scribble_list[-1][4]=[list(point),list(point)]
                self.scribble_list[-1][3].append(point)
                self.redraw_current_slide()
                return True
            elif self.drawing_mode == "erase" or (
                 self.drawing_mode == "scribble" and self.drag_button == Gdk.BUTTON_SECONDARY):
                for scribble in self.scribble_list[:]:
                    if intersects(self.last_del_point, point, scribble):
                        self.add_undo(('d', [scribble]))
                        self.scribble_list.remove(scribble)
                self.last_del_point = point
                self.redraw_current_slide()
                return True
            elif self.drawing_mode in ("box", "line"):
                self.scribble_list[-1][3][1] = point
                add_point_rect_ordered(point, self.scribble_list[-1][4])
                self.redraw_current_slide()
                return True
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
                return True
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
                    if scribble[0] in ("box", "text"):
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
                    return True
                self.last_del_point = point
                self.undo_stack[-1][2] = point[0] - self.move_from[0]
                self.undo_stack[-1][3] = point[1] - self.move_from[1]
                adjust_scribbles(self.selected, dx, dy)
                adjust_points(self.select_rect, dx, dy)
                self.redraw_current_slide()

        return False


    def toggle_scribble(self, e_type, point, button, always=False, state=0):
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
            return False

        if e_type == Gdk.EventType.BUTTON_PRESS:
            if self.drawing_mode == "scribble" and button[1] == Gdk.BUTTON_PRIMARY:
                self.scribble_list.append(["segment", self.scribble_color, self.scribble_width, [],[]])
                self.add_undo(('a', self.scribble_list[-1]))
            elif self.drawing_mode in ("erase", "select_t") or (
                 self.drawing_mode == "scribble" and button[1] == Gdk.BUTTON_SECONDARY):
                self.last_del_point = None
                self.stroke_selected = []
            elif self.drawing_mode == "box":
                self.scribble_list.append(["box", self.scribble_color, self.scribble_width, [point, point], [list(point), list(point)], self.fill_color])
                self.add_undo(('a', self.scribble_list[-1]))
            elif self.drawing_mode == "line":
                self.scribble_list.append(["segment", self.scribble_color, self.scribble_width, [point, point], [list(point), list(point)]])
                self.add_undo(('a', self.scribble_list[-1]))
            elif self.drawing_mode == "select_r":
                self.select_rect[0] = list(point)
            elif self.drawing_mode == "move":
                self.move_from = point
                self.last_del_point = point
                self.add_undo(['m', self.selected[:], 0, 0])
            elif self.drawing_mode == "text":
                if state & Gdk.ModifierType.SHIFT_MASK:
                    self.text_alignment = 0
                if state & Gdk.ModifierType.CONTROL_MASK:
                    self.text_alignment = 1 if state & Gdk.ModifierType.SHIFT_MASK else 2
                self.scribble_list.append(["text", self.scribble_color, self.scribble_width, [point], [[0, 0], [0, 0]], "", self.scribble_font, self.text_alignment])
                self.text_entry = self.scribble_list[-1]
                self.add_undo(('a', self.scribble_list[-1]))
            self.scribble_drawing = True
            return self.track_scribble(point, button)

        elif e_type == Gdk.EventType.BUTTON_RELEASE:
            self.scribble_drawing = False
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

        if draw_selected:
            scribbles_to_draw = self.scribble_list
        else:
            scribbles_to_draw = (s for s in self.scribble_list if s not in self.selected)

        for scribble in scribbles_to_draw:
            stype, color, pwidth, points, rect, *extra = scribble
            width = pwidth * pixels_per_point
            if stype == "segment":
                points = [(p[0] * ww, p[1] * wh) for p in points]

                cairo_context.set_source_rgba(*color)
                cairo_context.set_line_width(width)
                if points:
                    cairo_context.move_to(*points[0])

                for p in points[1:]:
                    cairo_context.line_to(*p)
                cairo_context.stroke()
            if stype == "box":
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
            if stype == "text":
                layout = PangoCairo.create_layout(cairo_context)
                PangoCairo.context_set_resolution(layout.get_context(), 72 * pixels_per_point)
                font=extra[1]
                layout.set_font_description(Pango.FontDescription(font))
                layout.set_text(extra[0], len(bytearray(extra[0],"utf8")))
                cairo_context.set_source_rgba(*color)
                if rect == [[0, 0], [0, 0]]:
                    _, ext = layout.get_extents()
                    rect[0] = [ points[0][0] + ext.x / ww / Pango.SCALE, points[0][1] + ext.y / wh / Pango.SCALE ]
                    rect[1][0] = rect[0][0] + ext.width / ww / Pango.SCALE
                    rect[1][1] = rect[0][1] + ext.height / wh / Pango.SCALE
                if extra[2] == 2:
                    x = (2 * points[0][0] - rect[1][0]) * ww
                elif extra[2] == 1:
                    x = (1.5 * points[0][0] - 0.5 * rect[1][0]) * ww
                else:
                    x = points[0][0] * ww
                cairo_context.move_to(x, points[0][1] * wh)
                PangoCairo.update_layout(cairo_context, layout)
                PangoCairo.show_layout(cairo_context, layout)

                if self.text_entry == scribble and widget is self.p_da_cur and self.draw_blink:
                    cursor = layout.get_cursor_pos(len(bytearray(extra[0],"utf8")))
                    cur_x = x + cursor.strong_pos.x / Pango.SCALE
                    cur_y = points[0][1] * wh + cursor.strong_pos.y / Pango.SCALE
                    cur_y1 = cur_y + cursor.strong_pos.height / Pango.SCALE
                    cairo_context.move_to(cur_x, cur_y)
                    cairo_context.line_to(cur_x, cur_y1)
                    cairo_context.set_source_rgba(*color)
                    cairo_context.set_line_width(2)
                    cairo_context.stroke()

                if self.show_text_frames:
                    # For debugging - frame
                    points = [(p[0] * ww, p[1] * wh) for p in rect]
                    x0, y0 = points[0]
                    x1, y1 = points[1]
                    x0 = x0 + x - points[0][0]
                    x1 = x1 + x - points[0][0]
                    cairo_context.move_to(x0, y0)
                    cairo_context.line_to(x0, y1)
                    cairo_context.line_to(x1, y1)
                    cairo_context.line_to(x1, y0)
                    cairo_context.close_path()
                    cairo_context.set_source_rgba(0,0,0,0.5)
                    cairo_context.set_line_width(1)
                    cairo_context.set_dash([4,2])
                    cairo_context.stroke()
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
                self.scribble_list[-1][6] = widget.get_font()
            self.scribble_font = widget.get_font()
            widget.get_children()[0].get_children()[0].set_label("A")
            widget.set_use_size(widget.get_font_size() < 24576)


    def update_color(self, widget):
        """ Callback for the color chooser button, to set scribbling color.

        Args:
            widget (:class:`~Gtk.ColorButton`):  the clicked button to trigger this event, if any
        """
        color = widget.get_rgba()
        if self.selected:
            self.add_undo(('c', [[s, s[1], color] for s in self.selected]))
            for s in self.selected:
                s[1] = color
        else:
            self.scribble_color = color
            self.buttons["scribble_alpha"].set_value(self.scribble_color.alpha)
            self.config.set('scribble', 'color', self.scribble_color.to_string())

    def update_alpha(self, widget, event, value):
        """ Callback for the alpha slider

        Args:
            widget (:class:`~Gtk.Scale`): The slider control used to select alpha channel value
            event (:class:`~Gdk.Event`):  the GTK event triggering this update.
            value (`int`): the width of the scribbles to be drawn
        """
        # It seems that values returned are not necessarily in range
        alpha = min(max(0, int(value * 100) / 100), 1)
        rgba = Gdk.RGBA(self.scribble_color.red, self.scribble_color.green, self.scribble_color.blue, alpha)
        if self.selected:
            self.add_undo(('p', [[s, s[1].alpha, alpha] for s in self.selected]), True)
            for s in self.selected:
                s[1] = Gdk.RGBA(s[1].red, s[1].green, s[1].blue, alpha)
        else:
            self.scribble_color = rgba
            self.buttons["color_button"].set_rgba(rgba)
            self.config.set('scribble', 'rgba', self.scribble_color.to_string())

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
        for i in to_del:
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
            self.key_entry = False
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
        self.show_button("")
        self.pen_pointer_p = Gdk.Cursor(Gdk.CursorType.X_CURSOR).get_image()

        self.p_central.queue_draw()
        extras.Cursor.set_cursor(self.p_central)

        return True

    def show_button(self, button):
        for b in self.buttons:
            opacity = 0.2 if b == button else 1
            self.buttons[b].set_opacity(opacity)

    def enable_erase(self, *args):
        self.drawing_mode = "erase"
        self.show_button("erase")
        self.selected = []
        self.select_rect = [[],[]]
        self.text_entry = False
        self.pen_pointer_p = Gdk.Cursor(Gdk.CursorType.CENTER_PTR).get_image()
        return True

    def enable_draw(self, *args):
        self.drawing_mode = "scribble"
        self.show_button("draw")
        self.selected = []
        self.select_rect = [[],[]]
        self.text_entry = False
        self.pen_pointer_p = Gdk.Cursor(Gdk.CursorType.PENCIL).get_image()
        return True

    def enable_box(self, *args):
        self.drawing_mode = "box"
        self.show_button("box")
        self.selected = []
        self.select_rect = [[],[]]
        self.text_entry = False
        self.pen_pointer_p = Gdk.Cursor(Gdk.CursorType.DOTBOX).get_image()
        return True

    def enable_line(self, *args):
        self.drawing_mode = "line"
        self.show_button("line")
        self.selected = []
        self.select_rect = [[],[]]
        self.text_entry = False
        self.pen_pointer_p = Gdk.Cursor(Gdk.CursorType.DRAFT_SMALL).get_image()
        return True

    def enable_text(self, *args):
        self.drawing_mode = "text"
        self.show_button("text")
        self.selected = []
        self.select_rect = [[],[]]
        self.pen_pointer_p = Gdk.Cursor(Gdk.CursorType.XTERM).get_image()
        return True

    def enable_select_touch(self, *args):
        self.drawing_mode = "select_t"
        self.show_button("select_t")
        self.select_rect = [[],[]]
        self.text_entry = False
        self.pen_pointer_p = Gdk.Cursor(Gdk.CursorType.HAND1).get_image()
        return True

    def enable_select_rect(self, *args):
        self.drawing_mode = "select_r"
        self.show_button("select_r")
        self.text_entry = False
        self.pen_pointer_p = Gdk.Cursor(Gdk.CursorType.CROSSHAIR).get_image()
        return True

    def enable_move(self, *args):
        if self.selected:
            self.drawing_mode = "move"
            self.show_button("move")
            self.select_rect = [self.selected[0][3][0][:],self.selected[0][3][0][:]]
            for i in self.selected:
                pts = i[4] if i[0] == "segment" else i[3]
                for p in pts:
                    add_point_rect_ordered(p, self.select_rect)
            self.pen_pointer_p = Gdk.Cursor(Gdk.CursorType.FLEUR).get_image()
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
