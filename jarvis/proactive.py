"""Jarvis speaking first: messages he sends without a prompt in that moment.

- Scheduled tasks: "every morning at 8 check my email and tell me what matters", "at 18:00 tell me the weather".
  When the time comes the task runs through the brain like a command, and the answer arrives as a message.
- New important emails: a heads-up when one arrives (checked every few minutes over IMAP, no tokens).
- The morning briefing once a day at morning_time (its own switch, "briefing"; see briefing.py).

How much he does unasked is the "proactive" setting (off / normal / chatty). Scheduled tasks always run: the user
asked for those. During quiet hours, messages nobody asked for still land in the chat but don't make a notification.
"""
import datetime as dt
import email.utils
import json
import logging
import threading
import time

from . import health
from .config import ROOT, settings

log = logging.getLogger("jarvis.proactive")

LEVELS = ("off", "normal", "chatty")
REPEATS = ("", "daily", "weekdays", "weekly")
TASKS_FILE = ROOT / ".brain" / "schedule.json"
STATE_FILE = ROOT / ".brain" / "proactive.json"
TICK = 20               # seconds between checks
MAIL_EVERY = 5 * 60     # how often to look for new email
MAIL_RETRY = 30 * 60    # after a failure (no email set up, offline), wait longer
TASK_PREFIX = "(Scheduled task) "

_lock = threading.Lock()
_tasks: dict[str, dict] = {}
_state: dict = {}
_runner = None      # fn(task_text, chat): run it like a command; its answer reaches the phone as a message
_sender = None      # fn(text, chat, notify): send a message to the phone
_on_change = None   # the phone's list of reminders needs refreshing
_started = False


def level() -> str:
    value = settings.get("proactive", "normal")
    return value if value in LEVELS else "normal"


def quiet(now: dt.datetime | None = None) -> bool:
    """Inside the quiet hours (default 23:00 to 08:00)?"""
    start, end = (settings.get("quiet_hours") or [23, 8])[:2]
    hour = (now or dt.datetime.now()).hour
    return start <= hour < end if start < end else (hour >= start or hour < end)


def set_handlers(runner, sender, on_change=None) -> None:
    global _runner, _sender, _on_change
    _runner, _sender, _on_change = runner, sender, on_change


# ---------------------------------------------------------------- saved state

def _load() -> None:
    global _state
    try:
        for t in json.loads(TASKS_FILE.read_text(encoding="utf-8")):
            _tasks[t["id"]] = t
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        _state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _state = {}


def _save_tasks() -> None:
    with _lock:
        items = list(_tasks.values())
    try:
        TASKS_FILE.parent.mkdir(exist_ok=True)
        TASKS_FILE.write_text(json.dumps(items, indent=1), encoding="utf-8")
    except OSError:
        log.warning("Couldn't save scheduled tasks", exc_info=True)


def _save_state() -> None:
    try:
        STATE_FILE.parent.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(_state), encoding="utf-8")
    except OSError:
        log.warning("Couldn't save proactive state", exc_info=True)


def _changed() -> None:
    if _on_change:
        try:
            _on_change()
        except Exception:
            log.debug("change callback failed", exc_info=True)


# ---------------------------------------------------------------- scheduled tasks

def next_time(when: float, repeat: str, now: float | None = None) -> float:
    """The next run of a repeating task after `now`."""
    now = time.time() if now is None else now
    t = dt.datetime.fromtimestamp(when)
    step = dt.timedelta(days=7 if repeat == "weekly" else 1)
    while t.timestamp() <= now or (repeat == "weekdays" and t.weekday() >= 5):
        t += step
    return t.timestamp()


def schedule(task: str, at: str = "", in_minutes: float = 0, repeat: str = "", chat: str | None = None) -> str:
    from .watchers import parse_when
    task = (task or "").strip()
    if not task:
        raise ValueError("Say what I should do at that time.")
    repeat = repeat if repeat in REPEATS else ""
    when = parse_when(at, in_minutes)
    while repeat == "weekdays" and dt.datetime.fromtimestamp(when).weekday() >= 5:
        when += 86400
    tid = "task-" + str(int(time.time() * 1000))
    with _lock:
        _tasks[tid] = {"id": tid, "task": task, "when": when, "repeat": repeat, "chat": chat, "since": time.time()}
    _save_tasks()
    _ensure_loop()
    _changed()
    return f"Okay, {_describe(_tasks[tid])}."


def _describe(t: dict) -> str:
    d = dt.datetime.fromtimestamp(t["when"])
    hhmm = d.strftime("%H:%M")
    if t["repeat"] == "daily":
        when = f"every day at {hhmm}"
    elif t["repeat"] == "weekdays":
        when = f"every weekday at {hhmm}"
    elif t["repeat"] == "weekly":
        when = f"every {d.strftime('%A')} at {hhmm}"
    elif d.date() == dt.date.today():
        when = f"at {hhmm}"
    elif d.date() == dt.date.today() + dt.timedelta(days=1):
        when = f"tomorrow at {hhmm}"
    else:
        when = d.strftime("on %a %d %b at ") + hhmm
    return f"{when} I'll do this and message you: {t['task']}"


def cancel(tid: str) -> str | None:
    """Cancel one scheduled task (or every one with tid 'all'). None if there's no such task."""
    with _lock:
        if tid == "all":
            n = len(_tasks)
            _tasks.clear()
        else:
            n = 1 if _tasks.pop(tid, None) else 0
    if not n and tid != "all":
        return None
    _save_tasks()
    _changed()
    return f"Cancelled {n} scheduled task(s)." if n else "You have no scheduled tasks."


