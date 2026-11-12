"""Waking your other computer (wake.py)."""
from jarvis import wake


def test_the_wake_up_packet():
    data = wake.packet("D4:5D:64:B2:94:A4")
    assert len(data) == 102 and data[:6] == b"\xff" * 6 and data[6:12] == bytes.fromhex("d45d64b294a4") * 1
    assert wake.broadcast_of("192.168.1.20", "255.255.255.0") == "192.168.1.255"
    assert wake.broadcast_of("10.0.5.9", "255.255.0.0") == "10.0.255.255"


def test_learning_and_waking_the_other_computers(monkeypatch):
    saved, sent = {}, []
    monkeypatch.setattr(wake, "set_setting", lambda k, v: saved.__setitem__(k, v))
    monkeypatch.setitem(wake.settings, "peers", {})
    monkeypatch.setattr(wake, "send", lambda mac, b="": sent.append((mac, b)))
    assert wake.learn([{"name": " Gaming  PC ", "mac": "d4-5d-64-b2-94-a4", "broadcast": "192.168.1.255"},
                       {"name": "bad", "mac": "nope"}, "junk"])
    assert saved["peers"] == {"Gaming PC": {"mac": "D4-5D-64-B2-94-A4", "broadcast": "192.168.1.255", "wired": True}}
    monkeypatch.setitem(wake.settings, "peers", saved["peers"])
    assert not wake.learn([{"name": "Gaming PC", "mac": "d4-5d-64-b2-94-a4", "broadcast": "192.168.1.255"}])
    assert wake.wake_by_name("").startswith("Sent Gaming PC")             # the only one
    assert wake.wake_by_name("my gaming pc").startswith("Sent")           # one other computer: that one
    assert wake.wake_by_name("gaming").startswith("Sent Gaming PC")
    assert sent[0] == ("D4-5D-64-B2-94-A4", "192.168.1.255")
    monkeypatch.setitem(wake.settings, "peers", {})
    assert "don't know" in wake.wake_by_name("PC")

