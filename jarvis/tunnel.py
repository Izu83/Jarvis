"""Public HTTPS address for the phone, via a Cloudflare quick tunnel (free, no account).

`cloudflared tunnel --url http://127.0.0.1:PORT` prints a random https://….trycloudflare.com address that forwards
to Jarvis's local WebSocket server. The address changes whenever cloudflared restarts, so cloudflared runs detached
and is reused across Jarvis restarts: the phone's saved address keeps working until the PC reboots. New addresses
are announced to the phone over the relay (see remote.py)."""
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
URL_FILE = ROOT / ".brain" / "tunnel.txt"        # current address; also read by `run.bat --pair`
LOG_FILE = ROOT / ".brain" / "cloudflared.log"   # cloudflared's own output, where it prints the address
DOWNLOAD = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class Tunnel:
    def __init__(self, port: int, on_url):
        self.port = port
        self.on_url = on_url
        self.url: str | None = None
        self.pid: int | None = None

    def start(self) -> None:
        if not BINARY.exists():
            log.error("cloudflared isn't installed. Download %s to %s", DOWNLOAD, BINARY)
            return
        threading.Thread(target=self._supervise, daemon=True, name="tunnel").start()

    def _find_running(self) -> psutil.Process | None:
        """A cloudflared from an earlier Jarvis run, still forwarding to our port."""
        for p in psutil.process_iter(["exe", "cmdline"]):
            try:
                if (p.info["exe"] or "").lower() == str(BINARY).lower():
                    if f"127.0.0.1:{self.port}" in " ".join(p.info["cmdline"] or []):
                        return p
                    p.kill()   # one pointing somewhere else is no use
            except psutil.Error:
                continue
        return None

    def _healthy(self, url: str) -> bool:
        """Does the tunnel actually reach this Jarvis? Our WebSocket server answers a plain GET with 426."""
        try:
            return requests.get(url + "/ws", timeout=10).status_code == 426
        except requests.RequestException:
            return False

    def _kill(self) -> None:
        if self.pid and psutil.pid_exists(self.pid):
            try:
                psutil.Process(self.pid).kill()
            except psutil.Error:
                pass
        self.pid = None

    def _supervise(self) -> None:
        running = self._find_running()
        url = last_url()
        if running and url:
            self.pid = running.pid
            if self._healthy(url):
                self.url = url
                log.info("Reusing the tunnel from before: %s", url)
                self.on_url(url)   # re-announce: cheap, and keeps the relay's copy fresh
            else:
                log.warning("The tunnel from before doesn't work any more; starting a new one.")
                self._kill()
        failures, last_check = 0, time.time()
        while True:
            if not self.pid or not psutil.pid_exists(self.pid):
                if self.pid:
                    log.warning("The tunnel stopped; starting a new one (the address will change).")
                self._launch()
                failures, last_check = 0, time.time()
            elif self.url and time.time() - last_check > 60:
                last_check = time.time()
                failures = 0 if self._healthy(self.url) else failures + 1
                if failures >= 3:
                    log.warning("The tunnel stopped reaching Jarvis; starting a new one (the address will change).")
                    self._kill()
                    continue
            time.sleep(15)

    def _launch(self) -> None:
        self.url = None
        LOG_FILE.parent.mkdir(exist_ok=True)
        with open(LOG_FILE, "w", encoding="utf-8") as out:
            # Detached, so it keeps running (and keeps the same address) when Jarvis restarts
            proc = subprocess.Popen(
                [str(BINARY), "tunnel", "--url", f"http://127.0.0.1:{self.port}", "--no-autoupdate"],
                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        self.pid = proc.pid
        deadline = time.time() + 60
        while time.time() < deadline:
            time.sleep(1)
            m = _URL.search(LOG_FILE.read_text(encoding="utf-8", errors="replace"))
            if m:
                self._publish_when_live(m.group(0))
                return
            if proc.poll() is not None:
                break
        log.error("cloudflared didn't give an address; see %s", LOG_FILE)

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
        self.url = url
        URL_FILE.write_text(url, encoding="utf-8")
        log.info("Tunnel ready: %s", url)
        self.on_url(url)

    def stop(self) -> None:
        """Only for tests: normally the tunnel outlives Jarvis on purpose."""
        if self.pid and psutil.pid_exists(self.pid):
            psutil.Process(self.pid).kill()


def last_url() -> str | None:
    try:
        return URL_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None
