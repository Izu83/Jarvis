"""The evening recap, app time, Jarvis's voice choice, the clock's weather and the live words in Voice mode."""
import datetime as dt
import time

from jarvis import apptime, briefing, proactive, recap, remote, speak, voice


def test_bulgarian_follows_the_voice(monkeypatch):
    monkeypatch.setitem(speak.settings, "edge_voice", "en-GB-RyanNeural")
    assert speak.voice_for("Hello there") == "en-GB-RyanNeural"
    assert speak.voice_for("Здравей, как си?") == "bg-BG-BorislavNeural"            # a man's voice in Bulgarian too
    assert speak.voice_for("Здравей", "en-GB-SoniaNeural") == "bg-BG-KalinaNeural"   # a woman's sample: Kalina
    assert speak.voice_for("Hi", "en-US-AriaNeural") == "en-US-AriaNeural"


def test_app_names():
    assert apptime.name_of("Code.exe") == "VS Code"
    assert apptime.name_of("chrome.exe") == "Chrome"
    assert apptime.name_of("LockApp.exe") is None          # Windows' own bits don't count
    assert apptime.name_of("obsidian.exe") == "Obsidian"
    assert apptime.duration(130) == "2 h 10 min" and apptime.duration(60) == "1 h" and apptime.duration(7) == "7 min"


def test_recap(monkeypatch):
    day = dt.date(2026, 10, 30)   # a Friday
    start = dt.datetime(2026, 10, 30, 9).timestamp() * 1000
    monkeypatch.setattr(recap.activity, "summary", lambda limit: [
        {"kind": "command", "status": "done", "started": start + 3, "text": "open spotify"},
        {"kind": "command", "status": "done", "started": start + 2, "text": "Open Spotify"},
        {"kind": "command", "status": "error", "started": start + 1, "text": "broken thing"},
        {"kind": "command", "status": "done", "started": start, "text": "send me the PDF I downloaded yesterday please"},
        {"kind": "command", "status": "done", "started": start - 86400000, "text": "yesterday's"}])
    monkeypatch.setattr(recap.apptime, "today", lambda d: [("VS Code", 130), ("Chrome", 75), ("Discord", 3)])
    monkeypatch.setattr(recap.briefing, "events", lambda d: ["09:30 Dentist"])
    monkeypatch.setattr(recap.briefing, "reminders", lambda d: [])
    monkeypatch.setattr(recap.briefing, "forecast", lambda n: "🌧️ Tomorrow in Botevgrad: 10° / 8°, light rain.")
    text = recap.compose(dt.datetime(2026, 10, 30, 21, 30))
    assert "Friday, 30 October" in text
    assert "I handled 3 things" in text and "“send me the PDF I downloaded…”" in text
    assert "yesterday's" not in text and "broken" not in text
    assert "VS Code 2 h 10 min and Chrome 1 h 15 min (3 h 28 min in all)" in text
    assert "📅 Tomorrow: 09:30 Dentist." in text and "Botevgrad" in text
    assert text.endswith("Tomorrow's the weekend. Sleep in.")


def test_quiet_day(monkeypatch):
    monkeypatch.setattr(recap.activity, "summary", lambda limit: [])
    monkeypatch.setattr(recap.apptime, "today", lambda d: [])
    monkeypatch.setattr(recap.briefing, "events", lambda d: None)
    monkeypatch.setattr(recap.briefing, "reminders", lambda d: [])
    monkeypatch.setattr(recap.briefing, "forecast", lambda n: None)
    text = recap.compose(dt.datetime(2026, 11, 2, 22))
    assert "didn't need me" in text and "On your PC" not in text and text.endswith("Good night.")


def test_recap_time(monkeypatch):
    monkeypatch.setitem(proactive.settings, "recap", True)
    monkeypatch.setitem(proactive.settings, "recap_time", "21:30")
    monkeypatch.setattr(proactive, "_state", {})
    assert not proactive._evening_due(dt.datetime(2026, 10, 30, 21, 0))
    assert proactive._evening_due(dt.datetime(2026, 10, 30, 21, 31))
    assert not proactive._evening_due(dt.datetime(2026, 10, 31, 2, 0))   # too late: skipped, not sent at 2 a.m.
    monkeypatch.setitem(proactive.settings, "recap", False)
    assert not proactive._evening_due(dt.datetime(2026, 10, 30, 21, 31))


def test_weather_icons():
    assert briefing.sky_icon(0) == "☀️" and briefing.sky_icon(0, day=False) == "🌙"
    assert briefing.sky_icon(63) == "🌧️" and briefing.sky_icon(73) == "🌨️" and briefing.sky_icon(95) == "⛈️"


def test_live_words(monkeypatch):
    calls, sent = [], []
    monkeypatch.setattr(voice, "quick_phone", lambda pcm: calls.append(len(pcm)) or "open spo")
    monkeypatch.setattr(remote.threading, "Thread", lambda target, **kw: type("T", (), {"start": lambda self: target()})())
    phone = remote.PhoneChannel.__new__(remote.PhoneChannel)
    phone._parts, phone._publish = {}, sent.append
    phone.words_so_far("r1", remote.b64e(b"\x01\x00" * 8000))
    phone.words_so_far("r1", remote.b64e(b"\x01\x00" * 8000))
    assert calls == [16000, 32000]                          # each pass hears everything said so far
    assert sent[-1] == {"kind": "stt_partial", "id": "stt", "req": "r1", "text": "open spo"}
    monkeypatch.setattr(voice, "transcribe_phone", lambda pcm, keep: ("open spotify", "en"))
    phone.send_words("r1", remote.b64e(b"\x01\x00" * 8000))
    assert "r1" not in phone._parts                          # done: no more live words for it


def test_old_live_words_are_dropped(monkeypatch):
    monkeypatch.setattr(voice, "quick_phone", lambda pcm: "")
    monkeypatch.setattr(remote.threading, "Thread", lambda target, **kw: type("T", (), {"start": lambda self: target()})())
    phone = remote.PhoneChannel.__new__(remote.PhoneChannel)
    phone._parts, phone._publish = {"old": {"pcm": bytearray(), "busy": False, "ts": time.time() - 120}}, lambda d: None
    phone.words_so_far("new", remote.b64e(b"\x00\x00" * 100))
    assert list(phone._parts) == ["new"]
