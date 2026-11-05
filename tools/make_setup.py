"""Build docs/jarvis-setup.exe: the installer window (setup/installer.cs) with setup/setup.ps1 and the logo built in,
compiled with the C# compiler that ships with Windows (.NET Framework 4), with the Jarvis icon. It's in docs/ so GitHub Pages serves it:
https://izu83.github.io/Jarvis/jarvis-setup.exe (the page people open is https://izu83.github.io/Jarvis/download/, and /info/ says what Jarvis does)

    .venv\\Scripts\\python tools\\make_setup.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SETUP = ROOT / "setup"
OUT = ROOT / "docs" / "jarvis-setup.exe"
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"


def main() -> int:
    script = (SETUP / "setup.ps1").read_bytes()
    if any(b > 127 for b in script):   # Windows PowerShell 5.1 would misread it
        sys.exit("setup.ps1 must be plain ASCII")
    with tempfile.TemporaryDirectory() as tmp:
        ico = Path(tmp) / "jarvis.ico"
        Image.open(ROOT / "docs" / "icon-192.png").convert("RGBA").save(
            ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128)])
        cmd = [str(CSC), "/nologo", "/optimize+", "/target:winexe", "/platform:anycpu",
               "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", "/r:System.Management.dll",
               f"/out:{OUT}", f"/win32icon:{ico}", f"/win32manifest:{SETUP / 'launcher.manifest'}",
               f"/resource:{SETUP / 'setup.ps1'},setup.ps1", f"/resource:{ROOT / 'docs' / 'icon-192.png'},logo.png",
               str(SETUP / "installer.cs")]
        result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout.strip() or f"Built {OUT} ({OUT.stat().st_size // 1024} KB)")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
