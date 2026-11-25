"""Listening on/off (Settings › Voice, the tray) and the quiet-room batching of the wake-word model (voice.py)."""
import threading
import time

import numpy as np
import pytest

from jarvis import voice
from jarvis.config import settings


class FakeWake:
    def __init__(self):
        self.sizes = []

    def predict(self, x):
        self.sizes.append(len(x))
        return {"hey_jarvis": 0.0}

    def reset(self):
        pass


@pytest.fixture
def listener(monkeypatch):
    keys = ("listening", "wake_name_alone", "wake_check", "wake_threshold")
    saved = {k: settings[k] for k in keys if k in settings}
    monkeypatch.setattr(voice, "set_setting", lambda k, v: settings.__setitem__(k, v))
    lst = voice.VoiceListener(lambda text, source: None)
    lst.wake = FakeWake()
    settings.update(wake_name_alone=False, wake_check=False, wake_threshold=0.5)
    yield lst
    voice._listeners.remove(lst)
    for k in keys:
        settings.pop(k, None)
    settings.update(saved)


def _chunk(level):
    return (np.ones(voice.CHUNK) * level).astype(np.int16)


def _run_loop(lst, chunks, seconds=1.0):
    for c in chunks:
        lst.audio.put(c)
    t = threading.Thread(target=lst._wake_loop, daemon=True)
    t.start()
    deadline = time.time() + seconds
    while not lst.audio.empty() and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.1)
    lst._reopen.set()
    t.join(2)
    return t


def test_a_quiet_room_is_scored_four_chunks_at_a_time(listener):
    settings["listening"] = True
    _run_loop(listener, [_chunk(10)] * 8)
    assert listener.wake.sizes == [4 * voice.CHUNK, 4 * voice.CHUNK]


def test_a_loud_chunk_is_scored_at_once_after_what_was_waiting(listener):
    settings["listening"] = True
    _run_loop(listener, [_chunk(10)] * 22 + [_chunk(10)] * 2 + [_chunk(3000)])
    # 22 quiet chunks: 5 batches of 4 and 2 waiting; then the 2 waiting ones, then the loud one by itself
    assert listener.wake.sizes[-1] == voice.CHUNK
    assert sum(listener.wake.sizes) == 25 * voice.CHUNK


def test_turned_off_the_loop_ends_and_nothing_is_scored(listener):
    settings["listening"] = False
    t = _run_loop(listener, [_chunk(3000)] * 4, seconds=0.3)
    assert not t.is_alive()
    assert listener.wake.sizes == []


def test_turning_it_off_and_on(listener):
    listener.set_listening(False)
    assert settings["listening"] is False and listener._reopen.is_set() and listener._wakeup.is_set()
    voice.set_listening(True)
    assert settings["listening"] is True


def test_off_for_a_while_puts_whisper_away(listener, monkeypatch):
    settings["listening"] = False
    listener.whisper = object()
    listener.used = time.time() - voice.UNLOAD_AFTER - 1
    listener._wakeup.set()
    t = threading.Thread(target=listener._rest, daemon=True)
    t.start()
    for _ in range(50):
        if listener.whisper is None:
            break
        time.sleep(0.05)
    assert listener.whisper is None
    settings["listening"] = True
    listener._wakeup.set()
    t.join(35)


def test_the_shortcut_still_works_while_off(listener):
    settings["listening"] = False
    listener.trigger()
    assert listener._manual.is_set() and listener._wakeup.is_set()
