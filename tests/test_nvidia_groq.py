"""NVIDIA and Groq (free, cloud.py), Gemini 4 getting the time it needs, and an AI's details page."""
import time

import pytest

from jarvis import cloud, router


class _Resp:
    def __init__(self, status, data=None, text="", headers=None):
        self.status_code, self._data, self.text = status, data or {}, text
        self.headers = headers or {}
        self.ok = status < 400

    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for d in (cloud._ladders, cloud._resting, cloud._provider_resting, cloud._good, cloud._choices, cloud._limits):
        d.clear()
    monkeypatch.setattr(cloud, "secret", lambda name: "key")


def test_nvidias_ladder_is_its_best_chat_models_in_order(monkeypatch):
    ids = ["nvidia/nemotron-3-embed-1b", "google/gemma-4-31b-it", "z-ai/glm-5.3-flash", "nvidia/nvclip",
           "mistralai/mistral-large-4-1t-a52b", "nvidia/nemotron-3-ultra-550b-a55b", "nvidia/nemotron-3.5-content-safety"]
    monkeypatch.setattr(cloud.requests, "get", lambda url, headers=None, timeout=None: _Resp(200, {"data": [{"id": i} for i in ids]}))
    assert cloud.ladder("nvidia", "key") == ["mistralai/mistral-large-4-1t-a52b", "nvidia/nemotron-3-ultra-550b-a55b",
                                             "z-ai/glm-5.3-flash", "google/gemma-4-31b-it"]


def test_groqs_ladder_needs_the_key_and_skips_speech_models(monkeypatch):
    seen = {}

    def get(url, headers=None, timeout=None):
        seen["auth"] = (headers or {}).get("Authorization")
        return _Resp(200, {"data": [{"id": "whisper-large-v3"}, {"id": "openai/gpt-oss-20b"}, {"id": "openai/gpt-oss-120b"},
                                    {"id": "qwen/qwen3.8-27b"}, {"id": "meta-llama/llama-prompt-guard-2-86m"}]})
    monkeypatch.setattr(cloud.requests, "get", get)
    assert cloud.ladder("groq", "gsk") == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
    assert seen["auth"] == "Bearer gsk"


def test_an_nvidia_key_is_checked_with_one_word(monkeypatch):
    monkeypatch.setattr(cloud.requests, "post", lambda url, json=None, headers=None, timeout=None:
                        _Resp(200 if headers["Authorization"].endswith("good") else 403))
    assert cloud.check_key("nvidia", "nvapi-good")
    assert not cloud.check_key("nvidia", "nvapi-wrong")


def test_a_groq_model_out_of_todays_requests_rests_until_tomorrow():
    r = _Resp(429, text='{"error":{"message":"Rate limit reached ... on requests per day (RPD): Limit 1000, Used 1000"}}')
    cloud._rest("groq", "openai/gpt-oss-120b", r)
    assert cloud._resting["groq:openai/gpt-oss-120b"] - time.time() > 60   # not the 2-minute rest of a busy one
    r = _Resp(429, text="busy", headers={"retry-after": "7"})
    cloud._rest("groq", "qwen/qwen3.8-27b", r)
    assert 25 <= cloud._resting["groq:qwen/qwen3.8-27b"] - time.time() <= 31


def test_a_model_that_cant_use_tools_hands_over_to_the_next_one(monkeypatch):
    monkeypatch.setattr(cloud, "ladder", lambda provider, key: ["a/one", "b/two"])
    brain = cloud.CloudBrain("nvidia")
    replies = iter([_Resp(400, text="tools not supported"), _Resp(200, {"ok": 1})])
    r = brain._post(lambda model, timeout: next(replies), deadline=time.time() + 30, per_model=10)
    assert r.status_code == 200 and brain.model == "b/two"


def test_gemini_4_gets_the_time_it_needs(monkeypatch):
    monkeypatch.setitem(cloud.settings, "gemini_model", "gemini-4-hydrogen")
    monkeypatch.setattr(cloud, "ladder", lambda provider, key: ["gemini-3.8-flash"])
    brain = cloud.CloudBrain("gemini")
    asked = []

    def call(model, timeout):
        asked.append((model, timeout[1]))
        return _Resp(200, {})
    brain._post(call, deadline=time.time() + cloud.FIRST_ANSWER, per_model=cloud.PER_MODEL)
    model, allowed = asked[0]
    assert model == "gemini-4-hydrogen" and allowed > 50   # not the 18 s a Flash model gets


