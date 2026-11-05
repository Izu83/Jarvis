"""The desktop app and the model picker."""
from jarvis import brain, desktop, remote


def test_desktop_page_talks_to_jarvis_locally(monkeypatch):
    monkeypatch.setattr(remote, "ensure_keys", lambda: ("topic1", b"\x01" * 32))
    monkeypatch.setitem(desktop.settings, "phone_port", 8787)
    monkeypatch.setitem(desktop.settings, "ntfy_server", "https://ntfy.sh")
    url = desktop.page_url()
    assert url.startswith("http://127.0.0.1:8788/index.html#t=topic1&k=")   # the secrets stay in the #fragment
    assert url.endswith("&u=http://127.0.0.1:8787/desktop")                 # straight to Jarvis, not the tunnel
    assert "&n=PC&d=desktop" in url   # which computer, for the device switcher


def test_pairing_link_names_the_computer(monkeypatch):
    monkeypatch.setattr(remote, "ensure_keys", lambda: ("topic1", b"\x01" * 32))
    monkeypatch.setattr(remote, "last_url", lambda: "")
    monkeypatch.setitem(remote.settings, "phone_page_url", "https://example.github.io/Jarvis/")
    monkeypatch.setitem(remote.settings, "ntfy_server", "https://ntfy.sh")
    monkeypatch.setattr(remote.pc, "device_name", lambda: "My Laptop")
    monkeypatch.setattr(remote.pc, "kind", lambda: "laptop")
    assert remote.pairing_link().endswith("&n=My%20Laptop&d=laptop")


def test_connections_say_what_they_are():
    label = remote.PhoneChannel._client_label
    assert label({"name": "  Laptop ", "kind": "computer"}) == {"name": "Laptop", "kind": "computer"}
    assert label({"name": "iPhone", "kind": "phone"}) == {"name": "iPhone", "kind": "phone"}
    assert label({"kind": "toaster"}) == {"name": "A phone", "kind": "phone"}   # unknown kinds count as a phone
    assert len(label({"name": "x" * 99, "kind": "tablet"})["name"]) == 24


def test_renaming_the_computer_from_the_phone(monkeypatch):
    saved = {}
    monkeypatch.setattr(remote, "set_setting", lambda k, v: saved.__setitem__(k, v))
    phone = remote.PhoneChannel.__new__(remote.PhoneChannel)
    phone.send_state = lambda: None
    phone._handle({"type": "set", "key": "device_name", "value": "  Gaming PC  ", "id": "x"})
    assert saved == {"device_name": "Gaming PC"}


def test_model_picker(monkeypatch):
    saved = {}
    monkeypatch.setattr(remote, "set_setting", lambda k, v: saved.__setitem__(k, v))
    phone = remote.PhoneChannel.__new__(remote.PhoneChannel)
    phone.send_state = lambda: None
    for key, value in (("claude_code_model", "opus"), ("claude_code_effort", "high"), ("use_qwen", False),
                       ("claude_code_model", "gpt-9"), ("claude_code_effort", "max")):   # the last two aren't choices
        phone._handle({"type": "set", "key": key, "value": value, "id": "x"})
    assert saved == {"claude_code_model": "opus", "claude_code_effort": "high", "use_qwen": False}


def test_a_new_model_means_a_new_session(monkeypatch):
    monkeypatch.setitem(brain.settings, "claude_code_model", "sonnet")
    monkeypatch.setitem(brain.settings, "claude_code_effort", "medium")
    before = brain._wanted()
    monkeypatch.setitem(brain.settings, "claude_code_model", "haiku")
    assert brain._wanted() != before == ("sonnet", "medium")
