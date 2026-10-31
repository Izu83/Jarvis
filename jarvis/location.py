"""Where you are: the location of the device you're talking to Jarvis from.

- The phone (and the desktop app) send their GPS position with each command, if you allow it (Settings › Security ›
  Share where I am). The street address comes from OpenStreetMap (Nominatim: free, at most one lookup a second, and
  each spot is looked up once).
- Spoken to the PC's own microphone: the desktop app's position if it has one, otherwise the town your internet
  connection is in.

Each command then carries a short note for the model, so "what's near me", "how far is…", "the weather here" mean
where you really are. Positions are kept in memory only, never written to disk.
"""
import logging
import threading
import time

import requests

log = logging.getLogger("jarvis.location")

FRESH = 15 * 60          # a phone's position counts for this long (seconds)
PC_FRESH = 24 * 3600     # the PC doesn't move
_where: dict[str, dict] = {}         # source ("phone", "desktop") -> {lat, lon, acc, ts}
_by_command: dict[str, str] = {}     # msg_id -> the source it came from
_addresses: dict[tuple, str] = {}    # rounded (lat, lon) -> address
_lock = threading.Lock()
_last_lookup = [0.0]


def update(where, source: str, msg_id: str = "") -> None:
    """A device's position, sent with a command: {lat, lon, acc}."""
    if msg_id:
        _by_command[msg_id] = source
        if len(_by_command) > 200:
            _by_command.pop(next(iter(_by_command)))
    try:
        lat, lon = float(where["lat"]), float(where["lon"])
        acc = float(where.get("acc") or 0)
    except (TypeError, KeyError, ValueError):
        return
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        _where[source] = {"lat": lat, "lon": lon, "acc": acc, "ts": time.time()}


def address(lat: float, lon: float) -> str:
    """'ул. Васил Левски 12, Ботевград, България' from OpenStreetMap, or '' if it can't be looked up."""
    key = (round(lat, 4), round(lon, 4))   # about 10 m
    with _lock:
        if key in _addresses:
            return _addresses[key]
        wait = 1.1 - (time.time() - _last_lookup[0])
        if wait > 0:
            time.sleep(wait)
        _last_lookup[0] = time.time()
        try:
            r = requests.get("https://nominatim.openstreetmap.org/reverse", timeout=8,
                             headers={"User-Agent": "Jarvis personal assistant (github.com/Izu83/Jarvis)"},
                             params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 18, "addressdetails": 1,
                                     "accept-language": "en,bg"})
            a = r.json().get("address") or {}
        except (requests.RequestException, ValueError):
            log.info("Couldn't look up the address.", exc_info=True)
            return ""
        street = " ".join(x for x in (a.get("road") or a.get("pedestrian") or "", a.get("house_number", "")) if x)
        area = a.get("suburb") or a.get("neighbourhood") or a.get("quarter") or ""
        town = a.get("city") or a.get("town") or a.get("village") or a.get("municipality") or ""
        text = ", ".join(x for x in (street, area, town, a.get("country", "")) if x)
        _addresses[key] = text
        return text


def note(via: str, msg_id: str = "") -> str:
    """The line added to a command so the model knows where you are ('' if nothing is known)."""
    source = _by_command.get(msg_id) if msg_id else None
    if via == "pc" or source is None:
        source = "desktop"   # the PC's own mic, a schedule, Siri: where the PC is
    here = _where.get(source)
    fresh = PC_FRESH if source == "desktop" else FRESH
    if here and time.time() - here["ts"] < fresh:
        place = address(here["lat"], here["lon"])
        what = {"phone": "their phone's location", "desktop": "this PC's location"}.get(source, "their location")
        acc = f", within about {round(here['acc'])} m" if here["acc"] else ""
        spot = f"{here['lat']:.5f}, {here['lon']:.5f}"
        return f"\n\n[Where the user is right now ({what}{acc}): {place + ' — ' if place else ''}{spot}]"
    try:
        from . import briefing
        p = briefing._cache().get("place") or {}
        if p.get("name"):
            return (f"\n\n[Where the user is, roughly (from the PC's internet connection): "
                    f"{p['name']}{', ' + p['country'] if p.get('country') else ''}]")
    except Exception:
        log.debug("no place for the PC", exc_info=True)
    return ""
