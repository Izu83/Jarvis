"""Models through their APIs: Gemini (Google AI Studio's free tier), ChatGPT (OpenAI) and Grok (xAI), all through the
same chat-completions API with tool calling, so one class runs Jarvis's tools for each.

- Gemini is free with an AI Studio key (no card; daily limits per model). It's the newest Flash model the key can
  use, and the next one down whenever that's busy or out of today's allowance; past all of them the next brain answers.
  For anything current it answers with Google Search instead (Gemini's own API, `search=True`).
  (The Gemini CLI on a Google plan was the first idea, but Google no longer lets personal accounts use it.)
- ChatGPT and Grok are paid per use: only while this month's spend is under "paid_budget" (USD, both together),
  and router.py sends them only what they're clearly best for, or what's left when the others can't answer.
  Prices are per million tokens in config.json ("chatgpt_price" / "grok_price"), so the spend is an estimate; the
  provider's dashboard has the exact bill.

Keys come from `run.bat --keys` (kept in .env); each model also has to be ticked in the model menu.
"""
import json
import logging
import re
import time

import requests

from . import memory, partial, stopping, tools, usage
from .brain import MAX_TOOL_ROUNDS, describe_step, step_kind, system_prompt
from .config import secret, settings

log = logging.getLogger("jarvis.cloud")

PROVIDERS = {
    #           .env key          API base                       model setting     default model    price setting
    "gemini":  ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini_model",
                "gemini-2.5-flash", "gemini_price"),
    "chatgpt": ("OPENAI_API_KEY", "https://api.openai.com/v1", "chatgpt_model", "gpt-5-mini", "chatgpt_price"),
    "grok":    ("XAI_API_KEY",    "https://api.x.ai/v1",       "grok_model",    "grok-4-fast", "grok_price"),
}
NAMES = {"gemini": "Gemini", "chatgpt": "ChatGPT", "grok": "Grok"}
FREE = {"gemini"}   # no budget to watch
SEARCH_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def has_key(provider: str) -> bool:
    return bool(secret(PROVIDERS[provider][0]))


def over_budget() -> bool:
    return usage.month_spend() >= float(settings.get("paid_budget", 5.0))


def usable(provider: str) -> bool:
    on = settings.get("use_" + provider, provider in FREE)
    return bool(on) and has_key(provider) and (provider in FREE or not over_budget())


# Gemini is used as a ladder: the newest stable Flash model your key can use first, and the next one down whenever
# it's busy ("high demand") or out of its free allowance for the day (each model has its own, about 20 a day).
# Pro models aren't on it: the free tier gives them nothing. gemini_model in config.json, if set, goes first.
_ladder: tuple[float, list[str]] = (0.0, [])
_resting: dict[str, float] = {}   # "model" (chat) or "model/search" -> until when it's skipped


