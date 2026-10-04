import os
import time
from contextlib import contextmanager
from dataclasses import dataclass

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

from .config import MODEL_PATH


@contextmanager
def _quiet_stderr():
    """MediaPipe's C++ startup logs write straight to fd 2; mute them briefly."""
    saved = os.dup(2)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 2)
    try:
        yield
    finally:
        os.dup2(saved, 2)
        os.close(null)
        os.close(saved)


@dataclass
class Hand:
    label: str          # "Left" / "Right" — the user's real hand
    lm: np.ndarray      # (21, 3); x in [0, aspect], y in [0, 1]
    score: float


def camera_names():
    try:
        import AVFoundation as AV
        return [str(d.localizedName()) for d in AV.AVCaptureDevice.devicesWithMediaType_(AV.AVMediaTypeVideo)]
    except Exception:
        return []


def _open(index, settings):
    cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings["width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings["height"])
    return cap if cap.isOpened() else None


def _delivers_images(cap, seconds=2.5):
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        ok, f = cap.read()
        if ok and f is not None and f.mean() > 3:
            return True
    return False


def open_camera(settings, verbose=True):
    """settings['camera']: an index, or "auto" = first camera that delivers real images
    (Continuity Camera iPhones show up as extra cameras and can take index 0)."""
    names = camera_names()
    want = settings["camera"]
    if want != "auto":
        cap = _open(int(want), settings)
        if cap is None:
            raise RuntimeError(f"Cannot open camera #{want}. Cameras: {names or 'none found'}")
        return cap, int(want)
    fallback = None
    for i in range(max(len(names), 1) + 1):
        cap = _open(i, settings)
        if cap is None:
            continue
        if _delivers_images(cap):
            if verbose:
                print(f"camera #{i}" + (f" ({names[i]})" if i < len(names) else "") + " selected")
            if fallback:
                fallback[0].release()
            return cap, i
        if fallback is None:
            fallback = (cap, i)
        else:
            cap.release()
    if fallback is None:
        raise RuntimeError("Cannot open any camera. Grant camera access to your terminal in "
                           "System Settings > Privacy & Security > Camera.")
    if verbose:
        print(f"! no camera delivered an image (cameras: {names}); using #{fallback[1]}. "
              "Check camera permission for your terminal app.")
    return fallback


class Tracker:
    """Camera capture + MediaPipe hand landmarks."""

    def __init__(self, settings, verbose=True):
        self.mirror = settings["mirror"]
        self.cap, self.index = open_camera(settings, verbose)
        opts = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(MODEL_PATH)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=2,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.4,
            min_tracking_confidence=0.5,
        )
        with _quiet_stderr():
            self.landmarker = vision.HandLandmarker.create_from_options(opts)
        self._frames = 0
        self._t0 = time.monotonic()
        self._last_ms = -1

    def read(self):
        ok, frame = self.cap.read()
        if not ok:
            return None, [], time.monotonic()
        if self.mirror:
            frame = cv2.flip(frame, 1)
        t = time.monotonic()
        ms = max(int((t - self._t0) * 1000), self._last_ms + 1)
        self._last_ms = ms
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        self._frames += 1
        if self._frames <= 3:  # first inferences print graph warnings
            with _quiet_stderr():
                res = self.landmarker.detect_for_video(img, ms)
        else:
            res = self.landmarker.detect_for_video(img, ms)
        h, w = frame.shape[:2]
        aspect = w / h
        hands = []
        for lms, hd in zip(res.hand_landmarks, res.handedness):
            arr = np.array([[p.x * aspect, p.y, p.z * aspect] for p in lms], np.float32)
            label = hd[0].category_name
            if not self.mirror:  # MediaPipe assumes a mirrored (selfie) image
                label = "Left" if label == "Right" else "Right"
            hands.append(Hand(label, arr, hd[0].score))
        return frame, hands, t

    def close(self):
        self.cap.release()
        self.landmarker.close()
