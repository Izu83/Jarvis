"""Small PC actions and live status for the phone: now playing, CPU/RAM, clipboard, lock."""
import ctypes
import platform
import time

import psutil
import win32clipboard

from . import sysinfo
from . import windows as win

psutil.cpu_percent(None)  # the first reading is always 0, so take it now


def now_playing() -> dict | None:
    """Spotify's window title is "Artist - Song" while playing and "Spotify Premium/Free" while paused."""
    for w in win.list_windows():
        if w.app == "spotify":
            if w.title.lower().startswith("spotify"):
                return {"playing": False}
            artist, _, song = w.title.partition(" - ")
            return {"playing": True, "artist": artist.strip(), "title": (song or artist).strip()}
    return None


def status() -> dict:
    return {
        "host": platform.node(),
        "cpu": round(psutil.cpu_percent(None)),
        "ram": round(psutil.virtual_memory().percent),
        "uptime": int(time.time() - psutil.boot_time()),
        "top": (sysinfo.apps(1) or [None])[0],
        "music": now_playing(),
    }


def get_clipboard() -> str:
    win32clipboard.OpenClipboard()
    try:
        return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    except TypeError:  # clipboard holds an image/file, not text
        return ""
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def lock() -> None:
    ctypes.windll.user32.LockWorkStation()
