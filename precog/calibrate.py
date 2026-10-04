"""./precog-run calibrate — fit the ten-finger standby trigger to your hands.

Measures how wide you spread your fingers, compares it with your normal open hand (taken
from your recorded swipes / expand) and saves a threshold halfway between the two."""
import json
import time

import numpy as np

from . import features as F
from . import store
from .config import SETTINGS_PATH


def _metrics(lm):
    s = F.hand_scale(lm)
    gap = np.mean([np.linalg.norm(lm[a, :2] - lm[b, :2]) / s for a, b in ((8, 12), (12, 16), (16, 20))])
    return gap, np.linalg.norm(lm[4, :2] - lm[5, :2]) / s


def _normal_open_hands():
    out = []
    for g in store.load_all():
        if g.kind == "pose":
            continue
        for smp in g.sample_frames():
            for _, lm in smp:
                for h in (lm if lm.ndim == 3 else [lm]):
                    if all(F.extended(h, f) for f in ("index", "middle", "ring", "pinky")):
                        out.append(_metrics(h))
    return np.array(out)


def run(settings):
    from .tracker import Tracker
    normal = _normal_open_hands()
    if len(normal):
        ref_gap, ref_thumb = np.percentile(normal[:, 0], 99), np.percentile(normal[:, 1], 99)
        print(f"Your normal open hand (from {len(normal)} recorded frames): gap up to {ref_gap:.2f}, thumb up to {ref_thumb:.2f}")
    else:
        ref_gap, ref_thumb = 0.45, 0.90
        print("No recorded open-hand gestures yet; using typical values.")
    tr = Tracker(settings)
    try:
        print("\nRaise BOTH hands, spread all ten fingers as wide as is comfortable, and hold still.")
        for k in (3, 2, 1):
            print(f"  {k}...", flush=True)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 1:
                tr.read()
        print("  measuring (3 s)", flush=True)
        got = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 3:
            _, hands, _ = tr.read()
            if len(hands) == 2:
                got += [_metrics(h.lm) for h in hands]
    finally:
        tr.close()
    if len(got) < 20:
        print("Didn't see two hands long enough — try again with both hands clearly in view.")
        return
    got = np.array(got)
    my_gap, my_thumb = np.percentile(got[:, 0], 20), np.percentile(got[:, 1], 20)
    print(f"Your spread: gap {my_gap:.2f}, thumb {my_thumb:.2f}")
    if my_gap <= ref_gap + 0.03:
        print("! Your spread is about the same as your normal open hand, so the trigger would be"
              " unreliable. Try again spreading wider (especially ring and pinky).")
        return
    gap = round(float((my_gap + ref_gap) / 2), 3)
    thumb = round(float(min((my_thumb + ref_thumb) / 2, my_thumb - 0.05)), 3)
    data = json.loads(SETTINGS_PATH.read_text()) if SETTINGS_PATH.exists() else {}
    data.setdefault("ten_fingers", {}).update({"gap": gap, "thumb": thumb})
    SETTINGS_PATH.write_text(json.dumps(data, indent=2))
    print(f"Saved to settings.json: gap >= {gap}, thumb >= {thumb}. Restart Precog to use it.")
