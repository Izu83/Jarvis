"""/info in the app: one short page about Jarvis right now. The connection (tunnel or direct, who's connected), how
much of the PC Jarvis itself is using, listening, which AIs answer, and today's usage. Read-only, nothing is sent
anywhere, no AI is asked (it costs nothing)."""
import datetime as dt
import os
import threading
import time

import psutil

from .config import settings

STARTED = time.time()


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h {seconds % 3600 // 60} min ago"
    return f"{seconds // 86400} d ago"


def _for(seconds: float) -> str:
    seconds = max(0, int(seconds))
    d, h, m = seconds // 86400, seconds % 86400 // 3600, seconds % 3600 // 60
    return f"{d} d {h} h" if d else f"{h} h {m} min" if h else f"{m} min"


def _gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


def _me() -> str:
    """Jarvis's own share of the PC: its process and the helpers it started (overlay, media, browser...)."""
    me = psutil.Process(os.getpid())
    procs = [me]
    try:
        procs += me.children(recursive=True)
    except Exception:
        pass
    for p in procs:
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    time.sleep(0.5)
    cpu, mem = 0.0, 0
    for p in procs:
        try:
            cpu += p.cpu_percent(None)
            mem += p.memory_info().rss
        except Exception:
            pass
    cores = psutil.cpu_count() or 1
    return (f"{cpu / cores:.1f}% of the CPU, {_gb(mem)} of memory, {threading.active_count()} threads "
            f"({len(procs) - 1} helper processes)")


def _listening() -> str:
    if not settings.get("voice_enabled", True):
        return "Off (voice is turned off in config)."
    from . import voice
    on = settings.get("listening", True)
    parts = ["“Hey Jarvis”: on" if on else "“Hey Jarvis”: off (Ctrl+Alt+J still works)"]
    if on and settings.get("wake_name_alone") is True:
        parts.append("also just “Jarvis”")
    for listener in voice._listeners:
        whisper = getattr(listener, "whisper", None)
        if whisper is None:
            parts.append("Whisper put away (loads when needed)")
        else:
            where = getattr(getattr(whisper, "model", None), "device", "cpu")
            parts.append(f"Whisper {voice.model_for(listener.loaded_for or 'en')} on the "
                         f"{'graphics card' if where == 'cuda' else 'CPU'}")
    return ", ".join(parts) + "."


def _connection(phone) -> str:
    from .tunnel import last_url
    url = last_url()
    lines = [f"Through a Cloudflare tunnel ({url.split('//')[-1]})" if url else "No tunnel yet: only this PC can reach Jarvis"]
    if phone is not None:
        phones = len(phone.clients) - len(phone.desktops)
        desk = len(phone.desktops)
        now = [f"{phones} phone{'s' if phones != 1 else ''}" if phones else "", "the desktop app" if desk else ""]
        now = " and ".join(x for x in now if x)
        lines.append(f"connected now: {now}" if now else "nothing connected right now")
        if phone.phone_seen and not phones:
            lines.append(f"phone last seen {_ago(time.time() - phone.phone_seen)}")
    return ", ".join(lines) + "."


def _brains() -> str:
    from .brain import RUNNING
    names = []
    if settings.get("use_claude", True):
        model = RUNNING.get("model") or settings.get("claude_code_model", "sonnet")
        names.append(f"Claude ({model})")
    from . import cloud
    for provider in cloud.PROVIDERS:
        try:
            if cloud.usable(provider):
                names.append(cloud.NAMES.get(provider, provider))
        except Exception:
            pass
    local = [n for n, k in (("Hermes", "use_hermes"), ("Qwen3", "use_qwen")) if settings.get(k, True)]
    text = ", ".join(names) or "none"
    if local:
        text += f"; on this PC: {', '.join(local)}" + (" (tried first)" if settings.get("local_first") else "")
    return text + "."


def _today() -> str:
    from . import usage
    s = usage.summary()
    day = dt.date.today().isoformat()
    try:
        with usage._lock:
            today = dict(usage._data.get("daily", {}).get(day, {}))
    except Exception:
        today = {}
    calls = sum(v.get("calls", 0) for v in today.values())
    claude = today.get("claude", {})
    toks = claude.get("tokens_in", 0) + claude.get("tokens_out", 0)
    spend = s.get("month_spend", 0.0)
    out = f"{calls} answer{'s' if calls != 1 else ''} today"
    if claude:
        out += f", {claude.get('calls', 0)} from Claude ({toks:,} tokens)"
    out += f". Pay-per-use this month: ${spend:.2f}."
    return out


def text(phone=None, app: str = "") -> str:
    vm = psutil.virtual_memory()
    rows = [
        ("Jarvis", f"running for {_for(time.time() - STARTED)}" + (f", app {app}" if app else "") + "."),
        ("Connection", _connection(phone)),
        ("Jarvis's share of the PC", _me() + "."),
        ("This PC", f"CPU {psutil.cpu_percent(None):.0f}%, memory {vm.percent:.0f}% "
                    f"({_gb(vm.used)} of {_gb(vm.total)}), up for {_for(time.time() - psutil.boot_time())}."),
        ("Listening", _listening()),
        ("AIs", _brains()),
        ("Usage", _today()),
    ]
    out = []
    for name, value in rows:
        try:
            out.append(f"**{name}:** {value}")
        except Exception:
            pass
    return "\n".join(out)


def safe_text(phone=None, app: str = "") -> str:
    try:
        return text(phone, app)
    except Exception as e:   # never leave the app waiting
        return f"Couldn't put the info together ({type(e).__name__}: {e})."
