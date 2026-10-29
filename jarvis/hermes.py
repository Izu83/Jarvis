"""A free, local brain: two models served by Ollama on this PC, working as a team, with Claude.

By default ("local_first": false) Claude does every command and the local team is the backstop: it steps in when
Claude can't answer (usage limit, no internet, a crash). The 8B models are too slow and too weak to be first in line
for real tasks (in practice Qwen3 timed out on most multi-step requests and Hermes looped or made things up), but
they keep Jarvis working when Claude is out. With "local_first": true the order flips, as described below.

- Hermes (hermes3:8b) is the quick one: simple, single-action commands ("open Spotify", "mute") go to it first.
- Qwen3 (qwen3:8b) is the thinker: it reasons before acting (its thinking shows up as Jarvis's thoughts), so
  multi-step requests, questions and anything Hermes gets stuck on go to it.
- Claude only gets what neither can do: screen vision, the web, exact file searches, or a local failure.

So a command goes:  simple -> Hermes -> (stuck) Qwen3 -> (stuck) Claude;  complex -> Qwen3 -> (stuck) Claude.
Both models are downloaded automatically the first time if they're missing (`local_auto_pull`). Set "qwen_model"
to "" to run Hermes alone, as before.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time

import requests

from . import health, memory, outbox, partial, stopping, tools, usage
from .brain import MAX_TOOL_ROUNDS, describe_step, make_brain_named, step_kind, system_prompt
from .config import ROOT, settings

log = logging.getLogger("jarvis.hermes")

# Tools that depend on actually seeing the screen — the text model can't, so these mean "let Claude do it".
VISION_TOOLS = {"look_at_screen", "mouse_click", "mouse_scroll", "mouse_drag"}

# Phrases that mean the command needs vision or the web (which the local tools can't do) — route to Claude first.
CLAUDE_HINTS = (
    "look at", "on my screen", "on the screen", "on screen", "see the screen", "read the screen",
    "what do you see", "what's on", "whats on", "screenshot", "take a screenshot",
    "search the web", "google ", "look it up", "look up ", "on the internet", "search online", "look online", "find online", "check online",
    "latest news", "the news", "weather", "stock price", "who won", "search for information",
)

# Exact file searches: Hermes tends to grab the wrong tool, so send these to Claude for a precise answer.
_FILE_SEARCH = re.compile(
    r"\b(find|search(?! the web)|locate|where'?s|where is|list (?:all |any )?)\b.*"
    r"\b(files?|pdfs?|documents?|folders?|photos?|images?|\.\w{2,4})\b", re.I)

# A command counts as "complex" (Qwen3 first) when it has several parts, is long, or asks for thinking.
_COMPLEX = re.compile(
    r"\b(and|then|also|after that|afterwards|but|before|if|unless)\b|[,;]|"
    r"\b(why|how (do|does|can|could|should|would|much|many)|explain|analy[sz]e|summari[sz]e|compare|review|"
    r"plan|figure out|work out|decide|write|draft|organi[sz]e|sort|clean up|tidy|which|what should)\b", re.I)
COMPLEX_WORDS = 14

# A follow-up leans on the conversation ("close it", "send that to her", "do it again") and a question may be about
# what Jarvis remembers ("what's my sister's email?"). Hermes can't keep track of a conversation (tested: it closed
# the wrong app and made up an address), Qwen3 can, so with any conversation or saved facts around these go to Qwen3.
_FOLLOWUP = re.compile(
    r"\b(it|its|it's|that|this|these|those|them|they|he|she|him|her|his|again|same|other|another|instead|too|"
    r"also|back|last|previous|earlier|before|one)\b|^(and|also|now|then|so|ok|okay|no|yes)\b|"
    r"^(what|who|where|when|which|did|do you|have you|remember)\b|\?\s*$", re.I)

# Earlier exchanges reach the local models as a written transcript in the system message (from memory.py, shared
# with Claude), never as replayed chat turns: replaying old tool calls made the small model answer a NEW command
# with "done" from the previous one, without acting.
CHAT_TIMEOUT = 90    # seconds per Hermes call; slower than this, the next brain in line takes the command
THINK_TIMEOUT = 180  # Qwen3 thinks first, so it gets longer

# A short nudge on top of the shared instructions, aimed at the smaller local model.
HERMES_NOTE = (
    "\n\nYou are a fast local model. Be decisive and pick the SINGLE most fitting tool: to find files use "
    "find_files (never disk_usage); to measure folder/disk sizes use disk_usage; to see what's open use "
    "list_windows. Never say you did something unless a tool result confirms it. Always finish with one short "
    "spoken sentence that states the ACTUAL result from the tools, not a vague 'I have provided that'.")

QWEN_NOTE = (
    "\n\nYou are the local planner. Think the request through briefly, then act with your tools step by step until "
    "EVERY part is done; don't stop after the first step. Use a tool result before you claim anything. If a tool "
    "fails, try one sensible alternative. Finish with a short plain answer (no markdown) that states what actually "
    "happened, or for a question, a complete and specific answer. Talk to the user as \"you\" (\"your sister\"), "
    "never \"the user\".")

_THINK_TAGS = re.compile(r"<think>(.*?)</think>", re.S)
# Hermes sometimes writes its tool call as text ('{"name": "window_action", "arguments": {...}}') instead of making it.
_TEXT_CALL = re.compile(r"^\s*(?:<tool_call>|```(?:json)?)?\s*(\{.*\}|\[.*\])\s*(?:</tool_call>|```)?\s*$", re.S)
# "Is there anything else you would like me to do?" adds nothing when spoken.
_FILLER = re.compile(r"\s*(Is there anything else[^.?!]*|Let me know if (you need|there's)[^.?!]*)[.?!]+\s*$", re.I)
# An answer that only promises ("Sure, I will send it") without a tool having run did nothing: hand it over.
_PROMISE = re.compile(r"\b(I will|I'll|let me|I am going to|I'm going to|please wait|one moment)\b", re.I)
# Giving up after a tool failed ("Apologies… could you provide the correct path?"): the next brain tries instead.
_GAVE_UP = re.compile(r"\b(apolog|sorry|confusion|could you (please )?(provide|give|tell)|correct (file )?path|"
                      r"seems (there|that) (might|may) (have been|be)|recheck)", re.I)
_MARKDOWN = re.compile(r"\*\*|__|`|^#+\s*", re.M)   # the answer is spoken, so no **bold** read out loud


def is_complex(command: str) -> bool:
    return bool(_COMPLEX.search(command)) or len(command.split()) > COMPLEX_WORDS


_status_cache: tuple[float, list] = (0.0, [])

# A model that errors twice in a row (Ollama's "500", often the graphics card being busy) is rested for a while,
# so every command doesn't first wait ~5 s for it to fail. Model name -> when it may try again.
STRIKES_TO_REST = 2
REST_SECONDS = 15 * 60
_resting: dict[str, float] = {}


def resting(model: str) -> bool:
    return _resting.get(model, 0) > time.time()


def _ran_out_of_memory() -> bool:
    """Did Ollama's last error come from Windows refusing memory? (the end of .brain/ollama.log says so)"""
    try:
        with open(ROOT / ".brain" / "ollama.log", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 6000))
            tail = f.read().decode("utf-8", "replace")
        return "Memory allocation failure" in tail or "out of memory" in tail.lower()
    except OSError:
        return False


