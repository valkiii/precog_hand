"""Screens and windows via the macOS Accessibility API (top-left global coordinates)."""
import os

from AppKit import (NSApplicationActivateIgnoringOtherApps, NSRunningApplication,
                    NSScreen, NSWorkspace)
from ApplicationServices import (AXIsProcessTrusted, AXUIElementCopyAttributeValue,
                                 AXUIElementCreateApplication, AXUIElementPerformAction,
                                 AXUIElementSetAttributeValue, AXValueCreate,
                                 AXValueGetValue, kAXValueCGPointType,
                                 kAXValueCGSizeType)
from Quartz import (CGPoint, CGSize, CGWindowListCopyWindowInfo, kCGNullWindowID,
                    kCGWindowListExcludeDesktopElements, kCGWindowListOptionOnScreenOnly)

OWN_PIDS = {os.getpid()}


def trusted():
    return bool(AXIsProcessTrusted())


# ---------------------------------------------------------------- screens
def screens():
    """[{'frame': (x, y, w, h), 'visible': (x, y, w, h)}] in top-left coordinates."""
    all_s = NSScreen.screens()
    ph = all_s[0].frame().size.height

    def conv(r):
        return (r.origin.x, ph - (r.origin.y + r.size.height), r.size.width, r.size.height)

    return [{"frame": conv(s.frame()), "visible": conv(s.visibleFrame())} for s in all_s]


def desktop_bounds():
    xs = [s["frame"] for s in screens()]
    x0 = min(f[0] for f in xs)
    y0 = min(f[1] for f in xs)
    x1 = max(f[0] + f[2] for f in xs)
    y1 = max(f[1] + f[3] for f in xs)
    return x0, y0, x1 - x0, y1 - y0


def _overlap(a, b):
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(w, 0) * max(h, 0)


def screen_index_for(rect):
    sc = screens()
    return max(range(len(sc)), key=lambda i: _overlap(sc[i]["frame"], rect))


def screen_at(x, y):
    for i, s in enumerate(screens()):
        f = s["frame"]
        if f[0] <= x < f[0] + f[2] and f[1] <= y < f[1] + f[3]:
            return i
    return 0


def neighbour_screen(idx, direction):
    """Screen whose centre lies furthest in `direction` ('left'/'right'/'next'/'prev')."""
    sc = screens()
    if len(sc) < 2:
        return None
    if direction in ("next", "prev"):
        order = sorted(range(len(sc)), key=lambda i: (sc[i]["frame"][0], sc[i]["frame"][1]))
        pos = order.index(idx)
        return order[(pos + (1 if direction == "next" else -1)) % len(order)]
    cx = sc[idx]["frame"][0] + sc[idx]["frame"][2] / 2
    cands = [(abs(s["frame"][0] + s["frame"][2] / 2 - cx), i) for i, s in enumerate(sc)
             if (s["frame"][0] + s["frame"][2] / 2 - cx) * (1 if direction == "right" else -1) > 0]
    return min(cands)[1] if cands else None


# ---------------------------------------------------------------- windows
def window_list():
    infos = CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements, kCGNullWindowID)
    out = []
    for w in infos or []:
        if w.get("kCGWindowLayer", 1) != 0 or w.get("kCGWindowOwnerPID") in OWN_PIDS:
            continue
        b = w.get("kCGWindowBounds", {})
        rect = (b.get("X", 0), b.get("Y", 0), b.get("Width", 0), b.get("Height", 0))
        if rect[2] < 60 or rect[3] < 40 or w.get("kCGWindowAlpha", 1) == 0:
            continue
        out.append({"pid": int(w["kCGWindowOwnerPID"]), "rect": rect,
                    "title": w.get("kCGWindowName") or "",
                    "owner": w.get("kCGWindowOwnerName") or ""})
    return out


def _attr(el, name):
    err, v = AXUIElementCopyAttributeValue(el, name, None)
    return v if err == 0 else None


