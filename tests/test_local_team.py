"""Hermes + Qwen3 working together, against a fake Ollama: who gets which command, and who takes over."""
import json

import pytest

from jarvis import hermes


class FakeOllama:
    """Answers /api/tags and /api/chat. `script[model]` is a list of replies (messages) handed out in order."""
    def __init__(self, installed, script):
        self.installed, self.script, self.chats, self.bodies = installed, script, [], []

    def get(self, url, timeout=None):
        return Resp({"models": [{"name": m, "model": m} for m in self.installed]})

    def post(self, url, json=None, timeout=None, **kw):
        assert url.endswith("/api/chat")
        self.chats.append((json["model"], json.get("think", False)))
        self.bodies.append(json)
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
                                "qwen_model": "qwen3:8b", "hermes_fallback": "claude-code", "local_first": True})
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
    # Qwen3 starts clean: it doesn't see Hermes's failed attempt
    assert [m["role"] for m in fake.bodies[1]["messages"]] == ["system", "user"]


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


def test_local_status(monkeypatch):
    """Settings' "Local brain" rows: loaded in the GPU, installed, missing, or Ollama not running."""
    for k, v in {"brain": "hermes", "hermes_model": "hermes3:8b", "qwen_model": "qwen3:8b"}.items():
        monkeypatch.setitem(hermes.settings, k, v)
    answers = {"/api/tags": ["hermes3:8b"], "/api/ps": ["hermes3:8b"]}
    monkeypatch.setattr(hermes.requests, "get", lambda url, timeout=None:
                        Resp({"models": [{"name": m} for m in answers[url[url.index("/api"):]]]}))
    monkeypatch.setattr(hermes, "_status_cache", (0.0, []))
    assert [m["state"] for m in hermes.local_status()] == ["loaded", "missing"]

    answers["/api/tags"].append("qwen3:8b")
    answers["/api/ps"].clear()
    monkeypatch.setattr(hermes, "_status_cache", (0.0, []))
    assert [m["state"] for m in hermes.local_status()] == ["ready", "ready"]

    def down(url, timeout=None):
        raise hermes.requests.ConnectionError("refused")
    monkeypatch.setattr(hermes.requests, "get", down)
    monkeypatch.setattr(hermes, "_status_cache", (0.0, []))
    assert [m["state"] for m in hermes.local_status()] == ["offline", "offline"]

    monkeypatch.setitem(hermes.settings, "brain", "claude-code")
    monkeypatch.setattr(hermes, "_status_cache", (0.0, []))
    assert [m["state"] for m in hermes.local_status()] == ["off", "off"]


def test_promise_without_acting_is_handed_over(team):
    """ "Sure, I will send you the video" with no tool run did nothing: Qwen3 gets a go instead."""
    brain, fake, handle, steps = team(["hermes3:8b", "qwen3:8b"], {
        "hermes3:8b": [say("Sure, I will send you the video.")], "qwen3:8b": [say("Muted.")]})
    assert handle("mute the pc") == "Muted."


def test_giving_up_after_a_failed_tool_is_handed_over(team, monkeypatch):
    monkeypatch.setattr(hermes.tools, "run", lambda name, args: "Error: path\to\video.mp4 isn't a file.", raising=False)
    brain, fake, handle, steps = team(["hermes3:8b"], {"hermes3:8b": [
        call("send_file_to_phone", path="path/to/video.mp4"),
        say("Apologies for the confusion. Could you please provide the correct file path?")]})
    assert handle("send the clip") == "claude did it"


def test_local_brain_is_told_the_last_file_sent(team, monkeypatch, tmp_path):
    clip = tmp_path / "site.webm"
    clip.write_bytes(b"v")
    monkeypatch.setattr(hermes.outbox, "last_sent", lambda: str(clip))
    brain, fake, handle, steps = team(["hermes3:8b"], {"hermes3:8b": [say("Sent it.")]})
    seen = []
    real_post = hermes.requests.post
    monkeypatch.setattr(hermes.requests, "post", lambda url, json=None, **kw: seen.append(json) or real_post(url, json=json, **kw))
    handle("forward that recording")
    assert str(clip) in seen[0]["messages"][-1]["content"]


