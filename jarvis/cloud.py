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
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", "openrouter_model",
                   "nvidia/nemotron-3-ultra-550b-a55b:free", "openrouter_price"),
    "chatgpt": ("OPENAI_API_KEY", "https://api.openai.com/v1", "chatgpt_model", "gpt-5-mini", "chatgpt_price"),
    "grok":    ("XAI_API_KEY",    "https://api.x.ai/v1",       "grok_model",    "grok-4-fast", "grok_price"),
    # Perplexity: answers from the web with sources, through its Agent API (the Sonar API it replaced was retired
    # on Sept 27, 2026). The "model" is its effort preset; "fast" searches the web and is the cheapest.
    "perplexity": ("PERPLEXITY_API_KEY", "https://api.perplexity.ai/v1", "perplexity_effort", "fast", "perplexity_price"),
}
NAMES = {"gemini": "Gemini", "openrouter": "Free models", "chatgpt": "ChatGPT", "grok": "Grok", "perplexity": "Perplexity"}
FREE = {"gemini", "openrouter"}   # no budget to watch
LADDERS = {"gemini", "openrouter"}   # many models, tried in turn (see below)
SEARCH_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# How long a model gets before the next one down (or the next brain) takes over. Gemini's newest models sometimes
# just hang when Google is busy; waiting the old 180 s for each made "yo Jr" take three minutes.
FIRST_ANSWER = 45   # s: the first reply of a command, for all of the ladder together
NEXT_STEP = 90      # s: each later step (it's already working on it)
PER_MODEL = 18      # s: one model's share of the first reply, so a hung one leaves time for the next one down
BUSY_REST = 900     # s: a model that hung is skipped this long
KEEP_GOOD = 1800    # s: the model that last answered goes first this long (no waiting on a busy newer one each time)
# Gemini's Flash models think before answering. Unlimited thinking was the slow part (5 to 37 s to say hi); "low" was
# 2 to 3 times faster in tests and still picked the right tools. A bit more for doing things on the PC and code.
GEMINI_EFFORT = {"action": "medium", "code": "medium", "sight": "medium"}   # everything else: "low"


def perplexity_answer(data: dict) -> tuple[str, list[tuple[str, str]]]:
    """(the answer, its sources as (title, url)) from a Perplexity Agent API response. The answer is the "message"
    item's text; the sources are the "search_results" item's results, in their [1], [2] order (or the citations on
    the text when there are none)."""
    text, sources, cited = "", [], []
    for item in data.get("output") or []:
        if item.get("type") == "message":
            for part in item.get("content") or []:
                text += part.get("text") or ""
                cited += [(a.get("title") or a.get("url"), a["url"]) for a in part.get("annotations") or []
                          if a.get("type") == "citation" and a.get("url")]
        elif item.get("type") == "search_results":
            sources += [(r.get("title") or r.get("url"), r["url"]) for r in item.get("results") or [] if r.get("url")]
    text = text or data.get("output_text") or ""
    text = re.sub(r"\[web:(\d+)\]", r"[\1]", text).strip()   # the higher efforts mark sources [web:1]
    seen, out = set(), []
    for title, url in sources or cited:
        if url not in seen:
            seen.add(url)
            out.append((title, url))
    return text, out[:8]


def has_key(provider: str) -> bool:
    return bool(secret(PROVIDERS[provider][0]))


KEY_PAGES = {"gemini": "https://aistudio.google.com/apikey", "openrouter": "https://openrouter.ai/keys",
             "chatgpt": "https://platform.openai.com/api-keys", "grok": "https://console.x.ai",
             "perplexity": "https://console.perplexity.ai"}


def check_key(provider: str, key: str) -> bool:
    """Does the provider accept this key? (A cheap read-only call; nothing is spent.)"""
    base = PROVIDERS[provider][1]
    check = f"{base}/key" if provider == "openrouter" else f"{base}/models"   # OpenRouter's model list is public
    try:
        if provider == "perplexity":
            # No read-only call checks a Perplexity key, so send an empty question: a wrong key gets 401 before
            # anything else; a good one gets "bad request" (400), and nothing is answered or charged.
            r = requests.post(f"{base}/agent", json={}, headers={"Authorization": f"Bearer {key}"}, timeout=15)
            return r.status_code not in (401, 403) and r.status_code < 500
        return requests.get(check, headers={"Authorization": f"Bearer {key}"}, timeout=15).ok
    except requests.RequestException:
        return False


