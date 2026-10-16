"""Asking the user before anything destructive (deleting, clearing, running commands).

The Jarvis process registers a handler with serve(): it asks on the phone if the command came from there,
otherwise with a Windows dialog. Tools usually run in a separate process (the MCP server that Claude Code starts),
so serve() also opens a small token-protected HTTP endpoint on 127.0.0.1 that ask() calls from there.
If there's no way to ask, the answer is no.
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
_handler = None


def ask(title: str, detail: str, confirm: str = "Allow") -> bool:
    """True only if the user explicitly allowed it."""
    if _handler:
        return bool(_handler(title, detail, confirm))
    url, token = os.environ.get("JARVIS_APPROVAL_URL"), os.environ.get("JARVIS_APPROVAL_TOKEN")
    if not url or not token:
        return False
    body = json.dumps({"title": title, "detail": detail, "confirm": confirm}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "X-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT + 30) as r:
            return json.load(r).get("approved") is True
    except OSError:
        return False


def serve(handler) -> dict:
    """Register how to ask (in the Jarvis process) and return env vars that let other processes ask too."""
    global _handler
    _handler = handler
    token = secrets.token_hex(16)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.headers.get("X-Token") != token:
                self.send_response(403)
                self.end_headers()
                return
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            try:
                ok = bool(handler(data.get("title", ""), data.get("detail", ""), data.get("confirm", "Allow")))
            except Exception:
                log.exception("Asking for permission failed")
                ok = False
            out = json.dumps({"approved": ok}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

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
