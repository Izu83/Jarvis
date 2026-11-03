<p align="center">
  <img src="assets/readme/banner.svg" alt="Jarvis, made by Nikolay Rangelov" width="100%">
</p>

<p align="center">
  <a href="https://github.com/Izu83" title="Nikolay Rangelov (Izu83)"><img src="https://github.com/Izu83.png?size=192" width="96" height="96" alt="Nikolay Rangelov"></a>
</p>

<p align="center">
  <img alt="Windows" src="https://img.shields.io/badge/Windows-11-0e3a4f?style=for-the-badge&labelColor=071422&logo=windows&logoColor=5fe3ff">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.13-0e3a4f?style=for-the-badge&labelColor=071422&logo=python&logoColor=5fe3ff">
  <img alt="Claude" src="https://img.shields.io/badge/Prompts-Claude-0e3a4f?style=for-the-badge&labelColor=071422&logo=claude&logoColor=5fe3ff">
  <img alt="Whisper" src="https://img.shields.io/badge/Speech-Whisper-0e3a4f?style=for-the-badge&labelColor=071422">
  <img alt="iPhone" src="https://img.shields.io/badge/iPhone-web%20app-0e3a4f?style=for-the-badge&labelColor=071422&logo=apple&logoColor=5fe3ff">
</p>

<p align="center">
  Say <b>“Hey Jarvis, open Spotify and Discord and put them on my left monitor”</b>, or type it on your iPhone from
  anywhere, and it happens. Jarvis lives on your PC, listens for his name, answers out loud,
  and you can chat with him from your phone, the desktop app or Siri.
</p>

<p align="center">
  <a href="#what-it-does">What it does</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#getting-started">Getting started</a> ·
  <a href="#things-you-can-say">Things you can say</a> ·
  <a href="#on-your-iphone">iPhone</a> ·
  <a href="#the-models">Models</a> ·
  <a href="#privacy-and-safety">Privacy</a> ·
  <a href="#troubleshooting">Troubleshooting</a> ·
  <a href="GUIDE.md"><b>Full guide</b></a>
</p>

<h2><a name="what-it-does"></a><img src="assets/readme/headers/what-it-does.svg" alt="What it does" width="100%"></h2>

- **Listens for "Hey Jarvis"** on your PC's microphone, in English or Bulgarian, and answers in a natural voice. Say "stop" or "wait" to cut him off.
- **Runs your PC**: opens apps, folders and websites, moves windows between your monitors, plays and skips music, changes the volume, locks or shuts down the PC.
- **Saved layouts**: say "work mode" and every app opens in its place on every monitor in a second.
- **Chat from your iPhone**, from anywhere: the same commands by text or voice, with a live view of your screens, now playing, CPU and GPU stats and your files.
- **A desktop app** with your chats down the side and a model picker, like a normal chat app.
- **Voice mode** with a glowing orb (or a 3D head) that listens, thinks and talks back.
- **Siri**: "Hey Siri, Ask Jarvis" works too, and so does the Share button in any app.
- **Does website jobs in the background** (checking the weather on Windy, sending an Instagram message) in his own hidden browser, so your screen and mouse stay yours.
- **Messages you first**: a morning briefing (weather, calendar, reminders, email), an evening recap, reminders, and a heads-up when an important email arrives.
- **Finds and sends files**: "send me the PDF I downloaded yesterday" lands on your phone.
- **Remembers** what you talked about and the facts you tell him, and asks before anything risky (Face ID on the phone, Windows Hello on the PC).

<h2><a name="how-it-works"></a><img src="assets/readme/headers/how-it-works.svg" alt="How it works" width="100%"></h2>

```
 "Hey Jarvis" on the PC mic ─┐
 iPhone app / desktop app ───┼──►  Jarvis on your PC  ──►  Claude decides the steps  ──►  apps, windows, files,
 Siri shortcut ──────────────┘     (Python, tray icon)                                     music, websites, ...
```

