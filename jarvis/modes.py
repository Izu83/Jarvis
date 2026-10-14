"""Modes: named window layouts ("Jarvis, work mode") that run instantly, without asking Claude.

Stored in modes.json. Each mode lists, per monitor, which windows to open and how to tile them:
  "app:<Start menu name>"   an app (reuses its window if already open)
  "folder:<path>"           a File Explorer window ("folder:" = a plain one)
  "url:<https://...>"       opened in the browser (not placed)
Order matters: top -> bottom for "rows", left -> right for "columns".
"""
import json
import re

from . import apps
from . import windows as win
from .config import ROOT

MODES_PATH = ROOT / "modes.json"
ICONS = ("briefcase", "code", "gamepad", "book", "music", "film", "moon", "chat", "house", "sparkles")

DEFAULT_MODES = {
    "work": {
        "icon": "briefcase",
        "say": "Work mode is ready.",
        "screens": [
            {"monitor": 1, "layout": "rows", "windows": ["folder:", "app:Claude", "app:Spotify"]},
            {"monitor": 3, "layout": "columns", "windows": ["app:Opera", "folder:"]},
            {"monitor": 2, "layout": "maximize", "windows": ["app:Visual Studio Code"]},
        ],
    },
}


def load() -> dict:
    if not MODES_PATH.exists():
        save(DEFAULT_MODES)
    return json.loads(MODES_PATH.read_text(encoding="utf-8"))


def save(modes: dict) -> None:
    MODES_PATH.write_text(json.dumps(modes, indent=2), encoding="utf-8")


def _label(item: str) -> str:
    kind, _, target = item.partition(":")
    if kind == "folder":
        return f"File Explorer ({target})" if target else "File Explorer"
    return target


def summary(mode: dict) -> str:
    return " · ".join(
        f"Monitor {s['monitor']}: " + ", ".join(_label(i) for i in s["windows"]) for s in mode["screens"]
    )


def overview() -> list[dict]:
    """What the phone shows: name, icon and a one-line summary per mode."""
    return [{"name": n, "icon": m.get("icon", "sparkles"), "summary": summary(m)} for n, m in load().items()]


def put(name: str, screens: list[dict], say: str = "", icon: str = "sparkles") -> str:
    name = name.strip().lower().removesuffix(" mode").strip()
    if not name:
        raise ValueError("A mode needs a name.")
    monitors = {m.number for m in win.list_monitors()}
    for s in screens:
        if s["monitor"] not in monitors:
            raise ValueError(f"There is no monitor {s['monitor']}.")
        if s.get("layout", "auto") not in win.LAYOUTS:
            raise ValueError(f"Unknown layout {s.get('layout')}.")
        for item in s["windows"]:
            if item.partition(":")[0] not in ("app", "folder", "url"):
                raise ValueError(f"'{item}' must start with app:, folder: or url:")
    modes = load()
    modes[name] = {"icon": icon if icon in ICONS else "sparkles", "say": say or f"{name.title()} mode is ready.",
                   "screens": screens}
    save(modes)
    return f"Saved {name} mode: {summary(modes[name])}"


def remove(name: str) -> str:
    modes = load()
    key = name.strip().lower().removesuffix(" mode").strip()
    if key not in modes:
        return f"There's no mode called {name}."
    del modes[key]
    save(modes)
    return f"Deleted {key} mode."


def run(name: str) -> str:
    modes = load()
    key = name.strip().lower().removesuffix(" mode").strip()
    if key not in modes:
        return f"There's no {name} mode. Known modes: {', '.join(modes) or 'none'}."
    mode = modes[key]
    claimed: set[int] = set()
    problems: list[str] = []
    for screen in mode["screens"]:
        placed = []
        for item in screen["windows"]:
            kind, _, target = item.partition(":")
            if kind == "url":
                apps.open_target(target)
                continue
            if kind == "app":
                msg, w = apps.launch_app(target, skip=claimed)
            else:
                msg, w = apps.launch_folder(target, reuse=True, skip=claimed)
            if w:
                placed.append(w)
                claimed.add(w.hwnd)
            else:
                problems.append(_label(item))
        if placed:
            win.arrange(placed, screen["monitor"], screen.get("layout", "auto"))
    reply = mode.get("say") or f"{key.title()} mode is ready."
    if problems:
        reply += f" I couldn't place {', '.join(problems)}."
    return reply


_MODE_COMMAND = re.compile(
    r"^(?:(?:start|activate|enable|launch|open|run|turn on|switch to|go to|go into|put me in)\s+)?"
    r"(?:the\s+|my\s+)?(?:mode\s+)?(?P<name>.+?)(?:\s+mode)?$"
)


def match(text: str) -> str | None:
    """If the text is just 'work mode' / 'start work mode' etc., return the mode name."""
    t = re.sub(r"[^\w\s]", " ", text.lower())
    t = re.sub(r"^\s*(hey\s+)?(jarvis|jervis)\s+", "", t)
    t = re.sub(r"\s+(please|now)\s*$", "", t)
    t = " ".join(t.split())
    if "mode" not in t.split():
        return None
    m = _MODE_COMMAND.match(t)
    name = m.group("name").strip() if m else ""
    return name if name in load() else None
