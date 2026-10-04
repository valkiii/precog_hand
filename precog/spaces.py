"""Switch desktops (Spaces) the way a three-finger trackpad swipe does.

macOS ignores synthetic Ctrl+Arrow presses for Space switching, so instead we post the
private "dock swipe" gesture events the trackpad produces. On macOS 27+ each event must
also carry a serialized raw IOHID payload (CGEvent field 4205).
Ported from iss by Joshua Li (0BSD): https://github.com/joshuarli/iss
"""
import ctypes
import platform
import struct

from Quartz import (CGEventCreate, CGEventCreateData, CGEventCreateFromData,
                    CGEventGetDoubleValueField, CGEventGetIntegerValueField, CGEventGetTimestamp,
                    CGEventPost, CGEventSetDoubleValueField, CGEventSetIntegerValueField,
                    kCGSessionEventTap)

# private CGEvent fields (reverse engineered)
TYPE_FIELD, HID_TYPE, SCROLL_Y, SWIPE_MOTION, SWIPE_PROGRESS = 55, 110, 119, 123, 124
VEL_X, VEL_Y, PHASE, FLAG_BITS, ZOOM_DX = 129, 130, 132, 135, 139
SWIPE_MASK, POS_X, POS_Y, PHASE_ALIAS, ZOOM_DY, SRC_ALIAS, RAW_IOHID = 115, 125, 126, 134, 138, 169, 4205
GESTURE, DOCK_CONTROL, DOCK_SWIPE, HORIZONTAL = 29, 30, 23, 1
BEGAN, CHANGED, ENDED = 1, 2, 4
FLT_TRUE_MIN = 1.401298464324817e-45

_libc = ctypes.CDLL(None)
_libc.mach_absolute_time.restype = ctypes.c_uint64
MACOS_MAJOR = int(platform.mac_ver()[0].split(".")[0] or 0)


def _fixed(v):
    f = int(v * 65536.0)
    if f == 0 and v != 0:
        return 1 if v > 0 else -1
    return max(-2**31, min(2**31 - 1, f))


def _payload(ev):
    phase = CGEventGetIntegerValueField(ev, PHASE)
    vx = CGEventGetDoubleValueField(ev, VEL_X)
    vy = CGEventGetDoubleValueField(ev, VEL_Y)
    with_vel = vx != 0 or vy != 0 or phase == ENDED
    fluid = struct.pack("<IIIB3xiiiIHHi", 40, 23, (phase & 0xFF) << 24, 0,
                        _fixed(CGEventGetDoubleValueField(ev, POS_X)),
                        _fixed(CGEventGetDoubleValueField(ev, POS_Y)), 0,
                        CGEventGetIntegerValueField(ev, SWIPE_MASK) & 0xFFFFFFFF,
                        CGEventGetIntegerValueField(ev, SWIPE_MOTION), 3,
                        _fixed(CGEventGetDoubleValueField(ev, SWIPE_PROGRESS)))
    vel = struct.pack("<IIIB3xiii", 28, 9, 0, 1, _fixed(vx), _fixed(vy), 0) if with_vel else b""
    ts = CGEventGetTimestamp(ev) or _libc.mach_absolute_time()
    header = struct.pack("<QQIII", ts, 0, 0, 0, 2 if with_vel else 1)
    return header + fluid + vel


def _augment(ev):
    data = bytes(CGEventCreateData(None, ev))
    if data[:4] != b"\x00\x00\x00\x02":
        return None
    p = _payload(ev)
    blob = data + struct.pack(">HH", len(p), RAW_IOHID) + p
    return CGEventCreateFromData(None, blob)


