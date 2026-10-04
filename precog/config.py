import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GESTURE_DIR = ROOT / "gestures"
MODEL_PATH = ROOT / "hand_landmarker.task"
SETTINGS_PATH = ROOT / "settings.json"

DEFAULTS = {
    "camera": "auto",         # or an index; "auto" skips cameras that give no image (iPhone)
    "width": 1280,
    "height": 720,
    "mirror": True,
    "preview": False,         # camera HUD window (toggle from the menu-bar item)
    "preview_width": 1100,
    "overlay": True,
    "overlay_widget": True,   # small corner panel with your hand skeleton, no video
    "overlay_hands_in_gesture_mode": True,
    "three_finger_swipe": True,  # trackpad-style desktop swipe with three fingers up
    # both hands, ten fingers spread wide, held still -> standby on / off
    "ten_fingers": {"enabled": True, "hold": 0.8, "gap": 0.46, "thumb": 0.85},
    "cooldown": 0.8,          # seconds between two fired gestures
    "pose_hold": 0.45,        # how long a pose must be held to fire
    "motion_traj_weight": 0.35,
    "pointer": {
        "hand": "Right",      # preferred hand for manipulation mode
        "margin": 0.18,       # camera border ignored when mapping to the screen
        "min_cutoff": 0.9,    # One Euro filter: lower = smoother, laggier
        "beta": 0.015,
        "pinch_on": 0.32,     # thumb-index distance / hand size (3D)
        "pinch_off": 0.48,
        "drag_px": 45,        # hand movement while pinched that turns a click into a drag
        "fling_speed": 2600,  # px/s on release that counts as a throw
    },
}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load_settings():
    s = copy.deepcopy(DEFAULTS)
    if SETTINGS_PATH.exists():
        _merge(s, json.loads(SETTINGS_PATH.read_text()))
    return s
