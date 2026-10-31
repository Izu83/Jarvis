"""Jarvis's own hidden browser, for website jobs (Windy, Instagram, forms…) without touching your screen.

Microsoft's Playwright MCP (installed in .brain/browser-mcp) gives Claude a headless Chrome: no window, nothing in
the taskbar. It opens pages, reads them, clicks, types, uploads and takes screenshots for itself, and closes after a
few idle minutes. It keeps its own profile (.brain/browser-profile), separate from your browsers, so it's signed in
only where you've signed it in: open_login_window (or the tray, or `run.bat --browser`) shows that same profile in a
normal Chrome window on your screen, you log in yourself, close it, and from then on Jarvis stays logged in, hidden.

It presents itself as your Chrome version (not "HeadlessChrome"), so sites treat it like a normal browser.
"""
import logging
import os
import subprocess
import time
from pathlib import Path

from .config import ROOT, settings

log = logging.getLogger("jarvis.hidden_browser")

MCP_CLI = ROOT / ".brain" / "browser-mcp" / "node_modules" / "@playwright" / "mcp" / "cli.js"
PROFILE = ROOT / ".brain" / "browser-profile"
CHROME = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe"
IDLE_MS = 5 * 60 * 1000   # it closes after 5 idle minutes, and opens again on the next website job


def ready() -> bool:
    return settings.get("hidden_browser", True) and MCP_CLI.exists() and CHROME.exists()


def _chrome_version() -> str:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              f"(Get-Item '{CHROME}').VersionInfo.ProductVersion"],
                             capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        return out.stdout.strip() or "140.0.0.0"
    except Exception:
        return "140.0.0.0"


def mcp_server() -> dict | None:
    """The MCP server entry for Claude Code's config, or None when it isn't installed."""
    if not ready():
        return None
    major = _chrome_version().split(".")[0]
    agent = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             f"Chrome/{major}.0.0.0 Safari/537.36")
    PROFILE.mkdir(parents=True, exist_ok=True)
    return {"command": "node", "args": [
        str(MCP_CLI), "--browser", "chrome", "--headless", "--user-data-dir", str(PROFILE),
        "--viewport-size", "1366x900", "--user-agent", agent, "--idle-timeout", str(IDLE_MS),
        "--output-dir", str(ROOT / ".brain" / "browser-output"),
    ]}


def _close_hidden() -> None:
    """The hidden browser and a visible window can't share the profile at once: close the hidden one."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object "
                              f"{{ $_.CommandLine -like '*{PROFILE.name}*' }} | ForEach-Object {{ $_.ProcessId }}"],
                             capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        for pid in out.stdout.split():
            subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        log.debug("couldn't close the hidden browser", exc_info=True)


def login_window(url: str = "") -> str:
    """Show Jarvis's browser profile in a normal Chrome window, so you can sign in to a site yourself."""
    if not CHROME.exists():
        return "Google Chrome isn't installed, so Jarvis's browser can't be shown."
    _close_hidden()
    time.sleep(.5)
    PROFILE.mkdir(parents=True, exist_ok=True)
    args = [str(CHROME), f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check", "--new-window"]
    if url:
        args.append(url)
    subprocess.Popen(args, close_fds=True)
    log.info("Showing Jarvis's browser for a log-in%s.", f" ({url})" if url else "")
    return ("I've opened my own browser on your screen" + (f" at {url}" if url else "") + ". Log in there yourself "
            "(I never type your passwords), then close that window. After that I'll use it hidden, already logged in.")
