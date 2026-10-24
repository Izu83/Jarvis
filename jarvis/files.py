"""Files: look around (list, search, read, what's using space) and change things (create, write, move, copy,
delete). Anything that deletes or overwrites goes through approval.ask() first, see tools.py."""
import ctypes
import ctypes.wintypes as wt
import fnmatch
import os
import shutil
import string
import time
from pathlib import Path

from send2trash import send2trash

SKIP_DIRS = {"$recycle.bin", "system volume information", "node_modules", ".git", "__pycache__", ".venv"}
TEXT_EXT = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".log", ".ini", ".cfg", ".toml", ".yaml", ".yml",
            ".xml", ".html", ".css", ".bat", ".ps1", ".sh", ".c", ".cpp", ".h", ".cs", ".java", ".go", ".rs", ".sql",
            ".env.example", ".gitignore", ".srt", ".tsv"}


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def resolve(path: str) -> Path:
    """Accepts 'D:\\x', '~/Downloads', '%TEMP%', and well-known names like 'downloads' or 'desktop'."""
    known = {"downloads": "~/Downloads", "desktop": "~/Desktop", "documents": "~/Documents", "pictures": "~/Pictures",
             "videos": "~/Videos", "music": "~/Music", "temp": os.environ.get("TEMP", "~/AppData/Local/Temp")}
    raw = known.get(path.strip().lower(), path.strip())
    return Path(os.path.expandvars(os.path.expanduser(raw)))


# ---------------------------------------------------------------- safety

def _protected() -> list[Path]:
    home = Path.home()
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    paths = [windir, Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
             Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
             Path(os.environ.get("ProgramData", r"C:\ProgramData")), Path(r"C:\Users"), home,
             home / "AppData", home / "AppData" / "Local", home / "AppData" / "Roaming", home / "Documents",
             home / "Desktop", home / "Downloads", home / "Pictures", home / "Videos", home / "Music",
             Path(__file__).resolve().parent.parent]   # Jarvis itself
    return [p.resolve() for p in paths]


def check_deletable(p: Path) -> None:
    """Refuse drive roots, Windows/system folders, the user folders themselves (their *contents* are fine) and
    anything inside Windows or Program Files."""
    p = p.resolve()
    if p.parent == p:
        raise PermissionError(f"{p} is a whole drive; I won't delete that.")
    for prot in _protected():
        if p == prot:
            raise PermissionError(f"{p} is a system or main user folder; I won't delete it (I can delete things inside it).")
    for root in _protected()[:4]:   # Windows, Program Files (both), ProgramData: nothing inside either
        if root in p.parents:
            raise PermissionError(f"{p} is inside {root}; deleting system files could break Windows.")


# ---------------------------------------------------------------- sizes

def tree_size(path: Path, deadline: float) -> tuple[int, int, bool]:
    """(bytes, files, complete) for a folder, giving up at the deadline."""
    total, files, stack = 0, 0, [str(path)]
    while stack:
        if time.time() > deadline:
            return total, files, False
        folder = stack.pop()
        try:
            with os.scandir(folder) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not e.is_junction():
                                stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, files, True


def _entry_line(name: str, size: int | None, kind: str, mtime: float, complete: bool = True) -> str:
    when = time.strftime("%Y-%m-%d", time.localtime(mtime)) if mtime else ""
    s = fmt_size(size) + ("+" if not complete else "") if size is not None else "?"
    return f"{kind:6} {s:>10}  {when}  {name}"


def scan_folder(path: str, limit: int = 40, with_folder_sizes: bool = True) -> str:
    p = resolve(path)
    if not p.is_dir():
        raise FileNotFoundError(f"{p} isn't a folder.")
    deadline = time.time() + 12
    rows = []
    with os.scandir(p) as it:
        for e in it:
            try:
                st = e.stat(follow_symlinks=False)
                if e.is_dir(follow_symlinks=False):
                    size, _, complete = tree_size(Path(e.path), deadline) if with_folder_sizes else (None, 0, True)
                    rows.append((size or 0, _entry_line(e.name + "\\", size, "folder", st.st_mtime, complete)))
                else:
                    rows.append((st.st_size, _entry_line(e.name, st.st_size, "file", st.st_mtime)))
            except OSError:
                continue
    rows.sort(key=lambda r: r[0], reverse=True)
    total = sum(r[0] for r in rows)
    out = [f"{p}  ({len(rows)} items, {fmt_size(total)}{'+' if time.time() > deadline else ''}, largest first)"]
    out += [r[1] for r in rows[:limit]]
    if len(rows) > limit:
        out.append(f"... and {len(rows) - limit} more")
    return "\n".join(out)


