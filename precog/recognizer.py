"""Matches live hand data against recorded gestures.

pose    -> current hand shape vs. recorded shapes, must be held still for `pose_hold`.
motion  -> recent movement window vs. recorded movements using DTW on hand shape + path.
motion2 -> the same for two-hand movements (shapes + gap between hands + midpoint path).
Thresholds are calibrated automatically from the spread between recorded samples.
"""
from collections import deque

import numpy as np

from . import features as F


class Compiled:
    """A gesture ready for matching. Samples that disagree with the rest (a misread hand,
    a much slower take) are dropped, and the threshold comes from the remaining spread."""

    def __init__(self, g, traj_weight):
        self.g = g
        self.name = g.name
        self.kind = g.kind
        samples = [s for s in g.sample_frames() if len(s) >= 2]
        if g.kind == "pose":
            templates = [np.mean([F.pose_vec(lm) for _, lm in s], 0) for s in samples]
            dist, slack, default, lo, hi, k, c = F.pose_dist, 0.25, 0.30, 0.18, 0.45, 1.6, 0.10
        else:
            templates = [F.seq_features(s) for s in samples]
            dist = lambda a, b: F.seq_dist(a, b, traj_weight)  # noqa: E731
            slack, default, lo, hi, k, c = 0.5, 0.9, 0.35, 1.2, 1.4, 0.15
        loo = self._loo(templates, dist)
        keep = list(range(len(templates)))
        if loo:
            med = float(np.median(loo))
            good = [i for i, d in enumerate(loo) if d <= max(3 * med, med + slack)]
            if len(good) >= 2:
                keep = good
        self.dropped = len(templates) - len(keep)
        self.spread = max(loo[i] for i in keep) if loo else None
        self.templates = [templates[i] for i in keep]
        if g.kind != "pose":
            self.durations = [samples[i][-1][0] - samples[i][0][0] for i in keep]
            self.paths = [F.motion_amount(samples[i]) for i in keep]
        base = default if self.spread is None else float(np.clip(self.spread * k + c, lo, hi))
        self.threshold = base * g.sensitivity
        # samples this far apart mean the recording itself is unreliable
        self.inconsistent = self.dropped > 0 or (self.spread or 0) > (0.3 if g.kind == "pose" else 0.8)

    @staticmethod
    def _loo(T, dist):
        """Each sample's distance to its nearest other sample."""
        if len(T) < 2:
            return None
        return [min(dist(T[i], T[j]) for j in range(len(T)) if j != i) for i in range(len(T))]


