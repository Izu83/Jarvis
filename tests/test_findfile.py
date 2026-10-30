"""Find my file on a fake home folder: what counts as a request, which files match, and picking from a list."""
import datetime as dt
import os
import time

import pytest

from jarvis import findfile, nlu


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(findfile, "HOME", tmp_path)
    findfile._pending.clear()
    for name in findfile.FOLDERS:
        (tmp_path / name).mkdir()
    return tmp_path


def _file(path, days_ago=0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 100)
    t = time.time() - days_ago * 86400
    os.utime(path, (t, t))
    return path


def test_what_counts_as_a_file_request():
    assert findfile.parse("send me a joke") is None
    assert findfile.parse("get me the weather") is None
    spec = findfile.parse("send me the PDF I downloaded yesterday")
    assert spec["exts"] == {".pdf"} and spec["folder"] == "Downloads" and spec["window"][2] == "yesterday"
    assert not findfile.parse("find the photo from last week")["newest"]
    assert findfile.parse("send me the latest screenshot")["newest"]
    assert findfile.parse("send me the invoice pdf")["words"] == ["invoice"]


def test_one_match_is_sent_straight_away(home, ran, monkeypatch):
    monkeypatch.setattr(findfile, "_when", lambda st: st.st_mtime)   # creation time is "now" for test files
    now = dt.datetime.now()
    yesterday_noon = (dt.datetime.combine(now.date(), dt.time(12)) - dt.timedelta(days=1)).timestamp()
    pdf = _file(home / "Downloads" / "bill.pdf")
    os.utime(pdf, (yesterday_noon, yesterday_noon))
    _file(home / "Downloads" / "older.pdf", days_ago=5)
    _file(home / "Downloads" / "photo.jpg", days_ago=1)
    nlu.try_handle("send me the PDF I downloaded yesterday")
    assert ran == [("send_file_to_phone", {"path": str(pdf)})]


def test_several_are_listed_then_picked(home, ran, monkeypatch):
    monkeypatch.setattr(findfile, "_when", lambda st: st.st_mtime)
    _file(home / "Pictures" / "a.png", days_ago=0.3)
    second = _file(home / "Desktop" / "b.jpg", days_ago=0.6)
    _file(home / "Documents" / "notes.txt", days_ago=0.1)
    answer = nlu.try_handle("send me the photo from this week")
    assert answer.startswith("I found 2 pictures") and "1. a.png" in answer and "2. b.jpg" in answer
    assert not ran
    nlu.try_handle("the second one")
    assert ran == [("send_file_to_phone", {"path": str(second)})]
    ran.clear()
    assert nlu.try_handle("the second one") != "ran send_file_to_phone"   # the list is used up


def test_nothing_found_says_so(home):
    answer = nlu.try_handle("send me the video from yesterday")
    assert answer.startswith("I couldn't find a video from yesterday")