def local_status() -> list[dict]:
    """Is each local model online? For the phone's Settings: [{name, model, role, state}], where state is
    "loaded" (in the GPU, answers right away), "ready" (installed, loads on first use), "missing" (not downloaded),
    "offline" (Ollama isn't running), "resting" (it kept failing, so Claude covers for it a while) or "off" (the
    local brain isn't in use). Cached for a few seconds."""
    global _status_cache
    if time.time() - _status_cache[0] < 5:
        return _status_cache[1]
    team = [("Hermes", settings["hermes_model"], "Quick one-step commands")]
    if settings.get("qwen_model"):
        team.append(("Qwen3", settings["qwen_model"], "Thinks through multi-step requests"))
    installed, loaded, up = set(), set(), False
    if settings["brain"] == "hermes":
        host = settings["ollama_host"].rstrip("/")
        try:
            for path, names in (("/api/tags", installed), ("/api/ps", loaded)):
                for m in requests.get(f"{host}{path}", timeout=1.5).json().get("models", []):
                    names.update({m.get("name", ""), m.get("model", "")})
            up = True
        except (requests.RequestException, ValueError):
            pass

    def state(model: str) -> str:
        if settings["brain"] != "hermes":
            return "off"
        if not up:
            return "offline"
        if resting(model):
            return "resting"
        has = lambda names: model in names or f"{model}:latest" in names
        return "loaded" if has(loaded) else "ready" if has(installed) else "missing"

    result = [{"name": n, "model": m, "role": r, "state": state(m)} for n, m, r in team]
    _status_cache = (time.time(), result)
    return result


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
    if "[the user attached these files" in c:   # photos and documents from the phone: only Claude can see them
        return True
    if any(h in c for h in CLAUDE_HINTS):
        return True
    return bool(_FILE_SEARCH.search(c))