class Window:
    def __init__(self, el, pid):
        self.el = el
        self.pid = pid

    @property
    def frame(self):
        p = _attr(self.el, "AXPosition")
        s = _attr(self.el, "AXSize")
        if p is None or s is None:
            return None
        _, pt = AXValueGetValue(p, kAXValueCGPointType, None)
        _, sz = AXValueGetValue(s, kAXValueCGSizeType, None)
        return (pt.x, pt.y, sz.width, sz.height)

    def move(self, x, y):
        AXUIElementSetAttributeValue(
            self.el, "AXPosition", AXValueCreate(kAXValueCGPointType, CGPoint(x, y)))

    def resize(self, w, h):
        AXUIElementSetAttributeValue(
            self.el, "AXSize", AXValueCreate(kAXValueCGSizeType, CGSize(w, h)))

    def set_frame(self, x, y, w, h):
        # size/position/size: keeps apps from clamping when crossing screens
        self.resize(w, h)
        self.move(x, y)
        self.resize(w, h)

    def raise_(self):
        AXUIElementPerformAction(self.el, "AXRaise")
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(self.pid)
        if app:
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)

    def minimize(self):
        AXUIElementSetAttributeValue(self.el, "AXMinimized", True)

    def close(self):
        btn = _attr(self.el, "AXCloseButton")
        if btn is not None:
            AXUIElementPerformAction(btn, "AXPress")

    @property
    def key(self):
        return (self.pid, _attr(self.el, "AXTitle"))


def _ax_window_matching(info):
    app = AXUIElementCreateApplication(info["pid"])
    best, best_d = None, None
    for el in _attr(app, "AXWindows") or []:
        w = Window(el, info["pid"])
        f = w.frame
        if not f:
            continue
        d = sum(abs(a - b) for a, b in zip(f, info["rect"]))
        if best_d is None or d < best_d:
            best, best_d = w, d
    return best


def front_window():
    """The window the user is working in, skipping Precog's own windows."""
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app and app.processIdentifier() not in OWN_PIDS:
        pid = app.processIdentifier()
        el = _attr(AXUIElementCreateApplication(pid), "AXFocusedWindow")
        if el is not None:
            return Window(el, pid)
    for info in window_list():
        w = _ax_window_matching(info)
        if w:
            return w
    return None


def window_at(x, y):
    for info in window_list():
        r = info["rect"]
        if r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]:
            return _ax_window_matching(info)
    return None


def activate_screen(idx):
    """Focus the top-most window on screen `idx`."""
    f = screens()[idx]["frame"]
    for info in window_list():
        if _overlap(info["rect"], f) > 0.5 * info["rect"][2] * info["rect"][3]:
            w = _ax_window_matching(info)
            if w:
                w.raise_()
                return w
    return None


# ---------------------------------------------------------------- apps
def app_list(limit=12):
    """Regular apps, most recently used first (front-to-back window order, then the rest)."""
    ws = NSWorkspace.sharedWorkspace()
    apps = {a.processIdentifier(): a for a in ws.runningApplications()
            if a.activationPolicy() == 0 and a.processIdentifier() not in OWN_PIDS}
    order = []
    for info in window_list():
        if info["pid"] in apps and info["pid"] not in order:
            order.append(info["pid"])
    rest = sorted((p for p in apps if p not in order), key=lambda p: apps[p].localizedName().lower())
    out = []
    for pid in (order + rest)[:limit]:
        a = apps[pid]
        out.append({"pid": pid, "name": str(a.localizedName()).strip("\u200e\u200f "),
                    "bundle": str(a.bundleIdentifier() or "")})
    return out


def activate_app(pid, bundle=""):
    import subprocess
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return
    app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps | 1)  # | all windows
    if bundle and not any(w["pid"] == pid for w in window_list()):
        subprocess.Popen(["open", "-b", bundle])  # no window open: ask it to make one


# ---------------------------------------------------------------- layouts
LAYOUTS = [  # 3x3 grid: (name, fx, fy, fw, fh) as fractions of the visible screen
    ("top-left", 0, 0, .5, .5), ("top half", 0, 0, 1, .5), ("top-right", .5, 0, .5, .5),
    ("left half", 0, 0, .5, 1), ("maximize", 0, 0, 1, 1), ("right half", .5, 0, .5, 1),
    ("bottom-left", 0, .5, .5, .5), ("center", .15, .1, .7, .8), ("bottom-right", .5, .5, .5, .5),
]


def layout_rect(vis, idx):
    _, fx, fy, fw, fh = LAYOUTS[idx]
    return (vis[0] + fx * vis[2], vis[1] + fy * vis[3], fw * vis[2], fh * vis[3])
