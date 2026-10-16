"""Public HTTPS address for the phone, via a Cloudflare quick tunnel (free, no account).

`cloudflared tunnel --url http://127.0.0.1:PORT` prints a random https://….trycloudflare.com address that forwards
to Jarvis's local WebSocket server. The address changes whenever cloudflared restarts, so it's announced to the phone
over the relay (see remote.py)."""
import atexit
import logging
import re
import subprocess
import threading
import time

import psutil
import requests

from .config import ROOT

log = logging.getLogger("jarvis.tunnel")

BINARY = ROOT / "bin" / "cloudflared.exe"
URL_FILE = ROOT / ".brain" / "tunnel.txt"   # for `run.bat --pair`, which runs in another process
DOWNLOAD = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class Tunnel:
    def __init__(self, port: int, on_url):
        self.port = port
        self.on_url = on_url
        self.url: str | None = None
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        if not BINARY.exists():
            log.error("cloudflared isn't installed. Download %s to %s", DOWNLOAD, BINARY)
            return
        _kill_leftovers()
        atexit.register(self.stop)
        threading.Thread(target=self._run, daemon=True, name="tunnel").start()

    def _run(self) -> None:
        while True:
            self.proc = subprocess.Popen(
                [str(BINARY), "tunnel", "--url", f"http://127.0.0.1:{self.port}", "--no-autoupdate"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in self.proc.stderr:   # cloudflared logs to stderr, including the address
                m = _URL.search(line)
                if m and m.group(0) != self.url:
                    url = m.group(0)
                    threading.Thread(target=self._publish_when_live, args=(url,), daemon=True).start()
            code = self.proc.wait()
            log.warning("cloudflared exited (code %s); restarting in 5 s.", code)
            self.url = None
            time.sleep(5)

    def _publish_when_live(self, url: str) -> None:
        """Only hand out the address once DNS knows it. A lookup that comes too early gets a "doesn't exist" answer
        that home routers cache for minutes, so the phone would fail to connect even though the tunnel is up."""
        host = url.removeprefix("https://")
        for _ in range(60):
            try:
                r = requests.get("https://cloudflare-dns.com/dns-query", params={"name": host, "type": "A"},
                                 headers={"accept": "application/dns-json"}, timeout=5)
                if r.ok and r.json().get("Answer"):
                    break
            except (requests.RequestException, ValueError):
                pass
            time.sleep(2)
        time.sleep(3)   # let it spread to other resolvers too
        if self.proc is None or self.proc.poll() is not None:
            return
        self.url = url
        URL_FILE.parent.mkdir(exist_ok=True)
        URL_FILE.write_text(url, encoding="utf-8")
        log.info("Tunnel ready: %s", url)
        self.on_url(url)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()


def last_url() -> str | None:
    try:
        return URL_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _kill_leftovers() -> None:
    """A tunnel left behind by a Jarvis that was killed would point at nothing; stop it."""
    for p in psutil.process_iter(["exe"]):
        try:
            if p.info["exe"] and p.info["exe"].lower() == str(BINARY).lower():
                p.kill()
        except psutil.Error:
            continue
