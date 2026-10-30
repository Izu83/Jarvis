"""A small always-on-top "Hey Jarvis" pop-up in the top-right of the MAIN monitor.

Runs as its OWN process (launched by jarvis), so a GUI hiccup can never slow down or crash Jarvis itself. It reads
one JSON command per line on stdin:
    {"cmd":"show"}                      -> appear, start listening
    {"cmd":"partial","text":"..."}      -> live transcription as you speak
    {"cmd":"state","state":"thinking"}  -> listening | thinking | speaking (optional "text": the current step)
    {"cmd":"reply","text":"..."}        -> show Jarvis's reply, then auto-hide
    {"cmd":"hide"}                      -> hide
and writes events back on stdout, one JSON per line:
    {"event":"cancel"}                  -> the user clicked the X (stop listening)
Launch it with:  pythonw -m jarvis.overlay

Note: on Windows, -transparentcolor (for rounded corners) and -alpha (fade) fight each other, so this uses
transparentcolor only and shows/hides instantly.

The panel is frosted glass: Tk can't blur what's behind a window, so the pop-up grabs that part of the screen, blurs
and tints it, and draws it as its own background (refreshed every second while it's up). The window is excluded from
screen capture so it never grabs itself; that also keeps it out of screenshots and the live screen.
"""
import ctypes
import json
import logging
import math
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont

from .orb import Orb

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageGrab, ImageTk
except Exception:      # no Pillow: the plain dark panel
    Image = None

log = logging.getLogger("jarvis.overlay")

KEY = "#ff00fe"          # chroma-key colour Windows renders fully transparent -> rounded corners
PANEL = "#15161c"
BORDER = "#2b2d38"
RING_BG = "#2a2c36"
TEXT = "#f3f4f8"
MUTED = "#aeb4c2"
W = 360                  # window width
PAD = 18                 # screen-edge margin
COLORS = {"listening": "#3cffaa", "thinking": "#9678ff", "speaking": "#3ce1ff"}   # the orb's colours, as on the phone
SPIN = {"listening": .35, "thinking": 1.6, "speaking": .55}   # how fast the little orb turns (radians a second)
LABELS = {"listening": "Listening…", "thinking": "Working…", "speaking": "Jarvis"}
MAX_H = 330              # tallest the window gets; the backdrop is grabbed this tall
TINT = (18, 19, 28)      # the glass's dark tint over the blurred screen…
TINT_ALPHA = 0.62        # …strong enough that white text stays readable over a white page
REFRESH_MS = 1000
WDA_EXCLUDEFROMCAPTURE = 0x11


def _mix(color: str, amount: float) -> str:
    """The colour faded towards the glass (Tk has no see-through colours): amount 1 = full colour, 0 = the glass."""
    a = max(0.0, min(1.0, amount))
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(t + (v - t) * a) for v, t in zip((r, g, b), TINT))


def _primary_size():
    """Width/height of the MAIN monitor (its top-left is always 0,0 on Windows), so the pop-up stays there."""
    try:
        u = ctypes.windll.user32
        return u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


def _scale() -> float:
    """Screen pixels per Tk pixel on the main monitor (this process isn't DPI-aware, so Windows scales it)."""
    try:
        u, g = ctypes.windll.user32, ctypes.windll.gdi32
        dc = u.GetDC(0)
        try:
            return g.GetDeviceCaps(dc, 118) / max(1, g.GetDeviceCaps(dc, 8))   # DESKTOPHORZRES / HORZRES
        finally:
            u.ReleaseDC(0, dc)
    except Exception:
        return 1.0


