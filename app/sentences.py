import re

SENTENCE_END = re.compile(r"[.!?…。！？]+[\"”’')\]]*$")
ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "prof", "st", "vs", "etc", "sr", "jr", "hr", "fr", "nr", "z.b", "e.g", "i.e"}

# Words a sentence cannot normally end on. When Whistle puts a full stop after one, the speaker was only pausing.
UNFINISHED = {
    "en": "the a an and but or nor so because if that than of to for with in on at by from as about into is are was were my your our their this these those".split(),
    "es": "el la los las un una y e o u pero que de del a al en con por para porque si sino como es son mi tu su".split(),
    "fr": "le la les un une des et ou mais que qui de du à au aux en pour avec dans sur par parce si comme est sont mon ma mes ton ta son sa ses".split(),
    "de": "der die das den dem des ein eine einen einem einer und oder aber dass weil wenn von zu mit für auf in im an am bei nach ist sind mein dein sein ihr unser".split(),
    "it": "il lo la i gli le un uno una e o ma che di del della a al in con per da su se perché come è sono mio tuo suo".split(),
    "nl": "de het een en of maar dat van in met voor op om aan bij naar omdat als is zijn mijn jouw ons".split(),
    "pl": "i a lub ale że w z na do od dla czy bo o po przez jest są mój twój jego".split(),
}

# Words that only start with a capital because Whistle thought a new sentence began.
OPENERS = {
    "en": "we you he she it they there this these those what when how who which but i'd".split(),
    "es": "el la los las eso esto este esta lo que y pero nosotros ellos hay".split(),
    "fr": "le la les ce cette ces nous vous il elle ils elles on et mais qui que".split(),
    "de": "der die das wir sie er es ich und aber dass dies diese dieser".split(),
    "it": "il la le questo questa noi loro e ma che".split(),
    "nl": "de het een wij we zij ze hij dit deze en maar dat".split(),
    "pl": "to ten ta te my oni i ale że".split(),
}
_UNFINISHED = {lang: set(words) for lang, words in UNFINISHED.items()}
_CONTINUING = {lang: _UNFINISHED[lang] | set(OPENERS[lang]) for lang in UNFINISHED}


def bare(word):
    return word.strip().strip("\"”’'()[]«».,;:!?…").lower()


def ends_sentence(word):
    word = word.strip()
    match = SENTENCE_END.search(word)
    if not match:
        return False
    stem = word[:match.start()].strip("\"”’'([").lower()
    return bool(stem) and stem not in ABBREVIATIONS and not (len(stem) == 1 and stem.isalpha() and stem not in "aiyoe")


def group_sentences(words):
    sentences, current = [], []
    for word in words:
        current.append(word)
        if ends_sentence(word["word"]):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    return sentences


def tidy(text):
    text = " ".join(text.split())
    # Whistle sometimes ends a stretch of speech with a stray closing quote.
    if text.count('"') % 2 and text.endswith('"'):
        text = text[:-1].rstrip()
    return text[:1].upper() + text[1:]


def is_unfinished(text, language):
    words = text.split()
    return bool(words) and bare(words[-1]) in _UNFINISHED.get(language, ())


def join(first, second, language):
    """Rejoins a sentence the speaker paused in the middle of."""
    first = first.rstrip().rstrip(".,;:…\"”")
    head, _, rest = second.partition(" ")
    if bare(head) in _CONTINUING.get(language, ()):
        head = head[:1].lower() + head[1:]
    return f"{first} {head} {rest}".strip()


def continue_sentence(text, language):
    """Removes the capital from a line that carries on the previous one. German nouns keep theirs."""
    head = text.split(" ", 1)[0]
    if language == "de" and bare(head) not in _CONTINUING["de"]:
        return text
    if head == "I" or head.startswith("I'") or head[1:] != head[1:].lower():
        return text
    return text[:1].lower() + text[1:]


class Joiner:
    """Holds a sentence ending on an unfinished word until the speaker carries on, or clearly has not."""

    def __init__(self):
        self.held = None

    def add(self, sentences):
        released = []
        for sentence in sentences:
            if self.held is not None:
                sentence.text = join(self.held.text, sentence.text, sentence.language)
                sentence.seconds += self.held.seconds
                self.held = None
            if is_unfinished(sentence.text, sentence.language):
                self.held = sentence
            else:
                released.append(sentence)
        return released

    def flush(self):
        held, self.held = self.held, None
        return [held] if held else []


def guess_language(text, default="en"):
    """For recognizers that do not report a language: the one whose common words appear most."""
    words = [bare(word) for word in text.split()]
    scores = {lang: sum(word in common for word in words) for lang, common in _CONTINUING.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else default
