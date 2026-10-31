"""When Jarvis asks before doing something, and how an Allow from the desktop app is checked."""
from jarvis import approval, remote


def test_messages_and_emails_never_ask(monkeypatch):
    monkeypatch.setitem(approval.settings, "ask_permission", True)
    monkeypatch.setitem(approval.settings, "autonomy", "ask")
    assert approval.automatic("send")
    assert approval.automatic("power") == "" and approval.automatic("delete") == ""   # the rest still asks


def test_asking_switched_off(monkeypatch):
    monkeypatch.setitem(approval.settings, "ask_permission", False)
    for risk in ("power", "cmd", "delete", "change"):
        assert approval.automatic(risk)


def test_desktop_allow_is_checked_by_windows(monkeypatch):
    from jarvis import winverify
    checked = []
    monkeypatch.setattr(winverify, "verify", lambda reason: checked.append(reason) or True)
    monkeypatch.setattr(remote.faceid, "verify", lambda w, ch: False)   # a phone without Face ID is refused
    assert remote.PhoneChannel._its_you({"_desktop": True, "webauthn": {"pc": True}}, "ch", "Allow Jarvis to: x")
    assert checked == ["Allow Jarvis to: x"]
    assert not remote.PhoneChannel._its_you({"webauthn": {"pc": True}}, "ch", "y")   # a phone can't skip Face ID
