"""Win32 window and monitor control: find windows, move them between monitors, tile them."""
import ctypes
import ctypes.wintypes as wt
import math
import re
import time
from dataclasses import dataclass

import psutil

user32 = ctypes.WinDLL("user32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
MONITOR_DEFAULTTONEAREST = 2
WM_CLOSE = 0x0010

# Background/system windows that should never be treated as "apps"
IGNORED_PROCESSES = {
    "textinputhost.exe", "nvidia overlay.exe", "shellexperiencehost.exe",
    "searchhost.exe", "startmenuexperiencehost.exe", "lockapp.exe",
}
IGNORED_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindow.restype = wt.HWND
user32.MonitorFromWindow.restype = wt.HMONITOR


def enable_dpi_awareness() -> None:
    """Use real pixel coordinates on every monitor (must run before any window calls)."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)


# ---------------------------------------------------------------- monitors

class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
        ("szDevice", wt.WCHAR * 32),
    ]


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int


@dataclass
class Monitor:
    number: int
    device: str
    bounds: Rect
    work: Rect  # area not covered by the taskbar
    primary: bool
    handle: int

    @property
    def portrait(self) -> bool:
        return self.bounds.h > self.bounds.w


def _rect(r: wt.RECT) -> Rect:
    return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)


def list_monitors() -> list[Monitor]:
    monitors: list[Monitor] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
    def callback(hmon, _hdc, _rect_ptr, _lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        m = re.search(r"DISPLAY(\d+)", info.szDevice)
        monitors.append(Monitor(
            number=int(m.group(1)) if m else len(monitors) + 1,
            device=info.szDevice,
            bounds=_rect(info.rcMonitor),
            work=_rect(info.rcWork),
            primary=bool(info.dwFlags & 1),
            handle=hmon,
        ))
        return True

    user32.EnumDisplayMonitors(None, None, callback, 0)
    return sorted(monitors, key=lambda m: m.number)


def get_monitor(number: int) -> Monitor:
    for m in list_monitors():
        if m.number == number:
            return m
    raise ValueError(f"No monitor {number}. Available: {[m.number for m in list_monitors()]}")


# ---------------------------------------------------------------- windows

@dataclass
class Window:
    hwnd: int
    title: str
    process: str  # e.g. "spotify.exe"
    cls: str
    monitor: int

    @property
    def app(self) -> str:
        return self.process.removesuffix(".exe")

    def describe(self) -> str:
        return f"#{self.hwnd} [{self.app}] \"{self.title}\" (monitor {self.monitor})"


def _is_cloaked(hwnd) -> bool:
    cloaked = wt.DWORD()
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return bool(cloaked.value)


def _monitor_numbers() -> dict[int, int]:
    return {m.handle: m.number for m in list_monitors()}


def list_windows() -> list[Window]:
    """Top-level, visible, user-facing windows in z-order (front-most first)."""
    windows: list[Window] = []
    mon_numbers = _monitor_numbers()

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or _is_cloaked(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value in IGNORED_CLASSES:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            proc = psutil.Process(pid.value).name().lower()
        except psutil.Error:
            proc = "unknown"
        if proc in IGNORED_PROCESSES:
            return True
        hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        windows.append(Window(int(hwnd), title.value, proc, cls.value, mon_numbers.get(hmon, 0)))
        return True

    user32.EnumWindows(callback, 0)
    return windows


def get_window(hwnd: int) -> Window | None:
    return next((w for w in list_windows() if w.hwnd == hwnd), None)


# Spoken names -> process names
APP_ALIASES = {
    "file explorer": "explorer", "explorer": "explorer", "files": "explorer", "file folder": "explorer",
    "folder": "explorer", "vs code": "code", "vscode": "code", "visual studio code": "code",
    "chrome": "chrome", "google chrome": "chrome", "edge": "msedge", "terminal": "windowsterminal",
}


def match_score(win: Window, query: str) -> int:
    """How well a window matches a spoken/typed name. 0 = no match."""
    q = query.lower().strip().lstrip("#")
    if q.isdigit():
        return 100 if int(q) == win.hwnd else 0
    title = win.title.lower()
    proc = APP_ALIASES.get(q, q).replace(" ", "")
    if title == q:
        return 90
    if win.app == proc:
        return 80
    if proc and proc in win.app:
        return 60
    if title.startswith(q + " ") or title.startswith(q + " -"):
        return 50
    if q in title:
        return 40
    return 0


def find_windows(query: str, windows: list[Window] | None = None) -> list[Window]:
    windows = list_windows() if windows is None else windows
    scored = [(match_score(w, query), i, w) for i, w in enumerate(windows)]
    return [w for s, i, w in sorted((x for x in scored if x[0] > 0), key=lambda x: (-x[0], x[1]))]


def resolve_windows(queries: list[str], timeout: float = 12.0) -> tuple[list[Window], list[str]]:
    """Map each query to a distinct window, waiting for slow-starting apps.

    Queries with fewer candidates are resolved first, so "Jarvis" grabs the Jarvis
    Explorer window before "explorer" picks among the remaining Explorer windows.
    """
    deadline = time.time() + timeout
    while True:
        windows = list_windows()
        candidates = {q: find_windows(q, windows) for q in queries}
        if all(candidates.values()) or time.time() > deadline:
            break
        time.sleep(0.5)

    chosen: dict[str, Window] = {}
    claimed: set[int] = set()
    for q in sorted(queries, key=lambda q: len(candidates[q]) or 999):
        pick = next((w for w in candidates[q] if w.hwnd not in claimed), None)
        if pick:
            chosen[q] = pick
            claimed.add(pick.hwnd)
    missing = [q for q in queries if q not in chosen]
    return [chosen[q] for q in queries if q in chosen], missing


# ---------------------------------------------------------------- moving

def _frame_margins(hwnd) -> tuple[int, int, int, int]:
    """Windows 10/11 windows have invisible resize borders; measure them so tiles line up."""
    outer = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(outer))
    frame = wt.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(frame), ctypes.sizeof(frame)):
        return 0, 0, 0, 0
    return frame.left - outer.left, frame.top - outer.top, outer.right - frame.right, outer.bottom - frame.bottom


def place(hwnd: int, r: Rect) -> None:
    if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    flags = SWP_NOZORDER | SWP_NOACTIVATE
    # Twice: the first move may cross into a monitor with different scaling, which changes the margins.
    for _ in range(2):
        l, t, rr, b = _frame_margins(hwnd)
        user32.SetWindowPos(hwnd, None, r.x - l, r.y - t, r.w + l + rr, r.h + t + b, flags)


def maximize_on(hwnd: int, mon: Monitor) -> None:
    w = mon.work
    place(hwnd, Rect(w.x + 50, w.y + 50, w.w - 100, w.h - 100))
    user32.ShowWindow(hwnd, SW_MAXIMIZE)


def focus(hwnd: int) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # Windows only lets the foreground app steal focus; a fake Alt press lifts that restriction.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)


def minimize(hwnd: int) -> None:
    user32.ShowWindow(hwnd, SW_MINIMIZE)


def close(hwnd: int) -> None:
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


# ---------------------------------------------------------------- layouts

LAYOUTS = ("auto", "maximize", "columns", "rows", "grid", "main-left",
           "left-half", "right-half", "top-half", "bottom-half")
HALVES = {"left-half": (False, 0), "right-half": (False, 1), "top-half": (True, 0), "bottom-half": (True, 1)}


def _split(area: Rect, n: int, vertical: bool) -> list[Rect]:
    if vertical:
        step = area.h // n
        return [Rect(area.x, area.y + i * step, area.w, step if i < n - 1 else area.h - i * step) for i in range(n)]
    step = area.w // n
    return [Rect(area.x + i * step, area.y, step if i < n - 1 else area.w - i * step, area.h) for i in range(n)]


def _grid(area: Rect, n: int) -> list[Rect]:
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    if area.h > area.w:
        cols, rows = rows, cols
    cells = []
    for row_idx, row in enumerate(_split(area, rows, vertical=True)):
        in_row = min(cols, n - row_idx * cols)
        cells += _split(row, in_row, vertical=False)
    return cells


def layout_rects(area: Rect, n: int, layout: str) -> list[Rect]:
    portrait = area.h > area.w
    if layout == "auto":
        if n == 1:
            layout = "maximize"
        elif portrait:
            layout = "rows"
        elif n == 2:
            layout = "columns"
        elif n == 3:
            layout = "main-left"
        else:
            layout = "grid"
    if layout == "maximize":
        return [area] * n
    if layout in HALVES:  # every window fills that half (normally there's just one)
        vertical, index = HALVES[layout]
        return [_split(area, 2, vertical)[index]] * n
    if layout == "columns":
        return _split(area, n, vertical=False)
    if layout == "rows":
        return _split(area, n, vertical=True)
    if layout == "main-left" and n > 1:
        main, rest = _split(area, 2, vertical=portrait)
        return [main] + _split(rest, n - 1, vertical=not portrait)
    return _grid(area, n)


def arrange(windows: list[Window], monitor: int, layout: str = "auto") -> str:
    mon = get_monitor(monitor)
    if layout == "maximize" or (layout == "auto" and len(windows) == 1):
        for w in windows:
            maximize_on(w.hwnd, mon)
    else:
        for w, r in zip(windows, layout_rects(mon.work, len(windows), layout)):
            place(w.hwnd, r)
    for w in reversed(windows):  # bring them all to the front, first one on top
        focus(w.hwnd)
    return f"Arranged {len(windows)} window(s) on monitor {monitor} ({layout})."
