"""./precog-run doctor — checks camera, hand tracking, permissions and the overlay."""
import time

import cv2
import numpy as np


def _frames(tracker, n, pump=None):
    got, bright, hands = 0, [], 0
    t0 = time.monotonic()
    for _ in range(n):
        frame, hs, _ = tracker.read()
        if frame is not None:
            got += 1
            bright.append(float(frame.mean()))
            hands += bool(hs)
        if pump:
            pump(frame)
    fps = got / max(time.monotonic() - t0, 1e-3)
    return got, (np.mean(bright[-10:]) if bright else 0.0), hands, fps


def run(settings):
    from . import windows as W
    from .tracker import Tracker
    print("PRECOG DOCTOR — keep one hand raised in front of the camera.\n")
    from .tracker import camera_names
    print("1. cameras found:", camera_names() or "none", flush=True)
    t0 = time.monotonic()
    try:
        tr = Tracker(settings)
    except Exception as e:
        print(f"   FAIL: {e}")
        return
    print(f"   using camera #{tr.index}, opened in {time.monotonic() - t0:.1f}s")
    got, br, hands, fps = _frames(tr, 90)
    print(f"   without window: {got}/90 frames, {fps:.0f} fps, brightness {br:.0f}/255, "
          f"hand seen in {hands} frames")
    shown = {}

    def pump(frame):
        if frame is not None:
            cv2.imshow("PRECOG doctor", cv2.resize(frame, (480, 270)))
            shown["x"] = True
        cv2.waitKey(1)
    got2, br2, hands2, fps2 = _frames(tr, 90, pump)
    cv2.destroyAllWindows()
    cv2.waitKey(1)
    print(f"   with window:    {got2}/90 frames, {fps2:.0f} fps, brightness {br2:.0f}/255, "
          f"hand seen in {hands2} frames")
    tr.close()
    if br < 3 and br2 >= 3:
        print("   -> the camera only delivers images while a window is open (fix needed in Precog)")
    elif br < 3:
        print("   -> camera images are black: allow Camera for this terminal app in System Settings >"
              " Privacy & Security > Camera, then restart the terminal")
    elif hands == 0 and hands2 == 0:
        print("   -> camera works but no hand detected: more light, hand 40-80 cm from the camera")
    else:
        print("   -> camera OK")

    print("\n2. accessibility (moving windows, key presses):",
          "OK" if W.trusted() else "NOT granted — System Settings > Privacy & Security > Accessibility")

    print("3. screen overlay ...", flush=True)
    from .overlay import Overlay
    ov = Overlay(True)
    time.sleep(2.0)
    alive = ov.proc.is_alive()
    ov.send({"banner": {"title": "PRECOG DOCTOR", "sub": "overlay OK"}})
    time.sleep(1.5)
    ov.close()
    print("   OK (you should have seen 'PRECOG DOCTOR' on screen)" if alive else "   FAIL: overlay process died")
