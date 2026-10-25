"""Spoken replies.

Prefers Microsoft's free neural voices via edge-tts (natural, a female voice, needs internet, no API key). If
that's unavailable it falls back to the built-in offline Windows voice (pyttsx3 / SAPI). Everything runs on one
worker thread; COM is initialised there so the SAPI fallback can't crash the thread.
"""
import asyncio
import ctypes
import logging
import os
import queue
import tempfile
import threading

from .config import settings

log = logging.getLogger("jarvis.speak")
_queue: "queue.Queue[str]" = queue.Queue()
_mci = ctypes.windll.winmm.mciSendStringW


def _play_mp3(path: str) -> None:
    """Play an MP3 synchronously via Windows' built-in MCI (no extra dependency)."""
    _mci("close jarvisvoice", None, 0, None)
    if _mci(f'open "{path}" type mpegvideo alias jarvisvoice', None, 0, None) == 0:
        _mci("play jarvisvoice wait", None, 0, None)
        _mci("close jarvisvoice", None, 0, None)
    else:
        raise OSError("MCI couldn't open the audio")


def _edge_say(text: str, out: str) -> bool:
    """Synthesise with edge-tts to an MP3 file. Returns False if it's unavailable (e.g. no internet)."""
    import edge_tts

    async def go():
        await edge_tts.Communicate(text, settings["edge_voice"]).save(out)

    try:
        asyncio.run(go())
        return os.path.getsize(out) > 0
    except Exception as e:
        log.info("edge-tts unavailable (%s); using the built-in voice.", e)
        return False


def synth(text: str) -> bytes | None:
    """The same neural voice as MP3 bytes, for the phone to play (its own browser voices sound robotic)."""
    fd, out = tempfile.mkstemp(suffix=".mp3", prefix="jarvis_phone_")
    os.close(fd)
    try:
        return open(out, "rb").read() if _edge_say(text, out) else None
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


class _Sapi:
    """The offline fallback: a pyttsx3/SAPI engine, created lazily on the worker thread (which is COM-initialised)."""
    def __init__(self):
        self.engine = None

    def say(self, text: str) -> None:
        if self.engine is None:
            import comtypes
            try:
                comtypes.CoInitialize()   # SAPI needs a COM apartment on this thread
            except Exception:
                pass
            import pyttsx3
            self.engine = pyttsx3.init()
            wanted = settings["tts_voice"].lower()
            if wanted:
                for v in self.engine.getProperty("voices"):
                    if wanted in v.name.lower():
                        self.engine.setProperty("voice", v.id)
                        break
            self.engine.setProperty("rate", 175)
        self.engine.say(text)
        self.engine.runAndWait()


def _worker() -> None:
    sapi = _Sapi()   # the edge-tts path needs no COM; the SAPI fallback initialises it lazily
    tmp = os.path.join(tempfile.gettempdir(), "jarvis_voice.mp3")
    while True:
        text = _queue.get()
        if not text:
            continue
        spoke = False
        if settings["tts_engine"] == "edge" and _edge_say(text, tmp):
            try:
                _play_mp3(tmp)
                spoke = True
            except Exception:
                log.debug("mp3 playback failed; falling back", exc_info=True)
        if not spoke:
            try:
                sapi.say(text)
            except Exception:
                log.exception("TTS failed")


def start() -> None:
    if settings["speak_replies"]:
        threading.Thread(target=_worker, daemon=True, name="tts").start()


def say(text: str) -> None:
    if settings["speak_replies"] and text:
        _queue.put(text)
