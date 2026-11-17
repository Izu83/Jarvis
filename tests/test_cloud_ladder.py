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
    monkeypatch.setattr(cloud, "_good", {})
    monkeypatch.setattr(cloud, "_ladders", {"openrouter": (time.time(), ["a:free", "b:free", "c:free"])})
    monkeypatch.setattr(cloud, "_resting", {})
    monkeypatch.setattr(cloud, "_provider_resting", {})
    brain, tried = cloud.CloudBrain("openrouter"), []

    def call(model, timeout):
        tried.append(model)
        return _Resp(429 if model == "a:free" else 200, "rate-limited upstream")
    assert brain._post(call).status_code == 200
    assert tried == ["a:free", "b:free"] and brain.model == "b:free"
    assert brain._models()[0] == "b:free"   # a rests for a while


def test_the_daily_allowance_rests_all_of_them(monkeypatch):
    monkeypatch.setattr(cloud, "_good", {})
    monkeypatch.setattr(cloud, "_ladders", {"openrouter": (time.time(), ["a:free", "b:free"])})
    monkeypatch.setattr(cloud, "_resting", {})
    monkeypatch.setattr(cloud, "_provider_resting", {})
    brain, tried = cloud.CloudBrain("openrouter"), []
    brain._post(lambda m, timeout: (tried.append(m), _Resp(429, "Rate limit exceeded: free-models-per-day."))[1])
    assert tried == ["a:free"]   # no point trying the others: it's one allowance for all
    assert cloud._all_resting("openrouter")


def test_a_model_that_hangs_is_skipped_quickly(monkeypatch):
    """Google's newest Flash sometimes just hangs: the next one down answers, and the hung one rests."""
    monkeypatch.setattr(cloud, "_good", {})
    monkeypatch.setattr(cloud, "_ladders", {"gemini": (time.time(), ["gemini-3.8-flash", "gemini-3.7-flash"])})
    monkeypatch.setattr(cloud, "_resting", {})
    monkeypatch.setattr(cloud, "_provider_resting", {})
    monkeypatch.setitem(cloud.settings, "gemini_model", "")
    brain, asked = cloud.CloudBrain("gemini"), []

    def call(model, timeout):
        asked.append((model, timeout))
        if model == "gemini-3.8-flash":
            raise cloud.requests.ReadTimeout("hung")
        return _Resp(200, "ok")
    assert brain._post(call, deadline=time.time() + 40).status_code == 200
    assert [m for m, _ in asked] == ["gemini-3.8-flash", "gemini-3.7-flash"]
    assert asked[0][1][1] <= 40                       # never the old 180 s
    assert brain._models()[0] == "gemini-3.7-flash"   # the hung one rests for a while
    cloud._resting.clear()
    assert brain._models()[0] == "gemini-3.7-flash"   # and the one that answered stays first for a while


def test_when_none_answer_in_time_the_next_brain_takes_over(monkeypatch):
    monkeypatch.setattr(cloud, "_good", {})
    monkeypatch.setattr(cloud, "_ladders", {"gemini": (time.time(), ["gemini-3.8-flash"])})
    monkeypatch.setattr(cloud, "_resting", {})
    monkeypatch.setitem(cloud.settings, "gemini_model", "")
    brain = cloud.CloudBrain("gemini")

    def hang(model, timeout):
        raise cloud.requests.ReadTimeout("hung")
    try:
        brain._post(hang, deadline=time.time() + 40)
    except cloud.requests.Timeout:
        pass
    else:
        raise AssertionError("expected a Timeout so the next brain answers")


def test_a_pasted_key_is_checked_before_it_is_kept(monkeypatch):
    """The app's Paste Key: a key the provider turns down changes nothing; a good one is kept and switched on."""
    kept, ticked = {}, {}
    import jarvis.config as config
    monkeypatch.setattr(config, "set_secret", lambda k, v: kept.__setitem__(k, v))
    monkeypatch.setattr(config, "set_setting", lambda k, v: ticked.__setitem__(k, v))
    monkeypatch.setattr(cloud, "check_key", lambda provider, key: key.endswith("good"))
    saved, text = cloud.save_key("openrouter", "sk-or-v1-" + "x" * 30 + "bad")
    assert not saved and "didn't accept" in text and not kept and not ticked
    saved, text = cloud.save_key("openrouter", "short")
    assert not saved and "doesn't look like" in text
    saved, text = cloud.save_key("openrouter", " sk-or-v1-" + "x" * 30 + "\ngood ")   # pasted with a line break
    assert saved and kept == {"OPENROUTER_API_KEY": "sk-or-v1-" + "x" * 30 + "good"} and ticked == {"use_openrouter": True}
    assert "x" * 30 not in text   # the key is never said back
