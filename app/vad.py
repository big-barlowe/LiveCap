from collections import deque

import numpy as np

FRAME_SAMPLES = 512  # 32 ms at 16 kHz, the size Silero works in
FRAME_SECONDS = FRAME_SAMPLES / 16000
START_PROBABILITY = 0.5
CONTINUE_PROBABILITY = 0.35
START_FRAMES = 5  # about 160 ms of voice opens the gate, so a click does not
PRE_ROLL_FRAMES = 19  # about 600 ms kept from before the gate opens, so first syllables survive
HANGOVER_FRAMES = 25  # about 800 ms without voice closes it


class SpeechGate:
    """Passes only speech on to Whistle, which can invent words like "Thank you" from breaths and clicks.

    process() returns a list of pieces in order: float32 sample arrays while speech is open, and END when it closes.
    """

    END = object()

    def __init__(self, detector=None):
        if detector is None:
            from pysilero_vad import SileroVoiceActivityDetector

            detector = SileroVoiceActivityDetector()
        self.detector = detector
        self.reset()

    def reset(self):
        self.speaking = False
        self.voiced = 0
        self.unvoiced = 0
        self.pre_roll = deque(maxlen=PRE_ROLL_FRAMES)
        self.remainder = np.zeros(0, np.float32)

    def process(self, samples):
        data = np.concatenate((self.remainder, samples))
        count = len(data) // FRAME_SAMPLES
        self.remainder = data[count * FRAME_SAMPLES:]
        pieces, open_frames = [], []

        def close_audio():
            if open_frames:
                pieces.append(np.concatenate(open_frames))
                open_frames.clear()

        for index in range(count):
            frame = data[index * FRAME_SAMPLES:(index + 1) * FRAME_SAMPLES]
            pcm = (np.clip(frame, -1, 1) * 32767).astype("<i2").tobytes()
            probability = self.detector(pcm)
            if self.speaking:
                open_frames.append(frame)
                self.unvoiced = self.unvoiced + 1 if probability < CONTINUE_PROBABILITY else 0
                if self.unvoiced >= HANGOVER_FRAMES:
                    # A soft first syllable can sit in this tail, under the detector's threshold.
                    self.pre_roll.extend(open_frames[-PRE_ROLL_FRAMES:])
                    close_audio()
                    pieces.append(self.END)
                    self.speaking = False
                    self.voiced = 0
            else:
                self.pre_roll.append(frame)
                self.voiced = self.voiced + 1 if probability >= START_PROBABILITY else 0
                if self.voiced >= START_FRAMES:
                    self.speaking = True
                    self.unvoiced = 0
                    open_frames.extend(self.pre_roll)
                    self.pre_roll.clear()
        close_audio()
        return pieces
