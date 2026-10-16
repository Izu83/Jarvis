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
