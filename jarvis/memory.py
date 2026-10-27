"""What Jarvis remembers, shared by every brain.

- The conversation: each command, Jarvis's answer and the steps it took, whichever handled it (a shortcut, Hermes,
  Qwen3 or Claude), kept per chat in .brain/conversation.json. Every brain is shown the same recent conversation, so
  a follow-up like "close it" or "email that to Ana" works even when a different brain handled the message before,
  and nothing is forgotten when Jarvis restarts or a Claude session is swapped for a fresh one.
- Facts: things worth keeping for good (people, email addresses, preferences), saved with the remember tool in
  .brain/memory.json. Tools may run in a separate process (the Claude tool server), so facts are read from disk.
"""
import json
import logging
import re
import threading
import time

from .config import ROOT

log = logging.getLogger("jarvis.memory")

CONV_PATH = ROOT / ".brain" / "conversation.json"
FACTS_PATH = ROOT / ".brain" / "memory.json"
KEEP_PER_CHAT = 40        # exchanges saved per chat
CONTEXT_EXCHANGES = 10    # how many of them a brain is shown
CONTEXT_HOURS = 12        # older than this is a different conversation
DROP_CHAT_DAYS = 14
MAX_FACTS = 200

_lock = threading.Lock()


def _read(path, empty):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty


def _write(path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        log.exception("Couldn't save %s", path.name)


_conv: dict[str, list[dict]] = _read(CONV_PATH, {})


def _key(chat) -> str:
    return chat or "pc"   # voice and the PC shortcut share one conversation


# ---------------------------------------------------------------- the conversation

def record(chat, user: str, answer: str, did=None, started: float | None = None) -> None:
    """Save one exchange. `started` is when the command arrived (brains compare against it)."""
    entry = {"ts": started or time.time(), "user": str(user)[:2000], "jarvis": str(answer)[:3000],
             "did": [d.rstrip("…").strip() for d in (did or []) if d and d != "Working…"][:12]}
    now = time.time()
    with _lock:
        _conv.setdefault(_key(chat), []).append(entry)
        del _conv[_key(chat)][:-KEEP_PER_CHAT]
        for k in [k for k, v in _conv.items() if not v or now - v[-1]["ts"] > DROP_CHAT_DAYS * 86400]:
            del _conv[k]
        _write(CONV_PATH, _conv)


def recent(chat, since: float = 0.0, limit: int = CONTEXT_EXCHANGES) -> list[dict]:
    """The latest exchanges of this chat (oldest first) that started after `since`."""
    cutoff = max(since, time.time() - CONTEXT_HOURS * 3600)
    with _lock:
        return [dict(e) for e in _conv.get(_key(chat), []) if e["ts"] > cutoff][-limit:]


def from_phone(recent_msgs: list | None) -> list[dict]:
    """The phone's own copy of the chat ({who, text}), for a chat this PC has no record of."""
    out, user = [], None
    for m in recent_msgs or []:
        text = str(m.get("text", ""))[:600]
        if m.get("who") == "me":
            if user is not None:
                out.append({"user": user, "jarvis": "", "did": []})
            user = text
        elif user is not None:
            out.append({"user": user, "jarvis": text, "did": []})
            user = None
    return out


def _ago(ts) -> str:
    if not ts:
        return ""
    mins = int((time.time() - ts) / 60)
    return " (just now)" if mins < 2 else f" ({mins} min ago)" if mins < 90 else f" ({mins // 60} h ago)"


def transcript(entries: list[dict]) -> str:
    lines = []
    for e in entries:
        lines.append(f"User{_ago(e.get('ts'))}: {e['user'][:600]}")
        if e.get("did"):
            lines.append("  (Jarvis did: " + "; ".join(e["did"]) + ")")
        if e.get("jarvis"):
            lines.append(f"Jarvis: {e['jarvis'][:800]}")
    return "\n".join(lines)


def background(chat, since: float = 0.0, phone_recent: list | None = None, fresh: bool = True,
               with_facts: bool | None = None) -> str:
    """What a brain should know before the new command. `fresh`: the brain remembers nothing (new session), so
    give it the facts and the whole recent conversation; otherwise only what happened since it last answered
    (exchanges other brains handled). Empty when there's nothing to tell."""
    parts = []
    known = facts_text() if (fresh if with_facts is None else with_facts) else ""
    if known:
        parts.append("What you remember about the user (saved with the remember tool):\n" + known)
    entries = recent(chat, 0.0 if fresh else since)
    if not entries and fresh and phone_recent:
        entries = from_phone(phone_recent)
    if entries:
        head = ("The conversation so far (already handled; use it to understand follow-ups like \"it\", \"that\", "
                "\"again\", \"the other one\", \"send it to him\"):") if fresh else "Meanwhile, Jarvis also handled these:"
        parts.append(head + "\n" + transcript(entries))
    return "\n\n".join(parts)


def context(chat, since: float = 0.0, phone_recent: list | None = None, fresh: bool = True,
            with_facts: bool | None = None) -> str:
    """background() as a prefix for the command itself ("" when there's nothing to tell)."""
    body = background(chat, since, phone_recent, fresh, with_facts)
    return f"[{body}]\n\nThe user's new message:\n" if body else ""


# ---------------------------------------------------------------- facts

def facts() -> list[dict]:
    return _read(FACTS_PATH, [])


def facts_text() -> str:
    return "\n".join("- " + f["text"] for f in facts())


def facts_stamp() -> float:
    """Changes whenever a fact is added or removed (brains re-send the facts then)."""
    try:
        return FACTS_PATH.stat().st_mtime
    except OSError:
        return 0.0


def _words(text: str) -> set[str]:
    return set(re.findall(r"[\w@.+-]+", text.lower()))


def remember(text: str) -> str:
    text = " ".join(str(text).split())
    if not text:
        return "Nothing to remember."
    with _lock:
        items = facts()
        if any(f["text"].lower() == text.lower() for f in items):
            return f"I already know that: {text}"
        items.append({"text": text, "ts": int(time.time())})
        _write(FACTS_PATH, items[-MAX_FACTS:])
    return f"Remembered: {text}"


def forget(about: str) -> str:
    """Remove the facts that mention `about` (every word of it)."""
    want = _words(about)
    if not want:
        return "Tell me what to forget."
    with _lock:
        items = facts()
        gone = [f for f in items if want <= _words(f["text"])]
        if not gone:
            return f"I didn't have anything saved about {about}."
        _write(FACTS_PATH, [f for f in items if f not in gone])
    return "Forgot: " + "; ".join(f["text"] for f in gone)


def lookup_address(name: str) -> str | None:
    """An email address saved in the facts for this person ("Ana's email is ana@x.com"), if there is exactly one."""
    want = _words(name) - {"my", "the", "to"}
    found = set()
    for f in facts():
        if want and want <= _words(f["text"]):
            found.update(a.rstrip(".") for a in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", f["text"]))
    return found.pop() if len(found) == 1 else None
