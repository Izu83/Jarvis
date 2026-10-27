"""The free local parser must handle simple commands itself and hand everything else to Claude untouched."""
import pytest

from jarvis import approval, nlu


@pytest.fixture(autouse=True)
def main_tools(monkeypatch):
    calls = []
    monkeypatch.setattr(approval, "run_tool", lambda name, args: calls.append(name) or f"ran {name}")
    return calls


@pytest.mark.parametrize("text, tool", [
    ("next song", "media_key"),
    ("hey jarvis, turn the volume up", "media_key"),
    ("lock my pc", "lock_pc"),
    ("how much disk space is left", "disk_usage"),
    ("clean up", "cleanup_suggestions"),
    ("free up space", "cleanup_suggestions"),
    ("restart the pc", "power_action"),
    ("turn off my computer", "power_action"),
    ("remind me at 10:53 to call mom and dad", "remind_me"),
    ("notify me when the download is done", "notify_when"),
])
def test_handled_locally(ran, text, tool):
    assert nlu.try_handle(text) is not None
    assert ran and ran[0][0] == tool


@pytest.mark.parametrize("text", [
    "show me a picture of the Eiffel tower",
    "send me the photo I saved on my desktop",
    "take a screenshot and tell me what's on it",
    "how much space does my Steam folder take",
    "how much space does my Steam folder take, then delete the oldest game",
    "clean up my desktop by moving the screenshots into a folder",
    "turn the volume up and skip this song",
    "turn off",
    "what is the weather tomorrow",
])
def test_left_for_claude(ran, main_tools, text):
    assert nlu.try_handle(text) is None
    assert not ran and not main_tools


@pytest.mark.parametrize("text, tool", [
    ("send me a screenshot", "screens_photo"),
    ("take a screenshot", "screens_photo"),
    ("take a selfie", "camera_photo"),
    ("send me a photo from the webcam", "camera_photo"),
    ("camera", "camera_photo"),
])
def test_phone_pictures(main_tools, text, tool):
    assert nlu.try_handle(text) is not None
    assert main_tools == [tool]


def test_stop_never_starts_paused_music(ran, monkeypatch):
    monkeypatch.setattr(nlu, "_is_playing", lambda: False)
    assert nlu.try_handle("stop the music") == "Nothing is playing."
    assert not ran
    assert nlu.try_handle("resume") == "Resumed."
    assert ran == [("media_key", {"key": "play_pause"})]


def test_pause_when_unknown_still_toggles(ran):
    assert nlu.try_handle("pause") == "Paused."
    assert ran == [("media_key", {"key": "play_pause"})]


def test_reminder_parses_minutes(ran):
    nlu.try_handle("remind me in 20 minutes to check the oven")
    name, args = ran[0]
    assert name == "remind_me" and args["in_minutes"] == 20 and args["message"] == "Check the oven"


@pytest.mark.parametrize("text", ["send it again", "okay send me the video", "send it", "resend that please",
                                  "can you send me the file again"])
def test_send_it_again_resends_the_last_file(ran, monkeypatch, tmp_path, text):
    from jarvis import outbox
    clip = tmp_path / "site.webm"
    clip.write_bytes(b"v")
    monkeypatch.setattr(outbox, "last_sent", lambda: str(clip))
    nlu.try_handle(text)
    assert ran == [("send_file_to_phone", {"path": str(clip)})]


def test_send_it_with_nothing_sent_goes_to_the_brain(ran, monkeypatch):
    from jarvis import outbox
    monkeypatch.setattr(outbox, "last_sent", lambda: None)
    assert nlu.try_handle("send me the video") is None and ran == []
