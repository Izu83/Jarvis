"""Phone channel: end-to-end encrypted messages relayed through ntfy.sh.

The phone page encrypts each command with a key only it and this PC know (AES-256-GCM),
then publishes it to a random ntfy topic. We decrypt, check it's fresh, run it, and send
an encrypted reply to "<topic>-r". ntfy only ever sees ciphertext.
"""
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

from . import modes, screens
from .config import secret, set_secret, settings

log = logging.getLogger("jarvis.remote")

CMD_AAD = b"jarvis-cmd"
REPLY_AAD = b"jarvis-reply"
IMAGE_AAD = b"jarvis-image"
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
    return f"{base}#t={topic}&k={b64e(key)}&s={settings['ntfy_server']}"


class PhoneChannel:
    def __init__(self, on_command):
        self.on_command = on_command
        self.topic, key = ensure_keys()
        self.aes = AESGCM(key)
        self.server = settings["ntfy_server"].rstrip("/")
        self.seen = collections.deque(maxlen=500)

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

    def _seal(self, data: bytes, aad: bytes) -> bytes:
        iv = secrets.token_bytes(12)
        return iv + self.aes.encrypt(iv, data, aad)

    def _publish(self, payload: dict) -> None:
        payload["ts"] = int(time.time() * 1000)
        body = b64e(self._seal(json.dumps(payload).encode(), REPLY_AAD))
        try:
            requests.post(f"{self.server}/{self.topic}-r", data=body, timeout=10)
        except requests.RequestException:
            log.exception("Couldn't send to phone")

    def reply(self, msg_id: str, text: str, done: bool = True) -> None:
        self._publish({"kind": "reply", "id": msg_id, "text": text, "done": done})
        if done:
            self.send_state()

    def send_state(self) -> None:
        """Tell the phone which modes exist (it shows them as buttons)."""
        self._publish({"kind": "state", "id": "state", "modes": modes.overview()})

    def send_screens(self, msg_id: str) -> None:
        """Screenshots go straight to the phone as encrypted ntfy attachments; Claude never sees them."""
        try:
            shots = screens.capture()
        except Exception as e:
            log.exception("Screenshot failed")
            self.reply(msg_id, f"I couldn't take screenshots: {e}")
            return
        for index, (mon, jpeg) in enumerate(shots):
            label = _short_label(mon.number)
            meta = {"kind": "image", "id": msg_id, "index": index, "count": len(shots),
                    "monitor": mon.number, "label": label, "ts": int(time.time() * 1000)}
            try:
                requests.put(
                    f"{self.server}/{self.topic}-r", data=self._seal(jpeg, IMAGE_AAD), timeout=30,
                    headers={"Filename": f"screen-{mon.number}.bin",
                             "Message": b64e(self._seal(json.dumps(meta).encode(), REPLY_AAD))},
                )
            except requests.RequestException:
                log.exception("Couldn't upload screenshot")
        self.reply(msg_id, "Here are your screens.")

    def _handle(self, data: dict) -> None:
        kind = data.get("type", "cmd")
        if kind == "sync":
            self.send_state()
        elif kind == "screens":
            log.info("Phone: screenshots")
            threading.Thread(target=self.send_screens, args=(data["id"],), daemon=True).start()
        elif data.get("text"):
            log.info("Phone: %r", data["text"])
            self.reply(data["id"], "On it...", done=False)
            self.on_command(data["text"], lambda text, i=data["id"]: self.reply(i, text))

    def run(self) -> None:
        log.info("Phone channel listening (topic %s...).", self.topic[:14])
        while True:
            try:
                with requests.get(f"{self.server}/{self.topic}/json", stream=True, timeout=(10, 120)) as r:
                    r.raise_for_status()
                    for line in r.iter_lines():
                        if not line:
                            continue
                        event = json.loads(line)
                        if event.get("event") != "message":
                            continue
                        data = self._decrypt(event.get("message", ""))
                        if data:
                            self._handle(data)
            except (requests.RequestException, json.JSONDecodeError) as e:
                log.warning("Phone channel disconnected (%s); reconnecting in 5 s.", e.__class__.__name__)
                time.sleep(5)
