"""/cleandisk: pick a disk, see what's safe to clean, what's your call and what to leave alone, then clean.

Everything that can be cleaned is a folder this file knows by name (temp folders, caches, crash reports, the
Recycle Bin), worked out here on the PC: the phone only ever sends back the ids of the items you ticked, never a
path. "Safe" items are things programs rebuild by themselves (caches, temp files older than a day). "Your call"
is gone for good once cleaned (the Recycle Bin) or comes back only as a big re-download (AI model caches).
"Leave alone" is shown so you know where the space went, but this never deletes it: your own files, Windows'
page and hibernation files, installed programs. Some Windows-owned caches need administrator rights, so for those
you get a button that opens Windows' own Disk Cleanup.
"""
import ctypes
import logging
import os
import time
from ctypes import wintypes as wt
from pathlib import Path

from .files import fmt_size, tree_size

log = logging.getLogger("jarvis.cleandisk")

HOME = Path.home()
LOCAL = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local"))
ROAMING = Path(os.environ.get("APPDATA", HOME / "AppData/Roaming"))
WINDIR = Path(os.environ.get("WINDIR", r"C:\Windows"))
TEMP_AGE = 24 * 3600       # temp files younger than this may belong to something running (an installer, say)
SCAN_SECONDS = 45          # the whole scan gives up after this; sizes then show as "at least"


def _chromium(name: str, base: Path | list, exe: str) -> dict:
    """A Chromium browser's caches, every profile (not its history, logins, cookies or offline site data)."""
    def roots():
        out = []
        for b in base if isinstance(base, list) else [base]:
            if not b.is_dir():
                continue
            for prof in [b, *[p for p in b.iterdir() if p.is_dir()]]:
                for sub in ("Cache", "Code Cache", "GPUCache", "DawnCache", "DawnWebGPUCache", "GrShaderCache",
                            "ShaderCache"):
                    if (prof / sub).is_dir():
                        out.append(prof / sub)
        return out
    return {"id": "cache-" + exe.split(".")[0].lower(), "label": f"{name} cache", "verdict": "safe",
            "why": f"Saved copies of web pages. {name} downloads them again as needed. Close {name} first.",
            "roots": roots, "apps": [exe]}


def _paths(*paths) -> callable:
    return lambda: [Path(p) for p in paths if Path(p).is_dir()]


def _glob(base: Path, pattern: str) -> callable:
    return lambda: [p for p in base.glob(pattern) if p.is_dir()] if base.is_dir() else []


# What can be cleaned. verdict: "safe" (ticked), "ask" (your call, not ticked).
CATEGORIES = [
    {"id": "temp", "label": "Temporary files", "verdict": "safe", "older": TEMP_AGE,
     "why": "Left behind by installers and apps. Only files older than a day are removed.",
     "roots": lambda: [Path(os.environ.get("TEMP", LOCAL / "Temp"))]},
    {"id": "wintemp", "label": "Windows temporary files", "verdict": "safe", "older": TEMP_AGE,
     "why": "Windows' own leftovers. Some need administrator rights and are skipped.",
     "roots": _paths(WINDIR / "Temp")},
    {"id": "crash", "label": "Crash reports", "verdict": "safe",
     "why": "Memory dumps and error reports from apps that crashed. Only useful to developers.",
     "roots": _paths(LOCAL / "CrashDumps", LOCAL / "Microsoft/Windows/WER/ReportArchive",
                     LOCAL / "Microsoft/Windows/WER/ReportQueue", LOCAL / "Microsoft/Windows/WER/Temp")},
    {"id": "shaders", "label": "Graphics shader caches", "verdict": "safe",
     "why": "Rebuilt automatically. Games may stutter for a moment the first time after.",
     "roots": _paths(LOCAL / "D3DSCache", LOCAL / "NVIDIA/DXCache", LOCAL / "NVIDIA/GLCache",
                     ROAMING / "NVIDIA/ComputeCache", LOCAL / "AMD/DxCache", LOCAL / "AMD/GLCache")},
    _chromium("Chrome", LOCAL / "Google/Chrome/User Data", "chrome.exe"),
    _chromium("Edge", LOCAL / "Microsoft/Edge/User Data", "msedge.exe"),
    _chromium("Brave", LOCAL / "BraveSoftware/Brave-Browser/User Data", "brave.exe"),
    _chromium("Opera", [LOCAL / "Opera Software" / v for v in ("Opera Stable", "Opera GX Stable", "Opera Neon Stable")],
              "opera.exe"),
    {"id": "cache-firefox", "label": "Firefox / Zen cache", "verdict": "safe", "apps": ["firefox.exe", "zen.exe"],
     "why": "Saved copies of web pages. Downloaded again as needed. Close the browser first.",
     "roots": lambda: _glob(LOCAL / "Mozilla/Firefox/Profiles", "*/cache2")() + _glob(LOCAL / "zen/Profiles", "*/cache2")()},
    {"id": "cache-discord", "label": "Discord cache", "verdict": "safe", "apps": ["Discord.exe"],
     "why": "Images and videos Discord already showed you. Close Discord first.",
     "roots": _paths(ROAMING / "discord/Cache", ROAMING / "discord/Code Cache", ROAMING / "discord/GPUCache")},
    {"id": "cache-spotify", "label": "Spotify cache", "verdict": "safe", "apps": ["Spotify.exe"],
     "why": "Songs Spotify kept to play faster (not your downloads). Close Spotify first.",
     "roots": lambda: _paths(LOCAL / "Spotify/Data")() + _glob(LOCAL / "Packages", "SpotifyAB.SpotifyMusic_*/LocalCache/Spotify/Data")()},
    {"id": "cache-vscode", "label": "VS Code cache", "verdict": "safe", "apps": ["Code.exe"],
     "why": "VS Code's own caches and old update files, not your settings or extensions. Close VS Code first.",
     "roots": _paths(ROAMING / "Code/Cache", ROAMING / "Code/CachedData", ROAMING / "Code/CachedExtensionVSIXs",
                     ROAMING / "Code/Code Cache", ROAMING / "Code/GPUCache")},
    {"id": "dev", "label": "Developer download caches", "verdict": "safe",
     "why": "pip, npm, yarn and NuGet keep copies of packages they downloaded; they fetch them again if needed.",
     "roots": _paths(LOCAL / "pip/Cache", LOCAL / "pip/cache", LOCAL / "npm-cache", ROAMING / "npm-cache",
                     LOCAL / "Yarn/Cache", LOCAL / "NuGet/v3-cache", LOCAL / "NuGet/plugins-cache")},
    {"id": "recycle", "label": "Recycle Bin", "verdict": "ask", "recycle": True,
     "why": "Things you deleted. Once it's emptied you can't get them back.", "roots": lambda: []},
    {"id": "models", "label": "Downloaded AI models", "verdict": "ask",
     "why": "Model caches from Hugging Face, PyTorch and Kaggle. Programs that need one download it again (can be "
            "gigabytes).",
     "roots": _paths(HOME / ".cache/huggingface", HOME / ".cache/torch", HOME / ".cache/kagglehub")},
]

