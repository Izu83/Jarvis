"""When Windows' media service stops answering, Jarvis backs off instead of piling up stuck requests (media.py)."""
import asyncio
import threading

import pytest

from jarvis import media


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(media, "_backoff", {"until": 0.0, "wait": 0.0, "busy": False})
    monkeypatch.setattr(media, "TIMEOUT", 0.05)


def test_a_read_that_never_answers_makes_it_wait(monkeypatch):
    calls = []

    async def hangs():
        calls.append(1)
        await asyncio.sleep(10)

    monkeypatch.setattr(media, "_now", hangs)
    assert media.now_playing() is None
    assert media.now_playing() is None   # straight after: not asked again
    assert len(calls) == 1
    assert media._backoff["wait"] == media.FIRST_WAIT


def test_the_wait_grows_and_resets_after_an_answer(monkeypatch):
    async def hangs():
        await asyncio.sleep(10)

    async def answers():
        return {"title": "Song"}

    monkeypatch.setattr(media, "_now", hangs)
    media.now_playing()
    media._backoff["until"] = 0
    media.now_playing()
    assert media._backoff["wait"] == media.FIRST_WAIT * 2
    media._backoff["until"] = 0
    monkeypatch.setattr(media, "_now", answers)
    assert media.now_playing() == {"title": "Song"}
    assert media._backoff["wait"] == 0


def test_only_one_read_at_a_time(monkeypatch):
    started, release = threading.Event(), threading.Event()

    async def slow():
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.005)
        return {"title": "Song"}

    monkeypatch.setattr(media, "TIMEOUT", 5)
    monkeypatch.setattr(media, "_now", slow)
    t = threading.Thread(target=media.now_playing)
    t.start()
    started.wait(2)
    assert media.now_playing() is None   # the first is still going: no second one
    release.set()
    t.join(5)
