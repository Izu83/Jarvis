"""Background watches: "notify me when the download is done / when this app closes / when the export finishes".

Each watch polls a cheap condition every few seconds and, when it comes true, fires a notification (a phone push,
or a banner if the app is open). Watches auto-expire after a few hours so they never pile up.
"""
import datetime as dt
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import psutil

from .config import ROOT

log = logging.getLogger("jarvis.watchers")

POLL = 8            # seconds between checks
MAX_HOURS = 8       # give up after this long
PARTIAL = (".crdownload", ".part", ".tmp", ".opdownload", ".download")

_watches: dict[str, dict] = {}
_lock = threading.Lock()
_notifier = None    # set by the app: fn(title, body, call=False)
_on_change = None   # set by the app: called when watches are added, fire or are cancelled (the phone's clock chip)
_started = False
REMINDERS_FILE = ROOT / ".brain" / "reminders.json"   # timed reminders survive a Jarvis restart


def set_notifier(fn) -> None:
    global _notifier
    _notifier = fn


def set_on_change(fn) -> None:
    global _on_change
    _on_change = fn


def _changed() -> None:
    if _on_change:
        try:
            _on_change()
        except Exception:
            log.debug("watch change callback failed", exc_info=True)


def _downloads() -> Path:
    return Path(os.path.expanduser("~/Downloads"))


def _partials(folder: Path) -> list[Path]:
    try:
        return [p for p in folder.iterdir() if p.suffix.lower() in PARTIAL]
    except OSError:
        return []


def _procs(name: str) -> list[psutil.Process]:
    n = name.lower().removesuffix(".exe")
    out = []
    for p in psutil.process_iter(["name"]):
        try:
            if n in (p.info["name"] or "").lower():
                out.append(p)
        except psutil.Error:
            continue
    return out


def _check(w: dict) -> bool:
    kind = w["kind"]
    if kind == "at":
        return time.time() >= w["when"]
    if kind == "download":
        # Fire only when a download that was in progress (a .crdownload/.part/… file) has finished.
        if _partials(_downloads()):
            w["saw_partial"] = True
            return False
        return bool(w.get("saw_partial"))
    if kind == "process_exits":
        running = bool(_procs(w["name"]))
        if running:
            w["saw"] = True
            return False
        return bool(w.get("saw"))   # it was running and has now exited
    if kind == "process_starts":
        return bool(_procs(w["name"]))
    if kind == "file":
        return Path(os.path.expandvars(os.path.expanduser(w["path"]))).exists()
    if kind == "app_idle":
        procs = _procs(w["name"])
        if not procs:
            return bool(w.get("busy"))   # the app finished and closed
        cpu = 0.0
        for p in procs:
            try:
                cpu += p.cpu_percent(None)
            except psutil.Error:
                pass
        cpu /= max(1, psutil.cpu_count())
        thr = w.get("threshold", 8)
        if cpu > thr:
            w["busy"], w["idle_since"] = True, 0
            return False
        if w.get("busy"):
            w["idle_since"] = w.get("idle_since") or time.time()
            return time.time() - w["idle_since"] > 20   # quiet for 20s after being busy
        return False
    return False


def _loop() -> None:
    while True:
        time.sleep(POLL)
        with _lock:
            due = []
            for wid, w in list(_watches.items()):
                try:
                    if time.time() > w["deadline"]:
                        _watches.pop(wid, None)
                        continue
                    if _check(w):
                        due.append(_watches.pop(wid))
                except Exception:
                    log.debug("watch check failed", exc_info=True)
        if any(w["kind"] == "at" for w in due):
            _save_reminders()
        if due:
            _changed()
        for w in due:
            log.info("Watch done: %s", w["label"])
            from . import activity
            activity.record(w["label"], kind="reminder" if w["kind"] == "at" else "watch",
                            reply="Called your phone." if w.get("call") else "Sent you a notification.")
            if not _notifier:
                continue
            if w["kind"] == "at":
                _notifier("⏰ Reminder", w["label"], w.get("call", False))
            else:
                _notifier("✅ " + w["label"], w.get("done_text", "It's done."))


def _ensure_loop() -> None:
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_loop, daemon=True, name="watchers").start()


