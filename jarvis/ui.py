"""Acting *inside* apps: read a window's buttons/links/tabs through Windows UI Automation (the accessibility
tree screen readers use) and click, type or press keys. Text only, so it costs far fewer tokens than screenshots."""
import ctypes
import ctypes.wintypes as wt
import time

import uiautomation as auto

from . import windows as win

CLICKABLE = {"ButtonControl", "ListItemControl", "HyperlinkControl", "TabItemControl", "TreeItemControl",
             "MenuItemControl", "CheckBoxControl", "RadioButtonControl", "ComboBoxControl", "EditControl",
             "SplitButtonControl", "DataItemControl"}
MAX_WALK = 1500


def _window(query: str) -> win.Window:
    found, _ = win.resolve_windows([query], timeout=6)
    if not found:
        raise ValueError(f"No open window matches '{query}'. Open the app first.")
    return found[0]


def _controls(hwnd: int) -> list:
    root = auto.ControlFromHandle(hwnd)
    out = []
    for i, (ctrl, _depth) in enumerate(auto.WalkControl(root, maxDepth=60)):
        if i > MAX_WALK:
            break
        if ctrl.ControlTypeName in CLICKABLE and (ctrl.Name or "").strip():
            out.append(ctrl)
    return out


def _controls_warm(hwnd: int) -> list:
    """Chromium/Electron apps only build their accessibility tree after someone asks, so ask twice if needed."""
    ctrls = _controls(hwnd)
    if len(ctrls) < 8:
        time.sleep(1.5)
        ctrls = _controls(hwnd)
    return ctrls


def list_controls(window: str, filter: str = "", limit: int = 60) -> str:
    with auto.UIAutomationInitializerInThread():
        w = _window(window)
        seen, lines = set(), []
        for c in _controls_warm(w.hwnd):
            name = " ".join(c.Name.split())[:90]
            kind = c.ControlTypeName.removesuffix("Control")
            if filter and filter.lower() not in name.lower():
                continue
            if (kind, name) in seen:
                continue
            seen.add((kind, name))
            lines.append(f"[{kind}] {name}")
            if len(lines) >= limit:
                lines.append("... (more; use a filter)")
                break
        head = f"Controls in {w.describe()}" + (f" matching '{filter}'" if filter else "") + ":"
        return head + "\n" + ("\n".join(lines) if lines else "(none found)")


def _find(hwnd: int, name: str) -> list:
    want = " ".join(name.lower().split())
    ctrls = _controls_warm(hwnd)
    exact = [c for c in ctrls if " ".join(c.Name.split()).lower() == want]
    return exact or [c for c in ctrls if want in " ".join(c.Name.split()).lower()]


def _activate(ctrl) -> bool:
    """Press a control the way assistive tech does (works even if it's scrolled off screen)."""
    for getter, action in (("GetInvokePattern", "Invoke"), ("GetSelectionItemPattern", "Select")):
        try:
            pattern = getattr(ctrl, getter)()
            if pattern:
                getattr(pattern, action)()
                return True
        except Exception:
            continue
    return False


def _mouse_click(ctrl, double: bool = False) -> bool:
    """Fallback: a real left click (or double-click) in the middle of the control, then put the cursor back."""
    try:
        r = ctrl.BoundingRectangle
        if r.width() <= 0 or r.height() <= 0:
            return False
        old = wt.POINT()
        user32 = ctypes.windll.user32
        user32.GetCursorPos(ctypes.byref(old))
        user32.SetCursorPos((r.left + r.right) // 2, (r.top + r.bottom) // 2)
        for _ in range(2 if double else 1):
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # left down
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # left up
            time.sleep(0.05)
        user32.SetCursorPos(old.x, old.y)
        return True
    except Exception:
        return False


def click(window: str, name: str, index: int = 0, double: bool = False) -> str:
    with auto.UIAutomationInitializerInThread():
        w = _window(window)
        win.focus(w.hwnd)
        for attempt in range(2):  # the UI may re-render between finding and clicking; then look again
            matches = _find(w.hwnd, name)
            if not matches:
                if attempt:
                    raise ValueError(f"No control named '{name}' in {w.app}. Use ui_list to see what's there.")
                time.sleep(0.8)
                continue
            target = matches[min(index, len(matches) - 1)]
            label = f"[{target.ControlTypeName.removesuffix('Control')}] " + " ".join(target.Name.split())[:80]
            if double:   # e.g. double-click a Spotify track to play it
                if _mouse_click(target, double=True):
                    return f"Double-clicked {label}"
            elif _activate(target) or _mouse_click(target):
                extra = f" ({len(matches)} matches, used #{matches.index(target)})" if len(matches) > 1 else ""
                return f"Clicked {label}{extra}"
            time.sleep(0.8)
        raise RuntimeError(f"Found '{name}' but couldn't click it.")


# ---------------------------------------------------------------- keyboard

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x2, 0x4
VK = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "enter": 0x0D, "return": 0x0D,
      "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "up": 0x26,
      "down": 0x28, "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
      **{f"f{i}": 0x6F + i for i in range(1, 13)}}


def _send(inputs: list[INPUT]) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    i = INPUT(type=1)
    i.ki = KEYBDINPUT(vk, scan, flags, 0, None)
    return i


def type_text(text: str, window: str = "") -> str:
    if window:
        win.focus(_window(window).hwnd)
        time.sleep(0.15)
    events = []
    for ch in text:
        code = ord(ch)
        # characters outside the Basic Multilingual Plane (emoji) go as a UTF-16 surrogate pair
        codes = [code] if code <= 0xFFFF else [0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF)]
        for c in codes:
            events += [_key(0, c, KEYEVENTF_UNICODE), _key(0, c, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    _send(events)
    return f"Typed {len(text)} characters."


def press_keys(keys: str, window: str = "") -> str:
    """e.g. 'ctrl+k', 'enter', 'alt+tab', 'ctrl+shift+t'."""
    if window:
        win.focus(_window(window).hwnd)
        time.sleep(0.15)
    parts = [p.strip().lower() for p in keys.split("+") if p.strip()]
    codes = []
    for p in parts:
        if p in VK:
            codes.append(VK[p])
        elif len(p) == 1:
            codes.append(ctypes.windll.user32.VkKeyScanW(ord(p)) & 0xFF)
        else:
            raise ValueError(f"Unknown key '{p}'")
    _send([_key(c) for c in codes] + [_key(c, 0, KEYEVENTF_KEYUP) for c in reversed(codes)])
    return f"Pressed {keys}."
