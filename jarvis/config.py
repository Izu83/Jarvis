"""Settings: secrets come from .env, preferences from config.json (created with defaults on first run)."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
CONFIG_PATH = ROOT / "config.json"

DEFAULTS = {
    # Brain: "claude-code" uses your Claude Pro/Max subscription through the Claude Code CLI;
    # "api" uses a paid API key (ANTHROPIC_API_KEY) and is a bit faster;
    # "hermes" runs a free, local Hermes model (via Ollama) that tries every command on your own PC
    # with zero tokens, and quietly falls back to Claude for anything it can't do.
    "brain": "claude-code",
    # Local Hermes brain (only used when brain is "hermes")
    "ollama_host": "http://127.0.0.1:11434",
    "hermes_model": "hermes3:8b",     # the Ollama model to run; pull it with: ollama pull hermes3:8b
    "hermes_fallback": "claude-code",  # which brain handles what Hermes can't ("claude-code" or "api")
    # Qwen3 works with Hermes: Hermes does quick one-step commands, Qwen3 thinks through multi-step ones and takes
    # over when Hermes gets stuck. "" = Hermes alone. Bigger/smaller: "qwen3:14b" (needs ~12 GB VRAM), "qwen3:4b".
    "qwen_model": "qwen3:8b",
    "local_auto_pull": True,   # download missing local models through Ollama automatically (a few GB, once)
    "local_ctx": 16384,        # context window for the local models (Jarvis's tool list alone is ~6.5k tokens)
    # false = Claude does every command and the local models only step in when Claude can't answer (usage limit,
    # offline); true = the local models try first (free, but much slower and they get real tasks wrong).
    "local_first": False,
    # Personality and memory (also set on the phone: Settings > Memory). humour: "off", "light" or "lots".
    # memory_span: how far back Jarvis rereads the conversation: "short" (2 h), "normal" (12 h), "long" (3 days), "max".
    "humour": "light",
    "memory_span": "normal",
    # Messages Jarvis sends on his own (also on the phone: Settings > Memory). proactive: "off" (only what you ask
    # for, like reminders and scheduled tasks), "normal" (+ a heads-up about new important email) or "chatty"
    # (+ a heads-up about every new email). During quiet_hours [from, to] they arrive without a notification.
    "proactive": "normal",
    # Check-ins (checkin.py): now and then he asks what you're up to. "off", "sometimes" or "often";
    # check_in_aloud also says them out loud when you're at the PC.
    "check_ins": "sometimes",
    "check_in_aloud": False,
    "morning_time": "08:30",
    # The morning briefing at morning_time: weather, calendar, reminders, email, PC (no tokens). briefing_speak also
    # reads it out on the PC. weather_city "" = work it out from your internet connection.
    "briefing": True,
    "briefing_speak": False,
    # The evening recap at recap_time: what Jarvis did, your time in apps, tomorrow's calendar and weather (no tokens)
    "recap": True,
    "recap_time": "21:30",
    "weather_city": "",
    "quiet_hours": [23, 8],
    # Which brains Jarvis may use: the model picker's three ticks (phone and desktop app). Any mix, at least one.
    # use_claude off = no Claude at all (zero tokens). use_hermes / use_qwen only count with "brain": "hermes".
    # With nothing on, only the free built-in command parser answers.
    "use_claude": True,
    "use_hermes": True,
    "use_qwen": True,
    # Gemini, OpenRouter's free models, ChatGPT and Grok through their APIs (cloud.py), each once you add its key
    # (run.bat --keys). Gemini is free
    # with a Google AI Studio key (the newest Flash model is picked when you add it). ChatGPT and Grok are paid per use:
    # off until ticked, and together they stay under paid_budget (USD a month). Prices are USD per million tokens
    # [input, output], for the estimate.
    # Website jobs in Jarvis's own hidden Chrome (hidden_browser.py), not on your screen. false = your browser + mouse.
    "hidden_browser": True,
    "use_gemini": True,
    "gemini_model": "",   # "" = the newest Flash the key can use, then the next one down; a name = try that one first
    "gemini_price": [0, 0],
    # OpenRouter's free models (a free key from openrouter.ai, run.bat --keys): DeepSeek whenever one of its models is
    # free there, otherwise the strongest free ones. "" = picked for you; a name = try that one first.
    # Your Shkolo e-diary (shkolo.py): grades, homework, timetable, absences, read in the hidden browser once you've
    # logged in there ("log in to Shkolo"). shkolo_alerts: a message when there's a new grade, homework or absence.
    "shkolo": True,
    "shkolo_alerts": True,
    "use_openrouter": True,
    "openrouter_model": "",
    "openrouter_price": [0, 0],
    "use_chatgpt": False,
    "use_grok": False,
    "chatgpt_model": "gpt-5-mini",
    "grok_model": "grok-4-fast",
    "chatgpt_price": [0.25, 2.0],
    "grok_price": [0.2, 0.5],
    "paid_budget": 5.0,
    "claude_code_model": "sonnet",  # "sonnet", "haiku" (fastest) or "opus"; empty = your Claude Code default
    "claude_code_effort": "medium",  # medium = smarter multi-step; "low" = fastest, "high" = smartest (more tokens)
    # Only for "brain": "api"
    "model": "claude-opus-5-5",
    "effort": "low",  # low keeps voice commands snappy; raise to "medium" if it gets things wrong
    # What the user calls each monitor. Numbers are Windows' DISPLAY numbers.
    "monitor_names": {
        "1": "right vertical monitor",
        "2": "main / middle monitor",
        "3": "left monitor",
    },
    # Shortcuts the brain can use when you say a place by name
    "known_folders": {
        "projects": "D:\\Projects",
    },
    # Voice
    "voice_enabled": True,
    "overlay_enabled": True,   # the "Hey Jarvis" pop-up in the top-right corner of the screen
    "hotkey_enabled": True,    # a keyboard shortcut that starts Jarvis listening without the wake word
    "hotkey": "Ctrl+Alt+J",    # the shortcut to press (modifiers + one key, e.g. "Ctrl+Shift+Space")
    "tray_enabled": True,      # show a Jarvis icon in the taskbar tray (so it can run with no console window)
    # How much Jarvis confirms before acting: "ask" (confirm everything risky), "risky_only" (run commands & change
    # things freely, still confirm deletes/power/messages), "full" (do almost everything without asking; system/Windows
    # folders are still never deletable, and shutdowns and sending messages still confirm).
    "autonomy": "full",
    # false = never ask: everything risky just happens (Settings › Security › Ask before risky actions).
    # Sending messages and emails never asks either way.
    "ask_permission": True,
    "wake_word_model": "hey_jarvis",
    # "Hey Jarvis" is found in two steps: a small, fast model picks out anything that sounds a bit like it
    # (wake_check_threshold, low on purpose), then Whisper listens to those last two seconds and only wakes him if
    # his name is really in them, so videos, songs and people talking don't. With wake_check off, the fast model
    # alone decides, at wake_threshold.
    "wake_check": True,
    "wake_check_threshold": 0.15,
    "wake_threshold": 0.5,
    # Also catch a plain "Jarvis, ..." / "Джарвис, ..." at the start of anything you say (the fast model only really
    # knows "Hey Jarvis", and not in Bulgarian). "auto": only when Whisper runs on the graphics card.
    "wake_name_alone": "auto",
    "whisper_model": "small.en",  # good accuracy on CPU; "base.en" is faster but mishears more, "medium.en" is slower but best
    # Which language you talk to Jarvis in: "en", "bg" (Bulgarian) or "both". Bulgarian uses the multilingual
    # whisper_model_multi (a ~480 MB download the first time, kept in .brain/whisper).
    "voice_language": "en",
    "whisper_model_multi": "large-v3-turbo",
    "whisper_device": "auto",   # "auto" = the graphics card when it can be used, "cpu" = never
    "speak_replies": True,
    "speak_on_shortcut": True,  # speak the reply aloud when you start a command with the Ctrl+Alt+J shortcut
    # Voice: "edge" = Microsoft's free natural neural voices (online, no key); "windows" = the built-in offline voice.
    "tts_engine": "edge",
    "edge_voice": "en-GB-RyanNeural",  # Jarvis's voice: a British man, like the films (the phone offers speak.VOICES)
    "edge_voice_bg": "bg-BG-KalinaNeural",  # used for replies written in Bulgarian (bg-BG-BorislavNeural = male)
    "tts_voice": "",  # the built-in Windows voice name (offline fallback), e.g. "Zira"; empty = default
    "input_device": None,       # which microphone to listen on (device index); None = Windows default
    # How long Jarvis listens. Bigger = waits longer for you, less chance of cutting you off.
    "silence_seconds": 5.0,       # quiet time that ends a command (bigger = more room to pause mid-sentence)
    "max_command_seconds": 40.0,  # hard cap on one command
    "no_speech_seconds": 8.0,     # give up if no speech after the wake word
    # Phone
    "phone_enabled": True,
    "ntfy_server": "https://ntfy.sh",  # only used to tell the phone the tunnel address
    "phone_port": 8787,  # local port the tunnel forwards to
    # Gesture-App (webcam hand control); gesture_python auto-detects if empty
    "gesture_app": "D:\\Projects\\Gesture-App",
    "gesture_python": "",
    "phone_page_url": "",  # e.g. https://<you>.github.io/jarvis/
    # What the phone calls this computer when you have Jarvis on more than one (your PC and your laptop). Empty:
    # "Laptop" when it has a battery, otherwise "PC". You can rename it on the phone too.
    "device_name": "",
    # Email: your address here + an app password in .env (EMAIL_APP_PASSWORD). Servers are worked out from the
    # address (Gmail, Outlook, Yahoo, iCloud); set the hosts only for another provider.
    "email_address": "",
    "email_imap_host": "",
    "email_smtp_host": "",
    "email_vip": [],   # senders (names, addresses or domains) that are always important
}


def _load_json() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
        return dict(DEFAULTS)
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {**DEFAULTS, **data}


load_dotenv(ENV_PATH)
settings = _load_json()


def set_setting(key: str, value) -> None:
    """Change a preference and save it to config.json so it sticks across restarts."""
    settings[key] = value
    CONFIG_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def secret(name: str) -> str:
    return os.environ.get(name, "")


def set_secret(name: str, value: str) -> None:
    """Add or replace a KEY=value line in .env."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    lines = [l for l in lines if not l.startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[name] = value
