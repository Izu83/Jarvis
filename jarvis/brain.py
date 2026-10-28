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

from . import memory, tools, usage
from .config import ROOT, settings

log = logging.getLogger("jarvis.brain")

SYSTEM_TEMPLATE = """You are Jarvis, a voice assistant that controls the user's Windows PC. \
Commands arrive from speech recognition on the PC microphone or the user's phone, so expect \
transcription mistakes and fix them sensibly ("cloud" usually means the Claude app, "spot a fly" means Spotify).

Act, don't ask: carry out the WHOLE request to completion with your tools, then reply with ONE short spoken-style \
sentence (no markdown, no lists) saying what you did or what went wrong. Latency-sensitive; be quick. \
That one-sentence rule is for ACTIONS. When asked a question or to analyse / review / explain / summarise something, \
give a complete, genuinely useful answer in plain conversational text (the phone shows it) — dig in and be specific, \
never shrink real analysis to one line or claim you can only see a little.

Think about the full request before acting: break a multi-step command into its steps and do every one. \
For example "make a project called X in the Projects folder, open Claude and open that project in it" means: \
create_folder for the project, open the Claude app, then use ui_list/ui_click to open the project inside it. \
Don't stop after the first step. If a step's target isn't visible yet, open/focus the app first, then ui_list to \
find it. If you truly can't do a step, do the rest and say which part you couldn't.

How to work:
- Call independent tools together in one turn (e.g. open several apps at once).
- Create folders/projects with create_folder. "the Projects folder" / "my projects" is the known 'projects' folder below.
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
- Email: check_email lists the inbox already sorted into important / not important; say who the important ones are \
from and what about, and just count the rest. read_email opens one. Reply ONLY when the user asks, with reply_email. \
"Email Ana / send an email to X saying Y" means send_email: write it in the user's voice, `to` can be a name (it's \
looked up), attach files by path when asked (find them first). Both show the user the email and send ONLY if they \
allow it; never send email any other way. If the address can't be found, ask the user for it.
- "start/stop gesture control" (or "gestures"/"hand control") means gesture_control.
- "call me" / "ring me" / "call me and tell me X" means call_user (put X in the message). After you call, the user \
answers and talks to you by voice; keep spoken replies short and natural.
- Seeing: look_at_screen saves a screenshot; open it with the Read tool to see it. Use it when ui_list doesn't show what you need (games, browsers, custom apps), to verify something worked, or when asked what's on screen. Then act with mouse_click / mouse_scroll / mouse_drag using pixel positions from that picture, plus type_text and press_keys. After clicking, look again if you need to confirm. Screenshots cost tokens: use ui_list first when it works.
- Files the user attached from the phone appear as paths in the message: Read them (images included) and act on them.
- Questions about the world (news, facts, prices, weather, how-to): answer with WebSearch / WebFetch.
- Read / Glob / Grep let you look through files and code on the PC directly.
- Analysing a website or project: if it's a folder or code on the PC, Glob/Grep/Read through its files thoroughly \
before answering (you have full read access to every drive). If it's a live URL, WebFetch it (that returns the page's \
HTML and text, not the JavaScript-rendered view); to judge how it actually looks, also open it in the browser and \
look_at_screen. Then give a real, specific review — strengths, issues, concrete suggestions — not a vague "I can only \
see part of it".
- Timed reminders: remind_me for "remind me at 10:53", "notify me in 20 minutes", "call me at 7" (call=true). These reach the phone even when the app is closed. You CAN schedule notifications; never say you can't.
- You are the user's stand-in at this PC: if a request is doable with keyboard, mouse, screen and PowerShell, do it rather than saying you can't. Only stop for things that need their password, money or explicit consent.
- Do the whole job in ONE go. The user wants to say a task once and have it done: never stop halfway to ask \
"would you like me to…?" or hand them steps to do themselves when you could do them. When a detail is missing, \
choose a sensible default (file name, the Desktop or Documents folder, a subject line) and mention what you chose. \
Ask only when a wrong guess would matter (who to send something to, spending money, deleting their things).
- Websites and web apps (Instagram, Google Drive, Gmail on the web, YouTube, any site): open the URL with \
open_target (the user's browsers: Opera is the default, Zen is installed too), then look_at_screen and act with \
mouse_click / type_text / press_keys, looking again after each important step. They are signed in already in their \
browser. To upload a file on a website (e.g. Drive's New > File upload), click the upload control, then in the \
Windows file dialog type the file's full path into the File name box and press Enter.
- Making things: write_file creates any text file (code, notes, HTML, CSV, Markdown). For anything else (convert, \
zip, resize, Office files, downloads), write a script and run it with run_powershell. If the task needs a program \
that isn't installed, install it with `winget install --id <Id> -e --accept-source-agreements \
--accept-package-agreements` and say you did.
- Be fast: fewest steps that work, independent tools in one turn, ui_list before screenshots. Reply in the user's \
language.
- Modes are saved layouts the user names ("work mode"). "<name> mode" means run_mode. When the user \
describes a new mode or a change to one, build it with save_mode (list_modes first when editing) and say what you saved.

Memory: you are in an ongoing conversation. Messages may start with a bracketed note of what you remember about the \
user and the conversation so far (some of it handled by other parts of Jarvis): use it. Resolve follow-ups ("do it \
again", "close it", "send that to Ana", "the other one", "what did I just ask") from it instead of asking or \
guessing, and never act as if the conversation just started. Don't repeat earlier steps unless the user asks. When \
the user tells you something worth keeping (people, email addresses, preferences, "remember that…"), save it with \
remember; forget removes facts.

This PC:
{environment}"""

