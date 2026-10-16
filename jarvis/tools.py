"""The actions Claude can take on this PC: JSON schemas + the Python that runs them."""
import ctypes
import json

from . import apps, modes, sysinfo, ui
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
        "name": "list_modes",
        "description": "List the saved modes (named window layouts like 'work') with their full definitions.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_mode",
        "description": "Run a saved mode: opens its apps/folders and tiles them on their monitors.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Mode name, e.g. 'work'"}},
            "required": ["name"],
        },
    },
    {
        "name": "save_mode",
        "description": "Create or replace a mode. To change an existing mode, call list_modes first and send the "
                       "complete new definition. Window items: 'app:<Start menu app name>', 'folder:<path>' "
                       "('folder:' alone = plain File Explorer), 'url:<https://...>' (opened, not placed). "
                       "Order inside a screen is top->bottom for 'rows' and left->right for 'columns'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short name without the word 'mode', e.g. 'gaming'"},
                "screens": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "monitor": {"type": "integer"},
                            "layout": {"type": "string", "enum": list(win.LAYOUTS)},
                            "windows": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["monitor", "layout", "windows"],
                    },
                },
                "say": {"type": "string", "description": "Short sentence Jarvis says when the mode is ready"},
                "icon": {"type": "string", "enum": list(modes.ICONS)},
            },
            "required": ["name", "screens"],
        },
    },
    {
        "name": "delete_mode",
        "description": "Delete a saved mode.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "ui_list",
        "description": "See the buttons, list items, links, tabs and text fields inside an app window (its "
                       "accessibility tree). Use it to find what to click when the user wants something INSIDE an "
                       "app, e.g. a project, chat, tab or menu. Use filter to search, e.g. filter='jarvis'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "window": {"type": "string", "description": "Window id like '#123456' or app name, e.g. 'claude'"},
                "filter": {"type": "string", "description": "Only controls whose name contains this text"},
            },
            "required": ["window"],
        },
    },
    {
        "name": "ui_click",
        "description": "Click a control inside an app window by its name (as shown by ui_list). Exact name "
                       "matches win over partial ones; use index to pick among several matches.",
        "input_schema": {
            "type": "object",
            "properties": {
                "window": {"type": "string"},
                "name": {"type": "string", "description": "Control name, e.g. 'Jarvis voice command assistant'"},
                "index": {"type": "integer", "description": "Which match to click when several share the name (0 = first)"},
            },
            "required": ["window", "name"],
        },
    },
    {
        "name": "type_text",
        "description": "Type text into the focused field of a window (click the field with ui_click first).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "window": {"type": "string", "description": "Window to focus first (optional)"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "press_keys",
        "description": "Press a key or shortcut in a window, e.g. 'enter', 'ctrl+k', 'ctrl+shift+t', 'esc'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keys": {"type": "string"},
                "window": {"type": "string", "description": "Window to focus first (optional)"},
            },
            "required": ["keys"],
        },
    },
    {
        "name": "system_info",
        "description": "Current CPU, GPU, memory, disk space, network speed and the apps using the most CPU.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "lock_pc",
        "description": "Lock the PC (Windows lock screen). Only when the user asks to lock it.",
        "input_schema": {"type": "object", "properties": {}},
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
    if name == "list_modes":
        return json.dumps(modes.load(), indent=1)
    if name == "run_mode":
        return modes.run(args["name"])
    if name == "save_mode":
        return modes.put(args["name"], args["screens"], args.get("say", ""), args.get("icon", "sparkles"))
    if name == "delete_mode":
        return modes.remove(args["name"])
    if name == "ui_list":
        return ui.list_controls(args["window"], args.get("filter", ""))
    if name == "ui_click":
        return ui.click(args["window"], args["name"], int(args.get("index", 0)))
    if name == "type_text":
        return ui.type_text(args["text"], args.get("window", ""))
    if name == "press_keys":
        return ui.press_keys(args["keys"], args.get("window", ""))
    if name == "system_info":
        return _system_summary()
    if name == "lock_pc":
        ctypes.windll.user32.LockWorkStation()
        return "Locked the PC."
    if name == "media_key":
        vk = MEDIA_KEYS[args["key"]]
        for _ in range(max(1, min(int(args.get("times", 1)), 50))):
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        return f"Pressed {args['key']}."
    raise ValueError(f"Unknown tool {name}")


def _gb(n: float) -> str:
    return f"{n / 1024**3:.0f} GB"


def _system_summary() -> str:
    s = sysinfo.snapshot()
    lines = [f"CPU {s['cpu']['pct']}% ({s['cpu']['name']}), memory {s['ram']['pct']}% "
             f"({_gb(s['ram']['used'])} of {_gb(s['ram']['total'])})"]
    for g in s["gpus"]:
        lines.append(f"GPU {g['name']}: {g['load']:.0f}% load, {g['vram_used']:.0f}/{g['vram_total']:.0f} MB VRAM, {g['temp']:.0f}°C")
    for d in s["disks"]:
        lines.append(f"Disk {d['name']}: {_gb(d['free'])} free of {_gb(d['total'])} ({d['pct']}% used)")
    lines.append("Busiest apps: " + ", ".join(f"{a['name']} {a['cpu']}% CPU" for a in s["apps"]))
    return "\n".join(lines)


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
