"""Ten spread fingers (both hands, held still) toggles STANDBY: Precog ignores every gesture
until you show ten fingers again. Wider than a normal open hand, so swipes / expand don't count."""
from collections import deque

import numpy as np

from . import features as F


def spread_wide(lm, gap, thumb):
    s = F.hand_scale(lm)
    if not all(F.extended(lm, f) for f in ("index", "middle", "ring", "pinky")):
        return False
    gaps = [np.linalg.norm(lm[a, :2] - lm[b, :2]) / s for a, b in ((8, 12), (12, 16), (16, 20))]
    return np.mean(gaps) >= gap and np.linalg.norm(lm[4, :2] - lm[5, :2]) / s >= thumb


class TenFingers:
    def __init__(self, settings):
        c = settings["ten_fingers"]
        self.hold, self.gap, self.thumb = c["hold"], c["gap"], c["thumb"]
        self.since = None
        self.latched = False        # fired; wait for the pose to go away before re-arming
        self.quiet_until = 0.0
        self.palms = deque(maxlen=10)
        self.progress = 0.0

    def block(self, t, seconds=2.0):
        """Ignore the pose for a moment (e.g. right after another gesture fired)."""
        self.quiet_until = max(self.quiet_until, t + seconds)

    def update(self, t, hands):
        """True on the frame the toggle should happen."""
        ok = len(hands) == 2 and all(spread_wide(h.lm, self.gap, self.thumb) for h in hands)
        if ok:
            c = np.mean([F.palm_center(h.lm) / F.hand_scale(h.lm) for h in hands], 0)
            self.palms.append((t, c))
            t0, c0 = self.palms[0]
            if t - t0 > 0.1 and np.linalg.norm(c - c0) / (t - t0) > 1.5:   # hands moving
                ok = False
        if not ok:
            self.since = None
            self.progress = 0.0
            self.palms.clear()
            self.latched = False
            return False
        if self.latched or t < self.quiet_until:
            self.progress = 0.0
            return False
        self.since = self.since or t
        self.progress = min(1.0, (t - self.since) / self.hold)
        if self.progress >= 1.0:
            self.latched = True
            self.progress = 0.0
            return True
        return False
