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

import gi
import cairo
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk

from pympress import builder, extras, evdev_pad

def ccw(A, B, C):
    """ Returns True if triangle ABC is counter clockwise
    """
    return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])

def segments_intersect(A, B, C, D):
    """ Return true if line segments AB and CD intersect
    """
    return ccw(A, C, D) != ccw(B, C, D) and ccw(A, B, C) != ccw(A, B, D)

class Scribbler(builder.Builder):
    """ UI that allows to draw free-hand on top of the current slide.

    Args:
        config (:class:`~pympress.config.Config`): A config object containing preferences
        builder (:class:`~pympress.builder.Builder`): A builder from which to load widgets
        notes_mode (`bool`): The current notes mode, i.e. whether we display the notes on second slide
    """
    #: Whether we are displaying the interface to scribble on screen and the overlays containing said scribbles
    scribbling_mode = False
    #: `list` of scribbles to be drawn, as tuples of color :class:`~Gdk.RGBA`, width `int`, and a `list` of points.
    scribble_list = []
    #: Whether the current mouse movements are drawing strokes or should be ignored
    scribble_drawing = False
    #: :class:`~Gdk.RGBA` current color of the scribbling tool
    scribble_color = Gdk.RGBA()
    #: `int` current stroke width of the scribbling tool
    scribble_width = 1

    #: :class:`~Gtk.HBox` that replaces normal panes when scribbling is on, contains buttons and scribble drawing area.
    scribble_overlay = None
    #: :class:`~Gtk.DrawingArea` for the scribbles in the Presenter window. Actually redraws the slide.
    scribble_p_da = None
    #: :class:`~Gtk.EventBox` for the scribbling in the Content window, captures freehand drawing
    scribble_c_eb = None
    #: :class:`~Gtk.EventBox` for the scribbling in the Presenter window, captures freehand drawing
    scribble_p_eb = None
    #: :class:`~Gtk.AspectFrame` for the slide in the Presenter's highlight mode
    scribble_p_frame = None

    #: A :class:`~Gtk.OffscreenWindow` where we render the scribbling interface when it's not shown
    off_render = None
    #: :class:`~Gtk.Box` in the Presenter window, where we insert scribbling.
    p_central = None

    #: :class:`~Gtk.CheckMenuItem` that shows whether the scribbling is toggled
    pres_highlight = None
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

    #: callback, to be connected to :func:`~pympress.ui.UI.swap_layout`
    swap_layout = lambda: None
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
    #: Indicates that dragging the cursor erases instead of draws. Requires scribbling_mode
    drawing_mode = None
    #: Save scribbling mode when enabling it for erasing mode, to restore when leaving ersing mode
    before_erasing = None

    #: position of the pen (writing pad) pointer (from UI class)
    pen_pointer = None
    #:
    pen_event = None

    #: Undo stack
    undo_stack = []
    #: Position in undo stack. Allows re-do
    undo_stack_pos = 0

    def __init__(self, config, builder, notes_mode):
        super(Scribbler, self).__init__()

        self.load_ui('highlight')
        builder.load_widgets(self)

        self.on_draw = builder.get_callback_handler('on_draw')
        self.track_motions = builder.get_callback_handler('track_motions')
        self.track_clicks = builder.get_callback_handler('track_clicks')
        self.swap_layout = builder.get_callback_handler('swap_layout')
        self.redraw_current_slide = builder.get_callback_handler('redraw_current_slide')
        self.resize_cache = builder.get_callback_handler('cache.resize_widget')
        self.get_slide_point = builder.get_callback_handler('zoom.get_slide_point')
        self.start_zooming = builder.get_callback_handler('zoom.start_zooming')
        self.stop_zooming = builder.get_callback_handler('zoom.stop_zooming')

        self.connect_signals(self)

        self.scribble_color = Gdk.RGBA()
        self.scribble_color.parse(config.get('scribble', 'color'))
        self.scribble_width = config.getfloat('scribble', 'width')

        self.config = config

        # Presenter-size setup
        self.get_object("scribble_color").set_rgba(self.scribble_color)
        self.get_object("scribble_width").set_value(self.scribble_width)

        self.pen_event = evdev_pad.PenEventLoop(self)
        if self.pen_event.pen_thread:
            self.pen_pointer = builder.pen_pointer
        else:
            self.pen_event = None


    def nav_scribble(self, name, ctrl_pressed, command = None):
        """ Handles an key press event: undo or disable scribbling.

        Args:
            name (`str`): The name of the key pressed
            ctrl_pressed (`bool`): whether the ctrl modifier key was pressed
            command (`str`): the name of the command in case this function is called by on_navigation

        Returns:
            `bool`: whether the event was consumed
        """
        if not self.scribbling_mode:
            return False
        elif command == 'undo_scribble':
            self.undo()
        elif command == 'redo':
            self.redo()
        elif command == 'cancel':
            self.disable_scribbling()
        else:
            return False
        return True

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
                self.scribble_list[-1][3].append(point)
                self.redraw_current_slide()
                return True
            elif self.drawing_mode == "erase" or (
                 self.drawing_mode == "scribble" and self.drag_button == Gdk.BUTTON_SECONDARY):
                for scribble in self.scribble_list[:]:
                    if scribble[0] == 'segment':
                        if self.last_del_point:
                            for i in range(len(scribble[3]) - 1):
                                if segments_intersect(point, self.last_del_point, scribble[3][i], scribble[3][i + 1]):
                                    self.add_undo(('d', scribble))
                                    self.scribble_list.remove(scribble)
                                    break
                    if scribble[0] == 'box':
                        if min(scribble[3][0][0],scribble[3][1][0]) <= point[0] <= max(scribble[3][0][0],scribble[3][1][0]) and \
                           min(scribble[3][0][1],scribble[3][1][1]) <= point[1] <= max(scribble[3][0][1],scribble[3][1][1]):
                            self.add_undo(('d', scribble))
                            self.scribble_list.remove(scribble)

                self.last_del_point = point
                self.redraw_current_slide()
                return True
            elif self.drawing_mode == "box":
                self.scribble_list[-1][3][1] = point
                self.redraw_current_slide()
                return True

        return False


    def toggle_scribble(self, e_type, point, button, always=False):
        """ Start/stop drawing scribbles.

        Args:
            e_type: Gdk.event type (event.get_event_type())
            point: point on slide where event occured (self.zoom.get_slide_point(widget, event))
            button: button code (event.get_button())
            always: a boolean allowing scribbling when not in highlight mode

        Returns:
            `bool`: whether the event was consumed
        """
        if not always and not self.scribbling_mode:
            return False

        if e_type == Gdk.EventType.BUTTON_PRESS:
            if self.drawing_mode == "scribble" and button[1] == Gdk.BUTTON_PRIMARY:
                self.scribble_list.append(("segment", self.scribble_color, self.scribble_width, []))
                self.add_undo(('a', self.scribble_list[-1]))
            elif self.drawing_mode == "erase" or (
                 self.drawing_mode == "scribble" and button[1] == Gdk.BUTTON_SECONDARY):
                self.last_del_point = None
            elif self.drawing_mode == "box":
                self.scribble_list.append(("box", self.scribble_color, self.scribble_width, [point, point]))
                self.add_undo(('a', self.scribble_list[-1]))
            self.scribble_drawing = True
            return self.track_scribble(point, button)

        elif e_type == Gdk.EventType.BUTTON_RELEASE:
            self.scribble_drawing = False
            return True

        return False


    def draw_scribble(self, widget, cairo_context):
        """ Perform the drawings by user.

        Args:
            widget (:class:`~Gtk.DrawingArea`): The widget where to draw the scribbles.
            cairo_context (:class:`~cairo.Context`): The canvas on which to render the drawings
        """
        ww, wh = widget.get_allocated_width(), widget.get_allocated_height()

        cairo_context.set_line_cap(cairo.LINE_CAP_ROUND)

        for stype, color, width, points in self.scribble_list:
            if stype == "segment":
                points = [(p[0] * ww, p[1] * wh) for p in points]

                cairo_context.set_source_rgba(*color)
                cairo_context.set_line_width(width)
                cairo_context.move_to(*points[0])

                for p in points[1:]:
                    cairo_context.line_to(*p)
                cairo_context.stroke()
            if stype == "box":
                points = [(p[0] * ww, p[1] * wh) for p in points]
                cairo_context.set_source_rgba(*color)
                cairo_context.move_to(*points[0])
                x0, y0 = points[0]
                x1, y1 = points[1]
                cairo_context.move_to(x0,y0)
                cairo_context.line_to(x0,y1)
                cairo_context.line_to(x1,y1)
                cairo_context.line_to(x1,y0)
                cairo_context.close_path()
                cairo_context.set_source_rgba(*color)
                cairo_context.fill_preserve()
                cairo_context.set_source_rgba(*color)
                cairo_context.set_line_width(width)
                cairo_context.stroke()

    def update_color(self, widget):
        """ Callback for the color chooser button, to set scribbling color.

        Args:
            widget (:class:`~Gtk.ColorButton`):  the clicked button to trigger this event, if any
        """
        self.scribble_color = widget.get_rgba()
        self.config.set('scribble', 'color', self.scribble_color.to_string())


    def update_width(self, widget, event, value):
        """ Callback for the width chooser slider, to set scribbling width.

        Args:
            widget (:class:`~Gtk.Scale`): The slider control used to select the scribble width
            event (:class:`~Gdk.Event`):  the GTK event triggering this update.
            value (`int`): the width of the scribbles to be drawn
        """
        # It seems that values returned are not necessarily in range
        self.scribble_width = max(0.1, int(value*10)/10)
        self.config.set('scribble', 'width', str(self.scribble_width))


    def clear_scribble(self, *args):
        """ Callback for the scribble clear button, to remove all scribbles.
        """
        if self.scribbling_mode:
            self.add_undo(('c', self.scribble_list[:]))
        del self.scribble_list[:]

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


    def switch_scribbling(self, widget, event = None):
        """ Starts the mode where one can read on top of the screen.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget which has received the event.
            event (:class:`~Gdk.Event` or None):  the GTK event., None when called through a menu item

        Returns:
            `bool`: whether the event was consumed
        """
        if issubclass(type(widget), Gtk.CheckMenuItem) and widget.get_active() == self.scribbling_mode:
            # Checking the checkbox conforming to current situation: do nothing
            return False

        elif issubclass(type(widget), Gtk.Actionable):
            # A button or menu item, etc. directly connected to this action
            pass

        elif event.type != Gdk.EventType.KEY_PRESS:
            return False

        # Perform the state toggle
        if self.scribbling_mode:
            return self.disable_scribbling()

        else:
            return self.enable_scribbling()


    def enable_scribbling(self):
        """ Enable the scribbling mode.

        Returns:
            `bool`: whether it was possible to enable (thus if it was not enabled already)
        """
        self.enable_draw()
        if self.scribbling_mode:
            return False

        self.off_render.remove(self.scribble_overlay)
        self.swap_layout(None, 'highlight')

        self.p_central.queue_draw()
        self.scribble_overlay.queue_draw()

        self.scribbling_mode = True
        self.pres_highlight.set_active(self.scribbling_mode)

        self.undo_stack = []
        self.get_object("scribble_redo").set_sensitive(False)
        self.get_object("scribble_undo").set_sensitive(False)

        return True


    def disable_scribbling(self):
        """ Disable the scribbling mode.

        Returns:
            `bool`: whether it was possible to disable (thus if it was not disabled already)
        """
        if not self.scribbling_mode:
            return False

        self.swap_layout('highlight', None)

        self.off_render.add(self.scribble_overlay)
        self.scribbling_mode = False
        self.drawing_mode = None
        self.pres_highlight.set_active(self.scribbling_mode)

        self.p_central.queue_draw()
        extras.Cursor.set_cursor(self.p_central)

        return True

    def switch_erasing(self):
        """ Toggle the erasing mode.
        """
        if self.drawing_mode == "erase":
            return self.disable_erasing()
        return self.enable_erasing()

    def enable_erasing(self):
        """ Enable the erasing mode.

        Enables scribbling mode if needed.

        Returns:
            `bool`: whether it was possible to enable (thus if it was not enabled already)
        """
        if self.drawing_mode == "erase":
            return False

        self.before_erasing = self.scribbling_mode
        if not self.scribbling_mode:
            self.enable_scribbling()
        # Probably a race condition here
        self.enable_erase()

        return True

    def disable_erasing(self):
        """ Disaable the erasing mode.

        Disables scribbling mode if it was enabled by the corresponding
        enable_erasing().

        Returns:
            `bool`: whether it was possible to disable (thus if it was not enabled already)
        """
        if self.drawing_mode != "erase":
            return False

        self.enable_draw()
        if not self.before_erasing:
            self.disable_scribbling()

        return True

    def enable_erase(self, *args):
        self.drawing_mode = "erase"

    def enable_draw(self, *args):
        self.drawing_mode = "scribble"

    def enable_box(self, *args):
        self.drawing_mode = "box"
    def add_undo(self, operation):
        if self.undo_stack_pos < len(self.undo_stack):
            del self.undo_stack[self.undo_stack_pos:]
        self.undo_stack.append(operation)
        self.undo_stack_pos = self.undo_stack_pos + 1
        self.get_object("scribble_redo").set_sensitive(False)
        self.get_object("scribble_undo").set_sensitive(True)

    def undo(self, *args):
        if self.undo_stack_pos > 0:
            self.get_object("scribble_redo").set_sensitive(True)
            self.undo_stack_pos = self.undo_stack_pos - 1
            if self.undo_stack_pos == 0:
                self.get_object("scribble_undo").set_sensitive(False)
            op = self.undo_stack[self.undo_stack_pos]
            if op[0] == 'a':
                self.scribble_list.remove(op[1])
            elif op[0] == 'd':
                self.scribble_list.append(op[1])
            elif op[0] == 'c':
                self.scribble_list.extend(op[1])

            self.redraw_current_slide()

    def redo(self, *args):
        if self.undo_stack_pos < len(self.undo_stack):
            op = self.undo_stack[self.undo_stack_pos]
            if op[0] == 'a':
                self.scribble_list.append(op[1])
            elif op[0] == 'd':
                self.scribble_list.remove(op[1])
            elif op[0] == 'c':
                self.scribble_list = []
            self.undo_stack_pos = self.undo_stack_pos + 1
            if self.undo_stack_pos == len(self.undo_stack):
                self.get_object("scribble_redo").set_sensitive(False)
            self.get_object("scribble_undo").set_sensitive(True)

            self.redraw_current_slide()
