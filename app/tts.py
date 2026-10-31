import io
import logging
import os
import threading
import wave
from collections import OrderedDict

import numpy as np

from .languages import SUPERTONIC_LANGUAGES, voice_for
from .models import DATA_DIR, download, lock_for

log = logging.getLogger("livecap.tts")

PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
PIPER_DIR = DATA_DIR / "voices"
PIPER_MAX_LOADED = 4
SUPERTONIC_DIR = os.environ.get("LIVECAP_SUPERTONIC_DIR", "/opt/supertonic-3")  # the Mac app sets this to its bundled copy
SUPERTONIC_VOICE = os.environ.get("LIVECAP_VOICE", "M1")
SUPERTONIC_STEPS = 5  # quality levels off here; fewer steps start to slur words
THREADS = 2  # more barely helps, and would take cores from transcription

# The model starts each clip from random noise, which makes every sentence sound like a slightly different voice.
SUPERTONIC_SEED = 7

ENGINE = os.environ.get("LIVECAP_VOICE_ENGINE", "supertonic")
MIN_STEADY_SPEED = 0.85  # the slowest the voice settles at, for a slower speaker
MAX_STEADY_SPEED = 1.25  # the fastest it settles at, for a faster one
MAX_SPEED = 1.4  # with catch-up on top; beyond this, words start to be lost
MIN_CHARACTERS_PER_SECOND = 14  # a floor for voices that drag in some languages
SYLLABIC_LANGUAGES = {"zh", "ja", "ko", "hi", "ar"}  # characters per second means something else in these scripts
DEFAULT_SECONDS_PER_CHARACTER = 1 / 15
RATE_MEMORY = 0.7
PACE_MEMORY = 0.8  # the voice follows the speaker's overall pace, not each sentence, so its speed does not jump
MIN_SECONDS_TO_LEARN_PACE = 1.5  # shorter sentences say too little about how fast someone talks
SILENCE_LEVEL = 0.01
KEEP_BEFORE_SECONDS = 0.03
KEEP_AFTER_SECONDS = 0.08
FADE_SECONDS = 0.01
TARGET_LOUDNESS = 0.1  # RMS of the speech, so that sentences match in volume


class SupertonicVoices:
    """One model for every language, sounding more natural than Piper. Bundled with the Mac app."""

    def __init__(self):
        self.tts = None
        self.style = None
        self.lock = threading.Lock()

    def has_voice(self, lang):
        return lang in SUPERTONIC_LANGUAGES

    def prepare(self, lang):
        with self.lock:
            if self.tts is None:
                from supertonic import TTS

                self.tts = TTS(model="supertonic-3", model_dir=SUPERTONIC_DIR,
                               intra_op_num_threads=THREADS, inter_op_num_threads=1)
                self.style = self.tts.get_voice_style(SUPERTONIC_VOICE)

    def synthesize(self, lang, text, speed):
        self.prepare(lang)
        np.random.seed(SUPERTONIC_SEED)
        audio, _ = self.tts.synthesize(text, voice_style=self.style, total_steps=SUPERTONIC_STEPS, speed=speed,
                                       lang=SUPERTONIC_LANGUAGES[lang])
        return np.asarray(audio).reshape(-1), self.tts.sample_rate


def _piper_url(voice):
    locale, name, quality = voice.split("-")
    return f"{PIPER_URL}/{locale.split('_')[0]}/{locale}/{name}/{quality}/{voice}.onnx"