class Recognizer:
    def __init__(self, gestures, settings):
        self.s = settings
        self.tw = settings["motion_traj_weight"]
        self.gestures = [Compiled(g, self.tw) for g in gestures if g.samples]
        self.buffers = {}       # hand label -> deque[(t, lm)]
        self.pair = deque()     # both hands -> deque[(t, (2, 21, 3))], left-most first
        self.pair_seen = -9.0
        self.has_pair = any(g.kind == "motion2" for g in self.gestures)
        self.last_seen = {}
        self.hold = {}          # (gesture, hand) -> hold start time
        self.latched = set()    # poses that already fired and are still held
        self.cooldown_until = 0.0
        self.scores = {}        # gesture name -> (distance, threshold)
        self._frame = 0

    def buffer(self, label):
        return self.buffers.get(label, ())

    def reset(self):
        self.buffers.clear()
        self.pair.clear()
        self.hold.clear()

    def update(self, t, hands, allowed=None):
        """Feed one frame. Returns (Compiled gesture, hand label) when one fires."""
        self._frame += 1
        for h in hands:
            if t - self.last_seen.get(h.label, 0) > 0.35:
                self.buffers[h.label] = deque()
            buf = self.buffers.setdefault(h.label, deque())
            buf.append((t, h.lm))
            while buf and t - buf[0][0] > 3.5:
                buf.popleft()
            self.last_seen[h.label] = t

        if len(hands) >= 2:
            a, b = sorted(hands[:2], key=lambda h: F.palm_center(h.lm)[0])
            if t - self.pair_seen > 0.35:
                self.pair = deque()
            self.pair.append((t, np.stack([a.lm, b.lm])))
            while self.pair and t - self.pair[0][0] > 3.5:
                self.pair.popleft()
            self.pair_seen = t
        # with two-hand gestures defined, two visible hands means "not a one-hand swipe"
        two_handed = self.has_pair and t - self.pair_seen < 0.5

        if t < self.cooldown_until:
            return None
        present = {h.label: h for h in hands}
        best = None
        scores = {}
        for cg in self.gestures:
            if allowed is not None and cg.g.action not in allowed:
                continue
            if cg.kind == "motion2":
                labels = ["both"] if t - self.pair_seen < 0.1 else []
            elif cg.kind == "motion" and two_handed:
                labels = []
            else:
                labels = list(present) if cg.g.hand == "any" else [cg.g.hand] * (cg.g.hand in present)
            for label in labels:
                if cg.kind == "pose":
                    d = self._pose(cg, label, present[label], t)
                else:
                    if self._frame % 2:  # DTW every other frame is plenty
                        continue
                    d = self._motion(cg, list(self.pair) if label == "both" else list(self.buffer(label)), t)
                if d is None:
                    continue
                prev = scores.get(cg.name)
                if prev is None or d < prev[0]:
                    scores[cg.name] = (d, cg.threshold)
                if d < cg.threshold and (cg.kind != "pose" or self._held(cg, label, t)):
                    r = d / cg.threshold
                    if best is None or r < best[0]:
                        best = (r, cg, label)
        self.scores.update(scores)
        for name in list(self.scores):
            if name not in scores and self._frame % 2 == 0:
                self.scores.pop(name)
        if best:
            _, cg, label = best
            self.cooldown_until = t + self.s["cooldown"]
            if cg.kind == "pose":
                self.latched.add((cg.name, label))
            elif label == "both":
                self.pair = deque()
                self.buffers.clear()
            else:
                self.buffers[label] = deque()
            self.hold.clear()
            return cg, label
        return None

    def _pose(self, cg, label, hand, t):
        d = min(F.pose_dist(F.pose_vec(hand.lm), tpl) for tpl in cg.templates)
        key = (cg.name, label)
        still = F.palm_speed(list(self.buffer(label))[-8:]) < 1.2
        if d < cg.threshold and still:
            self.hold.setdefault(key, t)
        else:
            self.hold.pop(key, None)
        if d > cg.threshold * 1.25:
            self.latched.discard(key)
        return d

    def _held(self, cg, label, t):
        key = (cg.name, label)
        return (key not in self.latched and key in self.hold
                and t - self.hold[key] >= self.s["pose_hold"])

    def _motion(self, cg, buf, t):
        if len(buf) < 6:
            return None
        best = None
        for tpl, dur, plen in zip(cg.templates, cg.durations, cg.paths):
            for k in (0.7, 1.0, 1.4):
                win = [f for f in buf if f[0] >= t - dur * k]
                if len(win) < 5 or F.motion_amount(win) < 0.5 * plen:
                    continue
                d = F.seq_dist(F.seq_features(win), tpl, self.tw)
                if best is None or d < best:
                    best = d
        return best


def conflicts(target, others, settings):
    """Distances from a gesture's samples to other gestures of the same kind."""
    tw = settings["motion_traj_weight"]
    a = Compiled(target, tw)
    out = []
    for g in others:
        if g.name == target.name or g.kind != target.kind or not g.samples:
            continue
        b = Compiled(g, tw)
        if a.kind == "pose":
            d = min(F.pose_dist(x, y) for x in a.templates for y in b.templates)
        else:
            d = min(F.seq_dist(x, y, tw) for x in a.templates for y in b.templates)
        if d < max(a.threshold, b.threshold):
            out.append((g.name, d))
    return out
