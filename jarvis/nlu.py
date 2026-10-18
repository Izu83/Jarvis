"""Local command understanding — handle the common requests with no Claude (so no tokens).

try_handle(text) returns a spoken-style answer if it fully understood and did the command, or None to let the
Claude brain handle it. It only acts when it's confident; anything compound/unusual falls back to Claude so
nothing is half-done.
"""
import logging
import re

from . import apps, modes, tools
from . import windows as win
from .config import settings

log = logging.getLogger("jarvis.nlu")

WAKE = re.compile(r"^\s*(hey\s+|ok\s+|okay\s+)?(jarvis|jervis|javis)[\s,]*", re.I)
FOLDER_WORDS = {"file explorer", "explorer", "files", "file folder", "a file folder", "folder", "a folder"}
POSITIONS = ("left", "right", "vertical", "main", "middle", "primary")
LAYOUTS = {"side by side": "columns", "columns": "columns", "stacked": "rows", "rows": "rows",
           "grid": "grid", "maximize": "maximize", "maximized": "maximize", "full screen": "maximize",
           "fullscreen": "maximize", "normal": "auto", "normally": "auto", "organize": "auto", "organized": "auto"}
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}


def clean(text: str) -> str:
    return WAKE.sub("", text.strip()).strip()


# ---------------------------------------------------------------- monitors

def monitor_from(text: str) -> int | None:
    t = text.lower()
    m = re.search(r"(?:monitor|display|screen)\s*(\d)", t) or re.search(r"\b(\d)(?:st|nd|rd|th)?\s*(?:monitor|display|screen)", t)
    if m:
        return int(m.group(1))
    for word, num in ORDINALS.items():
        if re.search(rf"\b{word}\s+(?:monitor|display|screen)", t):
            return num
    names = settings["monitor_names"]  # position word -> the monitor numbered with that name
    for pos in POSITIONS:
        if re.search(rf"\b{pos}\b", t):
            for num, name in names.items():
                if pos in name.lower():
                    return int(num)
    return None


def layout_from(text: str) -> str:
    t = text.lower()
    for word, lay in LAYOUTS.items():
        if word in t:
            return lay
    return "auto"


# ---------------------------------------------------------------- simple intents

def _volume(key: str, times: int = 3) -> str:
    tools.run("media_key", {"key": key, "times": times})
    return {"volume_up": "Turned the volume up.", "volume_down": "Turned the volume down.",
            "mute": "Muted."}.get(key, "Done.")


SIMPLE = []


def simple(pattern):
    def deco(fn):
        SIMPLE.append((re.compile(pattern, re.I), fn))
        return fn
    return deco


@simple(r"^(pause|stop|unpause|resume|play)(\s+(the\s+)?(music|song|spotify|it))?$")
def _play_pause(m, text):
    tools.run("media_key", {"key": "play_pause"})
    return "Done."


@simple(r"^(next|skip)(\s+(song|track|one))?$")
def _next(m, text):
    tools.run("media_key", {"key": "next"})
    return "Skipped."


@simple(r"^(previous|back|last)(\s+(song|track|one))?$|^go back a song$")
def _prev(m, text):
    tools.run("media_key", {"key": "previous"})
    return "Went back."


@simple(r"\b(volume|turn it|turn the volume)\b.*\b(up|louder|higher)\b|^louder$")
def _vol_up(m, text):
    return _volume("volume_up", 5 if re.search(r"\b(a lot|way|much)\b", text.lower()) else 3)


@simple(r"\b(volume|turn it|turn the volume)\b.*\b(down|lower|quieter)\b|^quieter$")
def _vol_down(m, text):
    return _volume("volume_down", 5 if re.search(r"\b(a lot|way|much)\b", text.lower()) else 3)


@simple(r"^(mute|unmute)( the (sound|volume|audio))?$")
def _mute(m, text):
    return _volume("mute", 1)


@simple(r"^lock( the| my)? (pc|computer|screen)$|^lock it$|^lock$")
def _lock(m, text):
    return tools.run("lock_pc", {})


@simple(r"\b(disk space|disk usage|how much (space|storage|gb)|free space|storage left)\b")
def _disk(m, text):
    drive = re.search(r"\b([cdef])\s*(?:disk|drive|:)", text.lower())
    return tools.run("disk_usage", {"path": f"{drive.group(1).upper()}:\\" if drive else "C:\\"})


