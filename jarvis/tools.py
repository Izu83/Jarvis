"""The actions Claude can take on this PC: JSON schemas + the Python that runs them."""
import ctypes

from . import apps
from . import windows as win
from .config import settings

TOOLS = [
    {
        "name": "open_app",
        "description": "Open an installed app by name (anything in the Start menu: Spotify, Claude, Discord, "
                       "Chrome, Visual Studio Code, ...). If it's already running, focuses the existing window. "
                       "Returns the window id (#number) to use with arrange_windows.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "App name as the user said it"}},
            "required": ["name"],
        },
    },
    {
        "name": "open_folder",
        "description": "Open a folder in File Explorer in a new window. Omit path for a plain File Explorer window. "
                       "Returns the window id.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute folder path, e.g. D:\\Projects\\Jarvis"}},
        },
    },
    {
        "name": "find_folder",
        "description": "Search for folders by name when you don't know the exact path. Returns matching paths, "
                       "exact name matches first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Folder name or part of it"},
                "root": {"type": "string", "description": "Where to search, e.g. D:\\ (default: all drives)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "open_target",
        "description": "Open a URL in the default browser, or open a file with its default program.",
        "input_schema": {
            "type": "object",
            "properties": {"target": {"type": "string", "description": "https:// URL or absolute file path"}},
            "required": ["target"],
        },
    },
    {
        "name": "list_windows",
        "description": "List open windows with their id, app, title and current monitor.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "arrange_windows",
        "description": "Move windows onto one monitor and tile them neatly. 'auto' layout: 1 window is maximized; "
                       "on landscape monitors 2 go side by side, 3 become one big on the left + two stacked on the right, "
                       "4+ a grid; on portrait monitors they stack top to bottom. Waits up to 12 s for windows of "
                       "apps that are still starting. List the windows in order of importance (first = biggest/top-left).",
        "input_schema": {
            "type": "object",
            "properties": {
                "windows": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Window ids like '#123456' (preferred, from open_* results) or app/title names",
                },
                "monitor": {"type": "integer", "description": "Windows monitor number"},
                "layout": {"type": "string", "enum": list(win.LAYOUTS)},
            },
            "required": ["windows", "monitor"],
        },
    },
    {
        "name": "window_action",
        "description": "Focus, minimize, maximize (on its current monitor) or close one window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "window": {"type": "string", "description": "Window id like '#123456' or app/title name"},
                "action": {"type": "string", "enum": ["focus", "minimize", "maximize", "close"]},
            },
            "required": ["window", "action"],
        },
    },
    {
        "name": "media_key",
        "description": "Press a media key (controls Spotify or whatever is playing) or change the system volume.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string",
                        "enum": ["play_pause", "next", "previous", "volume_up", "volume_down", "mute"]},
                "times": {"type": "integer", "description": "Repeat count, e.g. 5 volume_up presses = +10%"},
            },
            "required": ["key"],
        },
    },
]

MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1,
              "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}


def _one_window(query: str) -> win.Window:
    found, _ = win.resolve_windows([query], timeout=5)
    if not found:
        raise ValueError(f"No open window matches '{query}'.")
    return found[0]


def run(name: str, args: dict) -> str:
    if name == "open_app":
        return apps.open_app(args["name"])
    if name == "open_folder":
        return apps.open_folder(args.get("path", ""))
    if name == "find_folder":
        hits = apps.find_folder(args["name"], args.get("root", ""))
        return "\n".join(hits) if hits else "No folders found."
    if name == "open_target":
        return apps.open_target(args["target"])
    if name == "list_windows":
        return "\n".join(w.describe() for w in win.list_windows()) or "No windows open."
    if name == "arrange_windows":
        found, missing = win.resolve_windows(args["windows"])
        if not found:
            return f"Error: none of these windows were found: {missing}"
        result = win.arrange(found, int(args["monitor"]), args.get("layout", "auto"))
        moved = [win.get_window(w.hwnd) or w for w in found]
        result += "\n" + "\n".join(w.describe() for w in moved)
        if missing:
            result += f"\nNot found (skipped): {missing}"
        return result
    if name == "window_action":
        w = _one_window(args["window"])
        action = args["action"]
        if action == "focus":
            win.focus(w.hwnd)
        elif action == "minimize":
            win.minimize(w.hwnd)
        elif action == "maximize":
            win.maximize_on(w.hwnd, win.get_monitor(w.monitor))
        elif action == "close":
            win.close(w.hwnd)
        return f"{action}: {w.describe()}"
    if name == "media_key":
        vk = MEDIA_KEYS[args["key"]]
        for _ in range(max(1, min(int(args.get("times", 1)), 50))):
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        return f"Pressed {args['key']}."
    raise ValueError(f"Unknown tool {name}")


def environment_description() -> str:
    """Static facts about this PC for the system prompt (kept stable so it caches)."""
    names = settings["monitor_names"]
    lines = ["Monitors (Windows numbering):"]
    for m in win.list_monitors():
        shape = "portrait" if m.portrait else "landscape"
        extra = " (primary)" if m.primary else ""
        lines.append(f"- Monitor {m.number}: {names.get(str(m.number), '')} - {m.bounds.w}x{m.bounds.h} {shape}{extra}")
    lines.append("Known folders:")
    lines += [f"- {k}: {v}" for k, v in settings["known_folders"].items()]
    return "\n".join(lines)
