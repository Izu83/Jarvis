"""Jarvis checking in: now and then he asks what you're up to, or offers help with what's on your screen.

How often is the "check_ins" setting: "off", "sometimes" (every 2.5 to 4 hours at most) or "often" (every 1 to 2
hours), never more than a few times a day. He doesn't check in:
- during quiet hours, a nap, away mode, or while a game or film is full screen,
- while you're already talking to him (anything in the last 30 minutes),
- after two check-ins you didn't answer, until you say something again.

The words come from a free model (Gemini, the free OpenRouter ones, or Hermes on this PC) given the time, what's on screen, the music,
what he knows about you and what you talked about last; with none of those set up, from a small list of his own.
They arrive as a message in the app (a notification when it's closed) and, with "check_in_aloud" on and you at the
PC, he says them out loud too. Your reply is an ordinary message, so the conversation just carries on.
"""
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import logging
import random
import re
import threading
import time

import requests

from .config import secret, settings

log = logging.getLogger("jarvis.checkin")

LEVELS = ("off", "sometimes", "often")
GAP = {"sometimes": (150, 240), "often": (60, 120)}   # minutes between check-ins, picked at random in this range
PER_DAY = {"sometimes": 4, "often": 8}
BUSY = 30 * 60        # you talked to him this recently: no need to check in
AT_PC = 5 * 60        # input this recently means you're at the PC
MAX_UNANSWERED = 2

_state = {"last": 0.0, "next": 0.0, "day": "", "count": 0, "unanswered": 0, "heard": 0.0, "recent": []}


def level() -> str:
    value = settings.get("check_ins", "sometimes")
    return value if value in LEVELS else "sometimes"


def heard() -> None:
    """You said or typed something: you're around, and any check-in counts as answered."""
    _state["heard"] = time.time()
    _state["unanswered"] = 0


def _plan_next(now: float) -> None:
    lo, hi = GAP.get(level(), GAP["sometimes"])
    wait = random.uniform(lo, hi) * 60 * (2 if _state["unanswered"] else 1)
    _state["next"] = now + wait


# ---------------------------------------------------------------- what's going on