BUILTIN_TOOLS = "Read,Glob,Grep,WebSearch,WebFetch"


def _drives() -> list[str]:
    """Every fixed drive, so Read can open screenshots, uploads and the user's files anywhere."""
    import string
    return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]


# After a long idle gap the Claude session is swapped for a fresh one (so its context doesn't pile up); the fresh one
# is given the recent conversation from memory.py, so nothing the user said is forgotten.
IDLE_RESET_SECONDS = 300
MAX_TOOL_ROUNDS = 12
TASK_SECONDS = 600   # a real task (build something, fill in a website, record a video) may take a few minutes


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
    "gesture_control": lambda a: f"{'Starting' if a.get('action') != 'stop' else 'Stopping'} gesture control…",
    "call_user": lambda a: "Calling your phone…",
    "notify_when": lambda a: f"Setting a watch: {a.get('label', '')}…",
    "cancel_watches": lambda a: "Cancelling watches…",
    "send_message": lambda a: f"Writing a message to {a.get('to', 'them')}…",
    "send_file_to_phone": lambda a: "Sending the file to your phone…",
    "check_email": lambda a: "Checking your email…",
    "read_email": lambda a: "Reading the email…",
    "reply_email": lambda a: "Waiting for your OK to send the reply…",
    "send_email": lambda a: "Waiting for your OK to send the email…",
    "remember": lambda a: "Remembering that…",
    "forget": lambda a: "Forgetting that…",
    "look_at_screen": lambda a: "Looking at your screen…",
    "mouse_click": lambda a: "Clicking…",
    "mouse_scroll": lambda a: "Scrolling…",
    "mouse_drag": lambda a: "Dragging…",
    "remind_me": lambda a: "Setting a reminder…",
    "list_watches": lambda a: "Checking your reminders…",
    "clipboard": lambda a: "Using the clipboard…",
    "camera_photo": lambda a: "Taking a webcam photo…",
    "send_screens_to_phone": lambda a: "Sending your screens to your phone…",
    "Read": lambda a: "Looking at it…",
    "Glob": lambda a: "Searching files…",
    "Grep": lambda a: "Searching inside files…",
    "WebSearch": lambda a: f"Searching the web for “{a.get('query', '')}”…",
    "WebFetch": lambda a: "Reading a web page…",
}


# The small label on each step pill the phone shows ("Command · Opening Spotify…").
STEP_KINDS = {
    "App": ("open_app", "open_target", "gesture_control"),
    "Window": ("open_folder", "list_windows", "arrange_windows", "window_action"),
    "Inside app": ("ui_list", "ui_click"),
    "Keyboard": ("type_text", "press_keys", "clipboard"),
    "Mouse": ("mouse_click", "mouse_scroll", "mouse_drag"),
    "Screen": ("look_at_screen", "send_screens_to_phone"),
    "Files": ("find_folder", "scan_folder", "find_files", "read_file", "disk_usage", "cleanup_suggestions",
              "create_folder", "write_file", "move_path", "copy_path", "delete_paths", "clear_folder",
              "empty_recycle_bin", "Read", "Glob", "Grep"),
    "Command": ("run_powershell",),
    "System": ("system_info", "lock_pc", "power_action"),
    "Music": ("play_music", "media_key"),
    "Mode": ("list_modes", "run_mode", "save_mode", "delete_mode"),
    "Reminder": ("remind_me", "notify_when", "cancel_watches", "list_watches"),
    "Phone": ("call_user", "send_file_to_phone", "camera_photo"),
    "Message": ("send_message",),
    "Email": ("check_email", "read_email", "reply_email", "send_email"),
    "Memory": ("remember", "forget"),
    "Web": ("WebSearch", "WebFetch"),
}
_KIND_OF = {tool: kind for kind, names in STEP_KINDS.items() for tool in names}


