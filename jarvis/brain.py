"""Claude decides which tools to run for a command, we run them, repeat until it answers.

Two interchangeable brains:
- ClaudeCodeBrain (default): runs the Claude Code CLI headless, so it uses your Claude Pro/Max
  subscription. Jarvis's tools reach it through jarvis/mcp_server.py.
- ApiBrain: calls the Claude API directly (faster, but needs a paid API key).
"""
import atexit
import json
import os
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
- Requests about something INSIDE an app ("open Claude's Jarvis project", "open my chat with X", "go to the \
settings tab") mean: open/focus the app, then ui_list with a short filter to find the control, then ui_click it. \
Possessives like "Claude's Jarvis project" name the app (Claude) and the thing inside it (Jarvis). If a filter finds \
nothing, try a shorter one or no filter. Report what you clicked.
- Never click or press anything that sends messages, deletes, buys, posts or changes accounts/settings unless the \
user explicitly asked for exactly that.
- Files: scan_folder / find_files / read_file / disk_usage / cleanup_suggestions to look; create_folder, write_file, move_path, copy_path to organise. To delete or clear, use delete_paths / clear_folder / empty_recycle_bin: they ask the user for permission themselves, so just call them (don't ask in chat first). Deleting goes to the Recycle Bin unless the user says "permanently" or it's temp/cache junk; remember the Recycle Bin doesn't free disk space. If the user declines, stop and say so; never work around a "no".
- For anything else on the PC use run_powershell (the user approves each command). Keep commands short and explain them in `why`.
- Music: play_music for "play <song/artist/playlist>"; media_key for pause, skip and volume.
- Power: power_action for sleep/restart/shutdown/sign out (it asks permission). lock_pc just locks.
- Sending a message in a chat app: open the app and the conversation with open_app/ui_list/ui_click, then call \
send_message with the text. It types, shows the user the message, and sends only if they allow it; never send any \
other way.
- "Send me <file>" means send_file_to_phone after finding the file.
- Modes are saved layouts the user names ("work mode"). "<name> mode" means run_mode. When the user \
describes a new mode or a change to one, build it with save_mode (list_modes first when editing) and say what you saved.

This PC:
{environment}"""

# Long idle gap = new conversation, so old context can't confuse new commands.
IDLE_RESET_SECONDS = 300
MAX_TOOL_ROUNDS = 12


def thread_switch_note(recent: list | None) -> str:
    """The phone has separate chats; when the user switches, tell Claude which earlier messages count."""
    if not recent:
        return "[New conversation. Earlier messages in this session are unrelated.]\n\n"
    lines = [f"{'User' if m.get('who') == 'me' else 'Jarvis'}: {str(m.get('text', ''))[:300]}" for m in recent[-6:]]
    return "[Switched conversation. Only these earlier messages belong to it:\n" + "\n".join(lines) + "]\n\n"


STEP_LABELS = {
    "open_app": lambda a: f"Opening {a.get('name', 'the app')}…",
    "open_folder": lambda a: "Opening File Explorer…",
    "find_folder": lambda a: f"Looking for “{a.get('name', '')}”…",
    "open_target": lambda a: "Opening it…",
    "list_windows": lambda a: "Checking what's open…",
    "arrange_windows": lambda a: f"Arranging windows on monitor {a.get('monitor', '')}…",
    "window_action": lambda a: f"{str(a.get('action', '')).title()} {a.get('window', '')}…",
    "ui_list": lambda a: f"Looking inside {a.get('window', 'the app')}…",
    "ui_click": lambda a: f"Clicking “{a.get('name', '')}”…",
    "type_text": lambda a: "Typing…",
    "press_keys": lambda a: f"Pressing {a.get('keys', '')}…",
    "list_modes": lambda a: "Checking your modes…",
    "run_mode": lambda a: f"Starting {a.get('name', '')} mode…",
    "save_mode": lambda a: f"Saving {a.get('name', '')} mode…",
    "delete_mode": lambda a: f"Deleting {a.get('name', '')} mode…",
    "media_key": lambda a: "Controlling music…",
    "lock_pc": lambda a: "Locking your PC…",
    "system_info": lambda a: "Checking your PC…",
    "scan_folder": lambda a: f"Looking in {a.get('path', 'the folder')}…",
    "find_files": lambda a: f"Searching for “{a.get('pattern', '')}”…",
    "read_file": lambda a: "Reading the file…",
    "disk_usage": lambda a: "Measuring what uses space…",
    "cleanup_suggestions": lambda a: "Looking for things to clean up…",
    "create_folder": lambda a: "Creating the folder…",
    "write_file": lambda a: "Writing the file…",
    "move_path": lambda a: "Moving it…",
    "copy_path": lambda a: "Copying it…",
    "delete_paths": lambda a: "Waiting for your permission to delete…",
    "clear_folder": lambda a: "Waiting for your permission to clear it…",
    "empty_recycle_bin": lambda a: "Waiting for your permission…",
    "run_powershell": lambda a: "Waiting for your permission to run a command…",
    "power_action": lambda a: f"{str(a.get('action', '')).replace('_', ' ').title()}…",
    "play_music": lambda a: f"Playing {a.get('query', 'music')}…",
    "send_message": lambda a: f"Writing a message to {a.get('to', 'them')}…",
    "send_file_to_phone": lambda a: "Sending the file to your phone…",
}


def describe_step(tool: str, args: dict) -> str:
    """'mcp__jarvis__open_app' + {'name': 'Claude'} -> 'Opening Claude…' for the phone's live status."""
    label = STEP_LABELS.get(tool.removeprefix("mcp__jarvis__"))
    try:
        return label(args) if label else "Working…"
    except Exception:
        return "Working…"


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
            # lets the tools ask the user for permission through this Jarvis process (approval.py)
            "env": {k: v for k, v in os.environ.items() if k.startswith("JARVIS_APPROVAL_")},
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
            env={**os.environ, "MCP_TOOL_TIMEOUT": "300000"},   # tools may wait up to 2 min for permission
            text=True, encoding="utf-8", bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.events = queue.Queue()
        self.commands = 0
        self.thread = None  # which chat the session's context belongs to
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

    def handle(self, command: str, chat: str | None = None, recent: list | None = None, progress=None) -> str:
        with self.lock:
            if self.proc.poll() is not None or self.commands >= self.MAX_COMMANDS_PER_SESSION:
                self._restart()
            if chat != self.thread:
                command = thread_switch_note(recent) + command
                self.thread = chat
            self.commands += 1
            started = time.time()
            try:
                answer = self._ask(command, progress)
            finally:
                self.last_used = time.time()
            log.info("answered in %.1fs", time.time() - started)
            return answer

    def _ask(self, command: str, progress=None) -> str:
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
            if event.get("type") == "assistant" and progress:
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        progress(describe_step(block.get("name", ""), block.get("input") or {}))
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
        self.thread = None
        self.progress = None
        self.last_used = 0.0
        self.lock = threading.Lock()

    def handle(self, command: str, chat: str | None = None, recent: list | None = None, progress=None) -> str:
        with self.lock:
            self.progress = progress
            if time.time() - self.last_used > IDLE_RESET_SECONDS or len(self.history) > 60 or chat != self.thread:
                self.history = []
                if chat != self.thread:
                    command = thread_switch_note(recent) + command
                self.thread = chat
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
                if self.progress:
                    self.progress(describe_step(call.name, call.input))
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
