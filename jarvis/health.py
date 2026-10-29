"""Jarvis keeps an eye on himself: when something he depends on breaks, he messages you once what's wrong and how
to fix it, and again when it works again. "/health" (or "are you okay?") asks for the full check right now.

Problems are reported from where they happen (the Claude brain, email checks, the local models, the microphone)
through problem() / fixed(). A problem that's still there is mentioned again at most once a day.
"""
import logging
import re
import threading
import time

log = logging.getLogger("jarvis.health")

REMIND_AFTER = 24 * 3600

_lock = threading.Lock()
_active: dict[str, dict] = {}   # key -> {"text", "since", "told"}
_send = None                    # fn(text): message the user (set by __main__)

_LOGIN = re.compile(r"/login|log ?in|logged out|authenticat|api key|oauth|401|unauthori[sz]ed", re.I)
_LIMIT = re.compile(r"usage limit|limit reached|rate.?limit|quota|429|too many requests|resets? ", re.I)


def set_sender(fn) -> None:
    global _send
    _send = fn


def _tell(text: str) -> None:
    log.info("Health: %s", text)
    if _send is not None:
        try:
            _send(text)
        except Exception:
            log.debug("couldn't send a health message", exc_info=True)


def problem(key: str, text: str) -> None:
    """Something's broken. The first time (or a day later, if it still is) the user hears about it."""
    now = time.time()
    with _lock:
        p = _active.get(key)
        if p and now - p["told"] < REMIND_AFTER:
            p["text"] = text
            return
        _active[key] = {"text": text, "since": p["since"] if p else now, "told": now}
    _tell("⚠️ " + text)


def fixed(key: str, text: str = "") -> None:
    with _lock:
        was = _active.pop(key, None)
    if was and text:
        _tell("✅ " + text)


def problems() -> list[str]:
    with _lock:
        return [p["text"] for p in _active.values()]


# ------------------------------------------------------------------ what the brains report

def claude_error(result: str) -> None:
    """Claude Code finished with an error: say what it means in plain words."""
    if _LOGIN.search(result):
        problem("claude_login", "Claude isn't logged in on your PC any more, so I can only use the slower local "
                "models. Fix: on the PC, open a terminal, run `claude`, type /login and sign in again.")
    elif _LIMIT.search(result):
        problem("claude_limit", f"You've hit your Claude usage limit, so the local models are covering until it "
                f"resets. Claude said: {result.strip()[:200]}")


def claude_ok() -> None:
    fixed("claude_login", "Claude is logged in and working again.")
    fixed("claude_limit", "Claude's usage limit has reset; I'm back to full speed.")


def out_of_memory() -> None:
    """Windows refused memory (a MemoryError here, "Memory allocation failure" in the local models). On this PC
    that's the page file: it lives on C:, and a full C: stops it growing."""
    problem("memory", "Windows ran out of memory to give out, so things fail at random (the local models, the "
            "microphone). The page file can't grow because C: is nearly full. Fix: free 10 GB or more on C:, or "
            "move the page file to D: (Settings › System › About › Advanced system settings › Performance › "
            "Settings › Advanced › Virtual memory).")


def _free_commit_gb() -> float | None:
    """How much more memory Windows can hand out (RAM + what the page file can still take)."""
    import ctypes

    class MEM(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MEM(dwLength=ctypes.sizeof(MEM))
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return None
    return m.ullAvailPageFile / 1024**3


# ------------------------------------------------------------------ the full check ("/health")

def check_now() -> str:
    """Everything at once, in a few lines: what's broken first, then a short all-clear for the rest."""
    lines = list(problems())
    try:
        from . import sysinfo
        for d in sysinfo.disks():
            free = d["free"] / 1024**3
            if d["total"] >= 32 * 1024**3 and free < 5:
                lines.append(f"{d['name']} has only {free:.1f} GB free; Windows and the local models can fail "
                             f"when it's this full.")
    except Exception:
        log.debug("disk check failed", exc_info=True)
    try:
        free = _free_commit_gb()
        if free is not None and free < 4:
            lines.append(f"Windows can only hand out {free:.1f} GB more memory, so a local model loading can make "
                         f"things fail. Freeing space on C: lets the page file grow.")
    except Exception:
        log.debug("memory check failed", exc_info=True)
    try:
        from .config import settings
        if settings.get("brain") == "hermes" and settings.get("local_first"):
            from .hermes import local_status
            for m in local_status():
                if m["state"] in ("offline", "missing", "resting"):
                    lines.append(f"Local model {m['name']} is {m['state']}.")
    except Exception:
        log.debug("local brain check failed", exc_info=True)
    try:
        from . import camera
        if not camera.present():
            lines.append("No webcam is connected, so /camera and away mode's photo won't work.")
    except Exception:
        pass
    if not lines:
        return "Everything's working: Claude, the local models, your phone link, disk space and the webcam."
    seen, out = set(), []
    for line in lines:   # the same thing can come from a report and from the check
        if line not in seen:
            seen.add(line)
            out.append("• " + line)
    return "Here's what's not right:\n" + "\n".join(out)