def save_key(provider: str, key: str) -> tuple[bool, str]:
    """Check a pasted key and, if it works, keep it in .env and tick the model: (saved, what to tell the user).
    Used by `run.bat --keys` and by the app's Paste Key. The key itself is never logged or sent back."""
    from .config import set_secret, set_setting
    name = NAMES[provider]
    key = "".join(key.split())   # pasted with a stray space or line break
    if not 20 <= len(key) <= 300 or not key.isascii():
        return False, f"That doesn't look like a {name} key."
    if not check_key(provider, key):
        return False, f"{name} didn't accept that key. Nothing changed."
    set_secret(PROVIDERS[provider][0], key)
    set_setting("use_" + provider, True)
    _ladders.pop(provider, None)        # its models may differ with this key: look again
    _provider_resting.pop(provider, None)
    log.info("%s key saved and %s ticked.", name, name)
    return True, f"{name} is connected and switched on."


def over_budget() -> bool:
    return usage.month_spend() >= float(settings.get("paid_budget", 5.0))


def usable(provider: str) -> bool:
    on = settings.get("use_" + provider, provider in FREE)
    return bool(on) and has_key(provider) and (provider in FREE or not over_budget()) and not _all_resting(provider)


# The free ones are used as a ladder: the best model first, and the next one down whenever it's busy or out of its
# free allowance, each skipped until it's back.
#  - Gemini: the newest stable Flash model your key can use first (each has its own allowance, about 20 a day).
#    Pro models aren't on it: the free tier gives them nothing.
#  - OpenRouter: its free models (":free"; about 50 requests a day for all of them together, 1,000 after a one-time
#    $10 top-up). DeepSeek first whenever one of its models is free, then the strongest of the rest.
# A model named in config.json (gemini_model / openrouter_model) goes first.
_ladders: dict[str, tuple[float, list[str]]] = {}
_resting: dict[str, float] = {}   # "provider:model" (chat) or "...:model/search" -> until when it's skipped
_provider_resting: dict[str, float] = {}   # the whole provider: OpenRouter's daily free allowance is used up
_good: dict[str, tuple[str, float]] = {}   # provider -> (the model that last answered, when)
_names: dict[str, str] = {}   # OpenRouter model id -> its name ("NVIDIA: Nemotron 3 Ultra (free)"), for the phone
# OpenRouter's free models, best first after DeepSeek (by name; anything else comes after these, newest first).
# Too small, or not for chatting: left out.
OPENROUTER_PREFER = ("nemotron-3-ultra", "inkling:", "nemotron-3.5-super", "gemma-4-31b", "nemotron-3.5-lightning",
                     "gemma-4-26b", "inkling-small")
OPENROUTER_SKIP = ("content-safety", "guard", "lfm-", "-code", "embed", "omni", "-vl", "vision", "audio", "image")


