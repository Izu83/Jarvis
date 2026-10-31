"""The clock on the phone: where the PC is and its time zone, without ever waiting on the internet."""
import datetime as dt

from jarvis import briefing


def test_where_comes_from_the_cache(monkeypatch):
    monkeypatch.setitem(briefing.settings, "weather_city", "")
    monkeypatch.setattr(briefing, "_cache", lambda: {"place_for": "", "place": {
        "name": "Botevgrad", "country": "Bulgaria", "tz": "Europe/Sofia", "lat": 42.9, "lon": 23.8}})
    monkeypatch.setattr(briefing, "place", lambda: (_ for _ in ()).throw(AssertionError("looked it up")))
    w = briefing.where_now()
    assert (w["name"], w["country"], w["tz"]) == ("Botevgrad", "Bulgaria", "Europe/Sofia")
    assert isinstance(w["offset"], int)


def test_unknown_place_is_looked_up_in_the_background(monkeypatch):
    looked = []
    monkeypatch.setitem(briefing.settings, "weather_city", "")
    monkeypatch.setattr(briefing, "_cache", lambda: {})
    monkeypatch.setattr(briefing, "_looked", 0.0)
    monkeypatch.setattr(briefing, "place", lambda: looked.append(1))
    started = []
    monkeypatch.setattr(briefing.threading, "Thread", lambda target, **kw: started.append(target) or
                        type("T", (), {"start": lambda self: target()})())
    w = briefing.where_now()
    assert w["name"] == "" and looked == [1]
    briefing.where_now()                     # not again straight away
    assert looked == [1]


def test_several_calendars(monkeypatch):
    """iPhone (iCloud) share links and Google secret addresses, as many as you like; events merged in time order."""
    import os
    monkeypatch.setitem(os.environ, briefing.ICS_SECRET, "")
    assert briefing.add_calendar("webcal://p52-caldav.icloud.com/published/2/home")
    assert briefing.add_calendar("https://calendar.google.com/calendar/ical/x/basic.ics")
    assert briefing.add_calendar("webcal://p52-caldav.icloud.com/published/2/home")   # twice is still once
    assert not briefing.add_calendar("not a link")
    assert briefing.calendars() == ["https://p52-caldav.icloud.com/published/2/home",
                                    "https://calendar.google.com/calendar/ical/x/basic.ics"]
    ics = {
        "home": "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:1\nDTSTART:20261102T093000\nSUMMARY:Dentist\nEND:VEVENT\n"
                "BEGIN:VEVENT\nUID:2\nDTSTART;VALUE=DATE:20261102\nSUMMARY:Ana's birthday\nEND:VEVENT\nEND:VCALENDAR\n",
        "basic.ics": "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:3\nDTSTART:20261102T080000\nSUMMARY:Gym\nEND:VEVENT\n"
                     "BEGIN:VEVENT\nUID:4\nDTSTART:20261102T093000\nSUMMARY:Dentist\nEND:VEVENT\nEND:VCALENDAR\n"}
    fake = lambda url, timeout=None: type("R", (), {"content": ics[url.rsplit("/", 1)[1]].encode()})()
    monkeypatch.setattr(briefing.requests, "get", fake)
    assert briefing.events(dt.date(2026, 11, 2)) == ["All day: Ana's birthday", "08:00 Gym", "09:30 Dentist"]
    briefing.remove_calendars()
    assert briefing.calendars() == [] and briefing.events(dt.date(2026, 11, 2)) is None
