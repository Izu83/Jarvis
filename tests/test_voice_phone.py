"""Voice mode on the phone: its recording reaches the PC as url-safe base64 and Whisper's words go back."""
from types import SimpleNamespace

import numpy as np

from jarvis import remote, voice


class FakeWhisper:
    def __init__(self, *texts):
        self.texts = texts

    def transcribe(self, samples, **kw):
        return [SimpleNamespace(text=t, no_speech_prob=0.1) for t in self.texts], None


def _speech(seconds=1.0):
    return (np.sin(np.arange(int(16000 * seconds)) / 9) * 8000).astype(np.int16).tobytes()


def test_words_without_hey_jarvis(monkeypatch):
    monkeypatch.setattr(voice, "_model_for_phone", lambda: FakeWhisper(" Hey Jarvis,", "open Spotify."))
    monkeypatch.setitem(voice.settings, "voice_language", "en")
    assert voice.transcribe_phone(_speech()) == ("open Spotify.", "en")


def test_no_words_from_noise_or_a_tap(monkeypatch):
    monkeypatch.setattr(voice, "_model_for_phone", lambda: FakeWhisper(" Thank you for watching!"))
    monkeypatch.setitem(voice.settings, "voice_language", "en")
    assert voice.transcribe_phone(_speech())[0] == ""          # Whisper's favourite phantom
    assert voice.transcribe_phone(_speech(0.1))[0] == ""       # too short to be anything


def test_phone_recording_round_trip(monkeypatch):
    got, sent = [], []
    monkeypatch.setattr(voice, "transcribe_phone", lambda pcm, keep=False: (got.append(pcm), ("lock my pc", "en"))[1])
    phone = remote.PhoneChannel.__new__(remote.PhoneChannel)
    phone._publish = sent.append
    pcm = _speech(0.5)
    phone.send_words("r1", remote.b64e(pcm))                    # the phone sends it without = padding
    assert got == [pcm]
    assert sent == [{"kind": "stt", "id": "stt", "req": "r1", "text": "lock my pc", "lang": "en"}]


def test_a_broken_recording_says_so(monkeypatch):
    def boom(pcm, keep=False):
        raise RuntimeError("no model")
    monkeypatch.setattr(voice, "transcribe_phone", boom)
    sent = []
    phone = remote.PhoneChannel.__new__(remote.PhoneChannel)
    phone._publish = sent.append
    phone.send_words("r2", "AAAA")
    assert sent == [{"kind": "stt", "id": "stt", "req": "r2", "error": True}]
