// Takes the app screenshots in the README (assets/readme/shots/) from the real page in docs/, filled with made-up
// example chats and stats, so no real chat, key or file ever ends up in a picture.
//
//   node tools/make_readme_shots.js
//
// It uses the Playwright that the hidden browser installs (.brain/browser-mcp) and your installed Chrome, headless.
// The page is served straight from docs/ through Playwright (no server); 127.0.0.1:8788 is the desktop app's address,
// so the page lays itself out as the desktop app there.
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const { chromium } = require(path.join(ROOT, ".brain", "browser-mcp", "node_modules", "playwright-core"));
const DOCS = path.join(ROOT, "docs");
const OUT = path.join(ROOT, "assets", "readme", "shots");
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".json": "application/json", ".png": "image/png",
                ".jpg": "image/jpeg", ".bin": "application/octet-stream", ".svg": "image/svg+xml" };

// --- the made-up example data -----------------------------------------------------------------------------------
function seed() {   // runs in the page before its own script: a fake pairing (random key, nowhere to connect) + chats
  if (sessionStorage.getItem("seeded")) return;
  sessionStorage.setItem("seeded", "1");
  localStorage.clear();
  const key = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))));
  localStorage.setItem("jarvis.pairing", JSON.stringify({ topic: "readme-demo", key, server: "http://127.0.0.1:9", url: "http://127.0.0.1:9" }));
  localStorage.setItem("jarvis.mainMode", "chat");
  localStorage.setItem("jarvis.orbForm", "orb");
  const now = Date.now(), m = 60000, h = 3600000;
  const chat = (id, title, ago, messages) => ({ id, title, group: null, created: now - ago, updated: now - ago + 60000, messages, unread: false });
  const main = chat("c1", "Left monitor, Windy, pizza", 14 * m, [
    { who: "me", text: "Open Spotify, Discord and Chrome and put them on my left monitor", ts: now - 14 * m },
    { who: "steps", ts: now - 14 * m + 2000, steps: [{ k: "Apps", t: "Opened Spotify" }, { k: "Apps", t: "Opened Discord" },
      { k: "Apps", t: "Opened Chrome" }, { k: "Windows", t: "Side by side on the left monitor" }] },
    { who: "jarvis", text: "Done. Spotify, Discord and Chrome are side by side on your left monitor.", ts: now - 14 * m + 9000 },
    { who: "me", text: "What's the wind like in Botevgrad on Windy?", ts: now - 6 * m },
    { who: "steps", ts: now - 6 * m + 2000, steps: [{ k: "Browser", t: "Opening windy.com in my browser" },
      { k: "Browser", t: "Reading the page" }, { k: "Browser", t: "Looking at the forecast" }] },
    { who: "jarvis", text: "Almost calm in Botevgrad right now: **0–2 knots**, with gusts up to about **9 knots** this afternoon. It's **8°C**, so take a jacket.", ts: now - 6 * m + 40000 },
    { who: "me", text: "Remind me in 20 minutes to take the pizza out", ts: now - m },
    { who: "steps", ts: now - m + 1000, steps: [{ k: "Reminder", t: "Take the pizza out · in 20 min" }] },
    { who: "jarvis", text: "I'll remind you in 20 minutes. Don't let it burn.", ts: now - m + 3000 },
  ]);
  const old = [
    chat("c2", "Clean up my D: drive", 26 * h, [{ who: "me", text: "Clean up my D: drive", ts: now - 26 * h }, { who: "jarvis", text: "Freed 14.2 GB.", ts: now - 26 * h + 5000 }]),
    chat("c3", "Morning briefing", 9 * h, [{ who: "jarvis", text: "Good morning.", ts: now - 9 * h }]),
    chat("c4", "Work mode", 3 * h, [{ who: "me", text: "Work mode", ts: now - 3 * h }, { who: "jarvis", text: "Work mode is on.", ts: now - 3 * h + 2000 }]),
    chat("c5", "Send me the PDF I downloaded yesterday", 50 * h, [{ who: "me", text: "Send me the PDF I downloaded yesterday", ts: now - 50 * h }, { who: "jarvis", text: "Here it is.", ts: now - 50 * h + 3000 }]),
    chat("c6", "How much space is left on my disks?", 75 * h, [{ who: "me", text: "How much space is left on my disks?", ts: now - 75 * h }, { who: "jarvis", text: "C: has 61 GB free and D: has 412 GB free.", ts: now - 75 * h + 3000 }]),
  ];
  localStorage.setItem("jarvis.db", JSON.stringify({ chats: [...old, main], groups: [], current: "c1" }));
}