1. **Hearing you.** A small wake-word model listens for "Hey Jarvis". Then [Whisper](https://github.com/SYSTRAN/faster-whisper) writes down what you said, on your own PC (on the graphics card when there is one).
2. **Quick things first.** Simple commands like "next song", "volume up" or "work mode" are understood right on the PC with no AI at all, so they're instant and free.
3. **Claude handles the prompts.** Everything else goes to Claude, which decides the steps and uses Jarvis's tools to carry them out: open this, move that, read the screen, search the files, send a message.
4. **Your phone connects** through an encrypted tunnel, so it works from anywhere, not just on your Wi-Fi.

<h2><a name="getting-started"></a><img src="assets/readme/headers/getting-started.svg" alt="Getting started" width="100%"></h2>

**You need:** Windows 11, [Python 3.13](https://www.python.org/downloads/), a microphone, a [Claude](https://claude.ai) plan with [Claude Code](https://claude.com/claude-code) installed, and an iPhone if you want the phone app. An NVIDIA graphics card makes speech recognition much faster but isn't required.

**1. Download and install**

```bash
git clone https://github.com/Izu83/Jarvis.git
cd Jarvis
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

**2. Log in to Claude once.** Open a terminal, run `claude`, type `/login`, finish in the browser, then `/exit`. Jarvis uses that login, so there's no API key to pay for.

**3. Start Jarvis**

```bash
run.bat
```

Nothing opens: Jarvis sits in the taskbar tray (under the **^** arrow). Click the icon to open the desktop app, right-click it for the menu. Say **"Hey Jarvis"** (or press **Ctrl+Alt+J**), say what you want, and pause.

**4. Optional extras**

| Command | What it does |
|---|---|
| `run.bat --startup` | Start Jarvis automatically when you log in to Windows |
| `.venv\Scripts\python -m jarvis.desktop --install` | Put a **Jarvis** shortcut on the Desktop and in the Start menu |
| `run.bat --keys` | Add a free Gemini key, or ChatGPT / Grok keys (see [The models](#the-models)) |
| `run.bat --email` | Connect your email so Jarvis can check, read and send it |
| `npm install --prefix .brain/browser-mcp @playwright/mcp@0.0.83` | The hidden browser for website jobs (needs [Node.js](https://nodejs.org) and Chrome) |
| `run.bat --console` | Run with the log in a window, to see what's going on |

<h2><a name="things-you-can-say"></a><img src="assets/readme/headers/things-you-can-say.svg" alt="Things you can say" width="100%"></h2>

| You say | What happens |
|---|---|
| "Open Spotify, Claude and File Explorer and put them on my left monitor" | Opens all three and tiles them on that monitor |
| "Work mode" | Opens your saved layout on all monitors, instantly |
| "Make a gaming mode with Discord on the vertical monitor and Steam full screen" | Saves a new layout you can start by name |
| "Pause the music", "next song", "turn it up a bit" | Controls whatever is playing |
| "Send me the latest screenshot" | Sends the file to your phone |
| "What's the wind like in Botevgrad on Windy?" | Checks the site in the hidden browser and tells you |
| "Every morning at 8 check my email and tell me what matters" | Saves a task that runs every day and messages you |
| "Remind me in 20 minutes to take the pizza out" | Sets a reminder that pops up on your phone and PC |
| "How much space is left on my disks?" | Answers from live PC stats |
| "Кой си ти?" | Answers in Bulgarian, with a Bulgarian voice |

<h2><a name="on-your-iphone"></a><img src="assets/readme/headers/on-your-iphone.svg" alt="On your iPhone" width="100%"></h2>

The phone app is a web app on GitHub Pages ([izu83.github.io/Jarvis](https://izu83.github.io/Jarvis/)), so there's no App Store and nothing to pay for.

<table>
  <tr>
    <td align="center" width="200"><img src="docs/icon-192.png" width="120" alt="The Jarvis icon"><br><sub>How it looks on your Home Screen</sub></td>
    <td>
      1. On the PC, right-click the Jarvis tray icon › <b>Pair your phone (QR code)…</b><br>
      2. Scan the code with your iPhone camera and open the link in <b>Safari</b>.<br>
      3. Tap <b>Share › Add to Home Screen</b>. Jarvis now opens like a normal app.<br><br>
      <b>Siri:</b> in the app, open <b>Settings › Siri &amp; Share</b> and follow the steps once. Then say <i>“Hey Siri, Ask Jarvis”</i>.
    </td>
  </tr>
</table>

In the app you get your chats, Voice mode, a Control Center (now playing, CPU, clipboard, lock), live pictures of your monitors, a live remote screen (`/live`), the activity log and all the settings. Type `/` in the message box to see every shortcut.

<h2><a name="the-models"></a><img src="assets/readme/headers/the-models.svg" alt="The models" width="100%"></h2>

Claude handles the prompts. You can also tick other models in the model picker (in the message box), and Jarvis picks the best ticked one for each message, the cheapest that can do the job well. If one fails or hits its limit, the next one answers.

| Model | Cost | Best at |
|---|---|---|
| **Claude** | Your Claude plan | Doing things on the PC, code, reading the screen |
| **Gemini** | Free key from [Google AI Studio](https://aistudio.google.com/apikey) | Everyday questions, news and anything current (with Google Search) |
| **ChatGPT**, **Grok** | Pay per use, with a monthly budget you set | Backup; Grok for X / Twitter |
| **Hermes**, **Qwen3** | Free, on your graphics card ([Ollama](https://ollama.com)) | Simple commands when you're offline or out of Claude |

<h2><a name="privacy-and-safety"></a><img src="assets/readme/headers/privacy-and-safety.svg" alt="Privacy and safety" width="100%"></h2>

- **Your voice stays on your PC.** The wake word and speech recognition both run locally.
- **Phone and PC talk end-to-end encrypted** (AES-256-GCM). The tunnel and relay in between only ever see scrambled data.
- **Your keys live in `.env`** on your PC, which is never uploaded. The code and the phone page contain no secrets.
- **Risky actions ask first**: deleting files, running commands, shutting down. On the phone the Allow button can require Face ID; on the PC, Windows Hello or your Windows password. You can turn the questions off in Settings.
- **Hard limits stay**: Windows and system folders can never be deleted.
- **Keep your pairing link private.** It's the key to your PC. If you lose your phone, delete `JARVIS_TOPIC` and `JARVIS_KEY` from `.env` and run `run.bat --pair`.

<h2><a name="troubleshooting"></a><img src="assets/readme/headers/troubleshooting.svg" alt="Troubleshooting" width="100%"></h2>

| Problem | Fix |
|---|---|
| "Hey Jarvis" isn't heard | Right-click the tray icon and pick the right microphone. Still nothing? Lower `wake_threshold` in `config.json` (e.g. `0.35`) |
| It triggers by itself | Raise `wake_threshold` a little |
| The phone says it can't reach the PC | Make sure Jarvis is running, then right-click the tray icon › **Pair your phone** and scan the new code |
| "Claude Code CLI not found" | Install [Claude Code](https://claude.com/claude-code), then log in once (step 2 above) |
| Speech recognition is slow | Without an NVIDIA graphics card it runs on the CPU. That works, just slower |
| A website job needs a login | Right-click the tray icon › **Log in to a site for Jarvis…**, log in yourself and close the window |
| Something else is wrong | Say or type **"health check"**. Jarvis checks everything and tells you how to fix what's broken. The log is in `jarvis.log` |

<h2><a name="project-layout"></a><img src="assets/readme/headers/project-layout.svg" alt="Project layout" width="100%"></h2>

| Path | What it is |
|---|---|
| `jarvis/` | Jarvis himself: voice, the phone connection, the tools, the models |
| `docs/` | The phone and desktop app (served by GitHub Pages) |
| `tests/` | Tests: `.venv\Scripts\python -m pytest tests` |
| `tools/` | Scripts that build the 3D head and the pictures in this README |
| `assets/readme/` | The banner and headers used here |
| `run.bat`, `Jarvis.vbs` | Start Jarvis |
| `config.json`, `modes.json` | Your settings and saved layouts (made on the first run) |
| `.env` | Your private keys (never uploaded) |
| [`GUIDE.md`](GUIDE.md) | **The full guide**: every feature, every setting and what each file does |

<h2><a name="credits"></a><img src="assets/readme/headers/credits.svg" alt="Credits" width="100%"></h2>

Built with [Claude](https://claude.ai) for the prompts, [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [openWakeWord](https://github.com/dscripka/openWakeWord), [edge-tts](https://github.com/rany2/edge-tts), [Playwright MCP](https://github.com/microsoft/playwright-mcp), [Cloudflare Tunnel](https://github.com/cloudflare/cloudflared), [ntfy](https://ntfy.sh), [Ollama](https://ollama.com), [pywebview](https://pywebview.flowrl.com) and [Open-Meteo](https://open-meteo.com).

The humanoid in Voice mode is "Infinite, 3D Head Scan" by Lee Perry-Smith ([Infinite Realities](https://www.ir-ltd.net)), licensed [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/).

<p align="center">
  <a href="https://github.com/Izu83"><img src="https://github.com/Izu83.png?size=112" width="56" alt="Izu83"></a><br>
  Made by <b>Nikolay Rangelov</b> (<a href="https://github.com/Izu83">Izu83</a>)
</p>
