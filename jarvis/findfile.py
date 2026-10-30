"""Find my file: "send me the PDF I downloaded yesterday", "find the photo from last week", "what did I download
today?".

Looks through the folders where your own files live (Downloads, Desktop, Documents, Pictures, Videos, Music and
OneDrive's copies of them) by kind, by when and by words in the name, with no brain and no tokens. One clear match
is sent straight to the phone; several are listed, and "the second one" sends that one. Anything it can't make
sense of (no kind of file, no "file" or "download" in the request) is left to the brain.
"""
import datetime as dt
import os
import re
import time
from pathlib import Path

from . import files

HOME = Path(os.path.expanduser("~"))
FOLDERS = ("Downloads", "Desktop", "Documents", "Pictures", "Videos", "Music")
SKIP = {"node_modules", ".git", "__pycache__", ".venv", "venv", "appdata", "$recycle.bin", "site-packages"}
DEPTH = 4
SEARCH_SECONDS = 4
PENDING_FOR = 10 * 60     # how long "the second one" still refers to the last list
SHOW = 6

DOCS = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".pages", ".md"}
SHEETS = {".xls", ".xlsx", ".csv", ".ods", ".numbers"}
SLIDES = {".ppt", ".pptx", ".key", ".odp"}
IMAGES = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VIDEOS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv"}
AUDIO = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
ARCHIVES = {".zip", ".rar", ".7z", ".tar", ".gz"}
KINDS = [   # (words, extensions, how to say it) — first match wins, so the specific ones come first
    (("pdf", "pdfs"), {".pdf"}, "PDF"),
    (("word doc", "word document", "word file", "docx"), {".doc", ".docx"}, "Word document"),
    (("excel", "spreadsheet", "spreadsheets", "xlsx", "csv"), SHEETS, "spreadsheet"),
    (("powerpoint", "presentation", "presentations", "slides", "pptx"), SLIDES, "presentation"),
    (("screenshot", "screenshots"), {".png", ".jpg", ".jpeg"}, "screenshot"),
    (("photo", "photos", "picture", "pictures", "pic", "pics", "image", "images", "jpg", "jpeg", "png"), IMAGES,
     "picture"),
    (("video", "videos", "clip", "clips", "movie", "recording", "recordings", "mp4"), VIDEOS, "video"),
    (("song", "songs", "audio", "mp3", "music file", "voice memo"), AUDIO, "audio file"),
    (("zip", "archive", "rar"), ARCHIVES, "archive"),
    (("installer", "setup file", "exe"), {".exe", ".msi"}, "installer"),
    (("document", "documents", "doc", "docs"), DOCS, "document"),
    (("file", "files", "thing", "something"), None, "file"),
]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
STOP = set("""a an the my me i you it this that these those of from in on at to for with and please can could
would send find get give show where wheres is was were did do what which one ones all some any just again
downloaded download downloads saved save made got received took take today yesterday morning afternoon evening
tonight week month last this past recent recently latest newest new ago days day hours hour earlier
monday tuesday wednesday thursday friday saturday sunday folder desktop documents pictures videos music
there here now phone""".split())

TRIGGER = re.compile(r"^(?:(?:can|could|would) you |please |hey |ok(?:ay)? )*"
                     r"(?P<verb>send|find|get|give|show|where'?s|where is|look for|search for|what did i)"
                     r"(?: me)?\s+(?P<rest>.+?)[?.!]*$", re.I)

_pending: dict = {}   # {"items": [Path], "ts": float}


# ---------------------------------------------------------------- understanding the request

