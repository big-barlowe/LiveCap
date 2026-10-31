import numpy as np

from app.tts import KEEP_AFTER_SECONDS, KEEP_BEFORE_SECONDS, MAX_SPEED, MAX_STEADY_SPEED, MIN_STEADY_SPEED, TARGET_LOUDNESS, Speaker, finish_clip, natural_pace

RATE = 1000


def padded(level=0.5, before=0.5, speech=1.0, after=0.6):
    return np.concatenate((np.zeros(int(before * RATE)), np.full(int(speech * RATE), level), np.zeros(int(after * RATE))))


def test_clip_loses_its_padding_but_keeps_short_edges():
    clip = finish_clip(padded(), RATE)
    assert abs(len(clip) / RATE - (1.0 + KEEP_BEFORE_SECONDS + KEEP_AFTER_SECONDS)) < 0.01


def test_quiet_and_loud_sentences_come_out_equally_loud():
    for level in (0.05, 0.5):
        clip = finish_clip(padded(level), RATE)
        speech = clip[len(clip) // 4:len(clip) // 2]
        assert abs(np.sqrt(np.mean(speech ** 2)) - TARGET_LOUDNESS) < 0.01


def test_silence_gives_an_empty_clip():
    assert len(finish_clip(np.zeros(RATE), RATE)) == 0


def test_pace_stays_in_bounds():
    assert natural_pace("en", 100, 100.0, 1 / 20) == MIN_STEADY_SPEED
    assert natural_pace("en", 1000, 1.0, 1 / 15) == MAX_STEADY_SPEED


class FakeVoices:
    def __init__(self):
        self.speeds = []

    def has_voice(self, lang):
        return True

    def synthesize(self, lang, text, speed):
        self.speeds.append(speed)
        return padded(), RATE


def speaker():
    result = Speaker("piper")
    result.voices = FakeVoices()
    return result


def test_speed_moves_gradually_when_one_sentence_is_rushed():
    s = speaker()
    text = "x" * 45
    s.synthesize("en", text, 3.0)
    s.synthesize("en", text, 3.0)
    s.synthesize("en", text, 1.5)
    first, _, rushed = s.voices.speeds
    assert rushed - first < 0.1


def test_boost_speeds_up_within_the_limit():
    s = speaker()
    s.synthesize("en", "x" * 45, 3.0, boost=0.3)
    assert s.voices.speeds[0] <= MAX_SPEED
    assert s.voices.speeds[0] >= 1.25


def test_short_sentences_do_not_move_the_pace():
    s = speaker()
    s.synthesize("en", "x" * 45, 3.0)
    before = s.paces["en"]
    s.synthesize("en", "Yes, thank you very much.", 0.5)
    assert s.paces["en"] == before
