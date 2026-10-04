"""The live loop: camera -> recognizer / manipulation / pickers -> actions, overlay and HUD.

By default there is no camera window: feedback lives in the screen overlay (corner widget,
banners, pickers) and the menu-bar item. The camera HUD can be toggled from that menu.
"""
import threading
import time

import cv2

from . import actions as A
from . import features as F
from . import store
from . import windows as W
from .hud import AMBER, CYAN, GREEN, HUD, MAGENTA, RED
from .overlay import Overlay
from .pointer import Pointer
from .recognizer import Recognizer
from .desktop_swipe import DesktopSwipe
from .recorder import WINDOW, bring_to_front
from .standby import TenFingers
from .switcher import AppSwitcher, LayoutPicker
from .tracker import Tracker

POINTER_EVENTS = {"grab": "TARGET LOCKED", "stretch": "STRETCH", "throw": "THROWN",
                  "throw_up": "MAXIMIZED", "throw_down": "MINIMIZED", "click": "CLICK",
                  "double": "DOUBLE CLICK", "right": "RIGHT CLICK",
                  "snap": "SNAPPED"}

MODE_NAMES = {"pointer": "Manipulation", "gesture": "Gesture mode", "standby": "Standby",
              "switcher": "App switcher", "layout": "Window layout"}
MODE_COLORS = {"pointer": AMBER, "gesture": CYAN, "standby": RED, "switcher": MAGENTA,
               "layout": GREEN}
HINTS = {
    "pointer": "PINCH = click  ·  MIDDLE-FINGER PINCH = right-click  ·  PINCH + MOVE = drag window  ·  "
               "EXIT: pointer pose again, or lower your hands",
    "switcher": "LOCKED OPEN — relax your hand  ·  MOVE ← → = choose app  ·  PINCH = open it  ·  LOWER HAND = cancel",
    "layout": "LOCKED OPEN — relax your hands  ·  POINT at a layout  ·  PINCH = apply  ·  LOWER HAND = cancel",
}
POINTER_IDLE_EXIT = 2.0   # seconds without hands before manipulation mode ends by itself


def pretty(name):
    return name.replace("_", " ")


