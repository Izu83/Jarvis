"""jarvis-setup.exe: the script inside it is the current one, and Windows PowerShell 5.1 can read it."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_setup_script_is_plain_ascii():
    assert all(b < 128 for b in (ROOT / "setup" / "setup.ps1").read_bytes())


def test_the_exe_has_the_current_script():   # run tools/make_setup.py after changing setup.ps1
    exe = (ROOT / "docs" / "jarvis-setup.exe").read_bytes()
    script = (ROOT / "setup" / "setup.ps1").read_bytes()
    assert script in exe or script.replace(b"\r\n", b"\n") in exe   # git may check it out with Windows line endings
    assert b"asInvoker" in exe   # runs as you: no admin prompt just because it's called "setup"
