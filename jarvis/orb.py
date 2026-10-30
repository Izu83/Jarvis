"""The Jarvis orb: a sphere of glowing points joined by threads, with a few bright knots where they bunch up.

It's Jarvis's face everywhere: the phone's Voice mode animates it (in JavaScript, same recipe), the PC pop-up shows a
small live one, and the app and tray icons are a still of it. `python -m jarvis.orb` redraws the icons in docs/.
"""
import math
import random
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"

# the colour of each state, the same as on the phone
COLORS = {"idle": (70, 215, 255), "listening": (60, 255, 170), "thinking": (150, 120, 255),
          "speaking": (60, 225, 255), "error": (255, 110, 100)}


class Orb:
    def __init__(self, shell=250, knots=7, per_knot=24, dust=0.16, seed=7):
        rnd = random.Random(seed)
        self.pts, self.links = [], []

        def add(x, y, z, knot):
            m = math.sqrt(x * x + y * y + z * z) or 1
            loose = not knot and rnd.random() < dust   # a few float loose around it
            self.pts.append({"x": x / m, "y": y / m, "z": z / m, "dust": loose, "knot": knot,
                             "shell": 1.1 + rnd.random() * .35 if loose else 1.0, "size": .7 + rnd.random() * .9,
                             "seed": rnd.random() * 6.283})

        turn = math.pi * (3 - math.sqrt(5))
        for i in range(shell):   # evenly spread over the sphere, then nudged so it isn't a neat lattice
            y0 = 1 - (i + .5) / shell * 2
            r, a = math.sqrt(1 - y0 * y0), turn * i
            j = lambda: (rnd.random() - .5) * .16
            add(math.cos(a) * r + j(), y0 + j(), math.sin(a) * r + j(), False)
        g = lambda: (rnd.random() + rnd.random() + rnd.random() - 1.5) * .5
        for _ in range(knots):
            cx, cy, cz = g(), g(), g()
            m = math.sqrt(cx * cx + cy * cy + cz * cz) or 1
            for _ in range(per_knot):
                add(cx / m + g() * .5, cy / m + g() * .5, cz / m + g() * .5, True)
        seen = set()
        for i, p in enumerate(self.pts):   # threads to the nearest neighbours, and a few long ones across the knots
            if p["dust"]:
                continue
            near = sorted(((p["x"] - q["x"]) ** 2 + (p["y"] - q["y"]) ** 2 + (p["z"] - q["z"]) ** 2, j)
                          for j, q in enumerate(self.pts) if j != i and not q["dust"])
            near = [n for n in near if n[0] < .3]
            picks = [j for d, j in near if d < .07][:4 if p["knot"] else 3]
            if p["knot"] and len(near) > 8 and rnd.random() < .45:
                picks.append(near[rnd.randrange(8, len(near))][1])
            for j in picks:
                k = (min(i, j), max(i, j))
                if k not in seen:
                    seen.add(k)
                    self.links.append(k)

    def project(self, t, spin, R, cx, cy, level=0.0):
        """Where each point is on screen: [(x, y, depth 0 far … 1 near)]."""
        ax = .38 + .18 * math.sin(t * .23)
        sy, cyr, sx, cxr = math.sin(spin), math.cos(spin), math.sin(ax), math.cos(ax)
        breath = 1 + .025 * math.sin(t * 1.25)
        out = []
        for p in self.pts:
            wave = math.sin(p["x"] * 3.1 + t * 2.3) * math.sin(p["y"] * 2.7 - t * 1.9) * math.sin(p["z"] * 3.3 + t * 1.6)
            s = R * p["shell"] * (breath + level * (.08 + .3 * wave))
            x, y, z = p["x"] * s, p["y"] * s, p["z"] * s
            x1, z1 = x * cyr + z * sy, -x * sy + z * cyr
            y2, z2 = y * cxr - z1 * sx, y * sx + z1 * cxr
            f = 3.2 / (3.2 - z2 / R)
            out.append((cx + x1 * f, cy + y2 * f, max(0.0, min(.999, (z2 / R + 1.3) / 2.6))))
        return out


