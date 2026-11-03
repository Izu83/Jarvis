"""The wake word's double-check: what Whisper heard counts as his name, or doesn't."""
from jarvis.voice import NAME, NAME_FIRST, WAKE_PREFIX


def test_his_name_in_any_spelling():
    for said in ["Hey Jarvis.", "Hey, Jervis!", "Javis", "ok jarviz", "Хей Джарвис.", "Джарвис, колко е часът?",
                 "Жарвис", "hey djarvis open chrome"]:
        assert NAME.search(said), said


def test_not_his_name():
    for said in ["Hey Travis, did you see the game?", "the service was slow", "gravy", "Jaws is a good film",
                 "I was talking with my friends", "Мапуисито. Мапуисито.", "Целта е да се наберат средства", ""]:
        assert not NAME.search(said), said


def test_name_alone_only_at_the_start():
    for said in ["Jarvis, open Spotify.", "Hey Jarvis.", "Джарвис, колко е часът?", "Хей Джарвис", "ok, jarvis what time"]:
        assert NAME_FIRST.search(said), said
    for said in ["and then Jarvis said hi", "I told Travis", "Казах на Джарвис", "the service"]:
        assert not NAME_FIRST.search(said), said


def test_the_name_is_taken_off_the_command():
    assert WAKE_PREFIX.sub("", "Джарвис, колко е часът?").strip(" ,.") == "колко е часът?"
    assert WAKE_PREFIX.sub("", "Hey Jarvis, open Chrome").strip(" ,.") == "open Chrome"
