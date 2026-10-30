"""Microphone: wait for "Hey Jarvis" (or the Ctrl+Alt+J shortcut), record until you stop talking, transcribe."""
import collections
import logging
import queue
import re
import threading
import time
import winsound

import numpy as np
import sounddevice as sd

from . import health
from .config import ROOT, set_setting, settings

log = logging.getLogger("jarvis.voice")

# Which languages you speak to Jarvis in (config "voice_language", also in the phone's Settings): "en", "bg", or
# "both" (each command is checked for English vs Bulgarian first). Bulgarian needs a multilingual Whisper model,
# "small" by default, kept on this drive (.brain/whisper) since C: is nearly full.
LANGUAGES = ("en", "bg", "both")
PROMPTS = {"en": "Hey Jarvis, open Spotify, Claude, File Explorer, VS Code, Discord, Chrome. Put them on monitor 3. "
                 "What's the weather in Botevgrad?",
           "bg": "Хей Джарвис, отвори Spotify, Discord и Chrome. Сложи ги на монитор 3."}

RATE = 16000
CHUNK = 1280  # 80 ms, the frame size openWakeWord expects
PRE_ROLL_CHUNKS = 22  # keep ~1.8 s before the wake word so words right after it aren't lost

# Vocabulary hint so Whisper spells app names right
WHISPER_PROMPT = PROMPTS["en"]
WAKE_PREFIX = re.compile(r"^\W*(hey|hi|ok|okay|хей|ей)?\W*(jarvis|jervis|javis|джарвис|джарвиз)\W*", re.IGNORECASE)


_listeners: list = []


def language_changed() -> None:
    """The voice language was changed (phone Settings): load the right Whisper now, not on the next command."""
    for listener in _listeners:
        if getattr(listener, "loaded_for", None) and model_for(language()) != model_for(listener.loaded_for):
            threading.Thread(target=listener._load_whisper, daemon=True, name="whisper-reload").start()


def language() -> str:
    lang = settings.get("voice_language", "en")
    return lang if lang in LANGUAGES else "en"


def model_for(lang: str) -> str:
    """English-only models (".en") are faster and better at English; Bulgarian needs a multilingual one."""
    if lang == "en":
        return settings["whisper_model"]
    name = settings.get("whisper_model_multi", "small")
    return name[:-3] if name.endswith(".en") else name


def _beep(freq: int) -> None:
    threading.Thread(target=winsound.Beep, args=(freq, 120), daemon=True).start()


def input_devices() -> list[tuple[int, str]]:
    """Every usable microphone as (device index, name), de-duplicated by name."""
    out, seen = [], set()
    try:
        for i, d in enumerate(sd.query_devices()):
            name = (d.get("name") or "").strip()
            if d.get("max_input_channels", 0) > 0 and name and name.lower() not in seen:
                seen.add(name.lower())
                out.append((i, name))
    except Exception:
        log.debug("couldn't list input devices", exc_info=True)
    return out


def device_name(index) -> str:
    if index is None:
        return "Windows default"
    try:
        return sd.query_devices(index)["name"]
    except Exception:
        return f"device {index}"