def still(size: int, color=COLORS["idle"], bold=False, background=True):
    """The orb as a picture (PIL RGB/RGBA image), drawn big and scaled down so every thread is smooth.
    bold: fewer, thicker threads that still read at tray-icon size."""
    import numpy as np
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    k = 4
    S = size * k
    orb = Orb(shell=26, knots=1, per_knot=6, dust=0, seed=5) if bold else Orb()
    R = S * (.36 if bold else .3)
    pts = orb.project(t=2.0, spin=.6, R=R, cx=S / 2, cy=S / 2)
    unit = S / 512   # line widths are designed at 512 px
    r, g, b = color
    layer = Image.new("RGB", (S, S))
    if bold:   # at tray size the threads vanish, so a bright rim carries the shape
        ImageDraw.Draw(layer).ellipse([S / 2 - R * 1.04, S / 2 - R * 1.04, S / 2 + R * 1.04, S / 2 + R * 1.04],
                                      outline=(int(r * .75), int(g * .75), int(b * .75)), width=round(S * .06))
    for depth in range(4):   # far to near, each layer brighter
        lay = Image.new("RGB", (S, S))
        d = ImageDraw.Draw(lay)
        a = min(1.0, (.16 + depth * .22) * (2.2 if bold else 1))
        w = max(1, round(S * .028 * (.7 + depth * .25) if bold else 1.2 * unit * (.7 + depth * .25)))
        for i, j in orb.links:
            p, q = pts[i], pts[j]
            if int((p[2] + q[2]) * 2) == depth:
                d.line([p[:2], q[:2]], fill=(int(r * a), int(g * a), int(b * a)), width=w)
        for (x, y, dep), p in zip(pts, orb.pts):
            if int(dep * 4) == depth:
                s = (S * .02 if bold else 1.15 * unit) * p["size"] * (.7 + depth * .35) * (1.3 if p["knot"] else 1)
                a2 = min(1.0, .45 + depth * .2)
                d.ellipse([x - s, y - s, x + s, y + s],
                            fill=(int(min(255, r + 60) * a2), int(min(255, g + 30) * a2), int(b * a2)))
        layer = ImageChops.add(layer, lay)
    glow = layer.filter(ImageFilter.GaussianBlur(S * .02))
    halo = layer.filter(ImageFilter.GaussianBlur(S * .08))
    art = ImageChops.add(ImageChops.add(layer, glow), halo)
    if background:
        yy, xx = np.mgrid[0:S, 0:S].astype(np.float32) / S - .5
        dist = np.sqrt(xx * xx + yy * yy) / .71
        inner, outer = np.array([12, 32, 50], np.float32), np.array([3, 6, 12], np.float32)
        bg = inner + (outer - inner) * np.clip(dist * 1.25, 0, 1)[..., None]
        core = np.exp(-(dist / .32) ** 2)[..., None] * np.array([r, g, b], np.float32) * .16   # soft light inside
        base = Image.fromarray(np.clip(bg + core, 0, 255).astype(np.uint8))
        out = ImageChops.add(base, art)
    else:
        alpha = art.convert("L").point(lambda v: min(255, v * 3))
        out = art.copy()
        out.putalpha(alpha)
    return out.resize((size, size), Image.LANCZOS)


def make_icons() -> None:
    """The app icons in docs/ (home screen, notifications, browser tab, pairing window) and the tray's source."""
    big = still(1024)
    for name, px in (("icon-512", 512), ("icon-192", 192), ("apple-touch-icon", 180)):
        big.resize((px, px), resample=3).save(DOCS / f"{name}.png", optimize=True)
    still(256, bold=True).resize((64, 64), resample=3).save(DOCS / "favicon.png", optimize=True)


if __name__ == "__main__":
    make_icons()
    print("Icons written to", DOCS)
