"""The answer as it's written, the self-check messages, away mode's trigger logic, and Bulgarian."""
import time

import pytest

from jarvis import away, health, nlu, partial, stopping


def test_partial_is_throttled_and_cleared():
    got = []
    partial.set_sink(got.append)
    partial.update("Hel")
    partial.update("Hello")           # too soon after the first: skipped
    time.sleep(partial.EVERY + 0.05)
    partial.update("Hello there")
    partial.clear()
    partial.set_sink(None)
    partial.update("nobody listening")
    assert got == ["Hel", "Hello there", ""]


def test_health_tells_once_then_again_when_fixed(monkeypatch):
    told = []
    health.set_sender(told.append)
    health._active.clear()
    health.claude_error("Invalid API key · Please run /login")
    health.claude_error("Invalid API key · Please run /login")   # still broken: not repeated
    assert len(told) == 1 and "/login" in told[0]
    health.claude_ok()
    assert len(told) == 2 and told[1].startswith("✅")
    health.claude_ok()                                            # nothing was broken: silent
    assert len(told) == 2
    health.claude_error("Claude AI usage limit reached|1793300000")
    assert "usage limit" in told[-1]
    health._active.clear()


def test_health_reminds_after_a_day(monkeypatch):
    told = []
    health.set_sender(told.append)
    health._active.clear()
    health.problem("x", "broken")
    now = time.time()
    monkeypatch.setattr(health.time, "time", lambda: now + health.REMIND_AFTER + 1)
    health.problem("x", "broken")
    assert len(told) == 2
    health._active.clear()


def test_away_only_physical_input_after_settling_and_cooldown(monkeypatch):
    alerts = []
    away.set_handler(lambda: alerts.append(1))
    monkeypatch.setattr(away.threading, "Thread", lambda target, **kw: type("T", (), {"start": lambda s: target()})())
    away._state.update(on=True, since=time.time() - 10, last_alert=0.0, own_until=0.0)
    away._physical()
    away._physical()                         # within the cooldown: one alert only
    assert alerts == [1]
    away._state.update(last_alert=0.0)
    away.note_own_input()                    # Jarvis is moving the mouse itself
    away._physical()
    assert alerts == [1]
    away._state.update(own_until=0.0, since=time.time())   # just turned on: you're still at the desk
    away._physical()
    assert alerts == [1]
    away._state.update(on=False)
    away._physical()
    assert alerts == [1]


@pytest.mark.parametrize("text,on", [("away mode on", True), ("turn on away mode", True), ("I'm leaving", True),
                                     ("away mode off", False), ("I'm back", False), ("turn off away mode", False)])
def test_away_phrases(monkeypatch, text, on):
    calls = []
    monkeypatch.setattr(away, "turn_on", lambda: calls.append("on") or "on")
    monkeypatch.setattr(away, "turn_off", lambda: calls.append("off") or "off")
    assert nlu.try_handle(text) == ("on" if on else "off")


def test_health_phrase(monkeypatch):
    monkeypatch.setattr(health, "check_now", lambda: "all good")
    assert nlu.try_handle("are you okay?") == "all good"
    assert nlu.try_handle("health check") == "all good"


@pytest.mark.parametrize("text", ["спри", "Джарвис, спри", "стоп", "стига", "няма значение"])
def test_bulgarian_stop_phrases(text):
    assert stopping.is_stop_phrase(text)
