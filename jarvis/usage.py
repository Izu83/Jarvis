"""Counts how often (and how many tokens) Jarvis has used Claude, so Settings can show it.

A tiny JSON counter in .brain/usage.json. Hermes handling a command locally records a "local" (free) call;
any Claude call — whether Claude is the main brain or just Hermes's fallback — records its token usage here.
"""
import json
import logging
import threading

from .config import ROOT

log = logging.getLogger("jarvis.usage")
PATH = ROOT / ".brain" / "usage.json"
_lock = threading.Lock()
_data = {"claude_calls": 0, "tokens_in": 0, "tokens_out": 0, "local_calls": 0}

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


def summary() -> dict:
    with _lock:
        d = dict(_data)
    d["tokens_total"] = d["tokens_in"] + d["tokens_out"]
    return d


def reset() -> None:
    with _lock:
        for k in _data:
            _data[k] = 0
        _save()
