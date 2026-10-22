"""A small always-on-top "Hey Jarvis" pop-up in the top-right of the MAIN monitor.

Runs as its OWN process (launched by jarvis), so a GUI hiccup can never slow down or crash Jarvis itself. It reads
one JSON command per line on stdin:
    {"cmd":"show"}                      -> appear, start listening
    {"cmd":"partial","text":"..."}      -> live transcription as you speak
    {"cmd":"state","state":"thinking"}  -> listening | thinking | speaking
    {"cmd":"reply","text":"..."}        -> show Jarvis's reply, then auto-hide
    {"cmd":"hide"}                      -> hide
and writes events back on stdout, one JSON per line:
    {"event":"cancel"}                  -> the user clicked the X (stop listening)
Launch it with:  pythonw -m jarvis.overlay

Note: on Windows, -transparentcolor (for rounded corners) and -alpha (fade) fight each other, so this uses
transparentcolor only and shows/hides instantly.
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

log = logging.getLogger("jarvis.overlay")

KEY = "#ff00fe"          # chroma-key colour Windows renders fully transparent -> rounded corners
PANEL = "#15161c"
BORDER = "#2b2d38"
RING_BG = "#2a2c36"
TEXT = "#f3f4f8"
MUTED = "#aeb4c2"
W = 360                  # window width
PAD = 18                 # screen-edge margin
COLORS = {"listening": "#3b82f6", "thinking": "#c164f2", "speaking": "#32d365"}
LABELS = {"listening": "Listening…", "thinking": "Thinking…", "speaking": "Jarvis"}


def _primary_size():
    """Width/height of the MAIN monitor (its top-left is always 0,0 on Windows), so the pop-up stays there."""
    try:
        u = ctypes.windll.user32
        return u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


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
        self.pulse = 0.0
        self.h = 70
        self.hide_after = None
        self._xbox = (0, 0, 0, 0)   # clickable rectangle of the close button
        self._place(self.h, off=True)   # start parked off-screen
        threading.Thread(target=self._reader, daemon=True).start()
        self.root.after(40, self._poll)
        self.root.after(33, self._tick)

    def _place(self, h: int, off: bool = False):
        pw, _ = _primary_size()
        x = pw - W - PAD
        self.root.geometry(f"{W}x{h + 20}+{x}+{-h - 600}" if off else f"{W}x{h + 20}+{x}+{PAD}")

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
        h = max(70, 40 + th)
        if h != self.h:
            self.h = h
            self._place(h)
        top = 6
        self._round(c, 2, top + 1, W - 2, top + h, 22, fill=BORDER, outline="")       # soft border
        self._round(c, 3, top + 2, W - 3, top + h - 1, 21, fill=PANEL, outline="")     # panel
        # pulsing ring = the Jarvis "icon"
        cx, cy = 38, top + h / 2
        rr = 12 + 2.2 * math.sin(self.pulse)
        c.create_oval(cx - 15, cy - 15, cx + 15, cy + 15, outline=RING_BG, width=2)
        c.create_arc(cx - 15, cy - 15, cx + 15, cy + 15, start=self.pulse * 40 % 360, extent=90,
                     style="arc", outline=accent, width=3)
        c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=accent, width=2)
        c.create_oval(cx - 3.5, cy - 3.5, cx + 3.5, cy + 3.5, fill=accent, outline="")
        # label + text
        c.create_text(72, top + 15, anchor="w", text=LABELS.get(self.state, "Jarvis"), fill=accent, font=self.sub_font)
        c.create_text(72, top + 24, anchor="nw", text=self.text or "", fill=TEXT, font=self.txt_font, width=W - 104)
        # close (X) button, top-right
        bx, by = W - 24, top + 20
        self._xbox = (bx - 13, by - 13, bx + 13, by + 13)
        c.create_oval(bx - 11, by - 11, bx + 11, by + 11, fill=RING_BG, outline="")
        c.create_text(bx, by - 1, text="✕", fill=MUTED, font=self.x_font)

    def _click(self, ev):
        x1, y1, x2, y2 = self._xbox
        if self.visible and x1 <= ev.x <= x2 and y1 <= ev.y <= y2:
            _emit("cancel")
            self._hide()

    def _tick(self):
        if self.visible:
            self.pulse += 0.16
            self._draw()
        self.root.after(33, self._tick)

    def _show(self):
        self.visible = True
        self.root.overrideredirect(True)    # re-assert: Windows can re-add the title bar after remap
        self._place(self.h)
        self.root.update_idletasks()        # force the on-screen geometry to apply NOW (else it can stay parked)
        self.root.attributes("-topmost", True)
        self.root.lift()

    def _hide(self):
        self.visible = False
        self.canvas.delete("all")
        self._place(self.h, off=True)       # park off-screen so it never blocks clicks

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
    def reply(self, text):     self._send({"cmd": "reply", "text": text})
    def hide(self):            self._send({"cmd": "hide"})


if __name__ == "__main__":
    try:
        Overlay().run()
    except Exception:
        pass