class VoiceListener:
    def __init__(self, on_command, on_wake=None, on_partial=None, on_idle=None):
        self.on_command = on_command  # (text, source) where source is "wake" or "hotkey"
        self.on_wake = on_wake        # wake word / shortcut heard -> show the pop-up
        self.on_partial = on_partial  # live transcription as you speak
        self.on_idle = on_idle        # heard nothing usable -> hide the pop-up
        self.audio: queue.Queue[np.ndarray] = queue.Queue()
        self._manual = threading.Event()   # the shortcut asked to start listening now
        self._active = threading.Event()   # True while actively recording a command
        self._cancel = threading.Event()   # stop the command currently being recorded
        self._reopen = threading.Event()   # re-open the audio stream (the mic was changed)
        self.loaded_for, self.last_lang = None, "en"
        self.skip = 0   # samples of pre-roll (with the wake word) at the start of the recording
        _listeners.append(self)

    def trigger(self) -> None:
        """The Ctrl+Alt+J shortcut: start listening, or — if already listening — stop."""
        if self._active.is_set():
            self._cancel.set()
        else:
            self._manual.set()

    def cancel(self) -> None:
        """Stop the command currently being recorded (the pop-up's X button)."""
        self._cancel.set()

    def set_device(self, index) -> None:
        """Switch microphone (index, or None for the Windows default) and re-open the stream."""
        set_setting("input_device", index)
        self._reopen.set()

    def _load_models(self):
        from openwakeword.model import Model
        from openwakeword.utils import download_models

        name = settings["wake_word_model"]
        log.info("Loading wake word model '%s'...", name)
        download_models(model_names=[name])
        self.wake = Model(wakeword_models=[name], inference_framework="onnx")
        self._load_whisper()

    def _load_whisper(self) -> None:
        """(Re)load Whisper for the current voice language. Called again when the language changes."""
        from faster_whisper import WhisperModel
        lang = language()
        name = model_for(lang)
        log.info("Loading Whisper '%s' for %s (first run downloads it)...", name,
                 {"en": "English", "bg": "Bulgarian", "both": "English and Bulgarian"}[lang])
        # the English models are already in the usual cache; new multilingual ones go to D:, C: is nearly full
        root = None if name.endswith(".en") else str(ROOT / ".brain" / "whisper")
        self.whisper = WhisperModel(name, device="cpu", compute_type="int8", download_root=root)
        self.loaded_for = lang
        self.last_lang = "bg" if lang == "bg" else "en"

    def _callback(self, indata, _frames, _time, status):
        if status:
            log.debug("audio status: %s", status)
        self.audio.put(indata[:, 0].copy())

    def run(self) -> None:
        self._load_models()
        fails = 0   # in a row: one blip isn't worth a message, a mic that stays broken is
        while True:
            self._reopen.clear()
            dev = settings["input_device"]
            opened = 0.0
            try:
                with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK,
                                    callback=self._callback, device=dev):
                    log.info("Listening on %s. Say \"Hey Jarvis, ...\" or press Ctrl+Alt+J.", device_name(dev))
                    opened = time.time()
                    if fails >= 3:
                        health.fixed("mic", "The microphone works again.")
                    self._wake_loop()
            except MemoryError:   # not the mic's fault: Windows ran out of memory to hand out
                log.exception("Out of memory while listening; retrying in 2 s.")
                health.out_of_memory()
                time.sleep(2)
                continue
            except Exception:
                log.exception("Microphone error; retrying in 2 s (try a different mic in the tray).")
                fails = 1 if opened and time.time() - opened > 60 else fails + 1   # it had worked a while
                if fails >= 3:
                    health.problem("mic", f"I can't use the microphone ({device_name(dev)}), so \"Hey Jarvis\" "
                                   "doesn't work on the PC. Check it's plugged in, or pick another one: right-click "
                                   "the tray icon › Microphone.")
                time.sleep(2)

    def _wake_loop(self) -> None:
        threshold = settings["wake_threshold"]
        recent = collections.deque(maxlen=PRE_ROLL_CHUNKS)
        while not self._reopen.is_set():
            try:
                chunk = self.audio.get(timeout=0.5)
            except queue.Empty:
                continue
            recent.append(chunk)
            scores = self.wake.predict(chunk)
            manual = self._manual.is_set()
            if not manual and max(scores.values()) < threshold:
                continue
            self._manual.clear()
            source = "hotkey" if manual else "wake"
            _beep(880)
            if self.on_wake:
                try: self.on_wake()
                except Exception: log.debug("on_wake failed", exc_info=True)
            self._cancel.clear()
            self._active.set()
            noise = float(np.percentile([_rms(c) for c in recent], 20))
            # the wake word is at the end of the pre-roll; with the shortcut there's no wake word to skip
            self.skip = len(recent) * CHUNK if source == "wake" else 0
            audio = self._record(list(recent), noise)
            self._active.clear()
            self.wake.reset()
            recent.clear()
            _drain(self.audio)
            if self._cancel.is_set():   # stopped by the shortcut or the X button
                log.info("Listening cancelled.")
                if self.on_idle:
                    try: self.on_idle()
                    except Exception: pass
                continue
            text = self._transcribe(audio) if audio.size else ""
            if text:
                _beep(660)
                self.on_command(text, source)
            else:
                log.info("Heard the wake word but no command.")
                if self.on_idle:
                    try: self.on_idle()
                    except Exception: pass

    def _record(self, pre_roll: list[np.ndarray], noise: float) -> np.ndarray:
        """Collect audio until the speaker has been quiet for silence_seconds, emitting live partial
        transcriptions (word-by-word feel) while they talk. Stops early if cancelled or the mic changes."""
        silence_stop = settings["silence_seconds"]
        max_cmd = settings["max_command_seconds"]
        no_speech = settings["no_speech_seconds"]
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

        while total < max_cmd:
            if self._cancel.is_set() or self._reopen.is_set():
                break
            try:
                chunk = self.audio.get(timeout=0.5)
            except queue.Empty:
                continue
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
            if heard_speech and quiet >= silence_stop:
                break
            if not heard_speech and total >= no_speech:
                break
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)

    def _quick_transcribe(self, audio: np.ndarray) -> str:
        """A fast, rough pass for the live pop-up (the accurate pass runs once you stop talking)."""
        samples = audio.astype(np.float32) / 32768.0
        lang = self.last_lang   # in "both", the language of your last command is the best quick guess
        segments, _ = self.whisper.transcribe(samples, language=lang, beam_size=1,
                                              condition_on_previous_text=False, initial_prompt=PROMPTS[lang])
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.85).strip()
        return WAKE_PREFIX.sub("", text).strip(" ,.")

    def _language_of(self, samples: np.ndarray) -> str:
        """English or Bulgarian? Whisper is near-certain (0.99+) about English when it is English, but calls short
        Bulgarian anything from Russian to Polish or Albanian, so: English when it's sure, Bulgarian otherwise.
        The "Hey Jarvis" at the start (English either way) is left out of the guess."""
        setting = language()
        if setting != "both":
            return setting
        try:
            words = samples[self.skip:] if len(samples) - self.skip > RATE // 2 else samples
            _, _, probs = self.whisper.detect_language(words)
            return "en" if dict(probs).get("en", 0) >= 0.5 else "bg"
        except Exception:
            log.debug("language detection failed", exc_info=True)
            return self.last_lang

    def _transcribe(self, audio: np.ndarray) -> str:
        if language() != self.loaded_for and model_for(language()) != model_for(self.loaded_for):
            self._load_whisper()   # the language was changed in Settings
        self.loaded_for = language()
        samples = audio.astype(np.float32) / 32768.0
        lang = self.last_lang = self._language_of(samples)
        segments, _ = self.whisper.transcribe(
            samples, language=lang,
            beam_size=5,                           # search wider for the right words (base used greedy = more mistakes)
            temperature=[0.0, 0.2, 0.4],           # retry a little hotter if a pass looks like a hallucination
            condition_on_previous_text=False,      # each command is independent; stops it echoing an earlier one
            vad_filter=True, vad_parameters=dict(min_silence_duration_ms=300),
            no_speech_threshold=0.7,
            initial_prompt=PROMPTS[lang])
        # Whisper hallucinates words like "You" on silence; drop only segments it's quite sure aren't speech
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.85).strip()
        log.info("Heard: %r", text)
        return WAKE_PREFIX.sub("", text).strip(" ,.")


