"""What Jarvis remembers, shared by every brain.

- The conversation: each command, Jarvis's answer and the steps it took, whichever handled it (a shortcut, Hermes,
  Qwen3 or Claude), kept per chat in .brain/conversation.json. Every brain is shown the same recent conversation, so
  a follow-up like "close it" or "email that to Ana" works even when a different brain handled the message before,
  and nothing is forgotten when Jarvis restarts or a Claude session is swapped for a fresh one.
- Facts: things worth keeping for good (people, email addresses, preferences), saved with the remember tool in
  .brain/memory.json. Tools may run in a separate process (the Claude tool server), so facts are read from disk.
- About me: a note the user writes themselves on the phone (Settings > Memory), in .brain/about_me.txt. Every brain
  gets it along with the facts. The phone can also add and delete facts, and set how far back the conversation
  reaches ("memory_span") and how much humour Jarvis uses ("humour").
"""
import json
import logging
import re
import threading
import time

from .config import ROOT, settings

log = logging.getLogger("jarvis.memory")

CONV_PATH = ROOT / ".brain" / "conversation.json"
FACTS_PATH = ROOT / ".brain" / "memory.json"
ABOUT_PATH = ROOT / ".brain" / "about_me.txt"
KEEP_PER_CHAT = 40        # exchanges saved per chat
DROP_CHAT_DAYS = 14
MAX_FACTS = 200
MAX_ABOUT = 4000

# How far back a brain is shown the conversation: (hours, exchanges). Older than that is a different conversation.
SPANS = {"short": (2, 6), "normal": (12, 10), "long": (72, 20), "max": (DROP_CHAT_DAYS * 24, 30)}

# How Jarvis talks, set on the phone. Goes into every brain's system prompt.
HUMOUR = {
    "off": "Be plain and businesslike: no jokes or banter.",
    "light": "Have a touch of dry, understated wit, like a calm British butler: now and then a light, friendly quip "
             "in a reply, never at the cost of clarity or speed, never when the user is stressed, something failed, "
             "or the topic is serious. Most replies need no joke at all.",
    "lots": "Be playful and witty: a quick joke, a bit of teasing or a clever aside in most replies, but still keep "
            "replies short, get the job done first, and drop the jokes when something is serious or went wrong.",
}


def span() -> tuple[int, int]:
    return SPANS.get(settings.get("memory_span", "normal"), SPANS["normal"])


def personality() -> str:
    return HUMOUR.get(settings.get("humour", "light"), HUMOUR["light"])

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


def recent(chat, since: float = 0.0, limit: int | None = None) -> list[dict]:
    """The latest exchanges of this chat (oldest first) that started after `since`, within the memory span."""
    hours, most = span()
    limit = most if limit is None else limit
    cutoff = max(since, time.time() - hours * 3600)
    with _lock:
        return [dict(e) for e in _conv.get(_key(chat), []) if e["ts"] > cutoff][-limit:]


def clear_conversations() -> None:
    """Forget every chat's conversation (the facts and About me stay)."""
    with _lock:
        _conv.clear()
        _write(CONV_PATH, _conv)


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
    if fresh if with_facts is None else with_facts:
        mine = about()
        if mine:
            parts.append("About the user, in their own words (always keep this in mind):\n" + mine)
        known = facts_text()
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


def _mtime(path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def facts_stamp() -> float:
    """Changes whenever a fact or About me changes (brains re-send them then)."""
    return _mtime(FACTS_PATH) + _mtime(ABOUT_PATH)


def about() -> str:
    try:
        return ABOUT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def set_about(text: str) -> None:
    text = str(text).strip()[:MAX_ABOUT]
    try:
        ABOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        ABOUT_PATH.write_text(text, encoding="utf-8")
    except OSError:
        log.exception("Couldn't save About me")


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


def forget_exact(text: str) -> bool:
    """Remove one fact by its exact text (the phone's delete button)."""
    with _lock:
        items = facts()
        kept = [f for f in items if f["text"] != text]
        if len(kept) == len(items):
            return False
        _write(FACTS_PATH, kept)
    return True


def lookup_address(name: str) -> str | None:
    """An email address saved in the facts for this person ("Ana's email is ana@x.com"), if there is exactly one."""
    want = _words(name) - {"my", "the", "to"}
    found = set()
    for f in facts():
        if want and want <= _words(f["text"]):
            found.update(a.rstrip(".") for a in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", f["text"]))
    return found.pop() if len(found) == 1 else None
