"""MANIPULATION mode — the Minority Report part.

  move hand            cursor follows your knuckles
  pinch + release      click (the cursor freezes while you pinch); twice quickly = double-click
  middle-finger pinch  right-click
  pinch + move         grab the window under the cursor and drag it in mid-air
  2nd hand pinches     stretch / shrink the grabbed window (spread hands apart)
  release with a fling throw it: left/right = other display, up = maximize, down = minimize
  drag to an edge      snap preview: left/right edge = half screen, top edge = maximize
"""
import math
from collections import deque

from . import actions as A
from . import features as F
from . import windows as W


class OneEuro:
    def __init__(self, min_cutoff, beta, d_cutoff=1.0):
        self.mc, self.beta, self.dc = min_cutoff, beta, d_cutoff
        self.x = self.dx = self.t = None

    @staticmethod
    def _a(cutoff, dt):
        r = 2 * math.pi * cutoff * dt
        return r / (r + 1)

    def __call__(self, x, t):
        if self.t is None:
            self.x, self.dx, self.t = x, 0.0, t
            return x
        dt = max(t - self.t, 1e-3)
        dx = (x - self.x) / dt
        self.dx += self._a(self.dc, dt) * (dx - self.dx)
        cutoff = self.mc + self.beta * abs(self.dx)
        self.x += self._a(cutoff, dt) * (x - self.x)
        self.t = t
        return self.x