# Shown so you know where the space is, never cleaned here.
LEAVE = [
    ("hiberfil", "Hibernation file", lambda d: Path(d + "\\hiberfil.sys"),
     "Windows needs it to hibernate. To get the space back, turn hibernation off yourself: Start › type cmd › Run as "
     "administrator › powercfg /h off."),
    ("pagefile", "Page file", lambda d: Path(d + "\\pagefile.sys"),
     "Windows' overflow memory. Leave it: when it can't grow, programs fail."),
    ("downloads", "Downloads", lambda d: HOME / "Downloads",
     "Your own files. Have a look yourself and delete what you don't need."),
    ("desktop", "Desktop", lambda d: HOME / "Desktop", "Your own files."),
    ("videos", "Videos", lambda d: HOME / "Videos", "Your own files."),
    ("pictures", "Pictures", lambda d: HOME / "Pictures", "Your own files."),
    ("documents", "Documents", lambda d: HOME / "Documents", "Your own files."),
]
# Windows-owned caches that need administrator rights: Windows' Disk Cleanup does these safely.
ADMIN = ("Windows Update leftovers, old Windows versions and system logs need administrator rights. Open Windows "
         "Disk Cleanup › Clean up system files for those.")


def _running(exes: list[str]) -> list[str]:
    try:
        import psutil
        names = {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
    except Exception:
        return []
    return [e for e in exes if e.lower() in names]


def _recycle(drive: str) -> tuple[int, int]:
    class INFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]
    info = INFO(ctypes.sizeof(INFO), 0, 0)
    ctypes.windll.shell32.SHQueryRecycleBinW(drive + "\\", ctypes.byref(info))
    return info.i64Size, info.i64NumItems


def _on(drive: str, p: Path) -> bool:
    return p.drive.upper() == drive.upper()


def _guard(root: Path) -> bool:
    """Last check before deleting inside a folder: strictly inside AppData or .cache (never those folders
    themselves, never the user folder), or Windows' own Temp. The roots come from CATEGORIES, never from the phone;
    this is the belt to those braces. A root that is a link pointing elsewhere resolves outside and is refused."""
    r = root.resolve()
    bases = [b.resolve() for b in (LOCAL, ROAMING, HOME / ".cache")]
    if r in bases or r == HOME.resolve():
        return False
    if any(b in r.parents for b in bases):
        return True
    wt_ = (WINDIR / "Temp").resolve()
    return r == wt_ or wt_ in r.parents


def _walk(root: Path):
    """(folder, [files as DirEntry], [subfolders]) deepest first, never following links or junctions out."""
    out, stack = [], [str(root)]
    while stack:
        folder = stack.pop()
        files, subs = [], []
        try:
            with os.scandir(folder) as it:
                for e in it:
                    try:
                        if e.is_symlink() or e.is_junction():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            subs.append(e.path)
                        else:
                            files.append(e)
                    except OSError:
                        continue
        except OSError:
            continue
        stack.extend(subs)
        out.append((folder, files, subs))
    return reversed(out)


