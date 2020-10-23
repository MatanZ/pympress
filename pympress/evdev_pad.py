# -*- coding: utf-8 -*-
#
#       evdev_pad.py
"""
:mod:`pympress.evdev_pad` -- Read raw writing pad evdev devices
---------------------------------------------------------------
"""
import threading
import evdev
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk

class PenEventLoop():
    pressed_buttons = set()
    pen_thread = None
    pen_range = None
    collect_coords = {'x': -1, 'y': -1, 'p': -1, 'last': (-1, -1)}
    scribbler = None
    exchange_xy = True
    mirror_x = True
    mirror_y = False
    quit = False

    def __init__(self, scr):
        dev = self.find_device(evdev.ecodes.BTN_TOUCH)
        if dev:
            self.pen_range = (
                (dev.capabilities()[3][0][1].min, dev.capabilities()[3][0][1].max),
                (dev.capabilities()[3][1][1].min, dev.capabilities()[3][1][1].max))
            self.pen_thread = threading.Thread(target=self.pen_event_loop, daemon=True, args=[dev])
            if self.pen_thread:
                self.scribbler = scr
                self.pen_thread.start()

    def find_device(self, button):
        for name in evdev.list_devices():
            dev = evdev.InputDevice(name)
            if 1 in dev.capabilities()[0] and button in dev.capabilities()[1]:
                return dev
        return None

    def pen_event_loop(self, dev):
        for event in dev.read_loop():
            if self.quit:
                return
            if event.type == evdev.ecodes.EV_KEY:
                if event.value == evdev.KeyEvent.key_up:
                    self.pressed_buttons.difference_update({event.code})
                    if event.code == evdev.ecodes.BTN_DIGI:
                        self.scribbler.set_pointer(())
                    else:
                        self.scribbler.toggle_scribble(Gdk.EventType.BUTTON_RELEASE, self.collect_coords['last'],
                            (True, Gdk.BUTTON_SECONDARY if evdev.ecodes.BTN_STYLUS in self.pressed_buttons
                             else Gdk.BUTTON_PRIMARY),
                            always=True)
                else:
                    self.pressed_buttons.add(event.code)
                    if event.code == evdev.ecodes.BTN_TOUCH:
                        self.scribbler.toggle_scribble(Gdk.EventType.BUTTON_PRESS, self.collect_coords['last'],
                            (True, Gdk.BUTTON_SECONDARY if evdev.ecodes.BTN_STYLUS in self.pressed_buttons
                             else Gdk.BUTTON_PRIMARY),
                            always=True)
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
                        self.scribbler.track_scribble(point, (False, 0))
                    elif evdev.ecodes.BTN_DIGI in self.pressed_buttons:
                        self.scribbler.set_pointer(point)
                pass