def disk_usage(path: str = "C:\\", top: int = 15) -> str:
    """What's taking the space: the biggest folders and files one level down, plus free space."""
    p = resolve(path)
    usage = shutil.disk_usage(p.anchor or p)
    lines = [f"Drive {p.anchor}: {fmt_size(usage.free)} free of {fmt_size(usage.total)} ({usage.used / usage.total:.0%} used)"]
    lines.append(scan_folder(str(p), limit=top))
    return "\n".join(lines)


def _recycle_bin_size() -> tuple[int, int]:
    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]
    info = SHQUERYRBINFO(ctypes.sizeof(SHQUERYRBINFO), 0, 0)
    ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    return info.i64Size, info.i64NumItems


def cleanup_suggestions() -> str:
    """Places that are usually safe to clear, with their sizes."""
    deadline = time.time() + 20
    lines = []
    size, n = _recycle_bin_size()
    lines.append(f"Recycle Bin: {fmt_size(size)} ({n} items). Empty with empty_recycle_bin.")
    candidates = [
        ("Your temp files", resolve("temp")),
        ("Windows temp files", Path(os.environ.get("WINDIR", r"C:\Windows")) / "Temp"),
        ("Downloads", resolve("downloads")),
        ("Chrome cache", Path.home() / "AppData/Local/Google/Chrome/User Data/Default/Cache"),
        ("Edge cache", Path.home() / "AppData/Local/Microsoft/Edge/User Data/Default/Cache"),
        ("Opera cache", Path.home() / "AppData/Local/Opera Software/Opera Stable/Cache"),
        ("Discord cache", Path.home() / "AppData/Roaming/discord/Cache"),
        ("Spotify cache", Path.home() / "AppData/Local/Packages/SpotifyAB.SpotifyMusic_zpdnekdrzrea0/LocalCache/Spotify/Data"),
        ("NVIDIA shader cache", Path.home() / "AppData/Local/NVIDIA/DXCache"),
        ("Crash dumps", Path.home() / "AppData/Local/CrashDumps"),
    ]
    for label, path in candidates:
        if path.is_dir():
            size, files, complete = tree_size(path, deadline)
            lines.append(f"{label}: {fmt_size(size)}{'+' if not complete else ''} in {files} files ({path})")
    lines.append("Clear one with clear_folder (temp and cache folders can be cleared permanently).")
    return "\n".join(lines)


# ---------------------------------------------------------------- search + read

def _drives() -> list[str]:
    return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]


def find_files(pattern: str, root: str = "", min_mb: float = 0, days: float = 0, limit: int = 30) -> str:
    """Name search with wildcards ('*.pdf', '*invoice*'); plain words match anywhere in the name."""
    pat = pattern.lower() if any(c in pattern for c in "*?[") else f"*{pattern.lower()}*"
    roots = [resolve(root)] if root else [Path(d) for d in _drives()]
    since = time.time() - days * 86400 if days else 0
    deadline = time.time() + 15
    hits: list[tuple[float, str]] = []
    stack = [str(r) for r in roots]
    while stack and time.time() < deadline and len(hits) < limit * 4:
        folder = stack.pop()
        try:
            with os.scandir(folder) as it:
                for e in it:
                    try:
                        is_dir = e.is_dir(follow_symlinks=False)
                        if is_dir and (e.name.lower() in SKIP_DIRS or e.name.startswith("$") or e.is_junction()):
                            continue
                        if fnmatch.fnmatch(e.name.lower(), pat):
                            st = e.stat(follow_symlinks=False)
                            if (is_dir or st.st_size >= min_mb * 1024 * 1024) and st.st_mtime >= since:
                                hits.append((st.st_mtime, _entry_line(e.path + ("\\" if is_dir else ""),
                                                                      None if is_dir else st.st_size,
                                                                      "folder" if is_dir else "file", st.st_mtime)))
                        if is_dir:
                            stack.insert(0, e.path)   # breadth-first: shallow results come first
                    except OSError:
                        continue
        except OSError:
            continue
    hits.sort(reverse=True)
    head = f"{len(hits)} match(es) for '{pattern}'" + (" (search stopped early; narrow the folder)" if stack else "")
    return head + "\n" + "\n".join(h[1] for h in hits[:limit]) if hits else head


