"""The free models' ladder: which OpenRouter model goes first, and what happens when one can't answer."""
import time

from jarvis import cloud


class _Resp:
    def __init__(self, status=200, text="", data=None):
        self.status_code, self.text, self._data = status, text, data

    def json(self):
        return self._data


def _models(*specs):
    return {"data": [{"id": i, "name": n, "created": c, "supported_parameters": ["tools"] if t else []}
                     for i, n, c, t in specs]}


def test_deepseek_goes_first_when_its_free(monkeypatch):
    data = _models(("nvidia/nemotron-3-ultra-550b-a55b:free", "NVIDIA: Nemotron 3 Ultra (free)", 1, True),
                   ("google/gemma-4-31b-it:free", "Google: Gemma 4 31B (free)", 2, True),
                   ("deepseek/deepseek-v4.1-flash:free", "DeepSeek: V4.1 Flash (free)", 3, True),
                   ("deepseek/deepseek-v4.1-flash", "DeepSeek: V4.1 Flash", 3, True),            # paid: not on it
                   ("nvidia/nemotron-3.5-content-safety:free", "Safety (free)", 4, False),      # not for chat
                   ("liquid/lfm-2.5-2.6b:free", "Tiny (free)", 5, True))                        # too small
    monkeypatch.setattr(cloud.requests, "get", lambda *a, **k: _Resp(data=data))
    assert cloud._fetch_models("openrouter", "") == ["deepseek/deepseek-v4.1-flash:free",
                                                     "nvidia/nemotron-3-ultra-550b-a55b:free",
                                                     "google/gemma-4-31b-it:free"]
    assert cloud._names["deepseek/deepseek-v4.1-flash:free"] == "DeepSeek: V4.1 Flash"


def test_a_busy_model_is_skipped_and_the_next_answers(monkeypatch):
    monkeypatch.setattr(cloud, "_ladders", {"openrouter": (time.time(), ["a:free", "b:free", "c:free"])})
    monkeypatch.setattr(cloud, "_resting", {})
    monkeypatch.setattr(cloud, "_provider_resting", {})
    brain, tried = cloud.CloudBrain("openrouter"), []

    def call(model):
        tried.append(model)
        return _Resp(429 if model == "a:free" else 200, "rate-limited upstream")
    assert brain._post(call).status_code == 200
    assert tried == ["a:free", "b:free"] and brain.model == "b:free"
    assert brain._models()[0] == "b:free"   # a rests for a while


def test_the_daily_allowance_rests_all_of_them(monkeypatch):
    monkeypatch.setattr(cloud, "_ladders", {"openrouter": (time.time(), ["a:free", "b:free"])})
    monkeypatch.setattr(cloud, "_resting", {})
    monkeypatch.setattr(cloud, "_provider_resting", {})
    brain, tried = cloud.CloudBrain("openrouter"), []
    brain._post(lambda m: (tried.append(m), _Resp(429, "Rate limit exceeded: free-models-per-day."))[1])
    assert tried == ["a:free"]   # no point trying the others: it's one allowance for all
    assert cloud._all_resting("openrouter")
