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
   - `"hermes"`: free local models through [Ollama](https://ollama.com), working as a team: **Hermes**
     (`hermes3:8b`) does quick one-step commands, **Qwen3** (`qwen3:8b`) thinks through multi-step requests and
     questions and takes over when Hermes gets stuck, and Claude (`hermes_fallback`) only gets what neither can do
     (screen vision, the web, exact file searches). Install Ollama once; Jarvis starts it and downloads both models
     by itself the first time (about 10 GB; or run `ollama pull hermes3:8b` and `ollama pull qwen3:8b`). The phone's
     step pills show which one is working ("Brain · Qwen3 is thinking it through…"). On an 8 GB GPU set the user
     environment variables `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` (then restart Ollama) so Qwen3
     with the 16k context fits fully on the GPU; without them part of it runs on the CPU and is too slow.
     By default (`"local_first": false`) Claude does every command and the local team only steps in when Claude
     can't answer (usage limit, offline, crash): in real use the 8B models were too slow and too unreliable to go
     first. Settings › Local brain › **Try local models first** (or `"local_first": true`) switches to the order above.
   - `"api"`: a paid key from https://console.anthropic.com/settings/keys in `.env` as `ANTHROPIC_API_KEY=`.
2. **Dependencies** (already done on this PC):
   ```
   py -3.13 -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
3. **Phone page on GitHub Pages** (`https://izu83.github.io/Jarvis/`)
   - In the GitHub repo go to *Settings → Pages → Build and deployment → Deploy from a branch → `main` / `/docs`*.
   - Right-click the tray icon › **Pair your phone (QR code)…** and scan the code with the iPhone Camera app, or
     copy the link from that window. (`run.bat --pair` prints the same in a console and saves `pair-phone.svg`.)
   - In Safari tap *Share → Add to Home Screen*. Jarvis now opens like an app.

GitHub Pages on a *private* repo needs a paid GitHub plan; on a free account the repo has to be public. That's safe:
the page and code contain no secrets. Your key lives only in `.env` (git-ignored) and on your phone.

## Running

```
run.bat                # start Jarvis hidden: no window, just the tray icon
run.bat --console      # run in this window instead, with the log printed live
run.bat --text         # also type commands in the console (handy for testing)
run.bat --no-voice     # phone only
run.bat --startup      # start Jarvis hidden at every login (--startup-off to stop)
```

Jarvis lives in the taskbar tray (under the ^ arrow): right-click its icon to stop what Jarvis is doing, pick a microphone, pair your phone (a
window with the QR code and the pairing link), restart Jarvis, open the log or quit. `run.bat` and `Jarvis.vbs` both start it that way, with no console window. Only one Jarvis runs at
a time.

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
Saying or typing **"stop"** ("Jarvis, stop", "cancel", "never mind") while he's busy stops the running command
instead of waiting its turn. When he's idle, "stop" still pauses the music.

With **local models first** on, a model that errors twice in a row (Ollama's "500", often when the graphics card is
busy) rests for 15 minutes and Claude covers for it; Settings › Local brain shows it. Ollama's own output, with the
reason for those errors, goes to `.brain/ollama.log` when Jarvis starts it.

## Things you can say

- "Open Spotify, Claude and a file folder, organize them on my 3rd monitor and open the Jarvis project on the D disk"
- "Put Spotify on the vertical monitor" / "Maximize Claude on the main monitor"
- "Pause the music", "Next song", "Turn the volume up a bit"
- "Close File Explorer", "What's open on monitor 2?"
- "Open YouTube"

## Modes

A mode is a saved layout you start with one phrase: **"Jarvis, work mode"**, or type **/work** (`/` + the mode's name) on the phone.
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
- **Control Center**: shown on every new chat, or tap **Your PC** in the tab bar. Spotify now playing with play/pause,
  skip and volume; live CPU and memory with the busiest app; send your phone's clipboard to the PC or see the PC's
  clipboard; lock the PC. These buttons go straight to the PC without Claude, so they're instant and free.
- **Slash commands**: type `/` in the message box for the list: `/work` (and your other modes), `/monitors`,
  `/camera`, `/system`, `/control`, `/call` and `/newmode`. Tap one, or type the whole name and press send.
- **Stop button**: while Jarvis works on something in the chat you're in, the send button becomes a ■ stop
  button. He stops within a second or so (a command still waiting its turn is just taken off the list). `/stop` does
  the same for whatever he's doing, even from another chat.
- **Send again / edit**: press and hold one of your messages to send it again, edit it first, or copy it.
- **Answers as they're written**: the reply bubble fills in word by word (Claude and the local models both
  stream); commentary before an action is taken back off when the action starts.
- **Unread badge**: replies that arrive in another chat get a dot in the sidebar (and on the app icon, where iOS allows it).
- **Everything at the bottom**: like most iPhone apps there's no bar at the top. The tab bar at the bottom has
  Chats, Your PC (the Control Center, with a green/red dot for the connection), New, Activity and You (settings).
- **Step pills**: everything Jarvis does for a command appears as a small pill in the chat ("Files · Searching for
  invoice…"), live while it works and saved afterwards. A **Working** chip above the message box shows the current step even for
  commands you gave by voice on the PC.
- **Reminders**: a clock chip above the message box shows the next reminder. Tap it for all reminders and watches; cancel any one.
- **Activity**: the Activity tab (or Control Center → Recent activity) lists every command (phone or PC mic), its steps and
  the answer. Kept on the PC in `.brain/activity.json`.
- **Never lost**: if the phone's connection silently dropped, the app reconnects and re-sends (the PC never runs it
  twice). If the app was closed while Jarvis worked, the answer is fetched when you open it again. Files Jarvis sends (pictures, videos) wait on
  the PC until the phone confirms it has them (`.brain/outbox.json`, 3 days), and stay saved in the chat on the phone.

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

## Siri and the Share button

Make an **Ask Jarvis** shortcut once in the iPhone's Shortcuts app (the app's Settings › Siri & Share shows each
step, with the address link and key to copy). Then say *"Hey Siri, Ask Jarvis"*, or pick Ask Jarvis under any app's
Share button to send it a link or text. Siri reads the answer out.

