"""The Jarvis orb: the same shape every time (so the icons don't change on their own), and drawable at any size."""
from jarvis import orb


def test_same_orb_every_time():
    a, b = orb.Orb(), orb.Orb()
    assert a.links == b.links and len(a.pts) == 250 + 7 * 24
    assert all(i != j for i, j in a.links)


def test_points_stay_round_the_middle():
    pts = orb.Orb(shell=55, knots=2, per_knot=9).project(t=1.0, spin=.5, R=15, cx=38, cy=40)
    assert all(abs(x - 38) < 30 and abs(y - 40) < 30 and 0 <= d < 1 for x, y, d in pts)


def test_icons_draw():
    assert orb.still(64).size == (64, 64)
    tray = orb.still(48, bold=True, background=False)
    assert tray.mode == "RGBA" and tray.getpixel((0, 0))[3] == 0   # see-through corners for the taskbar
