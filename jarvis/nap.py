"""Nap: the PC looks asleep (locked, screens off) but stays awake, so the phone can still reach it and wake it.

Real sleep switches the network off with everything else, and a phone web page can't send the signal that wakes
a sleeping PC. So a nap locks the PC, turns the screens off and asks Windows not to sleep while it lasts; "wake"
from the phone turns the screens back on. Using the mouse or keyboard at the PC ends the nap as well. The price:
while it naps, the PC draws its normal idle power instead of almost none.
"""
import ctypes
import logging
import threading
import time
from ctypes import wintypes

log = logging.getLogger("jarvis.nap")

ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER = 0xFFFF, 0x0112, 0xF170
MOUSEEVENTF_MOVE = 0x0001
CHECK = 2        # seconds between looks at whether someone is using the PC
SETTLE = 5       # input in the first seconds is the tap that started the nap, not someone waking it

_lock = threading.Lock()
_wake = threading.Event()
_since = 0.0
_on_change = None   # fn() when a nap starts or ends (the phone shows a chip while it naps)


class _LASTINPUT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def is_on() -> bool:
    return bool(_since)


def since() -> float:
    return _since


def set_on_change(fn) -> None:
    global _on_change
    _on_change = fn


def _changed() -> None:
    if _on_change:
        try:
            _on_change()
        except Exception:
            log.debug("nap change callback failed", exc_info=True)


# ---------------------------------------------------------------- Windows

def _lock_pc() -> None:
    ctypes.windll.user32.LockWorkStation()


def _screens(on: bool) -> None:
    u = ctypes.windll.user32
    # PostMessage, not SendMessage: one hung window must not hang Jarvis
    u.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, -1 if on else 2)
    if on:   # some monitors only come back for real input: nudge the mouse a pixel and back
        u.mouse_event(MOUSEEVENTF_MOVE, 1, 0, 0, 0)
        u.mouse_event(MOUSEEVENTF_MOVE, -1, 0, 0, 0)


def _keep_awake(on: bool) -> None:
    """Asks Windows not to sleep (or stops asking). It belongs to the thread that calls it, so the nap's own
    thread does both."""
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


def _idle_seconds() -> float:
    info = _LASTINPUT(ctypes.sizeof(_LASTINPUT), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 1e9
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


# ---------------------------------------------------------------- the nap

def _hold() -> None:
    """Runs for the whole nap: keeps Windows awake and ends the nap when someone uses the PC."""
    _keep_awake(True)
    try:
        started = time.time()
        time.sleep(1)   # let the lock screen appear first, or it turns the screens straight back on
        if not _wake.is_set():
            _screens(False)
        while not _wake.wait(CHECK):
            if time.time() - started > SETTLE and _idle_seconds() < CHECK + 0.5:
                log.info("Someone used the PC: the nap is over.")
                _end(screens=False)   # they've woken the screens already
                break
    except Exception:
        log.exception("Nap failed")
        _end(screens=True)
    finally:
        _keep_awake(False)


def turn_on() -> str:
    global _since
    with _lock:
        if _since:
            return "It's already napping."
        _since = time.time()
        _wake.clear()
    _lock_pc()
    threading.Thread(target=_hold, daemon=True, name="nap").start()
    log.info("Napping: locked, screens off, staying reachable.")
    _changed()
    return ("Napping: locked, screens off, and I'm still here. Say \"wake up\" or tap Wake when you want it back.")


def _end(screens: bool) -> bool:
    global _since
    with _lock:
        if not _since:
            return False
        _since = 0.0
        _wake.set()
    if screens:
        _screens(True)
    _changed()
    return True


def turn_off() -> str:
    if not _end(screens=True):
        return "The PC isn't napping."
    log.info("Woken from a nap.")
    return "Awake. The screens are on and it's still locked, so sign in at the PC."
