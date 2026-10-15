"""The owner's "say it like" list: word -> a respelling in plain English.

Format (one entry per line in text form; ``#`` starts a comment line)::

    Nguyen = win
    Domovoi = DOH-moe-voy
    Siobhan = shiv-AWN
    Domovoi = /dˈəʊməʊvɔɪ/        (advanced: the voice's own phonemes)

Left: one word as it appears in text (letters, with ``'`` or ``-`` inside),
matched without regard to case or accents; ``Word's`` follows ``Word``.
Right: plain English spelling. Hyphens or spaces split it into syllables;
each syllable is read the way the converter reads that spelling as a word
on its own (dictionary, then the model), never through this list again. The
syllable written in CAPITALS takes the main stress, and the other syllables
are said lighter (an unstressed "uh" or "er" becomes a schwa); with no
capitals the first syllable takes the stress. Between slashes, the text is
used as the voice's phoneme string unchanged (every symbol must be one the
voices know).

The resolving (spelling -> ARPAbet) lives in :mod:`.convert`, which owns
the dictionary and the model; this module is the parsing and checking the
settings page and the Kotlin port share.
"""
import os
import re
import unicodedata
from dataclasses import dataclass, replace

from .normalize import TOKEN_RE, strip_accents
from .phoneme_ids import BOS, EOS, PAD, default_id_map

MAX_WORD_CHARS = 48
MAX_SAY_CHARS = 100
MAX_SYLLABLES = 12
DEFAULT_FILE = "pronunciations_default.txt"
_PIECE_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*'?")


class PronunciationError(ValueError):
    """An entry the converter can't use; the message says which and why."""


@dataclass(frozen=True)
class Pronunciation:
    word: str                 # as the owner wrote it
    key: str                  # what text is matched on: accents stripped, lower case
    say_it_like: str          # as the owner wrote it
    pieces: tuple = ()        # the respelling's syllables (empty for the /phonemes/ form)
    stressed: int = 0         # index of the syllable with the main stress
    has_capitals: bool = False
    ipa: str | None = None    # the /phonemes/ form, slashes removed
    phones: tuple | None = None  # ARPAbet, once resolved by the converter

    @property
    def spelling(self):
        """The respelling's letters, run together (vowel-spelling cues for IPA)."""
        return "".join(self.pieces).lower()

    def with_phones(self, phones):
        return replace(self, phones=tuple(phones))


def pronunciation_key(word):
    """The normalized word an entry matches, or PronunciationError."""
    w = (word or "").strip()
    if not w:
        raise PronunciationError("the word is empty")
    if len(w) > MAX_WORD_CHARS:
        raise PronunciationError(f"{w[:20]}...: a word is at most {MAX_WORD_CHARS} letters")
    if not TOKEN_RE.fullmatch(w) or w.startswith("<") or not any(c.isalpha() for c in w):
        raise PronunciationError(
            f"{w}: one word only (letters, with ' or - inside), as it is written in text")
    return strip_accents(unicodedata.normalize("NFC", w)).lower()


def parse_say_it_like(word, say):
    """Check one entry; a :class:`Pronunciation` without ``phones`` (the
    converter resolves a respelling), or PronunciationError."""
    key = pronunciation_key(word)
    s = (say or "").strip()
    if not s:
        raise PronunciationError(f"{word}: say it like what?")
    if len(s) > MAX_SAY_CHARS:
        raise PronunciationError(f"{word}: at most {MAX_SAY_CHARS} characters")
    if s.startswith("/"):
        if len(s) < 3 or not s.endswith("/"):
            raise PronunciationError(f"{word}: phonemes go between two slashes, like /wɪn/")
        ipa = s[1:-1].strip()
        known = default_id_map()
        bad = []
        for ch in unicodedata.normalize("NFD", ipa):
            if (ch not in known or ch in (BOS, EOS, PAD)) and ch not in bad:
                bad.append(ch)
        if not ipa or bad:
            raise PronunciationError(
                f"{word}: the voices don't know {' '.join(repr(c) for c in bad) or 'an empty phoneme string'}")
        return Pronunciation(word=word.strip(), key=key, say_it_like=s, ipa=ipa)
    pieces = tuple(p for p in re.split(r"[\s\-]+", s) if p)
    if len(pieces) > MAX_SYLLABLES:
        raise PronunciationError(f"{word}: at most {MAX_SYLLABLES} syllables")
    for p in pieces:
        if not _PIECE_RE.fullmatch(p):
            raise PronunciationError(
                f"{word}: write {s!r} in plain letters (a-z), syllables split by - or spaces")
    upper = [p.isupper() for p in pieces]
    has_capitals = any(upper) and not all(upper)
    stressed = upper.index(True) if has_capitals else 0
    return Pronunciation(word=word.strip(), key=key, say_it_like=s, pieces=pieces,
                         stressed=stressed, has_capitals=has_capitals)