def items() -> list[dict]:
    """Scheduled tasks for the phone's reminder list (same shape as watchers.items())."""
    with _lock:
        ts = list(_tasks.values())
    return [{"id": t["id"], "kind": "at", "label": ("🔁 " if t["repeat"] else "") + t["task"], "call": False,
             "when": int(t["when"] * 1000), "task": True} for t in ts]


def listing() -> str:
    with _lock:
        ts = sorted(_tasks.values(), key=lambda t: t["when"])
    if not ts:
        return "No scheduled tasks."
    return "Scheduled: " + "; ".join(f"[{t['id']}] {_describe(t)}" for t in ts)


def _run_due() -> None:
    now = time.time()
    due = []
    with _lock:
        for t in list(_tasks.values()):
            if t["when"] <= now:
                due.append(dict(t))
                if t["repeat"]:
                    t["when"] = next_time(t["when"], t["repeat"], now)
                else:
                    _tasks.pop(t["id"], None)
    if not due:
        return
    _save_tasks()
    _changed()
    for t in due:
        if now - t["when"] > 6 * 3600:   # Jarvis was off for hours: a stale one-off isn't worth doing now
            log.info("Skipped a scheduled task that was due long ago: %s", t["task"])
            continue
        log.info("Scheduled task: %s", t["task"])
        if _runner:
            _runner(TASK_PREFIX + t["task"], t.get("chat"))


# ---------------------------------------------------------------- unprompted: email and the morning briefing

def _say(text: str) -> None:
    if _sender:
        _sender(text, None, not quiet())


def _check_mail() -> None:
    from . import mail
    mails = mail.check(unread_only=True, limit=10)
    seen = set(_state.get("seen_mail", []))
    first = not _state.get("mail_seeded")
    new = [m for m in mails if m["id"] not in seen]
    _state["seen_mail"] = (list(seen) + [m["id"] for m in new])[-300:]
    _state["mail_seeded"] = True
    _save_state()
    important = [m for m in new if m["important"] or level() == "chatty"]
    if first or not important:   # the first look only learns what's already there
        return

    def who(m):
        name, addr = email.utils.parseaddr(m["from"])
        return name or addr
    if len(important) == 1:
        m = important[0]
        _say(f"📬 New email from {who(m)}: {m['subject']}" + (f" — {m['snippet'][:120]}" if m["snippet"] else ""))
    else:
        _say(f"📬 {len(important)} new emails worth a look:\n"
             + "\n".join(f"- {who(m)}: {m['subject']}" for m in important[:5]))


def _due(now: dt.datetime, switch: str, at: str, default: str, done_key: str) -> bool:
    """Once a day at the time in `at`, while `switch` is on. Up to 3 hours late (the PC was off), never later."""
    if not settings.get(switch, True) or _state.get(done_key) == now.date().isoformat():
        return False
    try:
        hh, mm = (int(x) for x in str(settings.get(at, default)).split(":")[:2])
    except ValueError:
        hh, mm = (int(x) for x in default.split(":"))
    start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return start <= now < start + dt.timedelta(hours=3)   # PC switched on late: skip it rather than greet at 3pm


def _morning_due(now: dt.datetime) -> bool:
    return _due(now, "briefing", "morning_time", "08:30", "morning")


def _evening_due(now: dt.datetime) -> bool:
    return _due(now, "recap", "recap_time", "21:30", "evening")


def send_briefing() -> str:
    """Put the briefing together and send it (also read out on the PC when briefing_speak is on)."""
    from . import briefing
    text = briefing.compose()
    if _sender:
        _sender(text, None, True)   # you asked for this one, so it may buzz
    if settings.get("briefing_speak"):
        from . import speak
        speak.say(briefing.spoken(text))
    return text


def send_recap() -> str:
    """The evening recap (recap.py): what Jarvis did, your time in apps, tomorrow."""
    from . import recap
    text = recap.compose()
    if _sender:
        _sender(text, None, not quiet())
    return text


def _loop() -> None:
    next_mail, mail_fails = time.time() + 60, 0
    while True:
        time.sleep(TICK)
        try:
            _run_due()
        except Exception:
            log.exception("Running scheduled tasks failed")
        now = dt.datetime.now()
        try:
            if _morning_due(now):
                _state["morning"] = now.date().isoformat()
                _save_state()
                send_briefing()
        except Exception:
            log.exception("Morning briefing failed")
        try:
            if _evening_due(now):
                _state["evening"] = now.date().isoformat()
                _save_state()
                send_recap()
        except Exception:
            log.exception("Evening recap failed")
        if level() == "off":
            continue
        if time.time() >= next_mail and settings.get("email_address"):
            try:
                _check_mail()
                next_mail = time.time() + MAIL_EVERY
                mail_fails = 0
                health.fixed("email", "I can read your email again.")
            except Exception as e:
                log.info("Couldn't check for new email (%s); trying again later.", e)
                next_mail = time.time() + MAIL_RETRY
                mail_fails += 1
                # three in a row (not one network hiccup); not set up at all is your choice, not a fault
                if mail_fails >= 3 and "isn't set up" not in str(e):
                    health.problem("email", f"I can't check your email ({str(e)[:160]}). If you changed your "
                                   "Google password, make a new app password and run `run.bat --email` on the PC.")


def _ensure_loop() -> None:
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_loop, daemon=True, name="proactive").start()


def start() -> None:
    _load()
    _ensure_loop()
    if _tasks:
        log.info("%d scheduled task(s) waiting.", len(_tasks))
