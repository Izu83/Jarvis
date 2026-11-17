"""DeepSeek through its own API (cloud.py): checking a key for free, thinking only where it helps, its thinking going
back with its tool calls, and when it's asked."""
from jarvis import cloud, router


class _Resp:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data or {}
        self.ok = status < 400

    def json(self):
        return self._data


def test_a_key_is_checked_with_its_free_model_list(monkeypatch):
    sent = []

    def get(url, headers=None, timeout=None):
        sent.append(url)
        return _Resp(200 if headers["Authorization"].endswith("good") else 401)
    monkeypatch.setattr(cloud.requests, "get", get)
    assert cloud.check_key("deepseek", "sk-good")
    assert not cloud.check_key("deepseek", "sk-wrong")
    assert sent == ["https://api.deepseek.com/models"] * 2


def test_it_thinks_for_code_and_pc_jobs_but_answers_chat_straight_away():
    assert cloud.extra_options("deepseek", "code") == {"thinking": {"type": "enabled"}}
    assert cloud.extra_options("deepseek", "action") == {"thinking": {"type": "enabled"}}
    assert cloud.extra_options("deepseek", "chat") == {"thinking": {"type": "disabled"}}
    assert cloud.extra_options("gemini", "chat") == {"reasoning_effort": "low"}
    assert cloud.extra_options("chatgpt", "code") == {}


def test_its_thinking_goes_back_with_its_tool_calls(monkeypatch):
    """DeepSeek refuses the next step of a job if the thinking behind a tool call isn't sent back with it."""
    monkeypatch.setattr(cloud, "secret", lambda name: "sk-test")
    monkeypatch.setattr(cloud.memory, "context", lambda *a, **k: "")
    monkeypatch.setattr(cloud.usage, "record_other", lambda *a, **k: None)
    monkeypatch.setattr(cloud.tools, "run", lambda name, args: "done")
    monkeypatch.setattr(cloud, "system_prompt", lambda: "You are Jarvis.")
    monkeypatch.setattr("jarvis.hermes._ollama_tools", lambda: [])
    monkeypatch.setitem(cloud.settings, "deepseek_model", "")
    replies = [
        {"choices": [{"message": {"content": "", "reasoning_content": "I should open it.",
                                  "tool_calls": [{"id": "1", "function": {"name": "open_app", "arguments": "{}"}}]}}]},
        {"choices": [{"message": {"content": "Opened."}}]},
    ]
    bodies = []

    def post(url, json=None, headers=None, timeout=None):
        bodies.append(json)
        return _Resp(200, replies[len(bodies) - 1])
    monkeypatch.setattr(cloud.requests, "post", post)
    brain = cloud.CloudBrain("deepseek")
    assert brain.handle("open notepad", kind="action") == "Opened."
    assert bodies[0]["model"] == "deepseek-flash" and bodies[0]["thinking"] == {"type": "enabled"}
    assistant = [m for m in bodies[1]["messages"] if m["role"] == "assistant"][0]
    assert assistant["reasoning_content"] == "I should open it."


def test_it_comes_after_the_free_ones_and_your_plan():
    available = {"claude", "gemini", "openrouter", "deepseek", "chatgpt"}
    assert router.plan("tell me a joke", available) == ["gemini", "openrouter", "claude", "deepseek", "chatgpt"]
    assert router.plan("fix this python bug", available)[:2] == ["claude", "deepseek"]   # before ChatGPT for code
    assert "deepseek" not in router.plan("what's the latest news?", available)   # it can't search the web


def test_it_is_paid_so_it_stays_within_the_budget(monkeypatch):
    monkeypatch.setattr(cloud, "secret", lambda name: "sk-test")
    monkeypatch.setitem(cloud.settings, "use_deepseek", True)
    assert "deepseek" not in cloud.FREE
    monkeypatch.setattr(cloud, "over_budget", lambda: True)
    assert not cloud.usable("deepseek")
    monkeypatch.setattr(cloud, "over_budget", lambda: False)
    assert cloud.usable("deepseek")
