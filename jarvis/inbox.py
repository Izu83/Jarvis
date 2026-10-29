"""Messages from Jarvis on their way to the phone: extra messages during a task ("Found it, uploading now…") and
ones he sends on his own (a reminder, a scheduled task's result, a new important email, a good-morning note).

The phone app is only connected while it's open, so each message is kept here (in .brain/inbox.json) until the
phone says it has it, and is sent again every time the app connects. Same idea as outbox.py, but for text.
"""
import json
import logging
import secrets
import threading
import time

from .config import ROOT

log = logging.getLogger("jarvis.inbox")

PATH = ROOT / ".brain" / "inbox.json"
KEEP_SECONDS = 3 * 86400   # a message nobody picked up in 3 days is dropped
RESEND_AFTER = 20          # don't send the same message again sooner than this

_lock = threading.Lock()
_items: list[dict] = []
_sent_at: dict[str, float] = {}   # mid -> when it last went out (in memory only)


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
        log.exception("Couldn't save the inbox")


_load()


def add(text: str, chat: str | None = None, reply_to: str = "") -> dict:
    """Keep a message until the phone confirms it. `reply_to`: the command it belongs to (shown above its answer)."""
    item = {"mid": secrets.token_hex(6), "text": text.strip(), "chat": chat, "reply_to": reply_to,
            "ts": int(time.time() * 1000)}
    with _lock:
        _items.append(item)
        del _items[:-100]
        _save()
    return item


def mark_sent(mid: str) -> None:
    _sent_at[mid] = time.time()


def done(mid: str) -> None:
    """The phone has the message: stop sending it."""
    with _lock:
        before = len(_items)
        _items[:] = [i for i in _items if i["mid"] != mid]
        if len(_items) != before:
            _save()
    _sent_at.pop(mid, None)


def due(force: bool = False) -> list[dict]:
    """Messages the phone hasn't confirmed yet that are worth sending again now (oldest first)."""
    now = time.time()
    with _lock:
        fresh = [i for i in _items if now - i["ts"] / 1000 < KEEP_SECONDS]
        if len(fresh) != len(_items):
            _items[:] = fresh
            _save()
        return [dict(i) for i in fresh if force or now - _sent_at.get(i["mid"], 0) > RESEND_AFTER]


def payload(item: dict) -> dict:
    return {"kind": "msg", "id": "msg", "mid": item["mid"], "text": item["text"], "chat": item.get("chat"),
            "reply_to": item.get("reply_to", ""), "ts": item["ts"]}
