"""The answer as it's being written, for the phone: shown in the reply bubble until the real answer lands.

run_job points this at the phone command it's working on (set_sink); the brains call update() with the text so
far and clear() when it turns out to be commentary before a tool runs, not the answer. Updates are throttled,
and anything left unsent doesn't matter: the final answer replaces it all.
"""
import threading
import time

EVERY = 0.25   # seconds between updates to the phone

_lock = threading.Lock()
_sink = None
_last = 0.0


def set_sink(fn) -> None:
    global _sink, _last
    with _lock:
        _sink, _last = fn, 0.0


def update(text: str) -> None:
    global _last
    with _lock:
        sink, now = _sink, time.time()
        if sink is None or not text.strip() or now - _last < EVERY:
            return
        _last = now
    try:
        sink(text)
    except Exception:
        pass


def clear() -> None:
    """What was written so far was the brain thinking out loud before using a tool: take it off the bubble."""
    global _last
    with _lock:
        sink, _last = _sink, 0.0
    if sink is not None:
        try:
            sink("")
        except Exception:
            pass
