"""Files for the phone wait in the outbox until the phone confirms them, so they arrive even if the app was closed."""
import base64

import pytest

from jarvis import outbox


@pytest.fixture(autouse=True)
def empty_outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(outbox, "PATH", tmp_path / "outbox.json")
    monkeypatch.setattr(outbox, "_items", [])
    monkeypatch.setattr(outbox, "_sent_at", {})


def test_kept_until_confirmed(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"video bytes")
    item = outbox.add(str(f), f.name, 11, "", "chat1")
    assert item["mime"] == "video/mp4"
    assert [i["fid"] for i in outbox.due()] == [item["fid"]]

    body = outbox.payload(item)
    assert base64.b64decode(body["data"]) == b"video bytes" and body["chat"] == "chat1"
    outbox.mark_sent(item["fid"])
    assert outbox.due() == []           # just sent: not again straight away
    assert len(outbox.due(force=True)) == 1   # unless the phone just (re)connected

    outbox._sent_at[item["fid"]] = 0     # long ago and still not confirmed -> send again
    assert len(outbox.due()) == 1
    outbox.done(item["fid"])
    assert outbox.due() == [] and outbox._items == []


def test_survives_restart_and_drops_missing_files(tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    outbox.add(str(f), f.name, 1, "image/png", None)
    outbox._load()                       # as if Jarvis restarted
    assert len(outbox._items) == 1
    f.unlink()
    assert outbox.due() == [] and outbox._items == []

