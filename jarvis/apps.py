"""Launching apps, folders and URLs, and finding the window each launch produced."""
import difflib
import json
import os
import string
import subprocess
import time
import webbrowser
from pathlib import Path

from . import windows as win

_start_apps: list[dict] | None = None

# Web apps people "open" like programs. When no installed app matches, "open instagram" opens the site instead.
SITES = {
    "instagram": "https://www.instagram.com/", "facebook": "https://www.facebook.com/",
    "messenger": "https://www.messenger.com/", "youtube": "https://www.youtube.com/",
    "youtube music": "https://music.youtube.com/", "gmail": "https://mail.google.com/",
    "google": "https://www.google.com/", "google drive": "https://drive.google.com/", "drive": "https://drive.google.com/",
    "google docs": "https://docs.google.com/", "google sheets": "https://sheets.google.com/",
    "google slides": "https://slides.google.com/", "google calendar": "https://calendar.google.com/",
    "google maps": "https://maps.google.com/", "maps": "https://maps.google.com/",
    "google photos": "https://photos.google.com/", "google translate": "https://translate.google.com/",
    "tiktok": "https://www.tiktok.com/", "twitter": "https://x.com/", "x": "https://x.com/",
    "reddit": "https://www.reddit.com/", "twitch": "https://www.twitch.tv/", "netflix": "https://www.netflix.com/",
    "whatsapp": "https://web.whatsapp.com/", "whatsapp web": "https://web.whatsapp.com/",
    "telegram": "https://web.telegram.org/", "linkedin": "https://www.linkedin.com/",
    "pinterest": "https://www.pinterest.com/", "snapchat": "https://web.snapchat.com/",
    "github": "https://github.com/", "chatgpt": "https://chatgpt.com/", "claude.ai": "https://claude.ai/",
    "outlook": "https://outlook.live.com/", "onedrive": "https://onedrive.live.com/", "amazon": "https://www.amazon.com/",
    "ebay": "https://www.ebay.com/", "wikipedia": "https://www.wikipedia.org/", "canva": "https://www.canva.com/",
    "figma": "https://www.figma.com/", "notion": "https://www.notion.so/", "dropbox": "https://www.dropbox.com/",
    "soundcloud": "https://soundcloud.com/", "kick": "https://kick.com/",
}

