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

## Smarter: acting inside apps

Jarvis can read and click the buttons, lists and tabs *inside* apps through Windows UI Automation (the
accessibility tree screen readers use). So "open Claude's Jarvis project", "go to the settings tab" or "open my chat
with X" work: it opens the app, finds the control by name and clicks it. It's text-only (no screenshots), so it stays
cheap. It won't click anything that sends, deletes, buys or changes settings unless you explicitly ask.

## System page

Tap **System** in the dock or Control Center for live stats every few seconds: CPU (with a graph and per-core bars),
GPU load/VRAM/temperature/fan (NVIDIA), memory, network speed, free space on every drive (red when nearly full),
and the busiest apps. You can also ask "how much space is left on my disks?".

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
| `jarvis/remote.py` | Encrypted WebSocket phone channel (commands, live status, screenshots, covers) |
| `jarvis/modes.py` | Saved layouts ("work mode") that run without Claude |
| `jarvis/screens.py` | Screenshots of each monitor for the phone |
| `jarvis/ui.py` | Acting inside apps: list/click controls, type, press keys (UI Automation) |
| `jarvis/sysinfo.py` | Detailed stats for the System page (CPU, GPU, RAM, disks, network, top apps) |
| `jarvis/tunnel.py` | Cloudflare quick tunnel that gives the phone a public, encrypted way in |
| `jarvis/media.py` | What's playing in any app, with album covers (Windows media session) |
| `jarvis/pc.py` | Live PC status (now playing, CPU/RAM, busiest app), clipboard, lock |
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
- Jarvis can only do what's in `tools.py` (open/arrange/close windows, media keys). It deliberately has no
  "run any command" tool, so even a leaked key can't do much more than open apps.
- If your phone is lost, run `run.bat --pair` after deleting `JARVIS_TOPIC`/`JARVIS_KEY` from `.env` to rotate keys.
- Windows of apps running as administrator can't be moved unless Jarvis also runs as administrator.