class Pointer:
    def __init__(self, settings):
        self.p = settings["pointer"]
        self.reset()

    def reset(self):
        self.fx = OneEuro(self.p["min_cutoff"], self.p["beta"])
        self.fy = OneEuro(self.p["min_cutoff"], self.p["beta"])
        self.pinched = {}
        self.pinch_t0 = None
        self.pinch_pos0 = None
        self.pinch_dead = False     # pinch started over nothing: no click, no grab
        self.grab = None            # {"win", "offset", "frame"}
        self.snap = None            # target rect while dragging into a screen edge
        self.stretch = None         # {"d0", "frame0", "mid0"}
        self.history = deque(maxlen=12)
        self.last_click = (-9.0, (0, 0))
        self.right_pinched = False
        self.cursor = None
        self.event = None
        self._last_set = 0.0

    # hands -> camera coordinates in [0, 1]
    @staticmethod
    def _cam_point(lm, aspect):
        x, y = (lm[5, :2] + lm[9, :2]) / 2   # index + middle knuckles
        return x / aspect, y

    def _click_pos(self, t):
        """Where the cursor was just before the fingers started closing."""
        for ht, hx, hy in reversed(self.history):
            if t - ht >= 0.12:
                return hx, hy
        return self.history[0][1:] if self.history else self.cursor

    def _snap_zone(self, x, y):
        bx, by, bw, bh = W.desktop_bounds()
        idx = W.screen_at(x, y)
        vis = W.screens()[idx]["visible"]
        if x <= bx + 6:
            return W.layout_rect(vis, 3)   # left half
        if x >= bx + bw - 7:
            return W.layout_rect(vis, 5)   # right half
        if y <= by + 6:
            return W.layout_rect(vis, 4)   # maximize
        return None

    def _to_screen(self, u, v):
        m = self.p["margin"]
        bx, by, bw, bh = W.desktop_bounds()
        u = min(max((u - m) / (1 - 2 * m), 0), 1)
        v = min(max((v - m) / (1 - 2 * m), 0), 1)
        return bx + u * (bw - 1), by + v * (bh - 1)

    def _is_pinched(self, label, lm):
        r = F.pinch_ratio(lm)
        if F.pinch_ratio(lm, 12) < 0.8 * r:
            r = 9.0   # thumb is on the middle finger: that's a right-click, not this
        was = self.pinched.get(label, False)
        now = r < self.p["pinch_on"] if not was else r < self.p["pinch_off"]
        self.pinched[label] = now
        return now, r

    def update(self, t, hands, aspect):
        """Returns a state dict for the HUD / overlay; self.event holds any one-shot event."""
        self.event = None
        if not hands:
            if self.grab:
                self._release(t, fling=False)
            self.pinched.clear()
            return {"cursor": None}
        pref = [h for h in hands if h.label == self.p["hand"]]
        main = pref[0] if pref else hands[0]
        other = next((h for h in hands if h is not main), None)

        u, v = self._cam_point(main.lm, aspect)
        sx, sy = self._to_screen(u, v)
        x, y = self.fx(sx, t), self.fy(sy, t)
        ratio_now = F.pinch_ratio(main.lm)
        if self.cursor and not self.grab and self.p["pinch_on"] < ratio_now < 0.6:
            # fingers closing in on a click: slow the cursor right down
            x = self.cursor[0] + (x - self.cursor[0]) * 0.25
            y = self.cursor[1] + (y - self.cursor[1]) * 0.25
        self.cursor = (x, y)
        self.history.append((t, x, y))
        pinched, ratio = self._is_pinched(main.label, main.lm)
        other_pinched = self._is_pinched(other.label, other.lm)[0] if other else False

        if pinched and self.pinch_t0 is None:          # pinch start
            self.pinch_t0, self.pinch_pos0, self.pinch_dead = t, self._click_pos(t), False
        elif pinched and not self.pinch_dead:          # pinch held
            # like a mouse button: it only becomes a drag once the hand really moves
            if not self.grab and math.dist(self.pinch_pos0, (x, y)) > self.p["drag_px"]:
                self._start_grab(*self.pinch_pos0)
            if self.grab:
                self._drag(t, x, y, other if other_pinched else None, aspect)
        elif not pinched and self.pinch_t0 is not None:  # pinch released
            if self.grab:
                self._release(t, fling=True)
            elif not self.pinch_dead:
                lt, lp = self.last_click
                double = t - lt < 0.5 and math.dist(lp, self.pinch_pos0) < 30
                A.click(*self.pinch_pos0, count=2 if double else 1)
                self.event = "double" if double else "click"
                self.last_click = (t, self.pinch_pos0)
            self.pinch_t0 = None

        # right-click: middle finger to thumb (index stays apart)
        r_mid = F.pinch_ratio(main.lm, 12)
        r_pinch = (r_mid < self.p["pinch_on"] and r_mid < 0.8 * F.pinch_ratio(main.lm)
                   and F.extended(main.lm, "index"))   # a fist folds the index: not a right-click
        if r_pinch and not self.right_pinched and self.pinch_t0 is None:
            A.click(x, y, right=True)
            self.event = "right"
        self.right_pinched = r_mid < self.p["pinch_off"]

        if self.pinch_t0 is not None and not self.grab:
            x, y = self.pinch_pos0                     # frozen while pinching
            self.cursor = (x, y)
        if not self.grab:
            A.mouse_move(x, y)

        return {
            "cursor": (x, y),
            "pinch": pinched,
            "pinch_ratio": ratio,
            "grab": self.grab["win"].frame if self.grab else None,
            "snap": self.snap,
            "stretch": self.stretch is not None,
            "second": self._to_screen(*self._cam_point(other.lm, aspect)) if other else None,
        }

    def _start_grab(self, x, y):
        win = W.window_at(x, y)
        if win is None or win.frame is None:
            self.pinch_dead = True
            return
        f = win.frame
        win.raise_()
        self.grab = {"win": win, "offset": (x - f[0], y - f[1]), "frame": f}
        self.event = "grab"

    def _drag(self, t, x, y, other, aspect):
        if t - self._last_set < 1 / 40:
            return
        self._last_set = t
        win = self.grab["win"]
        if other is not None:
            ox, oy = self._to_screen(*self._cam_point(other.lm, aspect))
            d = math.dist((x, y), (ox, oy))
            mid = ((x + ox) / 2, (y + oy) / 2)
            if self.stretch is None:
                self.stretch = {"d0": max(d, 40), "frame0": win.frame or self.grab["frame"],
                                "mid0": mid}
                self.event = "stretch"
            s = self.stretch
            k = max(d, 40) / s["d0"]
            fx, fy, fw, fh = s["frame0"]
            w, h = max(fw * k, 240), max(fh * k, 160)
            cx = fx + fw / 2 + (mid[0] - s["mid0"][0])
            cy = fy + fh / 2 + (mid[1] - s["mid0"][1])
            win.set_frame(cx - w / 2, cy - h / 2, w, h)
            return
        if self.stretch is not None:   # second hand let go: re-anchor the drag
            f = win.frame
            if f:
                self.grab["offset"] = (x - f[0], y - f[1])
            self.stretch = None
        self.snap = self._snap_zone(x, y)
        if self.snap is None:
            ox, oy = self.grab["offset"]
            win.move(x - ox, y - oy)

    def _release(self, t, fling):
        win = self.grab["win"]
        snap = self.snap
        self.grab = None
        self.snap = None
        self.stretch = None
        self.pinch_t0 = None
        self.event = "drop"
        if snap:
            win.set_frame(*snap)
            self.event = "snap"
            return
        if not fling or len(self.history) < 3:
            return
        t0, x0, y0 = next(((ht, hx, hy) for ht, hx, hy in self.history if t - ht <= 0.15),
                          self.history[0])
        t1, x1, y1 = self.history[-1]
        dt = max(t1 - t0, 1e-3)
        vx, vy = (x1 - x0) / dt, (y1 - y0) / dt
        fs = self.p["fling_speed"]
        try:
            if abs(vx) > fs and abs(vx) > abs(vy):
                f = win.frame
                idx = W.neighbour_screen(W.screen_index_for(f), "right" if vx > 0 else "left")
                if idx is not None:
                    A.move_to_screen(win, idx)
                    self.event = "throw"
            elif vy < -fs:
                vis = W.screens()[W.screen_index_for(win.frame)]["visible"]
                win.set_frame(*vis)
                self.event = "throw_up"
            elif vy > fs:
                win.minimize()
                self.event = "throw_down"
        except Exception:
            pass