@simple(r"\b(clean ?up|free up space|what can i (delete|clean)|clear (cache|space)|junk|temp files)\b")
def _cleanup(m, text):
    return tools.run("cleanup_suggestions", {}) + "\n\nTell me which of these to clear and I'll ask before deleting."


@simple(r"\b(system|pc) (info|stats|status)\b|\b(cpu|gpu|ram|memory) (usage|temp|temperature)\b|how('?s| is) my (pc|computer)")
def _sysinfo(m, text):
    return tools.run("system_info", {})


@simple(r"\b(take|show|send)\s*(me\s+)?(a\s+)?(screenshot|screenshots|screens|my screens?)\b")
def _screens(m, text):
    return "Tap Screens at the bottom of the app to see your monitors."


@simple(r"^(what'?s|what is) open\b|^list (the )?(open )?(windows|apps)$")
def _whats_open(m, text):
    wins = win.list_windows()
    if not wins:
        return "Nothing is open."
    apps_open = sorted({w.app.title() for w in wins})
    return "Open: " + ", ".join(apps_open) + "."


@simple(r"\b(start|stop|turn on|turn off|toggle)\s+(the\s+)?(gesture|gestures|hand)\s*(control|controls|mode|app|recognition)?\b")
def _gesture(m, text):
    from . import gesture
    action = "stop" if re.search(r"\b(stop|turn off)\b", text.lower()) else "start" if re.search(r"\b(start|turn on)\b", text.lower()) else "toggle"
    return {"start": gesture.start, "stop": gesture.stop, "toggle": gesture.toggle}[action]()


@simple(r"^(sleep|go to sleep|restart|reboot|shut ?down|turn off|sign out|log out)( the| my)?( pc| computer)?$")
def _power(m, text):
    t = text.lower()
    action = ("restart" if re.search(r"restart|reboot", t) else "shutdown" if re.search(r"shut ?down|turn off", t)
              else "sign_out" if re.search(r"sign out|log out", t) else "sleep")
    return tools.run("power_action", {"action": action})


# ---------------------------------------------------------------- "play X on spotify"

def _play_query(text: str) -> str | None:
    m = re.match(r"^play\s+(.+?)(?:\s+(?:on|in|with)\s+spotify)?$", text.strip(), re.I)
    if not m:
        return None
    q = m.group(1).strip()
    if q.lower() in ("music", "something", "a song", "the music"):
        q = ""   # just resume / shuffle
    return tools.run("play_music", {"query": q})


# ---------------------------------------------------------------- opening apps/folders/projects + arranging

OPEN_VERB = re.compile(r"^(open|launch|start|run|bring up|pull up|fire up|get me)\s+", re.I)
SPLIT = re.compile(r"\s*,\s*|\s+and\s+|\s*&\s*|\s+then\s+", re.I)


def _target_window(piece: str, opened: set[int]):
    """Open one target (app / folder / project / url) and return its window, or (msg, None) on failure."""
    p = piece.strip().strip(".").removeprefix("the ").strip()
    low = p.lower()
    if low in FOLDER_WORDS:
        return apps.launch_folder("", skip=opened)
    if low.startswith(("http://", "https://", "www.")) or re.search(r"\.(com|net|org|io|gg|tv)\b", low):
        url = p if p.startswith("http") else "https://" + p.lstrip("/")
        return apps.open_target(url), None
    # "the X project [on the D disk]" -> a folder named X
    proj = re.match(r"(.+?)\s+project(?:\s+(?:on|in)\s+(?:the\s+)?([a-z])\s*(?:disk|drive))?$", low)
    if proj:
        name = proj.group(1).strip()
        root = f"{proj.group(2).upper()}:\\" if proj.group(2) else ""
        hits = apps.find_folder(name, root)
        if hits:
            return apps.launch_folder(hits[0], reuse=True, skip=opened)
        return f"I couldn't find a folder called '{name}'.", None
    app = apps.find_app(p)
    if app:
        return apps.launch_app(p, skip=opened)
    return None, None   # unknown -> signal fallback to Claude


