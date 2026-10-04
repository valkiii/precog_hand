# PRECOG — Minority Report style gesture control for macOS

Webcam + OpenCV + MediaPipe hand tracking. Control Spaces, Mission Control and windows with
your hands, grab windows in mid-air, and teach it your own gestures.

Install with Python 3.12 (Apple silicon):
```sh
uv venv -p 3.12 .venv
uv pip install -p .venv -r requirements.txt
curl -L -o hand_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task
```
A visual tour of how it works is in `docs/precog.html` (open it in a browser).

## Quick start
```sh
./precog-run setup     # guided: record a starter pack (swipes, expand, V-sign...)
./precog-run test      # camera HUD, recognises but does NOT execute (tune here)
./precog-run           # go — no camera window, everything shows on screen
./precog-run --camera  # same, with the camera HUD window
```
While running there is no camera window by default. Feedback is on screen: a small corner
widget (your hand skeleton, the mode, the last gesture), banners and pickers. The **◉ menu-bar
item** shows/hides the camera HUD, switches mode, toggles dry run and quits.
First run: allow **Camera** and **Accessibility** for your terminal app
(System Settings → Privacy & Security). Accessibility is needed for keystrokes and moving windows.

## Modes
**GESTURE MODE** (cyan): your recorded gestures trigger actions, plus the built-in
**three-finger swipe** — like the trackpad: hold up index + middle + ring (pinky and thumb
folded) and move sideways; the desktop slides with your hand. Release (lower the hand or change
the pose) past a third of the way, or with a flick, to switch; otherwise it springs back.
Three fingers + move up = Mission Control, down = App Exposé. (`"three_finger_swipe": false`
in settings.json turns it off.) Desktop switching uses synthetic trackpad "dock swipe" events,
because macOS ignores synthetic Ctrl+←/→ — ported from [iss](https://github.com/joshuarli/iss) (0BSD).

**APP SWITCHER** (magenta) — a gesture bound to `app_switcher` shows a strip of your apps
(most recent first) with the *previous* app highlighted. The strip **stays open by itself** — relax
your hand, move it left/right to move the highlight, **pinch** (thumb to the tip of an extended
index finger) to open it, lower your hand to cancel. Folding into a fist never selects. (`next_app` = plain one-shot Cmd+Tab.)

**WINDOW LAYOUT** (green) — a gesture bound to `layout_picker` (the starter pack uses the two-hand
*expand*) shows a 3×3 grid: halves, quarters, maximize, centred. It starts on *maximize*; move
your hand towards a layout and the screen previews where the window will go. **Pinch** applies,
lowering your hand cancels.

**MANIPULATION MODE** (amber) — press `M` or bind a gesture to `toggle_pointer`.
Exit by doing that gesture again, lowering your hands for 2 s, or `ESC` in the HUD.
| hand | effect |
|---|---|
| move | the on-screen reticle follows your knuckles |
| pinch thumb + index, release | click (the cursor freezes while you pinch; the ring around it fills as your fingers close) |
| two pinches in a row | double-click |
| pinch thumb + middle finger | right-click |
| pinch, then move | grab the window under the reticle and drag it in mid-air |
| second hand pinches too | spread / close hands to stretch or shrink the window |
| drag to the left / right / top edge | snap preview → half screen / maximize on release |
| release while flinging left/right | throw the window to the next display |
| fling up / down | maximize / minimize |

**STANDBY** (red): show **ten spread fingers** (both hands, held still ~1 s) and Precog ignores
all gestures until you show ten fingers again. A ring in the corner widget fills while you hold.
It's wider than a normal open hand so swipes / expand don't trigger it; `./precog-run calibrate`
fits it to your hands (5 seconds).

Keys in the HUD window: `M` manipulate, `P` standby, `D` dry-run toggle, `ESC` back to gesture mode, `Q` quit.

## Making gestures
```sh
./precog-run new                       # wizard: name, pose/motion, hand, action, record
./precog-run new "circle" --kind motion --action mission_control
./precog-run record "swipe left" --append   # add more samples to improve it
./precog-run bind "swipe left"         # change its action
./precog-run sensitivity "swipe left" 1.3   # >1 triggers easier, <1 stricter
./precog-run list | actions | delete NAME
```
* **pose** – a hand shape held still (~0.5 s): fist, thumbs up, V-sign…
* **motion** – a one-hand movement: swipes, push, circle, fist→open hand… When recording: hold
  still, do the move, hold still — repeat. 5–8 samples with natural variation work best.
* `./precog-run list` flags gestures whose samples disagree (a misread hand, a much slower
  take); those samples are ignored, and re-recording usually fixes the gesture.
* **two-hand** – a movement with both hands, e.g. *expand* (hands together → apart) for
  maximize. While both hands are in view, one-hand swipes are ignored so they can't misfire.

The matcher compares hand shape + palm path with DTW and calibrates each gesture's threshold
from the spread of your samples. The HUD's PATTERN MATCH panel shows how close you are
(the tick in each bar is the trigger point). The wizard warns when two gestures look alike.

Gestures are stored as JSON in `gestures/`. Custom actions: `hotkey` (any shortcut),
`open_app`, `shell`, `applescript`, or add a function with `@action` in `precog/actions.py`.

## Troubleshooting
`./precog-run doctor` checks the camera (with and without a window), hand detection,
Accessibility and the overlay. The camera is chosen automatically (`"camera": "auto"`): the
first one that delivers a real image wins, which matters when an iPhone (Continuity Camera)
shows up as a second camera. To force one, set `"camera": 0` (or 1…) in `settings.json`.

## Settings
Create `settings.json` to override anything in `precog/config.py`, e.g.
```json
{"camera": "auto", "preview": true, "cooldown": 1.0, "pointer": {"hand": "Left", "margin": 0.2}}
```

## Notes
* MediaPipe is pinned to 0.10.21 — 1.x crashes on this macOS (Metal "Service is unavailable").
* Space switching uses Ctrl+←/→; keep those shortcuts enabled in Keyboard → Shortcuts → Mission Control.
