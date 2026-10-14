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


def open_app(name: str) -> str:
    app = find_app(name)
    if not app:
        return f"Error: no installed app matches '{name}'."
    hints = _app_hints(name, app)

    # Already running? Bring it back instead of starting a second copy.
    for hint in hints:
        existing = win.find_windows(hint)
        if existing and win.match_score(existing[0], hint) >= 60:
            win.focus(existing[0].hwnd)
            return f"{app['Name']} was already open: window {existing[0].describe()}"

    before = {w.hwnd for w in win.list_windows()}
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['AppID']}"])
    w = _new_window(before, hints, timeout=15)
    if w:
        return f"Opened {app['Name']}: window {w.describe()}"
    return f"Launched {app['Name']}, but its window hasn't appeared yet (it may still be loading)."


def open_folder(path: str = "") -> str:
    before = {w.hwnd for w in win.list_windows()}
    if path:
        p = Path(os.path.expandvars(os.path.expanduser(path)))
        if not p.exists():
            return f"Error: folder '{path}' does not exist. Try find_folder first."
        subprocess.Popen(["explorer.exe", str(p)])
        hints = [p.name or str(p)]
    else:
        subprocess.Popen(["explorer.exe"])
        hints = []
    w = _new_window(before, hints, timeout=8, cls="CabinetWClass")
    if w:
        return f"Opened File Explorer: window {w.describe()}"
    return "Opened File Explorer, but couldn't identify its window."


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
