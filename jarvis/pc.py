"""Small PC actions and live status for the phone: now playing, CPU/RAM, clipboard, lock."""
import ctypes
import platform
import time

import psutil
import win32clipboard

from . import media, sysinfo
from . import windows as win

def now_playing() -> dict | None:
    """Any app via Windows' media session (with album art); falls back to reading Spotify's window title."""
    info = media.now_playing()
    if info:
        return info
    for w in win.list_windows():
        if w.app == "spotify":  # "Artist - Song" while playing, "Spotify Premium/Free" while paused
            if w.title.lower().startswith("spotify"):
                return {"playing": False, "app": "Spotify"}
            artist, _, song = w.title.partition(" - ")
            return {"playing": True, "artist": artist.strip(), "title": (song or artist).strip(), "app": "Spotify"}
    return None


_KIND = None


def kind() -> str:
    """"laptop" when this computer has a battery, else "desktop" (for the phone's device switcher)."""
    global _KIND
    if _KIND is None:
        try:
            _KIND = "laptop" if psutil.sensors_battery() is not None else "desktop"
        except Exception:
            _KIND = "desktop"
    return _KIND


def device_name() -> str:
    """What the phone calls this computer: the device_name setting, else "Laptop" or "PC"."""
    from .config import settings
    return (settings.get("device_name") or "").strip() or ("Laptop" if kind() == "laptop" else "PC")


def status() -> dict:
    return {
        "host": platform.node(),
        "name": device_name(),
        "kind": kind(),
        "cpu": sysinfo.cpu_now(),
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
