"""The desktop app and the model picker."""
from jarvis import brain, desktop, remote


def test_desktop_page_talks_to_jarvis_locally(monkeypatch):
    monkeypatch.setattr(remote, "ensure_keys", lambda: ("topic1", b"\x01" * 32))
    monkeypatch.setitem(desktop.settings, "phone_port", 8787)
    monkeypatch.setitem(desktop.settings, "ntfy_server", "https://ntfy.sh")
    url = desktop.page_url()
    assert url.startswith("http://127.0.0.1:8788/index.html#t=topic1&k=")   # the secrets stay in the #fragment
    assert url.endswith("&u=http://127.0.0.1:8787/desktop")                 # straight to Jarvis, not the tunnel


def test_model_picker(monkeypatch):
    saved = {}
    monkeypatch.setattr(remote, "set_setting", lambda k, v: saved.__setitem__(k, v))
    phone = remote.PhoneChannel.__new__(remote.PhoneChannel)
    phone.send_state = lambda: None
    for key, value in (("claude_code_model", "opus"), ("claude_code_effort", "high"),
                       ("claude_code_model", "gpt-9"), ("claude_code_effort", "max")):   # the last two aren't choices
        phone._handle({"type": "set", "key": key, "value": value, "id": "x"})
    assert saved == {"claude_code_model": "opus", "claude_code_effort": "high"}


def test_a_new_model_means_a_new_session(monkeypatch):
    monkeypatch.setitem(brain.settings, "claude_code_model", "sonnet")
    monkeypatch.setitem(brain.settings, "claude_code_effort", "medium")
    before = brain._wanted()
    monkeypatch.setitem(brain.settings, "claude_code_model", "haiku")
    assert brain._wanted() != before == ("sonnet", "medium")
