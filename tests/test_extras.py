"""Resting a failing local model, the Siri endpoint's helpers, and the "webcam is back" watcher."""
import threading

from jarvis import camera, hermes, siri


def _team_brain():
    b = object.__new__(hermes.HermesBrain)   # no Ollama, no threads: just the bookkeeping
    b.team = {"hermes": ("hermes-test", "Hermes", False), "qwen": ("qwen-test", "Qwen3", True)}
    b._strikes = {}
    b.installed = {"hermes-test", "qwen-test"}
    b._checked = 1e18   # "just refreshed"
    return b


def test_a_model_that_keeps_failing_rests_then_comes_back(monkeypatch):
    b = _team_brain()
    hermes._resting.clear()
    b._strike("qwen")
    assert b._ready("qwen")                     # one failure: still tried
    b._strike("qwen")
    assert not b._ready("qwen")                 # two in a row: resting
    assert b._ready("hermes")
    assert b._plan("do a long multi-step thing and then another and another", followup=True) == ["hermes"]
    now = hermes.time.time()
    monkeypatch.setattr(hermes.time, "time", lambda: now + hermes.REST_SECONDS + 1)
    assert b._ready("qwen")                     # rested long enough
    hermes._resting.clear()


def test_a_success_clears_the_strikes():
    b = _team_brain()
    hermes._resting.clear()
    b._strike("hermes")
    b._strikes["hermes"] = 0                    # what _local does after an answer
    b._strike("hermes")
    assert b._ready("hermes")


def test_siri_key_check(monkeypatch):
    monkeypatch.setenv("JARVIS_SIRI_KEY", "s3cret")
    assert siri.authorised("s3cret") and siri.authorised(" s3cret ")
    assert not siri.authorised("nope") and not siri.authorised("")
    monkeypatch.setenv("JARVIS_SIRI_KEY", "")
    assert not siri.authorised("")              # no key set yet: nothing gets in


def test_siri_question_is_url_decoded():
    assert siri.question("what%27s%20the%20time%3F") == "what's the time?"
    assert siri.question("%D0%BA%D0%BE%D0%BB%D0%BA%D0%BE") == "колко"
    assert siri.question("") == ""
    assert siri.address_url("https://ntfy.sh/", "jarvis-abc") == "https://ntfy.sh/jarvis-abc-s/raw?poll=1&since=latest"


def test_webcam_back_message_fires_once(monkeypatch):
    seen = iter([False, False, True])
    monkeypatch.setattr(camera, "present", lambda: next(seen))
    called = threading.Event()
    camera.tell_when_back(called.set, every=0.01)
    camera.tell_when_back(lambda: (_ for _ in ()).throw(AssertionError("watching twice")), every=0.01)
    assert called.wait(2)
