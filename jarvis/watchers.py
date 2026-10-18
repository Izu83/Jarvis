"""Background watches: "notify me when the download is done / when this app closes / when the export finishes".

Each watch polls a cheap condition every few seconds and, when it comes true, fires a notification (a phone push,
or a banner if the app is open). Watches auto-expire after a few hours so they never pile up.
"""
import logging
import os
import threading
import time
from pathlib import Path

import psutil

log = logging.getLogger("jarvis.watchers")

POLL = 8            # seconds between checks
MAX_HOURS = 8       # give up after this long
PARTIAL = (".crdownload", ".part", ".tmp", ".opdownload", ".download")

_watches: dict[str, dict] = {}
_lock = threading.Lock()
_notifier = None    # set by the app: fn(title, body)
_started = False


def set_notifier(fn) -> None:
    global _notifier
    _notifier = fn


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
        for w in due:
            log.info("Watch done: %s", w["label"])
            if _notifier:
                _notifier("✅ " + w["label"], w.get("done_text", "It's done."))


def _ensure_loop() -> None:
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_loop, daemon=True, name="watchers").start()


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
    return f"Okay, I'll let you know: {label}."


def count() -> int:
    return len(_watches)


def clear() -> str:
    with _lock:
        n = len(_watches)
        _watches.clear()
    return f"Cancelled {n} watch(es)." if n else "You have no active watches."


def listing() -> str:
    if not _watches:
        return "No active watches."
    return "Watching: " + "; ".join(w["label"] for w in _watches.values())
