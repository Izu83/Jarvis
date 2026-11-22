"""When Windows' media service stops answering, Jarvis backs off and kills the stuck helper process instead of piling
up stuck threads in itself (media.py, media_helper.py)."""
import sys
import threading
import time

import pytest

from jarvis import media


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(media, "_backoff", {"until": 0.0, "wait": 0.0, "busy": False})
    monkeypatch.setattr(media, "_helper", {"proc": None, "lines": None})
    yield
    media._stop_helper()


def test_a_read_that_never_answers_makes_it_wait(monkeypatch):
    calls = []

    def hangs():
        calls.append(1)
        raise TimeoutError

    monkeypatch.setattr(media, "_ask", hangs)
    assert media.now_playing() is None
    assert media.now_playing() is None   # straight after: not asked again
    assert len(calls) == 1
    assert media._backoff["wait"] == media.FIRST_WAIT


def test_the_wait_grows_and_resets_after_an_answer(monkeypatch):
    def hangs():
        raise TimeoutError

    monkeypatch.setattr(media, "_ask", hangs)
    media.now_playing()
    media._backoff["until"] = 0
    media.now_playing()
    assert media._backoff["wait"] == media.FIRST_WAIT * 2
    media._backoff["until"] = 0
    monkeypatch.setattr(media, "_ask", lambda: {"title": "Song"})
    assert media.now_playing() == {"title": "Song"}
    assert media._backoff["wait"] == 0


def test_only_one_read_at_a_time(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return {"title": "Song"}

    monkeypatch.setattr(media, "_ask", slow)
    t = threading.Thread(target=media.now_playing)
    t.start()
    started.wait(2)
    assert media.now_playing() is None   # the first is still going: no second one
    release.set()
    t.join(5)


ANSWERS = """
import json, sys
for line in sys.stdin:
    print(json.dumps({"info": {"title": "Song", "art": "abc"}, "art": "aGk="}), flush=True)
"""
HANGS = """
import sys, time
sys.stdin.readline()
time.sleep(60)
"""


def _fake(monkeypatch, tmp_path, code):
    script = tmp_path / "fake_helper.py"
    script.write_text(code)
    monkeypatch.setattr(media, "HELPER", [str(script)])


def test_reads_from_the_helper_process_and_keeps_the_art(monkeypatch, tmp_path):
    _fake(monkeypatch, tmp_path, ANSWERS)
    info = media._ask()
    assert info == {"title": "Song", "art": "abc"}
    assert media.art("abc") == b"hi"
    first = media._helper["proc"]
    assert media._ask()["title"] == "Song"
    assert media._helper["proc"] is first   # the same helper keeps answering


def test_a_helper_that_hangs_is_killed(monkeypatch, tmp_path):
    _fake(monkeypatch, tmp_path, HANGS)
    monkeypatch.setattr(media, "START_TIMEOUT", 1)
    proc = None

    def remember(*a, **k):
        nonlocal proc
        proc = real(*a, **k)
        return proc

    import subprocess
    real = subprocess.Popen
    monkeypatch.setattr(subprocess, "Popen", remember)
    with pytest.raises(TimeoutError):
        media._ask()
    assert media._helper["proc"] is None
    for _ in range(50):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    assert proc.poll() is not None


def test_a_helper_whose_threads_pile_up_is_replaced(monkeypatch, tmp_path):
    _fake(monkeypatch, tmp_path, ANSWERS)
    media._ask()
    first = media._helper["proc"]
    monkeypatch.setattr(media, "_helper_threads", lambda proc: 5000)
    media._ask()
    assert media._helper["proc"] is not first
    assert first.poll() is not None


REFUSES = """
import json, sys
for line in sys.stdin:
    print(json.dumps({"info": None, "art": None, "error": "OSError: Call was canceled"}), flush=True)
"""


def test_a_refusal_from_windows_backs_off_too(monkeypatch, tmp_path):
    _fake(monkeypatch, tmp_path, REFUSES)
    assert media.now_playing() is None
    assert media._backoff["wait"] == media.FIRST_WAIT
