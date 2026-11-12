"""Waking your other computer: Wake-on-LAN from this one.

Each Jarvis tells the phone its network card's address (info()), and the phone passes those on to your other
computers ("peers" in config.json). So when the PC is asleep, the laptop (on the same home network) can send it the
"magic packet" that wakes it: from the phone's device list, or by asking ("wake up my PC").

It works when the sleeping computer is on a cable (Wi-Fi cards mostly can't listen while asleep) and its network
card is allowed to wake it (Device Manager › the network adapter › Power Management). From Sleep it just works;
from Shut down the BIOS has to allow it too.
"""
import logging
import re
import socket
import time

import psutil

from .config import set_setting, settings

log = logging.getLogger("jarvis.wake")

MAC = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
_cache: dict = {"at": 0.0, "info": None}


def _local_ip() -> str | None:
    """The address this computer uses to reach the internet (no packet is actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def broadcast_of(ip: str, mask: str) -> str:
    """192.168.1.20 with 255.255.255.0 -> 192.168.1.255"""
    a = [int(x) for x in ip.split(".")]
    m = [int(x) for x in mask.split(".")]
    return ".".join(str(a[i] & m[i] | (255 ^ m[i])) for i in range(4))


def info() -> dict | None:
    """{"mac", "broadcast", "wired"} of the network card this computer is online with (kept for 10 minutes)."""
    if time.time() - _cache["at"] < 600:
        return _cache["info"]
    found = None
    ip = _local_ip()
    if ip:
        for name, addrs in psutil.net_if_addrs().items():
            v4 = next((a for a in addrs if a.family == socket.AF_INET and a.address == ip), None)
            mac = next((a.address for a in addrs if a.family == psutil.AF_LINK and MAC.match(a.address or "")), None)
            if v4 and mac:
                wifi = any(w in name.lower() for w in ("wi-fi", "wifi", "wireless", "wlan"))
                found = {"mac": mac.replace("-", ":").upper(), "broadcast": broadcast_of(ip, v4.netmask or "255.255.255.0"),
                         "wired": not wifi}
                break
    _cache.update(at=time.time(), info=found)
    return found


def packet(mac: str) -> bytes:
    raw = bytes.fromhex(re.sub(r"[^0-9A-Fa-f]", "", mac))
    if len(raw) != 6:
        raise ValueError("That isn't a network card address.")
    return b"\xff" * 6 + raw * 16


def send(mac: str, broadcast: str = "") -> None:
    """The magic packet, to the home network's broadcast address and to everyone, on the usual ports."""
    data = packet(mac)
    targets = {"255.255.255.255"}
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", broadcast or ""):
        targets.add(broadcast)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for host in targets:
            for port in (9, 7):
                try:
                    s.sendto(data, (host, port))
                except OSError:
                    log.debug("Couldn't send the wake packet to %s:%d", host, port, exc_info=True)
    log.info("Sent a wake-up packet to %s.", mac)


def learn(peers) -> bool:
    """The phone's list of your other computers ([{"name", "mac", "broadcast"}]). True when it changed."""
    clean = {}
    for p in peers if isinstance(peers, list) else []:
        if not isinstance(p, dict) or not MAC.match(str(p.get("mac", ""))):
            continue
        name = " ".join(str(p.get("name") or "PC").split())[:30]
        clean[name] = {"mac": str(p["mac"]).upper(), "broadcast": str(p.get("broadcast") or "")[:15],
                       "wired": bool(p.get("wired", True))}
    if clean == (settings.get("peers") or {}):
        return False
    set_setting("peers", clean)
    return True


def wake_by_name(name: str = "") -> str:
    """For the wake_computer tool: wake the one you named (or the only one there is)."""
    peers = settings.get("peers") or {}
    if not peers:
        return ("I don't know your other computers' network addresses yet. Open the Jarvis app on your phone while "
                "it's connected to each computer once, then try again.")
    wanted = (name or "").strip().lower()
    match = [n for n in peers if n.lower() == wanted] or [n for n in peers if wanted and wanted in n.lower()]
    if not match and len(peers) == 1:
        match = list(peers)
    if not match:
        return f"I know these computers: {', '.join(peers)}. Which one should I wake?"
    n = match[0]
    send(peers[n]["mac"], peers[n].get("broadcast", ""))
    tip = "" if peers[n].get("wired", True) else " It's on Wi-Fi, though, which often can't be woken."
    return f"Sent {n} a wake-up signal. It should be online in about a minute if it was asleep.{tip}"
