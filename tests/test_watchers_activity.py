import datetime as dt
import json
import time

import pytest

from jarvis import activity, modes, watchers


def test_parse_when_in_minutes():
    assert abs(watchers.parse_when(in_minutes=10) - (time.time() + 600)) < 2


@pytest.mark.parametrize("text, hour, minute", [("10:53", 10, 53), ("10:53pm", 22, 53), ("7 pm", 19, 0),
                                                 ("12am", 0, 0), ("22.05", 22, 5)])
def test_parse_when_clock_times(text, hour, minute):
    when = dt.datetime.fromtimestamp(watchers.parse_when(text))
    assert (when.hour, when.minute) == (hour, minute)
    assert when > dt.datetime.now()   # always the NEXT such time


def test_parse_when_rejects_nonsense():
    with pytest.raises(ValueError):
        watchers.parse_when("whenever")


def test_reminders_listed_and_cancelled():
    watchers.clear()
    changes = []
    watchers.set_on_change(lambda: changes.append(1))
    watchers.remind("Call mom", in_minutes=30)
    items = watchers.items()
    assert [w["label"] for w in items] == ["Call mom"] and items[0]["when"]
    assert watchers.cancel(items[0]["id"]) == "Cancelled: Call mom."
    assert watchers.items() == [] and len(changes) == 2
    watchers.set_on_change(None)


def test_activity_records_steps_and_reply():
    activity.clear()
    tid = activity.start("open spotify", via="phone", msg_id="m1", status="queued")
    activity.step(tid, "App", "Opening Spotify…")
    activity.step(tid, "App", "Opening Spotify…")   # repeats are ignored
    assert activity.by_msg("m1")["status"] == "running"
    activity.finish(tid, "Opened Spotify.")
    e = activity.summary()[0]
    assert e["status"] == "done" and e["reply"] == "Opened Spotify." and e["steps"] == [{"k": "App", "t": "Opening Spotify…"}]


def test_activity_unfinished_entries_marked_after_restart():
    activity.clear()
    activity.start("still going")
    saved = json.loads(activity.PATH.read_text(encoding="utf-8"))
    assert saved[0]["status"] == "running"
    activity._load()   # what happens when Jarvis starts again
    assert activity.summary()[0]["status"] == "error"


def test_mode_match():
    modes.save(modes.DEFAULT_MODES)
    assert modes.match("Jarvis, work mode please") == "work"
    assert modes.match("start work mode") == "work"
    assert modes.match("what does work mode do") is None
    assert modes.match("party mode") is None
