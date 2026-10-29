"""Away mode: while you're out, a real hand on the mouse or keyboard gets you a webcam photo on the phone.

Low-level Windows input hooks see every key and mouse movement and mark the ones a program sent (Jarvis's own
clicks and typing, the live screen from the phone) as "injected", so only physical input counts. Hooks run only
while away mode is on. After an alert, the next one waits COOLDOWN seconds.
"""
import ctypes
import logging
import threading
import time
from ctypes import wintypes

log = logging.getLogger("jarvis.away")

COOLDOWN = 120
SETTLE = 3            # ignore input this many seconds after turning it on (you're still at the desk)
WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
LLKHF_INJECTED, LLMHF_INJECTED = 0x10, 0x01
WM_QUIT = 0x0012

_state = {"on": False, "since": 0.0, "last_alert": 0.0, "thread": None, "tid": 0, "own_until": 0.0}
_on_intruder = None   # fn() run (on its own thread) when someone uses the PC while you're away
_lock = threading.Lock()


class _KBD(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _MOUSE(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


def set_handler(fn) -> None:
    global _on_intruder
    _on_intruder = fn


def is_on() -> bool:
    return _state["on"]


def note_own_input(seconds: float = 2.0) -> None:
    """Jarvis is about to move the mouse itself (SetCursorPos isn't always marked as injected): ignore a moment."""
    _state["own_until"] = time.time() + seconds


def _physical() -> None:
    now = time.time()
    if not _state["on"] or now - _state["since"] < SETTLE or now < _state["own_until"]:
        return
    with _lock:
        if now - _state["last_alert"] < COOLDOWN:
            return
        _state["last_alert"] = now
    log.info("Away mode: someone is using the PC.")
    if _on_intruder:
        threading.Thread(target=_on_intruder, daemon=True, name="away-alert").start()


def _hook_loop() -> None:
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    _state["tid"] = kernel32.GetCurrentThreadId()

    def on_key(code, wparam, lparam):
        if code >= 0 and not (ctypes.cast(lparam, ctypes.POINTER(_KBD)).contents.flags & LLKHF_INJECTED):
            _physical()
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def on_mouse(code, wparam, lparam):
        if code >= 0 and not (ctypes.cast(lparam, ctypes.POINTER(_MOUSE)).contents.flags & LLMHF_INJECTED):
            _physical()
        return user32.CallNextHookEx(None, code, wparam, lparam)

    kp, mp = _HOOKPROC(on_key), _HOOKPROC(on_mouse)   # keep references: the hooks call back into these
    hk = user32.SetWindowsHookExW(WH_KEYBOARD_LL, kp, None, 0)
    hm = user32.SetWindowsHookExW(WH_MOUSE_LL, mp, None, 0)
    if not hk or not hm:
        log.error("Away mode couldn't watch the keyboard and mouse (error %s).", kernel32.GetLastError())
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:   # hooks need a message loop on this thread
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    for h in (hk, hm):
        if h:
            user32.UnhookWindowsHookEx(ctypes.c_void_p(h))


def turn_on() -> str:
    if _state["on"]:
        return "Away mode is already on."
    _state.update(on=True, since=time.time(), last_alert=0.0)
    t = threading.Thread(target=_hook_loop, daemon=True, name="away-hooks")
    _state["thread"] = t
    t.start()
    log.info("Away mode on.")
    return ("Away mode is on. If anyone uses the mouse or keyboard, I'll send you a webcam photo. "
            "Type /away again when you're back.")


def turn_off() -> str:
    if not _state["on"]:
        return "Away mode is already off."
    _state["on"] = False
    if _state["tid"]:
        ctypes.windll.user32.PostThreadMessageW(_state["tid"], WM_QUIT, 0, 0)
    _state["tid"] = 0
    log.info("Away mode off.")
    return "Welcome back. Away mode is off."


def toggle() -> str:
    return turn_off() if _state["on"] else turn_on()
