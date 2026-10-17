"""Start Jarvis automatically (and hidden) when you log in.

`python -m jarvis --startup` writes a tiny .vbs into the Windows Startup folder that launches Jarvis with
pythonw.exe (no console window) at login. `--startup-off` removes it.
"""
import os
import sys
from pathlib import Path

from .config import ROOT

STARTUP = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
VBS = STARTUP / "Jarvis.vbs"
PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"


def install() -> str:
    pyw = PYTHONW if PYTHONW.exists() else Path(sys.executable).with_name("pythonw.exe")
    script = (
        'Set sh = CreateObject("WScript.Shell")\n'
        f'sh.CurrentDirectory = "{ROOT}"\n'
        f'sh.Run """{pyw}"" -m jarvis", 0, False\n'
    )
    STARTUP.mkdir(parents=True, exist_ok=True)
    VBS.write_text(script, encoding="utf-8")
    return (f"Jarvis will now start automatically and hidden when you log in.\nLauncher: {VBS}\n"
            "Turn it off with:  run.bat --startup-off")


def uninstall() -> str:
    if VBS.exists():
        VBS.unlink()
        return "Jarvis will no longer start automatically at login."
    return "Jarvis wasn't set to start automatically."


def enabled() -> bool:
    return VBS.exists()
