"""Pay-per-use brains through their APIs: ChatGPT (OpenAI) and Grok (xAI). Both speak the same chat-completions API
with tool calling, so one class runs Jarvis's tools for either.

They're only used when you've added an API key (`run.bat --keys`, kept in .env) and ticked them in the model menu,
and only while this month's spend is under "paid_budget" (USD, both together): router.py sends them only what they
are clearly best for, or what's left when your plans' models can't answer. Prices are per million tokens, in
config.json ("chatgpt_price" / "grok_price", input and output), so the spend is an estimate; your provider's
dashboard has the exact bill.
"""
import json
import logging
import time

import requests

from . import memory, partial, stopping, tools, usage
from .brain import MAX_TOOL_ROUNDS, describe_step, step_kind, system_prompt
from .config import secret, settings

log = logging.getLogger("jarvis.cloud")

PROVIDERS = {
    #           .env key          API base                       model setting     default model    price setting
    "chatgpt": ("OPENAI_API_KEY", "https://api.openai.com/v1", "chatgpt_model", "gpt-5-mini", "chatgpt_price"),
    "grok":    ("XAI_API_KEY",    "https://api.x.ai/v1",       "grok_model",    "grok-4-fast", "grok_price"),
}
NAMES = {"chatgpt": "ChatGPT", "grok": "Grok"}


def has_key(provider: str) -> bool:
    return bool(secret(PROVIDERS[provider][0]))


def over_budget() -> bool:
    return usage.month_spend() >= float(settings.get("paid_budget", 5.0))


def usable(provider: str) -> bool:
    return bool(settings.get("use_" + provider, False)) and has_key(provider) and not over_budget()


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
        self.last_thinking = ""

    def handle(self, command: str, chat: str | None = None, recent: list | None = None, progress=None) -> str:
        self.failed, self.last_thinking = True, ""
        note = memory.context(chat, 0.0, recent, True, with_facts=True)
        messages = [{"role": "system", "content": system_prompt()}, {"role": "user", "content": note + command}]
        from .hermes import _ollama_tools   # Jarvis's tools in the OpenAI shape
        spec, spent, started = _ollama_tools(), 0.0, time.time()
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                stopping.check()
                r = requests.post(f"{self.base}/chat/completions", timeout=180,
                                  headers={"Authorization": f"Bearer {self.key}"},
                                  json={"model": self.model, "messages": messages, "tools": spec})
                if r.status_code >= 400:
                    log.warning("%s refused: %s %s", self.name, r.status_code, r.text[:300])
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
                        log.info("%s answered in %.1fs (about $%.4f).", self.name, time.time() - started, spent)
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
