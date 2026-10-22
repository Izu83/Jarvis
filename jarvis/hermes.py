"""A free, local brain: a Hermes model served by Ollama on this PC, using the same tools as the Claude brains.

It tries to handle the whole command locally (zero tokens, works offline). Three things make sure it only keeps
what it's actually good at, and hands the rest to Claude so nothing is wrong or lost:
- Up front, commands that clearly need screen vision, the web, or an exact file search go straight to Claude.
- Mid-task, if the local model reaches for the screen or mouse (which need vision), Claude takes over.
- At the end, an empty or unconfirmed answer is dropped in favour of Claude rather than guessed.
Everything else — apps, windows, PowerShell, media, files, modes, reminders — Hermes does itself, free and offline.
"""
import json
import logging
import re
import threading
import time

import requests

from . import tools
from .brain import (IDLE_RESET_SECONDS, MAX_TOOL_ROUNDS, SYSTEM_TEMPLATE,
                    describe_step, make_brain_named, thread_switch_note)
from .config import settings

log = logging.getLogger("jarvis.hermes")

# Tools that depend on actually seeing the screen — the text model can't, so these mean "let Claude do it".
VISION_TOOLS = {"look_at_screen", "mouse_click", "mouse_scroll", "mouse_drag"}

# Phrases that mean the command needs vision or the web (which the local tools can't do) — route to Claude first.
CLAUDE_HINTS = (
    "look at", "on my screen", "on the screen", "on screen", "see the screen", "read the screen",
    "what do you see", "what's on", "whats on", "screenshot", "take a screenshot",
    "search the web", "google ", "look it up", "look up ", "on the internet", "online",
    "latest news", "the news", "weather", "stock price", "who won", "search for information",
)

# Exact file searches: Hermes tends to grab the wrong tool, so send these to Claude for a precise answer.
_FILE_SEARCH = re.compile(
    r"\b(find|search(?! the web)|locate|where'?s|where is|list (?:all |any )?)\b.*"
    r"\b(files?|pdfs?|documents?|folders?|photos?|images?|\.\w{2,4})\b", re.I)

# A short nudge on top of the shared instructions, aimed at the smaller local model.
HERMES_NOTE = (
    "\n\nYou are a fast local model. Be decisive and pick the SINGLE most fitting tool: to find files use "
    "find_files (never disk_usage); to measure folder/disk sizes use disk_usage; to see what's open use "
    "list_windows. Never say you did something unless a tool result confirms it. Always finish with one short "
    "spoken sentence that states the ACTUAL result from the tools, not a vague 'I have provided that'.")


def _ollama_tools() -> list[dict]:
    """Jarvis's tools in the shape Ollama/OpenAI want: {type, function:{name, description, parameters}}."""
    out = []
    for t in tools.TOOLS:
        out.append({"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
        }})
    return out


def _needs_claude(command: str) -> bool:
    """True when the command clearly needs something Hermes can't do well (vision, web, exact file search)."""
    c = command.lower()
    if any(h in c for h in CLAUDE_HINTS):
        return True
    return bool(_FILE_SEARCH.search(c))


class HermesBrain:
    def __init__(self):
        self.host = settings["ollama_host"].rstrip("/")
        self.model = settings["hermes_model"]
        self.tools = _ollama_tools()
        self.system = SYSTEM_TEMPLATE.format(environment=tools.environment_description()) + HERMES_NOTE
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

    def _to_claude(self, command, chat, recent, progress) -> str:
        self.history = []   # Claude keeps its own history; start this command clean
        answer = self._claude().handle(command, chat=chat, recent=recent, progress=progress)
        self.last_thinking = getattr(self._claude(), "last_thinking", "") or ""
        return answer

    def handle(self, command: str, chat: str | None = None, recent: list | None = None, progress=None) -> str:
        with self.lock:
            self.progress = progress
            if time.time() - self.last_used > IDLE_RESET_SECONDS or len(self.history) > 60 or chat != self.thread:
                self.history = []
                if chat != self.thread:
                    command = thread_switch_note(recent) + command
                self.thread = chat
            self.last_used = time.time()

            if _needs_claude(command):
                log.info("Routing straight to Claude (needs vision/web/exact search): %r", command[:70])
                return self._to_claude(command, chat, recent, progress)

            try:
                answer = self._run(command)
                if answer is not None:
                    return answer
                log.info("Hermes couldn't finish confidently; handing this one to Claude.")
            except requests.RequestException as e:
                log.info("Local Hermes server unreachable (%s); using Claude for this command.", e)
            except Exception:
                log.exception("Hermes failed; using Claude for this command.")
            return self._to_claude(command, chat, recent, progress)

    def _run(self, command: str) -> str | None:
        """One local agent loop. Returns the spoken answer, or None if the model gave up, needs vision, or
        produced nothing confident — in which case the caller falls back to Claude."""
        self.history.append({"role": "user", "content": command})
        steps: list[str] = []
        started = time.time()
        for _ in range(MAX_TOOL_ROUNDS):
            msg = self._chat()
            self.history.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                answer = (msg.get("content") or "").strip()
                if not answer:
                    return None   # nothing to say -> let Claude answer properly
                self.last_thinking = _steps_thoughts(steps)
                log.info("Hermes answered locally in %.1fs (no tokens).", time.time() - started)
                return answer
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                if name in VISION_TOOLS:
                    log.info("Hermes reached for %s (needs to see the screen); handing to Claude.", name)
                    return None
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
