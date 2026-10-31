import logging
import os
import platform
import sys

log = logging.getLogger("livecap.recognizers")

# The Mac app keeps its own copy of the model; otherwise it comes from Hugging Face.
TARGET_PEAK = 0.5  # quiet speakers lose first words, so speech is brought up to this level before recognition
MAX_GAIN = 20.0
PARAKEET_MODEL = os.environ.get("LIVECAP_PARAKEET_DIR", "mlx-community/parakeet-tdt-0.6b-v3")


class Parakeet:
    """Runs on the Apple GPU. Far more accurate than Whistle, but needs Apple Silicon and about 1.5 GB of memory."""

    name = "parakeet"
    read_every = 1.0  # fast enough to re-read every second

    def __init__(self):
        from parakeet_mlx import from_pretrained

        self.model = from_pretrained(PARAKEET_MODEL)

    def __call__(self, samples, language):
        import mlx.core as mx
        from parakeet_mlx.audio import get_logmel

        gain = min(TARGET_PEAK / max(float(abs(samples).max()), 1e-6), MAX_GAIN)
        mel = get_logmel(mx.array(samples * gain), self.model.preprocessor_config)
        result = self.model.generate(mel)[0]
        words = []
        for sentence in result.sentences:
            for token in sentence.tokens:
                # Tokens are pieces of words; a leading space marks where a new word starts.
                if token.text.startswith(" ") or not words:
                    words.append({"word": token.text.strip(), "start": token.start, "end": token.end})
                else:
                    words[-1]["word"] += token.text
                    words[-1]["end"] = token.end
        return {"words": [word for word in words if word["word"]], "language": ""}


class Whistle:
    """Runs on any CPU in very little memory."""

    name = "whistle"
    read_every = 3.0

    def __init__(self):
        from needle.agent.whistle import Whistle as Model

        self.model = Model()

    def __call__(self, samples, language):
        return self.model.transcribe(samples, language=language, word_timestamps=True)


def create():
    choice = os.environ.get("LIVECAP_RECOGNIZER")
    if not choice:
        apple_silicon = sys.platform == "darwin" and platform.machine() == "arm64"
        choice = "parakeet" if apple_silicon else "whistle"
    log.info("loading %s", choice)
    return Parakeet() if choice == "parakeet" else Whistle()
