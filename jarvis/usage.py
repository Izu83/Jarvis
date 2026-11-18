"""Counts how often (and how many tokens) Jarvis has used Claude, so Settings can show it.

A tiny JSON counter in .brain/usage.json. Hermes handling a command locally records a "local" (free) call;
any Claude call — whether Claude is the main brain or just Hermes's fallback — records its token usage here.
Gemini, ChatGPT and Grok are counted per provider ("others"), and the pay-per-use ones' estimated cost per month
("spend", in USD), which keeps them under your budget (cloud.py).
"""
import datetime as dt
import json
import logging
import threading

from .config import ROOT

log = logging.getLogger("jarvis.usage")
PATH = ROOT / ".brain" / "usage.json"
_lock = threading.Lock()
_data = {"claude_calls": 0, "tokens_in": 0, "tokens_out": 0, "local_calls": 0, "others": {}, "spend": {},
         "daily": {}, "last": {}}
KEEP_DAYS = 62   # the daily counts kept: enough for this month and the one before

try:
    _data.update(json.loads(PATH.read_text(encoding="utf-8")))
except Exception:
    pass


def _save() -> None:
    try:
        PATH.parent.mkdir(exist_ok=True)
        PATH.write_text(json.dumps(_data), encoding="utf-8")
    except Exception:
        log.debug("couldn't save usage", exc_info=True)


def _day(who: str, tokens_in=0, tokens_out=0, cost=0.0) -> None:
    """Count one request for who ("claude", "local", "gemini"...) under today's date (the lock is already held)."""
    days = _data.setdefault("daily", {})
    today = dt.date.today().isoformat()
    d = days.setdefault(today, {}).setdefault(who, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0})
    d["calls"] += 1
    d["tokens_in"] += int(tokens_in or 0)
    d["tokens_out"] += int(tokens_out or 0)
    d["cost"] = round(d.get("cost", 0.0) + float(cost or 0), 6)
    for old in sorted(days)[:-KEEP_DAYS]:
        del days[old]


def record_answer(who: str, model: str, seconds: float) -> None:
    """How long the last answer took, and from which model (the details page shows it)."""
    import time
    with _lock:
        _data.setdefault("last", {})[who] = {"model": model, "secs": round(seconds, 1), "at": time.time()}
        _save()


def stats(who: str) -> dict:
    """Requests and tokens today, this month and all time, and the last answer, for one of them."""
    today = dt.date.today()
    with _lock:
        days = {k: dict(v.get(who) or {}) for k, v in _data.get("daily", {}).items()}
        last = dict(_data.get("last", {}).get(who) or {})
        if who == "claude":
            total = {"calls": _data["claude_calls"], "tokens_in": _data["tokens_in"], "tokens_out": _data["tokens_out"]}
        elif who == "local":
            total = {"calls": _data["local_calls"], "tokens_in": 0, "tokens_out": 0}
        else:
            total = dict(_data.get("others", {}).get(who) or {"calls": 0, "tokens_in": 0, "tokens_out": 0})

    def add(keys):
        out = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0}
        for k in keys:
            for f in out:
                out[f] += days[k].get(f, 0) or 0
        out["cost"] = round(out["cost"], 4)
        return out
    week = {(today - dt.timedelta(days=i)).isoformat() for i in range(7)}
    return {"today": add([today.isoformat()] if today.isoformat() in days else []),
            "week": add([k for k in days if k in week]),
            "month": add([k for k in days if k.startswith(today.strftime("%Y-%m"))]),
            "total": total, "last": last,
            "days": [[(today - dt.timedelta(days=i)).isoformat(), days.get((today - dt.timedelta(days=i)).isoformat(), {}).get("calls", 0)]
                     for i in range(13, -1, -1)]}   # the last 14 days, oldest first, for the little chart


def record_claude(tokens_in=0, tokens_out=0) -> None:
    with _lock:
        _day("claude", tokens_in, tokens_out)
        _data["claude_calls"] += 1
        _data["tokens_in"] += int(tokens_in or 0)
        _data["tokens_out"] += int(tokens_out or 0)
        _save()


def record_local() -> None:
    with _lock:
        _day("local")
        _data["local_calls"] += 1
        _save()


def record_other(provider: str, tokens_in=0, tokens_out=0, cost=0.0) -> None:
    """A Gemini / ChatGPT / Grok call; cost (USD, estimated) only for the pay-per-use ones."""
    with _lock:
        _day(provider, tokens_in, tokens_out, cost)
        o = _data.setdefault("others", {}).setdefault(provider, {"calls": 0, "tokens_in": 0, "tokens_out": 0})
        o["calls"] += 1
        o["tokens_in"] += int(tokens_in or 0)
        o["tokens_out"] += int(tokens_out or 0)
        if cost:
            month = dt.date.today().strftime("%Y-%m")
            _data.setdefault("spend", {})[month] = round(_data["spend"].get(month, 0.0) + cost, 6)
        _save()


def month_spend() -> float:
    """This month's estimated spend on the pay-per-use models (USD)."""
    with _lock:
        return float(_data.get("spend", {}).get(dt.date.today().strftime("%Y-%m"), 0.0))


def summary() -> dict:
    with _lock:
        d = {k: v for k, v in _data.items() if k not in ("daily", "last")}   # those are for the details page
    d["tokens_total"] = d["tokens_in"] + d["tokens_out"]
    d["month_spend"] = round(float(d.get("spend", {}).get(dt.date.today().strftime("%Y-%m"), 0.0)), 4)
    return d


def reset() -> None:
    with _lock:
        for k in _data:
            if k not in ("spend", "daily", "last"):   # the month's spend stays: it keeps the paid models under budget
                _data[k] = {} if isinstance(_data[k], dict) else 0
        _save()
