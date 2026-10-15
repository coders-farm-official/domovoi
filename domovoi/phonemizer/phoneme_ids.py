"""Phoneme string -> the integer ids a Piper voice reads.

The rule is Piper's own: the string is NFD-normalized and read one code
point at a time; the ids are ``^ _`` (begin, pad), then each known code
point's ids followed by the pad, then ``$`` (end). A code point the map
lacks is skipped. Domovoi's three voices share one map
(``data/phoneme_ids_en.json``, Piper's espeak map, which the en-us voices
seen so far carry too); a voice's own ``.onnx.json`` map can be passed
instead, and :func:`fit_to_map` first puts the nearest sound it has in
place of a symbol it lacks.
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


# What a symbol becomes for a voice whose map lacks it: the first candidate
# whose every code point the map has, else nothing. Every Piper voice seen
# so far (the shipped three, the owner's en-us ones) has all of them; this
# is for a voice trained on a smaller set, so it says the nearest sound
# instead of skipping it (and never stops speaking over it).
FALLBACKS = {
    "ɾ": ("t", "d"), "ᵻ": ("ɪ", "i", "ə"), "ɚ": ("əɹ", "ər", "ə"), "ɐ": ("ə", "a"), "æ": ("a", "ɛ"),
    "a": ("æ", "ɐ"), "ɒ": ("ɑ", "ɔ", "o"), "ɔ": ("ɒ", "o"), "ɑ": ("a", "ɒ"), "ɜ": ("ə", "ɛ"),
    "ʌ": ("ə", "a"), "ə": ("ɐ", "e"), "ɛ": ("e",), "e": ("ɛ",), "ɪ": ("i",), "i": ("ɪ",), "ʊ": ("u",),
    "u": ("ʊ",), "o": ("ɔ", "ʊ"), "ɹ": ("r", "ɾ"), "ɡ": ("g",), "ŋ": ("n",), "θ": ("t",), "ð": ("d",),
    "ʃ": ("s",), "ʒ": ("z", "ʃ"), "ː": ("",), "ˈ": ("",), "ˌ": ("",), "j": ("i",),
}


def fit_to_map(phonemes, id_map, memo=None):
    """``phonemes`` (NFD) with each code point ``id_map`` lacks replaced by
    its first :data:`FALLBACKS` candidate the map has wholly, else dropped.
    ``memo`` (a dict kept per voice): missing code point -> what took its
    place ("" when dropped), filled as they turn up."""
    memo = {} if memo is None else memo
    out = []
    for ch in unicodedata.normalize("NFD", phonemes):
        if ch in id_map:
            out.append(ch)
            continue
        sub = memo.get(ch)
        if sub is None:
            sub = next((c for c in FALLBACKS.get(ch, ()) if all(x in id_map for x in c)), "")
            memo[ch] = sub
        out.append(sub)
    return "".join(out)


def speaks_anything(ids, id_map=None):
    """Whether a sentence's ids hold any sound: something besides the
    begin, end and pad ids and the map's space and punctuation."""
    m = id_map if id_map is not None else default_id_map()
    quiet = set()
    for sym in (BOS, EOS, PAD, " ", ",", ".", "!", "?", ";", ":", "ˈ", "ˌ"):
        quiet.update(m.get(sym, ()))
    return any(i not in quiet for i in ids)
