"""Bridge between the tools (which run in the MCP subprocess) and the Jarvis process.

Two things cross the bridge:
- ask(): request the user's permission (shown on the phone if the command came from there, else a Windows dialog).
- push(): hand a payload (e.g. a file to download) to the phone over the live connection.

The Jarvis process calls serve() with handlers and gets env vars; the subprocess uses those env vars to reach it
over a tiny token-protected HTTP endpoint on 127.0.0.1. If there's no way to ask, the answer is no.
"""
import ctypes
import json
import logging
import os
import secrets
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("jarvis.approval")

TIMEOUT = 120   # seconds to answer before it counts as "no"
_ask_handler = None
_push_handler = None


def _call(payload: dict, timeout: float):
    url, token = os.environ.get("JARVIS_APPROVAL_URL"), os.environ.get("JARVIS_APPROVAL_TOKEN")
    if not url or not token:
        return None
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "X-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except OSError:
        return None


def ask(title: str, detail: str, confirm: str = "Allow") -> bool:
    """True only if the user explicitly allowed it."""
    if _ask_handler:
        return bool(_ask_handler(title, detail, confirm))
    res = _call({"action": "ask", "title": title, "detail": detail, "confirm": confirm}, TIMEOUT + 30)
    return bool(res and res.get("approved") is True)


def push(payload: dict) -> bool:
    """Send a payload to the phone (fire-and-forget). True if it was handed off."""
    if _push_handler:
        try:
            _push_handler(payload)
            return True
        except Exception:
            log.exception("push failed")
            return False
    res = _call({"action": "push", "payload": payload}, 30)
    return bool(res and res.get("ok"))


def serve(ask_handler, push_handler=None) -> dict:
    """Register handlers (in the Jarvis process) and return env vars that let the subprocess reach them."""
    global _ask_handler, _push_handler
    _ask_handler, _push_handler = ask_handler, push_handler
    token = secrets.token_hex(16)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.headers.get("X-Token") != token:
                self.send_response(403)
                self.end_headers()
                return
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            out = {"ok": False}
            try:
                if data.get("action") == "push" and push_handler:
                    push_handler(data.get("payload") or {})
                    out = {"ok": True}
                else:
                    out = {"approved": bool(ask_handler(data.get("title", ""), data.get("detail", ""),
                                                        data.get("confirm", "Allow")))}
            except Exception:
                log.exception("bridge request failed")
            blob = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)   # any free port
    threading.Thread(target=server.serve_forever, daemon=True, name="approval").start()
    return {"JARVIS_APPROVAL_URL": f"http://127.0.0.1:{server.server_port}/", "JARVIS_APPROVAL_TOKEN": token}


def desktop_confirm(title: str, detail: str, confirm: str) -> bool:
    """A topmost Yes/No dialog on the PC that defaults to No and gives up after TIMEOUT."""
    MB_YESNO, MB_ICONWARNING, MB_DEFBUTTON2 = 0x4, 0x30, 0x100
    MB_SETFOREGROUND, MB_TOPMOST = 0x10000, 0x40000
    IDYES = 6
    text = f"{title}\n\n{detail}\n\nClick Yes to {confirm.lower()}, No to cancel."
    result = ctypes.windll.user32.MessageBoxTimeoutW(
        None, text, "Jarvis needs your permission",
        MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_SETFOREGROUND | MB_TOPMOST, 0, TIMEOUT * 1000)
    return result == IDYES
