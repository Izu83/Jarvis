"""Photos you sent open full-size (remote._read_upload), and which AI is working goes to the app as a marker."""
from jarvis import hermes, remote


def test_a_photo_you_sent_comes_back_from_the_from_phone_folder(tmp_path, monkeypatch):
    folder = tmp_path / "From Phone"
    folder.mkdir()
    (folder / "IMG_1.jpg").write_bytes(b"\xff\xd8 picture")
    (folder / "notes.txt").write_text("not a picture")
    (tmp_path / "secret.jpg").write_bytes(b"\xff\xd8 elsewhere")
    monkeypatch.setitem(remote.settings, "upload_folder", str(folder))
    got = remote.PhoneChannel._read_upload(str(folder / "IMG_1.jpg"))
    assert got["ok"] and got["mime"] == "image/jpeg" and got["name"] == "IMG_1.jpg"
    assert not remote.PhoneChannel._read_upload(str(folder / "notes.txt"))["ok"]          # only pictures
    assert not remote.PhoneChannel._read_upload(str(tmp_path / "secret.jpg"))["ok"]       # only that folder
    assert not remote.PhoneChannel._read_upload(str(folder / ".." / "secret.jpg"))["ok"]  # no climbing out of it
    assert not remote.PhoneChannel._read_upload(str(folder / "gone.jpg"))["ok"]


def test_which_ai_is_working_is_a_marker_not_a_step():
    brain = hermes.HermesBrain.__new__(hermes.HermesBrain)
    said = []
    brain.progress = lambda text, kind: said.append((kind, text))
    brain._mark("groq")
    brain._mark("local")
    assert said == [("AI", "groq"), ("AI", "hermes")]
