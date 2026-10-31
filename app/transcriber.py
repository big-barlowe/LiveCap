import re
import time

import numpy as np

from .sentences import group_sentences, tidy

SAMPLE_RATE = 16000
DECODE_EVERY_SECONDS = 1.0  # how much new speech arrives between re-reads
DECODE_COST_FACTOR = 3  # a slow machine re-reads less often, spending at most a third of its time here
CLAUSE_BREAK_SECONDS = 8.0  # a sentence running longer is released at a comma, if it has one
MAX_BUFFER_SECONDS = 12.0  # one running longer still is released at its widest pause
MIN_WORDS_AFTER_SENTENCE = 2  # words that must follow a sentence before it can be trusted as finished
MIN_CLAUSE_WORDS = 6
PARTIAL_HOLD_WORDS = 1
START_MARGIN_SECONDS = 0.15
HOLD_BACK_SECONDS = 2.0  # never force a release this close to the live edge, where words are least certain


def comparable(words):
    return re.sub(r"\W+", " ", " ".join(word["word"] for word in words)).strip().lower()


class Sentence:
    __slots__ = ("text", "seconds", "language", "continues")

    def __init__(self, words, language):
        self.text = tidy(" ".join(word["word"].strip() for word in words))
        self.seconds = max(0.0, words[-1]["end"] - words[0]["start"])
        self.language = language
        self.continues = False  # the speaker's sentence carries on into the next one


class Transcriber:
    """Turns speech into finished sentences, trading a few seconds of delay for accuracy.

    Speech is held in a buffer and re-read in full every few seconds, so each word is decided with the words around
    it in view. A sentence is only released once two reads agree on it and more words have followed. While a sentence
    builds, partial holds the words the last two reads agree on.

    decode(samples) returns Whistle's transcribe() result with word timestamps.
    """

    def __init__(self, decode, every=DECODE_EVERY_SECONDS, max_buffer=MAX_BUFFER_SECONDS):
        self.decode = decode
        self.every = every
        self.max_buffer = max_buffer
        self.reset()

    def reset(self):
        self.buffer = np.zeros(0, np.float32)
        self.undecoded = 0.0
        self.interval = self.every
        self.previous = []
        self.partial = ""
        self.partial_words = []

    @property
    def buffered_seconds(self):
        return len(self.buffer) / SAMPLE_RATE

    def _read(self):
        started = time.monotonic()
        result = self.decode(self.buffer)
        self.interval = max(self.every, (time.monotonic() - started) * DECODE_COST_FACTOR)
        self.undecoded = 0.0
        return group_sentences(result.get("words") or []), result.get("language") or ""

    @staticmethod
    def _boundary(previous_end, next_start):
        # Word timings run late, so cut well ahead of the next word to keep its first sound.
        return max(previous_end, next_start - START_MARGIN_SECONDS)

    def _release(self, sentences, boundary, language, remaining=()):
        self.buffer = self.buffer[int(boundary * SAMPLE_RATE):]
        self.previous = list(remaining)  # what the last read said about the sentences still in the buffer
        self.partial = ""
        self.partial_words = []
        return [Sentence(words, language) for words in sentences]

    def _update_partial(self, sentences):
        """The words at the front of the buffer that the last two reads agree on: safe to show while speech continues."""
        words = [word for sentence in sentences for word in sentence]
        keys = [comparable([word]) for word in words]
        agreed = 0
        for new, old in zip(keys, self.partial_words):
            if new != old:
                break
            agreed += 1
        self.partial_words = keys
        # Words near the live edge are the ones the next read is most likely to change.
        agreed = min(agreed, max(0, len(words) - PARTIAL_HOLD_WORDS))
        text = " ".join(word["word"].strip() for word in words[:agreed])
        if len(text) > len(self.partial):
            self.partial = text

    def add(self, samples):
        """Takes more speech and returns any sentences that are now settled."""
        self.buffer = np.concatenate((self.buffer, samples))
        self.undecoded += len(samples) / SAMPLE_RATE
        if self.undecoded < self.interval:
            return []
        sentences, language = self._read()
        if not sentences:
            self.previous = []
            return []
        self._update_partial(sentences)
        keys = [comparable(words) for words in sentences]
        settled = 0
        for index in range(len(sentences) - 1):
            if index >= len(self.previous) or self.previous[index] != keys[index]:
                break
            settled = index + 1
        if settled and sum(len(words) for words in sentences[settled:]) < MIN_WORDS_AFTER_SENTENCE:
            settled -= 1
        if settled:
            boundary = self._boundary(sentences[settled - 1][-1]["end"], sentences[settled][0]["start"])
            return self._release(sentences[:settled], boundary, language, keys[settled:])
        self.previous = keys
        if self.buffered_seconds >= min(CLAUSE_BREAK_SECONDS, self.max_buffer):
            return self._force(sentences, language, clause_only=self.buffered_seconds < self.max_buffer)
        return []

    def _force(self, sentences, language, clause_only=False):
        """The speaker has not reached a settled sentence end, so break at the last clause inside the safe region."""
        words = [word for sentence in sentences for word in sentence]
        limit = self.buffered_seconds - HOLD_BACK_SECONDS
        candidates = [i for i, word in enumerate(words[:-1]) if word["end"] <= limit and i + 1 >= MIN_CLAUSE_WORDS]
        if not candidates:
            return []
        clauses = [i for i in candidates if words[i]["word"].rstrip().endswith((",", ";", ":", "—"))]
        if clause_only and not clauses:
            return []
        # Without punctuation to go on, the widest pause between words is the most natural break.
        at = clauses[-1] if clauses else max(candidates, key=lambda i: words[i + 1]["start"] - words[i]["end"])
        boundary = self._boundary(words[at]["end"], words[at + 1]["start"])
        released = self._release([words[:at + 1]], boundary, language)
        released[0].continues = True
        return released

    def finish(self, trim_seconds=0.0):
        """The speaker has paused: everything left is released, with the whole utterance read one last time."""
        if trim_seconds and len(self.buffer) > trim_seconds * SAMPLE_RATE:
            self.buffer = self.buffer[:-int(trim_seconds * SAMPLE_RATE)]
        if not len(self.buffer):
            self.reset()
            return []
        sentences, language = self._read()
        self.reset()
        return [Sentence(words, language) for words in sentences]