A Shortcut can't do the app's encryption, so it uses its own key (`JARVIS_SIRI_KEY` in `.env`) over the same HTTPS
tunnel: it looks up the PC's current address on the relay (`<topic>-s`), then asks `<address>/ask` with the key and
the question in headers. Answers that take longer than 45 seconds come to the app as a message instead.

## Live screen

`/live` streams one monitor at up to 20 pictures a second (about 16 measured through the Cloudflare tunnel; it
needs ~5 Mbit/s, so it's smoother on Wi-Fi than on weak mobile data). Tap to click, press and hold to right-click,
pinch to zoom; the bar at the bottom types text, presses Enter / Backspace / Esc and scrolls. Switch monitors at the
top; ✕ (or Esc) closes it and stops the pictures.

How it's fast (`jarvis/live.py`): mss grabs a monitor in ~25 ms (PIL's ImageGrab took ~125 ms), moving pictures are
halved with `Image.reduce` and sent as binary WebSocket messages (no base64), the phone confirms each one and at
most 6 are on the way at once (a slow link gets fewer, smaller pictures rather than a growing delay), an unchanged
screen isn't resent, and once it stops changing one full-size picture follows for sharp zooming.

## Away mode

`/away` (or "I'm leaving", "away mode on"): while it's on, a real hand on the mouse or keyboard gets you a message
and a webcam photo (kept in `.brain/away/`). Jarvis's own clicks and the live screen don't count: Windows marks
them as generated input. `/away` again, or "I'm back", turns it off; `/lock` locks the PC. While it's on, a chip
above the phone's message box says so; tap it to turn away mode off.

