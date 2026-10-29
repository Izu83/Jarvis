"""A photo sent from the phone (with or without words) must reach Claude: the local parser and the local models
can't see pictures."""
from jarvis import hermes, nlu

NOTE = ("\n\n[The user attached these files from their phone (saved on this PC): D:\From Phone\IMG_1.jpeg. "
        "Open them with the Read tool (it shows images) if they matter for the request.]")


def test_photo_without_words_is_not_handled_locally(ran):
    assert nlu.try_handle("(No message, just this attachment: look at it and respond.)" + NOTE) is None
    assert ran == []


def test_photos_always_go_to_claude():
    assert hermes._needs_claude("what is this" + NOTE)
    assert not hermes._needs_claude("what is this")
