"""Phoneme string -> the integer ids a Piper voice reads.

The rule is Piper's own: the string is NFD-normalized and read one code
point at a time; the ids are ``^ _`` (begin, pad), then each known code
point's ids followed by the pad, then ``$`` (end). A code point the map
lacks is skipped. Domovoi's three voices share one map
(``data/phoneme_ids_en.json``); a voice's own ``.onnx.json`` map can be
passed instead.
"""
import json
import os
import unicodedata
from functools import lru_cache

BOS, EOS, PAD = "^", "$", "_"
_MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "phoneme_ids_en.json")


@lru_cache(maxsize=1)
def default_id_map():
    """The id map shared by norman, kristin and ljspeech (code point -> ids)."""
    with open(_MAP_FILE, encoding="utf-8") as f:
        return json.load(f)["phoneme_id_map"]


def phoneme_ids(phonemes, id_map=None):
    """One sentence's phoneme string -> its ids, Piper's way."""
    m = id_map if id_map is not None else default_id_map()
    pad = list(m[PAD])
    ids = list(m[BOS]) + pad
    for ch in unicodedata.normalize("NFD", phonemes):
        got = m.get(ch)
        if got is None:
            continue
        ids += got
        ids += pad
    ids += m[EOS]
    return ids


def unknown_symbols(phonemes, id_map=None):
    """Code points of ``phonemes`` (NFD) the map lacks, in order, once each."""
    m = id_map if id_map is not None else default_id_map()
    out = []
    for ch in unicodedata.normalize("NFD", phonemes):
        if ch not in m and ch not in out:
            out.append(ch)
    return out
