"""The same chats on every phone and computer connected to this PC.

Each device keeps its own copy of its chats (the app's storage) and sends what changed here ("chats_push"); this PC
keeps the merged set in .brain/chats.json and passes the changes on to every device ("chats"). A device that connects
says which chats it has and how new each is ("chats_hello"): it gets what it's missing, and is asked for what this PC
is missing.

Merging never loses a message: a chat's messages are the union of every copy's (by time and who wrote it). The title
and group come from whichever copy was changed last ("rev", the device's clock when it changed). A deleted chat is
remembered for 60 days, so a device that was offline doesn't bring it back. Pictures and files stay on the device
that got them (they're too big to copy around); their chats still sync.
"""
import json
import logging
import threading
import time

from .config import ROOT

log = logging.getLogger("jarvis.chatsync")
PATH = ROOT / ".brain" / "chats.json"
MAX_MESSAGES = 200          # per chat, like the app
KEEP_DELETED = 60 * 86400   # s: how long a deleted chat is remembered
SYNCED = ("me", "jarvis", "steps")   # what's copied; pictures ("shots") and files stay where they are
FIELDS = ("id", "title", "group", "created", "updated", "rev")

_lock = threading.Lock()
_db = {"chats": {}, "deleted": {}, "groups": [], "groups_at": 0}
try:
    _db.update(json.loads(PATH.read_text(encoding="utf-8")))
except Exception:
    pass


def _save() -> None:
    try:
        PATH.parent.mkdir(exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_db, ensure_ascii=False), encoding="utf-8")
        tmp.replace(PATH)
    except Exception:
        log.exception("Couldn't save the chats")


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _key(m: dict) -> str:
    return f"{_num(m.get('ts')):.0f}|{m.get('who')}"


def _clean(chat: dict) -> dict | None:
    """A chat as it's kept and sent: its fields and the messages that sync, nothing else."""
    if not isinstance(chat, dict) or not isinstance(chat.get("id"), str) or not chat["id"]:
        return None
    out = {k: chat.get(k) for k in FIELDS}
    out["id"] = chat["id"][:64]
    out["title"] = str(out.get("title") or "")[:200]
    out["rev"] = _num(out.get("rev") or out.get("updated"))
    msgs = [m for m in chat.get("messages") or [] if isinstance(m, dict) and m.get("who") in SYNCED and m.get("ts")]
    out["messages"] = sorted(msgs, key=lambda m: _num(m.get("ts")))[-MAX_MESSAGES:]
    return out


def merge(old: dict | None, new: dict) -> dict:
    """Two copies of one chat as one: every message from both, and the newer copy's title and group."""
    if not old:
        return new
    newer = new["rev"] >= old["rev"]
    msgs = {_key(m): m for m in old["messages"]}
    for m in new["messages"]:
        if newer or _key(m) not in msgs:
            msgs[_key(m)] = m
    out = dict(new if newer else old)
    out["rev"] = max(old["rev"], new["rev"])
    out["created"] = min(_num(old.get("created")) or _num(new.get("created")), _num(new.get("created")) or _num(old.get("created")))
    out["messages"] = sorted(msgs.values(), key=lambda m: _num(m.get("ts")))[-MAX_MESSAGES:]
    out["updated"] = max(_num(old.get("updated")), _num(new.get("updated")),
                         _num(out["messages"][-1].get("ts")) if out["messages"] else 0)
    return out


def _forget_old_deletes() -> None:
    cutoff = time.time() * 1000 - KEEP_DELETED * 1000
    for cid in [c for c, at in _db["deleted"].items() if at < cutoff]:
        del _db["deleted"][cid]


def push(chats: list, deleted: dict | None = None, groups: list | None = None, groups_at=0) -> dict:
    """A device's changes. Returns what changed here, to pass on to every device ({} = nothing new)."""
    changed, gone = [], {}
    with _lock:
        for cid, at in (deleted or {}).items():
            if isinstance(cid, str) and _num(at) > _db["deleted"].get(cid, 0):
                chat = _db["chats"].get(cid)
                if chat and chat["rev"] > _num(at):
                    continue   # changed on another device after it was deleted here: it stays
                _db["chats"].pop(cid, None)
                _db["deleted"][cid] = _num(at)
                gone[cid] = _num(at)
        for raw in chats or []:
            chat = _clean(raw)
            if not chat or _db["deleted"].get(chat["id"], 0) >= chat["rev"]:
                continue   # gone, and not changed since
            _db["deleted"].pop(chat["id"], None)
            old = _db["chats"].get(chat["id"])
            new = merge(old, chat)
            if new != old:
                _db["chats"][chat["id"]] = new
                changed.append(new)
        out = {}
        if isinstance(groups, list) and _num(groups_at) > _db["groups_at"]:
            _db["groups"], _db["groups_at"] = groups[:100], _num(groups_at)
            out.update(groups=_db["groups"], groups_at=_db["groups_at"])
        if changed or gone or out:
            _forget_old_deletes()
            _save()
        if changed:
            out["chats"] = changed
        if gone:
            out["deleted"] = gone
        return out


def hello(have: dict | None, groups_at=0) -> dict:
    """A device connected with these chats ({id: rev}). What it's missing, and which of its chats this PC wants."""
    have = {k: _num(v) for k, v in (have or {}).items() if isinstance(k, str)}
    with _lock:
        send = [c for cid, c in _db["chats"].items() if c["rev"] > have.get(cid, -1)]
        want = [cid for cid, rev in have.items()
                if rev > _db["chats"].get(cid, {}).get("rev", -1) and rev > _db["deleted"].get(cid, 0)]
        deleted = {cid: at for cid, at in _db["deleted"].items() if cid in have}
        out = {"chats": send, "want": want, "deleted": deleted}
        if _db["groups_at"] > _num(groups_at):
            out.update(groups=_db["groups"], groups_at=_db["groups_at"])
        return out


def count() -> int:
    with _lock:
        return len(_db["chats"])
