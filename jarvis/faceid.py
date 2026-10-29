"""Face ID (or Touch ID / Windows Hello) for the permission prompts on the phone.

Setting it up, the phone makes a passkey-style key pair that only unlocks with your face (WebAuthn, platform
authenticator, user verification required); the PC keeps the public half in .brain/faceid.json. Then every
permission prompt sent to the phone carries a fresh random challenge, and "Allow" only counts if it comes back
signed by that key with your face checked. The check happens HERE, on the PC, so someone holding your unlocked
phone can't approve, and neither can anything that only has the pairing key.

Turning it off needs Face ID too, or the tray menu on the PC (for a lost or reset phone).
"""
import base64
import hashlib
import json
import logging
import secrets
import threading
import time
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from .config import ROOT

log = logging.getLogger("jarvis.faceid")

PATH = ROOT / ".brain" / "faceid.json"
UP, UV = 0x01, 0x04   # authenticator data flags: user present, user verified (face / fingerprint / PIN)

_lock = threading.Lock()


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _load() -> list[dict]:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(creds: list[dict]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(creds, indent=1), encoding="utf-8")


def enabled() -> bool:
    return bool(_load())


def ids() -> list[str]:
    return [c["id"] for c in _load()]


def challenge() -> str:
    return b64u(secrets.token_bytes(32))


def add(cred_id: str, spki_b64: str, client_data_b64: str, label: str = "") -> str:
    """A key made on the phone. Only when Face ID isn't on yet (to change phones, turn it off first)."""
    with _lock:
        if _load():
            return "Face ID is already set up. Turn it off first to set it up again."
        client = json.loads(unb64u(client_data_b64))
        if client.get("type") != "webauthn.create":
            return "That wasn't a Face ID setup."
        key = serialization.load_der_public_key(base64.b64decode(spki_b64))
        if not isinstance(key, (ec.EllipticCurvePublicKey, rsa.RSAPublicKey)):
            return "That kind of key isn't supported."
        origin = client.get("origin", "")
        _save([{"id": cred_id, "key": spki_b64, "origin": origin, "rp": urlparse(origin).hostname or "",
                "label": label[:60], "added": time.time()}])
    log.info("Face ID for permissions turned on (%s).", label or "a phone")
    return ""


def clear() -> None:
    with _lock:
        PATH.unlink(missing_ok=True)
    log.info("Face ID for permissions turned off.")


def verify(assertion: dict | None, expected_challenge: str) -> bool:
    """Is this a real "yes" from the registered key, with the face checked, for exactly this challenge?"""
    if not assertion:
        return False
    try:
        cred = next((c for c in _load() if c["id"] == assertion.get("id")), None)
        if not cred:
            log.warning("Face ID: an answer signed by an unknown key.")
            return False
        raw_client = unb64u(assertion["clientDataJSON"])
        client = json.loads(raw_client)
        auth = unb64u(assertion["authenticatorData"])
        sig = unb64u(assertion["signature"])
        if client.get("type") != "webauthn.get" or client.get("challenge") != expected_challenge:
            log.warning("Face ID: wrong kind of answer or a different question.")
            return False
        if client.get("origin") != cred["origin"]:
            log.warning("Face ID: signed on a different site (%s).", client.get("origin"))
            return False
        if len(auth) < 37 or auth[:32] != hashlib.sha256(cred["rp"].encode()).digest():
            log.warning("Face ID: the key belongs to a different site.")
            return False
        if auth[32] & (UP | UV) != (UP | UV):
            log.warning("Face ID: the face (or passcode) wasn't checked.")
            return False
        signed = auth + hashlib.sha256(raw_client).digest()
        key = serialization.load_der_public_key(base64.b64decode(cred["key"]))
        if isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(sig, signed, ec.ECDSA(hashes.SHA256()))
        else:
            key.verify(sig, signed, padding.PKCS1v15(), hashes.SHA256())
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError) as e:
        log.warning("Face ID: the signature didn't check out (%s).", type(e).__name__)
        return False
