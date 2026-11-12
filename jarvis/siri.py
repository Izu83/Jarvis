"""Ask Jarvis from Siri or the iPhone's Share button, through an iPhone Shortcut.

A Shortcut can't do the phone app's encryption, so it uses a key of its own (JARVIS_SIRI_KEY in .env) over the
same HTTPS tunnel the app uses:
  1. GET https://ntfy.sh/<topic>-s/raw?poll=1&since=latest  -> "<the PC's current address>/ask" (re-posted every 4 h)
  2. GET that with the headers X-Jarvis-Key: <key> and X-Jarvis-Ask: <URL-encoded text>
     -> the answer as plain text, which the Shortcut shows (Siri reads it out when you asked by voice).
An answer that takes longer than WAIT seconds is sent to the phone app as a message instead.

"Send to Jarvis" is the same, for photos and files from any app's Share button: a POST to the same address with
the item as the body (front.py takes it, since the WebSocket server only speaks GET) and what to do with it in
X-Jarvis-Ask (may be empty). The file is saved on the PC like one sent from the app, and Jarvis looks at it.

The phone app's Settings › Siri & Share shows the steps, with the address and key to copy.
"""
import hmac
import logging
import mimetypes
import secrets
import time
from urllib.parse import unquote

from .config import secret, set_secret

PATH = "/ask"
WAIT = 45   # Shortcuts gives up on a request after about a minute
LATE = "I'm still working on that. I'll message you in the Jarvis app when it's done."
LOOK_TEXT = "I shared this with you from my iPhone. Have a look and tell me briefly what it is."
LOOK_FILE = "I shared this with you from my iPhone. Have a look and tell me briefly what's in it."

log = logging.getLogger("jarvis.siri")


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


def _texty(ctype: str, body: bytes) -> str | None:
    """Text or a link (Shortcuts sends those as text/plain or similar): the text, else None for a real file."""
    if not (ctype.startswith("text/") or ctype in ("", "application/octet-stream", "application/json")):
        return None
    if len(body) > 200_000:
        return None
    try:
        text = body.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    printable = sum(c.isprintable() or c in "\n\r\t" for c in text[:2000])
    return text if text and printable >= 0.97 * min(len(text), 2000) else None


def _jpeg_from_heic(body: bytes) -> bytes | None:
    """iPhone photos are often HEIC, which the models can't open: a JPEG copy when pillow-heif is installed."""
    try:
        import io

        from PIL import Image
        from pillow_heif import register_heif_opener
        register_heif_opener()
        out = io.BytesIO()
        Image.open(io.BytesIO(body)).convert("RGB").save(out, "JPEG", quality=90)
        return out.getvalue()
    except Exception:
        log.info("Couldn't turn a HEIC photo into a JPEG (pip install pillow-heif)", exc_info=True)
        return None


def shared(note: str, headers: dict, body: bytes, save) -> str:
    """What Jarvis is asked when something is shared with him: the note plus the text or link, or the note plus
    where the file was saved (save(name, data) -> path). "" when there's nothing at all."""
    ctype = headers.get("content-type", "").split(";")[0].strip().lower()
    if not body:
        return note
    text = _texty(ctype, body)
    if text is not None:
        return f"{note or LOOK_TEXT}\n\nWhat I shared: {text[:8000]}"
    if ctype in ("image/heic", "image/heif"):
        jpeg = _jpeg_from_heic(body)
        if jpeg:
            body, ctype = jpeg, "image/jpeg"
    ext = {"image/jpeg": ".jpg", "image/heic": ".heic", "image/heif": ".heif"}.get(ctype) or mimetypes.guess_extension(ctype) or ""
    name = unquote(headers.get("x-jarvis-name", "")).strip().replace("/", "_").replace("\\", "_")
    name = name or f"Shared {time.strftime('%Y-%m-%d %H.%M.%S')}{ext}"
    path = save(name, body)
    return (f"{note or LOOK_FILE}\n\n[The user shared this file from their iPhone (saved on this PC): {path}. "
            "Open it with the Read tool (it shows images) if it matters for the request.]")