def step_kind(tool: str) -> str:
    return _KIND_OF.get(tool.removeprefix("mcp__jarvis__"), "Step")


def describe_step(tool: str, args: dict) -> str:
    """'mcp__jarvis__open_app' + {'name': 'Claude'} -> 'Opening Claude…' for the phone's live status."""
    label = STEP_LABELS.get(tool.removeprefix("mcp__jarvis__"))
    try:
        return label(args) if label else "Working…"
    except Exception:
        return "Working…"


def _compose_thoughts(think_text: list[str], narration: list[str], steps: list[str], answer: str) -> str:
    """Build the 'thoughts' shown under a reply. Prefer real thinking; otherwise the working commentary and the
    steps taken. The final answer is dropped so the panel shows the reasoning, not a copy of the reply."""
    if think_text:
        return "\n\n".join(think_text)
    parts = [n for n in narration if n and n.strip() != (answer or "").strip()]
    body = "\n\n".join(parts)
    doing = [s for s in steps if s and s != "Working…"]
    if doing:
        log_lines = "\n".join("• " + s.rstrip("…") for s in doing)
        body = (body + "\n\n" if body else "") + "What I did:\n" + log_lines
    return body.strip()


def make_brain_named(name: str):
    """Build a specific brain by name: 'api', 'hermes' (free, local), or 'claude-code' (the default)."""
    if name == "api":
        return ApiBrain()
    if name == "hermes":
        from .hermes import HermesBrain   # imported lazily so hermes.py can import from this module
        return HermesBrain()
    return ClaudeCodeBrain()


