"""The shared memory (conversation + saved facts) and sending new emails."""
import types

import pytest

from jarvis import brain, mail, memory


@pytest.fixture(autouse=True)
def fresh_memory(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "CONV_PATH", tmp_path / "conversation.json")
    monkeypatch.setattr(memory, "FACTS_PATH", tmp_path / "memory.json")
    monkeypatch.setattr(memory, "_conv", {})


def test_conversation_is_kept_per_chat_and_survives_a_restart(monkeypatch):
    memory.record("chat1", "open spotify", "Opened Spotify.", ["Opening Spotify…", "Working…"])
    memory.record("chat2", "what time is it", "It's 9.")
    memory.record("chat1", "play jazz", "Playing jazz.")
    assert [e["user"] for e in memory.recent("chat1")] == ["open spotify", "play jazz"]
    assert memory.recent("chat1")[0]["did"] == ["Opening Spotify"]
    monkeypatch.setattr(memory, "_conv", memory._read(memory.CONV_PATH, {}))   # as if Jarvis restarted
    assert [e["user"] for e in memory.recent("chat2")] == ["what time is it"]


def test_old_conversation_is_left_out():
    memory.record(None, "open spotify", "Opened Spotify.", started=1.0)   # long ago
    assert memory.background(None) == ""


def test_background_fresh_and_since():
    memory.remember("The user likes lo-fi music")
    memory.record(None, "open spotify", "Opened Spotify.", started=100.0 + memory.time.time() - 200)
    t = memory.time.time() - 50
    memory.record(None, "mute", "Muted.")
    fresh = memory.background(None)
    assert "lo-fi" in fresh and "open spotify" in fresh and "mute" in fresh
    later = memory.background(None, since=t, fresh=False)
    assert "mute" in later and "open spotify" not in later and "lo-fi" not in later
    assert memory.background(None, since=memory.time.time() + 1, fresh=False) == ""


def test_phone_history_is_used_when_the_pc_has_none():
    note = memory.background("new-chat", phone_recent=[{"who": "me", "text": "find my cv"},
                                                       {"who": "jarvis", "text": "Found cv.pdf in Documents."}])
    assert "User: find my cv" in note and "Jarvis: Found cv.pdf" in note


def test_remember_forget_and_lookup():
    assert memory.remember("Ana is the user's sister; her email is ana@example.com.").startswith("Remembered")
    assert memory.remember("ana is the user's sister; her email is ana@example.com.").startswith("I already know")
    memory.remember("Boris works with the user; boris@work.com")
    assert memory.lookup_address("Ana") == "ana@example.com"
    assert memory.lookup_address("my sister Ana") == "ana@example.com"
    assert memory.lookup_address("Carl") is None
    assert "Ana" in memory.forget("ana email")
    assert memory.lookup_address("Ana") is None and memory.lookup_address("boris") == "boris@work.com"
    assert memory.forget("nobody").startswith("I didn't have")


def test_claude_session_gets_what_it_missed():
    """A fresh Claude session gets everything; a running one only what the local brains did since."""
    me = types.SimpleNamespace(seen=0.0, facts_seen=0.0, thread=None)
    with_memory = lambda cmd, fresh: brain.ClaudeCodeBrain._with_memory(me, cmd, None, None, fresh)
    memory.record(None, "open spotify", "Opened Spotify.")
    first = with_memory("play jazz", fresh=True)
    assert "open spotify" in first and first.endswith("The user's new message:\nplay jazz")
    me.seen = memory.time.time()
    assert with_memory("louder", fresh=False) == "louder"   # nothing new
    memory.record(None, "mute", "Muted.")                 # handled by Hermes meanwhile
    note = with_memory("unmute", fresh=False)
    assert "mute" in note and "open spotify" not in note
    memory.remember("The user's name is Nikolay")         # a new fact is passed on once
    assert "Nikolay" in with_memory("hi", fresh=False)


def test_resolve_recipients(monkeypatch):
    assert mail.resolve("ana@example.com") == ("ana@example.com", False)
    assert mail.resolve("Ana", "ana@example.com") == ("ana@example.com", False)
    monkeypatch.setattr(mail, "find_address", lambda name: ["boris@work.com"])
    assert mail.resolve("Boris") == ("boris@work.com", True)
    monkeypatch.setattr(mail, "find_address", lambda name: ["a@x.com", "b@y.com"])
    with pytest.raises(mail.NoAddress, match="More than one"):
        mail.resolve("Ivan")
    monkeypatch.setattr(mail, "find_address", lambda name: [])
    with pytest.raises(mail.NoAddress, match="Ask the user"):
        mail.resolve("Ivan")


def test_build_new_email_with_attachment(monkeypatch, tmp_path):
    monkeypatch.setattr(mail, "_account", lambda: ("me@gmail.com", "pw"))
    doc = tmp_path / "cv.pdf"
    doc.write_bytes(b"%PDF-1.4 hi")
    msg = mail.build_new(["ana@example.com"], "My CV", "Hi Ana,\nhere it is.", cc=["b@y.com"], attachments=[doc])
    assert msg["From"] == "me@gmail.com" and msg["To"] == "ana@example.com" and msg["Cc"] == "b@y.com"
    assert msg["Subject"] == "My CV"
    parts = list(msg.iter_attachments())
    assert [p.get_filename() for p in parts] == ["cv.pdf"] and parts[0].get_content_type() == "application/pdf"
    assert "here it is." in msg.get_body(("plain",)).get_content()
