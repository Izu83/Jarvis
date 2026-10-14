"""Claude decides which tools to run for a command, we run them, repeat until it answers.

Two interchangeable brains:
- ClaudeCodeBrain (default): runs the Claude Code CLI headless, so it uses your Claude Pro/Max
  subscription. Jarvis's tools reach it through jarvis/mcp_server.py.
- ApiBrain: calls the Claude API directly (faster, but needs a paid API key).
"""
import atexit
import json
import logging
import queue
import sys
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import tools
from .config import ROOT, settings

log = logging.getLogger("jarvis.brain")

SYSTEM_TEMPLATE = """You are Jarvis, a voice assistant that controls the user's Windows PC. \
Commands arrive from speech recognition on the PC microphone or the user's phone, so expect \
transcription mistakes and fix them sensibly ("cloud" usually means the Claude app, "spot a fly" means Spotify).

Act, don't ask: carry out the whole request with your tools, then reply with ONE short spoken-style \
sentence (no markdown, no lists) saying what you did or what went wrong. Latency-sensitive; be quick.

How to work:
- Call independent tools together in one turn (e.g. open several apps at once).
- "file folder" / "file explorer" / "files" means open_folder with no path. Use open_folder, not open_app, for Explorer.
- "Project X on the D disk" means a folder: check known folders, otherwise find_folder with root "D:\\\\".
- After opening windows that should go on a monitor, call arrange_windows ONCE with all their ids. \
"Organize them as normal" means the auto layout.
- If the user names a monitor by position (left, right, vertical, main, 1/2/3), map it using the list below.
- If something fails, try a sensible alternative once, then report it.

This PC:
{environment}"""

# Long idle gap = new conversation, so old context can't confuse new commands.
IDLE_RESET_SECONDS = 300
MAX_TOOL_ROUNDS = 12


def make_brain():
    return ApiBrain() if settings["brain"] == "api" else ClaudeCodeBrain()