def read_file(path: str, max_chars: int = 6000) -> str:
    p = resolve(path)
    if not p.is_file():
        raise FileNotFoundError(f"{p} isn't a file.")
    size = p.stat().st_size
    if p.suffix.lower() not in TEXT_EXT and size > 0:
        with open(p, "rb") as f:
            if b"\0" in f.read(2048):
                return f"{p} is a binary file ({fmt_size(size)}); I can only read text files."
    with open(p, encoding="utf-8", errors="replace") as f:   # only read what we show, even for a huge log
        text = f.read(max_chars + 1)
    more = f"\n... (showing the start; the file is {fmt_size(size)})" if len(text) > max_chars else ""
    return f"{p} ({fmt_size(size)}):\n{text[:max_chars]}{more}"


# ---------------------------------------------------------------- changes (non-destructive)

def create_folder(path: str) -> str:
    p = resolve(path)
    p.mkdir(parents=True, exist_ok=True)
    return f"Created {p}"


def write_file(path: str, text: str, append: bool = False) -> str:
    p = resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8") as f:
        f.write(text)
    return f"{'Appended to' if append else 'Wrote'} {p} ({len(text)} characters)"


def move(src: str, dest: str) -> str:
    s, d = resolve(src), resolve(dest)
    if not s.exists():
        raise FileNotFoundError(f"{s} doesn't exist.")
    if d.is_dir():
        d = d / s.name
    if d.exists():
        raise FileExistsError(f"{d} already exists; pick another name or delete it first.")
    check_deletable(s)   # moving a system folder away is as bad as deleting it
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return f"Moved {s} -> {d}"


def copy(src: str, dest: str) -> str:
    s, d = resolve(src), resolve(dest)
    if not s.exists():
        raise FileNotFoundError(f"{s} doesn't exist.")
    if d.is_dir():
        d = d / s.name
    if d.exists():
        raise FileExistsError(f"{d} already exists.")
    d.parent.mkdir(parents=True, exist_ok=True)
    (shutil.copytree if s.is_dir() else shutil.copy2)(str(s), str(d))
    return f"Copied {s} -> {d}"


# ---------------------------------------------------------------- destructive (only after approval)

def describe_targets(paths: list[str]) -> tuple[list[Path], str, int]:
    """Validate and summarise what would be deleted, for the permission prompt."""
    targets, lines, total = [], [], 0
    deadline = time.time() + 10
    for raw in paths:
        p = resolve(raw)
        if not p.exists():
            raise FileNotFoundError(f"{p} doesn't exist.")
        check_deletable(p)
        size = tree_size(p, deadline)[0] if p.is_dir() else p.stat().st_size
        total += size
        targets.append(p)
        lines.append(f"• {p}{chr(92) if p.is_dir() else ''} ({fmt_size(size)})")
    return targets, "\n".join(lines[:12]) + (f"\n• … and {len(lines) - 12} more" if len(lines) > 12 else ""), total


def delete(targets: list[Path], permanent: bool) -> str:
    done, failed = 0, []
    for p in targets:
        try:
            if permanent:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            else:
                send2trash(str(p))
            done += 1
        except OSError as e:
            failed.append(f"{p.name} ({e.strerror or e})")
    where = "permanently" if permanent else "to the Recycle Bin"
    return f"Deleted {done} item(s) {where}." + (f" Couldn't delete: {', '.join(failed[:5])}" if failed else "")


def folder_contents(path: str) -> list[Path]:
    p = resolve(path)
    if not p.is_dir():
        raise FileNotFoundError(f"{p} isn't a folder.")
    check_deletable(p / "x")   # the folder's contents must be deletable
    return list(p.iterdir())


def clear(items: list[Path], permanent: bool) -> str:
    """Delete everything in a folder, skipping files that are in use (normal for temp folders)."""
    freed, done, skipped = 0, 0, 0
    deadline = time.time() + 20
    for p in items:
        try:
            size = tree_size(p, deadline)[0] if p.is_dir() else p.stat().st_size
            if permanent:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            else:
                send2trash(str(p))
            freed += size
            done += 1
        except OSError:
            skipped += 1
    note = f" Skipped {skipped} item(s) that are in use." if skipped else ""
    return f"Cleared {done} item(s), {fmt_size(freed)} {'freed' if permanent else 'moved to the Recycle Bin'}.{note}"


def empty_recycle_bin() -> str:
    size, n = _recycle_bin_size()
    SHERB_NOCONFIRMATION, SHERB_NOPROGRESSUI, SHERB_NOSOUND = 0x1, 0x2, 0x4
    ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND)
    return f"Emptied the Recycle Bin: {n} item(s), {fmt_size(size)} freed."


def recycle_bin_summary() -> str:
    size, n = _recycle_bin_size()
    return f"{n} item(s), {fmt_size(size)}"
