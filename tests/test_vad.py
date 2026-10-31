import numpy as np

from app.vad import FRAME_SAMPLES, HANGOVER_FRAMES, PRE_ROLL_FRAMES, START_FRAMES, SpeechGate


class Script:
    """A detector that reports the probability it is given as the frame's value."""

    def __call__(self, pcm):
        return np.frombuffer(pcm, "<i2")[0] / 32767


def frames(*levels):
    return np.concatenate([np.full(FRAME_SAMPLES, level, np.float32) for level in levels])


def audio_and_ends(pieces):
    ends = sum(piece is SpeechGate.END for piece in pieces)
    samples = sum(len(piece) for piece in pieces if piece is not SpeechGate.END)
    return samples // FRAME_SAMPLES, ends


def test_noise_never_opens():
    gate = SpeechGate(Script())
    assert gate.process(frames(*[0.05] * 300)) == []


def test_short_click_does_not_open():
    gate = SpeechGate(Script())
    assert gate.process(frames(0.0, *[0.9] * (START_FRAMES - 1), 0.0, 0.0)) == []


def test_speech_opens_with_pre_roll_then_closes():
    gate = SpeechGate(Script())
    quiet = [0.0] * (PRE_ROLL_FRAMES + 5)
    voiced = [0.9] * 20
    pieces = gate.process(frames(*quiet, *voiced, *[0.0] * (HANGOVER_FRAMES + 3)))
    count, ends = audio_and_ends(pieces)
    assert ends == 1
    # All voiced frames, the hangover, and pre-roll up to the opening frame.
    assert count == PRE_ROLL_FRAMES + (len(voiced) - START_FRAMES) + HANGOVER_FRAMES
    assert pieces[-1] is SpeechGate.END


def test_short_pause_stays_open():
    gate = SpeechGate(Script())
    pieces = gate.process(frames(*[0.9] * 10, *[0.0] * (HANGOVER_FRAMES - 1), *[0.9] * 10))
    assert audio_and_ends(pieces)[1] == 0


def test_partial_frames_carry_over():
    gate = SpeechGate(Script())
    audio = frames(*[0.9] * 10)
    pieces = []
    for start in range(0, len(audio), 100):
        pieces += gate.process(audio[start:start + 100])
    assert audio_and_ends(pieces) == (10, 0)


def test_tail_of_a_closed_gate_is_kept_for_a_soft_next_start():
    gate = SpeechGate(Script())
    gate.process(frames(*[0.9] * 10, *[0.0] * HANGOVER_FRAMES))
    pieces = gate.process(frames(*[0.9] * START_FRAMES))
    assert audio_and_ends(pieces)[0] == PRE_ROLL_FRAMES
