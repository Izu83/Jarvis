"""Short names for new chats (titles.py)."""
import pytest

from jarvis import titles


@pytest.mark.parametrize("said, title", [
    ("ChatGPT Go and Codex", "ChatGPT Go and Codex"),
    ('"Where the homework is saved."', "Where the homework is saved"),
    ("Title: Moving Zen to the main monitor", "Moving Zen to the main monitor"),
    ("**Домашно по МОП**\n", "Домашно по МОП"),
    ("\n\nзаглавие: къде е домашното", "Къде е домашното"),
    ("", ""),
    (None, ""),
    ("This is far too long to be a chat title because it just keeps going and going on", ""),
])
def test_clean(said, title):
    assert titles.clean(said) == title


def test_asks_a_free_model_and_cleans_its_answer(monkeypatch):
    seen = {}

    def quick(prompt, providers, max_tokens):
        seen.update(prompt=prompt, providers=providers)
        return "“ChatGPT Go and Codex.”"

    monkeypatch.setattr("jarvis.cloud.quick", quick)
    assert titles.title_for("so now i have ChatGPT Go", "Go includes Codex, with limits.") == "ChatGPT Go and Codex"
    assert seen["providers"] == ("groq",) and "so now i have ChatGPT Go" in seen["prompt"]


def test_no_model_no_name(monkeypatch):
    monkeypatch.setattr("jarvis.cloud.quick", lambda *a, **k: None)
    assert titles.title_for("hi", "hello") == ""