# ---------------------------------------------------------------- Voice mode on the phone

# What Whisper "hears" in a clip with no real words in it
_PHANTOMS = {"you", "thank you for watching", "thanks for watching", "субтитри", "абонирайте се"}
_phone_model = None   # (name, model): only when the PC's own listener hasn't loaded one
_phone_lock = threading.Lock()
_phone_lang = "en"    # the language you last spoke on the phone (the live words' best guess in "both")


def _model_for_phone():
    want = model_for(language())
    for listener in _listeners:
        if getattr(listener, "whisper", None) and listener.loaded_for and model_for(listener.loaded_for) == want:
            return listener.whisper
    global _phone_model
    with _phone_lock:
        if not _phone_model or _phone_model[0] != want:
            from faster_whisper import WhisperModel
            root = None if want.endswith(".en") else str(ROOT / ".brain" / "whisper")
            log.info("Loading Whisper '%s' for the phone's voice mode...", want)
            _phone_model = (want, WhisperModel(want, device="cpu", compute_type="int8", download_root=root))
        return _phone_model[1]


def _samples(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0


def quick_phone(pcm: bytes) -> str:
    """A fast, rough pass over what you've said so far, for the words that appear while you talk."""
    samples = _samples(pcm)
    if len(samples) < RATE // 2:
        return ""
    lang = language() if language() != "both" else _phone_lang
    segments, _ = _model_for_phone().transcribe(samples, language=lang, beam_size=1, condition_on_previous_text=False,
                                                initial_prompt=PROMPTS[lang])
    return " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.85).strip()


def transcribe_phone(pcm: bytes, keep_wake: bool = False) -> tuple[str, str]:
    """Voice mode on the phone records you (16 kHz, mono, 16-bit) and sends it here: the same Whisper as
    "Hey Jarvis" turns it into words. Returns (text, language); text is "" when there were no real words.
    keep_wake: leave a leading "Jarvis" in (cutting in on Jarvis by voice listens for it)."""
    global _phone_lang
    samples = _samples(pcm)
    if len(samples) < RATE // 4:
        return "", "en"
    model = _model_for_phone()
    lang = language()
    if lang == "both":   # same rule as on the PC: English when Whisper is sure, Bulgarian otherwise
        try:
            _, _, probs = model.detect_language(samples)
            lang = "en" if dict(probs).get("en", 0) >= 0.5 else "bg"
        except Exception:
            lang = "en"
    segments, _ = model.transcribe(
        samples, language=lang, beam_size=5, temperature=[0.0, 0.2, 0.4], condition_on_previous_text=False,
        vad_filter=True, vad_parameters=dict(min_silence_duration_ms=300), no_speech_threshold=0.7,
        initial_prompt=PROMPTS[lang])
    text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.85).strip()
    if not keep_wake:
        text = WAKE_PREFIX.sub("", text).strip(" ,")
    if re.sub(r"[^\w\s]", "", text).strip().lower() in _PHANTOMS:
        text = ""
    _phone_lang = lang
    log.info("Heard on the phone: %r", text)
    return text, lang


def _rms(chunk: np.ndarray) -> float:
    return float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))


def _drain(q: queue.Queue) -> None:
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass
