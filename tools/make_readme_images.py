"""Draws the README's banner, section headers and diagrams into assets/readme/ (run: python tools/make_readme_images.py).

The banner carries the orb icon (docs/icon-192.png) inside the SVG as a data URI, since GitHub won't load
anything an SVG links to. The app screenshots come from tools/make_readme_shots.js.
"""
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "readme"
FONT = "'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif"
CYAN, ICE, DIM, CARD, LINE = "#5fe3ff", "#e8fbff", "#8fb3c4", "#0c1c2a", "#1d3a4f"

HEADERS = {
    "what-it-does": "What it does",
    "how-it-works": "How it works",
    "getting-started": "Getting started",
    "talking-to-jarvis": "Talking to Jarvis",
    "things-you-can-say": "Things you can say",
    "modes": "Modes",
    "on-your-iphone": "On your iPhone",
    "voice-mode": "Voice mode",
    "your-pc-at-a-glance": "Your PC at a glance",
    "the-desktop-app": "The desktop app",
    "the-models": "The models",
    "jarvis-messages-you": "Jarvis messages you",
    "privacy-and-safety": "Privacy and safety",
    "settings": "Settings",
    "troubleshooting": "Troubleshooting",
    "project-layout": "Project layout",
    "credits": "Credits",
}


def banner() -> str:
    orb = base64.b64encode((ROOT / "docs" / "icon-192.png").read_bytes()).decode()
    return f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1070" height="296" viewBox="0 0 1070 296" role="img" aria-label="Jarvis, made by Nikolay Rangelov">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#03070c"/>
      <stop offset="0.55" stop-color="#071422"/>
      <stop offset="1" stop-color="#0b2233"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="{CYAN}" stop-opacity="0.28"/>
      <stop offset="1" stop-color="{CYAN}" stop-opacity="0"/>
    </radialGradient>
    <clipPath id="round"><circle cx="880" cy="148" r="118"/></clipPath>
  </defs>
  <rect width="1070" height="296" rx="26" fill="url(#bg)"/>
  <rect x="1" y="1" width="1068" height="294" rx="25" fill="none" stroke="{CYAN}" stroke-opacity="0.3" stroke-width="2"/>
  <circle cx="880" cy="148" r="150" fill="url(#glow)"/>
  <image x="762" y="30" width="236" height="236" clip-path="url(#round)" xlink:href="data:image/png;base64,{orb}" href="data:image/png;base64,{orb}"/>
  <g font-family="{FONT}">
    <text x="64" y="116" font-size="76" font-weight="700" fill="{ICE}" letter-spacing="2">JARVIS</text>
    <text x="66" y="158" font-size="22" fill="{ICE}" fill-opacity="0.9">Your own assistant for your Windows PC.</text>
    <text x="66" y="186" font-size="22" fill="{ICE}" fill-opacity="0.9">Talk to it, or text it from your iPhone, and it gets it done.</text>
    <rect x="66" y="204" width="56" height="3" rx="1.5" fill="{CYAN}"/>
    <text x="66" y="240" font-size="24" font-weight="600" fill="{ICE}">Made by Nikolay Rangelov</text>
    <text x="66" y="272" font-size="17" fill="{DIM}">Python  ·  Claude  ·  Whisper  ·  iPhone web app</text>
  </g>
</svg>
"""


def header(text: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="76" viewBox="0 0 1000 76" role="img" aria-label="{text}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#050b12"/>
      <stop offset="1" stop-color="#0b2233"/>
    </linearGradient>
  </defs>
  <rect width="1000" height="76" rx="16" fill="url(#g)"/>
  <rect x="0" y="0" width="12" height="76" rx="6" fill="{CYAN}"/>
  <text x="38" y="51" font-family="{FONT}" font-size="38" font-weight="700" fill="{ICE}">{text}</text>
</svg>
"""


