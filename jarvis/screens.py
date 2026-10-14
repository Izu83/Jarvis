"""Screenshots of every monitor, for the phone app. Never sent to Claude, so they cost no tokens."""
import io

from PIL import ImageGrab

from . import windows as win


def capture(max_side: int = 1600, quality: int = 72) -> list[tuple[win.Monitor, bytes]]:
    """JPEGs of each monitor, ordered left to right as they sit on the desk."""
    monitors = sorted(win.list_monitors(), key=lambda m: m.bounds.x)
    left = min(m.bounds.x for m in monitors)
    top = min(m.bounds.y for m in monitors)
    desktop = ImageGrab.grab(all_screens=True)  # (0, 0) is the virtual desktop's top-left corner
    shots = []
    for m in monitors:
        b = m.bounds
        img = desktop.crop((b.x - left, b.y - top, b.x - left + b.w, b.y - top + b.h))
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
        shots.append((m, buf.getvalue()))
    return shots