def _emit(event: str) -> None:
    try:
        sys.stdout.write(json.dumps({"event": event}) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


class Overlay:
    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.root = tk.Tk()
        self.root.title("Jarvis")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.config(bg=KEY)
        try:
            self.root.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(self.root, width=W, height=320, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._click)
        self.sub_font = tkfont.Font(family="Segoe UI Semibold", size=10)
        self.txt_font = tkfont.Font(family="Segoe UI", size=13)
        self.x_font = tkfont.Font(family="Segoe UI", size=12, weight="bold")
        self.state = "listening"
        self.text = ""
        self.visible = False
        self.anim = False
        self.pulse = 0.0
        self.spin = 0.0
        self.orb = Orb(shell=55, knots=2, per_knot=9, dust=.08, seed=3)   # a small, simpler Jarvis orb
        self.h = 70
        self.y_on, self.y_off = PAD, -200
        self.y = self.target = self.y_off
        self.hide_after = None
        self._xbox = (0, 0, 0, 0)   # clickable rectangle of the close button
        self._backdrop = None       # the blurred, tinted screen behind the pop-up (None = plain panel)
        self._panels = {}           # height -> PhotoImage of the glass panel, rebuilt when the backdrop changes
        self._refresh_job = None
        self._place_y(self.y)       # start parked above the screen
        self._glass = Image is not None and self._exclude_from_capture()
        threading.Thread(target=self._reader, daemon=True).start()
        self.root.after(40, self._poll)
        self.root.after(16, self._tick)   # ~60 fps for a smooth slide

    def _place_y(self, y: float):
        pw, _ = _primary_size()
        x = pw - W - PAD
        self.root.geometry(f"{W}x{self.h + 20}+{x}+{int(y)}")

    def _exclude_from_capture(self) -> bool:
        """Keep this window out of screen grabs, so grabbing the backdrop never catches the pop-up itself."""
        try:
            self.root.update_idletasks()
            u = ctypes.windll.user32
            return bool(u.SetWindowDisplayAffinity(u.GetAncestor(self.root.winfo_id(), 2), WDA_EXCLUDEFROMCAPTURE))
        except Exception:
            return False

    def _grab_backdrop(self):
        """Blur and tint the part of the screen the pop-up covers: the frosted glass."""
        if not self._glass:
            return
        try:
            pw, _ = _primary_size()
            x0, y0, k = pw - W - PAD, self.y_on, _scale()
            shot = ImageGrab.grab(bbox=(int(x0 * k), int(y0 * k), int((x0 + W) * k), int((y0 + MAX_H) * k)))
            # blur small and scale back up: the same frost for a fraction of the work
            img = shot.convert("RGB").resize((W // 4, MAX_H // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(3))
            img = ImageEnhance.Color(img.resize((W, MAX_H), Image.BICUBIC)).enhance(1.5)
            self._backdrop = Image.blend(img, Image.new("RGB", img.size, TINT), TINT_ALPHA)
            self._panels.clear()
        except Exception:
            log.debug("backdrop grab failed", exc_info=True)
            self._backdrop = None

    def _refresh(self):
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
        self._refresh_job = None
        if self.visible and self._glass:
            self._grab_backdrop()
            self._refresh_job = self.root.after(REFRESH_MS, self._refresh)

    def _panel(self, h: int):
        """The glass panel for a pop-up h tall: the backdrop with a soft top highlight and a thin bright edge,
        cut to a rounded rectangle (hard-edged, so the corners key out cleanly)."""
        if h in self._panels:
            return self._panels[h]
        pw, ph, r = W - 4, h - 1, 21
        img = self._backdrop.crop((2, 7, 2 + pw, 7 + ph)).convert("RGBA")
        shine = Image.new("L", (1, ph))
        shine.putdata([int(26 * max(0.0, 1 - y / (ph * 0.5))) for y in range(ph)])
        img = Image.composite(Image.new("RGBA", img.size, (255, 255, 255, 255)), img, shine.resize((pw, ph)))
        ImageDraw.Draw(img).rounded_rectangle((0, 0, pw - 1, ph - 1), r, outline=(78, 80, 96, 255), width=1)
        mask = Image.new("L", (pw, ph), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw - 1, ph - 1), r, fill=255)
        out = Image.new("RGB", (pw, ph), KEY)
        out.paste(img.convert("RGB"), mask=mask)
        if len(self._panels) > 24:
            self._panels.clear()
        self._panels[h] = ImageTk.PhotoImage(out)
        return self._panels[h]

    @staticmethod
    def _round(c, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return c.create_polygon(pts, smooth=True, **kw)

    def _draw(self):
        c = self.canvas
        c.delete("all")
        accent = COLORS.get(self.state, COLORS["listening"])
        # measure wrapped text so the panel fits it
        probe = c.create_text(72, 0, anchor="nw", text=self.text or "…", fill=TEXT, font=self.txt_font, width=W - 104)
        bb = c.bbox(probe)
        th = (bb[3] - bb[1]) if bb else 18
        c.delete(probe)
        h = self.h = max(70, 40 + th)
        top = 6
        if self._backdrop is not None:
            c.create_image(2, top + 1, anchor="nw", image=self._panel(h))               # frosted glass
        else:
            self._round(c, 2, top + 1, W - 2, top + h, 22, fill=BORDER, outline="")   # soft border
            self._round(c, 3, top + 2, W - 3, top + h - 1, 21, fill=PANEL, outline="") # panel
        self._draw_orb(c, 38, top + h / 2, accent)
        # label + text
        c.create_text(72, top + 15, anchor="w", text=LABELS.get(self.state, "Jarvis"), fill=accent, font=self.sub_font)
        c.create_text(72, top + 24, anchor="nw", text=self.text or "", fill=TEXT, font=self.txt_font, width=W - 104)
        # close (X) button, top-right
        bx, by = W - 24, top + 20
        self._xbox = (bx - 13, by - 13, bx + 13, by + 13)
        c.create_oval(bx - 11, by - 11, bx + 11, by + 11, fill=RING_BG, outline="")
        c.create_text(bx, by - 1, text="✕", fill=MUTED, font=self.x_font)

    def _draw_orb(self, c, cx, cy, accent):
        """Jarvis's face: a small live orb in the state's colour. Listening, it beats and sends out a ring; working,
        it spins fast; speaking, it ripples."""
        t = self.pulse / 9.6   # seconds, near enough (the pulse grows 0.16 a frame)
        level = 0.0
        if self.state == "listening":
            beat = (t % 1.15) / 1.15
            level = .5 * math.exp(-beat * 9)
            ring = 18 + 9 * beat   # the heartbeat ring
            c.create_oval(cx - ring, cy - ring, cx + ring, cy + ring, outline=_mix(accent, 1 - beat), width=1.5)
        elif self.state == "speaking":
            level = .35 + .3 * math.sin(t * 9) * math.sin(t * 3.7)
        pts = self.orb.project(t, self.spin, 15, cx, cy, level)
        for i, j in self.orb.links:
            (x1, y1, d1), (x2, y2, d2) = pts[i], pts[j]
            c.create_line(x1, y1, x2, y2, fill=_mix(accent, .25 + .6 * (d1 + d2) / 2), width=1)
        for x, y, d in pts:
            s = .6 + 1.1 * d
            c.create_rectangle(x - s, y - s, x + s, y + s, fill=_mix(accent, .35 + .65 * d), outline="")

    def _click(self, ev):
        x1, y1, x2, y2 = self._xbox
        if self.visible and x1 <= ev.x <= x2 and y1 <= ev.y <= y2:
            _emit("cancel")
            self._hide()

    def _tick(self):
        if self.visible or self.anim:
            if self.anim:
                self.y += (self.target - self.y) * 0.3   # ease toward the target position
                if abs(self.y - self.target) < 1.0:
                    self.y, self.anim = self.target, False
                    if self.target <= self.y_off + 1:
                        self.visible = False
            self.pulse += 0.16
            self.spin += SPIN.get(self.state, .4) / 60
            self._draw()
            self._place_y(self.y)
        self.root.after(16, self._tick)

    def _show(self):
        if not self.visible:
            self.y = self.y_off             # start above the screen so it slides down into view
            self.visible = True
            self._refresh()                 # grab the frosted backdrop now, then once a second while it's up
        self.visible, self.anim, self.target = True, True, self.y_on
        self.root.overrideredirect(True)    # re-assert: Windows can re-add the title bar after remap
        self.root.attributes("-topmost", True)
        self.root.lift()

    def _hide(self):
        self.anim, self.target = True, self.y_off   # slide up out of view; _tick hides it when it's gone

    # ---- stdin -> queue ----
    def _reader(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                self.q.put(json.loads(line))
            except ValueError:
                pass
        self.q.put({"cmd": "quit"})

    def _poll(self):
        try:
            while True:
                self._handle(self.q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(40, self._poll)

    def _handle(self, msg: dict):
        cmd = msg.get("cmd")
        if cmd == "quit":
            self.root.destroy()
            return
        if self.hide_after:
            self.root.after_cancel(self.hide_after)
            self.hide_after = None
        if cmd == "show":
            self.state, self.text = "listening", ""
            self._show()
        elif cmd == "partial":
            self.state, self.text = "listening", msg.get("text", "")
            self._show()
        elif cmd == "state":
            self.state = msg.get("state", self.state)
            if msg.get("text"):
                self.text = msg["text"]
            self._show()
        elif cmd == "reply":
            self.state, self.text = "speaking", msg.get("text", "")
            self._show()
            self.hide_after = self.root.after(7000, self._hide)
        elif cmd == "hide":
            self.hide_after = self.root.after(500, self._hide)

    def run(self):
        self.root.mainloop()


class OverlayClient:
    """Lives in the main Jarvis process: starts the overlay as a child process, sends it commands and receives its
    events (the X button). Every call is best-effort — if the overlay isn't running, Jarvis just carries on."""
    def __init__(self, on_event=None):
        self.proc = None
        self.on_event = on_event   # called with an event name, e.g. "cancel"
        self._lock = threading.Lock()

    def start(self) -> None:
        import subprocess
        from pathlib import Path
        pyw = Path(sys.executable).with_name("pythonw.exe")
        exe = str(pyw if pyw.exists() else sys.executable)
        try:
            self.proc = subprocess.Popen(
                [exe, "-m", "jarvis.overlay"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                cwd=str(Path(__file__).resolve().parent.parent))
            threading.Thread(target=self._read, daemon=True).start()
        except Exception:
            self.proc = None

    def _read(self) -> None:
        p = self.proc
        if not p or not p.stdout:
            return
        for line in p.stdout:
            try:
                ev = json.loads(line).get("event")
            except ValueError:
                continue
            if ev and self.on_event:
                try: self.on_event(ev)
                except Exception: pass

    def _ensure(self) -> None:
        """Make sure the overlay process is alive; restart it if it died, so the pop-up always appears."""
        if self.proc is None or self.proc.poll() is not None:
            log.info("Overlay not running; (re)starting it.")
            self.start()

    def _send(self, obj: dict) -> None:
        p = self.proc
        if not p or p.poll() is not None or not p.stdin:
            log.debug("overlay not reachable for %s", obj.get("cmd"))
            return
        try:
            with self._lock:
                p.stdin.write(json.dumps(obj) + "\n")
                p.stdin.flush()
        except OSError:
            log.debug("overlay write failed", exc_info=True)

    def show(self):            self._ensure(); self._send({"cmd": "show"})
    def partial(self, text):   self._send({"cmd": "partial", "text": text})
    def thinking(self):        self._send({"cmd": "state", "state": "thinking"})
    def step(self, text):      self._send({"cmd": "state", "state": "thinking", "text": text})
    def reply(self, text):     self._send({"cmd": "reply", "text": text})
    def hide(self):            self._send({"cmd": "hide"})


if __name__ == "__main__":
    try:
        Overlay().run()
    except Exception:
        pass
