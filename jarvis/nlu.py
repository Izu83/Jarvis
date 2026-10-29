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
# "X and Y", "X, then Y": more than one request. The quick patterns below only ever do one thing, so a compound
# command goes to Claude, which does every part instead of silently doing just the first.
COMPOUND = re.compile(r"\b(and|then|also|after that|afterwards|but|before)\b|[,;]", re.I)


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


def simple(pattern, compound_ok: bool = False):
    """Register a quick pattern. compound_ok=True lets it run even when the text contains "and"/"then"/commas
    (e.g. a reminder's message: "remind me at 5 to call mom and dad")."""
    def deco(fn):
        SIMPLE.append((re.compile(pattern, re.I), fn, compound_ok))
        return fn
    return deco


def _is_playing() -> bool | None:
    """True/False if Windows knows whether something is playing, None if it can't tell."""
    try:
        from . import pc
        info = pc.now_playing()
    except Exception:
        return None
    return None if info is None else bool(info.get("playing"))


@simple(r"^(pause|stop|unpause|resume|play)(\s+(the\s+)?(music|song|spotify|it))?$")
def _play_pause(m, text):
    # play/pause is a toggle, so check first: "stop" must never START paused music, "resume" never pause it.
    want_playing = m.group(1).lower() in ("unpause", "resume", "play")
    playing = _is_playing()
    if playing is not None and playing == want_playing:
        return "It's already playing." if playing else "Nothing is playing."
    tools.run("media_key", {"key": "play_pause"})
    return "Resumed." if want_playing else "Paused."


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


@simple(r"\b(disk space|disk usage|free space|storage left|space (is )?left|how full is|"
        r"how much (space|storage|gb)( is)? (left|free|do i have|is there))\b")
def _disk(m, text):
    drive = re.search(r"\b([cdef])\s*(?:disk|drive|:)", text.lower())
    return tools.run("disk_usage", {"path": f"{drive.group(1).upper()}:\\" if drive else "C:\\"})


@simple(r"^(clean ?up( my)?( (pc|computer|disk|drive|c drive|c disk|space))?|free up (some )?(disk )?space|"
        r"what can i (delete|clean( up)?|remove)|clear (the )?(cache|caches|junk|temp files|space)|"
        r"(find|show me|any) junk( files)?)( please)?$")
def _cleanup(m, text):
    return tools.run("cleanup_suggestions", {}) + "\n\nTell me which of these to clear and I'll ask before deleting."


@simple(r"\b(system|pc) (info|stats|status)\b|\b(cpu|gpu|ram|memory) (usage|temp|temperature)\b|how('?s| is) my (pc|computer)")
def _sysinfo(m, text):
    return tools.run("system_info", {})


@simple(r"^(take|show|send|grab)( me)?( a| the| my)? ?(screenshot|screenshots|screens?|screen ?shot)"
        r"( of (my|the) (screens?|monitors?|pc|computer))?( please)?$")
def _screens(m, text):
    from . import approval
    return approval.run_tool("screens_photo", {})


@simple(r"^(take|send|grab|snap|show)( me)?( a| the)? ?(photo|picture|pic|selfie|shot)( of me)?"
        r"( (from|with|on|using) (the |my )?(webcam|camera))?( please)?$"
        r"|^(show|send)( me)? (the |my )?(webcam|camera)( view| photo| picture)?$|^(camera|webcam|selfie)$")
def _camera(m, text):
    from . import approval
    return approval.run_tool("camera_photo", {})


@simple(r"^((ok(ay)?|yes|yeah|yep|please|so|now|just|can you|could you|pls)[,.]? )*(re-?send|send)( me)?"
        r"( it| that| this| them| the (video|file|picture|photo|pic|clip|recording|screenshot))"
        r"( again| over| now| here| to (me|my phone))*( please| pls)?[.?!]*$")
def _resend(m, text):
    from . import outbox
    path = outbox.last_sent()
    if not path:
        return None   # nothing sent lately: the brain works out what "it" is
    return tools.run("send_file_to_phone", {"path": path})


@simple(r"^(check|read|show)( me)? (my |the )?(e-?mails?|inbox|mail)( please)?[.?!]*$"
        r"|^(do i have|have i got|did i get|are there|any) (any )?(new |unread |important )?(e-?mails?|mail)"
        r"( today)?[.?!]*$")
def _check_email(m, text):
    from . import mail
    try:
        return mail.spoken_summary()
    except mail.NotSetUp as e:
        return str(e)


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


