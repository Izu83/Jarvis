"""The same chats on the phone and the computer (chatsync.py): merging never loses a message, the newest title wins,
deleted chats stay deleted, and a device that connects gets what it's missing."""
import pytest

from jarvis import chatsync


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(chatsync, "PATH", tmp_path / "chats.json")
    monkeypatch.setattr(chatsync, "_db", {"chats": {}, "deleted": {}, "groups": [], "groups_at": 0})
    monkeypatch.setattr(chatsync, "KEEP_DELETED", 10 ** 12)   # the tests' tiny timestamps aren't "60 days ago"


def chat(cid, rev, *msgs, title="", group=None):
    return {"id": cid, "title": title, "group": group, "created": 1, "updated": rev, "rev": rev, "unread": True,
            "messages": [{"who": w, "text": t, "ts": ts} for ts, w, t in msgs]}


def test_a_chat_from_the_phone_reaches_the_computer():
    out = chatsync.push([chat("a", 10, (5, "me", "hi"), (6, "jarvis", "hello"))])
    assert [c["id"] for c in out["chats"]] == ["a"]
    assert "unread" not in out["chats"][0]   # what's read is up to each device
    got = chatsync.hello({})   # the computer connects with nothing
    assert [m["text"] for m in got["chats"][0]["messages"]] == ["hi", "hello"]


def test_messages_from_both_devices_are_kept():
    chatsync.push([chat("a", 10, (5, "me", "from the phone"), (6, "jarvis", "answer 1"))])
    out = chatsync.push([chat("a", 12, (5, "me", "from the phone"), (8, "me", "from the computer"))])
    texts = [m["text"] for m in out["chats"][0]["messages"]]
    assert texts == ["from the phone", "answer 1", "from the computer"]


def test_the_newest_title_wins():
    chatsync.push([chat("a", 10, (5, "me", "hi"), title="Old")])
    chatsync.push([chat("a", 20, (5, "me", "hi"), title="Renamed")])
    assert chatsync.push([chat("a", 15, (5, "me", "hi"), title="Stale")]) == {}   # nothing new: nothing passed on
    assert chatsync._db["chats"]["a"]["title"] == "Renamed"


def test_pictures_and_files_stay_on_their_device():
    out = chatsync.push([{"id": "a", "rev": 1, "messages": [{"who": "shots", "id": "x", "ts": 3},
                                                             {"who": "file", "fid": "f", "ts": 4},
                                                             {"who": "me", "text": "hi", "ts": 2}]}])
    assert [m["who"] for m in out["chats"][0]["messages"]] == ["me"]


def test_a_deleted_chat_stays_deleted():
    chatsync.push([chat("a", 10, (5, "me", "hi"))])
    out = chatsync.push([], deleted={"a": 50})
    assert out["deleted"] == {"a": 50}
    assert chatsync.push([chat("a", 10, (5, "me", "hi"))]) == {}   # an offline device's old copy doesn't bring it back
    assert chatsync.hello({"a": 10})["deleted"] == {"a": 50}
    assert chatsync.hello({"a": 10})["want"] == []


def test_a_chat_changed_after_it_was_deleted_comes_back():
    chatsync.push([], deleted={"a": 50})
    out = chatsync.push([chat("a", 60, (55, "me", "still here"))])
    assert out["chats"][0]["id"] == "a"


def test_connecting_gets_whats_missing_and_offers_whats_new():
    chatsync.push([chat("a", 10, (5, "me", "hi")), chat("b", 10, (5, "me", "yo"))])
    got = chatsync.hello({"a": 10, "b": 5, "c": 7})
    assert [c["id"] for c in got["chats"]] == ["b"]   # it has "a" already
    assert got["want"] == ["c"]                         # this PC hasn't seen "c"


def test_groups_follow_the_newest_change():
    out = chatsync.push([], groups=[{"id": "g", "name": "Work"}], groups_at=5)
    assert out["groups"] == [{"id": "g", "name": "Work"}]
    assert chatsync.push([], groups=[], groups_at=3) == {}
    assert chatsync.hello({}, groups_at=1)["groups"] == [{"id": "g", "name": "Work"}]
    assert "groups" not in chatsync.hello({}, groups_at=5)


def test_it_survives_a_restart(tmp_path):
    chatsync.push([chat("a", 10, (5, "me", "hi"))])
    import json
    assert json.loads(chatsync.PATH.read_text(encoding="utf-8"))["chats"]["a"]["messages"][0]["text"] == "hi"