function wire() {   // runs once the page is up: no connecting, and the PC's state as if Jarvis had sent it
  window.connect = async () => {};
  ws = { readyState: WebSocket.OPEN, send() {}, close() {} };   // looks connected; what it "sends" goes nowhere
  document.getElementById("lostCard")?.remove();
  window.demoState = {
    kind: "state", where: { name: "Botevgrad", country: "Bulgaria", tz: "Europe/Sofia", sky: "Clear", temp: 8 },
    modes: [{ name: "work", icon: "briefcase", summary: "Monitor 1: File Explorer, Claude, Spotify · Monitor 3: Opera, File Explorer · Monitor 2: Visual Studio Code" },
            { name: "game", icon: "gamepad", summary: "Monitor 2: Steam · Monitor 1: Discord, Spotify · Monitor 3: Opera" },
            { name: "night", icon: "moon", summary: "Monitor 2: Night Screens" }],
    pc: { host: "Niko's PC", uptime: 3 * 86400 + 5 * 3600, cpu: 14, ram: 47, top: { name: "Spotify", cpu: 3.1 },
          music: { title: "Blinding Lights", artist: "The Weeknd", app: "Spotify", playing: true, position: 84, duration: 200 } },
    local: [], use_claude: true, claude_model: "sonnet", effort: "medium", use_hermes: true, use_qwen: true,
    models: { use_gemini: true, gemini_key: true, use_chatgpt: false, chatgpt_key: false, use_grok: false, grok_key: false, spend: 0, budget: 5 },
    model_id: "claude-sonnet-5-5",
    devices: { phones: 1, desktops: 1, phone_seen: Date.now() / 1000, tunnel: true },
    local_first: false, hotkey_enabled: true, hotkey: "ctrl+alt+j", voice_language: "both", voice: "en-GB-RyanNeural",
    away: false, away_since: 0, faceid: true, ask_permission: true, nap: false, nap_since: 0, brain: "claude-code",
    usage: { claude_calls: 142, tokens_total: 1840233, local_calls: 37 }, notify: true,
  };
  onState(demoState); renderStatus(true);
}

function sysSample(i) {   // the System page: an i5-10400F + RTX 4060 PC, values wandering a little
  const w = (base, amp, f) => Math.max(1, Math.round(base + amp * Math.sin(i / f) + amp * 0.5 * Math.sin(i / (f * 0.37))));
  const GB = 1024 ** 3;
  return {
    host: "Niko's PC", uptime: 3 * 86400 + 5 * 3600,
    cpu: { pct: w(18, 9, 3), name: "Intel(R) Core(TM) i5-10400F CPU @ 2.90GHz", cores: 6, threads: 12, ghz: 4.1,
           per_core: Array.from({ length: 12 }, (_, c) => w(16 + (c % 4) * 6, 10, 2 + c / 3)) },
    gpus: [{ name: "GeForce RTX 4060", load: w(23, 8, 4), vram_used: 3420, vram_total: 8188, temp: 54, fan: 38 }],
    ram: { pct: w(47, 2, 6), used: 15.1 * GB, total: 31.9 * GB },
    net: { down: w(2.4e6, 1.2e6, 2.5), up: w(3.1e5, 1.5e5, 3) },
    disks: [{ name: "C:", label: "Windows", free: 61 * GB, total: 237 * GB, pct: 74 },
            { name: "D:", label: "Data", free: 412 * GB, total: 931 * GB, pct: 56 }],
    apps: [{ name: "Spotify", cpu: 3.1, mem: 412e6 }, { name: "Chrome", cpu: 2.4, mem: 1.3e9 }, { name: "Discord", cpu: 1.2, mem: 380e6 },
           { name: "Jarvis", cpu: 0.9, mem: 610e6 }, { name: "Steam", cpu: 0.4, mem: 240e6 }],
  };
}

