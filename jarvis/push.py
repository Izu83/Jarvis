"""Web Push notifications to the phone, even when the Jarvis app is closed.

Uses the standard Web Push protocol with our own VAPID keys, so the PC sends notifications directly to Apple's /
the browser's push service, no third party. iPhone requires the app to be added to the Home Screen and notifications
allowed. Subscriptions (one per device) and the VAPID keypair are stored under .brain/.
"""
import base64
import json
import logging
import threading

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from pywebpush import WebPushException, webpush

from .config import ROOT

log = logging.getLogger("jarvis.push")

VAPID_FILE = ROOT / ".brain" / "vapid.json"
SUBS_FILE = ROOT / ".brain" / "push_subs.json"
CLAIMS = {"sub": "mailto:jarvis@localhost"}
_lock = threading.Lock()


def _vapid() -> Vapid01:
    VAPID_FILE.parent.mkdir(exist_ok=True)
    if VAPID_FILE.exists():
        data = json.loads(VAPID_FILE.read_text())
        v = Vapid01.from_pem(data["private_pem"].encode())
    else:
        v = Vapid01()
        v.generate_keys()
        pem = v.private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()).decode()
        VAPID_FILE.write_text(json.dumps({"private_pem": pem}))
    return v


_V = _vapid()


def public_key() -> str:
    """The base64url applicationServerKey the browser needs to subscribe."""
    raw = _V.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _load_subs() -> list[dict]:
    try:
        return json.loads(SUBS_FILE.read_text())
    except (OSError, ValueError):
        return []


def _save_subs(subs: list[dict]) -> None:
    SUBS_FILE.write_text(json.dumps(subs))


def add_sub(sub: dict) -> None:
    if not sub.get("endpoint"):
        return
    with _lock:
        subs = [s for s in _load_subs() if s.get("endpoint") != sub["endpoint"]]
        subs.append(sub)
        _save_subs(subs[-10:])   # keep the 10 most recent devices
    log.info("Registered a device for notifications (%d total).", len(subs[-10:]))


def remove_sub(endpoint: str) -> None:
    with _lock:
        _save_subs([s for s in _load_subs() if s.get("endpoint") != endpoint])


def have_subs() -> bool:
    return bool(_load_subs())


def send(title: str, body: str, tag: str = "jarvis", url: str = "") -> int:
    """Send a notification to every registered device. Returns how many were delivered."""
    payload = json.dumps({"title": title, "body": body, "tag": tag, "url": url})
    sent, dead = 0, []
    for sub in _load_subs():
        try:
            webpush(subscription_info=sub, data=payload, vapid_private_key=_V, vapid_claims=dict(CLAIMS), timeout=10)
            sent += 1
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):   # the subscription is gone (app deleted / unsubscribed)
                dead.append(sub.get("endpoint"))
            else:
                log.warning("Push failed (%s)", code or e)
        except Exception:
            log.exception("Push failed")
    for ep in dead:
        remove_sub(ep)
    return sent


def send_async(title: str, body: str, tag: str = "jarvis", url: str = "") -> None:
    threading.Thread(target=send, args=(title, body, tag, url), daemon=True).start()
