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

from . import (activity, approval, away, camera, faceid, inbox, media, modes, outbox, pc, proactive, push, screens,
               siri, stopping, sysinfo, tools, watchers)
from .config import ROOT, secret, set_secret, set_setting, settings
from .tunnel import Tunnel, last_url

log = logging.getLogger("jarvis.remote")

CMD_AAD = b"jarvis-cmd"
REPLY_AAD = b"jarvis-reply"
FRAME_AAD = b"jarvis-frame"   # live-screen pictures: binary WebSocket messages (no base64, ~45% smaller)
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
        self.on_stop = lambda target="": None   # the stop button (set by __main__)
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
        self.last_chat: str | None = None   # the chat of the latest phone command (files sent go there)
        self._soon: dict[str, threading.Timer] = {}   # coalesced state / activity updates
        self._soon_lock = threading.Lock()
        activity.set_on_change(lambda: self.soon("activity", 0.3, self.send_activity))
        watchers.set_on_change(lambda: self.soon("activity", 0.3, self.send_activity))
        stopping.on_stop(self._drop_asks)
        away.set_handler(self._away_alert)
        away.set_on_change(lambda: self.soon("state", 0.2, self.send_state))   # the phone's away-mode chip
        self._frame_lock = threading.Lock()   # one live-screen picture at a time (the old, one-at-a-time way)
        from .live import LiveStream
        self.live = LiveStream(self._publish_frame, _short_label)

    def _drop_asks(self) -> None:
        """Stop pressed: any permission prompt still waiting is answered No and taken off the phone."""
        for ask_id, entry in list(self.asks.items()):
            entry["ok"] = False
            entry["event"].set()
            self._publish({"kind": "ask_done", "id": ask_id})

    # ------------------------------------------------------------ crypto

    def _decrypt(self, message: str) -> dict | None:
        try:
            raw = b64d(message.strip())
            data = json.loads(self.aes.decrypt(raw[:12], raw[12:], CMD_AAD))
        except (InvalidTag, ValueError, json.JSONDecodeError):
            log.warning("Ignored a message that wasn't encrypted with our key.")
            return None
        if abs(time.time() * 1000 - data.get("ts", 0)) > MAX_AGE_MS:
            log.warning("Ignored a stale phone message (is the phone's clock right?).")
            return None
        if data.get("id") in self.seen:
            # The phone re-sends a command when it hears nothing back (its socket had silently died). Don't run it
            # twice; just tell it where that command is, so it doesn't sit there looking like nothing happened.
            if data.get("text"):
                return {"type": "_resent", "id": data.get("id")}
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

    def _publish_frame(self, header: dict, jpeg: bytes | None) -> None:
        """A live-screen picture, sealed as one binary message: 2-byte header length, header JSON, then the JPEG."""
        if not self.clients or not self.loop:
            return
        head = json.dumps(header).encode()
        iv = secrets.token_bytes(12)
        data = iv + self.aes.encrypt(iv, len(head).to_bytes(2, "big") + head + (jpeg or b""), FRAME_AAD)
        for ws in list(self.clients):
            asyncio.run_coroutine_threadsafe(self._send(ws, data), self.loop)

    async def _send(self, ws, text: str | bytes) -> None:
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
            # the Siri Shortcut can't decrypt, so it reads the address from a topic of its own; the address alone
            # is no use without the Siri key
            requests.post(f"{self.server}/{self.topic}-s", data=url.rstrip("/") + siri.PATH, timeout=10)
        except requests.RequestException as e:
            log.warning("Couldn't announce the address to the relay: %s", e)

    def _re_announce(self) -> None:
        while True:
            time.sleep(ANNOUNCE_EVERY)
            if self.tunnel.url:
                self._announce(self.tunnel.url)

    def reply(self, msg_id: str, text: str, done: bool = True, kind: str = "reply", thinking: str = "",
              step_kind: str = "", steps: list | None = None) -> None:
        payload = {"kind": kind, "id": msg_id, "text": text, "done": done}
        if thinking:
            payload["thinking"] = thinking[:6000]
        if step_kind:
            payload["step_kind"] = step_kind   # a real step (shown as a pill), not just a status like "On it"
        if steps is not None:
            payload["steps"] = steps[-40:]
        self._publish(payload)
        if done:
            # Never on the caller's thread: this runs on the command worker, and a slow or failing status read
            # must not hold up (or kill) the next command.
            self.soon("state", 0.2, self.send_state)

    def soon(self, key: str, delay: float, fn) -> None:
        """Run fn once after `delay`, on its own thread, folding repeated requests into one."""
        with self._soon_lock:
            if key in self._soon:
                return

            def run():
                with self._soon_lock:
                    self._soon.pop(key, None)
                try:
                    fn()
                except Exception:
                    log.exception("Couldn't send %s to the phone", key)

            t = threading.Timer(delay, run)
            t.daemon = True
            self._soon[key] = t
            t.start()

    def send_activity(self) -> None:
        """What Jarvis is doing / has done, and the reminders it's holding (cheap: no PC stats)."""
        self._publish({"kind": "activity", "id": "activity", "activity": activity.summary(),
                       "watches": watchers.items() + proactive.items()})

    def send_state(self) -> None:
        """Modes (shown as buttons) and live PC status (the phone's Control Center)."""
        try:
            status = pc.status()
        except Exception:
            log.exception("Couldn't read PC status")
            status = None
        from . import usage
        try:
            from .hermes import local_status
            local = local_status()
        except Exception:
            log.exception("Couldn't check the local models")
            local = []
        self._publish({"kind": "state", "id": "state", "modes": modes.overview(), "pc": status, "local": local,
                       "use_claude": settings["use_claude"],
                       "local_first": bool(settings.get("local_first", False)),
                       "hotkey_enabled": settings["hotkey_enabled"], "hotkey": settings["hotkey"],
                       "voice_language": settings.get("voice_language", "en"),
                       "away": away.is_on(), "away_since": away.since(), "faceid": faceid.enabled(),
                       "brain": settings["brain"], "usage": usage.summary(),
                       "vapid": push.public_key(), "notify": push.have_subs(),
                       "activity": activity.summary(), "watches": watchers.items() + proactive.items()})

    def send_memory(self) -> None:
        """What Jarvis remembers, for Settings > Memory on the phone."""
        from . import memory
        self._publish({"kind": "memory", "id": "memory", "about": memory.about(),
                       "facts": [f["text"] for f in memory.facts()][::-1],   # newest first
                       "humour": settings.get("humour", "light"), "span": settings.get("memory_span", "normal"),
                       "proactive": proactive.level()})

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
            if isinstance(e, camera.NoCamera):
                camera.tell_when_back(lambda: self.message("Your webcam is connected again, so /camera works now."))
                e = f"{e}. I'll tell you when it's back"
            self.reply(msg_id, f"I couldn't take a photo: {e}.")
            return
        self._publish({"kind": "image", "id": msg_id, "monitors": [], "title": "Camera",
                       "data": base64.b64encode(jpeg).decode()})

    def _away_alert(self) -> None:
        """Away mode saw a real hand on the mouse or keyboard: tell the phone, with a webcam photo if there is one."""
        self.message("🚨 Someone is using your PC right now (away mode is on). Type /lock to lock it, or /away "
                     "when it's you.")
        try:
            jpeg = camera.capture()
        except Exception as e:
            self.message(f"I couldn't take a webcam photo of them: {e}.", notify=False)
            return
        folder = ROOT / ".brain" / "away"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / time.strftime("away-%Y%m%d-%H%M%S.jpg")
        path.write_bytes(jpeg)
        self.send_file({"path": str(path), "name": path.name, "size": len(jpeg), "mime": "image/jpeg"})

    def send_frame(self, msg_id: str, monitor) -> None:
        """One picture of a monitor for the live screen. Skipped if the last one is still being made."""
        if not self._frame_lock.acquire(blocking=False):
            return
        try:
            jpeg, info = screens.capture_monitor(int(monitor) if monitor else None)
            mons = [{"monitor": n, "label": _short_label(n)} for n in info.pop("all")]
            self._publish({"kind": "frame", "id": msg_id, **info, "monitors": mons,
                           "data": base64.b64encode(jpeg).decode()})
        except Exception as e:
            log.warning("Live screen picture failed: %s", e)
        finally:
            self._frame_lock.release()

    def live_input(self, d: dict) -> None:
        """A tap, scroll or typing from the live screen on the phone, done on the PC."""
        from . import ui
        from . import windows as win
        away.note_own_input()
        act = d.get("act", "")
        try:
            b = win.get_monitor(int(d.get("monitor") or 0)).bounds
            x = round(b.x + min(1.0, max(0.0, float(d.get("x", .5)))) * (b.w - 1))
            y = round(b.y + min(1.0, max(0.0, float(d.get("y", .5)))) * (b.h - 1))
            if act in ("click", "right", "double"):
                ui.mouse_click(x, y, "right" if act == "right" else "left", act == "double")
            elif act == "scroll":
                ui.mouse_scroll(max(-15, min(15, int(d.get("amount", 0)))), x, y)
            elif act == "type":
                ui.type_text(str(d.get("text", ""))[:2000])
            elif act == "key":
                ui.press_keys(str(d.get("keys", ""))[:40])
        except Exception as e:
            log.warning("Live screen %s failed: %s", act, e)

    def send_voice(self, req: str, text: str) -> None:
        """Speak text with the PC pop-up's neural voice and send the MP3 to the phone (no tokens)."""
        from . import speak
        audio = speak.synth(text[:4000]) if text.strip() else None
        self._publish({"kind": "tts", "id": "tts", "req": req,
                       **({"data": base64.b64encode(audio).decode()} if audio else {"error": True})})

    def send_art(self, art_id: str) -> None:
        jpeg = media.art(art_id)
        if jpeg:
            self._publish({"kind": "art", "id": "art", "art": art_id, "data": base64.b64encode(jpeg).decode()})

    def push(self, payload: dict) -> None:
        """Send a payload straight to the phone (used by tools via the approval bridge, e.g. file downloads)."""
        if payload.get("kind") == "file" and payload.get("path"):
            self.send_file(payload)
            return
        self._publish(payload)

    def send_file(self, payload: dict) -> None:
        """A file for the phone goes through the outbox: it's kept until the phone confirms it, so it still arrives
        if the app is closed right now (it's delivered the moment the app connects, with a notification first)."""
        item = outbox.add(payload["path"], payload.get("name", ""), payload.get("size", 0),
                          payload.get("mime", ""), self.last_chat)
        if self.clients:
            self.deliver_files()
        if not self.clients and push.have_subs():
            what = ("A video" if item["mime"].startswith("video/") else
                    "A picture" if item["mime"].startswith("image/") else "A file")
            push.send_async("Jarvis", f"{what} is ready for you: {item['name']}. Open Jarvis to get it.",
                            "file-" + item["fid"], "./")

    def message(self, text: str, chat: str | None = None, reply_to: str = "", notify: bool = True) -> None:
        """A message from Jarvis that isn't the answer to a command: an extra one during a task (reply_to = that
        command), or one he sends on his own. Kept until the phone confirms it, like files. If the phone doesn't
        confirm it soon (the app is closed, or its connection is asleep), it also comes as a notification."""
        text = (text or "").strip()
        if not text:
            return
        item = inbox.add(text, chat or self.last_chat, reply_to)
        self.deliver_messages()
        if notify and push.have_subs():
            self._later(0 if not self.clients else 12, self._push_if_unread, item["mid"], text)

    def _push_if_unread(self, mid: str, text: str) -> None:
        if any(i["mid"] == mid for i in inbox.due(force=True)):
            push.send("Jarvis", text[:160], "msg-" + mid, "./")

    def deliver_messages(self, force: bool = False) -> None:
        """Send every message the phone hasn't confirmed yet."""
        if not self.clients:
            return
        for item in inbox.due(force):
            inbox.mark_sent(item["mid"])
            self._publish(inbox.payload(item))

    def deliver_files(self, force: bool = False) -> None:
        """Send every file the phone hasn't confirmed yet (each read fresh from disk)."""
        if not self.clients:
            return
        for item in outbox.due(force):
            body = outbox.payload(item)
            if body:
                log.info("Sending %s to the phone.", item["name"])
                outbox.mark_sent(item["fid"])
                self._publish(body)

    def ask(self, title: str, detail: str, confirm: str) -> bool:
        """Show an Allow / Don't Allow prompt on the phone and wait for the answer (no answer = no)."""
        ask_id = secrets.token_hex(6)
        payload = {"kind": "ask", "id": ask_id, "title": title, "detail": detail, "confirm": confirm}
        if faceid.enabled():   # "Allow" must come back signed by your face (checked in _answer)
            payload.update(faceid=True, challenge=faceid.challenge(), keys=faceid.ids())
        entry = {"event": threading.Event(), "ok": False, "payload": payload}
        self.asks[ask_id] = entry
        self._publish(dict(payload))
        answered = entry["event"].wait(approval.TIMEOUT)
        self.asks.pop(ask_id, None)
        if not answered:
            self._publish({"kind": "ask_done", "id": ask_id})   # take the stale prompt off the screen
        return entry["ok"]

    def _clean_scan(self, msg_id: str, drive: str) -> None:
        """Scan a disk for /cleandisk. The paths stay here; the phone gets ids, labels, sizes and reasons."""
        from . import cleandisk
        progress = lambda t: self._publish({"kind": "clean_progress", "id": msg_id, "text": t})
        items = cleandisk.scan(drive, progress)
        scan_id = secrets.token_hex(6)
        self._clean = {"scan": scan_id, "items": {i["id"]: i for i in items}, "drive": drive, "ts": time.time(),
                       "challenge": faceid.challenge() if faceid.enabled() else ""}
        shown = [{k: i.get(k) for k in ("id", "label", "verdict", "why", "size", "partial", "blocked")} for i in items]
        self._publish({"kind": "clean_result", "id": msg_id, "scan": scan_id, "drive": drive, "items": shown,
                       "admin": cleandisk.ADMIN, "faceid": bool(self._clean["challenge"]),
                       "challenge": self._clean["challenge"], "keys": faceid.ids()})

    def _clean_run(self, msg_id: str, data: dict) -> None:
        """Clean what was ticked, from the latest scan only. Deleting: with Face ID on, it needs your face."""
        from . import cleandisk
        from .files import fmt_size
        scan = getattr(self, "_clean", None)
        if not scan or data.get("scan") != scan["scan"] or time.time() - scan["ts"] > 1800:
            self.reply(msg_id, "That scan is out of date. Run /cleandisk again.")
            return
        if scan["challenge"] and not faceid.verify(data.get("webauthn"), scan["challenge"]):
            self.reply(msg_id, "Face ID didn't check out, so nothing was cleaned.")
            return
        chosen = [scan["items"][i] for i in data.get("ids", []) if i in scan["items"]]
        self._clean = None   # one clean per scan
        log.info("Disk clean-up on %s: %s", scan["drive"], ", ".join(i["label"] for i in chosen) or "nothing")
        progress = lambda t: self._publish({"kind": "clean_progress", "id": msg_id, "text": t})
        out = cleandisk.clean(chosen, progress)
        free = next((d["free"] for d in sysinfo.disks() if d["name"].upper() == scan["drive"].upper()), None)
        text = f"Freed {fmt_size(out['freed'])} on {scan['drive']}."
        if out["skipped"]:
            text += f" {out['skipped']} files were in use and stayed."
        if free is not None:
            text += f" {fmt_size(free)} is free now."
        self._publish({"kind": "clean_done", "id": msg_id, "text": " ".join([text, *out["notes"]]),
                       "freed": out["freed"]})

    def _answer(self, data: dict) -> None:
        """Allow / Don't Allow from the phone. With Face ID on, an Allow needs a valid signature for this exact
        prompt; a bad one leaves the prompt open (Face ID can be tried again), it never counts as a yes."""
        ask_id = data.get("ask", "")
        entry = self.asks.get(ask_id)
        if not entry:
            return
        allow = data.get("allow") is True
        p = entry["payload"]
        if allow and p.get("faceid") and not faceid.verify(data.get("webauthn"), p["challenge"]):
            log.warning("Permission: an Allow without a valid Face ID was ignored (%s).", p.get("title"))
            self._publish({"kind": "ask_retry", "id": ask_id,
                           "text": "Face ID didn't check out, so it isn't allowed yet. Try again."})
            return
        entry["ok"] = allow
        entry["event"].set()

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
        if kind == "_resent":
            entry = activity.by_msg(msg_id)
            if entry and entry["status"] in ("done", "error"):
                self.reply(msg_id, entry["reply"] or "Done.", steps=entry["steps"])
            else:
                last = (entry or {}).get("steps") or []
                self.reply(msg_id, last[-1]["t"] if last else "On it...", done=False,
                           step_kind=last[-1]["k"] if last else "")
        elif kind == "sync":
            self.send_state()
            self.deliver_messages(force=True)   # anything that was waiting for the app to open
            self.deliver_files(force=True)
        elif kind == "got_file":   # the phone has saved a file: stop re-sending it
            outbox.done(data.get("fid", ""))
        elif kind == "got_msg":   # the phone has a message from Jarvis: stop re-sending it
            inbox.done(data.get("mid", ""))
        elif kind == "fetch_reply":   # the app reloaded while a command ran: send it the answer it missed
            entry = activity.by_msg(data.get("msg", ""))
            if entry and entry["status"] in ("done", "error"):
                self.reply(entry["msg_id"], entry["reply"] or "Done.", steps=entry["steps"])
        elif kind == "siri_get":   # Settings › Siri & Share: what the Shortcut needs (sent encrypted, like everything)
            self._publish({"kind": "siri", "id": "siri", "key": siri.key(),
                           "address": siri.address_url(self.server, self.topic)})
            if self.tunnel.url:   # make sure the address topic has the current address right away
                threading.Thread(target=self._announce, args=(self.tunnel.url,), daemon=True).start()
        elif kind == "stop":   # the stop button on a command that's queued or running
            log.info("Phone: stop")
            threading.Thread(target=self.on_stop, args=(data.get("target", ""),), daemon=True,
                             name="phone-stop").start()
        elif kind == "cancel_watch":
            if proactive.cancel(data.get("watch", "")) is None:   # a scheduled task, else a reminder / watch
                watchers.cancel(data.get("watch", ""))
        elif kind == "screens":
            log.info("Phone: screenshots")
            self.send_screens(msg_id)
        elif kind == "camera":
            log.info("Phone: camera photo")
            self.send_camera(msg_id)
        elif kind == "live":   # the live screen wants its next picture (an older phone page)
            self.send_frame(msg_id, data.get("monitor"))
        elif kind == "live_start":   # the live screen opened (or switched monitor): stream pictures
            self.live.start(data.get("monitor"))
        elif kind == "live_ack":
            self.live.ack(data.get("seq", 0))
        elif kind == "live_stop":
            self.live.stop()
        elif kind == "live_input":
            self.live_input(data)
            self.live.poke()
        elif kind == "away":   # /away on the phone: instant, even while a command is running
            self.reply(msg_id, away.toggle())
        elif kind == "health":   # /health on the phone
            from . import health
            self.reply(msg_id, health.check_now())
        elif kind == "watch":
            self.watch()
        elif kind == "tts":   # the phone wants a reply read aloud in Jarvis's own voice
            threading.Thread(target=self.send_voice, args=(data.get("req", ""), data.get("text", "")),
                             daemon=True, name="phone-tts").start()
        elif kind == "answer":
            self._answer(data)
        elif kind == "faceid_add":   # Settings: Face ID for permissions, on
            err = faceid.add(data.get("cred", ""), data.get("key", ""), data.get("client", ""), data.get("label", ""))
            self.reply(msg_id, err or "Face ID is on. Risky actions now need your face before they're allowed.")
        elif kind == "faceid_off_begin":   # turning it off needs Face ID too: here's what to sign
            self._faceid_off = faceid.challenge()
            self._publish({"kind": "faceid_challenge", "id": msg_id, "challenge": self._faceid_off,
                           "keys": faceid.ids()})
        elif kind == "faceid_off":
            ch, self._faceid_off = getattr(self, "_faceid_off", ""), ""
            if ch and faceid.verify(data.get("webauthn"), ch):
                faceid.clear()
                self.reply(msg_id, "Face ID for permissions is off.")
            else:
                self.reply(msg_id, "Face ID didn't check out, so it's still on.")
        elif kind == "away_off":   # the away-mode chip
            self.reply(msg_id, away.turn_off())
        elif kind == "clean_disks":   # /cleandisk: which disk?
            self._publish({"kind": "clean_disks", "id": msg_id, "disks": sysinfo.disks()})
        elif kind == "clean_scan":
            self._clean_scan(msg_id, str(data.get("drive", "C:")))
        elif kind == "clean_run":
            self._clean_run(msg_id, data)
        elif kind == "clean_open":   # the admin-only part: Windows' own Disk Cleanup, on the PC
            import subprocess
            drive = str(data.get("drive", "C:"))[:2]
            subprocess.Popen(["cleanmgr.exe", "/d", drive if drive[1:] == ":" else "C:"])
            self.reply(msg_id, "Windows Disk Cleanup is open on your PC. Click Clean up system files there.")
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
            if key == "voice_language" and data.get("value") in ("en", "bg", "both"):
                set_setting("voice_language", data["value"])
                log.info("Voice language set to %s from the phone.", data["value"])
                from . import voice
                voice.language_changed()
            elif key == "use_claude":
                set_setting("use_claude", value)
                log.info("Claude turned %s from the phone.", "on" if value else "off")
            elif key == "local_first":
                set_setting("local_first", value)
                log.info("Local models first turned %s from the phone.", "on" if value else "off")
            elif key == "hotkey_enabled":
                set_setting("hotkey_enabled", value)
                from . import hotkey
                hotkey.set_enabled(value)
                log.info("Keyboard shortcut turned %s from the phone.", "on" if value else "off")
            self.send_state()
        elif kind == "memory_get":
            self.send_memory()
        elif kind == "memory_set":   # Settings > Memory: About me, humour, how far back it remembers
            from . import memory
            if isinstance(data.get("about"), str):
                memory.set_about(data["about"])
                log.info("About me updated from the phone.")
            if data.get("humour") in memory.HUMOUR:
                set_setting("humour", data["humour"])
                log.info("Humour set to %s from the phone.", data["humour"])
            if data.get("proactive") in proactive.LEVELS:
                set_setting("proactive", data["proactive"])
                log.info("Messages on his own set to %s from the phone.", data["proactive"])
            if data.get("span") in memory.SPANS:
                set_setting("memory_span", data["span"])
                log.info("Memory span set to %s from the phone.", data["span"])
            self.send_memory()
        elif kind == "memory_add":
            from . import memory
            memory.remember(data.get("text", ""))
            self.send_memory()
        elif kind == "memory_forget":
            from . import memory
            memory.forget_exact(data.get("text", ""))
            self.send_memory()
        elif kind == "memory_clear":
            from . import memory
            memory.clear_conversations()
            log.info("Conversation memory cleared from the phone.")
            self.send_memory()
        elif kind == "usage_reset":
            from . import usage
            usage.reset()
            log.info("Usage counters reset from the phone.")
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
            self.last_chat = data.get("chat") or self.last_chat
            self.reply(msg_id, "On it...", done=False)
            text = data["text"]
            files = [f for f in (data.get("files") or []) if isinstance(f, str) and os.path.isfile(f)]
            if files:   # the user attached files (photos, screenshots, docs) from the phone
                text += ("\n\n[The user attached these files from their phone (saved on this PC): "
                         + "; ".join(files) + ". Open them with the Read tool (it shows images) if they "
                         "matter for the request.]")
            self.on_command(text, lambda text, think="", steps=None, i=msg_id: self._reply_or_push(i, text, think, steps),
                            chat=data.get("chat"), recent=data.get("recent"),
                            progress=lambda text, k="", i=msg_id: self.reply(i, text, done=False, step_kind=k),
                            via="phone", msg_id=msg_id,
                            partial=lambda text, i=msg_id: self._publish({"kind": "partial", "id": i,
                                                                          "text": text[-8000:]}))

    async def _http(self, connection, request):
        """Plain HTTP requests on the phone port: only the Siri Shortcut's /ask (see siri.py). Anything else
        carries on as the app's WebSocket."""
        if request.path.split("?")[0] != siri.PATH:
            return None
        if not siri.authorised(request.headers.get("X-Jarvis-Key", "")):
            log.warning("Siri: a request with a wrong or missing key was refused.")
            return connection.respond(401, "Wrong Jarvis key. Copy it again from the Jarvis app's Settings.\n")
        text = siri.question(request.headers.get("X-Jarvis-Ask", ""))
        if not text:
            return connection.respond(400, "There was nothing to ask.\n")
        log.info("Siri: %r", text[:80])
        answer = await self.loop.run_in_executor(None, self._ask_from_siri, text)
        return connection.respond(200, answer)

    def _ask_from_siri(self, text: str) -> str:
        """Run it like any command and wait for the answer; if it's slow, it goes to the app as a message later."""
        done, lock, box = threading.Event(), threading.Lock(), {"late": False}

        def reply(answer, thinking="", steps=None):
            with lock:
                box["answer"] = answer
                late = box["late"]
            done.set()
            if late:
                self.message(answer)

        self.on_command(text, reply, via="siri")
        done.wait(siri.WAIT)
        with lock:
            if "answer" in box:
                return box["answer"]
            box["late"] = True
        return siri.LATE

    def _reply_or_push(self, msg_id: str, text: str, thinking: str = "", steps: list | None = None) -> None:
        """Final reply to a phone command: if the app has since closed, send it as a notification instead."""
        self.reply(msg_id, text, thinking=thinking, steps=steps)
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
                       "text": f"Saved {dest.name} to {folder}."})

    def _monitor(self) -> None:
        """Warn the phone when the disk gets low or the GPU runs hot (once per episode, re-armed on recovery)."""
        warned: set[str] = set()
        while True:
            time.sleep(60)
            if not self.clients and not push.have_subs():
                continue
            try:
                limit = float(settings.get("warn_disk_gb", 10))
                for d in sysinfo.disks():   # every drive, not just C: (phone uploads go to D: by default)
                    free_gb, tag = d["free"] / 1024**3, "disk-" + d["name"]
                    if d["total"] < 32 * 1024**3:   # skip USB sticks and small recovery partitions
                        continue
                    if free_gb < limit and tag not in warned:
                        warned.add(tag)
                        self._alert("warn-" + tag, "warning", "disk", "Low disk space",
                                    f"Only {free_gb:.1f} GB free on {d['name']}. Ask me to clean up.")
                    elif free_gb >= limit + 2:
                        warned.discard(tag)
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
        # open_timeout covers _http too, which may wait up to siri.WAIT for an answer
        async with serve(self._client, "127.0.0.1", self.port, max_size=2**26, ping_interval=20, ping_timeout=20,
                         compression=None, process_request=self._http, open_timeout=siri.WAIT + 15):
            log.info("Phone server on 127.0.0.1:%d; starting tunnel...", self.port)
            self.tunnel.start()
            threading.Thread(target=self._re_announce, daemon=True, name="announce").start()
            threading.Thread(target=self._monitor, daemon=True, name="monitor").start()
            await asyncio.Future()  # run forever

    def run(self) -> None:
        asyncio.run(self._serve())
