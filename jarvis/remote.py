"""Phone channel: an end-to-end encrypted WebSocket straight from the phone to this PC.

Jarvis runs a small WebSocket server on 127.0.0.1 and exposes it through a Cloudflare quick tunnel (tunnel.py).
Every message, both ways, is AES-256-GCM encrypted with a key only the phone and this PC know, so the tunnel only
sees ciphertext. The tunnel's address changes when it restarts, so we announce it (encrypted) on an ntfy.sh topic;
that's the only thing the relay carries now, a handful of messages a day instead of thousands.
"""
import os
import asyncio
import base64
import collections
import json
import logging
import secrets
import threading
import time

import requests
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from . import approval, camera, media, modes, pc, push, screens, sysinfo, tools
from .config import ROOT, secret, set_secret, set_setting, settings
from .tunnel import Tunnel, last_url

log = logging.getLogger("jarvis.remote")

CMD_AAD = b"jarvis-cmd"
REPLY_AAD = b"jarvis-reply"
WATCH_INTERVAL = 3          # seconds between live stats while the System page is open
ANNOUNCE_EVERY = 4 * 3600   # re-announce the address before the relay forgets it (12 h)
MAX_AGE_MS = 120_000


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _short_label(number: int) -> str:
    """'right vertical monitor' -> 'Right', for captions on the phone."""
    name = settings["monitor_names"].get(str(number), "").lower()
    for word, label in (("left", "Left"), ("right", "Right"), ("main", "Main"), ("middle", "Main"), ("vertical", "Vertical")):
        if word in name:
            return label
    return f"Monitor {number}"


def ensure_keys() -> tuple[str, bytes]:
    topic, key = secret("JARVIS_TOPIC"), secret("JARVIS_KEY")
    if not topic or not key:
        topic, key = "jarvis-" + secrets.token_hex(12), b64e(secrets.token_bytes(32))
        set_secret("JARVIS_TOPIC", topic)
        set_secret("JARVIS_KEY", key)
    return topic, b64d(key)


def pairing_link() -> str:
    topic, key = ensure_keys()
    base = settings["phone_page_url"] or "https://<your-github-name>.github.io/jarvis/"
    link = f"{base}#t={topic}&k={b64e(key)}&s={settings['ntfy_server']}"
    url = last_url()   # lets the phone connect right away, even before it hears an announcement
    return link + (f"&u={url}" if url else "")