def _svg(w: int, h: int, label: str, body: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{label}">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 0 L10 5 L0 10 z" fill="{CYAN}"/>
    </marker>
  </defs>
  <rect width="{w}" height="{h}" rx="20" fill="#050b12"/>
  <rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="19" fill="none" stroke="{CYAN}" stroke-opacity="0.18" stroke-width="2"/>
  <g font-family="{FONT}">
{body}
  </g>
</svg>
"""


def _card(x, y, w, h, title, sub="", accent=False, size=19) -> str:
    stroke = CYAN if accent else LINE
    out = f'    <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{CARD}" stroke="{stroke}" stroke-width="{2 if accent else 1.5}"/>\n'
    ty = y + (h / 2 + 7 if not sub else h / 2 - 4)
    out += f'    <text x="{x + 18}" y="{ty}" font-size="{size}" font-weight="600" fill="{ICE}">{title}</text>\n'
    if sub:
        out += f'    <text x="{x + 18}" y="{ty + 23}" font-size="14.5" fill="{DIM}">{sub}</text>\n'
    return out


def _arrow(x1, y1, x2, y2) -> str:
    return (f'    <path d="M{x1} {y1} C{(x1 + x2) / 2} {y1} {(x1 + x2) / 2} {y2} {x2} {y2}" fill="none" stroke="{CYAN}" '
            f'stroke-opacity="0.75" stroke-width="2" marker-end="url(#arrow)"/>\n')


def flow() -> str:
    """How it works: three ways in, Jarvis on the PC, what he does."""
    b = f'    <text x="40" y="52" font-size="15" font-weight="700" fill="{DIM}" letter-spacing="1.5">YOU</text>\n'
    b += f'    <text x="370" y="52" font-size="15" font-weight="700" fill="{DIM}" letter-spacing="1.5">ON YOUR PC</text>\n'
    b += f'    <text x="752" y="52" font-size="15" font-weight="700" fill="{DIM}" letter-spacing="1.5">HE DOES IT</text>\n'
    inputs = [("“Hey Jarvis”", "your PC's microphone"), ("iPhone or desktop app", "text or voice, from anywhere"),
              ("Siri", "“Hey Siri, Ask Jarvis”")]
    for i, (t, s) in enumerate(inputs):
        y = 80 + i * 108
        b += _card(30, y, 270, 80, t, s)
        b += _arrow(302, y + 40, 366, 236)
    b += f'    <rect x="370" y="80" width="330" height="296" rx="18" fill="{CARD}" stroke="{CYAN}" stroke-width="2"/>\n'
    b += f'    <text x="394" y="118" font-size="23" font-weight="700" fill="{ICE}">Jarvis</text>\n'
    steps = [("1", "Hears you", "wake word + Whisper, on your PC"), ("2", "Quick commands", "music, volume, modes: instant, no AI"),
             ("3", "Claude handles the prompt", "plans the steps, uses the tools")]
    for i, (n, t, s) in enumerate(steps):
        y = 146 + i * 74
        b += f'    <circle cx="410" cy="{y + 14}" r="15" fill="none" stroke="{CYAN}" stroke-width="2"/>\n'
        b += f'    <text x="410" y="{y + 20}" font-size="16" font-weight="700" fill="{CYAN}" text-anchor="middle">{n}</text>\n'
        b += f'    <text x="438" y="{y + 12}" font-size="18" font-weight="600" fill="{ICE}">{t}</text>\n'
        b += f'    <text x="438" y="{y + 35}" font-size="14.5" fill="{DIM}">{s}</text>\n'
    outputs = ["Apps, windows, monitors", "Music and volume", "Files and email", "Websites, out of sight", "Answers on your phone"]
    for i, t in enumerate(outputs):
        y = 80 + i * 60
        b += _arrow(702, 228, 748, y + 22)
        b += _card(752, y, 218, 46, t, size=16.5)
    return _svg(1000, 410, "How Jarvis works", b)


def work_mode() -> str:
    """The default work mode, monitor by monitor, the way they stand on the desk."""
    b = ""

    def monitor(x, y, w, h, label, panes, vertical=False):
        out = f'    <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="#08131d" stroke="{CYAN}" stroke-opacity="0.65" stroke-width="2.5"/>\n'
        n = len(panes)
        for i, p in enumerate(panes):
            if vertical:
                ph = (h - 16 - (n - 1) * 8) / n
                px, py, pw = x + 8, y + 8 + i * (ph + 8), w - 16
            else:
                pw = (w - 16 - (n - 1) * 8) / n
                px, py, ph = x + 8 + i * (pw + 8), y + 8, h - 16
            out += f'    <rect x="{px:.0f}" y="{py:.0f}" width="{pw:.0f}" height="{ph:.0f}" rx="6" fill="{CARD}" stroke="{LINE}"/>\n'
            out += f'    <text x="{px + pw / 2:.0f}" y="{py + ph / 2 + 6:.0f}" font-size="16" font-weight="600" fill="{ICE}" text-anchor="middle">{p}</text>\n'
        cx = x + w / 2
        out += f'    <rect x="{cx - 5:.0f}" y="{y + h}" width="10" height="22" fill="{LINE}"/>\n'
        out += f'    <rect x="{cx - 46:.0f}" y="{y + h + 22}" width="92" height="7" rx="3.5" fill="{LINE}"/>\n'
        out += f'    <text x="{cx:.0f}" y="{y + h + 58}" font-size="15" fill="{DIM}" text-anchor="middle">{label}</text>\n'
        return out

    b += monitor(30, 120, 300, 176, "Left (3)", ["Opera", "File Explorer"])
    b += monitor(352, 96, 380, 214, "Main (2)", ["Visual Studio Code"])
    b += monitor(756, 40, 190, 300, "Right, vertical (1)", ["File Explorer", "Claude", "Spotify"], vertical=True)
    b += f'    <text x="30" y="60" font-size="24" font-weight="700" fill="{ICE}">“Jarvis, work mode”</text>\n'
    b += f'    <text x="30" y="88" font-size="15" fill="{DIM}">every app opens in its place, in about a second, with no AI</text>\n'
    return _svg(980, 410, "Work mode across three monitors", b)


def connection() -> str:
    """How the phone reaches the PC: an encrypted tunnel, and a relay that only passes on the address."""
    b = _card(30, 70, 220, 84, "Your iPhone", "the Jarvis web app", accent=True)
    b += _card(390, 70, 220, 84, "Cloudflare tunnel", "sees only scrambled data")
    b += _card(750, 70, 220, 84, "Jarvis on your PC", "holds the same key", accent=True)
    for x1, x2 in ((252, 386), (612, 746)):
        b += f'    <line x1="{x1}" y1="112" x2="{x2}" y2="112" stroke="{CYAN}" stroke-width="2.5" marker-end="url(#arrow)" marker-start="url(#arrow)"/>\n'
    b += f'    <text x="319" y="100" font-size="13.5" fill="{CYAN}" text-anchor="middle">AES-256-GCM</text>\n'
    b += f'    <text x="679" y="100" font-size="13.5" fill="{CYAN}" text-anchor="middle">AES-256-GCM</text>\n'
    b += _card(390, 200, 220, 70, "ntfy relay", "passes on the PC's address")
    b += f'    <path d="M860 156 L860 235 L614 235" fill="none" stroke="{CYAN}" stroke-opacity="0.5" stroke-width="2" stroke-dasharray="6 6" marker-end="url(#arrow)"/>\n'
    b += f'    <path d="M386 235 L140 235 L140 160" fill="none" stroke="{CYAN}" stroke-opacity="0.5" stroke-width="2" stroke-dasharray="6 6" marker-end="url(#arrow)"/>\n'
    b += f'    <text x="30" y="44" font-size="15" font-weight="700" fill="{DIM}" letter-spacing="1.5">END-TO-END ENCRYPTED, FROM ANYWHERE</text>\n'
    return _svg(1000, 300, "How your phone connects to your PC", b)


if __name__ == "__main__":
    (OUT / "headers").mkdir(parents=True, exist_ok=True)
    (OUT / "banner.svg").write_text(banner(), encoding="utf-8")
    for name, text in HEADERS.items():
        (OUT / "headers" / f"{name}.svg").write_text(header(text), encoding="utf-8")
    for name, draw in (("flow", flow), ("work-mode", work_mode), ("connection", connection)):
        (OUT / f"{name}.svg").write_text(draw(), encoding="utf-8")
    print(f"Wrote the banner, {len(HEADERS)} headers and 3 diagrams to {OUT}")