def run(settings, dry_run=False, preview=None, overlay=True):
    preview = settings["preview"] if preview is None else preview
    gestures = store.load_all()
    rec = Recognizer(gestures, settings)
    tracker = Tracker(settings)
    hud = HUD(settings["preview_width"])
    ptr = Pointer(settings)
    pickers = {"switcher": AppSwitcher(settings), "layout": LayoutPicker(settings)}
    prefer = settings["pointer"]["hand"]
    desk = DesktopSwipe(settings) if settings["three_finger_swipe"] else None
    ten = TenFingers(settings) if settings["ten_fingers"]["enabled"] else None
    no_hands_since = None
    ov = Overlay(settings["overlay_widget"]) if overlay and settings["overlay"] else None
    if ov:
        W.OWN_PIDS.add(ov.pid)
    elif not preview:
        print("! No overlay and no camera window: you won't see any feedback.")
    trusted = W.trusted()
    mode = "gesture"
    gesture_rows = [{"name": g.name, "action": g.action} for g in gestures]
    if not gestures:
        print("No gestures recorded yet — run `./precog-run new`.")
    print(f"Precog running with {len(gestures)} gestures.")
    print("Use the ◉ menu-bar item to show the camera, change mode or quit (or Ctrl-C here).")

    def announce(title, sub="", at=None, color=CYAN):
        hud.toast(title, sub, at, color)
        if ov:
            ov.send({"banner": {"title": title.upper(), "sub": sub}})

    def set_mode(m, sub="", quiet=False):
        nonlocal mode
        mode = m
        ptr.reset()
        rec.reset()
        if ten:
            ten.block(frame_t, 1.5)   # hands still open after a picker / mode change
        if desk:
            desk.reset()
        if not quiet:
            announce(MODE_NAMES[m], sub, color=MODE_COLORS[m])

    def start_picker(m, t):
        set_mode(m, quiet=True)
        pickers[m].start(t)

    def fire(cg, label, hands, t):
        hand = next((h for h in hands if h.label == label), None)
        at = F.palm_center(hand.lm) if hand else None
        name = cg.g.action
        if name == "toggle_pointer":
            set_mode("gesture" if mode == "pointer" else "pointer")
        elif name == "toggle_pause":
            set_mode("gesture" if mode == "standby" else "standby")
        elif name in ("app_switcher", "layout_picker") and not dry_run:
            start_picker("switcher" if name == "app_switcher" else "layout", t)
        else:
            announce(pretty(cg.name), ("[dry run] " if dry_run else "") + pretty(name), at, GREEN)
            if not dry_run:
                threading.Thread(target=_safe_run, args=(name, cg.g.args), daemon=True).start()

    def toggle_preview():
        nonlocal preview, shown
        preview = not preview
        if not preview:
            cv2.destroyWindow(WINDOW)
            cv2.waitKey(1)
        shown = False

    shown = False
    running = True
    frame_t = 0.0
    cam_problem, cam_bad_since, frames_ok = None, None, 0
    try:
        while running:
            frame, hands, t = tracker.read()
            frame_t = t
            # camera health: report black / missing frames instead of silently doing nothing
            bad = "NO CAMERA FRAMES" if frame is None else ("CAMERA BLACK" if frame.mean() < 3 else None)
            if bad:
                cam_bad_since = cam_bad_since or time.monotonic()
                if time.monotonic() - cam_bad_since > 2 and cam_problem != bad:
                    cam_problem = bad
                    print(f"! {bad.lower()} — run `./precog-run doctor`")
            else:
                if cam_problem:
                    print("camera OK again")
                cam_problem, cam_bad_since = None, None
                frames_ok += 1
                if frames_ok == 1:
                    print("camera on — tracking hands")
            if frame is None:
                if ov:
                    ov.send({"camera": cam_problem})
                time.sleep(0.03)
                continue
            fh, fw = frame.shape[:2]
            aspect = fw / fh

            # menu-bar commands
            for cmd in (ov.commands() if ov else []):
                if cmd == "quit":
                    running = False
                elif cmd == "toggle_preview":
                    toggle_preview()
                elif cmd == "dry_run":
                    dry_run = not dry_run
                elif cmd == "mode:pointer":
                    set_mode("gesture" if mode == "pointer" else "pointer")
                elif cmd == "mode:standby":
                    set_mode("gesture" if mode == "standby" else "standby")

            no_hands_since = None if hands else (no_hands_since or t)
            if ten and mode in ("gesture", "pointer", "standby") and ten.update(t, hands):
                resume = mode == "standby"
                set_mode("gesture" if resume else "standby", "ten fingers")
                print("standby off" if resume else "standby on (ten fingers again to resume)")
            pstate = {}
            chooser = None
            allowed = None
            if mode == "pointer":
                pstate = ptr.update(t, hands, aspect) if not dry_run else {}
                if ptr.event in POINTER_EVENTS:
                    announce(POINTER_EVENTS[ptr.event], color=AMBER)
                if no_hands_since and t - no_hands_since > POINTER_IDLE_EXIT:
                    set_mode("gesture", "hands lowered")
                allowed = {"toggle_pointer", "toggle_pause"}
            elif mode in pickers:
                pk = pickers[mode]
                res = pk.update(t, hands, aspect, prefer)
                if res and res[0] == "pick":
                    what = MODE_NAMES[mode].lower()
                    done = pk.apply(res[1])
                    set_mode("gesture", quiet=True)
                    announce(done or "nothing to do", what, color=GREEN)
                elif res:
                    set_mode("gesture", "cancelled")
                else:
                    chooser = pk.view()
            elif mode == "standby":
                allowed = {"toggle_pause"}
            sliding = None
            if desk and mode == "gesture" and not dry_run:
                was = desk.active
                sliding = desk.update(t, hands)
                if desk.event:
                    announce(desk.event, "three-finger swipe", color=CYAN)
                if desk.active and not was:
                    rec.reset()          # a desktop swipe is not a recorded gesture
            hit = None
            if mode not in pickers and not (desk and desk.active):
                hit = rec.update(t, hands, allowed)
            if hit:
                fire(*hit, hands, t)
                if ten:
                    ten.block(t)        # e.g. the open hands at the end of an expand

            if ov:
                bx, by, bw, bh = W.desktop_bounds()
                show_hands = mode == "gesture" and settings["overlay_hands_in_gesture_mode"]
                ov.send({
                    "mode": mode,
                    "hint": HINTS.get(mode),
                    "cursor": pstate.get("cursor"),
                    "pinch": pstate.get("pinch", False),
                    "pinch_ratio": pstate.get("pinch_ratio"),
                    "grab": pstate.get("grab"),
                    "snap": pstate.get("snap"),
                    "stretch": pstate.get("stretch", False),
                    "second": pstate.get("second"),
                    "chooser": chooser,
                    "preview": preview,
                    "dry_run": dry_run,
                    "camera": cam_problem,
                    "sliding": sliding["progress"] if sliding and sliding["sliding"] else None,
                    "ten": ten.progress if ten else 0.0,
                    "skel": [(h.label, [(float(p[0]) / aspect, float(p[1])) for p in h.lm])
                             for h in hands],
                    "hands": [(bx + F.palm_center(h.lm)[0] / aspect * bw, by + F.palm_center(h.lm)[1] * bh)
                              for h in hands] if show_hands else [],
                })

            if preview:
                state = {"mode": mode, "dry_run": dry_run, "scores": rec.scores, "hint": HINTS.get(mode),
                         "gestures": gesture_rows, "accessibility": trusted}
                cv2.imshow(WINDOW, hud.render(frame, hands, state))
                if not shown:
                    bring_to_front()
                    shown = True
                key = cv2.waitKey(1) & 0xFF
                if key == 27 and mode != "gesture":
                    set_mode("gesture")
                elif key in (ord("q"), 27):
                    break
                elif key == ord("c"):
                    toggle_preview()
                elif key == ord("m"):
                    set_mode("gesture" if mode == "pointer" else "pointer")
                elif key == ord("p"):
                    set_mode("gesture" if mode == "standby" else "standby")
                elif key == ord("d"):
                    dry_run = not dry_run
                    announce("Dry run " + ("on" if dry_run else "off"))
    except KeyboardInterrupt:
        pass
    finally:
        tracker.close()
        if ov:
            ov.close()
        cv2.destroyAllWindows()


def _safe_run(name, args):
    try:
        A.run(name, args)
    except Exception as e:
        print(f"! action {name} failed: {e}")
