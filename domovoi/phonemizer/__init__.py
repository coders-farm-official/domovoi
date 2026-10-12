"""Domovoi's permissive text-to-phoneme converter for its Piper voices.

Turns English text into the phoneme strings (and ids) that Domovoi's three
public-domain voices were trained on, without espeak-ng: CMUdict, a text
normalizer, hand-written rules and a small model for words in no
dictionary. See ``README.md`` beside this file.

    from domovoi.phonemizer import get_phonemizer, phoneme_ids
    ph = get_phonemizer()
    for sentence in ph.phonemize("Hey Domovoi! It's 72°F."):
        ids = phoneme_ids(sentence)
"""
import threading

from .convert import DATA_DIR, G2P_MODEL, Phonemizer
from .phoneme_ids import default_id_map, phoneme_ids, unknown_symbols
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
    "DATA_DIR", "G2P_MODEL", "Phonemizer", "Pronunciation", "PronunciationError",
    "default_id_map", "format_pronunciation_text", "get_phonemizer",
    "load_default_pronunciations", "parse_pronunciation_text", "parse_say_it_like",
    "phoneme_ids", "pronunciation_key", "unknown_symbols",
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