def _dock_event_27(phase, right, progress=None, velocity=None):
    ev = CGEventCreate(None)
    CGEventSetIntegerValueField(ev, TYPE_FIELD, DOCK_CONTROL)
    CGEventSetIntegerValueField(ev, HID_TYPE, DOCK_SWIPE)
    CGEventSetIntegerValueField(ev, PHASE, phase)
    p = progress if progress is not None else (-1.0 if right else 1.0)
    CGEventSetDoubleValueField(ev, SWIPE_PROGRESS, p)
    CGEventSetIntegerValueField(ev, SWIPE_MOTION, HORIZONTAL)
    CGEventSetIntegerValueField(ev, PHASE_ALIAS, phase)
    CGEventSetDoubleValueField(ev, ZOOM_DY, 3.0)
    CGEventSetDoubleValueField(ev, SRC_ALIAS, float(_libc.mach_absolute_time()))
    CGEventSetDoubleValueField(ev, POS_X, 0.1)
    if phase == ENDED:
        v = velocity if velocity is not None else (-9999.0 if right else 9999.0)
        CGEventSetDoubleValueField(ev, VEL_X, v)
    return _augment(ev)


def _dock_event_old(phase, right):
    ev = CGEventCreate(None)
    CGEventSetIntegerValueField(ev, TYPE_FIELD, DOCK_CONTROL)
    CGEventSetIntegerValueField(ev, HID_TYPE, DOCK_SWIPE)
    CGEventSetIntegerValueField(ev, PHASE, phase)
    bits = struct.unpack("<i", struct.pack("<f", FLT_TRUE_MIN if right else -FLT_TRUE_MIN))[0]
    CGEventSetIntegerValueField(ev, FLAG_BITS, bits)
    CGEventSetIntegerValueField(ev, SWIPE_MOTION, HORIZONTAL)
    CGEventSetDoubleValueField(ev, SCROLL_Y, 0)
    CGEventSetDoubleValueField(ev, ZOOM_DX, FLT_TRUE_MIN)
    if phase == ENDED:
        CGEventSetDoubleValueField(ev, VEL_X, 400.0 if right else -400.0)
        CGEventSetDoubleValueField(ev, VEL_Y, 0)
    return ev


def _post(dock):
    companion = CGEventCreate(None)
    CGEventSetIntegerValueField(companion, TYPE_FIELD, GESTURE)
    CGEventPost(kCGSessionEventTap, dock)
    CGEventPost(kCGSessionEventTap, companion)


def switch(right):
    """Move one desktop to the right (next) or left (previous)."""
    make = _dock_event_27 if MACOS_MAJOR >= 27 else _dock_event_old
    events = [make(ph, right) for ph in (BEGAN, CHANGED, ENDED)]
    if any(e is None for e in events):
        raise RuntimeError("could not build dock swipe events")
    for e in events:
        _post(e)


# ---------------------------------------------------------------- diagnostics
def active_space():
    sl = ctypes.cdll.LoadLibrary("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight")
    sl.CGSMainConnectionID.restype = ctypes.c_int
    sl.CGSGetActiveSpace.restype = ctypes.c_uint64
    sl.CGSGetActiveSpace.argtypes = [ctypes.c_int]
    return sl.CGSGetActiveSpace(sl.CGSMainConnectionID())


CANCELLED = 8


class Swipe:
    """An interactive desktop slide: begin, follow a progress value, then commit or cancel."""

    def __init__(self):
        self.active = False
        self.progress = 0.0

    def begin(self):
        self.active = True
        self.progress = 0.0
        _post(_dock_event_27(BEGAN, True, progress=0.0) if MACOS_MAJOR >= 27 else _dock_event_old(BEGAN, True))

    def move(self, progress):
        """progress in [-1, 1]; negative reveals the desktop on the right."""
        self.progress = max(-1.0, min(1.0, progress))
        if MACOS_MAJOR >= 27:
            _post(_dock_event_27(CHANGED, self.progress < 0, progress=self.progress))

    def end(self, commit):
        if not self.active:
            return
        self.active = False
        p = self.progress
        if MACOS_MAJOR < 27:   # old path has no live progress: just switch at the end
            if commit and p:
                switch(p < 0)
            return
        if commit and p:
            _post(_dock_event_27(ENDED, p < 0, progress=p, velocity=-1000.0 if p < 0 else 1000.0))
        else:
            _post(_dock_event_27(CANCELLED, p < 0, progress=p, velocity=0.0))