@simple(r"^(sleep|go to sleep|restart|reboot|shut ?down|sign out|log out)( the| my)?( pc| computer)?$"
        r"|^turn off( the| my) (pc|computer)$")
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
    # "instagram in the zen browser" -> that site in that browser
    inb = re.match(r"(.+?)\s+(?:in|on|with|using)\s+(?:the\s+|my\s+)?(zen|opera gx|opera|google chrome|chrome|edge|"
                   r"firefox|brave)(?:\s+browser)?$", low)
    if inb:
        site = p[:len(inb.group(1))].strip()
        url = apps.site_url(site) or (("" if site.startswith("http") else "https://") + site if "." in site else None)
        return (apps.open_url(url, inb.group(2)), None) if url else (None, None)
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
    url = apps.site_url(p)
    if url:   # not an installed app, but a website people "open" like one
        return apps.open_url(url), None
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
            progress(f"Opening {piece.strip().strip('.')[:30]}…", "App")
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
            progress(f"Arranging on monitor {monitor}…", "Window")
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


@simple(r"^(call|ring|phone)\s+me(\s+(now|up))?$|^give me a call$")
def _call_me(m, text):
    return tools.run("call_user", {})


# ---------------------------------------------------------------- reminders at a time

_WHEN = r"(?:(?:at|for)\s+(?P<at>\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?)|in\s+(?P<at2>\d{1,2}[:.]\d{2}\s*(?:am|pm)?)|in\s+(?P<n>\d+|an?|one)\s*(?P<unit>min(?:ute)?s?|hours?|hrs?|h|m))"


@simple(r"^(?:please\s+)?(?:(?P<call>call|ring)\s+me|remind\s+me|notify\s+me|ping\s+me|alert\s+me|"
        r"(?:send|give)\s+me\s+(?:a\s+)?(?:notification|reminder|ping|alert))"
        r"(?:\s+on\s+my\s+phone)?\s+" + _WHEN +
        r"(?:\s+(?:to|that|about|saying|and\s+(?:say|tell\s+me))\s+(?P<msg>.+?))?(?:\s+please)?[.!]?$",
        compound_ok=True)
def _remind(m, text):
    at, n, unit = m.group("at") or m.group("at2"), m.group("n"), (m.group("unit") or "").lower()
    minutes = 0
    if n:
        count = 1 if n.lower() in ("a", "an", "one") else int(n)
        minutes = count * (60 if unit.startswith("h") else 1)
    msg = (m.group("msg") or "").strip() or ("Jarvis calling" if m.group("call") else "Reminder")
    return tools.run("remind_me", {"message": msg[:1].upper() + msg[1:], "at": at or "", "in_minutes": minutes,
                                   "call": bool(m.group("call"))})


# ---------------------------------------------------------------- notify me when …

@simple(r"\b(notify|tell|let)\s+me\s+(know\s+)?when\s+(the\s+|my\s+)?(downloads?)\s+(is|are|'?s|has|have)?\s*"
        r"(done|finished|complete|completed|ready|over)\b")
def _watch_download(m, text):
    return tools.run("notify_when", {"kind": "download", "label": "your download finished"})


@simple(r"\b(notify|tell|let)\s+me\s+(know\s+)?when\s+(.+?)\s+(closes|is closed|is done|finishes|is finished|exits|quits|ends)\b")
def _watch_app(m, text):
    app = re.sub(r"^(the\s+)?", "", m.group(3).strip()).strip()
    if app.lower() in ("download", "downloads", "the download", "it"):
        return tools.run("notify_when", {"kind": "download", "label": "your download finished"})
    return tools.run("notify_when", {"kind": "process_exits", "name": app, "label": f"{app} closed"})


@simple(r"^(((turn|switch) (on|off) )?away mode( on| off)?|i'?m (leaving|going out|heading out)( now)?|i'?m back)[.!]*$")
def _away(m, text):
    from . import away
    low = text.lower()
    if re.search(r"\b(off|back)\b", low):
        return away.turn_off()
    if re.search(r"\bon\b|leaving|going out|heading out", low):
        return away.turn_on()
    return away.toggle()


@simple(r"^(health check|self[- ]?check|check yourself|run a (health|self[- ]?)check|are you (ok|okay|alright|"
        r"working)|is everything (ok|okay|working)|what'?s (broken|wrong with you))[?.!]*$")
def _health(m, text):
    from . import health
    return health.check_now()


@simple(r"^(stop watching|cancel (the )?(watch|watches|notifications?|reminders?)|forget (the )?watch(es)?)$")
def _watch_cancel(m, text):
    return tools.run("cancel_watches", {})


# ---------------------------------------------------------------- make a folder / project

@simple(r"^(make|create|new|add)\s+(a\s+)?(new\s+)?(folder|project|directory|dir)\s+(?:called|named)\s+"
        r"[\"'\u201c]?([\w .\-]+?)[\"'\u201d]?(?:\s+(?:in|inside|under|into)\s+(?:the\s+)?(.+?)(?:\s+folder)?)?$")
def _make_folder(m, text):
    from . import files
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
    compound = bool(COMPOUND.search(cmd))
    try:
        for pattern, fn, compound_ok in SIMPLE:
            if compound and not compound_ok:
                continue
            m = pattern.search(cmd)   # match on original case so captured names keep their capitals
            if m:
                answer = fn(m, cmd)
                if answer is not None:
                    return answer
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
