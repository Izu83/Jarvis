"""A free, local brain: a Hermes model served by Ollama on this PC, using the same tools as the Claude brains.

It tries to handle the whole command locally (zero tokens, works offline). If the local server isn't running,
the model errors, or the command is too hard for it, Jarvis quietly falls back to Claude so nothing is lost.
Vision ("look at the screen") and web questions still go to Claude, since this text model can't do them well.
"""
import json
import logging
import threading
import time

import requests

from . import tools
from .brain import (IDLE_RESET_SECONDS, MAX_TOOL_ROUNDS, SYSTEM_TEMPLATE,
                    describe_step, make_brain_named, thread_switch_note)
from .config import settings

log = logging.getLogger("jarvis.hermes")


def _ollama_tools() -> list[dict]:
    """Jarvis's tools in the shape Ollama/OpenAI want: {type, function:{name, description, parameters}}."""
    out = []
    for t in tools.TOOLS:
        out.append({"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
        }})
    return out


class HermesBrain:
    def __init__(self):
        self.host = settings["ollama_host"].rstrip("/")
        self.model = settings["hermes_model"]
        self.tools = _ollama_tools()
        self.system = SYSTEM_TEMPLATE.format(environment=tools.environment_description())
        self.history: list = []
        self.thread = None
        self.progress = None
        self.last_used = 0.0
        self.last_thinking = ""
        self.lock = threading.Lock()
        self._fallback = None   # a Claude brain, created only if/when it's actually needed
        log.info("Local Hermes brain ready (model %s via %s). Claude is the fallback.", self.model, self.host)

    def _claude(self):
        if self._fallback is None:
            log.info("Spinning up the Claude fallback brain.")
            self._fallback = make_brain_named(settings["hermes_fallback"])
        return self._fallback

    def handle(self, command: str, chat: str | None = None, recent: list | None = None, progress=None) -> str:
        with self.lock:
            self.progress = progress
            if time.time() - self.last_used > IDLE_RESET_SECONDS or len(self.history) > 60 or chat != self.thread:
                self.history = []
                if chat != self.thread:
                    command = thread_switch_note(recent) + command
                self.thread = chat
            self.last_used = time.time()
            try:
                answer = self._run(command)
                if answer is not None:
                    return answer
                log.info("Hermes couldn't finish; handing this one to Claude.")
            except requests.RequestException as e:
                log.info("Local Hermes server unreachable (%s); using Claude for this command.", e)
            except Exception:
                log.exception("Hermes failed; using Claude for this command.")
            # Fall back to Claude. It keeps its own history, so start it clean for this command.
            self.history = []
            answer = self._claude().handle(command, chat=chat, recent=recent, progress=progress)
            self.last_thinking = getattr(self._claude(), "last_thinking", "") or ""
            return answer

    def _run(self, command: str) -> str | None:
        """One local agent loop. Returns the spoken answer, or None if the model gave up / looped out so we
        should fall back to Claude."""
        self.history.append({"role": "user", "content": command})
        steps: list[str] = []
        started = time.time()
        for _ in range(MAX_TOOL_ROUNDS):
            msg = self._chat()
            self.history.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                answer = (msg.get("content") or "").strip()
                self.last_thinking = _steps_thoughts(steps)
                log.info("Hermes answered locally in %.1fs (no tokens).", time.time() - started)
                return answer or "Done."
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                raw = fn.get("arguments", {})
                args = raw if isinstance(raw, dict) else _loads(raw)
                step = describe_step(name, args)
                steps.append(step)
                if self.progress:
                    self.progress(step)
                log.info("tool %s %s", name, json.dumps(args)[:400])
                try:
                    output = tools.run(name, args)
                except Exception as e:
                    log.exception("tool %s failed", name)
                    output = f"Error: {e}"
                log.info("  -> %s", str(output)[:300])
                self.history.append({"role": "tool", "content": str(output)})
        return None   # ran out of rounds -> let Claude take over

    def _chat(self) -> dict:
        r = requests.post(f"{self.host}/api/chat", json={
            "model": self.model,
            "messages": [{"role": "system", "content": self.system}, *self.history],
            "tools": self.tools,
            "stream": False,
            "keep_alive": "30m",   # keep the model warm in VRAM between commands
            "options": {"temperature": 0.3, "num_ctx": 8192},
        }, timeout=180)
        r.raise_for_status()
        return r.json().get("message", {}) or {}


def _loads(raw) -> dict:
    try:
        return json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except Exception:
        return {}


def _steps_thoughts(steps: list[str]) -> str:
    doing = [s for s in steps if s and s != "Working…"]
    return ("What I did:\n" + "\n".join("• " + s.rstrip("…") for s in doing)) if doing else ""
