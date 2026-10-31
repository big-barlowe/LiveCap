import logging
import queue
import threading
import time
from collections import deque

from . import recognizers
from .sentences import Joiner, continue_sentence, guess_language
from .transcriber import SAMPLE_RATE, Transcriber
from .vad import FRAME_SECONDS, HANGOVER_FRAMES, SpeechGate

log = logging.getLogger("livecap.engine")

MAX_BACKLOG_SECONDS = 30  # beyond this, oldest audio is dropped to stay live
STALL_SECONDS = 2.5  # no audio for this long means the source has stopped
HELD_SENTENCE_SECONDS = 1.5  # how long an unfinished sentence waits for the speaker to carry on
KEEP_AFTER_SPEECH_SECONDS = 0.3  # of the silence that closed the gate; more only invites invented words


class Engine(threading.Thread):
    """Runs speech recognition on its own thread: the models are not thread-safe.

    on_sentence(sentence) fires on this thread, once for each finished sentence.
    on_partial(text) fires when the words of the sentence in progress change.
    """

    def __init__(self, on_sentence, on_partial, input_lang="en", source=None):
        super().__init__(daemon=True, name="recognizer")
        self.on_sentence = on_sentence
        self.on_partial = on_partial
        self.recognizer_name = ""
        self.input_lang = input_lang
        self.source = source
        self.speaking = False
        self.last_read_ms = 0.0
        self.dropped_seconds = 0.0
        self.error = None
        self._audio = queue.Queue()
        self._queued = 0
        self._queued_lock = threading.Lock()
        self._restart = threading.Event()
        self._speech = deque()
        self._gate = None
        self._recognizer = None
        self._partial = ""
        self._detected = "en"
        self._continuing = False
        self._language = input_lang
        self._transcriber = Transcriber(self._decode)
        self._joiner = Joiner()
        self._held_since = None
        self._last_audio = 0.0

    @property
    def backlog_seconds(self):
        return self._queued / SAMPLE_RATE

    def feed(self, samples, source):
        if source != self.source or not len(samples):
            return
        self._audio.put(samples)
        with self._queued_lock:
            self._queued += len(samples)
        while self._queued > MAX_BACKLOG_SECONDS * SAMPLE_RATE:
            dropped = self._next_audio(0)
            if dropped is None:
                break
            self.dropped_seconds += len(dropped) / SAMPLE_RATE

    def configure(self, source=None, input_lang=None):
        """Change the source or spoken language; what has been heard so far is finished first."""
        if source is not None:
            self.source = source or None
        if input_lang is not None:
            self.input_lang = input_lang
        self._restart.set()

    def _next_audio(self, timeout):
        try:
            samples = self._audio.get(timeout=timeout) if timeout else self._audio.get_nowait()
        except queue.Empty:
            return None
        with self._queued_lock:
            self._queued -= len(samples)
        return samples

    def _next_speech(self, timeout):
        """The next piece through the speech gate: samples, SpeechGate.END, or None if nothing is ready."""
        while not self._speech:
            samples = self._next_audio(timeout)
            if samples is None:
                return None
            self._last_audio = time.monotonic()
            self._speech.extend(self._gate.process(samples))
            self.speaking = self._gate.speaking
        return self._speech.popleft()

    def _decode(self, samples):
        started = time.monotonic()
        result = self._recognizer(samples, self._language)
        self.last_read_ms = (time.monotonic() - started) * 1000
        return result

    def _emit(self, sentences):
        forced = None if self.input_lang == "auto" else self.input_lang
        for sentence in sentences:
            if not forced and not sentence.language:
                self._detected = guess_language(sentence.text, self._detected)
            sentence.language = forced or sentence.language or self._detected
            if self._continuing:
                sentence.text = continue_sentence(sentence.text, sentence.language)
            self._continuing = sentence.continues
        for sentence in self._joiner.add(sentences):
            self.on_sentence(sentence)
        self._held_since = time.monotonic() if self._joiner.held else None
        self._publish_partial()

    def _publish_partial(self):
        parts = [self._joiner.held.text.rstrip(".,;:…") if self._joiner.held else "", self._transcriber.partial]
        partial = " ".join(part for part in parts if part)
        if partial != self._partial:
            self._partial = partial
            self.on_partial(partial)

    def _flush_held(self):
        for sentence in self._joiner.flush():
            self.on_sentence(sentence)
        self._held_since = None
        self._publish_partial()

    def _finish_speech(self, trim_seconds=0.0):
        self._emit(self._transcriber.finish(trim_seconds))

    def _restart_stream(self):
        self._restart.clear()
        self._finish_speech()
        self._flush_held()
        while self._next_audio(0) is not None:
            pass
        self._gate.reset()
        self._speech.clear()
        self.speaking = False
        self._language = None if self.input_lang == "auto" else self.input_lang

    def _step(self):
        piece = self._next_speech(0.1)
        if self._restart.is_set():
            self._restart_stream()
        elif piece is SpeechGate.END:
            self._finish_speech(max(0.0, HANGOVER_FRAMES * FRAME_SECONDS - KEEP_AFTER_SPEECH_SECONDS))
        elif piece is not None:
            self._emit(self._transcriber.add(piece))
        else:
            now = time.monotonic()
            if self.speaking and now - self._last_audio > STALL_SECONDS:
                self._gate.reset()
                self._speech.clear()
                self.speaking = False
                self._finish_speech()
            if self._held_since and not self.speaking and now - self._held_since > HELD_SENTENCE_SECONDS:
                self._flush_held()

    def run(self):
        try:
            self._recognizer = recognizers.create()
            self.recognizer_name = self._recognizer.name
            self._transcriber.every = self._transcriber.interval = self._recognizer.read_every
            self._gate = SpeechGate()
        except Exception as error:
            log.exception("could not load speech recognition")
            self.error = str(error)
            return
        self._language = None if self.input_lang == "auto" else self.input_lang
        log.info("%s ready", self._recognizer.name)
        while True:
            try:
                self._step()
                self.error = None
            except Exception as error:
                log.exception("transcription failed")
                self.error = str(error)
                self._transcriber.reset()
                time.sleep(1)
