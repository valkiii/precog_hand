"""Holographic HUD rendered with OpenCV."""
import math
import time

import cv2
import numpy as np

from . import features as F

# BGR palette
CYAN = (255, 222, 70)
CYAN_DIM = (150, 110, 20)
ICE = (255, 248, 225)
AMBER = (40, 170, 255)
RED = (80, 70, 255)
GREEN = (150, 255, 120)
MAGENTA = (255, 110, 230)
FONT = cv2.FONT_HERSHEY_DUPLEX
MONO = cv2.FONT_HERSHEY_PLAIN

BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
         (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17),
         (17, 18), (18, 19), (19, 20)]
TIPS = [4, 8, 12, 16, 20]


def text(img, s, org, scale=0.5, color=CYAN, thick=1, font=FONT, glow=True, anchor="l"):
    (w, h), _ = cv2.getTextSize(s, font, scale, thick)
    x, y = org
    if anchor == "c":
        x -= w // 2
    elif anchor == "r":
        x -= w
    if glow:
        cv2.putText(img, s, (x, y), font, scale, tuple(c // 3 for c in color), thick + 3, cv2.LINE_AA)
    cv2.putText(img, s, (x, y), font, scale, color, thick, cv2.LINE_AA)
    return w


def brackets(img, x0, y0, x1, y1, color=CYAN, L=18, t=2):
    for (x, y, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        cv2.line(img, (x, y), (x + dx * L, y), color, t, cv2.LINE_AA)
        cv2.line(img, (x, y), (x, y + dy * L), color, t, cv2.LINE_AA)


def panel(img, x0, y0, x1, y1, alpha=0.55):
    roi = img[y0:y1, x0:x1]
    roi[:] = (roi * (1 - alpha) + np.array((40, 22, 6)) * alpha).astype(np.uint8)
    cv2.rectangle(img, (x0, y0), (x1, y1), CYAN_DIM, 1, cv2.LINE_AA)
    brackets(img, x0, y0, x1, y1, CYAN, 10, 2)


def arc(img, c, r, a0, a1, color, t=2):
    cv2.ellipse(img, c, (r, r), 0, a0, a1, color, t, cv2.LINE_AA)


class HUD:
    def __init__(self, width):
        self.W = width
        self.H = None
        self.mask = None
        self.trails = {}
        self.toasts = []      # (t0, title, subtitle, (x, y) or None, color)
        self.fps = 0.0
        self._last = time.monotonic()

    def _prepare(self, h):
        H, W = h, self.W
        self.H = H
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
        vign = np.clip(1.15 - 0.55 * r ** 2, 0.25, 1)
        scan = np.where((yy.astype(int) % 3) == 0, 0.72, 1.0)
        m = (vign * scan)[..., None]
        tint = np.array([1.0, 0.78, 0.30], np.float32)  # BGR -> deep teal hologram
        self.mask = (m * tint * 0.85).astype(np.float32)
        grid = np.zeros((H, W, 3), np.uint8)
        for x in range(0, W, 48):
            cv2.line(grid, (x, 0), (x, H), (40, 26, 6), 1)
        for y in range(0, H, 48):
            cv2.line(grid, (0, y), (W, y), (40, 26, 6), 1)
        self.grid = grid

    def toast(self, title, subtitle="", at=None, color=CYAN):
        self.toasts.append((time.monotonic(), title.upper(), subtitle, at, color))
        self.toasts = self.toasts[-3:]

    def to_px(self, pt, aspect):
        return int(pt[0] / aspect * self.W), int(pt[1] * self.H)

    # ------------------------------------------------------------------ render
    def render(self, frame, hands, state):
        now = time.monotonic()
        dt = now - self._last
        self._last = now
        self.fps = 0.9 * self.fps + 0.1 * (1 / max(dt, 1e-3))

        fh, fw = frame.shape[:2]
        h = int(self.W * fh / fw)
        if self.H != h:
            self._prepare(h)
        aspect = fw / fh
        small = cv2.resize(frame, (self.W, self.H), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        img = (gray[..., None].astype(np.float32) * self.mask).astype(np.uint8)
        img = cv2.add(img, self.grid)

        mode = state.get("mode", "gesture")
        accent = {"gesture": CYAN, "pointer": AMBER, "standby": RED, "switcher": MAGENTA,
                  "layout": GREEN}[mode]

        self._hands(img, hands, aspect, accent, state, now)
        self._chrome(img, state, accent, now)
        self._scores(img, state)
        self._toasts(img, now, aspect)
        if state.get("recording"):
            self._record_panel(img, state["recording"], now)
        return img

    def _hands(self, img, hands, aspect, accent, state, now):
        glow = np.zeros((self.H // 2, self.W // 2, 3), np.uint8)
        seen = set()
        for hand in hands:
            pts = [self.to_px(p, aspect) for p in hand.lm]
            half = [(x // 2, y // 2) for x, y in pts]
            for a, b in BONES:
                cv2.line(glow, half[a], half[b], accent, 5, cv2.LINE_AA)
            for i in TIPS:
                cv2.circle(glow, half[i], 7, accent, -1, cv2.LINE_AA)
            # trail of the palm centre
            c = self.to_px(F.palm_center(hand.lm), aspect)
            tr = self.trails.setdefault(hand.label, [])
            tr.append((now, c))
            seen.add(hand.label)
        glow = cv2.GaussianBlur(glow, (0, 0), 6)
        img[:] = cv2.add(img, cv2.resize(glow, (self.W, self.H)))

        for label in list(self.trails):
            tr = [p for p in self.trails[label] if now - p[0] < 0.7]
            self.trails[label] = tr if label in seen else []
            for (t0, p0), (t1, p1) in zip(tr, tr[1:]):
                k = 1 - (now - t1) / 0.7
                col = tuple(int(c * k) for c in ICE)
                cv2.line(img, p0, p1, col, max(1, int(6 * k)), cv2.LINE_AA)

        for hand in hands:
            pts = [self.to_px(p, aspect) for p in hand.lm]
            for a, b in BONES:
                cv2.line(img, pts[a], pts[b], ICE, 1, cv2.LINE_AA)
            for i, p in enumerate(pts):
                cv2.circle(img, p, 3 if i in TIPS else 2, ICE, -1, cv2.LINE_AA)
            for i in TIPS:
                cv2.circle(img, pts[i], 9, accent, 1, cv2.LINE_AA)
            # rotating reticle on the index tip
            spin = int(now * 180) % 360
            for k in range(3):
                arc(img, pts[8], 16, spin + k * 120, spin + k * 120 + 70, accent, 2)
            # label tag by the wrist
            wx, wy = pts[0]
            tag = f"{hand.label[0]}-HAND  {hand.score:.2f}"
            cv2.line(img, (wx, wy), (wx + 22, wy + 22), CYAN_DIM, 1, cv2.LINE_AA)
            cv2.line(img, (wx + 22, wy + 22), (wx + 130, wy + 22), CYAN_DIM, 1, cv2.LINE_AA)
            text(img, tag, (wx + 26, wy + 18), 0.38, accent)
            if state.get("mode") == "pointer":
                pinch = F.pinch_ratio(hand.lm)
                mid = ((pts[4][0] + pts[8][0]) // 2, (pts[4][1] + pts[8][1]) // 2)
                fill = int(360 * max(0, min(1, 1 - pinch / 0.9)))
                arc(img, mid, 26, -90, -90 + fill, AMBER if pinch > 0.3 else ICE, 3)

    def _chrome(self, img, state, accent, now):
        W, H = self.W, self.H
        brackets(img, 12, 12, W - 12, H - 12, accent, 36, 2)
        # header
        tw = text(img, "PRECOG", (32, 44), 0.9, ICE, 1)
        text(img, "//  GESTURE INTERFACE", (32 + tw + 14, 44), 0.48, accent)
        cv2.line(img, (32, 54), (360, 54), accent, 1, cv2.LINE_AA)
        stamp = time.strftime("%H:%M:%S")
        text(img, stamp, (W - 32, 44), 0.6, ICE, anchor="r")
        text(img, f"{self.fps:4.0f} FPS", (W - 32, 64), 0.4, CYAN_DIM, anchor="r", glow=False)
        # mode chip
        label = {"gesture": "GESTURE MODE", "pointer": "MANIPULATION", "standby": "STANDBY",
                 "switcher": "APP SWITCHER", "layout": "WINDOW LAYOUT"}[state.get("mode", "gesture")]
        if state.get("dry_run"):
            label += "  [DRY RUN]"
        (tw, _), _ = cv2.getTextSize(label, FONT, 0.5, 1)
        pulse = 0.6 + 0.4 * math.sin(now * 4)
        cv2.rectangle(img, (32, 66), (60 + tw, 92), accent, 1, cv2.LINE_AA)
        cv2.circle(img, (44, 79), 4, tuple(int(c * pulse) for c in accent), -1, cv2.LINE_AA)
        text(img, label, (54, 85), 0.5, accent, glow=False)
        # sweeping scan line
        y = int((now * 140) % H)
        cv2.line(img, (14, y), (W - 14, y), CYAN_DIM, 1)
        # footer
        hints = "[C] HIDE CAMERA   [M] MANIPULATE   [P] STANDBY   [D] DRY-RUN   [ESC] BACK   [Q] QUIT"
        if state.get("recording"):
            hints = "[R] DISCARD LAST   [ENTER] DONE   [ESC] CANCEL"
        text(img, hints, (32, H - 26), 0.42, CYAN_DIM, glow=False)
        if state.get("hint"):
            hint = state["hint"].replace("·", "/").replace("←", "<").replace("→", ">")
            for i, part in enumerate(hint.split("  /  ")):
                text(img, part, (32, 122 + i * 22), 0.45, accent)
        if state.get("accessibility") is False:
            text(img, "! ACCESSIBILITY NOT GRANTED - WINDOW CONTROL DISABLED", (W - 32, H - 26),
                 0.42, RED, anchor="r")

    def _scores(self, img, state):
        scores = state.get("scores")
        gestures = state.get("gestures") or []
        if not gestures or state.get("recording"):
            return
        W = self.W
        x0, y0 = W - 300, 92
        rows = gestures[:12]
        panel(img, x0, y0, W - 28, y0 + 34 + 30 * len(rows))
        text(img, "PATTERN MATCH", (x0 + 14, y0 + 22), 0.45, ICE)
        for i, g in enumerate(rows):
            y = y0 + 48 + i * 30
            d, thr = (scores or {}).get(g["name"], (None, None))
            strength = 0.0 if d is None else max(0.0, min(1.0, 1 - d / (2 * thr)))
            hot = d is not None and d < thr
            col = GREEN if hot else (CYAN if strength > 0.25 else CYAN_DIM)
            text(img, g["name"][:16].upper(), (x0 + 14, y), 0.38, col, glow=hot)
            text(img, g["action"][:14], (W - 40, y), 0.32, CYAN_DIM, anchor="r", glow=False)
            bx0, bx1 = x0 + 14, W - 40
            cv2.rectangle(img, (bx0, y + 5), (bx1, y + 9), (60, 40, 10), -1)
            cv2.rectangle(img, (bx0, y + 5), (bx0 + int((bx1 - bx0) * strength), y + 9), col, -1)
            mx = bx0 + (bx1 - bx0) // 2  # threshold marker
            cv2.line(img, (mx, y + 2), (mx, y + 12), ICE, 1)

    def _toasts(self, img, now, aspect):
        alive = []
        for i, (t0, title, sub, at, color) in enumerate(self.toasts):
            age = now - t0
            if age > 1.6:
                continue
            alive.append(self.toasts[i])
            k = 1 - age / 1.6
            col = tuple(int(c * k) for c in color)
            if at is not None:
                p = self.to_px(at, aspect)
                for j in range(3):
                    r = int(20 + (age * 260) + j * 26)
                    cv2.circle(img, p, r, tuple(int(c * k * (1 - j * 0.3)) for c in color), 2, cv2.LINE_AA)
            cx, cy = self.W // 2, int(self.H * 0.82)
            w = text(img, title, (cx, cy), 1.05, col, 2, anchor="c")
            cv2.line(img, (cx - w // 2 - 20, cy + 12), (cx + w // 2 + 20, cy + 12), col, 1, cv2.LINE_AA)
            if sub:
                text(img, sub, (cx, cy + 34), 0.5, col, anchor="c")
        self.toasts = alive

    def _record_panel(self, img, rec, now):
        W = self.W
        x0, y0, x1, y1 = W // 2 - 300, 110, W // 2 + 300, 250
        panel(img, x0, y0, x1, y1, 0.65)
        text(img, f"TRAINING  >>  {rec['name'].upper()}", (x0 + 18, y0 + 32), 0.6, ICE)
        kind = {"motion2": "TWO-HAND MOTION"}.get(rec["kind"], rec["kind"].upper())
        text(img, kind if rec["kind"] == "motion2" else f"{kind}  /  {rec['hand'].upper()} HAND", (x1 - 18, y0 + 32), 0.42, CYAN_DIM, anchor="r", glow=False)
        color = RED if rec["msg"].startswith(("CAMERA", "Seeing", "Only one")) else {"idle": CYAN, "wait": CYAN, "ready": GREEN, "rec": RED, "done": GREEN}.get(rec["phase"], CYAN)  # noqa
        if rec["phase"] == "rec" and int(now * 4) % 2:
            cv2.circle(img, (x0 + 26, y0 + 72), 7, RED, -1, cv2.LINE_AA)
        text(img, rec["msg"], (x0 + 44, y0 + 78), 0.62, color)
        n, total = rec["count"], rec["total"]
        step = (x1 - x0 - 36) / max(total, 1)
        for i in range(total):
            cx = int(x0 + 18 + step * (i + 0.5))
            filled = i < n
            cv2.rectangle(img, (cx - int(step / 2) + 4, y0 + 100), (cx + int(step / 2) - 4, y0 + 118),
                          GREEN if filled else CYAN_DIM, -1 if filled else 1, cv2.LINE_AA)
        if rec.get("progress") is not None:
            p = rec["progress"]
            cv2.rectangle(img, (x0 + 18, y0 + 126), (x0 + 18 + int((x1 - x0 - 36) * p), y0 + 130), color, -1)
