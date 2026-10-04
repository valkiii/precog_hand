"""Camera session that captures training samples for one gesture.

Hands-free: recording arms itself once your hand has been in view for a moment
(SPACE also starts it). Keys only matter for discard / finish / cancel.
"""
from collections import deque

import cv2
import numpy as np

from . import features as F
from .hud import HUD

WINDOW = "PRECOG"
AUTOSTART = 1.0   # seconds of visible hand before recording starts by itself


def bring_to_front():
    """OpenCV windows open behind the terminal on macOS; pull ours forward."""
    try:
        cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass


def _pick(hands, want):
    for h in hands:
        if want == "any" or h.label == want:
            return h
    return None


def _pair(hands):
    if len(hands) < 2:
        return None
    a, b = sorted(hands[:2], key=lambda h: F.palm_center(h.lm)[0])
    return np.stack([a.lm, b.lm])


def record(tracker, settings, name, kind, hand, total):
    """Returns a list of samples ([(t, lm), ...]) or None if cancelled."""
    hud = HUD(settings["preview_width"])
    samples = []
    buf = deque()
    phase, t_phase, cap = "idle", 0.0, []
    still_since = moving_since = seen_since = None
    note, note_t = "", -9.0
    last_seen = 0.0
    dark_since = None
    first = True

    def say(s, t):
        nonlocal note, note_t
        note, note_t = s, t

    while True:
        frame, hands, t = tracker.read()
        if frame is None:
            continue
        if kind == "motion2":
            cur = _pair(hands)
        else:
            h = _pick(hands, hand)
            cur = h.lm if h else None
        h = cur is not None
        if h:
            if t - last_seen > 0.3:
                buf.clear()
            last_seen = t
            buf.append((t, cur))
            while buf and t - buf[0][0] > 1.0:
                buf.popleft()
        speed = F.activity(list(buf)) if h else 0.0
        progress = None
        msg = ""

        if phase == "idle":
            if h:
                seen_since = seen_since or t
                progress = min(1, (t - seen_since) / AUTOSTART)
                msg = "Hand detected - starting..."
                if t - seen_since >= AUTOSTART:
                    phase, t_phase = ("count", t) if kind == "pose" else ("wait", t)
            else:
                seen_since = None
                msg = ("Raise BOTH hands in front of the camera" if kind == "motion2"
                       else "Raise your hand in front of the camera")

        if kind == "pose":
            if phase == "count":
                left = t_phase + 1.2 - t
                if t - last_seen > 0.25:
                    msg, t_phase = "No hand in view", t
                elif left > 0:
                    msg = f"Hold the pose...  {left:0.1f}"
                    progress = 1 - left / 1.2
                else:
                    phase, t_phase, cap = "rec", t, []
            if phase == "rec":
                msg = "Capturing"
                if h:
                    cap.append((t, cur))
                progress = min(1, (t - t_phase) / 0.6)
                if t - t_phase >= 0.6:
                    if len(cap) >= 5:
                        samples.append(cap)
                        say(f"Sample {len(samples)} saved - shift your hand slightly", t)
                    phase, t_phase = ("count", t + 0.9) if len(samples) < total else ("done", t)
        elif phase in ("wait", "ready", "rec"):
            if phase == "wait":
                msg = "Hold your hand still to arm"
                if h and speed < 0.6:
                    still_since = still_since or t
                    progress = min(1, (t - still_since) / 0.3)
                    if t - still_since > 0.3:
                        phase = "ready"
                else:
                    still_since = None
            gone = t - last_seen > 0.25
            if phase == "ready":
                msg = "GO - perform the movement"
                if gone:
                    phase = "wait"
                elif speed > 1.8:
                    phase, t_phase = "rec", t
                    cap = [f for f in buf if t - f[0] <= 0.12]
                    moving_since = t
            if phase == "rec":
                msg = "Recording"
                if gone:
                    phase = "wait"
                    say("Lost the hand - try again", t)
                elif h:
                    cap.append((t, cur))
                    if speed > 0.6:
                        moving_since = t
                    dur = t - t_phase
                    if t - moving_since > 0.18 and dur > 0.25:
                        cap = [f for f in cap if f[0] <= moving_since + 0.05]
                        if F.motion_amount(cap) < 1.0:
                            say("Movement too small - go bigger", t)
                        else:
                            samples.append(cap)
                            say(f"Sample {len(samples)} saved - hold still, then again", t)
                        phase, still_since = ("wait" if len(samples) < total else "done"), None
                    elif dur > 3.0:
                        phase, still_since = "wait", None
                        say("Too long - keep it under 3 s", t)

        if phase == "done":
            return samples

        # diagnostics win over everything else
        dark = frame.mean() < 4
        dark_since = (dark_since or t) if dark else None
        if dark_since and t - dark_since > 1.5:
            msg = "CAMERA IS BLACK - allow Camera for your terminal app"
        elif not h and hands and kind == "motion2":
            msg = "Only one hand visible - show both"
        elif not h and hands:
            msg = f"Seeing your {hands[0].label} hand - this gesture wants {hand}"
        elif t - note_t < 1.4 and phase != "rec":
            msg = note

        state = {"mode": "gesture", "recording": {
            "name": name, "kind": kind, "hand": hand, "phase": phase,
            "msg": msg, "count": len(samples), "total": total, "progress": progress}}
        cv2.imshow(WINDOW, hud.render(frame, hands, state))
        if first:
            bring_to_front()
            first = False
        key = cv2.waitKey(1) & 0xFF
        if key == 27:
            return None
        if key == ord(" ") and phase == "idle":
            phase, t_phase = ("count", t) if kind == "pose" else ("wait", t)
        elif key in (ord("r"), 8, 127) and samples:
            samples.pop()
            say("Discarded last sample", t)
        elif key in (13, 10) and samples:
            return samples
