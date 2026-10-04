"""Gestures live as one JSON file each in gestures/."""
import json
import re
from dataclasses import asdict, dataclass, field

import numpy as np

from .config import GESTURE_DIR


@dataclass
class Gesture:
    name: str
    kind: str                     # "pose" (held hand shape) or "motion" (movement)
    hand: str = "any"             # "Left", "Right" or "any"
    action: str = "none"
    args: dict = field(default_factory=dict)
    sensitivity: float = 1.0      # >1 = easier to trigger, <1 = stricter
    samples: list = field(default_factory=list)  # [{"t": [...], "lm": [[[x,y,z]*21]...]}]

    def sample_frames(self):
        """Samples as lists of (t, landmarks ndarray)."""
        return [[(t, np.asarray(lm, np.float32)) for t, lm in zip(s["t"], s["lm"])]
                for s in self.samples]


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "gesture"


def path_for(name):
    return GESTURE_DIR / f"{slug(name)}.json"


def frames_to_sample(frames):
    t0 = frames[0][0]
    return {"t": [round(t - t0, 4) for t, _ in frames],
            "lm": [np.round(lm, 5).tolist() for _, lm in frames]}


def save(g: Gesture):
    GESTURE_DIR.mkdir(exist_ok=True)
    path_for(g.name).write_text(json.dumps(asdict(g)))


def load_all():
    GESTURE_DIR.mkdir(exist_ok=True)
    out = []
    for p in sorted(GESTURE_DIR.glob("*.json")):
        try:
            out.append(Gesture(**json.loads(p.read_text())))
        except Exception as e:  # keep going if one file is broken
            print(f"! skipping {p.name}: {e}")
    return out


def get(name):
    for g in load_all():
        if g.name == name or slug(g.name) == slug(name):
            return g
    return None


def remove(name):
    p = path_for(name)
    if p.exists():
        p.unlink()
        return True
    return False
