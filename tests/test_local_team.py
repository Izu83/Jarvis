"""Hermes + Qwen3 working together, against a fake Ollama: who gets which command, and who takes over."""
import json

import pytest

from jarvis import hermes


class FakeOllama:
    """Answers /api/tags and /api/chat. `script[model]` is a list of replies (messages) handed out in order."""
    def __init__(self, installed, script):
        self.installed, self.script, self.chats = installed, script, []

    def get(self, url, timeout=None):
        return Resp({"models": [{"name": m, "model": m} for m in self.installed]})

    def post(self, url, json=None, timeout=None, **kw):
        assert url.endswith("/api/chat")
        self.chats.append((json["model"], json.get("think", False)))
        replies = self.script.get(json["model"]) or [{"role": "assistant", "content": ""}]
        return Resp({"message": dict(replies.pop(0))})


class Resp:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


@pytest.fixture
def team(monkeypatch):
    def make(installed, script):
        fake = FakeOllama(installed, script)
        monkeypatch.setattr(hermes.requests, "get", fake.get)
        monkeypatch.setattr(hermes.requests, "post", fake.post)
        monkeypatch.setattr(hermes.HermesBrain, "_prepare", lambda self: None)
        monkeypatch.setattr(hermes.tools, "environment_description", lambda: "", raising=False)
        monkeypatch.setattr(hermes.tools, "TOOLS", [], raising=False)
        hermes.settings.update({"ollama_host": "http://127.0.0.1:11434", "hermes_model": "hermes3:8b",
                                "qwen_model": "qwen3:8b", "hermes_fallback": "claude-code"})
        brain = hermes.HermesBrain()
        brain.claude_got = []
        monkeypatch.setattr(brain, "_to_claude", lambda c, *a: brain.claude_got.append(c) or "claude did it")
        steps = []
        return brain, fake, lambda cmd: brain.handle(cmd, progress=lambda t, k="": steps.append((k, t))), steps
    return make


def say(text):
    return {"role": "assistant", "content": text}


def call(tool, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": tool, "arguments": args}}]}


@pytest.mark.parametrize("text, expected", [
    ("open spotify", False), ("mute", False),
    ("open spotify and then play some jazz", True), ("why is my pc slow", True),
    ("organize my downloads folder by file type", True),
])
def test_complexity(text, expected):
    assert hermes.is_complex(text) is expected


def test_simple_command_goes_to_hermes(team):
    brain, fake, handle, steps = team(["hermes3:8b", "qwen3:8b"], {"hermes3:8b": [say("Opened Spotify.")]})
    assert handle("open spotify") == "Opened Spotify."
    assert fake.chats == [("hermes3:8b", False)]


def test_complex_command_goes_to_qwen_with_thinking(team, monkeypatch):
    monkeypatch.setattr(hermes.tools, "run", lambda name, args: "done", raising=False)
    brain, fake, handle, steps = team(["hermes3:8b", "qwen3:8b"], {"qwen3:8b": [
        {**call("open_app", name="Spotify"), "thinking": "First open Spotify, then play jazz."},
        call("play_music", query="jazz"), say("Opened Spotify and put on jazz.")]})
    assert handle("open spotify and then play some jazz") == "Opened Spotify and put on jazz."
    assert {m for m, _ in fake.chats} == {"qwen3:8b"} and all(t for _, t in fake.chats)
    assert "First open Spotify" in brain.last_thinking
    assert ("Brain", "Qwen3 is thinking it through…") in steps


def test_qwen_takes_over_when_hermes_is_stuck(team):
    brain, fake, handle, steps = team(["hermes3:8b", "qwen3:8b"], {
        "hermes3:8b": [say("")], "qwen3:8b": [say("<think>hmm</think>Muted.")]})
    assert handle("mute the pc") == "Muted."
    assert [m for m, _ in fake.chats] == ["hermes3:8b", "qwen3:8b"]
    assert ("Brain", "Hermes got stuck, so Qwen3 is taking over…") in steps
    assert "hmm" in brain.last_thinking and not brain.claude_got
    # the next command must not see the failed attempt, only the clean exchange
    assert [m["role"] for m in brain.history] == ["user", "assistant"]


def test_claude_when_both_are_stuck(team):
    brain, fake, handle, steps = team(["hermes3:8b", "qwen3:8b"], {"hermes3:8b": [say("")], "qwen3:8b": [say("")]})
    assert handle("mute the pc") == "claude did it"
    assert ("Brain", "Handing this one to Claude…") in steps


def test_vision_goes_straight_to_claude(team):
    brain, fake, handle, steps = team(["hermes3:8b", "qwen3:8b"], {})
    assert handle("what's on my screen") == "claude did it" and fake.chats == []


def test_qwen_not_downloaded_yet_hermes_tries(team):
    brain, fake, handle, steps = team(["hermes3:8b"], {"hermes3:8b": [say("Done both.")]})
    assert handle("open spotify and discord") == "Done both."
    assert fake.chats == [("hermes3:8b", False)]