# Browsers that "open X in <browser>" can name, by the paths they install to.
BROWSERS = {
    "zen": [r"%ProgramFiles%\Zen Browser\zen.exe"],
    "opera gx": [r"%LOCALAPPDATA%\Programs\Opera GX\opera.exe", r"%LOCALAPPDATA%\Programs\Opera GX\launcher.exe"],
    "opera": [r"%LOCALAPPDATA%\Programs\Opera\opera.exe", r"%LOCALAPPDATA%\Programs\Opera\launcher.exe"],
    "chrome": [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
               r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"],
    "edge": [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
             r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"],
    "firefox": [r"%ProgramFiles%\Mozilla Firefox\firefox.exe"],
    "brave": [r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe",
              r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe"],
}


def site_url(name: str) -> str | None:
    low = name.lower().strip().removeprefix("the ").removesuffix(" website").removesuffix(" site").strip()
    return SITES.get(low)


def open_url(url: str, browser: str = "") -> str:
    """Open a web page, in a named browser if given ("zen", "opera", ...) and installed, else the default one."""
    key = browser.lower().replace("browser", "").replace("google", "").strip()
    for path in BROWSERS.get(key, []):
        exe = os.path.expandvars(path)
        if os.path.isfile(exe):
            subprocess.Popen([exe, url])
            return f"Opened {url} in {browser.strip().title()}."
    webbrowser.open(url)
    return f"Opened {url} in the browser."


SKIP_DIRS = {"$recycle.bin", "system volume information", "windows", "node_modules", ".git",
             "program files", "program files (x86)", "programdata", "appdata", "__pycache__", ".venv"}


def start_apps() -> list[dict]:
    """Everything in the Start menu: [{"Name": "Spotify", "AppID": "..."}]. Cached."""
    global _start_apps
    if _start_apps is None:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
        _start_apps = json.loads(out) if out.strip() else []
    return _start_apps


def find_app(name: str) -> dict | None:
    apps = start_apps()
    q = name.lower().strip()
    names = {a["Name"].lower(): a for a in apps}
    if q in names:
        return names[q]
    starts = [a for n, a in names.items() if n.startswith(q)]
    if starts:
        return min(starts, key=lambda a: len(a["Name"]))
    contains = [a for n, a in names.items() if q in n]
    if contains:
        return min(contains, key=lambda a: len(a["Name"]))
    close = difflib.get_close_matches(q, names, n=1, cutoff=0.6)
    return names[close[0]] if close else None


def _new_window(before: set[int], hints: list[str], timeout: float, cls: str | None = None) -> win.Window | None:
    """Wait for a window that didn't exist before the launch, preferring ones matching the hints."""
    deadline = time.time() + timeout
    fallback_deadline = time.time() + min(timeout, 4)
    while time.time() < deadline:
        fresh = [w for w in win.list_windows() if w.hwnd not in before and (cls is None or w.cls == cls)]
        for hint in hints:
            for w in fresh:
                if win.match_score(w, hint):
                    return w
        if fresh and (not hints or time.time() > fallback_deadline):
            return fresh[0]
        time.sleep(0.3)
    return None


def _app_hints(name: str, app: dict | None) -> list[str]:
    hints = [name]
    if app:
        hints.append(app["Name"])
        # AppIDs often end in the exe name, e.g. "...\\Code.exe" or "Chrome"
        tail = app["AppID"].replace("!", "\\").split("\\")[-1].removesuffix(".exe")
        hints.append(tail)
    return hints


def launch_app(name: str, skip: set[int] = frozenset()) -> tuple[str, win.Window | None]:
    """Open an app (or reuse its open window). Returns a message and the window, if found."""
    app = find_app(name)
    if not app:
        url = site_url(name)
        if url:   # "instagram" isn't installed, but it's a website
            return open_url(url), None
        return (f"Error: no installed app matches '{name}'. If it's a website or web app, open its URL with "
                "open_target; if it's a program the user wants, it can be installed with winget."), None
    hints = _app_hints(name, app)

    # Already running? Bring it back instead of starting a second copy.
    for hint in hints:
        existing = [w for w in win.find_windows(hint) if w.hwnd not in skip]
        if existing and win.match_score(existing[0], hint) >= 60:
            win.focus(existing[0].hwnd)
            return f"{app['Name']} was already open: window {existing[0].describe()}", existing[0]

    before = {w.hwnd for w in win.list_windows()}
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['AppID']}"])
    w = _new_window(before, hints, timeout=15)
    if w:
        return f"Opened {app['Name']}: window {w.describe()}", w
    return f"Launched {app['Name']}, but its window hasn't appeared yet (it may still be loading).", None


def launch_folder(path: str = "", reuse: bool = False, skip: set[int] = frozenset()) -> tuple[str, win.Window | None]:
    """Open a folder in a new Explorer window, or with reuse=True an already-open one showing it."""
    p = Path(os.path.expandvars(os.path.expanduser(path))) if path else None
    if p and not p.exists():
        return f"Error: folder '{path}' does not exist. Try find_folder first.", None
    hints = [p.name or str(p)] if p else []
    if reuse:
        explorers = [w for w in win.list_windows() if w.cls == "CabinetWClass" and w.hwnd not in skip]
        match = next((w for w in explorers if not hints or win.match_score(w, hints[0])), None)
        if match:
            return f"Reused File Explorer: window {match.describe()}", match
    before = {w.hwnd for w in win.list_windows()}
    subprocess.Popen(["explorer.exe", str(p)] if p else ["explorer.exe"])
    w = _new_window(before, hints, timeout=8, cls="CabinetWClass")
    if w:
        return f"Opened File Explorer: window {w.describe()}", w
    return "Opened File Explorer, but couldn't identify its window.", None


def open_app(name: str) -> str:
    return launch_app(name)[0]


def open_folder(path: str = "") -> str:
    return launch_folder(path)[0]


def open_target(target: str) -> str:
    """URLs, files, or anything else Windows knows how to open."""
    if target.startswith(("http://", "https://")):
        webbrowser.open(target)
        return f"Opened {target} in the browser."
    os.startfile(target)
    return f"Opened {target}."


def _drives() -> list[str]:
    return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]


def find_folder(name: str, root: str = "", max_depth: int = 4, limit: int = 10) -> list[str]:
    """Breadth-first folder search by name (case-insensitive substring), shallow results first."""
    q = name.lower()
    roots = [root] if root else _drives()
    found: list[str] = []
    queue = [(Path(r), 0) for r in roots]
    exact: list[str] = []
    while queue and len(found) < limit * 3:
        folder, depth = queue.pop(0)
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            if not e.is_dir(follow_symlinks=False) or e.name.lower() in SKIP_DIRS or e.name.startswith("$"):
                continue
            if q in e.name.lower():
                (exact if e.name.lower() == q else found).append(e.path)
            if depth + 1 < max_depth:
                queue.append((Path(e.path), depth + 1))
    return (exact + found)[:limit]
