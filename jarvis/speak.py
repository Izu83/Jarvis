"""Spoken replies through the built-in Windows voices (offline)."""
import logging
import queue
import threading

from .config import settings

log = logging.getLogger("jarvis.speak")
_queue: queue.Queue[str] = queue.Queue()


def _worker() -> None:
    import pyttsx3  # pyttsx3 must live on one thread, so it's created here

    engine = pyttsx3.init()
    wanted = settings["tts_voice"].lower()
    if wanted:
        for v in engine.getProperty("voices"):
            if wanted in v.name.lower():
                engine.setProperty("voice", v.id)
                break
    engine.setProperty("rate", 185)
    while True:
        text = _queue.get()
        try:
            engine.say(text)
            engine.runAndWait()
        except Exception:
            log.exception("TTS failed")


def start() -> None:
    if settings["speak_replies"]:
        threading.Thread(target=_worker, daemon=True, name="tts").start()


def say(text: str) -> None:
    if settings["speak_replies"]:
        _queue.put(text)
