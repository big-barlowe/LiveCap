import numpy as np

from app.transcriber import MAX_BUFFER_SECONDS, SAMPLE_RATE, Transcriber


def spoken(text, start=0.0, per_word=0.5):
    return [{"word": word, "start": start + i * per_word, "end": start + (i + 1) * per_word - 0.05}
            for i, word in enumerate(text.split())]


class Reads:
    """Returns a scripted transcript for each read."""

    def __init__(self, *texts):
        self.texts = list(texts)
        self.lengths = []

    def __call__(self, samples):
        self.lengths.append(len(samples) / SAMPLE_RATE)
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        return {"language": "en", "words": spoken(text)}


def seconds(amount):
    return np.zeros(int(amount * SAMPLE_RATE), np.float32)


def test_nothing_is_released_before_a_read():
    t = Transcriber(Reads("Hello there. And more"), every=3)
    assert t.add(seconds(2)) == []


def test_sentence_needs_two_agreeing_reads_and_words_after_it():
    t = Transcriber(Reads("Hello there. And", "Hello there. And more words"), every=3)
    assert t.add(seconds(3)) == []
    released = t.add(seconds(3))
    assert [s.text for s in released] == ["Hello there."]


def test_disagreeing_reads_release_nothing():
    t = Transcriber(Reads("Hello their. And more", "Hello there. And more words"), every=3)
    t.add(seconds(3))
    assert t.add(seconds(3)) == []


def test_last_sentence_is_never_released_while_speech_continues():
    t = Transcriber(Reads("Hello there.", "Hello there."), every=3)
    t.add(seconds(3))
    assert t.add(seconds(3)) == []


def test_release_trims_the_buffer_to_the_sentence_boundary():
    t = Transcriber(Reads("Hello there. And more words", "Hello there. And more words"), every=3)
    t.add(seconds(3))
    t.add(seconds(3))
    assert 4.5 < t.buffered_seconds < 5.5


def test_cut_keeps_the_gap_before_the_next_word_but_not_the_last_word():
    assert Transcriber._boundary(1.0, 2.0) == 1.85
    assert Transcriber._boundary(1.0, 1.05) == 1.0


def test_long_run_breaks_at_a_clause():
    text = "one two three four five six seven, eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"
    t = Transcriber(Reads(text), every=3)
    released = []
    for _ in range(int(MAX_BUFFER_SECONDS / 3) + 1):
        released += t.add(seconds(3))
    assert released[0].text == "One two three four five six seven,"
    assert released[0].continues


def test_without_a_comma_a_long_run_waits_for_the_hard_limit():
    text = " ".join(f"w{i}" for i in range(40))
    t = Transcriber(Reads(text), every=3, max_buffer=MAX_BUFFER_SECONDS)
    held = [t.add(seconds(3)) for _ in range(int(MAX_BUFFER_SECONDS / 3) - 1)]
    assert not any(held)
    assert t.add(seconds(3))


def test_next_sentence_keeps_the_agreement_already_reached():
    both = "One two. Three four. Five six"
    t = Transcriber(Reads(both, both, "Three four. Five six seven", "Five six seven"), every=3)
    t.add(seconds(3))
    assert [s.text for s in t.add(seconds(3))] == ["One two.", "Three four."]


def test_partial_only_shows_words_two_reads_agree_on_and_never_shrinks():
    t = Transcriber(Reads("Hello there my frend", "Hello there my friend today", "Hello there my friend today now"), every=3)
    t.add(seconds(3))
    assert t.partial == ""
    t.add(seconds(3))
    assert t.partial == "Hello there my"
    t.add(seconds(3))
    assert t.partial == "Hello there my friend today"


def test_finish_releases_everything_and_clears():
    reads = Reads("Hello there. And more")
    t = Transcriber(reads, every=3)
    t.add(seconds(1))
    released = t.finish()
    assert [s.text for s in released] == ["Hello there.", "And more"]
    assert t.buffered_seconds == 0
    assert t.finish() == []


def test_finish_trims_trailing_silence_before_reading():
    reads = Reads("Hello.")
    t = Transcriber(reads, every=3)
    t.add(seconds(2))
    t.finish(trim_seconds=0.5)
    assert reads.lengths == [1.5]


def test_sentence_records_how_long_it_took_to_say():
    t = Transcriber(Reads("Hello there friend."), every=3)
    t.add(seconds(1))
    assert abs(t.finish()[0].seconds - 1.45) < 1e-6


def test_slow_reads_stretch_the_interval():
    def slow(samples):
        import time

        time.sleep(0.05)
        return {"language": "en", "words": []}

    t = Transcriber(slow, every=0.01)
    t.add(seconds(1))
    assert t.interval >= 0.15
