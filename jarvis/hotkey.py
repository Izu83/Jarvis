"""A global keyboard shortcut (default Ctrl+Alt+J) that starts Jarvis listening, as if you'd said the wake word.

Uses Windows' own RegisterHotKey so it works from any app, with no extra dependency. Each listener runs a tiny
message loop on its own thread; toggling the setting off posts WM_QUIT to that thread, which unregisters the key."""
import ctypes
import ctypes.wintypes
import logging
import threading

log = logging.getLogger("jarvis.hotkey")

user32 = ctypes.windll.user32

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000   # don't fire repeatedly while the key is held
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
_HOTKEY_ID = 1

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "ctl": MOD_CONTROL,
         "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN, "super": MOD_WIN}


def _parse(combo: str):
    """'Ctrl+Alt+J' -> (modifiers, virtual-key). Returns None if it can't be understood."""
    mods, vk = 0, None
    for part in (combo or "").replace(" ", "").split("+"):
        if not part:
            continue
        low = part.lower()
        if low in _MODS:
            mods |= _MODS[low]
        elif len(part) == 1:
            vk = ord(part.upper())   # letters/digits map straight to their virtual-key code
        else:
            return None
    if vk is None or mods == 0:   # need at least one modifier + one key, or Windows won't register it
        return None
    return mods, vk


class HotkeyListener:
    def __init__(self, combo: str, on_press):
        self.combo = combo
        self.on_press = on_press
        self._thread = None
        self._tid = None

    def start(self) -> bool:
        parsed = _parse(self.combo)
        if not parsed:
            log.warning("Couldn't understand the shortcut %r; ignoring it.", self.combo)
            return False
        self._thread = threading.Thread(target=self._run, args=parsed, daemon=True, name="hotkey")
        self._thread.start()
        return True

    def _run(self, mods: int, vk: int) -> None:
        self._tid = user32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, _HOTKEY_ID, mods | MOD_NOREPEAT, vk):
            log.warning("Couldn't register the shortcut %s (another app may already be using it).", self.combo)
            return
        log.info("Shortcut %s is active — press it to talk to Jarvis.", self.combo)
        msg = ctypes.wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self.on_press()
                    except Exception:
                        log.debug("hotkey callback failed", exc_info=True)
        finally:
            user32.UnregisterHotKey(None, _HOTKEY_ID)

    def stop(self) -> None:
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)   # ends GetMessageW -> unregisters the key


# --- A single listener for the app, so the phone/voice can turn it on and off at any time ---
_listener: HotkeyListener | None = None
_combo = "Ctrl+Alt+J"
_on_press = None


def configure(combo: str, on_press) -> None:
    """Remember which keys to watch and what to do when they're pressed (called once at startup)."""
    global _combo, _on_press
    _combo = combo or "Ctrl+Alt+J"
    _on_press = on_press


def set_enabled(on: bool) -> bool:
    """Turn the shortcut on or off right now. Returns whether it's active afterwards."""
    global _listener
    if on:
        if _listener is not None:
            return True
        if _on_press is None:   # voice/mic isn't running, so there's nothing to listen with
            return False
        listener = HotkeyListener(_combo, _on_press)
        if not listener.start():
            return False
        _listener = listener
        return True
    if _listener is not None:
        _listener.stop()
        _listener = None
    return False


def is_enabled() -> bool:
    return _listener is not None
