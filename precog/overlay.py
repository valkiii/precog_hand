"""Transparent, click-through, always-on-top screen overlay (separate process).

Everything Precog shows on your desktop lives here: the floating reticle, motion streaks,
the locked-window frame, snap / layout previews, the app switcher strip, action banners,
the corner status widget (your hand skeleton, no video) and the menu-bar item.
The main process sends small dicts through a Pipe; menu clicks come back the same way.
"""
import math
import multiprocessing as mp
import time


class Overlay:
    def __init__(self, widget=True):
        ctx = mp.get_context("spawn")
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=_main, args=(child, widget), daemon=True)
        self.proc.start()

    @property
    def pid(self):
        return self.proc.pid

    def send(self, msg):
        try:
            self.conn.send(msg)
        except (BrokenPipeError, OSError):
            pass

    def commands(self):
        """Menu-bar clicks since the last call."""
        out = []
        try:
            while self.conn.poll():
                out.append(self.conn.recv().get("cmd"))
        except (EOFError, OSError):
            pass
        return out

    def close(self):
        self.send({"quit": True})
        self.proc.join(1)
        if self.proc.is_alive():
            self.proc.terminate()


BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
         (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17),
         (17, 18), (18, 19), (19, 20)]


def _main(conn, widget_on):
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)   # Ctrl-C is the main process's job; it closes us
    import objc
    from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                        NSAttributedString, NSBackingStoreBuffered, NSBezierPath, NSColor,
                        NSFont, NSFontAttributeName, NSForegroundColorAttributeName,
                        NSMakeRect, NSMenu, NSMenuItem, NSRunningApplication, NSScreen,
                        NSShadow, NSStatusBar, NSString, NSView, NSWindow,
                        NSWindowStyleMaskBorderless, NSZeroRect)
    from Foundation import NSObject, NSTimer

    from .windows import LAYOUTS as LAYOUT_FRACS

    CAN_JOIN_ALL_SPACES, STATIONARY, IGNORES_CYCLE, FULLSCREEN_AUX = 1, 16, 64, 256
    SCREEN_SAVER_LEVEL = 1000

    def rgba(r, g, b, a=1.0):
        return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)

    CYAN = (0.28, 0.87, 1.0)
    AMBER = (1.0, 0.66, 0.16)
    MAGENTA = (0.9, 0.43, 1.0)
    GREEN = (0.47, 1.0, 0.6)
    RED = (1.0, 0.3, 0.32)
    ICE = (0.88, 0.97, 1.0)
    MODE_COLOR = {"gesture": CYAN, "pointer": AMBER, "switcher": MAGENTA, "layout": GREEN,
                  "standby": RED}
    MODE_LABEL = {"gesture": "GESTURE", "pointer": "MANIPULATE", "switcher": "APP SWITCH",
                  "layout": "LAYOUT", "standby": "STANDBY"}

    state = {"cursor": None, "trail": [], "hands": [], "grab": None, "mode": "gesture",
             "pinch": False, "banner": None, "second": None, "streaks": {}, "hint": None,
             "snap": None, "chooser": None, "skel": [], "last": None, "preview": False,
             "dry_run": False, "camera": None, "sliding": None, "pinch_ratio": None, "ten": 0.0}
    icons = {}

    def glow(color, radius=14):
        sh = NSShadow.alloc().init()
        sh.setShadowColor_(rgba(*color, 0.95))
        sh.setShadowBlurRadius_(radius)
        sh.setShadowOffset_((0, 0))
        sh.set()

    def shade(radius=4):
        """Dark halo instead of a coloured glow: readable on any background."""
        sh = NSShadow.alloc().init()
        sh.setShadowColor_(rgba(0, 0, 0, 0.85))
        sh.setShadowBlurRadius_(radius)
        sh.setShadowOffset_((0, 0))
        sh.set()

    def no_glow():
        sh = NSShadow.alloc().init()
        sh.setShadowColor_(None)
        sh.set()

    def ring(cx, cy, r, color, alpha, width, a0=0, a1=360):
        p = NSBezierPath.bezierPath()
        p.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
            (cx, cy), r, a0, a1, False)
        rgba(*color, alpha).setStroke()
        p.setLineWidth_(width)
        p.stroke()

    def dot(cx, cy, r, color, alpha=1.0):
        rgba(*color, alpha).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - r, cy - r, 2 * r, 2 * r)).fill()

    def line(pts, color, alpha, width):
        if len(pts) < 2:
            return
        p = NSBezierPath.bezierPath()
        p.moveToPoint_(pts[0])
        for q in pts[1:]:
            p.lineToPoint_(q)
        p.setLineCapStyle_(1)
        p.setLineJoinStyle_(1)
        rgba(*color, alpha).setStroke()
        p.setLineWidth_(width)
        p.stroke()

    def box(x, y, w, h, color, alpha, width=2.0, fill=None, radius=0):
        p = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x, y, w, h), radius, radius)
        if fill is not None:
            rgba(*fill).setFill()
            p.fill()
        if width:
            rgba(*color, alpha).setStroke()
            p.setLineWidth_(width)
            p.stroke()

    def brackets(x, y, w, h, color, alpha=1.0, c=18, width=3):
        for cx, cy, dx, dy in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
            line([(cx + dx * c, cy), (cx, cy), (cx, cy + dy * c)], color, alpha, width)

    def label(s, x, y, size, color, alpha=1.0, center=False, weight=0.3):
        attrs = {NSFontAttributeName: NSFont.monospacedSystemFontOfSize_weight_(size, weight),
                 NSForegroundColorAttributeName: rgba(*color, alpha)}
        ns = NSString.stringWithString_(s)
        if center:
            x -= ns.sizeWithAttributes_(attrs).width / 2
        ns.drawAtPoint_withAttributes_((x, y), attrs)

    def icon(pid):
        if pid not in icons:
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            icons[pid] = app.icon() if app else None
        return icons[pid]

    class View(NSView):
        def isFlipped(self):
            return True

        def drawRect_(self, rect):
            try:
                self._draw()
            except Exception as e:  # never let a drawing bug kill the overlay
                print("overlay draw error:", e)

        @objc.python_method
        def _draw(self):
            ox, oy = self.origin
            now = time.monotonic()
            spin = (now * 160) % 360
            mode = state["mode"]
            col = MODE_COLOR.get(mode, CYAN)
            fw, fh = self.frame().size.width, self.frame().size.height

            def L(p):
                return (p[0] - ox, p[1] - oy)

            # streaks of fast hand motion (gesture mode)
            for pts in state["streaks"].values():
                pts = [(t, p) for t, p in pts if now - t < 0.45]
                for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
                    k = 1 - (now - t1) / 0.45
                    glow(CYAN, 18)
                    line([L(p0), L(p1)], ICE, 0.75 * k, 2 + 10 * k)

            # ghost hand markers
            if mode == "gesture":
                for p in state["hands"]:
                    x, y = L(p)
                    glow(CYAN, 10)
                    ring(x, y, 10, CYAN, 0.55, 1.5)
                    ring(x, y, 22, CYAN, 0.3, 1, spin, spin + 90)
                    ring(x, y, 22, CYAN, 0.3, 1, spin + 180, spin + 270)

            # snap preview while dragging into an edge
            if state["snap"]:
                x, y = L(state["snap"][:2])
                w, h = state["snap"][2], state["snap"][3]
                glow(GREEN, 24)
                box(x + 6, y + 6, w - 12, h - 12, GREEN, 0.9, 3, fill=(*GREEN, 0.10), radius=14)
                label("SNAP", x + w / 2, y + h / 2 - 16, 26, ICE, 0.9, center=True)

            # locked window
            g = state["grab"]
            if g:
                x, y = L(g[:2])
                w, h = g[2], g[3]
                glow(AMBER, 22)
                box(x, y, w, h, AMBER, 0.9, 2.5)
                brackets(x, y, w, h, ICE, 1.0, 34, 4)
                tag = "◢ STRETCH" if state.get("stretch") else "◢ LOCKED"
                label(f"{tag}  {int(w)}×{int(h)}", x + 8, y - 22, 13, AMBER)

            ch = state["chooser"]
            if ch and ch["kind"] == "layout":
                self._layout(ch, L, fw, fh, now)
            if ch and ch["kind"] == "apps" and self.main:
                self._apps(ch, fw, fh, now)

            # reticle
            if state["cursor"] and mode == "pointer":
                trail = [L(p) for t, p in state["trail"] if now - t < 0.35]
                glow(AMBER, 16)
                line(trail, AMBER, 0.55, 3)
                x, y = L(state["cursor"])
                r = 13 if state["pinch"] else 24
                glow(col, 18)
                ring(x, y, 4, ICE, 1.0, 4)
                for k in range(3):
                    a = spin + k * 120
                    ring(x, y, r, col, 0.95, 3, a, a + 70)
                ring(x, y, r + 14, col, 0.35, 1, -spin, -spin + 40)
                pr = state["pinch_ratio"]
                if pr is not None and not state["pinch"]:
                    # click meter: fills as thumb and index close in
                    f = min(max((0.95 - pr) / (0.95 - 0.32), 0), 1)
                    if f > 0.05:
                        glow(ICE, 8)
                        ring(x, y, r + 8, ICE, 0.9, 3, -90, -90 + 360 * f)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    line([(x + dx * (r + 6), y + dy * (r + 6)), (x + dx * (r + 18), y + dy * (r + 18))], ICE, 0.8, 1.5)
                if state["second"]:
                    sx, sy = L(state["second"])
                    ring(sx, sy, 16, col, 0.7, 2)
                    if g:
                        line([(x, y), (sx, sy)], ICE, 0.35, 1)

            if not self.main:
                return

            # persistent instructions for the current mode
            if state["hint"]:
                attrs = {NSFontAttributeName: NSFont.monospacedSystemFontOfSize_weight_(13, 0.3)}
                tw = NSString.stringWithString_(state["hint"]).sizeWithAttributes_(attrs).width
                no_glow()
                box(fw / 2 - tw / 2 - 18, fh - 80, tw + 36, 36, col, 0.5, 1,
                    fill=(0.02, 0.04, 0.07, 0.88), radius=18)
                glow(col, 8)
                label(state["hint"], fw / 2, fh - 71, 13, col, 1.0, center=True)

            # three-finger desktop swipe progress
            if state["sliding"] is not None:
                p = state["sliding"]
                bw2 = 360
                bx2, by2 = fw / 2 - bw2 / 2, fh - 130
                no_glow()
                box(bx2, by2, bw2, 8, CYAN, 0.5, 1, fill=(0.02, 0.04, 0.07, 0.85), radius=4)
                mid = bx2 + bw2 / 2
                glow(CYAN if abs(p) < 0.33 else GREEN, 12)
                fill_col = (*(GREEN if abs(p) >= 0.33 else CYAN), 0.9)
                x_a, x_b = sorted((mid, mid + p * bw2 / 2))   # follows the hand
                box(x_a, by2, max(x_b - x_a, 1), 8, CYAN, 0, 0, fill=fill_col, radius=4)
                for q in (-0.33, 0.33):
                    line([(mid - q * bw2 / 2, by2 - 5), (mid - q * bw2 / 2, by2 + 13)], ICE, 0.7, 1)
                label("◀ desktop ▶" if abs(p) < 0.33 else ("release → next desktop" if p < 0 else "release → previous desktop"),
                      fw / 2, by2 - 26, 13, ICE, 0.9, center=True)

            # action banner
            b = state["banner"]
            if b and now - b["t"] < 1.6:
                k = 1 - (now - b["t"]) / 1.6
                cx, cy = fw / 2, fh * 0.12
                glow(CYAN, 20)
                label(b["title"], cx, cy, 30, ICE, k, center=True)
                if b.get("sub"):
                    label(b["sub"], cx, cy + 42, 14, CYAN, k, center=True)
                line([(cx - 220, cy + 36), (cx + 220, cy + 36)], CYAN, 0.7 * k, 1.5)

            if widget_on:
                self._widget(col, mode, now)

        # -------------------------------------------------------- app switcher strip
        @objc.python_method
        def _apps(self, ch, fw, fh, now):
            items = ch["items"]
            if not items:
                return
            tile, gap = 112, 14
            total = len(items) * tile + (len(items) - 1) * gap
            x0, y0 = fw / 2 - total / 2, fh * 0.38
            no_glow()
            box(x0 - 28, y0 - 46, total + 56, tile + 96, MAGENTA, 0.6, 1.5,
                fill=(0.03, 0.02, 0.08, 0.92), radius=22)
            glow(MAGENTA, 12)
            label("APP SWITCHER", x0 - 8, y0 - 34, 12, MAGENTA, 0.9)
            for i, (pid, name) in enumerate(items):
                x = x0 + i * (tile + gap)
                sel = i == ch["sel"]
                if sel:
                    glow(MAGENTA, 26)
                    box(x - 6, y0 - 6, tile + 12, tile + 12, MAGENTA, 1.0, 3,
                        fill=(*MAGENTA, 0.16), radius=18)
                else:
                    no_glow()
                    box(x, y0, tile, tile, ICE, 0.18, 1, radius=14)
                img = icon(pid)
                if img is not None:
                    no_glow()
                    s = 84 if sel else 68
                    img.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                        NSMakeRect(x + (tile - s) / 2, y0 + (tile - s) / 2 - 6, s, s),
                        NSZeroRect, 2, 1.0 if sel else 0.55, True, None)
                short = name if len(name) <= 13 else name[:12] + "…"
                glow(MAGENTA if sel else CYAN, 8)
                label(short, x + tile / 2, y0 + tile + 10, 13 if sel else 11,
                      ICE if sel else CYAN, 1.0 if sel else 0.6, center=True)
            # hand position along the strip
            if ch.get("cursor"):
                cx = x0 + ch["cursor"][0] * total
                glow(ICE, 12)
                line([(x0, y0 + tile + 36), (x0 + total, y0 + tile + 36)], MAGENTA, 0.3, 1)
                dot(cx, y0 + tile + 36, 5, ICE)

        # -------------------------------------------------------- layout picker
        @objc.python_method
        def _layout(self, ch, L, fw, fh, now):
            if ch.get("window"):
                x, y = L(ch["window"][:2])
                no_glow()
                box(x, y, ch["window"][2], ch["window"][3], ICE, 0.35, 1.5)
            px, py = L(ch["preview"][:2])
            pw, ph = ch["preview"][2], ch["preview"][3]
            glow(GREEN, 26)
            box(px + 8, py + 8, pw - 16, ph - 16, GREEN, 0.95, 3, fill=(*GREEN, 0.12), radius=16)
            brackets(px + 8, py + 8, pw - 16, ph - 16, ICE, 0.9, 30, 4)
            if not self.main:
                return
            # 3x3 picker in the middle of the main screen
            cell, gap = 92, 10
            size = 3 * cell + 2 * gap
            gx, gy = fw / 2 - size / 2, fh / 2 - size / 2
            no_glow()
            box(gx - 22, gy - 48, size + 44, size + 92, GREEN, 0.5, 1.5,
                fill=(0.02, 0.06, 0.04, 0.92), radius=20)
            glow(GREEN, 10)
            label("WINDOW LAYOUT", gx, gy - 36, 12, GREEN, 0.95)
            for i, (name, fx, fy, fwf, fhf) in enumerate(LAYOUT_FRACS):
                cx = gx + (i % 3) * (cell + gap)
                cy = gy + (i // 3) * (cell + gap)
                sel = i == ch["sel"]
                no_glow()
                box(cx, cy, cell, cell, ICE, 0.25 if not sel else 0.0, 1, radius=10)
                if sel:
                    glow(GREEN, 18)
                    box(cx - 3, cy - 3, cell + 6, cell + 6, GREEN, 1.0, 3, radius=12)
                # tiny glyph of the layout
                m = 12
                if sel:
                    glow(GREEN, 8)
                else:
                    no_glow()
                box(cx + m + fx * (cell - 2 * m), cy + m + fy * (cell - 2 * m),
                    fwf * (cell - 2 * m), fhf * (cell - 2 * m),
                    GREEN if sel else CYAN, 0.9, 1.5,
                    fill=((*GREEN, 0.45) if sel else (*CYAN, 0.18)), radius=4)
            glow(GREEN, 10)
            label(ch["names"][ch["sel"]].upper(), fw / 2, gy + size + 14, 16, ICE, 1.0, center=True)
            if ch.get("cursor"):
                cx = gx + ch["cursor"][0] * size
                cy = gy + ch["cursor"][1] * size
                glow(ICE, 12)
                ring(cx, cy, 7, ICE, 0.95, 2)

        # -------------------------------------------------------- corner widget
        @objc.python_method
        def _widget(self, col, mode, now):
            """Corner status: no panel — just your hand skeleton and a few words floating on
            the desktop. A soft dark shadow keeps them readable on light backgrounds."""
            vx, vy, vw, vh = self.vis
            w, h = 236, 156
            x, y = vx + vw - w - 18, vy + vh - h - 18
            shade()
            tag = MODE_LABEL.get(mode, mode.upper()) + ("  · DRY" if state["dry_run"] else "")
            label("PRECOG", x + 12, y + 9, 11, ICE, 0.9, weight=0.5)
            label(tag, x + w - 12 - 7.2 * len(tag), y + 10, 10, col, 1.0, weight=0.5)
            ax, ay, aw, ah = x + 10, y + 32, w - 20, h - 62
            if state["camera"]:
                label(state["camera"], x + w / 2, ay + ah / 2 - 16, 12, RED, 1.0, center=True)
                label("run: ./precog-run doctor", x + w / 2, ay + ah / 2 + 4, 10, ICE, 0.9, center=True)
            for hand_label, pts in state["skel"]:
                P = [(ax + u * aw, ay + v * ah) for u, v in pts]
                shade(5)
                for a, b in BONES:
                    line([P[a], P[b]], col, 0.95, 2.0)
                for i in (4, 8, 12, 16, 20):
                    dot(P[i][0], P[i][1], 2.6, ICE)
            ten = state["ten"] or 0.0
            if ten > 0:   # holding ten fingers: show how close the standby toggle is
                cx, cy = x + w / 2, ay + ah / 2
                shade(5)
                ring(cx, cy, 30, RED if mode != "standby" else GREEN, 0.95, 4, -90, -90 + 360 * ten)
                label("STANDBY" if mode != "standby" else "RESUME", cx, cy + 38, 11,
                      RED if mode != "standby" else GREEN, 1.0, center=True, weight=0.5)
            last = state["last"]
            if last:
                k = max(0.5, 1 - (now - last["t"]) / 4)
                shade()
                label("▸ " + last["text"][:28], x + 12, y + h - 24, 11, GREEN, k, weight=0.4)

    class Controller(NSObject):
        def menuCmd_(self, sender):
            try:
                conn.send({"cmd": sender.representedObject()})
            except Exception:
                pass

        def tick_(self, timer):
            try:
                self.tick()
            except Exception as e:   # an exception escaping into AppKit aborts the process
                print("overlay error:", e)

        @objc.python_method
        def tick(self):
            dirty = False
            try:
                while conn.poll():
                    msg = conn.recv()
                    dirty = True
                    if msg.get("quit"):
                        NSApplication.sharedApplication().terminate_(None)
                        return
                    now = time.monotonic()
                    for k in ("cursor", "grab", "mode", "pinch", "second", "stretch", "hint",
                              "snap", "chooser", "skel", "preview", "dry_run", "camera", "sliding",
                              "pinch_ratio", "ten"):
                        if k in msg:
                            state[k] = msg[k]
                    if "hands" in msg:
                        state["hands"] = msg["hands"]
                        for i, p in enumerate(msg["hands"]):
                            s = state["streaks"].setdefault(i, [])
                            s.append((now, p))
                            if len(s) >= 2 and math.dist(s[-1][1], s[-2][1]) < 18:
                                s.clear()   # keep only fast segments
                            del s[:-14]
                        for i in list(state["streaks"]):
                            if i >= len(msg["hands"]):
                                state["streaks"].pop(i)
                    if msg.get("cursor"):
                        state["trail"].append((now, msg["cursor"]))
                        del state["trail"][:-20]
                    if msg.get("banner"):
                        state["banner"] = {"t": now, **msg["banner"]}
                        state["last"] = {"t": now, "text": (msg["banner"]["title"] + "  " +
                                                            msg["banner"].get("sub", "")).strip()}
                    if {"mode", "preview", "dry_run"} & msg.keys():
                        self.refresh_menu()
            except (EOFError, OSError):
                NSApplication.sharedApplication().terminate_(None)
                return
            if dirty or state["mode"] != "gesture" or state["banner"]:
                for v in self.views:
                    v.setNeedsDisplay_(True)

        @objc.python_method
        def refresh_menu(self):
            mode = state["mode"]
            c = MODE_COLOR.get(mode, CYAN)
            title = NSAttributedString.alloc().initWithString_attributes_(
                "◉ " + MODE_LABEL.get(mode, ""),
                {NSForegroundColorAttributeName: rgba(*c),
                 NSFontAttributeName: NSFont.monospacedSystemFontOfSize_weight_(11, 0.4)})
            self.item.button().setAttributedTitle_(title)
            checks = {"toggle_preview": state["preview"], "dry_run": state["dry_run"],
                      "mode:pointer": mode == "pointer", "mode:standby": mode == "standby"}
            for key, mi in self.menu_items.items():
                if key in checks:
                    mi.setState_(1 if checks[key] else 0)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    screens = NSScreen.screens()
    ph = screens[0].frame().size.height
    views = []
    wins = []
    for i, s in enumerate(screens):
        f = s.frame()
        vf = s.visibleFrame()
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            f, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setIgnoresMouseEvents_(True)
        win.setHasShadow_(False)
        win.setLevel_(SCREEN_SAVER_LEVEL)
        win.setCollectionBehavior_(CAN_JOIN_ALL_SPACES | STATIONARY | IGNORES_CYCLE | FULLSCREEN_AUX)
        v = View.alloc().initWithFrame_(((0, 0), (f.size.width, f.size.height)))
        v.origin = (f.origin.x, ph - (f.origin.y + f.size.height))  # top-left global coords
        # visible area (no menu bar / Dock) in this view's flipped coordinates
        v.vis = (vf.origin.x - f.origin.x,
                 (f.origin.y + f.size.height) - (vf.origin.y + vf.size.height),
                 vf.size.width, vf.size.height)
        v.main = i == 0
        win.setContentView_(v)
        win.orderFrontRegardless()
        views.append(v)
        wins.append(win)

    ctl = Controller.alloc().init()
    ctl.views = views

    # menu-bar item
    ctl.item = NSStatusBar.systemStatusBar().statusItemWithLength_(-1)
    menu = NSMenu.alloc().init()
    ctl.menu_items = {}
    for title, key in (("Show camera HUD", "toggle_preview"), ("Manipulation mode", "mode:pointer"),
                       ("Standby", "mode:standby"), ("Dry run (don't act)", "dry_run"),
                       (None, None), ("Quit Precog", "quit")):
        if title is None:
            menu.addItem_(NSMenuItem.separatorItem())
            continue
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "menuCmd:", "")
        mi.setTarget_(ctl)
        mi.setRepresentedObject_(key)
        menu.addItem_(mi)
        ctl.menu_items[key] = mi
    ctl.item.setMenu_(menu)
    ctl.refresh_menu()

    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1 / 60, ctl, "tick:", None, True)
    app.run()

