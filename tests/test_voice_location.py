"""Stop / wait while Jarvis talks, and where you are."""
import pytest

from jarvis import location, router, voice


@pytest.mark.parametrize("said, kind", [
    ("Стоп.", "stop"), ("стоп", "stop"), ("Спри!", "stop"), ("Stop.", "stop"), ("Jarvis, stop", "stop"), ("Стига", "stop"),
    ("Чакай.", "wait"), ("Wait", "wait"), ("Hold on", "wait"), ("Момент", "wait"), ("Chakai", "wait"),
    ("Jarvis, open Chrome", "wait"),
    ("The weather today is sunny", None), ("Стопанството расте", None),
])
def test_cut_in_words(said, kind):
    got = "stop" if voice.STOP_WORDS.match(said) else "wait" if voice.WAIT_WORDS.match(said) else None
    assert got == kind


def test_location_note_from_the_phone(monkeypatch):
    monkeypatch.setattr(location, "address", lambda lat, lon: "Vasil Levski 12, Botevgrad, Bulgaria")
    location.update({"lat": 42.90451, "lon": 23.79312, "acc": 12}, "phone", "m1")
    note = location.note("phone", "m1")
    assert "their phone's location, within about 12 m" in note and "Vasil Levski 12, Botevgrad" in note
    assert "42.90451, 23.79312" in note
    location.update({"lat": "nonsense"}, "phone", "m2")   # a bad position is ignored, the last good one stays
    assert "Botevgrad" in location.note("phone", "m2")


def test_location_note_is_not_routed_on():
    note = "\n\n[Where the user is right now (their phone's location): Botevgrad — 42.9, 23.8]"
    assert router.kind("tell me a joke" + note) == "chat"   # "right now" in the note isn't the question
    assert router.kind("what's this\n\n[The user attached these files from their phone: a.jpg]") == "sight"
