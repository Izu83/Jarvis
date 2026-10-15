"""Small PC actions and live status for the phone: now playing, CPU/RAM, clipboard, lock."""
import ctypes
import time

import psutil
import win32clipboard

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


IDLE_PROCESSES = {"system idle process", "idle", "system", "registry", "memory compression"}
FRIENDLY = {
    "msedgewebview2": "Web apps (WebView2)", "dwm": "Windows graphics", "explorer": "File Explorer",
    "code": "VS Code", "msedge": "Edge", "chrome": "Chrome", "opera": "Opera", "claude": "Claude",
    "spotify": "Spotify", "discord": "Discord", "steam": "Steam", "steamwebhelper": "Steam",
    "python": "Python", "node": "Node.js", "antimalware service executable": "Windows Defender",
    "msmpeng": "Windows Defender", "searchhost": "Windows Search",
}


def busiest_app() -> dict | None:
    """The app using the most CPU since the last call (psutil reuses Process objects between calls)."""
    usage: dict[str, float] = {}
    for p in psutil.process_iter(["name"]):
        try:
            name = (p.info["name"] or "").removesuffix(".exe")
            if name.lower() in IDLE_PROCESSES:
                continue
            name = FRIENDLY.get(name.lower(), name)
            usage[name] = usage.get(name, 0.0) + p.cpu_percent(None)  # an app's processes add up
        except psutil.Error:
            continue
    if not usage:
        return None
    name, pct = max(usage.items(), key=lambda kv: kv[1])
    return {"name": name.title() if name.islower() else name, "cpu": round(pct / psutil.cpu_count())}


busiest_app()  # prime the per-process counters


def status() -> dict:
    return {
        "cpu": round(psutil.cpu_percent(None)),
        "ram": round(psutil.virtual_memory().percent),
        "uptime": int(time.time() - psutil.boot_time()),
        "top": busiest_app(),
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
