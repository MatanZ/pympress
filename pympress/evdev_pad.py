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

def find_device(button):
    for name in evdev.list_devices():
        dev = evdev.InputDevice(name)
        if 1 in dev.capabilities()[0] and button in dev.capabilities()[1]:
            return dev
    return None

pressed_buttons = set()
pen_thread = None
pen_range = None
collect_coords = {'x': -1, 'y': -1, 'p': -1, 'last':(-1,-1)}
scribbler = None
exchange_xy = True
mirror_x = True
mirror_y = False

def pen_event_loop(dev):
    global pressed_buttons
    for event in dev.read_loop():
        #print(evdev.categorize(event))
        if event.type == evdev.ecodes.EV_KEY:
            if event.value == evdev.KeyEvent.key_up:
                pressed_buttons.difference_update({event.code})
                if event.code == evdev.ecodes.BTN_DIGI:
                    scribbler.set_pointer(())
                else:
                    scribbler.toggle_scribble(Gdk.EventType.BUTTON_RELEASE, collect_coords['last'], (True,
                        Gdk.BUTTON_SECONDARY if evdev.ecodes.BTN_STYLUS in pressed_buttons else Gdk.BUTTON_PRIMARY),
                        always=True)
            else:
                pressed_buttons.add(event.code)
                if event.code == evdev.ecodes.BTN_TOUCH:
                    scribbler.toggle_scribble(Gdk.EventType.BUTTON_PRESS, collect_coords['last'], (True,
                        Gdk.BUTTON_SECONDARY if evdev.ecodes.BTN_STYLUS in pressed_buttons else Gdk.BUTTON_PRIMARY),
                        always=True)
        if event.type == evdev.ecodes.EV_ABS:
            if event.code == 0:
                collect_coords['x'] = event.value
            if event.code == 1:
                collect_coords['y'] = event.value
            if event.code == 24:
                collect_coords['p'] = event.value
        if event.type == evdev.ecodes.EV_SYN:
            if collect_coords['x'] > -1 and collect_coords['y'] > -1:
                point = ((collect_coords['x']-pen_range[0][0])/(pen_range[0][1]-pen_range[0][0]),
                         (collect_coords['y']-pen_range[1][0])/(pen_range[1][1]-pen_range[1][0]))
                if exchange_xy:
                    point = (point[1], point[0])
                if mirror_x:
                    point = (1-point[0], point[1])
                if mirror_y:
                    point = (point[0], 1-point[1])
                collect_coords['x'] = -1
                collect_coords['y'] = -1
                collect_coords['last'] = point
                if evdev.ecodes.BTN_TOUCH in pressed_buttons:
                    scribbler.track_scribble(point, (False, 0))
                elif evdev.ecodes.BTN_DIGI in pressed_buttons:
                    scribbler.set_pointer(point)
            pass
        

def start_pen_loop(scr):
    global pen_thread
    global pen_range
    global scribbler
    dev = find_device(evdev.ecodes.BTN_TOUCH)
    if dev:
        pen_range = (
            (dev.capabilities()[3][0][1].min, dev.capabilities()[3][0][1].max),
            (dev.capabilities()[3][1][1].min, dev.capabilities()[3][1][1].max))
        pen_thread = threading.Thread(target=pen_event_loop, daemon=True, args=[dev])
        if pen_thread:
            scribbler = scr
            pen_thread.start()
            return True
    return False


    
