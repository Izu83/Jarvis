"""Picking the best and cheapest model for each command, and keeping the pay-per-use ones under budget."""
import json

import pytest

from jarvis import cloud, router, usage

ALL = {"claude", "gemini", "local", "chatgpt", "grok"}


@pytest.mark.parametrize("command, first", [
    ("open spotify and put it on the left monitor", "claude"),
    ("send this pdf to my email", "claude"),
    ("write a python script that renames my photos", "claude"),
    ("what's on my screen", "claude"),
    ("what's the latest news about the elections", "gemini"),
    ("how much is bitcoin right now", "gemini"),
    ("summarize this youtube video https://youtu.be/abc", "gemini"),
    ("what's trending on twitter", "grok"),
    ("tell me a joke", "gemini"),
    ("why is the sky blue", "gemini"),
])
def test_best_for_the_job(command, first):
    assert router.plan(command, ALL)[0] == first


def test_only_what_is_available():
    assert router.plan("what's trending on twitter", {"claude", "gemini"}) == ["gemini", "claude"]   # no Grok key
    assert router.plan("tell me a joke", {"claude"}) == ["claude"]
    assert router.plan("open chrome", {"local", "claude"}, local_first=True) == ["local", "claude"]
    assert router.plan("what's the latest news", {"local", "gemini"}, local_first=True)[0] == "gemini"   # no web locally


def test_budget(monkeypatch):
    monkeypatch.setitem(cloud.settings, "paid_budget", 1.0)
    monkeypatch.setitem(cloud.settings, "use_grok", True)
    monkeypatch.setattr(cloud, "secret", lambda name: "key" if name == "XAI_API_KEY" else "")
    monkeypatch.setattr(usage, "month_spend", lambda: .4)
    assert cloud.usable("grok") and not cloud.usable("chatgpt")   # no ChatGPT key
    monkeypatch.setattr(usage, "month_spend", lambda: 1.2)
    assert not cloud.usable("grok")                               # over budget: off until next month


def test_cloud_runs_tools_and_counts_the_cost(monkeypatch):
    replies = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "open_app", "arguments": json.dumps({"name": "Spotify"})}}]}}],
         "usage": {"prompt_tokens": 1000, "completion_tokens": 100}},
        {"choices": [{"message": {"content": "Opened Spotify."}}], "usage": {"prompt_tokens": 1200, "completion_tokens": 20}},
    ]
    sent, ran, spent = [], [], []
    monkeypatch.setattr(cloud, "secret", lambda name: "key")
    monkeypatch.setattr(cloud.requests, "post", lambda url, **kw: (sent.append(kw["json"]), type("R", (), {
        "status_code": 200, "text": "", "json": lambda self, r=replies.pop(0): r})())[1])
    monkeypatch.setattr(cloud.tools, "run", lambda name, args: ran.append((name, args)) or "Opened.", raising=False)
    monkeypatch.setattr(cloud.usage, "record_other", lambda p, i, o, c=0.0: spent.append(c))
    monkeypatch.setattr(cloud.memory, "context", lambda *a, **k: "")
    monkeypatch.setattr(cloud, "system_prompt", lambda: "You are Jarvis.")
    monkeypatch.setitem(cloud.settings, "chatgpt_price", [1.0, 10.0])
    monkeypatch.setattr("jarvis.hermes._ollama_tools", lambda: [])
    brain = cloud.CloudBrain("chatgpt")
    assert brain.handle("open spotify") == "Opened Spotify." and not brain.failed
    assert ran == [("open_app", {"name": "Spotify"})]
    assert sent[1]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "Opened."}
    assert spent == [pytest.approx(.002), pytest.approx(.0014)]   # 1000 in + 100 out at $1 / $10 per million


def test_gemini_searches_google_for_current_things(monkeypatch):
    sent = []
    reply = {"candidates": [{"content": {"parts": [{"text": "Bitcoin is at $100,000 right now."}]}}],
             "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 12}}
    monkeypatch.setattr(cloud, "secret", lambda name: "key")
    monkeypatch.setattr(cloud.requests, "post", lambda url, **kw: (sent.append((url, kw["json"])), type("R", (), {
        "status_code": 200, "text": "", "json": lambda self: reply})())[1])
    monkeypatch.setattr(cloud.usage, "record_other", lambda *a: None)
    monkeypatch.setattr(cloud.memory, "context", lambda *a, **k: "")
    monkeypatch.setattr(cloud, "system_prompt", lambda: "You are Jarvis.")
    brain = cloud.CloudBrain("gemini")
    assert brain.handle("how much is bitcoin right now", search=True) == "Bitcoin is at $100,000 right now."
    url, body = sent[0]
    assert url.endswith(f"/models/{brain.model}:generateContent") and body["tools"] == [{"google_search": {}}]
    assert not brain.failed


def test_newest_stable_flash(monkeypatch):
    ids = ["models/gemini-2.0-flash", "models/gemini-2.5-flash", "models/gemini-2.5-flash-lite", "models/gemini-3.0-flash",
           "models/gemini-3.5-flash-preview", "models/gemini-2.5-pro", "models/text-embedding-004"]
    monkeypatch.setattr(cloud.requests, "get", lambda url, **kw: type("R", (), {
        "json": lambda self: {"data": [{"id": i} for i in ids]}})())
    assert cloud.best_gemini_model("key") == "gemini-3.0-flash"
    assert cloud.usable("gemini") is False   # no key (the test config has none)
