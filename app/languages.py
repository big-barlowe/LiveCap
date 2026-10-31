import os

INPUT_LANGUAGES = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "nl": "Dutch",
    "pl": "Polish",
}

# code: (name, Piper voice or None). Codes are Argos Translate's; "pb" is its Brazilian Portuguese.
# Chinese, Japanese and Korean have no Piper voice because it needs extra phonemizer dependencies for them.
OUTPUT_LANGUAGES = {
    "en": ("English", "en_US-amy-medium"),
    "es": ("Español", "es_ES-davefx-medium"),
    "fr": ("Français", "fr_FR-siwis-medium"),
    "de": ("Deutsch", "de_DE-thorsten-medium"),
    "it": ("Italiano", "it_IT-paola-medium"),
    "nl": ("Nederlands", "nl_NL-pim-medium"),
    "pl": ("Polski", "pl_PL-gosia-medium"),
    "pb": ("Português (Brasil)", "pt_BR-faber-medium"),
    "ru": ("Русский", "ru_RU-irina-medium"),
    "uk": ("Українська", "uk_UA-ukrainian_tts-medium"),
    "cs": ("Čeština", "cs_CZ-jirka-medium"),
    "sv": ("Svenska", "sv_SE-nst-medium"),
    "da": ("Dansk", "da_DK-talesyntese-medium"),
    "fi": ("Suomi", "fi_FI-harri-medium"),
    "el": ("Ελληνικά", "el_GR-rapunzelina-medium"),
    "ro": ("Română", "ro_RO-mihai-medium"),
    "hu": ("Magyar", "hu_HU-anna-medium"),
    "tr": ("Türkçe", "tr_TR-dfki-medium"),
    "ar": ("العربية", "ar_JO-kareem-medium"),
    "hi": ("हिन्दी", "hi_IN-pratham-medium"),
    "vi": ("Tiếng Việt", "vi_VN-vais1000-medium"),
    "zh": ("中文", None),
    "ja": ("日本語", None),
    "ko": ("한국어", None),
}

# Our code to Supertonic's, for languages it can read. It has no Chinese.
SUPERTONIC_LANGUAGES = {code: code for code in OUTPUT_LANGUAGES if code not in ("zh", "pb")} | {"pb": "pt"}

RTL_LANGUAGES = {"ar"}

_allowed = {code.strip() for code in os.environ.get("LIVECAP_LANGUAGES", "").split(",") if code.strip()}

# What viewers may choose. Limiting this bounds disk use and how many models a small machine can be asked to load.
AVAILABLE_OUTPUTS = {code: entry for code, entry in OUTPUT_LANGUAGES.items() if not _allowed or code in _allowed}


def voice_for(lang):
    entry = OUTPUT_LANGUAGES.get(lang)
    return entry[1] if entry else None