class HermesBrain:
    def __init__(self):
        self.host = settings["ollama_host"].rstrip("/")
        self.model = settings["hermes_model"]
        # the team: key -> (Ollama model, display name, thinks first?)
        self.team = {"hermes": (self.model, "Hermes", False)}
        if settings.get("qwen_model"):
            self.team["qwen"] = (settings["qwen_model"], "Qwen3", True)
        self.tools = _ollama_tools()
        self.systems: dict[str, str] = {}   # built on first use, and again when the humour setting changes
        self._personality = None
        self.installed: set[str] = set()   # models Ollama has (refreshed now and then)
        self._checked = 0.0
        self._pulling: set[str] = set()
        self._no_think = False             # an Ollama too old for "think": Qwen3 then thinks inline
        self.background = ""   # facts + the recent conversation, for the command being handled
        self.progress = None
        self.last_used = 0.0
        self.last_thinking = ""
        self.lock = threading.Lock()
        self._fallback = None   # a Claude brain, created only if/when it's actually needed
        self._strikes: dict[str, int] = {}   # errors in a row, per team member (see REST_SECONDS)
        log.info("Local brain ready: %s via %s. Claude is the fallback.",
                 " + ".join(f"{name} ({m})" for m, name, _ in self.team.values()), self.host)
        threading.Thread(target=self._prepare, daemon=True, name="ollama-start").start()

    # ------------------------------------------------------------ models

    def _prepare(self) -> None:
        if self._ensure_server():
            self._ensure_models()

    def _refresh_installed(self, force: bool = False) -> None:
        if not force and time.time() - self._checked < 60:
            return
        try:
            tags = requests.get(f"{self.host}/api/tags", timeout=3).json().get("models", [])
            self.installed = {t.get("name", "") for t in tags} | {t.get("model", "") for t in tags}
            self._checked = time.time()
        except (requests.RequestException, ValueError):
            pass

    def _has(self, model: str) -> bool:
        names = self.installed
        return model in names or f"{model}:latest" in names

    def _ensure_models(self) -> None:
        """Download any team model Ollama doesn't have yet (in the background; a few GB each, once)."""
        self._refresh_installed(force=True)
        if not settings.get("local_auto_pull", True):
            return
        for model, name, _ in self.team.values():
            if not self._has(model) and model not in self._pulling:
                self._pulling.add(model)
                threading.Thread(target=self._pull, args=(model, name), daemon=True, name=f"pull-{name}").start()

    def _pull(self, model: str, name: str) -> None:
        log.info("Downloading %s (%s) for the local brain; it joins in when the download finishes.", name, model)
        last = -10
        try:
            with requests.post(f"{self.host}/api/pull", json={"model": model, "stream": True},
                               stream=True, timeout=(10, 600)) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    ev = json.loads(line)
                    if ev.get("error"):
                        raise RuntimeError(ev["error"])
                    if ev.get("total") and ev.get("completed"):
                        pct = int(ev["completed"] * 100 / ev["total"])
                        if pct >= last + 10:
                            last = pct
                            log.info("  %s: %d%%", name, pct)
            log.info("%s is installed and ready.", name)
        except Exception as e:
            log.warning("Couldn't download %s (%s): %s. Run `ollama pull %s` yourself.", name, model, e, model)
        finally:
            self._pulling.discard(model)
            self._refresh_installed(force=True)

    def _ready(self, key: str) -> bool:
        if key not in self.team or resting(self.team[key][0]):
            return False
        self._refresh_installed()
        return self._has(self.team[key][0])

    def _strike(self, key: str) -> None:
        """This team member errored. Twice in a row and it rests, so commands go straight to the next brain."""
        self._strikes[key] = self._strikes.get(key, 0) + 1
        if self._strikes[key] >= STRIKES_TO_REST:
            self._strikes[key] = 0
            _resting[self.team[key][0]] = time.time() + REST_SECONDS
            log.warning("%s keeps failing; skipping it for %d minutes (Claude covers). See .brain/ollama.log.",
                        self.team[key][1], REST_SECONDS // 60)
            health.problem("local_" + key, f"The local model {self.team[key][1]} keeps failing, so Claude covers "
                           f"for it for {REST_SECONDS // 60} minutes. If it keeps happening, restart Jarvis from the "
                           f"tray; .brain/ollama.log on the PC says why.")

    def _plan(self, command: str, followup: bool = False) -> list[str]:
        """Which local models try this command, in order."""
        order = ["qwen"] if followup or is_complex(command) else ["hermes", "qwen"]
        ready = [k for k in order if self._ready(k)]
        if not ready and self._ready("hermes"):   # complex but Qwen3 isn't there (yet): Hermes has a go
            ready = ["hermes"]
        return ready

    def _server_up(self) -> bool:
        try:
            requests.get(f"{self.host}/api/tags", timeout=2)
            return True
        except requests.RequestException:
            return False

    def _ensure_server(self) -> bool:
        """Start the local Ollama server if it isn't running, so Hermes doesn't silently hand everything to Claude."""
        if self._server_up():
            return True
        if not any(h in self.host for h in ("127.0.0.1", "localhost")):
            return False   # a remote server isn't ours to start
        exe = shutil.which("ollama") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        if not os.path.exists(exe):
            log.warning("Ollama isn't installed (%s); Hermes can't run, so Claude handles commands.", exe)
            return False
        log.info("Ollama wasn't running; starting it for Hermes.")
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        # Its output goes to .brain/ollama.log, so when a model errors the reason can be looked up
        out = ROOT / ".brain" / "ollama.log"
        try:
            if out.exists() and out.stat().st_size > 5_000_000:
                out.unlink()
            with open(out, "ab") as f:
                subprocess.Popen([exe, "serve"], creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                                 stdout=f, stderr=subprocess.STDOUT)
        except OSError:
            log.exception("Couldn't start Ollama.")
            return False
        for _ in range(30):
            time.sleep(0.5)
            if self._server_up():
                log.info("Ollama is up.")
                health.fixed("ollama", "The local models are running again.")
                return True
        log.warning("Started Ollama but it didn't answer in time.")
        if settings.get("local_first"):
            health.problem("ollama", "The local models' server (Ollama) won't start, so Claude does everything for "
                           "now. Restarting the PC usually fixes it.")
        return False

    def close(self) -> None:
        if self._fallback is not None:
            self._fallback.close()

    def _claude(self):
        if self._fallback is None:
            log.info("Spinning up the Claude fallback brain.")
            self._fallback = make_brain_named(settings["hermes_fallback"])
        return self._fallback

    def _to_claude(self, command, chat, recent, progress) -> str:
        # Claude gets the conversation from memory.py itself, including what the local models handled.
        answer = self._claude().handle(command, chat=chat, recent=recent, progress=progress)
        self.last_thinking = getattr(self._claude(), "last_thinking", "") or ""
        return answer

    def handle(self, command: str, chat: str | None = None, recent: list | None = None, progress=None) -> str:
        original = command   # routed on what the user said, not on the conversation note added below
        with self.lock:
            self.progress = progress
            self.last_used = time.time()

            if not settings.get("local_first", False):
                answer = self._to_claude(command, chat, recent, progress)
                if not getattr(self._claude(), "failed", False):
                    return answer
                log.info("Claude couldn't answer (%r); the local team tries.", answer[:80])
                self._say("Claude can't answer right now, so the local brain is trying…")
                return self._local(original, command, chat, recent) or answer

            if _needs_claude(original):
                log.info("Routing straight to Claude (needs vision/web/exact search): %r", command[:70])
                return self._to_claude(command, chat, recent, progress)

            answer = self._local(original, command, chat, recent)
            if answer is not None:
                return answer
            self._say("Handing this one to Claude…")
            return self._to_claude(command, chat, recent, progress)

    def _local(self, original: str, command: str, chat, recent) -> str | None:
        """Let the local team try, in order. None if none of them could do it."""
        sent = outbox.last_sent()
        if sent:   # so "send it again" / "send the video" means this file, not a made-up path
            command += f"\n\n[The last file Jarvis sent to the phone: {sent}]"
        self.background = memory.background(chat, phone_recent=recent)
        followup = bool(self.background) and bool(_FOLLOWUP.search(original))
        plan = self._plan(original, followup)
        if not plan and self._ensure_server():   # Ollama was down: it's up now, look again
            self._refresh_installed(force=True)
            plan = self._plan(original, followup)
        tried = []
        for key in plan:
            name = self.team[key][1]
            if tried:
                self._say(f"{tried[-1]} got stuck, so {name} is taking over…")
            elif key == "qwen":
                self._say(f"{name} is thinking it through…")
            try:
                answer = self._run(key, command)
                self._strikes[key] = 0   # it answered (even if not confidently): it works
                health.fixed("local_" + key)
                if answer is not None:
                    return answer
                log.info("%s couldn't finish confidently.", name)
            except requests.RequestException as e:
                log.info("%s didn't answer (%s).", name, e)
                self._strike(key)
                if _ran_out_of_memory():
                    health.out_of_memory()
            except Exception:
                log.exception("%s failed.", name)
                self._strike(key)
            tried.append(name)
        return None

    def _say(self, text: str) -> None:
        """A step pill showing which brain is on it, so you can see the team at work."""
        if self.progress:
            try:
                self.progress(text, "Brain")
            except Exception:
                log.debug("progress callback failed", exc_info=True)

    def _run(self, key: str, command: str) -> str | None:
        """One local agent loop with one team member. Returns the spoken answer, or None if the model gave up,
        needs vision, or produced nothing confident — in which case the next brain in line takes over."""
        model, name, think = self.team[key]
        turn = [{"role": "user", "content": command}]   # this command's messages (tool calls and results)
        steps: list[str] = []
        thoughts: list[str] = []
        failed = False   # did the latest tool call fail?
        started = time.time()
        for _ in range(MAX_TOOL_ROUNDS):
            stopping.check()
            msg = self._chat(key, model, think, turn)
            thought = (msg.pop("thinking", "") or "").strip()
            content = msg.get("content") or ""
            inline = _THINK_TAGS.findall(content)   # thinking written into the text (older Ollama)
            if inline:
                thought = (thought + "\n" + "\n".join(t.strip() for t in inline)).strip()
                msg["content"] = content = _THINK_TAGS.sub("", content)
            if thought:
                thoughts.append(thought)
            turn.append(msg)
            calls = msg.get("tool_calls") or _text_calls(content)
            if not calls:
                answer = _FILLER.sub("", _MARKDOWN.sub("", content)).strip()
                if not answer:
                    return None   # nothing to say -> the next brain answers properly
                if (not steps and _PROMISE.search(answer)) or (failed and _GAVE_UP.search(answer)):
                    log.info("%s promised or gave up without doing it (%r); handing over.", name, answer[:80])
                    return None
                self.last_thinking = "\n\n".join(thoughts + ([_steps_thoughts(steps)] if steps else []))
                log.info("%s answered locally in %.1fs (no tokens).", name, time.time() - started)
                usage.record_local()
                return answer
            for call in calls:
                fn = call.get("function", {})
                tool = fn.get("name", "")
                if tool in VISION_TOOLS:
                    log.info("%s reached for %s (needs to see the screen); handing to Claude.", name, tool)
                    return None
                raw = fn.get("arguments", {})
                args = raw if isinstance(raw, dict) else _loads(raw)
                step = describe_step(tool, args)
                steps.append(step)
                if self.progress:
                    self.progress(step, step_kind(tool))
                log.info("[%s] tool %s %s", name, tool, json.dumps(args)[:400])
                stopping.check()
                try:
                    output = tools.run(tool, args)
                except Exception as e:
                    log.exception("tool %s failed", tool)
                    output = f"Error: {e}"
                log.info("  -> %s", str(output)[:300])
                failed = str(output).startswith("Error")
                turn.append({"role": "tool", "content": str(output)})
        return None   # ran out of rounds -> let Claude take over

    def _chat(self, key: str, model: str, think: bool, turn: list) -> dict:
        if self._personality != memory.personality():
            base = system_prompt()
            self._personality = memory.personality()
            self.systems = {"hermes": base + HERMES_NOTE, "qwen": base + QWEN_NOTE}
        system = self.systems[key]
        if self.background:
            system += ("\n\n" + self.background + "\n\nThe user's latest message is the NEW request: do that one. "
                       "The conversation above is already done; don't redo it unless asked.")
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *turn],
            "tools": self.tools,
            "stream": True,   # so the answer shows on the phone as it's written (partial.py)
            "keep_alive": "30m",   # keep the model warm in VRAM between commands
            # The tool list alone is ~6.5k tokens, so a small window silently cut off the instructions.
            "options": {"temperature": 0.6 if think else 0.3, "num_ctx": int(settings.get("local_ctx", 16384))},
        }
        if think and not self._no_think:
            body["think"] = True
        # stopping.wait: a slow model answer must not hold up the stop button
        try:
            return stopping.wait(self._stream, body, THINK_TIMEOUT if think else CHAT_TIMEOUT)
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 400 or "think" not in body:
                raise
            self._no_think = True   # this Ollama doesn't know "think": ask again without it
            body.pop("think")
            return stopping.wait(self._stream, body, THINK_TIMEOUT)

    def _stream(self, body: dict, timeout: float) -> dict:
        """One streamed /api/chat call, put back together into one message. The visible text goes to the phone as
        it arrives, unless it looks like a tool call written out as text."""
        msg = {"role": "assistant", "content": "", "thinking": ""}
        calls: list = []
        start = time.time()
        with requests.post(f"{self.host}/api/chat", json=body, stream=True, timeout=(10, timeout)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                if time.time() - start > timeout:
                    raise requests.Timeout(f"no full answer within {timeout:.0f} s")
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise requests.HTTPError(chunk["error"])
                part = chunk.get("message") or {}
                msg["content"] += part.get("content") or ""
                msg["thinking"] += part.get("thinking") or ""
                calls += part.get("tool_calls") or []
                text = msg["content"].lstrip()
                if text and not calls and not text.startswith(("{", "[", "<", "`")):
                    partial.update(_MARKDOWN.sub("", text))
                if chunk.get("done"):
                    break
        if calls:
            msg["tool_calls"] = calls
            partial.clear()
        return msg


def _text_calls(content: str) -> list[dict]:
    """Tool calls the model wrote out as JSON text instead of making them, in the usual tool_calls shape."""
    m = _TEXT_CALL.match(content or "")
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    names = {t["name"] for t in tools.TOOLS}
    out = []
    for c in data if isinstance(data, list) else [data]:
        if isinstance(c, dict) and c.get("name") in names:
            args = c.get("arguments", c.get("parameters", {}))
            out.append({"function": {"name": c["name"], "arguments": args if isinstance(args, dict) else _loads(args)}})
    return out


def _loads(raw) -> dict:
    try:
        return json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except Exception:
        return {}


def _steps_thoughts(steps: list[str]) -> str:
    doing = [s for s in steps if s and s != "Working…"]
    return ("What I did:\n" + "\n".join("• " + s.rstrip("…") for s in doing)) if doing else ""
