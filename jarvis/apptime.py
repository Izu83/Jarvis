"""How long you spend in each app on the PC, for the evening recap.

Once a minute, the app in front counts as one minute, but only if you've touched the mouse or keyboard in the last
two minutes (a film playing to an empty room doesn't count). Kept for a week in .brain/apptime.json; it never leaves
the PC except as the recap's one line.
"""
import ctypes
import datetime as dt
import json
import logging
import threading
import time
from ctypes import wintypes

from .config import ROOT

log = logging.getLogger("jarvis.apptime")

PATH = ROOT / ".brain" / "apptime.json"
EVERY = 60          # seconds between looks
IDLE = 120          # longer than this without input = you're away
KEEP_DAYS = 7
SAVE_EVERY = 5      # looks between saves

# What a program is called in the recap (by its .exe name, lower case); anything else gets its own name, tidied.
NAMES = {"chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "opera": "Opera", "brave": "Brave",
         "code": "VS Code", "spotify": "Spotify", "discord": "Discord", "explorer": "File Explorer",
         "windowsterminal": "Terminal", "cmd": "Terminal", "powershell": "Terminal", "claude": "Claude",
         "steam": "Steam", "steamwebhelper": "Steam", "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint",
         "outlook": "Outlook", "olk": "Outlook", "teams": "Teams", "ms-teams": "Teams", "vlc": "VLC",
         "obs64": "OBS", "notepad": "Notepad", "telegram": "Telegram", "whatsapp": "WhatsApp", "slack": "Slack",
         "figma": "Figma", "photoshop": "Photoshop", "blender": "Blender", "devenv": "Visual Studio",
         "pycharm64": "PyCharm", "idea64": "IntelliJ", "zoom": "Zoom", "fc26": "EA SPORTS FC 26"}
# Windows' own bits that are "in front" without being an app you use
SKIP = {"lockapp", "searchhost", "shellexperiencehost", "startmenuexperiencehost", "textinputhost",
        "applicationframehost", "pythonw", "python", "rundll32", "dwm", "systemsettings"}

_lock = threading.Lock()
_days: dict[str, dict[str, int]] = {}
_started = False


class _LASTINPUT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def _idle_seconds() -> float:
    info = _LASTINPUT(ctypes.sizeof(_LASTINPUT), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


def _front_app() -> str | None:
    """The friendly name of the app in front, or None (nothing, or a bit of Windows)."""
    import psutil
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        exe = psutil.Process(pid.value).name()
    except (psutil.Error, ValueError):
        return None
    return name_of(exe)


def name_of(exe: str) -> str | None:
    stem = exe.rsplit(".", 1)[0] if exe.lower().endswith(".exe") else exe
    key = stem.lower()
    if key in SKIP or not key:
        return None
    return NAMES.get(key) or (stem[:1].upper() + stem[1:])


def _load() -> None:
    global _days
    try:
        _days = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _days = {}


def _save() -> None:
    with _lock:
        keep = sorted(_days)[-KEEP_DAYS:]
        data = {d: _days[d] for d in keep}
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(PATH)
    except OSError:
        log.debug("Couldn't save app time", exc_info=True)


def note(app: str, day: dt.date | None = None, minutes: int = 1) -> None:
    """Count minutes in an app (the sampler does this each minute)."""
    key = (day or dt.date.today()).isoformat()
    with _lock:
        today = _days.setdefault(key, {})
        today[app] = today.get(app, 0) + minutes


def today(day: dt.date | None = None) -> list[tuple[str, int]]:
    """[(app, minutes)] for the day, most used first."""
    with _lock:
        counts = dict(_days.get((day or dt.date.today()).isoformat(), {}))
    return sorted(counts.items(), key=lambda kv: -kv[1])


def _loop() -> None:
    looks = 0
    while True:
        time.sleep(EVERY)
        try:
            if _idle_seconds() <= IDLE:
                app = _front_app()
                if app:
                    note(app)
            looks += 1
            if looks % SAVE_EVERY == 0:
                _save()
        except Exception:
            log.debug("App time look failed", exc_info=True)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    _load()
    threading.Thread(target=_loop, daemon=True, name="apptime").start()


def duration(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h} h {m} min" if h and m else f"{h} h" if h else f"{m} min"