def _window(low: str, now: dt.datetime) -> tuple[float, float, str] | None:
    """(from, to, label) of the time the request talks about, or None for any time."""
    day = dt.datetime.combine(now.date(), dt.time.min)
    if re.search(r"\b(today|this morning|this afternoon|this evening|tonight)\b", low):
        return day.timestamp(), now.timestamp() + 60, "today"
    if "yesterday" in low:
        return (day - dt.timedelta(days=1)).timestamp(), day.timestamp(), "yesterday"
    m = re.search(r"\b(\d+|a|one|two|three|four|five|six|seven)\s+days?\s+ago\b", low)
    if m:
        n = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}.get(m.group(1))
        n = n or int(m.group(1))
        start = day - dt.timedelta(days=n)
        return start.timestamp(), (start + dt.timedelta(days=1)).timestamp(), f"{n} day{'s' * (n > 1)} ago"
    for i, name in enumerate(WEEKDAYS):
        if re.search(rf"\b(on |last )?{name}\b", low):
            back = (now.weekday() - i) % 7 or 7
            start = day - dt.timedelta(days=back)
            return start.timestamp(), (start + dt.timedelta(days=1)).timestamp(), f"on {name.title()}"
    if re.search(r"\b(this|past|last) week\b", low):
        return (day - dt.timedelta(days=7)).timestamp(), now.timestamp() + 60, "this past week"
    if re.search(r"\b(this|past|last) month\b", low):
        return (day - dt.timedelta(days=31)).timestamp(), now.timestamp() + 60, "this past month"
    if re.search(r"\b(earlier|recently|just now|a minute ago|an hour ago)\b", low):
        return (now - dt.timedelta(hours=12)).timestamp(), now.timestamp() + 60, "recently"
    return None


def parse(text: str, now: dt.datetime | None = None) -> dict | None:
    """What to look for, or None when this isn't a find-a-file request."""
    m = TRIGGER.match(text.strip())
    if not m:
        return None
    verb, rest = m.group("verb").lower(), m.group("rest")
    low = " " + rest.lower() + " "
    kind = None
    for words, exts, label in KINDS:
        if any(re.search(rf"\b{re.escape(w)}\b", low) for w in words):
            kind = (exts, label)
            break
    downloaded = bool(re.search(r"\bdownload(ed|s)?\b", low))
    if verb == "what did i":
        if not downloaded and not kind:
            return None
        kind = kind or (None, "file")
    if not kind and not downloaded:
        return None   # "send me a joke", "get me the weather": not a file
    if not kind:
        kind = (None, "file")
    folder = "Downloads" if downloaded else next(
        (f for f in FOLDERS if re.search(rf"\b{f.lower()}\b", low)), None)
    words = [w for w in re.findall(r"[a-z0-9]+", rest.lower())
             if w not in STOP and len(w) > 1 and not any(w in ws for ws, _, _ in KINDS)]
    return {"exts": kind[0], "label": kind[1], "window": _window(low, now or dt.datetime.now()), "folder": folder,
            "words": words, "newest": bool(re.search(r"\b(latest|newest|most recent|just)\b|\blast\b(?! (week|month|"
                                                     r"year|night|monday|tuesday|wednesday|thursday|friday|saturday|"
                                                     r"sunday))", low)),
            "list": verb in ("what did i", "find", "where's", "wheres", "where is", "look for", "search for", "show")}


# ---------------------------------------------------------------- looking

def roots(folder: str | None = None) -> list[Path]:
    names = [folder] if folder else list(FOLDERS)
    out = [HOME / n for n in names]
    for od in sorted(HOME.glob("OneDrive*")):
        out += [od / n for n in names]
    seen, uniq = set(), []
    for p in out:
        key = str(p).lower()
        if key not in seen and p.is_dir():
            seen.add(key)
            uniq.append(p)
    return uniq


def _when(st) -> float:
    """When the file arrived: copying or downloading keeps an old modified date sometimes, creation is when it
    landed here."""
    return max(st.st_mtime, getattr(st, "st_birthtime", st.st_ctime))


def search(spec: dict) -> list[tuple[float, Path]]:
    """Matching files, newest first."""
    exts, window, words = spec["exts"], spec["window"], spec["words"]
    deadline = time.time() + SEARCH_SECONDS
    hits = []
    stack = [(r, 0) for r in roots(spec.get("folder"))]
    while stack and time.time() < deadline:
        folder, depth = stack.pop()
        try:
            with os.scandir(folder) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth < DEPTH and not e.name.startswith((".", "$")) and e.name.lower() not in SKIP:
                                stack.append((Path(e.path), depth + 1))
                            continue
                        name = e.name.lower()
                        if name.startswith(("~$", ".")) or name.endswith((".crdownload", ".part", ".tmp", ".ini",
                                                                          ".lnk", ".url")):
                            continue
                        if exts is not None and os.path.splitext(name)[1] not in exts:
                            continue
                        if words and not all(w in name for w in words):
                            continue
                        st = e.stat(follow_symlinks=False)
                        when = _when(st)
                        if window and not (window[0] <= when < window[1]):
                            continue
                        hits.append((when, Path(e.path)))
                    except OSError:
                        continue
        except OSError:
            continue
    hits.sort(reverse=True)
    return hits


