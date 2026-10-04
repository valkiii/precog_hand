import argparse
import sys

from . import actions as A
from . import store
from .config import load_settings

SUGGESTED = [
    ("swipe left", "motion", "desktop_right", "Flat hand sweeps right-to-left"),
    ("swipe right", "motion", "desktop_left", "Flat hand sweeps left-to-right"),
    ("swipe up", "motion", "mission_control", "Flat hand sweeps upwards"),
    ("swipe down", "motion", "show_desktop", "Flat hand sweeps downwards"),
    ("expand", "motion2", "layout_picker", "Both hands together in front of you, then pull them apart"),
    ("push", "motion", "window_next_screen", "Open palm pushes sideways towards the other screen"),
    ("pointer", "pose", "toggle_pointer", "Index + middle finger up ('V'), hold"),
    ("app switch", "pose", "app_switcher", "Thumb + pinky out, other fingers folded (shaka / 'call me'), hold"),
    ("standby", "pose", "toggle_pause", "Thumbs up, hold"),
]


def ask(prompt, default=None, choices=None):
    hint = f" [{default}]" if default is not None else ""
    while True:
        v = input(f"{prompt}{hint}: ").strip() or (default or "")
        if not choices or v in choices:
            return v
        print(f"  choose one of: {', '.join(choices)}")


def pick_action(current=None):
    names = list(A.ACTIONS)
    print("\nActions:")
    for i, n in enumerate(names, 1):
        print(f"  {i:2d}. {n:20s} {A.ACTIONS[n]['desc']}")
    while True:
        v = ask("Action (number or name)", current)
        name = names[int(v) - 1] if v.isdigit() and 0 < int(v) <= len(names) else v
        if name in A.ACTIONS:
            break
        print("  unknown action")
    args = {p: ask(f"  {p}") for p in A.ACTIONS[name]["params"]}
    return name, args


def _record(settings, g, samples_n, append=False):
    import cv2

    from .recognizer import Compiled, conflicts
    from .recorder import record
    from .tracker import Tracker
    tracker = Tracker(settings)
    try:
        got = record(tracker, settings, g.name, g.kind, g.hand, samples_n)
    finally:
        tracker.close()
        cv2.destroyAllWindows()
        cv2.waitKey(1)
    if not got:
        print("Cancelled — nothing saved.")
        return False
    new = [store.frames_to_sample(s) for s in got]
    g.samples = (g.samples + new) if append else new
    store.save(g)
    cg = Compiled(g, settings["motion_traj_weight"])
    print(f"Saved '{g.name}' with {len(g.samples)} samples (match threshold {cg.threshold:.2f}).")
    if cg.inconsistent:
        print(f"  ! {cg.dropped} sample(s) didn't look like the others and are ignored. If it "
              "misbehaves, record again keeping the gesture the same each time.")
    for other, d in conflicts(g, store.load_all(), settings):
        print(f"  ! looks similar to '{other}' (distance {d:.2f}) — consider making them more distinct")
    return True


def cmd_new(settings, name=None, kind=None, action=None, hand=None):
    print("\n== NEW GESTURE ==")
    name = name or ask("Name (e.g. 'swipe left')")
    if store.get(name) and ask("Exists — overwrite? (y/n)", "n") != "y":
        return
    print("  pose   = a hand shape you hold still (fist, thumbs up, 'V'...)")
    print("  motion = a one-hand movement (swipe, circle, push, open-the-hand...)")
    print("  two-hand = a movement with both hands (expand, pinch-in, rotate...)")
    kind = kind or ask("Type", "motion", ["pose", "motion", "two-hand"])
    kind = "motion2" if kind == "two-hand" else kind
    hand = "any" if kind == "motion2" else (hand or ask("Which hand", "any", ["any", "Right", "Left"]))
    if action:
        args = {p: ask(f"  {p}") for p in A.ACTIONS[action]["params"]}
    else:
        action, args = pick_action()
    n = int(ask("How many samples to record", "6"))
    g = store.Gesture(name=name, kind=kind, hand=hand, action=action, args=args)
    print("\nA camera window opens and starts recording as soon as it sees your hand.\n"
          "In that window: R discards the last sample, ENTER finishes early, ESC cancels.")
    if kind != "pose":
        print("Hold still → do the movement → hold still again. Repeat for each sample.")
    _record(settings, g, n)


def cmd_setup(settings):
    print("Starter pack — record each suggested gesture or skip it.\n")
    for name, kind, action, how in SUGGESTED:
        have = store.get(name)
        print(f"\n> {name!r} ({kind}) -> {action}\n  {how}" + ("  [already recorded]" if have else ""))
        v = ask("Record it? (y/n/q)", "n" if have else "y", ["y", "n", "q"])
        if v == "q":
            break
        if v == "y":
            print("  Camera window opening - recording starts when it sees your hand.")
            _record(settings, store.Gesture(name=name, kind=kind, hand="any", action=action), 6)


