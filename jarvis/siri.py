"""Ask Jarvis from Siri or the iPhone's Share button, through an iPhone Shortcut.

A Shortcut can't do the phone app's encryption, so it uses a key of its own (JARVIS_SIRI_KEY in .env) over the
same HTTPS tunnel the app uses:
  1. GET https://ntfy.sh/<topic>-s/raw?poll=1&since=latest  -> "<the PC's current address>/ask" (re-posted every 4 h)
  2. GET that with the headers X-Jarvis-Key: <key> and X-Jarvis-Ask: <URL-encoded text>
     -> the answer as plain text, which the Shortcut shows (Siri reads it out when you asked by voice).
An answer that takes longer than WAIT seconds is sent to the phone app as a message instead.
The phone app's Settings › Siri & Share shows the steps, with the address and key to copy.
"""
import hmac
import secrets
from urllib.parse import unquote

from .config import secret, set_secret

PATH = "/ask"
WAIT = 45   # Shortcuts gives up on a request after about a minute
LATE = "I'm still working on that. I'll message you in the Jarvis app when it's done."


def key() -> str:
    k = secret("JARVIS_SIRI_KEY")
    if not k:
        k = secrets.token_urlsafe(18)
        set_secret("JARVIS_SIRI_KEY", k)
    return k


def address_url(server: str, topic: str) -> str:
    """Where the Shortcut looks up the PC's current address (the tunnel's changes now and then)."""
    return f"{server.rstrip('/')}/{topic}-s/raw?poll=1&since=latest"


def authorised(given: str) -> bool:
    k = secret("JARVIS_SIRI_KEY")
    return bool(k) and hmac.compare_digest(given.strip().encode(), k.encode())


def question(raw: str) -> str:
    """The X-Jarvis-Ask header: URL-encoded by the Shortcut (headers can't carry most non-English text as is)."""
    return unquote(raw or "").strip()[:4000]