class _LASTINPUT(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def idle_seconds() -> float:
    info = _LASTINPUT(ctypes.sizeof(_LASTINPUT), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 1e9
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def fullscreen() -> bool:
    """A game or a film filling the screen (the desktop and the taskbar don't count)."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd or hwnd in (user32.GetDesktopWindow(), user32.GetShellWindow()):
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
        return False
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    mi = _MONITORINFO(ctypes.sizeof(_MONITORINFO))
    if not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi)):
        return False
    m = mi.rcMonitor
    return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom


def on_screen() -> str:
    """The front window, like 'code: "main.py - Jarvis - Visual Studio Code"'."""
    try:
        from . import windows
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        w = next((w for w in windows.list_windows() if w.hwnd == hwnd), None)
        return f'{w.app}: "{w.title[:120]}"' if w else ""
    except Exception:
        return ""


def _within(seconds: float, fn):
    """fn()'s result, or None if it takes longer than `seconds` or fails."""
    box = {}

    def go():
        try:
            box["v"] = fn()
        except Exception:
            log.debug("%s failed", getattr(fn, "__name__", fn), exc_info=True)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(seconds)
    return box.get("v")


def _context(at_pc: bool) -> str:
    from . import memory, pc
    now = dt.datetime.now()
    lines = [f"It's {now.strftime('%A %H:%M')}."]
    if at_pc:
        screen = on_screen()
        lines.append(f"The user is at the PC. In front: {screen}." if screen else "The user is at the PC.")
    else:
        lines.append("The user isn't at the PC right now (they'll see this on their phone).")
    music = _within(3, pc.now_playing)   # Windows' media session can stall: don't wait for it
    if music and music.get("playing") and music.get("title"):
        lines.append(f"Playing: {music.get('artist', '')} - {music['title']}.")
    about = memory.about()
    if about:
        lines.append(f"About the user: {about[:600]}")
    try:
        talk = memory.recent(None, limit=3)
        if talk:
            lines.append("Last things you talked about: " + " | ".join(e.get("user", "")[:100] for e in talk if e.get("user")))
    except Exception:
        pass
    if _state["recent"]:
        lines.append("Your last check-ins (say something different): " + " | ".join(_state["recent"][-4:]))
    return "\n".join(lines)


PROMPT = """You are Jarvis, the user's friendly personal assistant on their PC and phone. Nobody asked you anything:
you're checking in on your own, like a friend who's around. Write ONE short message (at most 25 words): ask what
they're up to, or offer help with what they seem to be doing (be specific when what's on screen makes it clear).
Casual and warm, a little witty, never pushy, no "As an AI". No quotes around it. {lang}

{context}"""


def _from_model(context: str) -> str | None:
    """A free model writes it; None when none is set up or none answers."""
    from . import cloud
    lang = {"bg": "Write in Bulgarian.", "both": "Write in the language the user mostly uses with you."}.get(
        settings.get("voice_language", "en"), "Write in English.")
    prompt = PROMPT.format(lang=lang, context=context)
    for provider in ("gemini", "openrouter"):
        if not cloud.usable(provider):
            continue
        brain = cloud.CloudBrain(provider)
        try:
            r = brain._post(lambda model: requests.post(
                f"{brain.base}/chat/completions", timeout=40, headers={"Authorization": f"Bearer {brain.key}"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}]}))
            if r is not None and r.status_code < 400:
                text = _clean(((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "")
                if text:
                    return text
        except (requests.RequestException, ValueError):
            log.debug("%s couldn't write a check-in", provider, exc_info=True)
    return _from_local(prompt)


def _clean(text: str) -> str | None:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip().strip('"').strip()
    return text if 3 < len(text) < 300 else None


def _from_local(prompt: str) -> str | None:
    """The free model on this PC (Hermes, through Ollama), when it's set up."""
    if not settings.get("use_hermes", True) or not settings.get("hermes_model"):
        return None
    try:
        r = requests.post(f"{settings.get('ollama_host', 'http://127.0.0.1:11434').rstrip('/')}/api/chat", timeout=150,   # the first one loads the model
                          json={"model": settings["hermes_model"], "stream": False, "keep_alive": "5m",
                                "messages": [{"role": "user", "content": prompt}], "options": {"num_predict": 90}})
        if r.status_code < 400:
            return _clean((r.json().get("message") or {}).get("content", ""))
    except (requests.RequestException, ValueError):
        log.debug("The local model couldn't write a check-in", exc_info=True)
    return None


OWN = {
    "morning": ["Morning! What's the plan for today? Anything I can set up for you?",
                "Good morning. Want me to line anything up for today?"],
    "day": ["What are you up to? Anything I can take off your hands?",
            "Just checking in. Need a hand with anything?",
            "How's it going? If there's something boring to do on the PC, I'm free."],
    "evening": ["How was your day? Anything you want me to do before tomorrow?",
                "Evening! Need anything sorted, or shall I just stay quiet?"],
}
OWN_AT = ["I see you're in {app}. Need a hand with anything there?",
          "Busy in {app}? Shout if I can help."]


def _own(at_pc: bool) -> str:
    hour = dt.datetime.now().hour
    lines = list(OWN["morning" if hour < 12 else "day" if hour < 18 else "evening"])
    screen = on_screen() if at_pc else ""
    app = screen.split(":")[0] if screen else ""
    if app and app not in ("explorer", "jarvis", "unknown"):
        lines += [line.format(app=app.capitalize()) for line in OWN_AT]
    fresh = [line for line in lines if line not in _state["recent"]] or lines
    return random.choice(fresh)


def compose(at_pc: bool) -> str:
    return _from_model(_context(at_pc)) or _own(at_pc)


# ---------------------------------------------------------------- when

def due(now: float | None = None) -> bool:
    """Is it time for a check-in? (Reads the PC only when the cheap checks pass.)"""
    from . import away, nap, proactive
    now = time.time() if now is None else now
    if level() == "off" or proactive.level() == "off" or proactive.quiet():
        return False
    today = dt.date.fromtimestamp(now).isoformat()
    if _state["day"] != today:
        _state.update(day=today, count=0)
    if not _state["next"]:   # just started: the first one comes after a while, not at once
        _plan_next(now)
        return False
    if now < _state["next"] or _state["count"] >= PER_DAY.get(level(), 4):
        return False
    if _state["unanswered"] >= MAX_UNANSWERED or now - _state["heard"] < BUSY:
        return False
    if nap.is_on() or away.is_on():
        return False
    return not fullscreen()


def run(send, speak=None) -> str | None:
    """Check in, if it's time: send(text) puts it in the app; speak(text) says it at the PC."""
    now = time.time()
    if not due(now):
        return None
    _state["count"] += 1   # booked before writing it (a model can take a while), so it can't come twice
    _state["last"] = now
    _plan_next(now)
    _state["unanswered"] += 1
    at_pc = idle_seconds() < AT_PC
    text = compose(at_pc)
    _state["recent"] = (_state["recent"] + [text])[-6:]
    log.info("Checking in: %s", text)
    send(text)
    if speak and at_pc and settings.get("check_in_aloud"):
        speak(text)
    return text