def _fetch_models(provider: str, key: str) -> list[str]:
    """What this provider offers that goes on the ladder, best first."""
    if provider == "gemini":
        r = requests.get(f"{PROVIDERS['gemini'][1]}/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
        ids = [m.get("id", "").removeprefix("models/") for m in r.json().get("data", [])]
        found = [(float(m.group(1)), name) for name in ids
                 if (m := re.fullmatch(r"gemini-(\d+(?:\.\d+)?)-flash", name))]   # stable only: no -lite, -preview
        return [name for _, name in sorted(found, reverse=True)]
    r = requests.get(f"{PROVIDERS['openrouter'][1]}/models", timeout=20)
    for m in r.json().get("data", []):
        if m.get("name"):
            _names[m["id"]] = re.sub(r"\s*\(free\)$", "", m["name"])
    free = [m for m in r.json().get("data", []) if m.get("id", "").endswith(":free")
            and "tools" in (m.get("supported_parameters") or [])   # Jarvis's tools are how he does things
            and not any(x in m["id"] for x in OPENROUTER_SKIP)]

    def rank(m):
        mid = m["id"]
        if mid.startswith("deepseek/"):
            return (0, -m.get("created", 0))
        for i, name in enumerate(OPENROUTER_PREFER):
            if name in mid:
                return (1 + i, 0)
        return (len(OPENROUTER_PREFER) + 1, -m.get("created", 0))
    return [m["id"] for m in sorted(free, key=rank)]


def ladder(provider: str, key: str) -> list[str]:
    """The provider's models, best first (looked up at most every 6 hours)."""
    when, models = _ladders.get(provider, (0.0, []))
    if not models or time.time() - when > 6 * 3600:
        try:
            found = _fetch_models(provider, key)
            if found:
                _ladders[provider] = (time.time(), found)
                models = found
        except (requests.RequestException, ValueError, AttributeError, KeyError):
            log.debug("couldn't list %s's models", provider, exc_info=True)
    return models or [PROVIDERS[provider][3]]


def gemini_models(key: str) -> list[str]:
    return ladder("gemini", key)


def best_gemini_model(key: str) -> str | None:
    """The newest stable Gemini Flash model this key can use."""
    models = gemini_models(key)
    return models[0] if models else None


def current_free_model() -> str:
    """The free model OpenRouter's ladder would use now ("DeepSeek: ..." / "NVIDIA: Nemotron 3 Ultra"), for the
    phone's model menu. "" until it's known, or when today's free allowance is used up."""
    if not has_key("openrouter") or _all_resting("openrouter"):
        return ""
    if "openrouter" not in _ladders:
        import threading
        threading.Thread(target=ladder, args=("openrouter", ""), daemon=True, name="free-models").start()
        return ""
    for m in _ladders["openrouter"][1]:
        if _resting.get("openrouter:" + m, 0) <= time.time():
            return _names.get(m, m)
    return ""


def _all_resting(provider: str) -> bool:
    return _provider_resting.get(provider, 0) > time.time()


def _until_tomorrow_utc() -> int:
    return int(86400 - time.time() % 86400) + 60


def _rest(provider: str, model: str, r: requests.Response, search: bool = False) -> None:
    """This model can't answer now: skip it until its allowance comes back (429: as long as the provider says, or
    10 min when it doesn't), for a few minutes when it's busy (503 and the like), or for a day when it's gone (404).
    Gemini's chat and Google Search have separate allowances, so they rest separately. OpenRouter's free daily
    allowance covers all its free models, so when that's used up the whole of it rests until midnight UTC."""
    name = NAMES[provider]
    if provider == "openrouter" and r.status_code == 429 and re.search(r"per[- ]day", r.text, re.I):
        secs = _until_tomorrow_utc()
        _provider_resting[provider] = time.time() + secs
        log.info("%s: today's free allowance is used up; back in %s.", name,
                 f"{secs // 3600} h" if secs >= 3600 else f"{max(1, secs // 60)} min")
        return
    secs = {429: 600, 404: 86400}.get(r.status_code, 300)
    if r.status_code == 429:
        m = re.search(r'"retryDelay":\s*"(\d+)', r.text)
        if m:
            secs = max(60, int(m.group(1)))
        elif provider == "openrouter":
            secs = 120   # a busy free model, or the per-minute limit
    _resting[f"{provider}:{model}" + "/search" * search] = time.time() + secs
    why = "isn't available" if r.status_code == 404 else (
        "has used up its free allowance" if r.status_code == 429 and provider == "gemini" else "is busy")
    log.info("%s %s %s; using the next one down for %s.", name, model, why,
             f"{secs // 3600} h" if secs >= 3600 else f"{max(1, secs // 60)} min")


def _cost(provider: str, tin: int, tout: int) -> float:
    pin, pout = settings.get(PROVIDERS[provider][4], [1.0, 4.0])
    return tin / 1e6 * pin + tout / 1e6 * pout


class CloudBrain:
    def __init__(self, provider: str):
        self.provider, self.name = provider, NAMES[provider]
        key_env, self.base, self.model_key, default, _ = PROVIDERS[provider]
        self.key = secret(key_env)
        self.model = settings.get(self.model_key) or default
        self.failed = False
        self.last_thinking = ""

    def _models(self, search: bool = False) -> list[str]:
        """Which models to try, in order: for a ladder its models (minus the ones resting), otherwise the one."""
        if self.provider not in LADDERS:
            return [self.model]
        pinned = settings.get(self.model_key) or ""
        models = [pinned] * bool(pinned) + [m for m in ladder(self.provider, self.key) if m != pinned]
        good, when = _good.get(self.provider + "/search" * search, ("", 0.0))
        if good in models and not pinned and time.time() - when < KEEP_GOOD:
            models = [good] + [m for m in models if m != good]
        ready = [m for m in models if _resting.get(f"{self.provider}:{m}" + "/search" * search, 0) <= time.time()]
        return ready or models[-1:]   # all resting: try the last anyway, it says why

    def _post(self, call, search: bool = False, deadline: float | None = None,
              per_model: float | None = None) -> requests.Response:
        """call(model, timeout) -> response, down the ladder until one answers in time (or there's nothing left to
        try). A model that doesn't answer before the deadline is skipped for a while, like a busy one. Raises
        requests.Timeout when none answered in time, so the next brain takes over."""
        r = None
        deadline = deadline or time.time() + 180
        for model in self._models(search):
            left = deadline - time.time()
            if left < 3:
                break
            try:
                r = call(model, (10, min(left, per_model or left)))
            except (requests.Timeout, requests.ConnectionError) as e:
                if self.provider not in LADDERS:
                    raise
                _resting[f"{self.provider}:{model}" + "/search" * search] = time.time() + BUSY_REST
                log.info("%s %s didn't answer in time (%s); skipping it for %d min.", self.name, model,
                         type(e).__name__, BUSY_REST // 60)
                r = None
                continue
            if self.provider in LADDERS and (r.status_code in (429, 404) or r.status_code >= 500
                                             or (self.provider == "openrouter" and r.status_code in (400, 402))):
                _rest(self.provider, model, r, search)
                if _all_resting(self.provider):
                    break
                continue
            self.model = model
            if self.provider in LADDERS and r.status_code < 400:
                _good[self.provider + "/search" * search] = (model, time.time())
            return r
        if r is None:
            raise requests.Timeout(f"none of {self.name}'s models answered in time")
        return r

    def handle(self, command: str, chat: str | None = None, recent: list | None = None, progress=None,
               search: bool = False, kind: str = "chat") -> str:
        self.failed, self.last_thinking = True, ""
        note = memory.context(chat, 0.0, recent, True, with_facts=True)
        if self.provider == "perplexity":
            return self._perplexity(note + command, progress)
        if search and self.provider == "gemini":
            return self._search(note + command, progress)
        messages = [{"role": "system", "content": system_prompt()}, {"role": "user", "content": note + command}]
        from .hermes import _ollama_tools   # Jarvis's tools in the OpenAI shape
        spec, spent, started = _ollama_tools(), 0.0, time.time()
        extra = {"reasoning_effort": GEMINI_EFFORT.get(kind, "low")} if self.provider == "gemini" else {}
        try:
            for step in range(MAX_TOOL_ROUNDS):
                stopping.check()
                r = self._post(lambda model, timeout: requests.post(
                    f"{self.base}/chat/completions", timeout=timeout, headers={"Authorization": f"Bearer {self.key}"},
                    json={"model": model, "messages": messages, "tools": spec, **extra}),
                    deadline=time.time() + (NEXT_STEP if step else FIRST_ANSWER), per_model=None if step else PER_MODEL)
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

    def _perplexity(self, prompt: str, progress) -> str:
        """Perplexity's Agent API: it searches the web and answers with sources, which go under the answer. It can't
        use Jarvis's tools (open apps and the like), so router.py only sends it questions about the world."""
        if progress:
            progress("Searching with Perplexity…", "Web")
        started = time.time()
        effort = settings.get("perplexity_effort") or "fast"
        try:
            r = requests.post(f"{self.base}/agent", timeout=(10, 90), headers={"Authorization": f"Bearer {self.key}"},
                              json={"effort": effort, "input": prompt, "instructions": system_prompt()})
            if r.status_code >= 400:
                log.warning("Perplexity refused: %s %s", r.status_code, r.text[:300])
                if r.status_code == 401:
                    return "Perplexity didn't accept its key. Add it again in Settings › AI Models."
                if r.status_code in (402, 429):
                    return "Perplexity is out of credit or busy right now."
                return f"Perplexity couldn't answer (error {r.status_code})."
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("Perplexity didn't answer: %s", e)
            return "Perplexity can't be reached right now."
        answer, sources = perplexity_answer(data)
        use = data.get("usage") or {}
        cost = float(((use.get("cost") or {}).get("total_cost")) or 0)   # Perplexity says what it charged
        usage.record_other("perplexity", use.get("input_tokens", 0), use.get("output_tokens", 0), cost)
        if not answer:
            return "Perplexity found nothing to say."
        self.failed = False
        log.info("Perplexity (%s) answered in %.1fs (about $%.4f).", effort, time.time() - started, cost)
        if sources:
            answer += "\n\nSources:\n" + "\n".join(f"{i}. [{t}]({u})" for i, (t, u) in enumerate(sources, 1))
        return answer

    def _search(self, prompt: str, progress) -> str:
        """Gemini with Google Search, through its own API: for news, prices, scores and anything else current."""
        if progress:
            progress("Searching Google…", "Web")
        started = time.time()
        try:
            r = self._post(lambda model, timeout: requests.post(
                SEARCH_API.format(model=model), timeout=timeout, headers={"x-goog-api-key": self.key},
                json={"systemInstruction": {"parts": [{"text": system_prompt()}]},
                      "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      "tools": [{"google_search": {}}]}), search=True, deadline=time.time() + 60, per_model=30)
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
