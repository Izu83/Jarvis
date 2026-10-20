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
PRE_ROLL_CHUNKS = 22  # keep ~1.8 s before the wake word so words right after it aren't lost
SILENCE_TO_STOP = 1.2  # seconds of quiet that end a command (longer = less chance of cutting you off mid-sentence)
MAX_COMMAND = 20.0
NO_SPEECH_TIMEOUT = 6.0

# Vocabulary hint so Whisper spells app names right
WHISPER_PROMPT = "Hey Jarvis, open Spotify, Claude, File Explorer, VS Code, Discord, Chrome. Put them on monitor 3."
WAKE_PREFIX = re.compile(r"^\W*(hey|hi|ok|okay)?\W*(jarvis|jervis|javis)\W*", re.IGNORECASE)


def _beep(freq: int) -> None:
    threading.Thread(target=winsound.Beep, args=(freq, 120), daemon=True).start()


class VoiceListener:
    def __init__(self, on_command, on_wake=None, on_partial=None, on_idle=None):
        self.on_command = on_command
        self.on_wake = on_wake        # wake word heard -> show the pop-up
        self.on_partial = on_partial  # live transcription as you speak
        self.on_idle = on_idle        # heard the wake word but no command -> hide the pop-up
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
                if self.on_wake:
                    try: self.on_wake()
                    except Exception: log.debug("on_wake failed", exc_info=True)
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
                    if self.on_idle:
                        try: self.on_idle()
                        except Exception: pass

    def _record(self, pre_roll: list[np.ndarray], noise: float) -> np.ndarray:
        """Collect audio until the speaker has been quiet for SILENCE_TO_STOP seconds, emitting live partial
        transcriptions (word-by-word feel) to on_partial while they talk."""
        speech_level = max(noise * 2.2, 280.0)   # a bit lower, so softer speech still registers
        chunks = list(pre_roll)
        heard_speech, quiet, total = False, 0.0, 0.0
        step = CHUNK / RATE
        last_partial, busy = 0.0, [False]

        def partial(buf):
            try:
                txt = self._quick_transcribe(buf)
                if txt and self.on_partial:
                    self.on_partial(txt)
            except Exception:
                log.debug("partial transcribe failed", exc_info=True)
            finally:
                busy[0] = False

        while total < MAX_COMMAND:
            chunk = self.audio.get()
            chunks.append(chunk)
            total += step
            if _rms(chunk) > speech_level:
                heard_speech, quiet = True, 0.0
            else:
                quiet += step
            # every ~0.7 s of speech, transcribe what we have so far and show it live (off-thread, non-overlapping)
            if self.on_partial and heard_speech and not busy[0] and total - last_partial >= 0.7:
                last_partial, busy[0] = total, True
                threading.Thread(target=partial, args=(np.concatenate(chunks),), daemon=True).start()
            if heard_speech and quiet >= SILENCE_TO_STOP:
                break
            if not heard_speech and total >= NO_SPEECH_TIMEOUT:
                break
        return np.concatenate(chunks)

    def _quick_transcribe(self, audio: np.ndarray) -> str:
        """A fast, rough pass for the live pop-up (the accurate pass runs once you stop talking)."""
        samples = audio.astype(np.float32) / 32768.0
        segments, _ = self.whisper.transcribe(samples, language="en", beam_size=1,
                                              condition_on_previous_text=False, initial_prompt=WHISPER_PROMPT)
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.85).strip()
        return WAKE_PREFIX.sub("", text).strip(" ,.")

    def _transcribe(self, audio: np.ndarray) -> str:
        samples = audio.astype(np.float32) / 32768.0
        segments, _ = self.whisper.transcribe(
            samples, language="en",
            beam_size=5,                           # search wider for the right words (base used greedy = more mistakes)
            temperature=[0.0, 0.2, 0.4],           # retry a little hotter if a pass looks like a hallucination
            condition_on_previous_text=False,      # each command is independent; stops it echoing an earlier one
            vad_filter=True, vad_parameters=dict(min_silence_duration_ms=300),
            no_speech_threshold=0.7,
            initial_prompt=WHISPER_PROMPT)
        # Whisper hallucinates words like "You" on silence; drop only segments it's quite sure aren't speech
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.85).strip()
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
