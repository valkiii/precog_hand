"""Everything a gesture can trigger. Add your own with the @action decorator."""
import subprocess
import time

from AppKit import NSEvent
from Quartz import (CGEventCreateKeyboardEvent, CGEventCreateMouseEvent, CGEventPost,
                    CGEventSetFlags, CGEventSourceCreate, CGWarpMouseCursorPosition,
                    kCGEventFlagMaskAlternate, kCGEventFlagMaskCommand,
                    kCGEventFlagMaskControl, kCGEventFlagMaskNumericPad,
                    kCGEventFlagMaskSecondaryFn, kCGEventFlagMaskShift,
                    kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGEventMouseMoved,
                    kCGEventRightMouseDown, kCGEventRightMouseUp, kCGMouseButtonRight,
                    kCGMouseEventClickState, CGEventSetIntegerValueField,
                    kCGEventSourceStateHIDSystemState, kCGHIDEventTap, kCGMouseButtonLeft)

from . import windows as W

ACTIONS = {}


def action(name, desc, params=()):
    def deco(fn):
        ACTIONS[name] = {"fn": fn, "desc": desc, "params": params}
        return fn
    return deco


def run(name, args=None):
    a = ACTIONS.get(name)
    if a is None:
        raise KeyError(f"unknown action {name!r}")
    return a["fn"](**(args or {}))


# ---------------------------------------------------------------- input events
KEYCODES = {
    **{c: k for c, k in zip("asdfhgzxcv", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9])},
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19,
    "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28,
    "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "return": 36,
    "enter": 36, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43,
    "/": 44, "n": 45, "m": 46, ".": 47, "tab": 48, "space": 49, "`": 50,
    "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121, "forwarddelete": 117,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
    "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}
MODS = {
    "cmd": kCGEventFlagMaskCommand, "command": kCGEventFlagMaskCommand,
    "ctrl": kCGEventFlagMaskControl, "control": kCGEventFlagMaskControl,
    "alt": kCGEventFlagMaskAlternate, "opt": kCGEventFlagMaskAlternate,
    "option": kCGEventFlagMaskAlternate, "shift": kCGEventFlagMaskShift,
    "fn": kCGEventFlagMaskSecondaryFn,
}
_src = CGEventSourceCreate(kCGEventSourceStateHIDSystemState)


def press(combo):
    """press('ctrl+right'), press('cmd+shift+4')"""
    parts = [p.strip().lower() for p in combo.split("+")]
    key = parts[-1]
    flags = 0
    for m in parts[:-1]:
        flags |= MODS[m]
    code = KEYCODES[key]
    if key in ("left", "right", "up", "down"):
        flags |= kCGEventFlagMaskSecondaryFn | kCGEventFlagMaskNumericPad
    for down in (True, False):
        ev = CGEventCreateKeyboardEvent(_src, code, down)
        CGEventSetFlags(ev, flags)
        CGEventPost(kCGHIDEventTap, ev)
        time.sleep(0.01)


def key_event(code, down, mods=()):
    """Single key down or up with modifier flags, e.g. key_event(55, True, ['cmd'])."""
    flags = 0
    for m in mods:
        flags |= MODS[m]
    ev = CGEventCreateKeyboardEvent(_src, code, down)
    CGEventSetFlags(ev, flags)
    CGEventPost(kCGHIDEventTap, ev)


def mouse_move(x, y):
    CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(None, kCGEventMouseMoved, (x, y),
                                                        kCGMouseButtonLeft))


def click(x, y, count=1, right=False):
    """count=2 makes the second click of a double-click."""
    CGWarpMouseCursorPosition((x, y))
    kinds = (kCGEventRightMouseDown, kCGEventRightMouseUp) if right else (kCGEventLeftMouseDown, kCGEventLeftMouseUp)
    for t in kinds:
        ev = CGEventCreateMouseEvent(None, t, (x, y), kCGMouseButtonRight if right else kCGMouseButtonLeft)
        CGEventSetIntegerValueField(ev, kCGMouseEventClickState, count)
        CGEventPost(kCGHIDEventTap, ev)
        time.sleep(0.02)


def media_key(code):
    for down in (True, False):
        ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            14, (0, 0), 0xA00 if down else 0xB00, 0, 0, None, 8,
            (code << 16) | ((0xA if down else 0xB) << 8), -1)
        CGEventPost(kCGHIDEventTap, ev.CGEvent())


def _mission_control(arg=None):
    subprocess.Popen(["open", "-a", "Mission Control"] + (["--args", arg] if arg else []))


# ---------------------------------------------------------------- spaces / system
@action("mission_control", "Show all windows (Mission Control)")
def mission_control():
    _mission_control()


@action("app_windows", "Show windows of the current app (App Exposé)")
def app_windows():
    _mission_control("2")


@action("show_desktop", "Reveal the desktop")
def show_desktop():
    _mission_control("1")


@action("desktop_left", "Previous desktop / Space")
def desktop_left():
    from . import spaces
    spaces.switch(right=False)   # Ctrl+Left keystrokes are ignored when synthesized


@action("desktop_right", "Next desktop / Space")
def desktop_right():
    from . import spaces
    spaces.switch(right=True)