# ---------------------------------------------------------------- answering

def _ago(ts: float, now: float | None = None) -> str:
    d, today = dt.datetime.fromtimestamp(ts), dt.datetime.fromtimestamp(now or time.time()).date()
    if d.date() == today:
        return f"today {d:%H:%M}"
    if d.date() == today - dt.timedelta(days=1):
        return f"yesterday {d:%H:%M}"
    return d.strftime("%a %d %b")


def _where(p: Path) -> str:
    try:
        return str(p.parent.relative_to(HOME))
    except ValueError:
        return str(p.parent)


def _plural(label: str) -> str:
    return label + ("es" if label.endswith("sh") else "s")


def _send(p: Path) -> str:
    from . import quips, tools
    done = tools.run("send_file_to_phone", {"path": str(p)})
    if done.startswith("Sent"):
        extra = quips.flavour("sent_file", "")
        return done + (" " + extra if extra else "")
    return done


def handle(text: str) -> str | None:
    spec = parse(text)
    if not spec:
        return None
    if not spec["list"] and not (spec["window"] or spec["words"] or spec["newest"]):
        return None   # "send me the video": which one comes from the conversation, so the brain answers
    hits = search(spec)
    when = f" from {spec['window'][2]}" if spec["window"] and spec["window"][2] != "today" else (
        " from today" if spec["window"] else "")
    what = spec["label"] + (f" with \"{' '.join(spec['words'])}\" in the name" if spec["words"] else "")
    where = "your Downloads" if spec["folder"] == "Downloads" else (
        f"your {spec['folder']}" if spec["folder"] else "Downloads, Desktop, Documents, Pictures, Videos or Music")
    if not hits:
        if spec["words"] and not spec["window"]:
            return None   # a name it can't find in your own folders: the brain can search every drive
        return f"I couldn't find a {what}{when} in {where}."
    if not spec["list"] and (len(hits) == 1 or spec["newest"]):
        return _send(hits[0][1])
    _pending.update(items=[p for _, p in hits[:SHOW]], ts=time.time())
    rows = [f"{i}. {p.name} ({_where(p)}, {_ago(t)}, {files.fmt_size(p.stat().st_size)})"
            for i, (t, p) in enumerate(hits[:SHOW], 1)]
    many = len(hits)
    head = (f"I found {many} {_plural(spec['label']) if many > 1 else spec['label']}{when}"
            + (f", here are the newest {SHOW}" if many > SHOW else "") + ":")
    ask = "Which one should I send? Say \"the first one\", \"number 2\" and so on." if many > 1 else \
        "Say \"send it\" and it's on your phone."
    return head + "\n" + "\n".join(rows) + "\n" + ask


ORDINAL = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
           "fifth": 5, "5th": 5, "sixth": 6, "6th": 6, "last": -1, "it": 1, "that": 1, "that one": 1}


def pick(text: str) -> str | None:
    """"the second one" / "number 3" / "send it" right after a list: send that file. None when there's no list."""
    items = _pending.get("items")
    if not items or time.time() - _pending.get("ts", 0) > PENDING_FOR:
        return None
    m = re.match(r"^(?:(?:ok(?:ay)?|yes|yeah|please)[,.]? )*(?:send(?: me)?|give me|get me)?\s*(?:the |number |no\.? ?|#)?"
                 r"(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th|sixth|6th|last|it|that(?: one)?|\d)"
                 r"(?: one)?(?: please)?[.!]*$", text.strip(), re.I)
    if not m:
        return None
    word = m.group(1).lower()
    if word.startswith(("it", "that")) and len(items) > 1:
        return None   # "send it" after a list of several: not sure which, so it isn't a pick
    n = int(word) if word.isdigit() else ORDINAL[word]
    if n > len(items) or n == 0:
        return f"There were only {len(items)} on that list."
    _pending.clear()
    return _send(items[n - 1] if n > 0 else items[-1])