// --- taking the pictures ----------------------------------------------------------------------------------------
async function open(browser, origin, viewport, scale) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: scale, colorScheme: "dark", serviceWorkers: "block",
                                             locale: "en-GB", timezoneId: "Europe/Sofia" });
  await context.route("**/*", route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return url.hostname.endsWith("googleapis.com") || url.hostname.endsWith("gstatic.com") ? route.continue() : route.abort();
    const file = path.join(DOCS, decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname));
    if (!file.startsWith(DOCS) || !fs.existsSync(file)) return route.fulfill({ status: 404, body: "" });
    route.fulfill({ status: 200, body: fs.readFileSync(file), contentType: TYPES[path.extname(file)] || "application/octet-stream" });
  });
  await context.addInitScript(seed);
  const page = await context.newPage();
  await page.goto(origin + "/");
  await page.waitForTimeout(1200);
  await page.evaluate(wire);
  await page.waitForTimeout(500);
  return page;
}

async function shot(page, name, prep, wait = 700) {
  if (prep) await page.evaluate(prep);
  await page.waitForTimeout(wait);
  await page.screenshot({ path: path.join(OUT, name + ".png") });
  console.log("  " + name + ".png");
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const phone = await open(browser, "http://127.0.0.1:8811", { width: 390, height: 844 }, 2);
  console.log("Phone:");

  await shot(phone, "chat", () => { $("log").scrollTop = $("log").scrollHeight; });
  await shot(phone, "home", () => { newChat(); });
  await shot(phone, "slash", () => { const t = $("text"); t.value = "/"; t.dispatchEvent(new Event("input", { bubbles: true })); });
  await phone.evaluate(() => { const t = $("text"); t.value = ""; t.dispatchEvent(new Event("input", { bubbles: true })); openChat("c1"); });
  await shot(phone, "models", () => { Models.open(); });
  await phone.evaluate(() => Models.close());
  await shot(phone, "control", () => { openControl(); onState(demoState); }, 900);
  await phone.evaluate(() => closeSheets());
  await phone.evaluate(sample => { openSystem(); for (let i = 0; i < 40; i++) onSys(eval("(" + sample + ")")(i)); }, sysSample.toString());
  await shot(phone, "system", null, 900);
  await phone.evaluate(() => closeSheets());

  const speak = () => {
    setMainMode("voice");
    VoiceMode.set("speaking", "Speaking", "Almost calm in Botevgrad right now, with gusts up to about 9 knots this afternoon.");
    Orb.source = () => .45 + .3 * Math.sin(performance.now() / 140) * Math.sin(performance.now() / 370);
  };
  await shot(phone, "voice", speak, 2500);
  await shot(phone, "humanoid", () => {
    Orb.setForm("head"); store("jarvis.orbForm", "head"); VoiceMode.renderToggles();
    Orb.source = () => .45 + .3 * Math.sin(performance.now() / 140) * Math.sin(performance.now() / 370);
  }, 3500);
  await phone.context().close();

  console.log("Desktop app:");
  const desk = await open(browser, "http://127.0.0.1:8788", { width: 1280, height: 800 }, 1.5);
  await shot(desk, "desktop", () => { openChat("c1"); $("log").scrollTop = $("log").scrollHeight; }, 900);
  await desk.context().close();
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