def _old_size(root: Path, older: float, deadline: float) -> tuple[int, int]:
    """Bytes and files in a folder that are older than `older` seconds."""
    cutoff, total, n = time.time() - older, 0, 0
    for _folder, files, _subs in _walk(root):
        if time.time() > deadline:
            break
        for e in files:
            try:
                st = e.stat(follow_symlinks=False)
                if st.st_mtime < cutoff:
                    total, n = total + st.st_size, n + 1
            except OSError:
                pass
    return total, n


def scan(drive: str, progress=None) -> list[dict]:
    """Everything on that drive, sorted into safe / ask / leave, with sizes. Each item: id, label, verdict, why,
    size, files, partial (gave up measuring), roots (paths, kept on the PC), blocked (apps to close first)."""
    drive = drive.rstrip("\\/").upper()
    deadline = time.time() + SCAN_SECONDS
    items = []
    for cat in CATEGORIES:
        if progress:
            progress(f"Looking at {cat['label'].lower()}…")
        if cat.get("recycle"):
            size, n = _recycle(drive)
            if size:
                items.append({"id": cat["id"], "label": cat["label"], "verdict": cat["verdict"], "why": cat["why"],
                              "size": size, "files": n, "partial": False, "roots": [], "recycle": True,
                              "drive": drive})
            continue
        roots, seen = [], set()
        for r in cat["roots"]():   # Windows names aren't case-sensitive: pip\Cache and pip\cache are one folder
            key = str(r.resolve()).lower()
            if _on(drive, r) and key not in seen:
                seen.add(key)
                roots.append(r)
        if not roots:
            continue
        size = files = 0
        partial = False
        for r in roots:
            if cat.get("older"):
                s, n = _old_size(r, cat["older"], deadline)
                done = time.time() < deadline
            else:
                s, n, done = tree_size(r, deadline)
            size, files, partial = size + s, files + n, partial or not done
        if size < 1024 * 1024:   # under 1 MB isn't worth a row
            continue
        blocked = _running(cat.get("apps", []))
        items.append({"id": cat["id"], "label": cat["label"], "verdict": cat["verdict"], "why": cat["why"],
                      "size": size, "files": files, "partial": partial, "roots": [str(r) for r in roots],
                      "older": cat.get("older", 0), "blocked": blocked})
    if progress:
        progress("Measuring your own folders…")
    for lid, label, where, why in LEAVE:
        p = where(drive)
        if not _on(drive, p) or not p.exists():
            continue
        if p.is_file():
            try:
                size, partial = p.stat().st_size, False
            except OSError:
                continue
        else:
            size, _, done = tree_size(p, min(deadline, time.time() + 10))
            partial = not done
        if size >= 100 * 1024 * 1024:
            items.append({"id": lid, "label": label, "verdict": "leave", "why": why, "size": size, "partial": partial})
    return items


def clean(items: list[dict], progress=None) -> dict:
    """Clean the chosen items (from scan()). Files in use are skipped. Returns freed bytes, skipped files, notes."""
    freed, skipped, notes = 0, 0, []
    for it in items:
        if it.get("verdict") not in ("safe", "ask"):
            continue   # "leave" is never cleaned, whatever was sent
        if it.get("blocked") and _running(it["blocked"]):
            notes.append(f"Skipped {it['label']}: close {', '.join(e.removesuffix('.exe') for e in it['blocked'])} first.")
            continue
        if progress:
            progress(f"Cleaning {it['label'].lower()}…")
        if it.get("recycle"):
            drive = it.get("drive", "C:")
            before, _ = _recycle(drive)
            ctypes.windll.shell32.SHEmptyRecycleBinW(None, drive + "\\", 0x1 | 0x2 | 0x4)   # no confirm/progress/sound
            freed += before - _recycle(drive)[0]
            continue
        cutoff = time.time() - it.get("older", 0) if it.get("older") else None
        for root in map(Path, it.get("roots", [])):
            if not root.is_dir() or not _guard(root):
                notes.append(f"Didn't touch {root}: it isn't a folder I clean.")
                continue
            f, s = _empty(root, cutoff)
            freed, skipped = freed + f, skipped + s
    log.info("Disk clean-up: %s freed, %d files skipped.", fmt_size(freed), skipped)
    return {"freed": freed, "skipped": skipped, "notes": notes}


def _empty(root: Path, cutoff: float | None) -> tuple[int, int]:
    """Delete what's inside root (root itself stays). With a cutoff, only files older than it. Files in use and
    folders that aren't empty afterwards are skipped."""
    freed, skipped = 0, 0
    for _folder, files, subs in _walk(root):   # deepest first, so emptied folders can go too
        for e in files:
            try:
                st = e.stat(follow_symlinks=False)
                if cutoff and st.st_mtime >= cutoff:
                    continue
                os.remove(e.path)
                freed += st.st_size
            except OSError:
                skipped += 1
        for d in subs:
            try:
                os.rmdir(d)   # only goes if it's empty now
            except OSError:
                pass
    return freed, skipped


def summary(items: list[dict]) -> str:
    safe = sum(i["size"] for i in items if i["verdict"] == "safe")
    return f"{fmt_size(safe)} is safe to clean."