class ClaudeCodeBrain:
    """Keeps one headless Claude Code session running and feeds it commands over stdin.

    Starting `claude` takes ~7 s, so the process is started ahead of time and reused: a command
    only pays for the model's own thinking. After a few idle minutes (or many commands) the
    session is swapped for a fresh, already-warm one so old context can't pile up.
    """

    MAX_COMMANDS_PER_SESSION = 25

    def __init__(self):
        exe = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")
        if not Path(exe).exists():
            raise RuntimeError("Claude Code CLI not found. Install it from https://claude.com/claude-code")
        self.exe = exe
        self.system = SYSTEM_TEMPLATE.format(environment=tools.environment_description())
        # An empty working folder, so no project files or CLAUDE.md get pulled into the prompt
        self.workdir = ROOT / ".brain"
        self.workdir.mkdir(exist_ok=True)
        self.mcp_config = self.workdir / "mcp.json"
        self.mcp_config.write_text(json.dumps({"mcpServers": {"jarvis": {
            "command": sys.executable,
            "args": [str(ROOT / "jarvis" / "mcp_server.py")],
        }}}), encoding="utf-8")
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.events: queue.Queue = queue.Queue()
        self.commands = 0
        self.last_used = time.time()
        self._spawn()
        threading.Thread(target=self._recycle_when_idle, daemon=True, name="brain-recycler").start()
        atexit.register(self._kill)

    def _spawn(self) -> None:
        cmd = [
            self.exe, "-p",
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--system-prompt", self.system,
            "--tools", "",  # no file editing / shell: only Jarvis's own tools
            "--mcp-config", str(self.mcp_config), "--strict-mcp-config",
            "--allowedTools", "mcp__jarvis",
            "--no-session-persistence",
            "--effort", settings["claude_code_effort"],
        ]
        if settings["claude_code_model"]:
            cmd += ["--model", settings["claude_code_model"]]
        self.proc = subprocess.Popen(
            cmd, cwd=self.workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.events = queue.Queue()
        self.commands = 0
        threading.Thread(target=self._read, args=(self.proc, self.events), daemon=True).start()
        log.info("Claude Code session started (pid %s).", self.proc.pid)

    @staticmethod
    def _read(proc: subprocess.Popen, events: queue.Queue) -> None:
        for line in proc.stdout:
            try:
                events.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        events.put(None)  # process ended

    def _kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()

    def _restart(self) -> None:
        self._kill()
        self._spawn()

    def _recycle_when_idle(self) -> None:
        while True:
            time.sleep(15)
            with self.lock:
                if self.commands and time.time() - self.last_used > IDLE_RESET_SECONDS:
                    log.info("Idle: starting a fresh Claude Code session.")
                    self._restart()

    def handle(self, command: str) -> str:
        with self.lock:
            if self.proc.poll() is not None or self.commands >= self.MAX_COMMANDS_PER_SESSION:
                self._restart()
            self.commands += 1
            started = time.time()
            try:
                answer = self._ask(command)
            finally:
                self.last_used = time.time()
            log.info("answered in %.1fs", time.time() - started)
            return answer

    def _ask(self, command: str) -> str:
        message = {"type": "user", "message": {"role": "user", "content": command}}
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except OSError:
            self._restart()
            return "My Claude Code session crashed; I restarted it. Please try again."
        deadline = time.time() + 180
        while True:
            try:
                event = self.events.get(timeout=max(1, deadline - time.time()))
            except queue.Empty:
                self._restart()
                return "That took too long, so I stopped."
            if event is None:
                self._spawn()
                return ("Claude Code stopped unexpectedly. If this keeps happening, run 'claude' in a "
                        "terminal and check you're logged in.")
            if event.get("type") != "result":
                continue
            usage = event.get("usage") or {}
            log.info("tokens: in=%s cached=%s+%s out=%s, model turns=%s",
                     usage.get("input_tokens"), usage.get("cache_read_input_tokens"),
                     usage.get("cache_creation_input_tokens"), usage.get("output_tokens"), event.get("num_turns"))
            if event.get("is_error"):
                log.error("Claude Code error: %s", event.get("result"))
                return event.get("result") or "Claude Code reported an error (maybe a usage limit)."
            return (event.get("result") or "Done.").strip()


class ApiBrain:
    def __init__(self):
        import anthropic

        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.system = SYSTEM_TEMPLATE.format(environment=tools.environment_description())
        self.history: list = []
        self.last_used = 0.0
        self.lock = threading.Lock()

    def handle(self, command: str) -> str:
        with self.lock:
            if time.time() - self.last_used > IDLE_RESET_SECONDS or len(self.history) > 60:
                self.history = []
            self.last_used = time.time()
            anthropic = self.anthropic
            try:
                return self._run(command)
            except anthropic.AuthenticationError:
                self.history = []
                return "My API key was rejected. Check ANTHROPIC_API_KEY in the .env file."
            except anthropic.RateLimitError:
                self.history = []
                return "I'm being rate limited, try again in a moment."
            except anthropic.APIStatusError as e:
                log.exception("API error")
                self.history = []
                return f"The Claude API returned an error, status {e.status_code}."
            except anthropic.APIConnectionError:
                self.history = []
                return "I can't reach the Claude API. Check the internet connection."

    def _run(self, command: str) -> str:
        self.history.append({"role": "user", "content": command})
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.beta.messages.create(
                model=settings["model"],
                max_tokens=8000,
                system=self.system,
                tools=tools.TOOLS,
                messages=self.history,
                output_config={"effort": settings["effort"]},
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            # Append the full content (incl. thinking blocks) so the conversation stays valid.
            self.history.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                self.history = []
                return "Sorry, I can't help with that one."

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                if response.stop_reason == "max_tokens":
                    self.history = []
                return " ".join(b.text for b in response.content if b.type == "text").strip() or "Done."

            results = []
            for call in tool_uses:  # sequential on purpose: launches are detected by watching new windows
                log.info("tool %s %s", call.name, json.dumps(call.input))
                try:
                    output, is_error = tools.run(call.name, call.input), False
                except Exception as e:  # report failures to Claude so it can recover
                    log.exception("tool %s failed", call.name)
                    output, is_error = f"Error: {e}", True
                log.info("  -> %s", output)
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": output, "is_error": is_error})
            self.history.append({"role": "user", "content": results})

        self.history = []
        return "That took too many steps, so I stopped."
