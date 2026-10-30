"""The morning briefing: one message about your day, put together on the PC (no Claude, no tokens).

- Weather from Open-Meteo (free, no key). The place is "weather_city", or worked out from your internet
  connection when that's empty.
- Your calendar, if you've given Jarvis your Google Calendar's secret iCal address (.env CALENDAR_ICS_URL,
  set from the phone). Repeating events count too.
- Today's reminders and scheduled tasks, unread email, and how the PC is doing.

Each part is optional: one that can't be reached right now is left out, never guessed. Sent every day at
"morning_time" while "briefing" is on (proactive.py does the timing), or any time you ask for it.
"""
import datetime as dt
import json
import logging
import re
import shutil

import requests

from . import quips
from .config import ROOT, secret, settings

log = logging.getLogger("jarvis.briefing")

CACHE = ROOT / ".brain" / "briefing.json"
ICS_SECRET = "CALENDAR_ICS_URL"
TIMEOUT = 10

# WMO weather codes (what Open-Meteo returns) in plain words
WEATHER = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "foggy", 48: "foggy with frost",
           51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
           61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
           71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "showers", 81: "showers",
           82: "heavy showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
           96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def _cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(data: dict) -> None:
    try:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        log.debug("Couldn't save the briefing cache", exc_info=True)


# ---------------------------------------------------------------- weather

def place() -> dict | None:
    """{"name", "lat", "lon"} for the weather: the city in settings, else where the internet connection is."""
    city = (settings.get("weather_city") or "").strip()
    cache = _cache()
    if cache.get("place") and cache.get("place_for") == city:
        return cache["place"]
    try:
        if city:
            r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                             params={"name": city, "count": 1}, timeout=TIMEOUT).json()
            hit = (r.get("results") or [None])[0]
            found = hit and {"name": hit["name"], "lat": hit["latitude"], "lon": hit["longitude"]}
        else:
            r = requests.get("http://ip-api.com/json/", params={"fields": "status,city,lat,lon"}, timeout=TIMEOUT).json()
            found = r.get("status") == "success" and {"name": r["city"], "lat": r["lat"], "lon": r["lon"]}
    except (requests.RequestException, ValueError, KeyError):
        log.info("Couldn't work out where you are for the weather.")
        return None
    if not found:
        return None
    cache.update(place=found, place_for=city)
    _save_cache(cache)
    return found


def weather() -> str | None:
    where = place()
    if not where:
        return None
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=TIMEOUT, params={
            "latitude": where["lat"], "longitude": where["lon"], "timezone": "auto", "forecast_days": 1,
            "current": "temperature_2m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"}).json()
        now, day = r["current"], r["daily"]
        rain = (day.get("precipitation_probability_max") or [None])[0]
        line = (f"{where['name']}: {round(now['temperature_2m'])}° and {WEATHER.get(now['weather_code'], 'mixed')} now, "
                f"{round(day['temperature_2m_max'][0])}° / {round(day['temperature_2m_min'][0])}° today")
        if rain is not None and rain >= 30:
            line += f", {rain}% chance of rain. Take an umbrella."
        else:
            line += "."
        return line
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        log.info("Couldn't get the weather.")
        return None


# ---------------------------------------------------------------- calendar

def calendar_set() -> bool:
    return bool(secret(ICS_SECRET))


def events(day: dt.date) -> list[str] | None:
    """Today's events as "09:30 Dentist" / "All day: Ana's birthday", or None when no calendar is set up or it
    can't be read."""
    url = secret(ICS_SECRET)
    if not url:
        return None
    try:
        import icalendar
        import recurring_ical_events
        cal = icalendar.Calendar.from_ical(requests.get(url, timeout=15).content)
        tz = dt.datetime.now().astimezone().tzinfo
        start = dt.datetime.combine(day, dt.time.min, tz)
        found = []
        for e in recurring_ical_events.of(cal).between(start, start + dt.timedelta(days=1)):
            when = e.get("DTSTART").dt
            title = str(e.get("SUMMARY") or "(no title)").strip()
            if isinstance(when, dt.datetime):
                when = when.astimezone(tz) if when.tzinfo else when.replace(tzinfo=tz)
                found.append((1, when.strftime("%H:%M"), f"{when:%H:%M} {title}"))
            else:
                found.append((0, "", f"All day: {title}"))
        return [text for _, _, text in sorted(found)]
    except Exception:
        log.info("Couldn't read the calendar.", exc_info=True)
        return None


# ---------------------------------------------------------------- reminders, email, PC

def reminders(day: dt.date) -> list[str]:
    from . import proactive, watchers
    out = []
    for w in watchers.items() + proactive.items():
        if w.get("when"):
            t = dt.datetime.fromtimestamp(w["when"] / 1000)
            if t.date() == day:
                out.append((t, f"{t:%H:%M} {w['label']}"))
    return [text for _, text in sorted(out)]


def email_line() -> str | None:
    if not settings.get("email_address"):
        return None
    try:
        from . import mail
        mails = mail.check(unread_only=True, limit=50)
    except Exception:
        log.info("Couldn't check email for the briefing.")
        return None
    if not mails:
        return "No unread email." + ("" if settings.get("humour", "light") == "off" else " Inbox zero, look at you.")
    import email.utils
    important = [m for m in mails if m["important"]]
    count = f"{len(mails)}+" if len(mails) >= 50 else str(len(mails))
    line = f"{count} unread"
    if important:
        def who(m):
            name, addr = email.utils.parseaddr(m["from"])
            return name or addr or "someone"
        line += f", {len(important)} worth a look: " + "; ".join(
            f"{who(m)}: {(m['subject'] or '').strip()[:50] or 'no subject'}" for m in important[:3])
    return line + "."


def pc_line() -> str:
    from . import health
    bits = list(health.problems())
    try:
        free = shutil.disk_usage("C:\\").free / 1024**3
        bits.append(f"C: has {free:.0f} GB free" + (". Time for /cleandisk." if free < 10 else "."))
    except OSError:
        pass
    return " ".join(bits) or "All good."


# ---------------------------------------------------------------- the message

def compose(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now()
    day = now.date()
    greeting = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening"
    lines = [f"☀️ {greeting}! It's {now:%A}, {now.day} {now:%B}."]
    w = weather()
    if w:
        lines.append(f"🌤 {w}")
    cal = events(day)
    if cal is not None:
        lines.append("📅 " + ("; ".join(cal) if cal else "Nothing on your calendar today."))
    rem = reminders(day)
    if rem:
        lines.append("⏰ " + "; ".join(rem))
    mail_line = email_line()
    if mail_line:
        lines.append(f"📬 {mail_line}")
    lines.append(f"💻 {pc_line()}")
    end = quips.signoff(now.weekday())
    if end:
        lines.append(end)
    return "\n".join(lines)


def spoken(text: str) -> str:
    """The briefing for reading aloud: no emoji, one sentence per line."""
    text = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]", "", text)
    return " ".join(line.strip() for line in text.splitlines() if line.strip())