def make_brain():
    return make_brain_named(settings["brain"])


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
        self.failed = False   # the last command got no real answer (limit, crash, timeout): a backup brain may try
        self.proc: subprocess.Popen | None = None
        self.events: queue.Queue = queue.Queue()
        self.commands = 0
        self.last_used = time.time()
        self.last_thinking = ""
        self._spawn()
        threading.Thread(target=self._recycle_when_idle, daemon=True, name="brain-recycler").start()
        atexit.register(self._kill)

    def _spawn(self) -> None:
        cmd = [
            self.exe, "-p",
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--system-prompt", self.system,
            # read-only built-ins (see images, search files, web); anything that changes the PC goes through
            # Jarvis's own tools, which ask the user where it matters
            "--tools", BUILTIN_TOOLS,
            "--mcp-config", str(self.mcp_config), "--strict-mcp-config",
            "--allowedTools", "mcp__jarvis," + BUILTIN_TOOLS,
            "--add-dir", *_drives(),
            "--no-session-persistence",
            "--effort", settings["claude_code_effort"],
        ]
        if settings["claude_code_model"]:
            cmd += ["--model", settings["claude_code_model"]]
        self.proc = subprocess.Popen(
            cmd, cwd=self.workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={**os.environ, "MCP_TOOL_TIMEOUT": "300000"},   # tools may wait up to 2 min for permission
            # errors="replace": one undecodable byte must not kill the reader thread (every later command would hang)
            text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.events = queue.Queue()
        self.commands = 0
        self.thread = None  # which chat the session's context belongs to
        self.seen = 0.0     # when this session last answered: exchanges after that were handled by someone else
        self.facts_seen = 0.0
        threading.Thread(target=self._read, args=(self.proc, self.events), daemon=True).start()
        log.info("Claude Code session started (pid %s).", self.proc.pid)

    @staticmethod
    def _read(proc: subprocess.Popen, events: queue.Queue) -> None:
        try:
            for line in proc.stdout:
                try:
                    events.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except Exception:
            log.exception("Lost the Claude Code output stream")
        events.put(None)  # process ended (or we can't read it any more)

    def close(self) -> None:
        self._kill()

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
            command = self._with_memory(command, chat, recent, fresh=self.commands == 0 or chat != self.thread)
            self.commands += 1
            started = time.time()
            self.last_thinking = ""
            try:
                answer = self._ask(command, progress)
            finally:
                self.last_used = self.seen = time.time()
            log.info("answered in %.1fs", time.time() - started)
            return answer

    def _with_memory(self, command: str, chat, recent, fresh: bool) -> str:
        """Put what this session doesn't know yet in front of the command: the facts and the recent conversation
        for a fresh session, or just what other brains handled since it last answered."""
        stamp = memory.facts_stamp()
        note = memory.context(chat, self.seen, recent, fresh, with_facts=fresh or stamp != self.facts_seen)
        self.thread, self.facts_seen = chat, stamp
        return note + command

    def _ask(self, command: str, progress=None) -> str:
        # On the Pro plan the CLI strips raw thinking text, so Jarvis's "thoughts" are built from its running
        # commentary (text it writes between actions) and the steps it takes — the honest process behind the answer.
        think_text: list[str] = []   # real thinking, when the model exposes it (API brain / future CLI)
        narration: list[str] = []    # text the model writes while working
        steps: list[str] = []        # the actions it took
        message = {"type": "user", "message": {"role": "user", "content": command}}
        self.failed = True   # until a proper answer arrives
        # Drop anything left over from the previous command (a late event, a stray result). Otherwise this command
        # would be "answered" by the old result and its own answer would land on the NEXT command, one step behind.
        stale = 0
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                break
            if ev is None:   # the session died between commands: start a fresh one first
                self._spawn()
                break
            stale += 1
        if stale:
            log.info("Dropped %d leftover event(s) from the previous command.", stale)
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except OSError:
            self._restart()
            return "My Claude Code session crashed; I restarted it. Please try again."
        deadline = time.time() + TASK_SECONDS
        while True:
            try:
                event = self.events.get(timeout=max(1, deadline - time.time()))
            except queue.Empty:
                self._restart()
                self.failed = False   # it was working, just too long: the weaker local models won't do better
                return "That took too long, so I stopped."
            if event is None:
                self._spawn()
                return ("Claude Code stopped unexpectedly. If this keeps happening, run 'claude' in a "
                        "terminal and check you're logged in.")
            if event.get("type") == "assistant":
                for block in event.get("message", {}).get("content", []):
                    bt = block.get("type")
                    if bt == "thinking":
                        chunk = (block.get("thinking") or "").strip()
                        if chunk:
                            think_text.append(chunk)
                    elif bt == "text":
                        chunk = (block.get("text") or "").strip()
                        if chunk:
                            narration.append(chunk)
                    elif bt == "tool_use":
                        step = describe_step(block.get("name", ""), block.get("input") or {})
                        steps.append(step)
                        if progress:
                            progress(step, step_kind(block.get("name", "")))
            if event.get("type") != "result":
                continue
            answer = (event.get("result") or "").strip()
            self.last_thinking = _compose_thoughts(think_text, narration, steps, answer)
            u = event.get("usage") or {}
            log.info("tokens: in=%s cached=%s+%s out=%s, model turns=%s",
                     u.get("input_tokens"), u.get("cache_read_input_tokens"),
                     u.get("cache_creation_input_tokens"), u.get("output_tokens"), event.get("num_turns"))
            usage.record_claude((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                                + (u.get("cache_creation_input_tokens") or 0), u.get("output_tokens") or 0)
            if event.get("is_error"):
                log.error("Claude Code error: %s", event.get("result"))
                return event.get("result") or "Claude Code reported an error (maybe a usage limit)."
            self.failed = False
            return (event.get("result") or "Done.").strip()


class ApiBrain:
    def close(self) -> None:
        pass

    def __init__(self):
        import anthropic

        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.system = SYSTEM_TEMPLATE.format(environment=tools.environment_description())
        self.history: list = []
        self.thread = None
        self.progress = None
        self.last_used = 0.0
        self.seen = 0.0
        self.facts_seen = 0.0
        self.failed = False
        self.last_thinking = ""
        self.lock = threading.Lock()

    _with_memory = ClaudeCodeBrain._with_memory

    def handle(self, command: str, chat: str | None = None, recent: list | None = None, progress=None) -> str:
        with self.lock:
            self.progress = progress
            if time.time() - self.last_used > IDLE_RESET_SECONDS or len(self.history) > 60 or chat != self.thread:
                self.history = []
            command = self._with_memory(command, chat, recent, fresh=not self.history)
            self.last_used = time.time()
            anthropic = self.anthropic
            self.failed = True
            try:
                answer = self._run(command)
                self.failed = False
                return answer
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
            finally:
                self.last_used = self.seen = time.time()

    def _run(self, command: str) -> str:
        self.history.append({"role": "user", "content": command})
        thoughts: list[str] = []
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
            u = getattr(response, "usage", None)
            if u:
                usage.record_claude((getattr(u, "input_tokens", 0) or 0)
                                    + (getattr(u, "cache_read_input_tokens", 0) or 0)
                                    + (getattr(u, "cache_creation_input_tokens", 0) or 0),
                                    getattr(u, "output_tokens", 0) or 0)
            for b in response.content:
                if b.type == "thinking" and getattr(b, "thinking", "").strip():
                    thoughts.append(b.thinking.strip())
            self.last_thinking = "\n\n".join(thoughts)

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
                    self.progress(describe_step(call.name, call.input), step_kind(call.name))
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
