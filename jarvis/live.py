"""The phone's live screen: a stream of pictures of one monitor, as fast as the link carries (up to FPS).

The phone says start / stop and confirms every picture it shows (ack). At most WINDOW pictures are on their way at
once, so a slow link gets fewer pictures rather than a growing delay, and when the link keeps the window full the
pictures get smaller (LEVELS) until it keeps up. An unchanged screen isn't sent again; a tiny "still" goes every
second instead, so the phone knows the stream is alive. Pictures are grabbed with mss (~25 ms a monitor; PIL's
ImageGrab took ~125 ms, which alone capped it at about 7 a second).
"""
import io
import logging
import threading
import time

from PIL import Image

from . import windows as win

log = logging.getLogger("jarvis.live")

FPS = 20
WINDOW = 6            # pictures sent but not yet confirmed by the phone (covers a ~300 ms round trip at 20/s)
IDLE_STOP = 8         # seconds without a confirmation: the phone has gone (closed, asleep), stop streaming
STILL_EVERY = 1.0     # an unchanged screen: say so this often
SHARP_AFTER = 0.4     # the screen stopped changing this long ago: send it once at full size, for zooming in
# While it moves: (shrink factor, JPEG quality), best first. Halving with Image.reduce is ~2x faster than resize
# (a 1920 monitor: ~17 ms to shrink and encode, 37 KB), which is what makes 20 a second possible.
LEVELS = [(2, 60), (2, 50), (3, 50), (4, 45)]
SHARP_QUALITY = 72


class LiveStream:
    def __init__(self, send, labels):
        self.send = send          # fn(header: dict, jpeg: bytes | None)
        self.labels = labels      # fn(monitor number) -> "Left" / "Main" / ...
        self.cond = threading.Condition()
        self.monitor = None
        self.running = False
        self.seq = self.acked = 0
        self.last_ack = 0.0
        self.forced = False
        self.wake = threading.Event()   # cuts short the wait between looks at a still screen (a tap, a restart)
        self.thread = None

    def start(self, monitor=None) -> None:
        with self.cond:
            self.monitor = int(monitor) if monitor else None
            self.last_ack = time.time()
            self.acked = self.seq        # a fresh start: nothing is "on the way" any more
            self.forced = True
            self.running = True
            self.cond.notify_all()
            self.wake.set()
            if not (self.thread and self.thread.is_alive()):
                self.thread = threading.Thread(target=self._run, daemon=True, name="live-screen")
                self.thread.start()

    def stop(self) -> None:
        with self.cond:
            self.running = False
            self.cond.notify_all()

    def ack(self, seq: int) -> None:
        with self.cond:
            self.acked = max(self.acked, int(seq))
            self.last_ack = time.time()
            self.cond.notify_all()

    def poke(self) -> None:
        """Something was clicked or typed: send the next picture right away, changed or not."""
        with self.cond:
            self.forced = True
            self.cond.notify_all()
        self.wake.set()

    def _run(self) -> None:
        import mss
        level, calm, slow, last_sig, last_sent, sharp, changed = 0, 0, 0, None, 0.0, True, time.time()
        log.info("Live screen started.")
        try:
            with mss.MSS() as sct:
                while True:
                    with self.cond:
                        waited = time.time()
                        while self.running and self.seq - self.acked >= WINDOW:
                            if time.time() - self.last_ack > IDLE_STOP:
                                self.running = False
                                break
                            self.cond.wait(0.05)
                        waited = time.time() - waited
                        if not self.running:
                            break
                        if time.time() - self.last_ack > IDLE_STOP:
                            log.info("Live screen: the phone stopped confirming; stopping.")
                            self.running = False
                            break
                        forced, self.forced, number = self.forced, False, self.monitor
                    # the link keeps being the bottleneck: smaller pictures; it's kept up a while: better ones
                    if waited > 0.15:
                        slow, calm = slow + 1, 0
                        if slow >= 3 and level < len(LEVELS) - 1:
                            level, slow = level + 1, 0
                    elif waited <= 0.03:
                        slow, calm = 0, calm + 1
                        if calm >= 60 and level > 0:
                            level, calm = level - 1, 0
                    started = time.time()
                    try:
                        if not number:
                            from .screens import _default_monitor
                            number = self.monitor = _default_monitor()
                        b = win.get_monitor(number).bounds
                        raw = sct.grab({"left": b.x, "top": b.y, "width": b.w, "height": b.h})
                    except Exception as e:
                        log.warning("Live screen: couldn't grab monitor %s (%s).", number, e)
                        time.sleep(0.5)
                        continue
                    sig = raw.bgra[::9973]   # a quick fingerprint: has anything on screen changed?
                    if sig == last_sig and not forced:
                        if not sharp and started - last_sent >= SHARP_AFTER:
                            sharp = True   # it's settled: one full-size picture, so zoomed-in text is crisp
                        else:
                            if started - last_sent >= STILL_EVERY:   # the phone confirms these too: still there
                                self.send({"still": True, "seq": self.seq, "monitor": number}, None)
                                last_sent = started
                            # look less often once it's been still a while (grabbing costs ~25 ms of CPU);
                            # a tap wakes it at once
                            self.wake.wait(1 / FPS if started - changed < 2 else 0.12)
                            self.wake.clear()
                            continue
                        factor, quality = 1, SHARP_QUALITY
                    else:
                        sharp, changed = False, started
                        factor, quality = LEVELS[level]
                    last_sig = sig
                    img = Image.frombuffer("RGB", raw.size, raw.bgra, "raw", "BGRX", 0, 1)
                    if factor > 1:
                        img = img.reduce(factor)
                    buf = io.BytesIO()
                    img.save(buf, "JPEG", quality=quality)
                    with self.cond:
                        self.seq += 1
                        seq = self.seq
                    mons = [{"monitor": m.number, "label": self.labels(m.number)}
                            for m in sorted(win.list_monitors(), key=lambda m: m.bounds.x)]
                    self.send({"seq": seq, "monitor": number, "w": b.w, "h": b.h, "monitors": mons, "level": level},
                              buf.getvalue())
                    last_sent = time.time()
                    time.sleep(max(0.0, 1 / FPS - (last_sent - started)))
        except Exception:
            log.exception("Live screen failed")
        finally:
            log.info("Live screen stopped.")
