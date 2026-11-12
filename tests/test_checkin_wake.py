"""Check-ins (checkin.py) and waking your other computer (wake.py)."""
import time

from jarvis import checkin, wake


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


def _quiet_pc(monkeypatch, level="often"):
    from jarvis import away, nap, proactive
    monkeypatch.setitem(checkin.settings, "check_ins", level)
    monkeypatch.setattr(proactive, "level", lambda: "normal")
    monkeypatch.setattr(proactive, "quiet", lambda now=None: False)
    monkeypatch.setattr(nap, "is_on", lambda: False)
    monkeypatch.setattr(away, "is_on", lambda: False)
    monkeypatch.setattr(checkin, "fullscreen", lambda: False)
    monkeypatch.setattr(checkin, "idle_seconds", lambda: 10)
    monkeypatch.setattr(checkin, "compose", lambda at_pc: "What are you up to?")
    monkeypatch.setattr(checkin, "_state", {"last": 0.0, "next": 0.0, "day": "", "count": 0, "unanswered": 0,
                                            "heard": 0.0, "recent": []})


def test_he_checks_in_now_and_then(monkeypatch):
    _quiet_pc(monkeypatch)
    sent = []
    now = time.time()
    assert checkin.run(sent.append) is None            # just started: not at once
    checkin._state["next"] = now - 1
    assert checkin.run(sent.append) == "What are you up to?" and sent == ["What are you up to?"]
    assert checkin.run(sent.append) is None            # not again straight away
    assert 60 * 60 <= checkin._state["next"] - now <= 2 * 60 * 60 + 5


def test_he_leaves_you_alone(monkeypatch):
    _quiet_pc(monkeypatch)
    checkin._state["next"] = time.time() - 1
    checkin.heard()                                    # you're talking to him already
    assert not checkin.due()
    checkin._state["heard"] = 0.0
    checkin._state["unanswered"] = 2                   # two unanswered: wait until you say something
    assert not checkin.due()
    checkin.heard()
    checkin._state["heard"] = 0.0
    assert checkin.due()
    monkeypatch.setattr(checkin, "fullscreen", lambda: True)   # a game or film
    assert not checkin.due()
    monkeypatch.setitem(checkin.settings, "check_ins", "off")
    monkeypatch.setattr(checkin, "fullscreen", lambda: False)
    assert not checkin.due()


def test_his_own_words_when_no_model_is_set_up(monkeypatch):
    monkeypatch.setattr(checkin, "on_screen", lambda: 'code: "main.py - Visual Studio Code"')
    monkeypatch.setattr(checkin, "_state", {**checkin._state, "recent": []})
    for _ in range(20):
        text = checkin._own(True)
        assert text and "{app}" not in text
