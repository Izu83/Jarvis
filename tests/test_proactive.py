"""Jarvis's own messages: extra ones during a task wait in the inbox until the phone confirms them, and scheduled
tasks / new important emails make him message the user without a prompt."""
import datetime as dt
import time

import pytest

from jarvis import inbox, proactive
from jarvis.config import settings


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "PATH", tmp_path / "inbox.json")
    monkeypatch.setattr(inbox, "_items", [])
    monkeypatch.setattr(inbox, "_sent_at", {})
    monkeypatch.setattr(proactive, "TASKS_FILE", tmp_path / "schedule.json")
    monkeypatch.setattr(proactive, "STATE_FILE", tmp_path / "proactive.json")
    monkeypatch.setattr(proactive, "_tasks", {})
    monkeypatch.setattr(proactive, "_state", {})
    monkeypatch.setattr(proactive, "_ensure_loop", lambda: None)   # tests drive the checks themselves
    ran, said = [], []
    proactive.set_handlers(lambda task, chat: ran.append((task, chat)),
                           lambda text, chat, notify: said.append((text, notify)))
    settings.update(proactive="normal", quiet_hours=[23, 8], email_address="me@example.com")
    yield ran, said
    proactive.set_handlers(None, None)


def test_messages_wait_until_the_phone_has_them():
    item = inbox.add("  On it, this takes a minute.  ", "chat1", reply_to="cmd7")
    body = inbox.payload(item)
    assert body["kind"] == "msg" and body["text"] == "On it, this takes a minute."
    assert body["chat"] == "chat1" and body["reply_to"] == "cmd7"
    inbox.mark_sent(item["mid"])
    assert inbox.due() == [] and len(inbox.due(force=True)) == 1   # resent when the app (re)connects
    inbox._load()                                                   # survives a restart
    assert len(inbox._items) == 1
    inbox.done(item["mid"])
    assert inbox.due(force=True) == []


def test_a_scheduled_task_runs_then_is_gone(clean):
    ran, _ = clean
    msg = proactive.schedule("Tell me the weather", in_minutes=30, chat="c1")
    # "tomorrow at" when the test runs in the last half hour before midnight
    assert msg.startswith(("Okay, at ", "Okay, tomorrow at ")) and msg.endswith("message you: Tell me the weather.")
    assert proactive.items()[0]["label"] == "Tell me the weather"
    proactive._run_due()
    assert ran == []                                   # not yet
    next(iter(proactive._tasks.values()))["when"] = time.time() - 1
    proactive._run_due()
    assert ran == [(proactive.TASK_PREFIX + "Tell me the weather", "c1")]
    assert proactive._tasks == {}


def test_repeating_tasks_move_to_their_next_time(clean):
    ran, _ = clean
    proactive.schedule("Check my email", at="08:00", repeat="daily")
    t = next(iter(proactive._tasks.values()))
    t["when"] -= 86400 * 2                              # due two days ago (Jarvis was off): skipped, not done late
    proactive._run_due()
    assert ran == [] and t["when"] > time.time() and dt.datetime.fromtimestamp(t["when"]).hour == 8
    assert "every day at 08:00" in proactive.listing()
    t["when"] = time.time() - 60                        # due a minute ago: runs, then comes back tomorrow
    proactive._run_due()
    assert len(ran) == 1 and t["when"] > time.time() + 3600
    assert proactive.items()[0]["label"].startswith("🔁")
    assert "every day at " in proactive.listing()
    assert proactive.cancel(t["id"]).startswith("Cancelled 1") and proactive.cancel(t["id"]) is None


def test_weekdays_skip_the_weekend():
    friday = dt.datetime(2026, 10, 30, 9, 0).timestamp()   # a Friday
    nxt = dt.datetime.fromtimestamp(proactive.next_time(friday, "weekdays", friday + 60))
    assert nxt.weekday() == 0 and nxt.hour == 9             # Monday, same time


def test_quiet_hours():
    assert proactive.quiet(dt.datetime(2026, 10, 29, 23, 30))
    assert proactive.quiet(dt.datetime(2026, 10, 29, 7, 59))
    assert not proactive.quiet(dt.datetime(2026, 10, 29, 12, 0))


def test_new_important_email_gets_a_heads_up(clean, monkeypatch):
    _, said = clean
    from jarvis import mail
    inbox_now = [{"id": "1", "from": "Ana <ana@x.com>", "subject": "Old one", "snippet": "", "important": True}]
    monkeypatch.setattr(mail, "check", lambda unread_only=True, limit=10: list(inbox_now))
    proactive._check_mail()
    assert said == []                                   # the first look only learns what's already there
    inbox_now.insert(0, {"id": "2", "from": "Ana <ana@x.com>", "subject": "Dinner?", "snippet": "Free at 8?",
                         "important": True})
    inbox_now.insert(0, {"id": "3", "from": "Shop <noreply@shop.com>", "subject": "Sale", "snippet": "",
                         "important": False})
    proactive._check_mail()
    assert len(said) == 1 and "Ana" in said[0][0] and "Dinner?" in said[0][0]
    proactive._check_mail()
    assert len(said) == 1                               # never twice for the same email


def test_briefing_has_its_own_switch_and_comes_once_a_day():
    morning = dt.datetime(2026, 10, 29, 8, 45)
    settings["morning_time"] = "08:30"
    settings["briefing"] = False
    assert not proactive._morning_due(morning)          # switched off
    settings["briefing"] = True
    settings["proactive"] = "off"
    assert proactive._morning_due(morning)              # its own switch, whatever "messages on his own" says
    assert not proactive._morning_due(dt.datetime(2026, 10, 29, 15, 0))   # PC switched on late: skip it
    proactive._state["morning"] = "2026-10-29"
    assert not proactive._morning_due(morning)


def test_delays_written_into_at_still_work():
    from jarvis.watchers import parse_when
    for text, minutes in (("in 1 minute", 1), ("in_minutes=1", 1), ("in 2 hours", 120), ("in 15", 15)):
        assert abs(parse_when(text) - (time.time() + minutes * 60)) < 5, text
