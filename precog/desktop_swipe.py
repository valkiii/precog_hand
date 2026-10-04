"""Three-finger desktop swipe — the trackpad gesture, in the air.

  three fingers up (index, middle, ring) and move sideways
      the desktop slides with your hand; release (lower the hand or change the pose)
      past ~1/3 of the way, or with a flick, to switch — otherwise it springs back
  three fingers + move up     Mission Control
  three fingers + move down   App Exposé
"""
from collections import deque

import numpy as np

from . import actions as A
from . import features as F
from . import spaces as S

extended = F.extended


def three_fingers(lm):
    return (extended(lm, "index") and extended(lm, "middle") and extended(lm, "ring")
            and not extended(lm, "pinky"))


class DesktopSwipe:
    START = 0.35        # hand-widths of movement before the slide starts
    SPAN = 4.0          # hand-widths for a full desktop
    COMMIT = 0.33       # fraction of the way that commits on release
    FLICK = 3.0         # hand-widths / s that commits regardless of distance
    VERTICAL = 1.6      # hand-widths up/down for Mission Control / App Exposé

    def __init__(self, settings):
        self.swipe = S.Swipe()
        self.reset()

    def reset(self):
        if self.swipe.active:
            self.swipe.end(commit=False)
        self.origin = None      # (t, x, y, scale) when the three-finger pose appeared
        self.seen_t = 0.0
        self.pose_since = None  # debounce: the pose must hold a moment (pinky flicker)
        self.hist = deque(maxlen=8)
        self.cooldown = 0.0
        self.event = None

    @property
    def active(self):
        """True while the hand is in a three-finger swipe (gesture recognition pauses)."""
        return self.origin is not None

    def update(self, t, hands):
        self.event = None
        hand = next((h for h in hands if three_fingers(h.lm)), None)
        if hand is None:
            self.pose_since = None
            if self.origin and t - self.seen_t > 0.15:   # pose released
                self._release(t)
            return self._state()
        self.pose_since = self.pose_since or t
        if self.origin is None and t - self.pose_since < 0.15:
            return self._state()
        self.seen_t = t
        c = F.palm_center(hand.lm)
        s = F.hand_scale(hand.lm)
        if self.origin is None:
            if t < self.cooldown:
                return self._state()
            self.origin = (t, c[0], c[1], s)
            self.hist.clear()
        _, x0, y0, s0 = self.origin
        dx, dy = (c[0] - x0) / s0, (c[1] - y0) / s0
        self.hist.append((t, dx))
        if not self.swipe.active:
            if abs(dy) > self.VERTICAL and abs(dy) > 2 * abs(dx):
                up = dy < 0
                A.run("mission_control" if up else "app_windows")
                self.event = "mission control" if up else "app windows"
                self.origin = None
                self.cooldown = t + 1.0
            elif abs(dx) > self.START and abs(dx) > abs(dy):
                self.swipe.begin()
        if self.swipe.active:
            sign = 1 if dx > 0 else -1
            self.swipe.move(sign * max(abs(dx) - self.START, 0) / self.SPAN)
        return self._state()

    def _release(self, t):
        if self.swipe.active:
            p = self.swipe.progress
            v = 0.0
            if len(self.hist) >= 2:
                (t0, a), (t1, b) = self.hist[0], self.hist[-1]
                v = (b - a) / max(t1 - t0, 1e-3)
            commit = abs(p) >= self.COMMIT or (abs(v) >= self.FLICK and np.sign(v) == np.sign(p))
            self.swipe.end(commit)
            self.event = ("next desktop" if p < 0 else "previous desktop") if commit else "swipe cancelled"
            self.cooldown = t + 0.4
        self.origin = None

    def _state(self):
        return {"sliding": self.swipe.active, "progress": self.swipe.progress}
