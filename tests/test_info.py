"""/info: one short, free page about Jarvis right now (info.py)."""
import time

from jarvis import info
from jarvis.config import settings


class FakePhone:
    def __init__(self, clients, desktops, seen=0.0):
        self.clients, self.desktops, self.phone_seen = clients, desktops, seen


def test_lists_the_connection_and_every_part(monkeypatch):
    monkeypatch.setattr("jarvis.tunnel.last_url", lambda: "https://abc-def.trycloudflare.com")
    text = info.safe_text(FakePhone(["p", "d"], ["d"]), "2JNIv1")
    for part in ("**Jarvis:**", "app 2JNIv1", "abc-def.trycloudflare.com", "1 phone and the desktop app",
                 "**Jarvis's share of the PC:**", "**This PC:**", "**Listening:**", "**AIs:**", "**Usage:**"):
        assert part in text, part


def test_says_when_the_phone_was_last_seen(monkeypatch):
    monkeypatch.setattr("jarvis.tunnel.last_url", lambda: None)
    text = info.safe_text(FakePhone(["d"], ["d"], seen=time.time() - 600), "")
    assert "No tunnel yet" in text and "phone last seen 10 min ago" in text


def test_listening_off(monkeypatch):
    monkeypatch.setitem(settings, "listening", False)
    monkeypatch.setitem(settings, "voice_enabled", True)
    assert "off (Ctrl+Alt+J still works)" in info._listening()


def test_never_raises(monkeypatch):
    def boom(*a):
        raise RuntimeError("x")
    monkeypatch.setattr(info, "text", boom)
    assert info.safe_text().startswith("Couldn't put the info together")
