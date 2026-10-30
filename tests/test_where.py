"""The clock on the phone: where the PC is and its time zone, without ever waiting on the internet."""
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
