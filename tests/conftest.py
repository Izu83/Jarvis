"""Lets the pure-Python parts of Jarvis be tested on any OS: the Windows-only modules (window handling, app
launching, the tool runner, clipboard) are replaced with small fakes, and config points at a temporary folder so
tests never touch your real config.json, modes.json or .brain/."""
import pathlib
import sys
import tempfile
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = pathlib.Path(tempfile.mkdtemp(prefix="jarvis-tests-"))

config = types.ModuleType("jarvis.config")
config.ROOT = TMP
config.settings = {
    "monitor_names": {"1": "right vertical monitor", "2": "main / middle monitor", "3": "left monitor"},
    "known_folders": {"projects": "D:\\Projects"},
    "use_claude": True,
}
config.set_setting = config.settings.__setitem__
config.secret = lambda name: ""
sys.modules["jarvis.config"] = config


class FakeTools(types.ModuleType):
    """Records every tool the local parser runs instead of touching the PC."""
    def __init__(self):
        super().__init__("jarvis.tools")
        self.calls = []

    def run(self, name, args):
        self.calls.append((name, args))
        return f"ran {name}"


tools = FakeTools()
sys.modules["jarvis.tools"] = tools

windows = types.ModuleType("jarvis.windows")
windows.LAYOUTS = ("auto", "rows", "columns", "grid", "maximize")
windows.list_windows = lambda: []
windows.list_monitors = lambda: []
sys.modules["jarvis.windows"] = windows

apps = types.ModuleType("jarvis.apps")
apps.find_app = lambda name: None   # no installed apps: "open X" falls through to Claude
apps.find_folder = lambda name, root="": []
apps.SITES = {"instagram": "https://www.instagram.com/"}
apps.site_url = lambda name: apps.SITES.get(name.lower().strip())
apps.opened = []   # (url, browser) pairs the parser opened
apps.open_url = lambda url, browser="": apps.opened.append((url, browser)) or f"Opened {url}."
sys.modules["jarvis.apps"] = apps

pc = types.ModuleType("jarvis.pc")
pc.now_playing = lambda: None   # "can't tell" unless a test says otherwise
sys.modules["jarvis.pc"] = pc


@pytest.fixture
def ran():
    """The tools the parser ran during one test."""
    tools.calls.clear()
    return tools.calls