## Nap: wake your PC from the phone

A phone web page can't send the signal that wakes a sleeping PC, so there's **Nap** instead: the Sleep tile in
Control Center asks Nap or Sleep, and `/nap` (or "take a nap") locks the PC, turns the screens off and keeps it
awake, so Jarvis stays reachable. `/wake`, the "Napping" chip above the message box, or "wake up" turns the screens
back on (the PC stays locked). Using the mouse or keyboard at the PC ends a nap too. While napping the PC draws its
normal idle power; real Sleep still switches it off, but then it can't be woken from the phone.

## Find my file

"Send me the PDF I downloaded yesterday", "send me the latest screenshot", "find the photo from last week", "what
did I download today?": Jarvis looks through Downloads, Desktop, Documents, Pictures, Videos and Music (and
OneDrive's copies) by kind, time and words in the name, in a fraction of a second and with no tokens. One match is
sent straight to the phone; several are listed, and "the second one" or "number 3" sends that one. `/find` starts
the sentence for you. Anything it can't place (no kind of file or "download" in it, or a name it can't find in
those folders) goes to the brain, which can search every drive.

## Morning briefing

Every morning at `morning_time` (Settings › Morning briefing): the weather, today's calendar, reminders and
scheduled tasks, unread email and how the PC is doing, in one message, with no tokens. `/briefing`, "brief me" or
"what's my day look like" gets it any time. Weather comes from Open-Meteo for `weather_city`, or the place your
internet connection is in when that's empty. The calendar is optional: paste your Google Calendar's *Secret address
in iCal format* in Settings (kept in `.env` as `CALENDAR_ICS_URL`); repeating events count. "Say it out loud on the
PC" (`briefing_speak`) also reads it out on the PC. With humour on, it ends with a line for the day.

## Cleaning up a disk

`/cleandisk` asks which disk, scans it (a few seconds) and sorts what it finds:

- **Safe to clean** (ticked): temp files older than a day, browser caches (Chrome, Edge, Brave, Opera, Firefox/Zen),
  Discord / Spotify / VS Code caches, graphics shader caches, crash reports, pip / npm / yarn / NuGet download caches.
  Programs rebuild these by themselves.
- **Your call** (not ticked): the Recycle Bin, downloaded AI models (Hugging Face, PyTorch, Kaggle).
- **Leave alone** (never cleaned, shown so you know where the space is): the hibernation and page files, Downloads,
  Documents and other folders of yours.

Only folders `jarvis/cleandisk.py` knows by name are ever cleaned; the phone sends back just the ids you ticked.
Apps that are open are skipped, files in use stay, links inside a cache are never followed, and with Face ID on,
cleaning needs your face. Windows Update leftovers and old Windows versions need administrator rights, so the
pop-up has a button that opens Windows' own Disk Cleanup on the PC.

## Face ID for permissions

Settings › Security › **Face ID for permissions** (Touch ID / Windows Hello in other browsers). Setting it up makes a
passkey-style key on the phone that only works with your face; the PC keeps the public half in `.brain/faceid.json`.
Every permission prompt on the phone (sending, deleting, running a command, shutting down: everything that asks)
then carries a fresh challenge, and Allow only counts when it comes back signed by that key with the face checked.
The PC checks the signature (`jarvis/faceid.py`), so someone holding your unlocked phone, or anything with only the
pairing key, can't approve. Turning it off needs Face ID too; with a lost or reset phone, right-click the tray icon
› **Turn off Face ID for permissions**. Prompts shown on the PC itself are unchanged.

## Bulgarian

Settings › **Language you speak**: English, Български or Both (`voice_language` in `config.json`). Bulgarian uses the
multilingual Whisper model (`whisper_model_multi`, "small", downloaded once to `.brain/whisper`); in Both, each
command counts as English only when Whisper is sure it's English, otherwise Bulgarian. Replies written in Cyrillic
are spoken with `edge_voice_bg` (bg-BG-KalinaNeural). Phone dictation follows the same setting.

