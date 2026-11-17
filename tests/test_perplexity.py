"""Perplexity through its Agent API (cloud.py): its answer and sources, checking a key for free, and when it's asked."""
from jarvis import cloud, router


def test_the_answer_comes_with_its_sources():
    data = {"output": [
        {"type": "search_results", "results": [{"title": "BBC", "url": "https://bbc.co.uk/a"},
                                               {"title": "Reuters", "url": "https://reuters.com/b"},
                                               {"title": "BBC again", "url": "https://bbc.co.uk/a"}]},
        {"type": "message", "content": [{"type": "output_text", "text": "It rained in Sofia [web:1][web:2]."}]},
    ]}
    text, sources = cloud.perplexity_answer(data)
    assert text == "It rained in Sofia [1][2]."
    assert sources == [("BBC", "https://bbc.co.uk/a"), ("Reuters", "https://reuters.com/b")]   # each once


def test_citations_stand_in_when_there_are_no_search_results():
    data = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Yes [1].", "annotations": [
        {"type": "citation", "url": "https://example.com/x", "title": "Example"}]}]}]}
    assert cloud.perplexity_answer(data) == ("Yes [1].", [("Example", "https://example.com/x")])


class _Resp:
    def __init__(self, status):
        self.status_code = status


def test_a_key_is_checked_without_asking_anything(monkeypatch):
    """Perplexity has no read-only call to check a key: an empty request gets 401 for a wrong key and 400 for a good
    one, and nothing is answered or charged."""
    sent = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append((url, json))
        return _Resp(400 if headers["Authorization"].endswith("good") else 401)
    monkeypatch.setattr(cloud.requests, "post", post)
    assert cloud.check_key("perplexity", "pplx-good")
    assert not cloud.check_key("perplexity", "pplx-wrong")
    assert all(url.endswith("/v1/agent") and body == {} for url, body in sent)


def test_it_answers_current_questions_after_gemini():
    available = {"claude", "gemini", "perplexity"}
    assert router.plan("what's the latest news about the Champions League?", available)[:2] == ["gemini", "perplexity"]
    assert "perplexity" not in router.plan("open Spotify", available)   # it can't use the PC
    assert "perplexity" not in router.plan("tell me a joke", available)
