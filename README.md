# Jarvis

Voice and phone control for this Windows PC. Say **"Hey Jarvis, open Spotify, Claude and File Explorer,
put them on my left monitor and open the Jarvis project"**, or type or say it on your phone from anywhere,
and it happens.

```
PC mic ── "Hey Jarvis" wake word ─┐
                                  ├─► Jarvis (this PC) ──► Claude API decides the steps
Phone page (GitHub Pages) ── encrypted via ntfy.sh ─┘            │
                                                                 ▼
                                    open apps / folders / URLs, tile windows on monitors, media keys
```

## Setup (once)

1. **Brain login**: Jarvis runs Claude Code in the background, so it uses your Claude Pro/Max plan (no API key
   needed). Log the CLI in once: open a terminal, run `claude`, type `/login`, finish in the browser, then `/exit`.
   *Alternative:* set `"brain": "api"` in `config.json` and put a paid key from
   https://console.anthropic.com/settings/keys into `.env` as `ANTHROPIC_API_KEY=`.
2. **Dependencies** (already done on this PC):
   ```
   py -3.13 -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
3. **Phone page on GitHub Pages** (`https://izu83.github.io/Jarvis/`)
   - In the GitHub repo go to *Settings → Pages → Build and deployment → Deploy from a branch → `main` / `/docs`*.
   - Run `run.bat --pair` and scan the QR code with the iPhone Camera app (it's also saved as `pair-phone.svg`).
   - In Safari tap *Share → Add to Home Screen*. Jarvis now opens like an app.

GitHub Pages on a *private* repo needs a paid GitHub plan; on a free account the repo has to be public. That's safe:
the page and code contain no secrets. Your key lives only in `.env` (git-ignored) and on your phone.

## Running

```
run.bat            # voice + phone
run.bat --text     # also type commands in the console (handy for testing)
run.bat --no-voice # phone only
```

Say **"Hey Jarvis"**, wait for the beep (or just keep talking), give the command, then pause. A second, lower
beep means it heard you; it replies out loud. Everything is logged to `jarvis.log`.

To start Jarvis automatically at login: press `Win+R`, type `shell:startup`, and put a shortcut to `run.bat` there.

## Things you can say

- "Open Spotify, Claude and a file folder, organize them on my 3rd monitor and open the Jarvis project on the D disk"
- "Put Spotify on the vertical monitor" / "Maximize Claude on the main monitor"
- "Pause the music", "Next song", "Turn the volume up a bit"
- "Close File Explorer", "What's open on monitor 2?"
- "Open YouTube"

## Settings (`config.json`)

| Key | What it does |
|---|---|
| `brain` | `claude-code` (uses your subscription) or `api` (paid key) |
| `claude_code_model` | Model for the Claude Code brain, e.g. `sonnet` or `haiku` for faster replies (empty = default) |
| `model` / `effort` | Model and effort for the `api` brain |
| `monitor_names` | What you call each monitor, by Windows number (1 = right vertical, 2 = main, 3 = left) |
| `known_folders` | Named shortcuts, e.g. `"projects": "D:\\Projects"` |
| `wake_threshold` | Lower (e.g. `0.35`) if "Hey Jarvis" isn't detected; raise if it triggers by itself |
| `whisper_model` | `base.en` (default, fast) or `small.en` (slower, try if words get misheard) |
| `speak_replies`, `tts_voice` | Spoken answers on the PC, and which Windows voice to use |
| `voice_enabled`, `phone_enabled` | Turn either input off |

## Code map

| File | Role |
|---|---|
| `jarvis/__main__.py` | Starts the voice, phone and worker threads |
| `jarvis/brain.py` | System prompt + the two brains (Claude Code CLI, or the API tool loop) |
| `jarvis/mcp_server.py` | Gives Claude Code the Jarvis tools |
| `jarvis/tools.py` | The actions Claude can take (schemas + implementations) |
| `jarvis/windows.py` | Win32: monitors, window lookup, moving and tiling |
| `jarvis/apps.py` | Start-menu app launching, folders, folder search |
| `jarvis/voice.py` | openWakeWord "hey jarvis" + recording + faster-whisper |
| `jarvis/remote.py` | Encrypted phone channel over ntfy.sh |
| `docs/` | The phone page served by GitHub Pages |

## Security notes

- Phone commands are AES-256-GCM encrypted end to end; ntfy.sh only relays ciphertext. Messages older than
  2 minutes, replays, and anything not encrypted with your key are ignored.
- Jarvis can only do what's in `tools.py` (open/arrange/close windows, media keys). It deliberately has no
  "run any command" tool, so even a leaked key can't do much more than open apps.
- If your phone is lost, run `run.bat --pair` after deleting `JARVIS_TOPIC`/`JARVIS_KEY` from `.env` to rotate keys.
- Windows of apps running as administrator can't be moved unless Jarvis also runs as administrator.