class PiperVoices:
    """Older-sounding, but faster and much smaller. A voice is fetched per language on first use."""

    def __init__(self):
        self.loaded = OrderedDict()
        self.lock = threading.Lock()

    def has_voice(self, lang):
        return bool(voice_for(lang))

    def _load(self, voice):
        from piper import PiperVoice

        with self.lock:
            if voice in self.loaded:
                self.loaded.move_to_end(voice)
                return self.loaded[voice]
        model = PIPER_DIR / f"{voice}.onnx"
        with lock_for(model):
            config = model.with_suffix(".onnx.json")
            if not config.exists() or not model.exists():
                url = _piper_url(voice)
                download(url, model)
                download(url + ".json", config)
        loaded = PiperVoice.load(str(model))
        with self.lock:
            self.loaded[voice] = loaded
            while len(self.loaded) > PIPER_MAX_LOADED:
                self.loaded.popitem(last=False)
        return loaded

    def prepare(self, lang):
        if voice_for(lang):
            self._load(voice_for(lang))

    def synthesize(self, lang, text, speed):
        from piper import SynthesisConfig

        voice = self._load(voice_for(lang))
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            voice.synthesize_wav(text, wav, syn_config=SynthesisConfig(length_scale=1 / speed))
        output.seek(0)
        with wave.open(output) as wav:
            return np.frombuffer(wav.readframes(wav.getnframes()), "<i2") / 32768, wav.getframerate()


def natural_pace(lang, characters, target_seconds, seconds_per_character):
    """The speed that would read this text in the time the speaker took, within what stays clear."""
    pace = characters * seconds_per_character / target_seconds if target_seconds > 0 else 1.0
    if lang not in SYLLABIC_LANGUAGES:
        pace = max(pace, seconds_per_character * MIN_CHARACTERS_PER_SECOND)
    return min(MAX_STEADY_SPEED, max(MIN_STEADY_SPEED, pace))


def finish_clip(audio, sample_rate):
    """Removes the model's silent padding, so the player alone sets the gaps, and levels the volume."""
    audio = np.asarray(audio, np.float32)
    loud = np.flatnonzero(np.abs(audio) > SILENCE_LEVEL)
    if not len(loud):
        return audio[:0]
    start = max(0, loud[0] - int(KEEP_BEFORE_SECONDS * sample_rate))
    end = min(len(audio), loud[-1] + int(KEEP_AFTER_SECONDS * sample_rate))
    audio = audio[start:end].copy()
    voiced = audio[np.abs(audio) > SILENCE_LEVEL]
    gain = TARGET_LOUDNESS / max(float(np.sqrt(np.mean(voiced ** 2))), 1e-6)
    audio *= min(gain, 0.97 / max(float(np.abs(audio).max()), 1e-6))
    fade = min(int(FADE_SECONDS * sample_rate), len(audio) // 2)
    if fade:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)
        audio[:fade] *= ramp
        audio[-fade:] *= ramp[::-1]
    return audio


class Speaker:
    """Text to WAV, at a steady pace that follows the speaker. Callers run this on one worker thread."""

    def __init__(self, engine=ENGINE):
        self.voices = PiperVoices() if engine == "piper" else SupertonicVoices()
        self.rates = {}  # seconds per character at normal speed, learned per language
        self.paces = {}  # the steady speed for each language, following the speaker

    def has_voice(self, lang):
        return self.voices.has_voice(lang)

    def prepare(self, lang):
        self.voices.prepare(lang)

    def _steady_pace(self, lang, characters, target_seconds, rate):
        pace = natural_pace(lang, characters, target_seconds, rate)
        if lang not in self.paces:
            self.paces[lang] = pace
        elif target_seconds >= MIN_SECONDS_TO_LEARN_PACE:
            self.paces[lang] = self.paces[lang] * PACE_MEMORY + pace * (1 - PACE_MEMORY)
        return self.paces[lang]

    def synthesize(self, lang, text, target_seconds=0.0, boost=0.0):
        """boost is how much faster to read while a listener's player catches up, such as 0.1 for 10%."""
        if not self.has_voice(lang):
            raise ValueError(f"no voice for {lang}")
        rate = self.rates.get(lang, DEFAULT_SECONDS_PER_CHARACTER)
        speed = min(MAX_SPEED, self._steady_pace(lang, len(text), target_seconds, rate) * (1 + boost))
        audio, sample_rate = self.voices.synthesize(lang, text, speed)
        audio = finish_clip(audio, sample_rate)
        measured = len(audio) / sample_rate * speed / max(1, len(text))
        if measured > 0:
            self.rates[lang] = rate * RATE_MEMORY + measured * (1 - RATE_MEMORY) if lang in self.rates else measured
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
        return output.getvalue()