def _handle_open(text: str, progress) -> str | None:
    # pull off a trailing arrangement phrase like "... on my third monitor" / "... organized on monitor 3"
    monitor = monitor_from(text)
    layout = layout_from(text)
    body = text
    if monitor is not None:
        body = re.split(r"\b(?:and\s+)?(?:put|place|arrange|organize|organise|set|move)?\s*(?:them|it|everything)?\s*(?:on|to)\b",
                        text, 1, flags=re.I)[0]
    body = OPEN_VERB.sub("", body.strip())
    # also strip a leading "open" that applied to the whole list
    pieces = [p for p in SPLIT.split(body) if p.strip()]
    if not pieces:
        return None
    opened: set[int] = set()
    windows, labels, problems = [], [], []
    for piece in pieces:
        if progress:
            progress(f"Opening {piece.strip().strip('.')[:30]}…")
        msg, w = _target_window(piece, opened)
        if msg is None and w is None:
            return None   # a target we don't understand -> let Claude do the whole thing
        if w:
            windows.append(w)
            opened.add(w.hwnd)
            labels.append(w.app.title() if w.app != "explorer" else "File Explorer")
        elif isinstance(msg, str) and msg.lower().startswith(("i couldn't", "error")):
            problems.append(msg)
        elif isinstance(msg, str):
            labels.append(piece.strip().strip("."))
    if monitor is not None and windows:
        if progress:
            progress(f"Arranging on monitor {monitor}…")
        try:
            win.arrange(windows, monitor, layout)
            where = settings["monitor_names"].get(str(monitor), f"monitor {monitor}")
            reply = f"Opened {_join(labels)} on the {where}."
        except Exception as e:
            reply = f"Opened {_join(labels)}, but couldn't arrange them: {e}"
    else:
        reply = f"Opened {_join(labels)}." if labels else "Done."
    if problems:
        reply += " " + " ".join(problems)
    return reply


def _join(items: list[str]) -> str:
    items = list(dict.fromkeys(items))  # de-dup, keep order
    if len(items) <= 1:
        return items[0] if items else "nothing"
    return ", ".join(items[:-1]) + " and " + items[-1]


# ---------------------------------------------------------------- make a folder / project

@simple(r"^(make|create|new|add)\s+(a\s+)?(new\s+)?(folder|project|directory|dir)\s+(?:called|named)\s+"
        r"[\"'\u201c]?([\w .\-]+?)[\"'\u201d]?(?:\s+(?:in|inside|under|into)\s+(?:the\s+)?(.+?)(?:\s+folder)?)?$")
def _make_folder(m, text):
    from . import files
    if re.search(r"\b(and|then|also)\b|,", text, re.I):   # compound command -> let Claude do all the steps
        return None
    name = m.group(5).strip()
    parent_raw = (m.group(6) or "").strip().lower()
    projects = settings["known_folders"].get("projects", r"D:\Projects")
    if not parent_raw or parent_raw in ("project", "projects", "project folder", "projects folder", "the project"):
        parent = projects
    else:
        try:
            parent = str(files.resolve(parent_raw))
        except Exception:
            parent = projects
    return tools.run("create_folder", {"path": f"{parent}\\{name}"})


# ---------------------------------------------------------------- window actions

@simple(r"^(close|minimi[sz]e|maximi[sz]e)\s+(.+)$")
def _window_action(m, text):
    action = {"close": "close", "minimize": "minimize", "minimise": "minimize",
              "maximize": "maximize", "maximise": "maximize"}[m.group(1).lower()]
    target = m.group(2).strip().strip(".")
    target = re.sub(r"^(the\s+)?", "", target)
    target = re.sub(r"\s+(window|app)$", "", target)
    try:
        return tools.run("window_action", {"window": target, "action": action})
    except Exception:
        return None


# ---------------------------------------------------------------- entry point

def try_handle(text: str, progress=None) -> str | None:
    cmd = clean(text)
    if not cmd:
        return None
    low = cmd.lower()
    try:
        for pattern, fn in SIMPLE:
            m = pattern.search(cmd)   # match on original case so captured names keep their capitals
            if m:
                return fn(m, cmd)
        if low.startswith("play "):
            r = _play_query(cmd)
            if r is not None:
                return r
        if OPEN_VERB.match(low):
            return _handle_open(cmd, progress)
    except Exception:
        log.exception("local parse failed for %r; falling back to Claude", cmd)
        return None
    return None