def cmd_list(settings):
    from .recognizer import Compiled
    gs = store.load_all()
    if not gs:
        print("No gestures yet. Try `./precog-run setup` or `./precog-run new`.")
    for g in gs:
        args = f" {g.args}" if g.args else ""
        c = Compiled(g, settings["motion_traj_weight"])
        warn = ""
        if c.inconsistent:
            warn = (f"   ! inconsistent samples ({c.dropped} ignored) - re-record: "
                    f"./precog-run record \"{g.name}\"")
        print(f"  {g.name:14s} {g.kind.replace('motion2', 'two-hand'):8s} -> {g.action}{args}"
              f"   ({len(g.samples)} samples){warn}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="precog", description="Minority Report style gesture control")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="start gesture control")
    r.add_argument("--dry-run", action="store_true", help="recognise but don't execute actions")
    r.add_argument("--camera", action="store_true", help="also show the camera HUD window")
    r.add_argument("--no-preview", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--no-overlay", action="store_true", help="no on-screen overlay")
    te = sub.add_parser("test", help="dry run with the camera HUD: recognise, don't act")
    te.add_argument("--no-camera", action="store_true")
    n = sub.add_parser("new", help="create + record a gesture")
    n.add_argument("name", nargs="?")
    n.add_argument("--kind", choices=["pose", "motion", "two-hand"])
    n.add_argument("--hand", choices=["Right", "Left", "any"])
    n.add_argument("--action", choices=list(A.ACTIONS))
    rr = sub.add_parser("record", help="re-record (or --append samples to) an existing gesture")
    rr.add_argument("name")
    rr.add_argument("--append", action="store_true")
    rr.add_argument("-n", type=int, default=6)
    b = sub.add_parser("bind", help="change the action of a gesture")
    b.add_argument("name")
    se = sub.add_parser("sensitivity", help="tune a gesture: >1 easier to trigger, <1 stricter")
    se.add_argument("name")
    se.add_argument("value", type=float)
    d = sub.add_parser("delete", help="delete a gesture")
    d.add_argument("name")
    sub.add_parser("list", help="list gestures")
    sub.add_parser("actions", help="list available actions")
    sub.add_parser("setup", help="guided starter pack")
    sub.add_parser("doctor", help="check camera, hand tracking, permissions, overlay")
    sub.add_parser("calibrate", help="fit the ten-finger standby trigger to your hands")
    a = p.parse_args(argv)
    try:
        _dispatch(a)
    except (KeyboardInterrupt, EOFError):
        print("\nBye.")


def _dispatch(a):
    s = load_settings()

    def need(name):
        g = store.get(name)
        if not g:
            sys.exit(f"No gesture named {name!r}. See `./precog-run list`.")
        return g

    if a.cmd in (None, "run", "test"):
        from .app import run
        if a.cmd == "test":
            preview = not a.no_camera
        elif getattr(a, "camera", False):
            preview = True
        else:
            preview = None   # settings.json decides (default: no camera window)
        run(s, dry_run=a.cmd == "test" or getattr(a, "dry_run", False),
            preview=preview, overlay=not getattr(a, "no_overlay", False))
    elif a.cmd == "new":
        cmd_new(s, a.name, a.kind, a.action, a.hand)
    elif a.cmd == "record":
        _record(s, need(a.name), a.n, a.append)
    elif a.cmd == "bind":
        g = need(a.name)
        g.action, g.args = pick_action(g.action)
        store.save(g)
        print(f"'{g.name}' -> {g.action} {g.args or ''}")
    elif a.cmd == "sensitivity":
        g = need(a.name)
        g.sensitivity = a.value
        store.save(g)
        print(f"'{g.name}' sensitivity = {a.value}")
    elif a.cmd == "delete":
        need(a.name)
        if ask(f"Delete '{a.name}'? (y/n)", "n") == "y":
            store.remove(a.name)
            print("Deleted.")
    elif a.cmd == "list":
        cmd_list(s)
    elif a.cmd == "actions":
        for k, v in A.ACTIONS.items():
            ps = f"  ({', '.join(v['params'])})" if v["params"] else ""
            print(f"  {k:20s} {v['desc']}{ps}")
    elif a.cmd == "setup":
        cmd_setup(s)
    elif a.cmd == "calibrate":
        from .calibrate import run as calibrate
        calibrate(s)
    elif a.cmd == "doctor":
        from .doctor import run as doctor
        doctor(s)
