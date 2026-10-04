"""Point-and-pinch choosers drawn on screen by the overlay.

  APP SWITCHER   a strip of app icons; the highlight follows your hand left/right
  LAYOUT PICKER  a 3x3 grid of window layouts; point at one, see the preview

  The picker stays open on its own: relax the hand that opened it (no need to hold the pose).
  pinch          choose the highlighted item — only a deliberate pinch counts: the index
                 finger must have been extended just before, so folding into a fist or
                 out of the opening pose never selects by accident
  lower hand     cancel
"""
from collections import deque

from . import features as F
from . import windows as W

TIMEOUT = 15.0
CELL = 0.11       # sideways hand movement per item, as a fraction of the camera width


def control_point(lm, aspect):
    """Knuckles of index + middle finger: barely move when you pinch."""
    x, y = (lm[5, :2] + lm[9, :2]) / 2
    return x / aspect, y


class Chooser:
    cols, rows = 1, 1

    def __init__(self, settings):
        self.p = settings["pointer"]
        self.active = False

    def start(self, t):
        self.active = True
        self.t0 = self.last_seen = t
        self.sel = self.default
        self.cursor = None
        self.anchor = None          # where the hand settled after opening = default item
        self.recent = deque()
        self.was_pinched = True     # require an open hand before a pinch counts
        self.ready_t = -9.0         # last time the hand was open with the index finger out

    default = 0

    def _axis(self, delta, n, start, cur):
        """Hand offset -> item index, relative to where the picker opened, with hysteresis
        so the border between two items doesn't flicker."""
        p = min(max(start + 0.5 + delta / self.cell(n), 0), n - 1e-4)
        if cur - 0.2 <= p < cur + 1.2:
            return cur, p
        return int(p), p

    def cell(self, n):
        return CELL

    def update(self, t, hands, aspect, prefer):
        """Returns None while choosing, ('pick', index) or ('cancel', None)."""
        if not self.active:
            return None
        hand = next((h for h in hands if h.label == prefer), hands[0] if hands else None)
        if hand is None:
            if t - self.last_seen > 1.0:
                self.active = False
                return ("cancel", None)
            return None
        self.last_seen = t
        u, v = control_point(hand.lm, aspect)
        if self.anchor is None:
            # the opening gesture is usually still in motion: wait until the hand settles
            self.recent.append((t, u, v))
            while self.recent and t - self.recent[0][0] > 0.15:
                self.recent.popleft()
            _, u0, v0 = self.recent[0]
            if abs(u - u0) + abs(v - v0) < 0.012 * (t - self.recent[0][0] > 0.1) or t - self.t0 > 0.8:
                self.anchor = (u, v)
            else:
                self.cursor = ((self.sel % self.cols + 0.5) / self.cols,
                               (self.sel // self.cols + 0.5) / self.rows)
                return None
        d0, d1 = self.default % self.cols, self.default // self.cols
        col, pc = self._axis(u - self.anchor[0], self.cols, d0, self.sel % self.cols)
        row, pr = self._axis((v - self.anchor[1]) * 1.4, self.rows, d1, self.sel // self.cols)
        self.sel = row * self.cols + col
        self.cursor = (pc / self.cols, pr / self.rows)
        r = F.pinch_ratio(hand.lm)
        if r > self.p["pinch_off"] and F.extended(hand.lm, "index"):
            self.ready_t = t
        pinched = r < self.p["pinch_on"] if not self.was_pinched else r < self.p["pinch_off"]
        fresh = pinched and not self.was_pinched and t - self.ready_t < 0.7
        self.was_pinched = pinched
        if fresh:
            self.active = False
            return ("pick", self.sel)
        if t - self.t0 > TIMEOUT:
            self.active = False
            return ("cancel", None)
        return None


class AppSwitcher(Chooser):
    default = 1   # like Cmd+Tab: start on the previous app

    def cell(self, n):
        return min(CELL, 0.75 / max(n, 1))   # many apps: smaller steps so all are reachable

    def start(self, t):
        self.apps = W.app_list()
        self.cols = max(len(self.apps), 1)
        self.default = 1 if len(self.apps) > 1 else 0
        super().start(t)

    def apply(self, idx):
        if 0 <= idx < len(self.apps):
            a = self.apps[idx]
            W.activate_app(a["pid"], a["bundle"])
            return a["name"]
        return None

    def view(self):
        return {"kind": "apps", "items": [(a["pid"], a["name"]) for a in self.apps],
                "sel": self.sel, "cursor": self.cursor}


class LayoutPicker(Chooser):
    cols, rows = 3, 3
    default = 4   # maximize in the middle

    def start(self, t):
        self.win = W.front_window()
        self.frame = self.win.frame if self.win else None
        self.vis = W.screens()[W.screen_index_for(self.frame) if self.frame else 0]["visible"]
        super().start(t)

    def target(self, idx):
        return W.layout_rect(self.vis, idx)

    def apply(self, idx):
        if not self.win:
            return None
        self.win.set_frame(*self.target(idx))
        self.win.raise_()
        return W.LAYOUTS[idx][0]

    def view(self):
        return {"kind": "layout", "names": [n for n, *_ in W.LAYOUTS], "sel": self.sel,
                "cursor": self.cursor, "preview": self.target(self.sel),
                "window": self.frame, "screen": self.vis}
