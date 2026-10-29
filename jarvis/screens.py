"""Screenshots for the phone app. Never sent to Claude, so they cost no tokens."""
import io

from PIL import Image, ImageDraw, ImageGrab

from . import windows as win

GAP = 10        # pixels between monitors in the picture, so the bezels read clearly
RADIUS = 14     # rounded screen corners
BACKGROUND = (12, 12, 18)


def capture_desktop(max_width: int = 2600, quality: int = 78) -> tuple[bytes, list[dict]]:
    """One picture of all monitors, laid out as they sit on the desk.

    Returns the JPEG and, per monitor, where it sits in the picture as fractions (0..1), so the phone can
    zoom into one monitor when you tap it.
    """
    monitors = sorted(win.list_monitors(), key=lambda m: m.bounds.x)
    left = min(m.bounds.x for m in monitors)
    top = min(m.bounds.y for m in monitors)
    width = max(m.bounds.x + m.bounds.w for m in monitors) - left
    height = max(m.bounds.y + m.bounds.h for m in monitors) - top
    desktop = ImageGrab.grab(all_screens=True)  # (0, 0) is the virtual desktop's top-left corner

    scale = min(1.0, max_width / width)
    canvas = Image.new("RGB", (round(width * scale), round(height * scale)), BACKGROUND)
    layout = []
    for m in monitors:
        b = m.bounds
        shot = desktop.crop((b.x - left, b.y - top, b.x - left + b.w, b.y - top + b.h))
        x, y = round((b.x - left) * scale) + GAP // 2, round((b.y - top) * scale) + GAP // 2
        w, h = round(b.w * scale) - GAP, round(b.h * scale) - GAP
        shot = shot.resize((w, h), Image.LANCZOS)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], RADIUS, fill=255)
        canvas.paste(shot, (x, y), mask)
        layout.append({"monitor": m.number, "x": x / canvas.width, "y": y / canvas.height,
                       "w": w / canvas.width, "h": h / canvas.height})
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue(), layout


def capture_monitor(number: int | None = None, max_width: int = 1400, quality: int = 60) -> tuple[bytes, dict]:
    """One monitor, small and quick, for the phone's live screen. Defaults to the one you're working on."""
    number = number or _default_monitor()
    m = win.get_monitor(number)
    b = m.bounds
    shot = ImageGrab.grab(bbox=(b.x, b.y, b.x + b.w, b.y + b.h), all_screens=True)
    scale = min(1.0, max_width / b.w)
    if scale < 1:
        shot = shot.resize((round(b.w * scale), round(b.h * scale)), Image.BILINEAR)
    buf = io.BytesIO()
    shot.save(buf, "JPEG", quality=quality)
    others = [x.number for x in sorted(win.list_monitors(), key=lambda x: x.bounds.x)]
    return buf.getvalue(), {"monitor": m.number, "w": b.w, "h": b.h, "all": others}


# ---------------------------------------------------------------- screenshots Claude looks at (costs tokens)

def _shot_dir():
    from .config import ROOT
    d = ROOT / ".brain" / "shots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _default_monitor() -> int:
    """The monitor with the active window, so 'look at my screen' sees what the user is working on."""
    import ctypes
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    w = win.get_window(hwnd) if hwnd else None
    if w and w.monitor:
        return w.monitor
    return win.list_monitors()[0].number


def capture_for_claude(monitor: int | None = None, max_side: int = 1400) -> str:
    """Save one monitor as a PNG for Claude to Read, and remember how its pixels map to the screen so
    mouse_click can take coordinates straight from the picture."""
    import json
    number = int(monitor) if monitor else _default_monitor()
    m = win.get_monitor(number)
    b = m.bounds
    shot = ImageGrab.grab(bbox=(b.x, b.y, b.x + b.w, b.y + b.h), all_screens=True)
    scale = min(1.0, max_side / max(b.w, b.h))
    if scale < 1:
        shot = shot.resize((round(b.w * scale), round(b.h * scale)), Image.LANCZOS)
    path = _shot_dir() / f"monitor{number}.png"
    shot.save(path, "PNG", optimize=True)
    (_shot_dir() / f"monitor{number}.json").write_text(json.dumps({"x": b.x, "y": b.y, "scale": scale}))
    return (f"Saved monitor {number} ({b.w}x{b.h}) as {path} ({shot.width}x{shot.height} px). Use the Read tool "
            f"to look at it. To click something in it, call mouse_click with monitor={number} and the x,y pixel "
            f"position in THIS picture.")


def to_screen(monitor: int, x: float, y: float) -> tuple[int, int]:
    """Picture coordinates from the last capture_for_claude of that monitor -> real screen coordinates."""
    import json
    f = _shot_dir() / f"monitor{int(monitor)}.json"
    if f.exists():
        info = json.loads(f.read_text())
    else:   # no picture yet: treat x,y as pixels on that monitor
        b = win.get_monitor(int(monitor)).bounds
        info = {"x": b.x, "y": b.y, "scale": 1.0}
    return round(info["x"] + x / info["scale"]), round(info["y"] + y / info["scale"])
