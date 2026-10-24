# Jarvis

Voice and phone control for this Windows PC. Say **"Hey Jarvis, open Spotify, Claude and File Explorer,
put them on my left monitor and open the Jarvis project"**, or type or say it on your phone from anywhere,
and it happens.

```
PC mic ── "Hey Jarvis" wake word ─┐
                                  ├─► Jarvis (this PC) ──► Claude API decides the steps
Phone page (GitHub Pages) ── encrypted WebSocket via Cloudflare tunnel ─┘            │
                                                                 ▼
                                    open apps / folders / URLs, tile windows on monitors, media keys
```

## Setup (once)

1. **Brain login**: Jarvis runs Claude Code in the background, so it uses your Claude Pro/Max plan (no API key
   needed). Log the CLI in once: open a terminal, run `claude`, type `/login`, finish in the browser, then `/exit`.
   *Alternatives* (set `"brain"` in `config.json`):
   - `"hermes"`: a free local model through [Ollama](https://ollama.com) (`ollama pull hermes3:8b`). It does most
     commands on your PC with zero tokens and hands anything that needs vision, the web or an exact file search to
     Claude (`hermes_fallback`).
   - `"api"`: a paid key from https://console.anthropic.com/settings/keys in `.env` as `ANTHROPIC_API_KEY=`.
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
run.bat                # voice + phone, with a tray icon
run.bat --text         # also type commands in the console (handy for testing)
run.bat --no-voice     # phone only
run.bat --startup      # start Jarvis hidden at every login (--startup-off to stop)
```

Or double-click `Jarvis.vbs` to run it in the background with no console window, just a tray icon under the ^ arrow
in the taskbar (right-click it to pick a microphone, open the log or quit). Only one Jarvis runs at a time.

Say **"Hey Jarvis"** or press **Ctrl+Alt+J**, give the command, then pause. A pop-up in the top-right of the main
monitor shows what it heard live, then each step while it works, then the reply, which it also speaks in a natural
voice (Microsoft's free neural voices via edge-tts; the built-in Windows voice when offline). Everything is logged to
`jarvis.log`.

## How a command is handled

1. **Modes** ("work mode") run instantly with no AI.
2. **Quick commands** (media keys, volume, "open X and Y on monitor 3", reminders, lock, power, screenshots/webcam to
   the phone) are understood by a free local parser (`nlu.py`). Anything with more than one part ("X and then Y") or
   that it isn't sure about goes to the brain, so nothing is half-done.
3. **The brain** (Claude Code, the API, or Hermes) does everything else with the tools in `tools.py`.

Commands run one at a time. If you send another while Jarvis is busy, it shows that it's waiting for the first one.

## Things you can say

- "Open Spotify, Claude and a file folder, organize them on my 3rd monitor and open the Jarvis project on the D disk"
- "Put Spotify on the vertical monitor" / "Maximize Claude on the main monitor"
- "Pause the music", "Next song", "Turn the volume up a bit"
- "Close File Explorer", "What's open on monitor 2?"
- "Open YouTube"

## Modes

A mode is a saved layout you start with one phrase: **"Jarvis, work mode"**, or the mode's button on the phone.
Modes run instantly and don't use Claude at all. The default `work` mode:

| Monitor | Windows |
|---|---|
| Right vertical (1) | File Explorer (top), Claude (middle), Spotify (bottom) |
| Left (3) | Opera (left half), File Explorer (right half) |
| Main (2) | VS Code, full screen |

Create or change modes by just asking, e.g. *"Jarvis, make a gaming mode with Discord on the vertical monitor and
Steam full screen on the main one"* or *"In work mode, put Chrome instead of Opera"*. They're saved in `modes.json`,
which you can also edit by hand.

## The phone app

- **Chats and groups**: the sidebar button (top left) lists your chats. Each chat has its own memory, so Jarvis
  doesn't mix up conversations. Tap the folder button to make a group; press and hold a chat or group to rename,
  move or delete it. Search finds chats by title or message text.
- **Control Center**: shown on every new chat, or tap the title at the top. Spotify now playing with play/pause,
  skip and volume; live CPU and memory with the busiest app; send your phone's clipboard to the PC or see the PC's
  clipboard; lock the PC. These buttons go straight to the PC without Claude, so they're instant and free.
- **Unread badge**: replies that arrive in another chat get a dot in the sidebar (and on the app icon, where iOS allows it).
- **Step pills**: everything Jarvis does for a command appears as a small pill in the chat ("Files · Searching for
  invoice…"), live while it works and saved afterwards. A **Working** chip at the top shows the current step even for
  commands you gave by voice on the PC.
- **Reminders**: a clock chip at the top shows the next reminder. Tap it for all reminders and watches; cancel any one.
- **Activity**: Control Center → Recent activity → See all lists every command (phone or PC mic), its steps and
  the answer. Kept on the PC in `.brain/activity.json`.
- **Never lost**: if the phone's connection silently dropped, the app reconnects and re-sends (the PC never runs it
  twice). If the app was closed while Jarvis worked, the answer is fetched when you open it again.

## Smarter: acting inside apps

Jarvis can read and click the buttons, lists and tabs *inside* apps through Windows UI Automation (the
accessibility tree screen readers use). So "open Claude's Jarvis project", "go to the settings tab" or "open my chat
with X" work: it opens the app, finds the control by name and clicks it. It's text-only (no screenshots), so it stays
cheap. It won't click anything that sends, deletes, buys or changes settings unless you explicitly ask.

## System page

Tap **System** in the dock or Control Center for live stats every few seconds: CPU (with a graph and per-core bars),
GPU load/VRAM/temperature/fan (NVIDIA), memory, network speed, free space on every drive (red when nearly full),
and the busiest apps. You can also ask "how much space is left on my disks?".

## Files and permissions

Jarvis can look through your files (list folders with sizes, search by name, read text files, show what's using
disk space, suggest what's safe to clean) and organise them (create, write, move, rename, copy). For anything else
it can run a PowerShell command.

How much Jarvis asks first is the `autonomy` setting:

| `autonomy` | Asks before |
|---|---|
| `full` (default) | only power actions (sleep/restart/shutdown/sign out) and sending messages |
| `risky_only` | also deleting, clearing and emptying the Recycle Bin |
| `ask` | also running commands and overwriting files |

When it asks: from the phone, an Allow / Don't Allow alert appears in the app (keep it open); otherwise a Windows
dialog pops up and Jarvis says so out loud. No answer within 2 minutes counts as no. Normal deletes go to the
Recycle Bin; "permanently" (or clearing temp/cache folders) frees the space for real. Windows, Program Files,
ProgramData, drive roots and your main user folders themselves can't be deleted at all.

## Now playing

The Control Center shows what's playing in any app (Spotify, YouTube in your browser, ...) with its album cover,
a live progress bar and controls, using Windows' own media information.

## Screens on your phone

Tap **Screens** to get one picture of all three monitors, laid out like your desk. Tap a monitor (or use the
All / Left / Main / Right switch) to zoom in; pinch, drag and double-tap work too. Screenshots go straight from the PC to your phone,
end-to-end encrypted. Claude never sees them, so they cost no tokens. They expire from the relay after 3 hours.

## Settings (`config.json`)

| Key | What it does |
|---|---|
| `brain` | `claude-code` (uses your subscription) or `api` (paid key) |
| `claude_code_model` | Model for the Claude Code brain, e.g. `sonnet` or `haiku` for faster replies (empty = default) |
| `model` / `effort` | Model and effort for the `api` brain |
| `monitor_names` | What you call each monitor, by Windows number (1 = right vertical, 2 = main, 3 = left) |
| `known_folders` | Named shortcuts, e.g. `"projects": "D:\\Projects"` |
| `ollama_host`, `hermes_model`, `hermes_fallback` | The local Hermes brain and which brain backs it up |
| `use_claude` | `false` = only free local commands, never Claude (also a switch in the phone's Settings) |
| `autonomy` | `full`, `risky_only` or `ask` (see *Files and permissions*) |
| `wake_threshold` | Lower (e.g. `0.35`) if "Hey Jarvis" isn't detected; raise if it triggers by itself |
| `whisper_model` | `small.en` (default), `base.en` (faster, mishears more) or `medium.en` (slowest, best) |
| `silence_seconds`, `max_command_seconds`, `no_speech_seconds` | How long it listens before ending a command |
| `input_device` | Which microphone (also in the tray menu); `null` = Windows default |
| `hotkey`, `hotkey_enabled`, `speak_on_shortcut` | The talk shortcut (default Ctrl+Alt+J) and whether replies to it are spoken |
| `overlay_enabled`, `tray_enabled` | The "Hey Jarvis" pop-up and the tray icon |
| `speak_replies`, `tts_engine`, `edge_voice`, `tts_voice` | Spoken answers: `edge` (natural, online) or `windows` (offline) and the voice |
| `voice_enabled`, `phone_enabled` | Turn either input off |
| `upload_folder` | Where files from the phone are saved (default `From Phone` on the Jarvis drive) |
| `warn_disk_gb`, `warn_gpu_temp` | When the phone gets a low-disk / hot-GPU warning |
| `gesture_app`, `gesture_python` | The Gesture-App folder and the Python that runs it (also used for webcam photos) |

## Code map

| File | Role |
|---|---|
| `jarvis/__main__.py` | Starts the voice, phone and worker threads |
| `jarvis/brain.py` | System prompt + the Claude brains (Claude Code CLI, or the API tool loop) |
| `jarvis/hermes.py` | The free local Hermes brain (Ollama) with Claude as the fallback |
| `jarvis/nlu.py` | Free local parser for quick commands |
| `jarvis/activity.py` | The saved log of every command and its steps (the phone's pills and Activity list) |
| `jarvis/watchers.py` | Reminders and "tell me when…" watches |
| `jarvis/mcp_server.py` | Gives Claude Code the Jarvis tools |
| `jarvis/tools.py` | The actions Claude can take (schemas + implementations) |
| `jarvis/windows.py` | Win32: monitors, window lookup, moving and tiling |
| `jarvis/apps.py` | Start-menu app launching, folders, folder search |
| `jarvis/voice.py` | openWakeWord "hey jarvis" + recording + faster-whisper |
| `jarvis/remote.py` | Encrypted WebSocket phone channel (commands, live status, screenshots, covers) |
| `jarvis/modes.py` | Saved layouts ("work mode") that run without Claude |
| `jarvis/screens.py` | Screenshots of each monitor for the phone |
| `jarvis/ui.py` | Acting inside apps: list/click controls, type, press keys (UI Automation) |
| `jarvis/sysinfo.py` | Detailed stats for the System page (CPU, GPU, RAM, disks, network, top apps) |
| `jarvis/tunnel.py` | Cloudflare quick tunnel that gives the phone a public, encrypted way in |
| `jarvis/media.py` | What's playing in any app, with album covers (Windows media session) |
| `jarvis/files.py` | Scanning, searching, reading, organising and deleting files (with safety checks) |
| `jarvis/approval.py` | Asking you before anything destructive (phone alert or Windows dialog) |
| `jarvis/pc.py` | Live PC status (now playing, CPU/RAM, busiest app), clipboard, lock |
| `jarvis/push.py` | Web Push notifications to the phone when the app is closed |
| `jarvis/voice.py`, `speak.py` | Wake word, recording and transcription; spoken replies |
| `jarvis/overlay.py`, `tray.py`, `hotkey.py` | The pop-up, the tray icon and the talk shortcut |
| `jarvis/power.py`, `spotify.py`, `camera.py`, `gesture.py` | Power actions, Spotify search, webcam photos, Gesture-App |
| `jarvis/autostart.py` | `--startup` / `--startup-off` |
| `tests/` | `pytest` tests for the parts that don't need Windows (parser, reminders, activity, modes) |
| `docs/` | The phone page served by GitHub Pages |

## How the phone connects

Jarvis runs a small WebSocket server on your PC and makes it reachable with a free Cloudflare quick tunnel
(`bin/cloudflared.exe`, no account needed). The tunnel's address changes each time Jarvis starts, so Jarvis announces
it (encrypted) on an ntfy.sh topic; the phone reads the newest announcement and connects directly. That's only a few
relay messages a day, well under ntfy's free limit of 250. If the phone can't find the PC, run `run.bat --pair`
while Jarvis is running and scan the new QR code: it contains the current address.

If `bin/cloudflared.exe` is missing, download it from
https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe into `bin/`.

## Security notes

- Everything between phone and PC is AES-256-GCM encrypted end to end; Cloudflare and ntfy.sh only see
  ciphertext. Messages older than 2 minutes, replays, and anything not encrypted with your key are ignored.
- Jarvis is built to do anything on this PC: it can run PowerShell, change and delete files, see the screen and
  send you files. With the default `autonomy: full` it does most of that without asking, so your pairing key is
  effectively the key to the PC. Keep the pairing link private.
- If your phone is lost, run `run.bat --pair` after deleting `JARVIS_TOPIC`/`JARVIS_KEY` from `.env` to rotate keys.
- Windows of apps running as administrator can't be moved unless Jarvis also runs as administrator.

## Tests

```
.venv\Scripts\pip install pytest
.venv\Scripts\python -m pytest tests
```
