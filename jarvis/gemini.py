"""Gemini as a brain, through the Gemini CLI signed in with your Google account, so it runs on your Gemini plan
(no API key, no cost), the way ClaudeCodeBrain runs Claude Code on your Claude plan.

- One `gemini -p` run per command (headless, stream-json), in .brain/gemini. Jarvis's tools reach it through the
  same MCP server as Claude (jarvis/mcp_server.py); the ones that change things still ask you first (approval.py).
- Its own built-in tools are limited to reading files and Google Search / web pages: no shell, no file writing.
- It's the best pick for anything current (Google Search is built in), YouTube videos and long documents, and it
  takes everyday questions and chat so your Claude limit lasts (router.py decides).

Install once: `npm install -g @google/gemini-cli`, then run `gemini.cmd` and sign in with Google.
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import memory, partial, stopping, usage
from .brain import TASK_SECONDS, _drives, describe_step, step_kind, system_prompt
from .config import ROOT, settings

log = logging.getLogger("jarvis.gemini")

WORKDIR = ROOT / ".brain" / "gemini"
# Gemini's own tools Jarvis lets it use: read-only, plus Google Search and fetching web pages
CORE_TOOLS = ["read_file", "read_many_files", "list_directory", "glob", "search_file_content", "google_web_search",
              "web_fetch"]
NO_TOOLS = ["run_shell_command", "write_file", "replace", "save_memory"]


def exe() -> str | None:
    for name in ("gemini.cmd", "gemini"):
        found = shutil.which(name)
        if found:
            return found
    guess = Path(os.environ.get("APPDATA", "")) / "npm" / "gemini.cmd"
    return str(guess) if guess.exists() else None


def signed_in() -> bool:
    """The CLI keeps your Google sign-in in ~/.gemini; without it, it can't answer."""
    home = Path.home() / ".gemini"
    return any((home / f).exists() for f in ("oauth_creds.json", "google_accounts.json"))


def ready() -> bool:
    return bool(exe()) and signed_in()


class GeminiBrain:
    def __init__(self):
        self.exe = exe()
        if not self.exe:
            raise RuntimeError("Gemini CLI isn't installed: npm install -g @google/gemini-cli")
        self.lock = threading.Lock()
        self.failed = False        # the last command got no real answer (limit, sign-in, crash): another brain may try
        self.last_thinking = ""
        self.busy = False
        self.proc: subprocess.Popen | None = None
        self._prepare()
        stopping.on_stop(self._interrupt)

    def _prepare(self) -> None:
        """Its workspace: Jarvis's tools (MCP), the safe built-ins only, and Jarvis's own instructions."""
        (WORKDIR / ".gemini").mkdir(parents=True, exist_ok=True)
        conf = {
            "mcpServers": {"jarvis": {
                "command": sys.executable, "args": [str(ROOT / "jarvis" / "mcp_server.py")],
                "env": {k: v for k, v in os.environ.items() if k.startswith("JARVIS_APPROVAL_")},
                "timeout": 300000, "trust": True,   # the tools ask you themselves where it matters
            }},
            "tools": {"core": CORE_TOOLS, "exclude": NO_TOOLS},
        }
        (WORKDIR / ".gemini" / "settings.json").write_text(json.dumps(conf, indent=1), encoding="utf-8")
        (WORKDIR / "system.md").write_text(system_prompt(), encoding="utf-8")

    def close(self) -> None:
        self._interrupt()

    def _interrupt(self) -> None:
        if self.busy and self.proc and self.proc.poll() is None:
            log.info("Stopping Gemini (pid %s).", self.proc.pid)
            stopping.kill_tree(self.proc.pid)

    def handle(self, command: str, chat: str | None = None, recent: list | None = None, progress=None) -> str:
        with self.lock:
            self.failed, self.last_thinking, self.busy = True, "", True
            note = memory.context(chat, 0.0, recent, True, with_facts=True)   # it starts fresh every time
            try:
                return self._run(note + command, progress)
            finally:
                self.busy = False

    def _run(self, prompt: str, progress) -> str:
        cmd = [self.exe, "-p", "(Reply to the message above.)", "-o", "stream-json", "--approval-mode", "yolo",
               "--skip-trust", "--allowed-mcp-server-names", "jarvis", "--include-directories", ",".join(_drives())]
        if settings.get("gemini_model"):
            cmd += ["-m", settings["gemini_model"]]
        env = {**os.environ, "GEMINI_SYSTEM_MD": str(WORKDIR / "system.md"), "NO_COLOR": "1", "MCP_TOOL_TIMEOUT": "300000"}
        started = time.time()
        self.proc = subprocess.Popen(cmd, cwd=WORKDIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=env, text=True, encoding="utf-8", errors="replace",
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        errors: list[str] = []
        threading.Thread(target=lambda: errors.extend(self.proc.stderr.readlines()), daemon=True).start()
        try:
            self.proc.stdin.write(prompt)
            self.proc.stdin.close()
        except OSError:
            pass
        timer = threading.Timer(TASK_SECONDS, self._interrupt)
        timer.start()
        text, tokens, problem = "", (0, 0), ""
        try:
            for line in self.proc.stdout:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = ev.get("type")
                if kind == "message" and ev.get("role") == "assistant":
                    text = text + ev.get("content", "") if ev.get("delta") else ev.get("content", text)
                    partial.update(text)
                elif kind == "tool_use":
                    name = str(ev.get("tool_name", "")).removeprefix("jarvis__")
                    if progress:
                        progress(describe_step(name, ev.get("parameters") or {}), step_kind(name))
                    if text:   # that was commentary before an action, not the answer
                        self.last_thinking += text.strip() + "\n"
                        text = ""
                        partial.clear()
                elif kind == "error":
                    problem = str(ev.get("message") or ev)
                elif kind == "result":
                    stats = ev.get("stats") or {}
                    tokens = (stats.get("input_tokens", 0), stats.get("output_tokens", 0))
                    if ev.get("status") not in (None, "success"):
                        problem = problem or str((ev.get("error") or {}).get("message") or ev.get("status"))
            self.proc.wait(timeout=10)
        finally:
            timer.cancel()
            partial.clear()
        if stopping.requested():
            self.failed = False
            raise stopping.Stopped()
        usage.record_other("gemini", *tokens)
        if text.strip() and not problem:
            self.failed = False
            log.info("Gemini answered in %.1fs.", time.time() - started)
            return text.strip()
        why = problem or "".join(errors[-5:]).strip() or f"exit code {self.proc.returncode}"
        log.warning("Gemini couldn't answer: %s", why[:300])
        low = why.lower()
        if "auth" in low or "login" in low or "credential" in low:
            return "Gemini isn't signed in. On the PC, run gemini.cmd once and sign in with Google."
        if "quota" in low or "429" in low or "rate" in low or "exhausted" in low:
            return "Gemini has hit its limit for now."
        return "Gemini couldn't answer that."