@action("next_app", "Jump back to the previous app (a single quick Cmd+Tab)")
def next_app():
    press("cmd+tab")


@action("fullscreen", "Toggle native full screen (Ctrl+Cmd+F)")
def fullscreen():
    press("ctrl+cmd+f")


# ---------------------------------------------------------------- windows
_saved_frames = {}


def _place(fn):
    w = W.front_window()
    if not w or not w.frame:
        return None
    f = w.frame
    vis = W.screens()[W.screen_index_for(f)]["visible"]
    w.set_frame(*fn(f, vis))
    w.raise_()
    return w


@action("maximize_window", "Enlarge the front window to fill its screen (again = restore)")
def maximize_window():
    w = W.front_window()
    if not w or not w.frame:
        return
    f = w.frame
    vis = W.screens()[W.screen_index_for(f)]["visible"]
    if all(abs(a - b) < 12 for a, b in zip(f, vis)) and w.key in _saved_frames:
        w.set_frame(*_saved_frames.pop(w.key))
    else:
        _saved_frames[w.key] = f
        w.set_frame(*vis)
    w.raise_()


@action("tile_left", "Snap the front window to the left half")
def tile_left():
    _place(lambda f, v: (v[0], v[1], v[2] / 2, v[3]))


@action("tile_right", "Snap the front window to the right half")
def tile_right():
    _place(lambda f, v: (v[0] + v[2] / 2, v[1], v[2] / 2, v[3]))


@action("center_window", "Centre the front window on its screen")
def center_window():
    _place(lambda f, v: (v[0] + (v[2] - f[2]) / 2, v[1] + (v[3] - f[3]) / 2, f[2], f[3]))


def move_to_screen(w, idx):
    f = w.frame
    src = W.screens()[W.screen_index_for(f)]["visible"]
    dst = W.screens()[idx]["visible"]
    sx, sy = dst[2] / src[2], dst[3] / src[3]
    w.set_frame(dst[0] + (f[0] - src[0]) * sx, dst[1] + (f[1] - src[1]) * sy,
                min(f[2] * sx, dst[2]), min(f[3] * sy, dst[3]))
    w.raise_()


@action("window_next_screen", "Send the front window to the next display")
def window_next_screen():
    w = W.front_window()
    if w and w.frame:
        idx = W.neighbour_screen(W.screen_index_for(w.frame), "next")
        if idx is not None:
            move_to_screen(w, idx)


@action("window_prev_screen", "Send the front window to the previous display")
def window_prev_screen():
    w = W.front_window()
    if w and w.frame:
        idx = W.neighbour_screen(W.screen_index_for(w.frame), "prev")
        if idx is not None:
            move_to_screen(w, idx)


@action("focus_next_screen", "Jump focus (and cursor) to the next display")
def focus_next_screen():
    from Quartz import CGEventCreate, CGEventGetLocation
    p = CGEventGetLocation(CGEventCreate(None))
    idx = W.neighbour_screen(W.screen_at(p.x, p.y), "next")
    if idx is None:
        return
    f = W.screens()[idx]["frame"]
    CGWarpMouseCursorPosition((f[0] + f[2] / 2, f[1] + f[3] / 2))
    W.activate_screen(idx)


@action("minimize_window", "Minimise the front window")
def minimize_window():
    w = W.front_window()
    if w:
        w.minimize()


@action("close_window", "Close the front window")
def close_window():
    w = W.front_window()
    if w:
        w.close()


# ---------------------------------------------------------------- media
@action("play_pause", "Play / pause media")
def play_pause():
    media_key(16)


@action("next_track", "Next track")
def next_track():
    media_key(17)


@action("prev_track", "Previous track")
def prev_track():
    media_key(18)


@action("volume_up", "Volume up")
def volume_up():
    media_key(0)


@action("volume_down", "Volume down")
def volume_down():
    media_key(1)


@action("mute", "Toggle mute")
def mute():
    media_key(7)


# ---------------------------------------------------------------- custom
@action("hotkey", "Press any shortcut, e.g. cmd+shift+4", params=("keys",))
def hotkey(keys):
    press(keys)


@action("open_app", "Open / focus an application", params=("app",))
def open_app(app):
    subprocess.Popen(["open", "-a", app])


@action("shell", "Run a shell command", params=("command",))
def shell(command):
    subprocess.Popen(command, shell=True)


@action("applescript", "Run an AppleScript snippet", params=("script",))
def applescript(script):
    subprocess.Popen(["osascript", "-e", script])


# ---------------------------------------------------------------- Precog itself
@action("toggle_pointer", "Enter / leave MANIPULATION mode (hand drives the cursor)")
def toggle_pointer():
    pass  # handled by the app loop


@action("app_switcher", "APP SWITCHER: icon strip on screen, move hand to pick, pinch to open")
def app_switcher():
    pass  # handled by the app loop


@action("layout_picker", "WINDOW LAYOUT: point at halves / quarters / maximize, pinch to apply")
def layout_picker():
    pass  # handled by the app loop


@action("toggle_pause", "Standby on / off (ignore all other gestures)")
def toggle_pause():
    pass  # handled by the app loop


@action("none", "Do nothing (useful for testing)")
def none():
    pass


INTERNAL = {"toggle_pointer", "toggle_pause", "app_switcher", "layout_picker"}