## Self-check

When something Jarvis depends on breaks he messages you once, with the fix, and again when it works:
Claude's login or usage limit, email login, the microphone, the local models (Ollama), and Windows running out of
memory (the page file is on C:, so a full C: makes programs fail at random). `/health`, "health check" or "are you
okay?" checks everything now. See `jarvis/health.py`.

## Screens on your phone

Type **/monitors** (or `/screens`) to get one picture of all three monitors, laid out like your desk. Tap a monitor (or use the
All / Left / Main / Right switch) to zoom in; pinch, drag and double-tap work too. Screenshots go straight from the PC to your phone,
end-to-end encrypted. Claude never sees them, so they cost no tokens. They expire from the relay after 3 hours.

## Email

"Check my email" lists your unread mail sorted into **important** and **not important** (Gmail's own Important
marker and inbox tabs, newsletter/no-reply signals, and senders you list in `email_vip`). The plain "check my email"
is answered locally with no tokens. "Read the one from Anna" opens it; "reply to Anna and say I'll be there" writes
the reply and shows it on your phone. "Email Ana and tell her I'm running late" or "send my CV to boris@work.com"
writes a new email (attachments too). Names are looked up in what Jarvis remembers and in your past emails, and an
address found that way is remembered. Every email is shown to you first and only sent if you tap **Send**, whatever
the autonomy setting. Checking never marks anything as read.

Setup (once):
1. Gmail: turn on 2-Step Verification, then create an app password at https://myaccount.google.com/apppasswords.
2. Run `run.bat --email`, type your address and paste the app password (it's hidden as you type; Jarvis checks it
   signs in, then saves it in `.env` as `EMAIL_APP_PASSWORD` and the address in `config.json`).
3. Restart Jarvis.

## Memory

Every command and answer (and the steps taken) is kept per chat in `.brain/conversation.json`, whichever part of
Jarvis handled it: a quick local shortcut, Hermes, Qwen3 or Claude. Each brain is shown the same recent conversation
(by default the last 10 exchanges of the past 12 hours), so follow-ups like "close it", "do it again" or "send that to Ana" work,
also after a restart or when Claude's session is swapped for a fresh one. "Remember that…" saves a fact for good in
`.brain/memory.json` (people, email addresses, preferences); Jarvis also saves clearly useful facts by itself, and
"forget…" removes them.

On the phone, **Settings › Memory** controls all of it:

- **About me**: a note in your own words (name, work, people, how you like answers), saved in `.brain/about_me.txt`
  and given to every brain at the start of each conversation.
- **Humour**: Off, A little (default: a dry, butler-ish quip now and then) or Lots (`humour` in `config.json`).
- **Remembers the conversation for**: 2 hours, 12 hours, 3 days or 2 weeks (`memory_span`).
- **Things Jarvis remembers**: every saved fact, each with a delete button, plus a box to add one yourself and a
  button that forgets recent conversations (About me and the facts stay).

Outlook, Yahoo and iCloud are detected from the address; for anything else set `email_imap_host` / `email_smtp_host`.

## Jarvis messages you

- **More than one message.** While working, Jarvis can send extra chat bubbles with the `message_user` tool (a
  heads-up before something slow, a result as soon as he has it, a long answer in a few texts); the final answer
  comes last. From the PC microphone they're said out loud instead.
- **Without a prompt.** "Every morning at 8 check my email and tell me what matters", "at 18:00 tell me the
  weather", "in an hour check if the render finished": `schedule_task` saves the task in `.brain/schedule.json`
  (once, daily, weekdays or weekly) and, when it's due, runs it like a command and sends the answer as a message.
  Scheduled tasks show up with your reminders on the phone, and × cancels them. Reminders and finished watches now
  land in the chat too.
