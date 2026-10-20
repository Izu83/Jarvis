"""The actions Claude can take on this PC: JSON schemas + the Python that runs them."""
import ctypes
import json
import subprocess
import time

import logging

from . import apps, approval, files, gesture, modes, pc, power, screens, spotify, sysinfo, ui, watchers
from . import windows as win
from .config import settings

log = logging.getLogger("jarvis.tools")

# Which risk categories each autonomy level auto-approves (everything else still asks the user).
# Hard safety stays in code regardless: files.check_deletable blocks system/Windows/main-user folders.
_AUTO = {"ask": (), "risky_only": ("cmd", "change"), "full": ("cmd", "change", "delete")}


def _confirm(risk: str, title: str, detail: str, confirm: str) -> bool:
    """Ask the user to approve an action, unless the chosen autonomy level auto-approves this kind of action."""
    if risk in _AUTO.get(settings.get("autonomy", "ask"), ()):
        log.info("Auto-approved (%s): %s", risk, title)
        return True
    return approval.ask(title, detail, confirm)

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
    # ---- files: looking (no permission needed)
    {
        "name": "scan_folder",
        "description": "List a folder's files and subfolders with sizes and dates, largest first. Accepts paths "
                       "like 'D:\\Projects', '~/Downloads' or names like 'downloads', 'desktop', 'temp'.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "find_files",
        "description": "Search for files/folders by name ('*.pdf', '*invoice*', or a plain word). Optional: root "
                       "folder (default all drives), min_mb size, days (modified within the last N days).",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"}, "root": {"type": "string"},
                "min_mb": {"type": "number"}, "days": {"type": "number"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file (code, notes, logs, CSV, JSON, ...).",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "disk_usage",
        "description": "Free space on a drive and the biggest folders/files in a folder, to find what uses space.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "e.g. 'C:\\' or a folder"}}},
    },
    {
        "name": "cleanup_suggestions",
        "description": "Sizes of places that are usually safe to clear: Recycle Bin, temp files, browser/app caches, Downloads.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ---- files: changing (no permission needed, nothing is lost)
    {
        "name": "create_folder",
        "description": "Create a folder (and any missing parent folders).",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "write_file",
        "description": "Create a text file, or append to one. Overwriting an existing file asks the user first.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "text": {"type": "string"}, "append": {"type": "boolean"}},
            "required": ["path", "text"],
        },
    },
    {
        "name": "move_path",
        "description": "Move or rename a file/folder. dest may be a folder (keeps the name) or a full new path.",
        "input_schema": {
            "type": "object",
            "properties": {"src": {"type": "string"}, "dest": {"type": "string"}},
            "required": ["src", "dest"],
        },
    },
    {
        "name": "copy_path",
        "description": "Copy a file/folder. dest may be a folder (keeps the name) or a full new path.",
        "input_schema": {
            "type": "object",
            "properties": {"src": {"type": "string"}, "dest": {"type": "string"}},
            "required": ["src", "dest"],
        },
    },
    # ---- destructive: these ask the user for permission themselves; never try to bypass that
    {
        "name": "delete_paths",
        "description": "Delete files/folders. Goes to the Recycle Bin unless permanent=true (only when the user "
                       "says 'permanently' or it's junk like temp/cache files). The user is asked for permission "
                       "with the exact list and sizes; if they decline, stop and tell them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}},
                "permanent": {"type": "boolean"},
            },
            "required": ["paths"],
        },
    },
    {
        "name": "clear_folder",
        "description": "Delete everything inside a folder but keep the folder (e.g. temp, a cache, Downloads). "
                       "permanent=true frees the space now (right for temp/caches); otherwise items go to the "
                       "Recycle Bin. Asks the user for permission first.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "permanent": {"type": "boolean"}},
            "required": ["path"],
        },
    },
    {
        "name": "empty_recycle_bin",
        "description": "Permanently empty the Recycle Bin. Asks the user for permission first.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_powershell",
        "description": "Run a PowerShell command on the PC for anything the other tools can't do (settings, "
                       "installing, shutdown/restart, network info, ...). The user sees the exact command and "
                       "must allow it every time. Prefer the dedicated tools; never use this to delete files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "why": {"type": "string", "description": "One plain sentence for the user: what this does"},
            },
            "required": ["command", "why"],
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
        "name": "call_user",
        "description": "Ring the user's phone with a 'Jarvis is calling…' notification so they can answer and talk "
                       "to you by voice. Use when the user says 'call me' or asks you to call them to tell them "
                       "something. Optional 'message' is the first thing you'll say when they answer.",
        "input_schema": {
            "type": "object",
            "properties": {"message": {"type": "string", "description": "Opening line to speak when they answer"}},
        },
    },
    {
        "name": "camera_photo",
        "description": "Take one photo from the PC's webcam and send it to the user's phone. Use for 'send me a "
                       "photo/picture from the camera', 'take a selfie', 'show me the webcam'. The photo goes straight "
                       "to the phone; you don't see it.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "notify_when",
        "description": "Watch for something to finish/happen on the PC and notify the user when it does, in the "
                       "background. Use for 'tell me when X is done/finished/closes/downloads'. Pick a kind: "
                       "'download' (a download in the Downloads folder finishes), 'process_exits' (an app/process "
                       "named 'name' closes), 'process_starts' (it opens), 'app_idle' (an app named 'name' finishes "
                       "heavy work, e.g. an export/render, then goes quiet), 'file' (a file at 'path' appears). "
                       "Give a short 'label' describing what's being waited for.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["download", "process_exits", "process_starts", "app_idle", "file"]},
                "label": {"type": "string"},
                "name": {"type": "string", "description": "App/process name for process_* and app_idle"},
                "path": {"type": "string", "description": "File path for kind 'file'"},
            },
            "required": ["kind", "label"],
        },
    },
    {
        "name": "cancel_watches",
        "description": "Cancel all the pending 'notify me when…' watches.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "gesture_control",
        "description": "Turn the webcam hand-gesture controller (Gesture-App) on or off on the PC. 'start' opens a "
                       "camera window; 'stop' closes it. Use for 'start/stop gesture control'.",
        "input_schema": {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": ["start", "stop", "toggle"]}},
            "required": ["action"],
        },
    },
    {
        "name": "power_action",
        "description": "Sleep, restart, shut down or sign out of the PC. Asks the user's permission first "
                       "(except 'cancel', which calls off a pending restart/shutdown).",
        "input_schema": {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": ["sleep", "restart", "shutdown", "sign_out", "cancel"]}},
            "required": ["action"],
        },
    },
    {
        "name": "play_music",
        "description": "Play something specific on Spotify by name: a song, artist, album, or a built-in list like "
                       "'liked songs'. Opens Spotify if it's closed. For simple pause/skip/volume use media_key.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "e.g. 'Theo', 'bohemian rhapsody', 'liked songs'"}},
            "required": ["query"],
        },
    },
    {
        "name": "send_message",
        "description": "Send a message in a chat app (Instagram, Discord, WhatsApp, ...). First open the app and the "
                       "right conversation (open_app, ui_list, ui_click), then call this with the message text. It "
                       "types the message, shows the user exactly what will be sent and to whom, and sends ONLY if "
                       "they allow it. Set send_control to the name of the Send button if pressing Enter won't send.",
        "input_schema": {
            "type": "object",
            "properties": {
                "window": {"type": "string", "description": "The chat app window, e.g. 'opera' or '#123456'"},
                "to": {"type": "string", "description": "Who the message is for (person/chat name), for the prompt"},
                "message": {"type": "string"},
                "send_control": {"type": "string", "description": "Name of the Send control; omit to press Enter"},
            },
            "required": ["window", "to", "message"],
        },
    },
    {
        "name": "send_file_to_phone",
        "description": "Send a file from the PC to the user's phone, where they get a download button. For "
                       "'send me the file X' type requests. Files up to ~40 MB.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
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
    {
        "name": "look_at_screen",
        "description": "Take a screenshot of one monitor and save it as an image you can then open with the Read "
                       "tool to SEE the screen. Use it when ui_list can't find what you need (games, web pages, "
                       "custom apps), to check the result of an action, or when the user asks what's on screen. "
                       "Costs tokens, so prefer ui_list/ui_click when they work. Default: the monitor with the "
                       "active window.",
        "input_schema": {
            "type": "object",
            "properties": {"monitor": {"type": "integer", "description": "Windows display number (1/2/3)"}},
        },
    },
    {
        "name": "mouse_click",
        "description": "Click at a position you saw in a look_at_screen picture. x,y are pixel coordinates in that "
                       "picture of that monitor; they're converted to the real screen for you.",
        "input_schema": {
            "type": "object",
            "properties": {
                "monitor": {"type": "integer"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "button": {"type": "string", "enum": ["left", "right", "middle"]},
                "double": {"type": "boolean"},
            },
            "required": ["monitor", "x", "y"],
        },
    },
    {
        "name": "mouse_scroll",
        "description": "Scroll the mouse wheel (positive = up, negative = down, in notches), optionally over a "
                       "point x,y in a look_at_screen picture of 'monitor'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer"},
                "monitor": {"type": "integer"},
                "x": {"type": "number"},
                "y": {"type": "number"},
            },
            "required": ["amount"],
        },
    },
    {
        "name": "mouse_drag",
        "description": "Drag with the left button from (x1,y1) to (x2,y2), coordinates in a look_at_screen picture "
                       "of 'monitor'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "monitor": {"type": "integer"},
                "x1": {"type": "number"}, "y1": {"type": "number"},
                "x2": {"type": "number"}, "y2": {"type": "number"},
            },
            "required": ["monitor", "x1", "y1", "x2", "y2"],
        },
    },
    {
        "name": "remind_me",
        "description": "Send the user a phone notification (or call them, call=true) at a set time. For 'remind me "
                       "at 10:53 to X', 'notify me in 20 minutes', 'call me at 7'. Give 'at' as HH:MM (24h) or "
                       "'YYYY-MM-DD HH:MM', or 'in_minutes'. 'message' is what the notification says.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "at": {"type": "string"},
                "in_minutes": {"type": "number"},
                "call": {"type": "boolean"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "list_watches",
        "description": "List pending reminders and 'notify me when…' watches.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "clipboard",
        "description": "Read the PC's clipboard text (action 'get') or put text on it (action 'set').",
        "input_schema": {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": ["get", "set"]}, "text": {"type": "string"}},
            "required": ["action"],
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
    if name in FILE_TOOLS:
        return FILE_TOOLS[name](args)
    if name == "lock_pc":
        ctypes.windll.user32.LockWorkStation()
        return "Locked the PC."
    if name == "look_at_screen":
        return screens.capture_for_claude(args.get("monitor"))
    if name == "mouse_click":
        x, y = screens.to_screen(args["monitor"], args["x"], args["y"])
        return ui.mouse_click(x, y, args.get("button", "left"), bool(args.get("double")))
    if name == "mouse_scroll":
        if args.get("monitor") and args.get("x") is not None and args.get("y") is not None:
            x, y = screens.to_screen(args["monitor"], args["x"], args["y"])
            return ui.mouse_scroll(int(args["amount"]), x, y)
        return ui.mouse_scroll(int(args["amount"]))
    if name == "mouse_drag":
        x1, y1 = screens.to_screen(args["monitor"], args["x1"], args["y1"])
        x2, y2 = screens.to_screen(args["monitor"], args["x2"], args["y2"])
        return ui.mouse_drag(x1, y1, x2, y2)
    if name == "clipboard":
        if args["action"] == "set":
            pc.set_clipboard(args.get("text", ""))
            return "Copied to the clipboard."
        return pc.get_clipboard() or "The clipboard has no text."
    if name in ("notify_when", "cancel_watches", "call_user", "remind_me", "list_watches", "camera_photo"):
        return approval.run_tool(name, args)   # must run in the main Jarvis process, not this short-lived one
    if name == "gesture_control":
        return {"start": gesture.start, "stop": gesture.stop, "toggle": gesture.toggle}[args["action"]]()
    if name == "power_action":
        return _power_action(args["action"])
    if name == "play_music":
        return spotify.play(args["query"])
    if name == "send_message":
        return _send_message(args)
    if name == "send_file_to_phone":
        return _send_file_to_phone(args["path"])
    if name == "media_key":
        vk = MEDIA_KEYS[args["key"]]
        for _ in range(max(1, min(int(args.get("times", 1)), 50))):
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        return f"Pressed {args['key']}."
    raise ValueError(f"Unknown tool {name}")


# ---------------------------------------------------------------- power, music, messaging, file send

DECLINED = "The user did NOT allow this, so nothing was changed. Tell them so; don't retry it another way."

_POWER = {
    "sleep": ("Put the PC to sleep?", "The PC will sleep. It wakes on a key press or the power button.", "Sleep", power.sleep),
    "restart": ("Restart the PC?", "Windows will restart in a few seconds.", "Restart", power.restart),
    "shutdown": ("Shut down the PC?", "Windows will shut down in a few seconds.", "Shut Down", power.shutdown),
    "sign_out": ("Sign out?", "You'll be signed out and unsaved work may be lost.", "Sign Out", power.sign_out),
}


def _power_action(action: str) -> str:
    if action == "cancel":
        return power.cancel()
    title, detail, confirm, fn = _POWER[action]
    if not _confirm("power", title, detail, confirm):
        return DECLINED
    return fn()


def _send_message(args: dict) -> str:
    window, to, message = args["window"], args["to"], args["message"]
    ui.type_text(message, window)   # type into the chat box the user has open
    time.sleep(0.2)
    if not _confirm("send", f"Send this message to {to}?", f"{message}", "Send"):
        return DECLINED + " The message was typed but not sent; the user can delete it."
    if args.get("send_control"):
        ui.click(window, args["send_control"])
    else:
        ui.press_keys("enter", window)
    return f"Sent to {to}."


def _send_file_to_phone(path: str) -> str:
    import base64
    p = files.resolve(path)
    if not p.is_file():
        raise FileNotFoundError(f"{p} isn't a file.")
    size = p.stat().st_size
    if size > 40 * 1024 * 1024:
        return f"{p.name} is {files.fmt_size(size)}; that's too big to send to the phone (limit 40 MB)."
    data = base64.b64encode(p.read_bytes()).decode()
    if not approval.push({"kind": "file", "id": "file", "name": p.name, "size": size, "data": data}):
        return "I couldn't send it: your phone isn't connected right now."
    return f"Sent {p.name} ({files.fmt_size(size)}) to your phone."


def _delete_paths(args: dict) -> str:
    permanent = bool(args.get("permanent"))
    targets, listing, total = files.describe_targets(args["paths"])
    title = (f"Permanently delete {len(targets)} item(s)?" if permanent
             else f"Move {len(targets)} item(s) to the Recycle Bin?")
    detail = listing + f"\n\nTotal: {files.fmt_size(total)}" + ("\nThis can't be undone." if permanent else "")
    if not _confirm("delete", title, detail, "Delete" if permanent else "Move to Bin"):
        return DECLINED
    return files.delete(targets, permanent)


def _clear_folder(args: dict) -> str:
    permanent = bool(args.get("permanent"))
    folder = files.resolve(args["path"])
    items = files.folder_contents(args["path"])
    if not items:
        return f"{folder} is already empty."
    size = files.tree_size(folder, time.time() + 10)[0]
    title = f"{'Permanently clear' if permanent else 'Clear'} {folder.name or folder}?"
    detail = (f"{folder}\n{len(items)} item(s), {files.fmt_size(size)}\n\n"
              + ("They'll be deleted permanently to free the space." if permanent
                 else "They'll go to the Recycle Bin."))
    if not _confirm("delete", title, detail, "Clear"):
        return DECLINED
    return files.clear(items, permanent)


def _empty_bin(args: dict) -> str:
    summary = files.recycle_bin_summary()
    if summary.startswith("0 item"):
        return "The Recycle Bin is already empty."
    if not _confirm("delete", "Empty the Recycle Bin?", f"{summary} will be deleted permanently.\nThis can't be undone.", "Empty"):
        return DECLINED
    return files.empty_recycle_bin()


def _write_file(args: dict) -> str:
    p = files.resolve(args["path"])
    if p.exists() and not args.get("append"):
        if not _confirm("change", f"Overwrite {p.name}?", f"{p}\nalready exists ({files.fmt_size(p.stat().st_size)}) "
                                                          f"and will be replaced.", "Overwrite"):
            return DECLINED
    return files.write_file(args["path"], args["text"], bool(args.get("append")))


def _run_powershell(args: dict) -> str:
    command = args["command"].strip()
    if not _confirm("cmd", "Run this command on your PC?", f"{args.get('why', '')}\n\n{command}", "Run"):
        return DECLINED
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return "The command was still running after 2 minutes, so I stopped waiting."
    out = (proc.stdout + ("\n" + proc.stderr if proc.stderr.strip() else "")).strip()
    return f"Exit code {proc.returncode}.\n{out[:4000] or '(no output)'}"


FILE_TOOLS = {
    "scan_folder": lambda a: files.scan_folder(a["path"]),
    "find_files": lambda a: files.find_files(a["pattern"], a.get("root", ""), float(a.get("min_mb", 0)), float(a.get("days", 0))),
    "read_file": lambda a: files.read_file(a["path"]),
    "disk_usage": lambda a: files.disk_usage(a.get("path") or "C:\\"),
    "cleanup_suggestions": lambda a: files.cleanup_suggestions(),
    "create_folder": lambda a: files.create_folder(a["path"]),
    "write_file": _write_file,
    "move_path": lambda a: files.move(a["src"], a["dest"]),
    "copy_path": lambda a: files.copy(a["src"], a["dest"]),
    "delete_paths": _delete_paths,
    "clear_folder": _clear_folder,
    "empty_recycle_bin": _empty_bin,
    "run_powershell": _run_powershell,
}


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
