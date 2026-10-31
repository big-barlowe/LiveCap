import asyncio
import logging
import os
import sys
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("livecap.hub")

ORIGINAL = "orig"
MAX_SEGMENTS = 500
BACKLOG = 30
MAX_CLIENT_QUEUE = 500
MAX_AUDIO_CACHE = 200
RETRY_AFTER_SECONDS = 30


def _lower_priority():
    # Applies to this thread and any it starts: captions must not lag behind translation or speech on a small machine.
    # On macOS nice would slow the whole process, and recognition is on the GPU anyway.
    if sys.platform == "linux":
        os.nice(10)


class Segment:
    __slots__ = ("id", "text", "source", "seconds", "translations")

    def __init__(self, id, text, source, seconds):
        self.id, self.text, self.source, self.seconds = id, text, source, seconds
        self.translations = {}


class Client:
    def __init__(self, lang, admin=False):
        self.lang = lang
        self.admin = admin
        self.outbox = asyncio.Queue(MAX_CLIENT_QUEUE)


class LanguageWorker:
    """Translates for one output language, keeping sentences in order."""

    def __init__(self, hub, lang):
        self.hub, self.lang = hub, lang
        self.segments = deque()
        self.backlog_upto = 0  # segments up to here are history for a viewer who just joined, not to be read aloud
        self.wake = asyncio.Event()
        self.task = asyncio.create_task(self._run())

    def submit(self, segment):
        self.segments.append(segment)
        self.wake.set()

    async def _run(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            while self.segments:
                await self._translate(self.segments.popleft())

    async def _translate(self, segment):
        # On failure the viewer still gets the original words, but they are never stored as the
        # translation, so they are not read aloud in the wrong language's voice.
        output = segment.translations.get(self.lang, segment.text)
        if self.lang not in segment.translations and await self.hub.ensure_language(segment.source, self.lang):
            try:
                output = await self.hub.run_translation(segment.text, segment.source, self.lang)
                segment.translations[self.lang] = output
            except Exception as failure:
                log.warning("translation %s to %s failed: %s", segment.source, self.lang, failure)
                self.hub.send_status(self.lang, "error", str(failure))
        self.hub.send_segment(self.lang, segment, output, segment.id <= self.backlog_upto)


class Hub:
    """Fans captions out to viewers, each in the output language they chose."""

    def __init__(self, translator, speaker):
        self.translator, self.speaker = translator, speaker
        self.loop = None
        self.clients = set()
        self.segments = OrderedDict()
        self.next_id = 1
        self.partial = ""
        self.partial_source = "en"
        self.workers = {}
        self.audio = OrderedDict()
        self.failed_at = {}
        self.translate_pool = ThreadPoolExecutor(2, thread_name_prefix="translate", initializer=_lower_priority)
        self.speech_pool = ThreadPoolExecutor(1, thread_name_prefix="speech", initializer=_lower_priority)

    def attach(self, loop):
        self.loop = loop

    def on_sentence(self, sentence):
        """Called on the engine thread."""
        self.loop.call_soon_threadsafe(self._add_sentence, sentence)

    def on_partial(self, text):
        """Called on the engine thread."""
        self.loop.call_soon_threadsafe(self._set_partial, text)

    # Everything below runs on the event loop.

    def set_input_language(self, lang):
        # With auto-detect, the language is only known from each finished sentence.
        if lang != "auto":
            self.partial_source = lang

    def _set_partial(self, text):
        self.partial = text
        for client in self.clients:
            if self._reads_source(client):
                self._put(client, {"type": "partial", "text": text})

    def _reads_source(self, client):
        # In-progress words are only shown untranslated: translating half a sentence would keep rewriting it.
        return client.lang in (ORIGINAL, self.partial_source)

    def _add_sentence(self, sentence):
        self.partial_source = sentence.language
        segment = Segment(self.next_id, sentence.text, sentence.language, sentence.seconds)
        self.next_id += 1
        self.segments[segment.id] = segment
        while len(self.segments) > MAX_SEGMENTS:
            self.segments.popitem(last=False)
        for lang in {client.lang for client in self.clients}:
            if lang == ORIGINAL or lang == segment.source:
                self.send_segment(lang, segment, segment.text)
            else:
                self._worker(lang).submit(segment)

    def _worker(self, lang):
        if lang not in self.workers:
            self.workers[lang] = LanguageWorker(self, lang)
        return self.workers[lang]

    def _put(self, client, message):
        try:
            client.outbox.put_nowait(message)
        except asyncio.QueueFull:
            # A viewer this far behind is gone or stalled; its socket handler closes on None.
            while not client.outbox.empty():
                client.outbox.get_nowait()
            client.outbox.put_nowait(None)

    def broadcast_state(self, admin_message, public_message):
        for client in self.clients:
            if client.admin:
                self._put(client, admin_message)
            elif public_message is not None:
                self._put(client, public_message)

    def send_segment(self, lang, segment, text, backlog=False):
        message = {"type": "segment", "id": segment.id, "text": text, "source": segment.source, "backlog": backlog}
        for client in self.clients:
            if client.lang == lang or (client.lang == ORIGINAL and lang == segment.source):
                self._put(client, message)

    def send_status(self, lang, state, detail=""):
        for client in self.clients:
            if client.lang == lang:
                self._put(client, {"type": "language", "lang": lang, "state": state,
                                   "detail": detail if client.admin else ""})

    async def run_translation(self, text, source, target):
        return await self.loop.run_in_executor(self.translate_pool, self.translator.translate, text, source, target)

    async def ensure_language(self, source, target):
        """Fetches the models this pair needs, telling viewers while it takes time. False if unavailable."""
        if self.translator.is_ready(source, target):
            return True
        failed_at = self.failed_at.get((source, target))
        if failed_at and time.monotonic() - failed_at < RETRY_AFTER_SECONDS:
            return False
        self.send_status(target, "downloading")
        try:
            await self.loop.run_in_executor(self.translate_pool, self.translator.prepare, source, target)
        except Exception as failure:
            log.warning("could not fetch %s to %s: %s", source, target, failure)
            self.failed_at[source, target] = time.monotonic()
            self.send_status(target, "error", str(failure))
            return False
        self.send_status(target, "ready")
        return True

    def join(self, lang, admin=False):
        client = Client(lang, admin)
        self.clients.add(client)
        self.send_backlog(client)
        return client

    def leave(self, client):
        self.clients.discard(client)

    def set_language(self, client, lang):
        client.lang = lang
        self._put(client, {"type": "reset"})
        self.send_backlog(client)

    def send_backlog(self, client):
        for segment in list(self.segments.values())[-BACKLOG:]:
            if client.lang in (ORIGINAL, segment.source):
                text = segment.text
            elif client.lang in segment.translations:
                text = segment.translations[client.lang]
            else:
                worker = self._worker(client.lang)
                worker.backlog_upto = max(worker.backlog_upto, segment.id)
                worker.submit(segment)
                continue
            self._put(client, {"type": "segment", "id": segment.id, "text": text, "source": segment.source, "backlog": True})
        if self.partial and self._reads_source(client):
            self._put(client, {"type": "partial", "text": self.partial})

    async def speech(self, lang, segment_id, boost=0.0):
        """WAV bytes reading one sentence. Concurrent listeners share one synthesis."""
        segment = self.segments.get(segment_id)
        if segment is None:
            return None
        if lang == ORIGINAL:
            lang = segment.source
        text = segment.text if lang == segment.source else segment.translations.get(lang)
        if not text or not self.speaker.has_voice(lang):
            return None
        key = lang, segment_id, boost
        if key not in self.audio:
            self.audio[key] = self.loop.run_in_executor(self.speech_pool, self.speaker.synthesize, lang, text, segment.seconds, boost)
            while len(self.audio) > MAX_AUDIO_CACHE:
                self.audio.popitem(last=False)
        try:
            return await asyncio.shield(self.audio[key])
        except asyncio.CancelledError:
            raise
        except Exception:
            self.audio.pop(key, None)
            raise
