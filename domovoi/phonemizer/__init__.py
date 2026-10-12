"""Domovoi's permissive text-to-phoneme converter for its Piper voices.

Turns English text into the phoneme strings (and ids) that Piper voices
were trained on, without espeak-ng: espeak's British "en" set (Domovoi's
three public-domain voices) or its American "en-us" set (most voices
people train themselves). CMUdict, a text normalizer, hand-written rules
and a small model for words in no dictionary. See ``README.md`` beside
this file.

    from domovoi.phonemizer import get_phonemizer, phoneme_ids
    ph = get_phonemizer()
    for sentence in ph.phonemize("Hey Domovoi! It's 72°F."):          # "en"
        ids = phoneme_ids(sentence)
    ph.phonemize("Hey Domovoi!", dialect="en-us")                   # an "en-us" voice
"""
import threading

from .convert import (
    DATA_DIR,
    DEFAULT_DIALECT,
    DIALECTS,
    G2P_MODEL,
    Phonemizer,
    dialect_for_espeak_voice,
)
from .phoneme_ids import default_id_map, fit_to_map, phoneme_ids, speaks_anything, unknown_symbols
from .pronunciations import (
    Pronunciation,
    PronunciationError,
    format_pronunciation_text,
    load_default_pronunciations,
    parse_pronunciation_text,
    parse_say_it_like,
    pronunciation_key,
)

__all__ = [
    "DATA_DIR", "DEFAULT_DIALECT", "DIALECTS", "G2P_MODEL", "Phonemizer", "Pronunciation",
    "PronunciationError", "default_id_map", "dialect_for_espeak_voice", "fit_to_map",
    "format_pronunciation_text", "get_phonemizer", "load_default_pronunciations",
    "parse_pronunciation_text", "parse_say_it_like", "phoneme_ids", "pronunciation_key",
    "speaks_anything", "unknown_symbols",
]

_shared = None
_shared_lock = threading.Lock()


def get_phonemizer():
    """The process-wide converter (loaded once, on first use; blocking)."""
    global _shared
    if _shared is None:
        with _shared_lock:
            if _shared is None:
                _shared = Phonemizer()
    return _shared