- **On his own** (Settings › Memory › Messages you on his own, `proactive` in `config.json`): *When asked* (only
  the above), *Important* (default: plus a heads-up when an important email arrives, checked every 5 minutes, no
  tokens) or *Chatty* (a heads-up for every new email). The morning briefing has its own switch (see Morning
  briefing). Between `quiet_hours` (23:00 to 08:00)
  unasked messages still arrive, but without a notification.

Messages wait in `.brain/inbox.json` until the phone confirms them, so they arrive even if the app was closed (with
a notification first).

## Settings (`config.json`)

| Key | What it does |
|---|---|
| `brain` | `claude-code` (uses your subscription) or `api` (paid key) |
| `claude_code_model` | Model for the Claude Code brain, e.g. `sonnet` or `haiku` for faster replies (empty = default) |
| `model` / `effort` | Model and effort for the `api` brain |
| `monitor_names` | What you call each monitor, by Windows number (1 = right vertical, 2 = main, 3 = left) |
| `known_folders` | Named shortcuts, e.g. `"projects": "D:\\Projects"` |
| `ollama_host`, `hermes_model`, `hermes_fallback` | The local Hermes brain and which brain backs it up |
| `qwen_model` | Qwen3, Hermes's teammate for multi-step commands (`qwen3:8b`; `qwen3:4b` for less VRAM, `qwen3:14b` for more; `""` = Hermes alone) |
| `local_auto_pull`, `local_ctx` | Download missing local models automatically; the local models' context window (16k) |
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
| `jarvis/hermes.py` | The free local brain: Hermes + Qwen3 (Ollama) as a team, with Claude as the fallback |
| `jarvis/nlu.py` | Free local parser for quick commands |
| `jarvis/activity.py` | The saved log of every command and its steps (the phone's pills and Activity list) |
| `jarvis/watchers.py` | Reminders and "tell me when…" watches |
| `jarvis/proactive.py` | Scheduled tasks, new-email heads-ups and when the morning briefing goes out |
| `jarvis/briefing.py` | The morning briefing: weather, calendar (iCal), reminders, email, PC |
| `jarvis/findfile.py` | Find my file: kind / time / name search in your own folders, pick from a list |
| `jarvis/nap.py` | Nap: locked, screens off, kept awake so the phone can wake it |
| `jarvis/quips.py` | Canned wit for quick replies and the briefing, as often as the humour setting says |
| `jarvis/inbox.py` | Jarvis's messages waiting for the phone to pick them up |
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
| `jarvis/away.py` | Away mode: low-level input hooks, physical input only |
| `jarvis/cleandisk.py` | /cleandisk: scan a disk into safe / your call / leave alone, clean what's ticked |
| `jarvis/faceid.py` | Face ID for permissions: stores the phone's public key, checks each signed Allow |
| `jarvis/health.py` | The self-check: problems reported once with the fix, `/health` |
| `jarvis/live.py` | The live screen stream: mss capture, confirmed pictures, adaptive size |
| `jarvis/partial.py` | The answer as it's written, throttled, for the phone |
| `jarvis/siri.py` | The "Ask Jarvis" Siri / Share shortcut's key and question handling |
| `jarvis/stopping.py` | The stop button: brains check it between steps; Claude's session is ended |
| `jarvis/pairing.py` | The tray's "Pair your phone" window: QR code + pairing link |
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
- With Face ID for permissions on, nothing that asks permission can be approved from the phone without your face.
- The Siri shortcut's key (`JARVIS_SIRI_KEY`) can run commands too, unencrypted apart from HTTPS (Cloudflare can see
  them). Delete it from `.env` and restart Jarvis to turn the shortcut off; a new one is made when you next open
  Settings › Siri & Share.
- Windows of apps running as administrator can't be moved unless Jarvis also runs as administrator.

## Tests

```
.venv\Scripts\pip install pytest
.venv\Scripts\python -m pytest tests
```
