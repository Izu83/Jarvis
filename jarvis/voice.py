"""Microphone: wait for "Hey Jarvis", record until you stop talking, transcribe with Whisper."""
import collections
import logging
import queue
import re
import threading
import winsound

import numpy as np
import sounddevice as sd

from .config import settings

log = logging.getLogger("jarvis.voice")

RATE = 16000
CHUNK = 1280  # 80 ms, the frame size openWakeWord expects
PRE_ROLL_CHUNKS = 20  # keep 1.6 s before the wake word so words right after it aren't lost
SILENCE_TO_STOP = 0.9  # seconds of quiet that end a command
MAX_COMMAND = 15.0
NO_SPEECH_TIMEOUT = 5.0

# Vocabulary hint so Whisper spells app names right
WHISPER_PROMPT = "Hey Jarvis, open Spotify, Claude, File Explorer, VS Code, Discord, Chrome. Put them on monitor 3."
WAKE_PREFIX = re.compile(r"^\W*(hey|hi|ok|okay)?\W*(jarvis|jervis|javis)\W*", re.IGNORECASE)


def _beep(freq: int) -> None:
    threading.Thread(target=winsound.Beep, args=(freq, 120), daemon=True).start()


class VoiceListener:
    def __init__(self, on_command):
        self.on_command = on_command
        self.audio: queue.Queue[np.ndarray] = queue.Queue()

    def _load_models(self):
        from faster_whisper import WhisperModel
        from openwakeword.model import Model
        from openwakeword.utils import download_models

        name = settings["wake_word_model"]
        log.info("Loading wake word model '%s'...", name)
        download_models(model_names=[name])
        self.wake = Model(wakeword_models=[name], inference_framework="onnx")
        log.info("Loading Whisper '%s' (first run downloads it)...", settings["whisper_model"])
        self.whisper = WhisperModel(settings["whisper_model"], device="cpu", compute_type="int8")

    def _callback(self, indata, _frames, _time, status):
        if status:
            log.debug("audio status: %s", status)
        self.audio.put(indata[:, 0].copy())

    def run(self) -> None:
        self._load_models()
        threshold = settings["wake_threshold"]
        recent = collections.deque(maxlen=PRE_ROLL_CHUNKS)
        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK, callback=self._callback):
            log.info("Listening for the wake word. Say \"Hey Jarvis, ...\"")
            while True:
                chunk = self.audio.get()
                recent.append(chunk)
                scores = self.wake.predict(chunk)
                if max(scores.values()) < threshold:
                    continue
                _beep(880)
                noise = float(np.percentile([_rms(c) for c in recent], 20))
                audio = self._record(list(recent), noise)
                self.wake.reset()
                recent.clear()
                _drain(self.audio)
                text = self._transcribe(audio)
                if text:
                    _beep(660)
                    self.on_command(text)
                else:
                    log.info("Heard the wake word but no command.")

    def _record(self, pre_roll: list[np.ndarray], noise: float) -> np.ndarray:
        """Collect audio until the speaker has been quiet for SILENCE_TO_STOP seconds."""
        speech_level = max(noise * 2.5, 400.0)
        chunks = list(pre_roll)
        heard_speech, quiet, total = False, 0.0, 0.0
        step = CHUNK / RATE
        while total < MAX_COMMAND:
            chunk = self.audio.get()
            chunks.append(chunk)
            total += step
            if _rms(chunk) > speech_level:
                heard_speech, quiet = True, 0.0
            else:
                quiet += step
            if heard_speech and quiet >= SILENCE_TO_STOP:
                break
            if not heard_speech and total >= NO_SPEECH_TIMEOUT:
                break
        return np.concatenate(chunks)

    def _transcribe(self, audio: np.ndarray) -> str:
        samples = audio.astype(np.float32) / 32768.0
        segments, _ = self.whisper.transcribe(samples, language="en", beam_size=1,
                                              vad_filter=True, initial_prompt=WHISPER_PROMPT)
        # Whisper hallucinates words like "You" on silence; drop segments it thinks aren't speech
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.6).strip()
        log.info("Heard: %r", text)
        return WAKE_PREFIX.sub("", text).strip(" ,.")


def _rms(chunk: np.ndarray) -> float:
    return float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))


def _drain(q: queue.Queue) -> None:
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass
