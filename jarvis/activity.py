"""A saved record of everything Jarvis does: each command (from the phone or the PC mic), the steps it took and how
it ended. The phone shows it as a list of small pills, and running ones as a live "working" indicator, so a task
never just vanishes. Kept in .brain/activity.json (the newest MAX_ENTRIES), so it survives restarts.
"""
import json
import logging
import threading
import time
import uuid

from .config import ROOT

log = logging.getLogger("jarvis.activity")

PATH = ROOT / ".brain" / "activity.json"
MAX_ENTRIES = 200
MAX_STEPS = 40

_lock = threading.RLock()   # re-entrant: the change callback may read the summary
_entries: list[dict] = []
_on_change = None   # set by the app: called (from any thread) whenever something changes


def _load() -> None:
    global _entries
    try:
        _entries = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _entries = []
    for e in _entries:   # anything still "running" when Jarvis stopped didn't finish
        if e.get("status") in ("running", "queued"):
            e["status"], e["reply"] = "error", e.get("reply") or "Jarvis stopped before this finished."


_load()


def set_on_change(fn) -> None:
    global _on_change
    _on_change = fn


def _save_and_notify() -> None:
    try:
        PATH.parent.mkdir(exist_ok=True)
        PATH.write_text(json.dumps(_entries[-MAX_ENTRIES:]), encoding="utf-8")
    except OSError:
        log.debug("couldn't save activity", exc_info=True)
    if _on_change:
        try:
            _on_change()
        except Exception:
            log.debug("activity change callback failed", exc_info=True)


def _find(tid: str) -> dict | None:
    return next((e for e in reversed(_entries) if e["id"] == tid), None)


def start(text: str, via: str = "pc", kind: str = "command", chat: str | None = None, msg_id: str | None = None,
          status: str = "running") -> str:
    """Record a new task and return its id."""
    tid = uuid.uuid4().hex[:12]
    with _lock:
        _entries.append({"id": tid, "kind": kind, "text": text, "via": via, "chat": chat, "msg_id": msg_id,
                         "status": status, "steps": [], "reply": "", "started": time.time(), "ended": None})
        del _entries[:-MAX_ENTRIES]
        _save_and_notify()
    return tid


def set_status(tid: str, status: str) -> None:
    with _lock:
        e = _find(tid)
        if e and e["status"] != status:
            e["status"] = status
            _save_and_notify()


def step(tid: str, kind: str, text: str) -> None:
    with _lock:
        e = _find(tid)
        if not e or (e["steps"] and e["steps"][-1]["t"] == text):
            return
        e["status"] = "running"
        e["steps"].append({"k": kind or "Step", "t": text})
        del e["steps"][:-MAX_STEPS]
        _save_and_notify()


def finish(tid: str, reply: str, ok: bool = True) -> None:
    with _lock:
        e = _find(tid)
        if not e:
            return
        e["status"], e["reply"], e["ended"] = ("done" if ok else "error"), reply or "", time.time()
        _save_and_notify()


def record(text: str, kind: str = "reminder", via: str = "pc", reply: str = "") -> None:
    """A finished one-off event, e.g. a reminder that just went off."""
    tid = start(text, via=via, kind=kind)
    finish(tid, reply)


def by_msg(msg_id: str) -> dict | None:
    with _lock:
        e = next((e for e in reversed(_entries) if msg_id and e.get("msg_id") == msg_id), None)
        return json.loads(json.dumps(e)) if e else None


def steps_of(tid: str) -> list[dict]:
    with _lock:
        e = _find(tid)
        return list(e["steps"]) if e else []


def summary(limit: int = 25) -> list[dict]:
    """Compact copies of the newest entries for the phone (newest first)."""
    with _lock:
        out = []
        for e in reversed(_entries[-limit:]):
            out.append({"id": e["id"], "kind": e["kind"], "text": e["text"][:160], "via": e["via"],
                        "chat": e.get("chat"), "msg_id": e.get("msg_id"), "status": e["status"],
                        "steps": [{"k": s["k"], "t": s["t"][:120]} for s in e["steps"][-8:]],
                        "nsteps": len(e["steps"]), "reply": (e["reply"] or "")[:240],
                        "started": int(e["started"] * 1000), "ended": int(e["ended"] * 1000) if e["ended"] else None})
        return out


def clear() -> None:
    with _lock:
        _entries.clear()
        _save_and_notify()
