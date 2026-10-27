"""Files on their way to the phone. The phone app is only connected while it's open (iOS freezes it in the
background), so a picture or video Jarvis sends is kept here until the phone says it got it, and is sent again every
time the app connects. Only the file's path is stored (in .brain/outbox.json), never a copy of the file.
"""
import json
import logging
import mimetypes
import secrets
import threading
import time
from pathlib import Path

from .config import ROOT

log = logging.getLogger("jarvis.outbox")

PATH = ROOT / ".brain" / "outbox.json"
KEEP_SECONDS = 3 * 86400   # a file nobody picked up in 3 days is dropped
RESEND_AFTER = 30          # don't send the same file again sooner than this (the phone may still be receiving it)

_lock = threading.Lock()
_items: list[dict] = []
_sent_at: dict[str, float] = {}   # fid -> when it last went out (in memory only)
_recent: list[tuple[float, str]] = []   # (when, path) of files Jarvis sent lately, for "send it again"


def _load() -> None:
    global _items
    try:
        _items = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _items = []


def _save() -> None:
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(_items, indent=1), encoding="utf-8")
    except OSError:
        log.exception("Couldn't save the outbox")


_load()


def add(path: str, name: str, size: int, mime: str, chat: str | None) -> dict:
    """Keep a file until the phone confirms it. Returns its record (with the id the phone acknowledges)."""
    item = {"fid": secrets.token_hex(6), "path": path, "name": name, "size": size,
            "mime": mime or mimetypes.guess_type(name)[0] or "", "chat": chat, "ts": int(time.time() * 1000)}
    with _lock:
        _items.append(item)
        _save()
        _recent.append((time.time(), path))
        del _recent[:-5]
    return item


def last_sent(max_age: float = 3 * 3600) -> str | None:
    """The file Jarvis sent to the phone most recently (if it was lately and still exists), for "send it again"."""
    for when, path in reversed(_recent):
        if time.time() - when < max_age and Path(path).is_file():
            return path
    return None


def mark_sent(fid: str) -> None:
    _sent_at[fid] = time.time()


def done(fid: str) -> None:
    """The phone has the file: stop sending it."""
    with _lock:
        before = len(_items)
        _items[:] = [i for i in _items if i["fid"] != fid]
        if len(_items) != before:
            _save()
    _sent_at.pop(fid, None)


def due(force: bool = False) -> list[dict]:
    """Files the phone hasn't confirmed yet that are worth sending again now (oldest first). `force`: all of them,
    even ones that just went out (the phone has just connected, so those may have gone to a dead connection)."""
    now = time.time()
    with _lock:
        fresh = [i for i in _items if now - i["ts"] / 1000 < KEEP_SECONDS and Path(i["path"]).is_file()]
        if len(fresh) != len(_items):
            _items[:] = fresh
            _save()
        return [dict(i) for i in fresh if force or now - _sent_at.get(i["fid"], 0) > RESEND_AFTER]


def payload(item: dict) -> dict | None:
    """The message that carries the file to the phone (read fresh from disk), or None if it's gone."""
    import base64
    try:
        data = base64.b64encode(Path(item["path"]).read_bytes()).decode()
    except OSError:
        done(item["fid"])
        return None
    return {"kind": "file", "id": "file", "fid": item["fid"], "name": item["name"], "size": item["size"],
            "mime": item["mime"], "chat": item.get("chat"), "ts": item["ts"], "data": data}
