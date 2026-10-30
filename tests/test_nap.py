"""Nap with the Windows calls faked: what happens on start, on Wake, and when someone touches the PC."""
import time

import pytest

from jarvis import nap


@pytest.fixture
def calls(monkeypatch):
    log = []
    monkeypatch.setattr(nap, "_lock_pc", lambda: log.append("lock"))
    monkeypatch.setattr(nap, "_screens", lambda on: log.append("screens on" if on else "screens off"))
    monkeypatch.setattr(nap, "_keep_awake", lambda on: log.append("awake" if on else "may sleep"))
    monkeypatch.setattr(nap, "CHECK", 0.05)
    monkeypatch.setattr(nap.time, "sleep", lambda s: None)
    monkeypatch.setattr(nap, "_since", 0.0)
    yield log
    nap._end(screens=False)


def _wait_for(cond, seconds=2.0):
    end = time.time() + seconds
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_nap_and_wake(calls, monkeypatch):
    monkeypatch.setattr(nap, "_idle_seconds", lambda: 1e9)   # nobody at the PC
    assert nap.turn_on().startswith("Napping")
    assert nap.is_on() and "already" in nap.turn_on()
    assert _wait_for(lambda: "screens off" in calls)
    assert calls[:3] == ["lock", "awake", "screens off"]
    assert nap.turn_off().startswith("Awake")
    assert _wait_for(lambda: "may sleep" in calls)
    assert "screens on" in calls and not nap.is_on()
    assert nap.turn_off() == "The PC isn't napping."


def test_using_the_pc_ends_the_nap(calls, monkeypatch):
    monkeypatch.setattr(nap, "SETTLE", 0)
    monkeypatch.setattr(nap, "_idle_seconds", lambda: 0.0)   # a hand on the mouse
    nap.turn_on()
    assert _wait_for(lambda: not nap.is_on())
    assert _wait_for(lambda: "may sleep" in calls)
    assert "screens on" not in calls   # they woke the screens themselves