class PhoneChannel:
    def __init__(self, on_command):
        self.on_command = on_command
        self.topic, key = ensure_keys()
        self.aes = AESGCM(key)
        self.server = settings["ntfy_server"].rstrip("/")
        self.port = int(settings.get("phone_port", 8787))
        self.seen = collections.deque(maxlen=500)
        self.clients: set = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.tunnel = Tunnel(self.port, self._announce)
        self.watch_until = 0.0
        self._watching = False
        self.asks: dict[str, dict] = {}   # permission prompts waiting for an answer
        self.uploads: dict[str, dict] = {}   # files arriving from the phone in pieces

    # ------------------------------------------------------------ crypto

    def _decrypt(self, message: str) -> dict | None:
        try:
            raw = b64d(message.strip())
            data = json.loads(self.aes.decrypt(raw[:12], raw[12:], CMD_AAD))
        except (InvalidTag, ValueError, json.JSONDecodeError):
            log.warning("Ignored a message that wasn't encrypted with our key.")
            return None
        if abs(time.time() * 1000 - data.get("ts", 0)) > MAX_AGE_MS or data.get("id") in self.seen:
            log.warning("Ignored a stale or repeated phone message.")
            return None
        self.seen.append(data.get("id"))
        return data

    def _seal(self, payload: dict) -> str:
        payload["ts"] = int(time.time() * 1000)
        iv = secrets.token_bytes(12)
        return b64e(iv + self.aes.encrypt(iv, json.dumps(payload).encode(), REPLY_AAD))

    # ------------------------------------------------------------ sending

    def _publish(self, payload: dict) -> None:
        """Send to every connected phone. Safe to call from any thread."""
        if not self.clients or not self.loop:
            return
        text = self._seal(payload)
        for ws in list(self.clients):
            asyncio.run_coroutine_threadsafe(self._send(ws, text), self.loop)

    async def _send(self, ws, text: str) -> None:
        try:
            await ws.send(text)
        except ConnectionClosed:
            self.clients.discard(ws)

    def _announce(self, url: str) -> None:
        """Tell the phone where to connect (encrypted, kept by the relay for 12 h)."""
        body = self._seal({"kind": "hello", "id": "hello", "url": url})
        try:
            r = requests.post(f"{self.server}/{self.topic}-r", data=body, timeout=10)
            if r.status_code == 429:
                log.warning("Couldn't post the address to the relay (its free daily limit is used up). A phone that "
                            "already knows this address connects fine; otherwise paste the link from `run.bat --pair`.")
            elif r.status_code != 200:
                log.warning("Couldn't tell the phone the new address (relay answered %s). To connect now, run "
                            "`run.bat --pair` and scan the QR code.", r.status_code)
            else:
                log.info("Told your phone the new address.")
        except requests.RequestException as e:
            log.warning("Couldn't announce the address to the relay: %s", e)

    def _re_announce(self) -> None:
        while True:
            time.sleep(ANNOUNCE_EVERY)
            if self.tunnel.url:
                self._announce(self.tunnel.url)

    def reply(self, msg_id: str, text: str, done: bool = True, kind: str = "reply", thinking: str = "") -> None:
        payload = {"kind": kind, "id": msg_id, "text": text, "done": done}
        if thinking:
            payload["thinking"] = thinking[:6000]
        self._publish(payload)
        if done:
            self.send_state()

    def send_state(self) -> None:
        """Modes (shown as buttons) and live PC status (the phone's Control Center)."""
        try:
            status = pc.status()
        except Exception:
            log.exception("Couldn't read PC status")
            status = None
        self._publish({"kind": "state", "id": "state", "modes": modes.overview(), "pc": status,
                       "use_claude": settings["use_claude"],
                       "hotkey_enabled": settings["hotkey_enabled"], "hotkey": settings["hotkey"],
                       "vapid": push.public_key(), "notify": push.have_subs()})

    def _later(self, seconds: float, fn, *args) -> None:
        threading.Timer(seconds, fn, args).start()

    def send_screens(self, msg_id: str) -> None:
        """One picture of all monitors, straight to the phone; Claude never sees it."""
        try:
            jpeg, layout = screens.capture_desktop()
        except Exception as e:
            log.exception("Screenshot failed")
            self.reply(msg_id, f"I couldn't take a screenshot: {e}")
            return
        for m in layout:
            m["label"] = _short_label(m["monitor"])
        self._publish({"kind": "image", "id": msg_id, "monitors": layout, "data": base64.b64encode(jpeg).decode()})

    def send_camera(self, msg_id: str) -> None:
        """One webcam photo straight to the phone; Claude never sees it, so it costs no tokens."""
        try:
            jpeg = camera.capture()
        except Exception as e:
            log.warning("Camera capture failed: %s", e)
            self.reply(msg_id, f"I couldn't take a photo: {e}")
            return
        self._publish({"kind": "image", "id": msg_id, "monitors": [], "title": "Camera",
                       "data": base64.b64encode(jpeg).decode()})

    def send_art(self, art_id: str) -> None:
        jpeg = media.art(art_id)
        if jpeg:
            self._publish({"kind": "art", "id": "art", "art": art_id, "data": base64.b64encode(jpeg).decode()})

    def push(self, payload: dict) -> None:
        """Send a payload straight to the phone (used by tools via the approval bridge, e.g. file downloads)."""
        self._publish(payload)

    def ask(self, title: str, detail: str, confirm: str) -> bool:
        """Show an Allow / Don't Allow prompt on the phone and wait for the answer (no answer = no)."""
        ask_id = secrets.token_hex(6)
        payload = {"kind": "ask", "id": ask_id, "title": title, "detail": detail, "confirm": confirm}
        entry = {"event": threading.Event(), "ok": False, "payload": payload}
        self.asks[ask_id] = entry
        self._publish(dict(payload))
        answered = entry["event"].wait(approval.TIMEOUT)
        self.asks.pop(ask_id, None)
        if not answered:
            self._publish({"kind": "ask_done", "id": ask_id})   # take the stale prompt off the screen
        return entry["ok"]

    def watch(self) -> None:
        """The System page is open: stream detailed stats until it stops asking (60 s)."""
        self.watch_until = time.time() + 60
        if self._watching:
            return
        self._watching = True

        def loop():
            try:
                while time.time() < self.watch_until and self.clients:
                    started = time.time()
                    try:
                        self._publish({"kind": "sys", "id": "sys", "sys": sysinfo.snapshot()})
                    except Exception:
                        log.exception("Couldn't read system stats")
                    time.sleep(max(0.5, WATCH_INTERVAL - (time.time() - started)))
            finally:
                self._watching = False

        threading.Thread(target=loop, daemon=True, name="sys-watch").start()

    # ------------------------------------------------------------ receiving

    def _handle(self, data: dict) -> None:
        kind, msg_id = data.get("type", "cmd"), data.get("id", "")
        if kind == "sync":
            self.send_state()
        elif kind == "screens":
            log.info("Phone: screenshots")
            self.send_screens(msg_id)
        elif kind == "camera":
            log.info("Phone: camera photo")
            self.send_camera(msg_id)
        elif kind == "watch":
            self.watch()
        elif kind == "answer":
            entry = self.asks.get(data.get("ask", ""))
            if entry:
                entry["ok"] = data.get("allow") is True
                entry["event"].set()
        elif kind == "art":
            self.send_art(data.get("art", ""))
        elif kind == "media":  # Control Center buttons: instant, no Claude
            tools.run("media_key", {"key": data["key"]})
            self._later(0.8, self.send_state)  # apps need a moment to update what's playing
        elif kind == "lock":
            log.info("Phone: lock PC")
            self.reply(msg_id, "Locked your PC.")
            pc.lock()
        elif kind == "clip_set":
            pc.set_clipboard(data.get("text", ""))
            self.reply(msg_id, "Copied to your PC's clipboard.")
        elif kind == "push_sub":   # the phone registered for notifications
            push.add_sub(data.get("sub") or {})
            if data.get("quiet"):
                self.send_state()
            else:
                self.reply(msg_id, "Notifications are on. You'll hear from me even when the app is closed.")
        elif kind == "push_off":
            push.remove_sub(data.get("endpoint", ""))
            self.send_state()
        elif kind == "push_test":
            if not push.have_subs():
                self.reply(msg_id, "Your PC has no phone registered. Turn the switch off and on again.")
            else:
                sent = push.send("Jarvis", "Notifications are working \U0001f44d", "test")
                self.reply(msg_id, "Test notification sent." if sent else
                           "Apple refused the notification. Turn the switch off and on again (details in jarvis.log).")
        elif kind == "set":   # a settings toggle from the phone
            key, value = data.get("key"), bool(data.get("value"))
            if key == "use_claude":
                set_setting("use_claude", value)
                log.info("Claude turned %s from the phone.", "on" if value else "off")
            elif key == "hotkey_enabled":
                set_setting("hotkey_enabled", value)
                from . import hotkey
                hotkey.set_enabled(value)
                log.info("Keyboard shortcut turned %s from the phone.", "on" if value else "off")
            self.send_state()
        elif kind == "clip_get":
            text = pc.get_clipboard()
            self.reply(msg_id, text or "Your PC's clipboard has no text in it.", kind="clip" if text else "reply")
        elif kind == "upload":   # a file sent from the phone -> save it on the PC (old single-message form)
            self._save_upload(data)
        elif kind == "upload_part":   # big files come in pieces so no single message is too large
            self._upload_part(data)
        elif kind == "gesture":   # the Gesture button: start/stop the webcam hand controller, no Claude
            from . import gesture
            action = data.get("action", "toggle")
            self.reply(msg_id, {"start": gesture.start, "stop": gesture.stop, "toggle": gesture.toggle}[action]())
        elif data.get("text"):
            log.info("Phone: %r", data["text"])
            self.reply(msg_id, "On it...", done=False)
            text = data["text"]
            files = [f for f in (data.get("files") or []) if isinstance(f, str) and os.path.isfile(f)]
            if files:   # the user attached files (photos, screenshots, docs) from the phone
                text += ("\n\n[The user attached these files from their phone (saved on this PC): "
                         + "; ".join(files) + ". Open them with the Read tool (it shows images) if they "
                         "matter for the request.]")
            self.on_command(text, lambda text, think="", i=msg_id: self._reply_or_push(i, text, think),
                            chat=data.get("chat"), recent=data.get("recent"),
                            progress=lambda text, i=msg_id: self.reply(i, text, done=False), via="phone")

    def _reply_or_push(self, msg_id: str, text: str, thinking: str = "") -> None:
        """Final reply to a phone command: if the app has since closed, send it as a notification instead."""
        self.reply(msg_id, text, thinking=thinking)
        if not self.clients and push.have_subs():
            push.send_async("Jarvis", text[:160], "reply", "./")

    async def _client(self, ws) -> None:
        self.clients.add(ws)
        log.info("Phone connected (%d open).", len(self.clients))
        for entry in list(self.asks.values()):   # a question still waiting? ask again on the new connection
            await ws.send(self._seal(dict(entry["payload"])))
        try:
            async for message in ws:
                if isinstance(message, str):
                    data = self._decrypt(message)
                    if data:   # run off the event loop so slow work never blocks other messages
                        self.loop.run_in_executor(None, self._safe_handle, data)
        except ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)
            log.info("Phone disconnected (%d open).", len(self.clients))

    def _safe_handle(self, data: dict) -> None:
        try:
            self._handle(data)
        except Exception:
            log.exception("Phone request failed")

    def _upload_part(self, data: dict) -> None:
        up, i, n = data.get("up", ""), int(data.get("i", 0)), int(data.get("n", 1))
        now = time.time()
        for k in [k for k, v in self.uploads.items() if now - v["t"] > 600]:   # drop abandoned uploads
            self.uploads.pop(k, None)
        entry = self.uploads.setdefault(up, {"name": data.get("name", "file"), "n": n, "parts": {}, "t": now})
        entry["parts"][i] = data.get("data", "")
        entry["t"] = now
        if len(entry["parts"]) < entry["n"]:
            self._publish({"kind": "upload_progress", "id": up, "got": len(entry["parts"]), "n": entry["n"]})
            return
        self.uploads.pop(up, None)
        joined = b"".join(base64.b64decode(entry["parts"][k]) for k in range(entry["n"]))
        self._save_upload({"id": up, "name": entry["name"]}, joined)

    def _save_upload(self, data: dict, raw: bytes | None = None) -> None:
        from pathlib import Path
        name = os.path.basename(data.get("name", "file")) or "file"
        # Default to "From Phone" on the same drive as the project (D:), so a nearly-full C: isn't a problem.
        # Override with "upload_folder" in config.json. Fall back to Downloads if that folder can't be made.
        folder = Path(settings.get("upload_folder") or (Path(ROOT).anchor + "From Phone"))
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = Path(os.path.expanduser("~/Downloads")) / "From Phone"
            folder.mkdir(parents=True, exist_ok=True)
        dest = folder / name
        i = 1
        while dest.exists():   # don't overwrite
            dest = folder / f"{Path(name).stem} ({i}){Path(name).suffix}"
            i += 1
        try:
            dest.write_bytes(raw if raw is not None else base64.b64decode(data.get("data", "")))
        except Exception:
            log.exception("Couldn't save the uploaded file")
            self._publish({"kind": "uploaded", "id": data.get("id", ""), "ok": False, "text": "I couldn't save that file."})
            return
        log.info("Saved a file from the phone: %s", dest)
        self._publish({"kind": "uploaded", "id": data.get("id", ""), "ok": True, "path": str(dest),
                       "text": f"Saved {dest.name} to Downloads\\From Phone."})

    def _monitor(self) -> None:
        """Warn the phone when the disk gets low or the GPU runs hot (once per episode, re-armed on recovery)."""
        warned: set[str] = set()
        while True:
            time.sleep(60)
            if not self.clients and not push.have_subs():
                continue
            try:
                import shutil
                free_gb = shutil.disk_usage("C:\\").free / 1024**3
                limit = float(settings.get("warn_disk_gb", 10))
                if free_gb < limit and "disk" not in warned:
                    warned.add("disk")
                    self._alert("warn-disk", "warning", "disk", "Low disk space",
                                f"Only {free_gb:.1f} GB free on C:. Windows may slow down. Ask me to clean up.")
                elif free_gb >= limit + 2:
                    warned.discard("disk")
                gpus = sysinfo.gpus()
                temp = gpus[0]["temp"] if gpus and gpus[0].get("temp") is not None else 0
                hot = float(settings.get("warn_gpu_temp", 85))
                if temp >= hot and "gpu" not in warned:
                    warned.add("gpu")
                    self._alert("warn-gpu", "danger", "cpu", "GPU running hot", f"Your GPU is at {temp:.0f}\u00b0C.")
                elif temp and temp < hot - 8:
                    warned.discard("gpu")
            except Exception:
                log.debug("monitor check failed", exc_info=True)

    def _alert(self, tag: str, level: str, icon: str, title: str, text: str, also_push: bool = True) -> None:
        """Show a banner if the app is open, otherwise send it as a phone notification."""
        if self.clients:
            self._publish({"kind": "warn", "id": tag, "level": level, "icon": icon, "title": title, "text": text})
        elif also_push and push.have_subs():
            push.send_async(title, text, tag, "./")

    async def _serve(self) -> None:
        self.loop = asyncio.get_running_loop()
        async with serve(self._client, "127.0.0.1", self.port, max_size=2**26, ping_interval=20, ping_timeout=20,
                         compression=None):
            log.info("Phone server on 127.0.0.1:%d; starting tunnel...", self.port)
            self.tunnel.start()
            threading.Thread(target=self._re_announce, daemon=True, name="announce").start()
            threading.Thread(target=self._monitor, daemon=True, name="monitor").start()
            await asyncio.Future()  # run forever

    def run(self) -> None:
        asyncio.run(self._serve())