def test_local_brain_sees_the_shared_conversation(team, monkeypatch, tmp_path):
    """A follow-up ("close it") reaches Hermes with the earlier exchange, even one another brain handled."""
    from jarvis import memory
    monkeypatch.setattr(memory, "CONV_PATH", tmp_path / "conv.json")
    monkeypatch.setattr(memory, "FACTS_PATH", tmp_path / "facts.json")
    monkeypatch.setattr(memory, "ABOUT_PATH", tmp_path / "about_me.txt")
    monkeypatch.setattr(memory, "_conv", {})
    memory.record(None, "open spotify", "Opened Spotify.", ["Opening Spotify…"])
    memory.remember("The user's sister is Ana; her email is ana@example.com")
    brain, fake, handle, steps = team(["hermes3:8b"], {"hermes3:8b": [say("Closed Spotify.")]})
    assert handle("close it") == "Closed Spotify."
    system, *rest = fake.bodies[0]["messages"]
    assert "User (just now): open spotify" in system["content"] and "Jarvis did: Opening Spotify" in system["content"]
    assert "ana@example.com" in system["content"]
    assert [m["content"] for m in rest] == ["close it"]   # the new request is the only user message


@pytest.mark.parametrize("text, followup", [
    ("close it", True), ("do that again", True), ("send it to her", True), ("what's my sister's email?", True),
    ("open spotify", False), ("mute", False), ("open discord", False),
])
def test_follow_ups_go_to_qwen_when_there_is_a_conversation(team, monkeypatch, text, followup):
    monkeypatch.setattr(hermes.memory, "background", lambda *a, **k: "The conversation so far: ...")
    brain, fake, handle, steps = team(["hermes3:8b", "qwen3:8b"], {"hermes3:8b": [say("Done.")],
                                                                   "qwen3:8b": [say("Done.")]})
    handle(text)
    assert fake.chats[0][0] == ("qwen3:8b" if followup else "hermes3:8b")


def test_no_conversation_means_hermes_for_short_commands(team, monkeypatch):
    monkeypatch.setattr(hermes.memory, "background", lambda *a, **k: "")
    brain, fake, handle, steps = team(["hermes3:8b", "qwen3:8b"], {"hermes3:8b": [say("Closed it.")]})
    assert handle("close it") == "Closed it." and fake.chats[0][0] == "hermes3:8b"


def test_tool_call_written_as_text_is_run(team, monkeypatch):
    ran = []
    monkeypatch.setattr(hermes.tools, "run", lambda name, args: ran.append((name, args)) or "Closed.", raising=False)
    monkeypatch.setattr(hermes.memory, "background", lambda *a, **k: "")
    brain, fake, handle, steps = team(["hermes3:8b"], {"hermes3:8b": [
        say('{\n  "arguments": {"action": "close", "window": "Spotify"},\n  "name": "window_action"\n}'),
        say("Closed Spotify. Is there anything else you would like me to do?")]})
    monkeypatch.setattr(hermes.tools, "TOOLS", [{"name": "window_action"}], raising=False)
    assert handle("close spotify") == "Closed Spotify."
    assert ran == [("window_action", {"action": "close", "window": "Spotify"})]


def test_claude_first_by_default(team, monkeypatch):
    """Claude does the command; the local team isn't even asked."""
    import types
    brain, fake, handle, steps = team(["hermes3:8b", "qwen3:8b"], {"hermes3:8b": [say("local")]})
    monkeypatch.setitem(hermes.settings, "local_first", False)
    monkeypatch.setattr(brain, "_claude", lambda: types.SimpleNamespace(failed=False))
    assert handle("open spotify and play jazz") == "claude did it" and fake.chats == []


def test_local_team_steps_in_when_claude_cant_answer(team, monkeypatch):
    import types
    brain, fake, handle, steps = team(["hermes3:8b", "qwen3:8b"], {"hermes3:8b": [say("Muted.")]})
    monkeypatch.setitem(hermes.settings, "local_first", False)
    monkeypatch.setattr(brain, "_claude", lambda: types.SimpleNamespace(failed=True))
    assert handle("mute") == "Muted."
    assert ("Brain", "Claude can't answer right now, so the local brain is trying…") in steps