def _save_reminders() -> None:
    with _lock:
        items = [w for w in _watches.values() if w["kind"] == "at"]
    try:
        REMINDERS_FILE.parent.mkdir(exist_ok=True)
        REMINDERS_FILE.write_text(json.dumps(items))
    except OSError:
        log.warning("Couldn't save reminders", exc_info=True)


def load_reminders() -> None:
    """Re-arm reminders saved before a restart (ones that came due while Jarvis was off fire right away)."""
    try:
        items = json.loads(REMINDERS_FILE.read_text())
    except (OSError, ValueError):
        return
    with _lock:
        for w in items:
            w["deadline"] = max(w["deadline"], time.time() + 120)
            _watches[w["id"]] = w
    if items:
        _ensure_loop()
        log.info("Re-armed %d reminder(s).", len(items))


def parse_when(at: str = "", in_minutes: float = 0) -> float:
    """'10:53', '10:53pm', '22:05', '2026-10-20 09:00', or in_minutes -> epoch seconds (the next such time)."""
    now = dt.datetime.now()
    if in_minutes:
        return time.time() + float(in_minutes) * 60
    text = (at or "").strip().lower().replace(".", ":")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt).timestamp()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if not m:
        raise ValueError(f"I couldn't understand the time '{at}'. Use HH:MM.")
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if when <= now:
        when += dt.timedelta(days=1)
    return when.timestamp()


def remind(message: str, at: str = "", in_minutes: float = 0, call: bool = False) -> str:
    when = parse_when(at, in_minutes)
    _ensure_loop()
    wid = str(time.time())
    w = {"id": wid, "kind": "at", "label": message or "Reminder", "when": when, "call": bool(call),
         "deadline": when + 3600, "since": time.time()}
    with _lock:
        _watches[wid] = w
    _save_reminders()
    _changed()
    hhmm = dt.datetime.fromtimestamp(when).strftime("%H:%M")
    day = "" if dt.datetime.fromtimestamp(when).date() == dt.date.today() else " tomorrow"
    return f"Okay, I'll {'call' if call else 'notify'} you at {hhmm}{day}: {message or 'reminder'}."


def add(kind: str, label: str, name: str = "", path: str = "", threshold: float = 8, done_text: str = "") -> str:
    _ensure_loop()
    wid = str(time.time())
    w = {"id": wid, "kind": kind, "label": label, "name": name, "path": path, "threshold": threshold,
         "done_text": done_text, "deadline": time.time() + MAX_HOURS * 3600, "since": time.time()}
    # seed the "was it running / was there a partial" state immediately
    if kind == "process_exits":
        w["saw"] = bool(_procs(name))
    elif kind == "app_idle":
        for p in _procs(name):
            try:
                p.cpu_percent(None)
            except psutil.Error:
                pass
    elif kind == "download":
        w["saw_partial"] = bool(_partials(_downloads()))
    with _lock:
        _watches[wid] = w
    _changed()
    return f"Okay, I'll let you know: {label}."


def count() -> int:
    return len(_watches)


def clear() -> str:
    with _lock:
        n = len(_watches)
        _watches.clear()
    _save_reminders()
    _changed()
    return f"Cancelled {n} watch(es)." if n else "You have no active watches."


def listing() -> str:
    if not _watches:
        return "No active watches."
    parts = []
    for w in _watches.values():
        if w["kind"] == "at":
            parts.append(f"{dt.datetime.fromtimestamp(w['when']).strftime('%H:%M')} reminder: {w['label']}")
        else:
            parts.append(w["label"])
    return "Watching: " + "; ".join(parts)


def cancel(wid: str) -> str:
    """Cancel one watch or reminder (the X next to it on the phone)."""
    with _lock:
        w = _watches.pop(wid, None)
    if not w:
        return "That reminder isn't active any more."
    if w["kind"] == "at":
        _save_reminders()
    _changed()
    return f"Cancelled: {w['label']}."


def items() -> list[dict]:
    """Active watches and reminders for the phone, soonest reminder first."""
    with _lock:
        ws = list(_watches.values())
    out = [{"id": w["id"], "kind": w["kind"], "label": w["label"], "call": bool(w.get("call")),
            "when": int(w["when"] * 1000) if w["kind"] == "at" else None} for w in ws]
    return sorted(out, key=lambda w: (w["when"] is None, w["when"] or 0))
