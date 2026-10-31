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
_data = {"claude_calls": 0, "tokens_in": 0, "tokens_out": 0, "local_calls": 0, "others": {}, "spend": {}}

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


def record_claude(tokens_in=0, tokens_out=0) -> None:
    with _lock:
        _data["claude_calls"] += 1
        _data["tokens_in"] += int(tokens_in or 0)
        _data["tokens_out"] += int(tokens_out or 0)
        _save()


def record_local() -> None:
    with _lock:
        _data["local_calls"] += 1
        _save()


def record_other(provider: str, tokens_in=0, tokens_out=0, cost=0.0) -> None:
    """A Gemini / ChatGPT / Grok call; cost (USD, estimated) only for the pay-per-use ones."""
    with _lock:
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
        d = dict(_data)
    d["tokens_total"] = d["tokens_in"] + d["tokens_out"]
    d["month_spend"] = round(float(d.get("spend", {}).get(dt.date.today().strftime("%Y-%m"), 0.0)), 4)
    return d


def reset() -> None:
    with _lock:
        for k in _data:
            if k != "spend":   # the month's spend stays: it keeps the paid models under budget
                _data[k] = {} if isinstance(_data[k], dict) else 0
        _save()