# A respelling syllable no dictionary knows ("shuh", "zhahn", "voy") is read
# with this key before the out-of-dictionary model, which reads it as a word
# ("shuh" came out SH UW1, so "SEER-shuh" said "SEER-shoo"): the conventions
# of dictionaries' and Wikipedia's pronunciation respellings ("uh" as in
# but, "ay" as in say, "ow" as in now, "zh" as in measure, "dh" as in this).
# A syllable is consonants, one vowel spelling, consonants; anything else
# (two vowels, an apostrophe) is left to the model. Longest spelling first.
RESPELL_VOWELS = {
    "eye": "AY", "igh": "AY", "air": "EH R", "ear": "IH R", "eer": "IH R", "oor": "UH R", "our": "AW R",
    "ahr": "AA R", "ah": "AA", "ai": "EY", "ar": "AA R", "au": "AO", "aw": "AO", "ay": "EY",
    "ea": "IY", "ee": "IY", "eh": "EH", "er": "ER", "ew": "Y UW", "ih": "IH", "ir": "ER",
    "oa": "OW", "oe": "OW", "oh": "OW", "oi": "OY", "oo": "UW", "or": "AO R", "ou": "AW", "ow": "AW",
    "oy": "OY", "uh": "AH", "ur": "ER", "uu": "UH",
    "a": "AE", "e": "EH", "i": "IH", "o": "AA", "u": "AH",
}
# A lone vowel letter ending the syllable: "pa", "mo", "ki", "zu".
RESPELL_OPEN_VOWELS = {"a": "AA", "e": "EH", "i": "IY", "o": "OW", "u": "UW"}
RESPELL_CONSONANTS = {
    "dzh": "JH", "tch": "CH", "ch": "CH", "ck": "K", "dh": "DH", "dz": "D Z", "gh": "G", "kh": "K",
    "ng": "NG", "nk": "NG K", "ph": "F", "qu": "K W", "sh": "SH", "th": "TH", "ts": "T S", "wh": "W",
    "zh": "ZH",
    "b": "B", "c": "K", "d": "D", "f": "F", "g": "G", "h": "HH", "j": "JH", "k": "K", "l": "L",
    "m": "M", "n": "N", "p": "P", "q": "K", "r": "R", "s": "S", "t": "T", "v": "V", "w": "W",
    "x": "K S", "y": "Y", "z": "Z",
}
_RESPELL_LETTER_VOWELS = "aeiou"


def _longest(s, i, table):
    for n in (3, 2, 1):
        if s[i:i + n] in table and len(s[i:i + n]) == n:
            return s[i:i + n]
    return None


def respell_arpa(syllable):
    """One respelling syllable -> ARPAbet by the key above (its vowel with
    stress 1, a doubled sound said once), or None when it is not
    consonants, one vowel spelling, consonants."""
    s = syllable.lower()
    if not s or not all("a" <= c <= "z" for c in s):
        return None
    onset, coda, i = [], [], 0
    while i < len(s) and s[i] not in _RESPELL_LETTER_VOWELS:
        m = _longest(s, i, RESPELL_CONSONANTS)
        onset += RESPELL_CONSONANTS[m].split()
        i += len(m)
    if i == len(s):
        return None
    v = _longest(s, i, RESPELL_VOWELS)
    i += len(v)
    while i < len(s):
        if s[i] in _RESPELL_LETTER_VOWELS:
            return None
        m = _longest(s, i, RESPELL_CONSONANTS)
        coda += RESPELL_CONSONANTS[m].split()
        i += len(m)
    vowel = (RESPELL_OPEN_VOWELS[v] if len(v) == 1 and not coda else RESPELL_VOWELS[v]).split()
    vowel = [p + "1" if p in ("AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY",
                              "UH", "UW") else p for p in vowel]
    out = []
    for p in onset + vowel + coda:
        if not out or out[-1].rstrip("012") != p.rstrip("012"):
            out.append(p)
    return out


def parse_pronunciation_text(text):
    """``Word = say it like`` lines -> [(word, say_it_like)], in order.
    Blank lines and ``#`` comment lines are skipped. Only the shape is
    checked here; the converter checks each entry when it takes the list."""
    out = []
    for n, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word, eq, say = line.partition("=")
        if not eq or not word.strip() or not say.strip():
            raise PronunciationError(f"line {n}: expected 'Word = say it like', got {line!r}")
        out.append((word.strip(), say.strip()))
    return out


def format_pronunciation_text(pairs):
    """[(word, say_it_like)] -> the text form, one entry per line."""
    return "".join(f"{w} = {s}\n" for w, s in pairs)


def load_default_pronunciations(data_dir):
    """The shipped default list (``data/pronunciations_default.txt``)."""
    with open(os.path.join(data_dir, DEFAULT_FILE), encoding="utf-8") as f:
        return parse_pronunciation_text(f.read())