def test_the_free_ones_go_where_they_are_best():
    have = {"gemini", "groq", "nvidia", "claude", "openrouter"}
    assert router.plan("tell me a joke", have)[:3] == ["gemini", "groq", "nvidia"]
    assert router.plan("fix this python bug", have)[:2] == ["claude", "nvidia"]


def test_the_details_page_lists_the_models_and_whats_resting(monkeypatch):
    monkeypatch.setattr(cloud, "ladder", lambda provider, key: ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"])
    cloud._ladders["groq"] = (time.time(), ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"])
    cloud._resting["groq:qwen/qwen3.8-27b"] = time.time() + 600
    cloud.limits_seen("groq", "openai/gpt-oss-120b", _Resp(200, headers={"x-ratelimit-remaining-requests": "987",
                                                                          "x-ratelimit-limit-requests": "1000", "other": "x"}))
    i = cloud.info("groq")
    assert [m["id"] for m in i["models"]] == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
    assert i["models"][1]["resting"] > time.time() and i["models"][0]["resting"] == 0
    assert i["live"]["x-ratelimit-remaining-requests"] == "987" and "other" not in i["live"]
    assert i["free"] and "1,000 a day" in i["limits"]


def test_a_conversation_googles_filter_refuses_is_left_out_and_asked_again(monkeypatch):
    """Friends swearing at Jarvis through the PC's microphone made Google refuse every request that carried that
    conversation, so Gemini said nothing at all. Now it asks once more with only the facts."""
    monkeypatch.setattr(cloud, "ladder", lambda provider, key: ["gemini-3.8-flash"])
    monkeypatch.setattr(cloud.memory, "context", lambda *a, conversation=True, **k: "[talk]\n" if conversation else "[facts]\n")
    monkeypatch.setattr(cloud.usage, "record_other", lambda *a, **k: None)
    monkeypatch.setattr(cloud.usage, "record_answer", lambda *a, **k: None)
    monkeypatch.setattr(cloud, "system_prompt", lambda: "sys")
    monkeypatch.setattr("jarvis.hermes._ollama_tools", lambda: [])
    sent = []
    replies = iter([_Resp(200, {"choices": [{"finish_reason": "content_filter: PROHIBITED_CONTENT"}]}),
                    _Resp(200, {"choices": [{"message": {"content": "Hi!"}}]})])

    def post(url, json=None, headers=None, timeout=None):
        sent.append(json["messages"][1]["content"])
        return next(replies)
    monkeypatch.setattr(cloud.requests, "post", post)
    brain = cloud.CloudBrain("gemini")
    assert brain.handle("hello") == "Hi!" and not brain.failed
    assert sent == ["[talk]\nhello", "[facts]\nhello"]


def test_refused_twice_hands_over_to_the_next_brain(monkeypatch):
    monkeypatch.setattr(cloud, "ladder", lambda provider, key: ["gemini-3.8-flash"])
    monkeypatch.setattr(cloud.memory, "context", lambda *a, conversation=True, **k: "[talk]\n" if conversation else "[facts]\n")
    monkeypatch.setattr(cloud.usage, "record_other", lambda *a, **k: None)
    monkeypatch.setattr(cloud, "system_prompt", lambda: "sys")
    monkeypatch.setattr("jarvis.hermes._ollama_tools", lambda: [])
    monkeypatch.setattr(cloud.requests, "post", lambda url, json=None, headers=None, timeout=None:
                        _Resp(200, {"choices": [{"finish_reason": "content_filter: PROHIBITED_CONTENT"}]}))
    brain = cloud.CloudBrain("gemini")
    brain.handle("hello")
    assert brain.failed   # so the next one in line answers


def test_a_turned_down_key_says_why_and_a_silent_check_still_keeps_it(monkeypatch):
    kept = {}
    import importlib
    config = importlib.import_module("jarvis.config")
    monkeypatch.setattr(config, "set_secret", lambda k, v: kept.update({k: v}))
    monkeypatch.setattr(config, "set_setting", lambda k, v: None)

    def post(url, json=None, headers=None, timeout=None):
        if headers["Authorization"].endswith("slow"):
            raise cloud.requests.Timeout()
        return _Resp(403, {"status": 403, "detail": "Authorization failed"})
    monkeypatch.setattr(cloud.requests, "post", post)
    saved, text = cloud.save_key("nvidia", "nvapi-" + "x" * 40 + "wrong")
    assert not saved and "403: Authorization failed" in text and "build.nvidia.com" in text and not kept
    saved, text = cloud.save_key("nvidia", "nvapi-" + "x" * 40 + "slow")
    assert saved and kept == {"NVIDIA_API_KEY": "nvapi-" + "x" * 40 + "slow"} and "first question" in text