def gemini_models(key: str) -> list[str]:
    """Every stable Gemini Flash model this key can use, newest first (looked up at most every 6 hours)."""
    global _ladder
    if _ladder[1] and time.time() - _ladder[0] < 6 * 3600:
        return _ladder[1]
    try:
        r = requests.get(f"{PROVIDERS['gemini'][1]}/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
        ids = [m.get("id", "").removeprefix("models/") for m in r.json().get("data", [])]
    except (requests.RequestException, ValueError, AttributeError):
        return _ladder[1] or [PROVIDERS["gemini"][3]]
    found = [(float(m.group(1)), name) for name in ids
             if (m := re.fullmatch(r"gemini-(\d+(?:\.\d+)?)-flash", name))]   # stable only: no -lite, -preview...
    if found:
        _ladder = (time.time(), [name for _, name in sorted(found, reverse=True)])
    return _ladder[1] or [PROVIDERS["gemini"][3]]


def best_gemini_model(key: str) -> str | None:
    """The newest stable Gemini Flash model this key can use."""
    models = gemini_models(key)
    return models[0] if models else None


def _rest(model: str, r: requests.Response, search: bool = False) -> None:
    """This model can't answer now: skip it until its allowance comes back (429: as long as Google says, or 10 min
    when it doesn't), for a few minutes when it's busy (503), or for a day when it's gone (404). Chat and Google
    Search have separate allowances, so they rest separately."""
    secs = {429: 600, 404: 86400}.get(r.status_code, 300)
    if r.status_code == 429:
        m = re.search(r'"retryDelay":\s*"(\d+)', r.text)
        if m:
            secs = max(60, int(m.group(1)))
    _resting[model + "/search" * search] = time.time() + secs
    why = {429: "has used up its free allowance", 404: "isn't available"}.get(r.status_code, "is busy")
    log.info("Gemini %s %s; using the next one down for %s.", model, why,
             f"{secs // 3600} h" if secs >= 3600 else f"{max(1, secs // 60)} min")


def _cost(provider: str, tin: int, tout: int) -> float:
    pin, pout = settings.get(PROVIDERS[provider][4], [1.0, 4.0])
    return tin / 1e6 * pin + tout / 1e6 * pout


class CloudBrain:
    def __init__(self, provider: str):
        self.provider, self.name = provider, NAMES[provider]
        key_env, self.base, model_key, default, _ = PROVIDERS[provider]
        self.key = secret(key_env)
        self.model = settings.get(model_key) or default
        self.failed = False

    def _models(self, search: bool = False) -> list[str]:
        """Which models to try, in order: for Gemini the ladder (minus the ones resting), otherwise the one."""
        if self.provider != "gemini":
            return [self.model]
        pinned = settings.get("gemini_model") or ""
        ladder = [pinned] * bool(pinned) + [m for m in gemini_models(self.key) if m != pinned]
        ready = [m for m in ladder if _resting.get(m + "/search" * search, 0) <= time.time()]
        return ready or ladder[-1:]   # all resting: try the last anyway, it says why

    def _post(self, call, search: bool = False) -> requests.Response:
        """call(model) -> response, down the ladder until one answers (or there's nothing left to try)."""
        for model in self._models(search):
            r = call(model)
            if r.status_code in (429, 503, 404, 500) and self.provider == "gemini":
                _rest(model, r, search)
                continue
            self.model = model
            return r
        return r
        self.last_thinking = ""

    def handle(self, command: str, chat: str | None = None, recent: list | None = None, progress=None,
               search: bool = False) -> str:
        self.failed, self.last_thinking = True, ""
        note = memory.context(chat, 0.0, recent, True, with_facts=True)
        if search and self.provider == "gemini":
            return self._search(note + command, progress)
        messages = [{"role": "system", "content": system_prompt()}, {"role": "user", "content": note + command}]
        from .hermes import _ollama_tools   # Jarvis's tools in the OpenAI shape
        spec, spent, started = _ollama_tools(), 0.0, time.time()
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                stopping.check()
                r = self._post(lambda model: requests.post(
                    f"{self.base}/chat/completions", timeout=180, headers={"Authorization": f"Bearer {self.key}"},
                    json={"model": model, "messages": messages, "tools": spec}))
                if r.status_code >= 400:
                    log.warning("%s refused: %s %s", self.name, r.status_code, r.text[:300])
                    if r.status_code == 429:
                        return f"{self.name} has hit its free limit for today."
                    return f"{self.name} couldn't answer (error {r.status_code})."
                data = r.json()
                use = data.get("usage") or {}
                tin, tout = use.get("prompt_tokens", 0), use.get("completion_tokens", 0)
                cost = _cost(self.provider, tin, tout)
                spent += cost
                usage.record_other(self.provider, tin, tout, cost)
                msg = (data.get("choices") or [{}])[0].get("message") or {}
                calls = msg.get("tool_calls") or []
                if not calls:
                    answer = (msg.get("content") or "").strip()
                    if answer:
                        self.failed = False
                        log.info("%s (%s) answered in %.1fs (about $%.4f).", self.name, self.model,
                                 time.time() - started, spent)
                    return answer or f"{self.name} had nothing to say."
                messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                if msg.get("content"):
                    self.last_thinking += msg["content"].strip() + "\n"
                for call in calls:
                    fn = call.get("function") or {}
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    if progress:
                        progress(describe_step(name, args), step_kind(name))
                    try:
                        output = tools.run(name, args)
                    except Exception as e:   # a tool error goes back to the model, which can try something else
                        output = f"Error: {e}"
                    messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": str(output)[:20000]})
            return f"{self.name} took too many steps, so I stopped it."
        except requests.RequestException as e:
            log.warning("%s didn't answer: %s", self.name, e)
            return f"{self.name} can't be reached right now."
        finally:
            partial.clear()

    def _search(self, prompt: str, progress) -> str:
        """Gemini with Google Search, through its own API: for news, prices, scores and anything else current."""
        if progress:
            progress("Searching Google…", "Web")
        started = time.time()
        try:
            r = self._post(lambda model: requests.post(
                SEARCH_API.format(model=model), timeout=120, headers={"x-goog-api-key": self.key},
                json={"systemInstruction": {"parts": [{"text": system_prompt()}]},
                      "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      "tools": [{"google_search": {}}]}), search=True)
            if r.status_code >= 400:
                log.warning("Gemini search refused: %s %s", r.status_code, r.text[:300])
                if r.status_code == 429:
                    return "Gemini has hit its free limit for now."
                return f"Gemini couldn't search (error {r.status_code})."
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("Gemini search didn't answer: %s", e)
            return "Gemini can't be reached right now."
        use = data.get("usageMetadata") or {}
        usage.record_other("gemini", use.get("promptTokenCount", 0), use.get("candidatesTokenCount", 0))
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        answer = "".join(p.get("text", "") for p in parts).strip()
        if answer:
            self.failed = False
            log.info("Gemini (%s) answered with Google Search in %.1fs.", self.model, time.time() - started)
        return answer or "Gemini found nothing to say."
