# -*- coding: utf-8 -*-
#
#       evdev_pad.py
"""
:mod:`pympress.evdev_pad` -- Read raw writing pad evdev devices
---------------------------------------------------------------
"""
import threading
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GLib
try:
    import evdev
except ModuleNotFoundError:
    pass

class PenEventLoop():
    pressed_buttons = set()
    pen_thread = None
    buttons_thread = None
    pen_range = None
    collect_coords = {'x': -1, 'y': -1, 'p': -1, 'last': (-1, -1)}
    scribbler = None
    exchange_xy = True
    mirror_x = True
    mirror_y = False
    quit = False

    def __init__(self, scr):
        try:
            dev = self.find_device(evdev.ecodes.BTN_TOUCH)
        except NameError:
            return
        if dev:
            self.pen_range = (
                (dev.capabilities()[3][0][1].min, dev.capabilities()[3][0][1].max),
                (dev.capabilities()[3][1][1].min, dev.capabilities()[3][1][1].max))
            self.pen_thread = threading.Thread(target=self.pen_event_loop, daemon=True, args=[dev])
            if self.pen_thread:
                self.scribbler = scr
                self.pen_thread.start()
        try:
            buttons_dev = self.find_device(evdev.ecodes.BTN_7)
        except NameError:
            return
        if buttons_dev:
            self.buttons_thread = threading.Thread(target=self.buttons_event_loop, daemon=True, args=[buttons_dev])
            if self.buttons_thread:
                self.scribbler = scr
                self.buttons_thread.start()

    def find_device(self, button):
        for name in evdev.list_devices():
            dev = evdev.InputDevice(name)
            if 1 in dev.capabilities()[0] and button in dev.capabilities()[1]:
                return dev
        return None

    def buttons_event_loop(self, dev):
        for event in dev.read_loop():
            if self.quit:
                return
            if event.type == evdev.ecodes.EV_KEY:
                if event.value == evdev.KeyEvent.key_up:
                    self.pressed_buttons.difference_update({event.code})
                    name = "BTN_"+str(event.code - evdev.ecodes.BTN_0)
                    GLib.idle_add(self.scribbler.evdev_callback_buttons, name)
                else:
                    self.pressed_buttons.add(event.code)

    def pen_event_loop(self, dev):
        for event in dev.read_loop():
            if self.quit:
                return
            if event.type == evdev.ecodes.EV_KEY:
                if event.value == evdev.KeyEvent.key_up:
                    self.pressed_buttons.difference_update({event.code})
                    if event.code == evdev.ecodes.BTN_DIGI:
                        GLib.idle_add(self.scribbler.evdev_callback_pointer, ())
                    else:
                        GLib.idle_add(self.scribbler.evdev_callback_track,
                            (Gdk.EventType.BUTTON_RELEASE, self.collect_coords['last'],
                             (True, Gdk.BUTTON_SECONDARY if evdev.ecodes.BTN_STYLUS in self.pressed_buttons
                             else Gdk.BUTTON_PRIMARY)))
                else:
                    self.pressed_buttons.add(event.code)
                    if event.code == evdev.ecodes.BTN_TOUCH:
                        GLib.idle_add(self.scribbler.evdev_callback_track,
                            (Gdk.EventType.BUTTON_PRESS, self.collect_coords['last'],
                             (True, Gdk.BUTTON_SECONDARY if evdev.ecodes.BTN_STYLUS in self.pressed_buttons
                             else Gdk.BUTTON_PRIMARY)))
            if event.type == evdev.ecodes.EV_ABS:
                if event.code == 0:
                    self.collect_coords['x'] = event.value
                if event.code == 1:
                    self.collect_coords['y'] = event.value
                if event.code == 24:
                    self.collect_coords['p'] = event.value
            if event.type == evdev.ecodes.EV_SYN:
                if self.collect_coords['x'] > -1 and self.collect_coords['y'] > -1:
                    point = ((self.collect_coords['x'] - self.pen_range[0][0]) /
                             (self.pen_range[0][1] - self.pen_range[0][0]),
                             (self.collect_coords['y'] - self.pen_range[1][0]) /
                             (self.pen_range[1][1] - self.pen_range[1][0]))
                    if self.exchange_xy:
                        point = (point[1], point[0])
                    if self.mirror_x:
                        point = (1 - point[0], point[1])
                    if self.mirror_y:
                        point = (point[0], 1 - point[1])
                    self.collect_coords['x'] = -1
                    self.collect_coords['y'] = -1
                    self.collect_coords['last'] = point
                    if evdev.ecodes.BTN_TOUCH in self.pressed_buttons:
                        GLib.idle_add(self.scribbler.evdev_callback_pen, point)
                    elif evdev.ecodes.BTN_DIGI in self.pressed_buttons or not self.pressed_buttons:
                        GLib.idle_add(self.scribbler.evdev_callback_pointer, point)
                pass
