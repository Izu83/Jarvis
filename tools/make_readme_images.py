"""Draws the README's banner and section headers into assets/readme/ (run: python tools/make_readme_images.py).

The banner carries the orb icon (docs/icon-192.png) inside the SVG as a data URI, since GitHub won't load
anything an SVG links to.
"""
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "readme"
FONT = "'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif"
CYAN, ICE, DIM = "#5fe3ff", "#e8fbff", "#8fb3c4"

HEADERS = {
    "what-it-does": "What it does",
    "how-it-works": "How it works",
    "getting-started": "Getting started",
    "things-you-can-say": "Things you can say",
    "on-your-iphone": "On your iPhone",
    "the-models": "The models",
    "privacy-and-safety": "Privacy and safety",
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


if __name__ == "__main__":
    (OUT / "headers").mkdir(parents=True, exist_ok=True)
    (OUT / "banner.svg").write_text(banner(), encoding="utf-8")
    for name, text in HEADERS.items():
        (OUT / "headers" / f"{name}.svg").write_text(header(text), encoding="utf-8")
    print(f"Wrote the banner and {len(HEADERS)} headers to {OUT}")
