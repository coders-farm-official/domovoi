"""Spoken forms of music-library names: one normalization for every user.

Whisper writes what it hears ("Suicide Boys", "21 Pilots", "Jay-Z"); the
library stores how an artist styles the name ("$uicideboy$", "twenty one
pilots", "JAŸ-Z"). A substring search between the two misses, so every
place that compares a spoken name with a library name goes through here:

* the spoken-name resolver (:mod:`domovoi.handlers.shared.library_match`)
  indexes every library name under all of its :func:`spoken_forms` and
  scores a transcript's forms against them (exact, fuzzy, sounds-alike);
* the household "also called" table (``library_aliases``, migration V019)
  keys every alias by :func:`alias_key`: two aliases whose keys are equal
  ARE the same alias, which is what makes "subtract" mean one thing;
* the MusicBrainz alias fetch accepts a MusicBrainz artist only when one of
  its names :func:`same_name` the library spelling, and keeps a fetched
  alias for a person only when it :func:`sounds_like` the name.

The rules are general ones, written down before any evaluation set was
read: fold accents and lookalike letters, read ``$`` as s and a ``!``
inside a word as i, ``&``/``+`` as "and", join dotted initials, read
numbers as words (both ways round, because Whisper writes "21 Pilots" for
a library's "twenty one pilots"), and read a single digit used inside a
word the three ways people use one (as a letter, ``5`` → s; as its number
word, ``n9ne`` → nine; as a sound, ``gener8ion`` → generation), merging
the letters it overlaps. Deliberately absent, because the 2026-10-02 audit
found them fitted to single test items: names for individual symbols
(``÷`` "divide", ``º`` "degrees", ``#`` "number"), a slang table
(tha/luv/u/n), comma-grouped thousands, and alternative "play" verbs.

``alias_key`` is FROZEN at :data:`KEY_VERSION`: stored keys are compared
with it. Changing what it returns for any input means bumping
``KEY_VERSION`` and re-keying ``library_aliases`` (the rows carry the
version they were computed with). :func:`spoken_forms` may grow new
variants freely — nothing stores them.

Pure functions, no I/O; safe to import from the web process.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from itertools import product

from anyascii import anyascii
from metaphone import doublemetaphone
from rapidfuzz import fuzz

#: Version of :func:`alias_key`. Stored with every ``library_aliases`` row.
KEY_VERSION = 1

#: Most spoken forms kept per name (the primary reading first).
MAX_FORMS = 8

#: :func:`sounds_like`'s spelling floor (rapidfuzz ratio, 0-100).
SOUNDS_LIKE_MIN_RATIO = 80

# ─── Numbers as words ──────────────────────────────────────────────────────

_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
)
_TENS = (
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety",
)
_SCALES = ((10**9, "billion"), (10**6, "million"), (1000, "thousand"))
_IRREGULAR_ORDINALS = {
    "one": "first", "two": "second", "three": "third", "five": "fifth",
    "eight": "eighth", "nine": "ninth", "twelve": "twelfth",
}


def int_to_words(n: int) -> str:
    """Cardinal English words, no "and": 182 → "one hundred eighty two"."""
    if n < 0:
        return "minus " + int_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + ("" if ones == 0 else " " + _ONES[ones])
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return _ONES[hundreds] + " hundred" + ("" if rest == 0 else " " + int_to_words(rest))
    for div, name in _SCALES:
        if n >= div:
            q, rest = divmod(n, div)
            return int_to_words(q) + " " + name + ("" if rest == 0 else " " + int_to_words(rest))
    raise AssertionError("unreachable")  # pragma: no cover


def int_to_ordinal_words(n: int) -> str:
    """21 → "twenty first", 3 → "third", 40 → "fortieth"."""
    words = int_to_words(n).split()
    last = words[-1]
    if last in _IRREGULAR_ORDINALS:
        last = _IRREGULAR_ORDINALS[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last = last + "th"
    return " ".join(words[:-1] + [last])


def _paired_reading(digits: str) -> str | None:
    """How a 3- or 4-digit number is often SAID: 182 → "one eighty two",
    1999 → "nineteen ninety nine", 3700 → "thirty seven hundred",
    2005 → "twenty oh five". None when it reads no differently."""
    if not digits.isdigit() or digits[0] == "0" or len(digits) not in (3, 4):
        return None
    head, tail = digits[:-2], digits[-2:]
    if tail == "00":
        if len(digits) == 3:
            return None  # "one hundred" is the cardinal already
        return int_to_words(int(head)) + " hundred"
    if tail[0] == "0":
        return int_to_words(int(head)) + " oh " + _ONES[int(tail[1])]
    return int_to_words(int(head)) + " " + int_to_words(int(tail))


# ─── Folding and symbols ───────────────────────────────────────────────────

# Dotted initials, two or more: "t.i." / "u.s.d.a" / "r. e. m." / "b.o.b".
_DOTTED_RE = re.compile(r"(?<![a-z0-9])((?:[a-z]\.\s?)+[a-z])\.?(?![a-z0-9])")
_APOSTROPHE_RE = re.compile(r"['`´‘’]")
_ORDINAL_RE = re.compile(r"\b(\d+)(st|nd|rd|th)\b")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def fold(text: str | None) -> str:
    """NFKC, then transliterate to ASCII (anyascii: "JAŸ-Z" → "JAY-Z",
    "Łaszewo" → "Laszewo", "MαriΩ" → "MariO", math-styled letters → plain),
    then lower-case. Punctuation is kept for the later steps."""
    s = unicodedata.normalize("NFKC", text or "")
    return anyascii(s).lower()


def _clean(folded: str) -> str:
    """The symbol rules on a folded string → space-separated [a-z0-9]
    tokens. Numbers are still digits here."""
    s = _DOTTED_RE.sub(lambda m: re.sub(r"[.\s]", "", m.group(1)), folded)
    s = _APOSTROPHE_RE.sub("", s)
    s = s.replace("&", " and ").replace("+", " and ")
    s = re.sub(r"(?<=[a-z0-9])@(?=[a-z0-9])", "a", s).replace("@", " at ")
    s = s.replace("$", "s")
    s = re.sub(r"(?<=[a-z])!(?=[a-z])", "i", s)
    s = re.sub(r"(?<=[a-z0-9])\*+(?=[a-z0-9])", "", s)
    s = _ORDINAL_RE.sub(lambda m: " " + int_to_ordinal_words(int(m.group(1))) + " ", s)
    return _NON_ALNUM_RE.sub(" ", s).strip()


# ─── Digits ────────────────────────────────────────────────────────────────

# A single digit standing in for letters inside a word.
_LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "6": "b", "7": "t", "9": "g"}
# ...or for the sound of its name.
_SOUND = {"2": "to", "4": "for", "8": "ate"}
_VOWELS = "aeiouy"


def _merge(left: str, sub: str, right: str, *, silent_e: bool = False) -> str:
    """Join letters + substitution + letters, dropping the letters the
    substitution repeats: "n" + "nine" + "ne" → "nine", "se" + "seven" + "en"
    → "seven". With ``silent_e`` a substitution ending in a silent e loses
    it before a vowel, the English suffix rule ("gener" + "ate" + "ion" →
    "generation", like create → creation)."""
    for k in range(min(len(left), len(sub) - 1), 0, -1):
        if left.endswith(sub[:k]):
            left = left[:-k]
            break
    for k in range(min(len(right), len(sub) - 1), 0, -1):
        if right.startswith(sub[-k:]):
            right = right[k:]
            break
    if silent_e and right and sub.endswith("e") and right[0] in _VOWELS:
        sub = sub[:-1]
    return left + sub + right


def _number_variants(digits: str) -> list[str]:
    """A whole-number token: the cardinal first, then the paired reading."""
    out = [int_to_words(int(digits))]
    paired = _paired_reading(digits)
    if paired:
        out.append(paired)
    return out


def _render_mixed(runs: list[str], reading, silent_e: bool) -> str | None:
    """One reading of a mixed token's runs: every multi-digit run as
    spaced number words, every single digit replaced by ``reading(digit)``
    and merged with the letters on both sides. None when ``reading`` has
    nothing for one of the digits."""
    acc = ""
    i = 0
    while i < len(runs):
        run = runs[i]
        if run.isdigit() and len(run) > 1:
            acc += " " + int_to_words(int(run)) + " "
            i += 1
            continue
        if run.isdigit():
            sub = reading(run)
            if sub is None:
                return None
            right = runs[i + 1] if i + 1 < len(runs) and runs[i + 1].isalpha() else ""
            left = re.search(r"[a-z]*$", acc).group(0)
            acc = acc[: len(acc) - len(left)] + _merge(left, sub, right, silent_e=silent_e)
            i += 2 if right else 1
            continue
        acc += run
        i += 1
    return " ".join(acc.split())


def _mixed_variants(tok: str) -> list[str]:
    """A token with letters AND digits ("n9ne", "gener8ion", "2pac",
    "h2o", "splurge3700"). The raw token first (it is how Whisper writes
    the names it has learned: "U2", "M2M", "Blink-182"), then the readings.
    A run of two or more digits is a number and reads as words; a single
    digit next to letters reads as a letter, as its number word and as its
    sound, each merged with the letters around it. A short token is also
    read spelled out ("h2o" → "h two o")."""
    runs = re.findall(r"\d+|[a-z]+", tok)
    out: dict[str, None] = {tok: None}
    for reading, silent_e in (
        (_LEET.get, False),
        (lambda d: _ONES[int(d)], False),
        (_SOUND.get, True),
    ):
        rendered = _render_mixed(runs, reading, silent_e)
        if rendered:
            out.setdefault(rendered, None)
    if len(tok) <= 4:
        spelled = " ".join(
            int_to_words(int(r)) if r.isdigit() else " ".join(r) for r in runs
        )
        out.setdefault(" ".join(spelled.split()), None)
    return list(out)


def _token_variants(tok: str) -> list[str]:
    """Readings of one cleaned token, the primary (key-defining) one first."""
    if tok.isdigit():
        variants = _number_variants(tok)
        if len(tok) == 1 and tok in _SOUND:
            variants.append(_SOUND[tok])
        return variants
    if any(c.isdigit() for c in tok):
        return _mixed_variants(tok)
    return [tok]


# ─── Public API ────────────────────────────────────────────────────────────


def compact(form: str) -> str:
    """A spoken form with its spaces removed — what exact matching compares."""
    return form.replace(" ", "")


def _drop_leading_the(form: str) -> str:
    return form[4:] if form.startswith("the ") and len(form) > 4 else form


@lru_cache(maxsize=200_000)
def spoken_forms(text: str) -> tuple[str, ...]:
    """Every way ``text`` may be said, as space-separated lower-case ASCII
    words; the primary reading first, at most :data:`MAX_FORMS`, each also
    without a leading "the". Empty tuple for a name with nothing sayable.

    "$uicideboy$" → ("suicideboys",); "Tech N9ne" → ("tech n9ne",
    "tech ngne", "tech nine", ...); "twenty one pilots" and "21 Pilots"
    share "twenty one pilots"."""
    tokens = _clean(fold(text)).split()
    if not tokens:
        return ()
    alts = [_token_variants(t) for t in tokens]
    # Fewest departures from the primary reading first.
    combos = list(product(*[range(len(a)) for a in alts]))
    combos.sort(key=lambda idx: sum(1 for i in idx if i))
    forms: dict[str, None] = {}
    for idx in combos:
        form = " ".join(" ".join(alts[t][i] for t, i in enumerate(idx)).split())
        forms.setdefault(form, None)
        if len(forms) >= MAX_FORMS:
            break
    out: dict[str, None] = {}
    for form in forms:
        out.setdefault(form, None)
        bare = _drop_leading_the(form)
        if bare != form:
            out.setdefault(bare, None)
    return tuple(out)


def alias_key(text: str) -> str:
    """The household-wide identity of a name or an alias (FROZEN, see
    :data:`KEY_VERSION`): the primary spoken form, leading "the" dropped,
    spaces removed. "Subtract", "subtract!" and " SUBTRACT " share
    "subtract"; "21 Pilots" and "twenty-one pilots" share
    "twentyonepilots". Empty for a name with nothing sayable."""
    forms = spoken_forms(text)
    if not forms:
        return ""
    return compact(_drop_leading_the(forms[0]))


@lru_cache(maxsize=200_000)
def phonetic_key(form: str) -> str:
    """Double Metaphone (primary code) of each word of a spoken form,
    concatenated: "dead mouse" → "TTMS"."""
    return "".join(doublemetaphone(w)[0] for w in form.split())


def compact_forms(text: str) -> frozenset[str]:
    """The :func:`compact` of every spoken form of ``text``."""
    return frozenset(compact(f) for f in spoken_forms(text))


def same_name(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` share a spoken form: "Suicide Boys" and
    "$uicideboy$", "Pink" and "P!nk", "Tech Nine" and "Tech N9ne"."""
    fa, fb = compact_forms(a), compact_forms(b)
    return bool(fa and fb and fa & fb)


def sounds_like(a: str, b: str, *, min_ratio: int = SOUNDS_LIKE_MIN_RATIO) -> bool:
    """True when ``a`` is plausibly a way of SAYING ``b``: a shared spoken
    form, a spelling within ``min_ratio`` (rapidfuzz ratio of the compact
    forms), or the same Double Metaphone key. "Dead Mouse" sounds like
    "deadmau5"; "Alecia Moore" does not sound like "P!nk"."""
    fa, fb = spoken_forms(a), spoken_forms(b)
    if not fa or not fb:
        return False
    ca, cb = {compact(f) for f in fa}, {compact(f) for f in fb}
    if ca & cb:
        return True
    if max(fuzz.ratio(x, y) for x in ca for y in cb) >= min_ratio:
        return True
    pa = {phonetic_key(f) for f in fa} - {""}
    pb = {phonetic_key(f) for f in fb} - {""}
    return bool(pa & pb)


# ─── Speakable readback ───────────────────────────────────────────────────

_SPEAK_DOTTED_RE = re.compile(r"(?<![A-Za-z0-9])((?:[A-Za-z]\.\s?)+[A-Za-z])\.?(?![A-Za-z0-9])")


def speakable(text: str | None) -> str:
    """A library name made sayable by a TTS voice, for readback ("Playing
    …", "Did you mean …?") when the household has no spoken alias for it:
    transliterated, ``$`` read as s and an in-word ``!`` as i, ``&``/``+``
    as "and", dotted initials spaced so they are spelled ("T.I." → "T I"),
    bracketed and "feat." noise dropped. Case is kept."""
    s = anyascii(unicodedata.normalize("NFKC", text or ""))
    s = clean_title(s)
    s = _SPEAK_DOTTED_RE.sub(lambda m: " ".join(re.sub(r"[.\s]", "", m.group(1))), s)
    s = s.replace("$", "s")
    s = re.sub(r"(?<=[A-Za-z])!(?=[A-Za-z])", "i", s)
    s = re.sub(r"\s*&\s*", " and ", s)
    s = re.sub(r"\s+\+\s+", " and ", s)
    s = re.sub(r"(?<=\s)@(?=\s)", "at", s)
    s = re.sub(r"[_*~^|<>{}\[\]\"]+", " ", s)
    s = " ".join(s.split())
    return s or (text or "").strip()


# ─── Library names ─────────────────────────────────────────────────────────

_BRACKETED_RE = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]")
_FEAT_TAIL_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s.*$", re.I)
_DASH_NOISE_RE = re.compile(
    r"\s+[-–—]\s+[^-–—]*\b(?:remaster(?:ed)?|live|version|edit|mono|stereo|"
    r"mix|remix|demo|radio|single|bonus|acoustic|instrumental|explicit|clean|official|"
    r"video|audio|lyrics?)\b[^-–—]*$",
    re.I,
)
_CREDIT_SPLIT_RE = re.compile(
    r"\s*(?:,|;|\s&\s|\s(?:feat\.?|ft\.?|featuring|x|vs\.?)\s)\s*", re.I
)
_ARTIST_IN_TITLE_RE = re.compile(r"^(.+?)(?:\s+[-–—]\s+|_\s+)(.+)$")


def clean_title(title: str | None) -> str:
    """A title or album without its decoration: bracketed parts
    ("(Official Video)", "[Remastered 2011]", "(feat. X)"), a "feat." tail
    and a " - Remastered"-style tail. Returns the input stripped when
    nothing would be left."""
    raw = (title or "").strip()
    t = _BRACKETED_RE.sub("", raw)
    t = _FEAT_TAIL_RE.sub("", t)
    t = _DASH_NOISE_RE.sub("", t)
    t = " ".join(t.split())
    return t or raw


def split_credit(credit: str | None) -> list[str]:
    """The performers in an artist credit: "Jeezy, JAŸ-Z, André 3000" →
    three names; "$UICIDEBOY$ Ft. Tech N9ne" → two. A credit with no
    separator comes back as itself. Splitting is naive on purpose ("Earth,
    Wind & Fire" splits too), which is why the resolver also indexes the
    whole credit."""
    raw = (credit or "").strip()
    if not raw:
        return []
    parts: dict[str, None] = {}
    for p in _CREDIT_SPLIT_RE.split(raw):
        p = p.strip()
        if p:
            parts.setdefault(p, None)
    return list(parts) or [raw]


def entity_names(
    title: str | None, artist: str | None, album: str | None
) -> list[tuple[str, str]]:
    """The names one library row is known by, as (kind, name) pairs, kind
    one of "credit" (a whole multi-artist credit), "artist", "title",
    "album". A row with no artist whose title reads "Artist - Title" (or
    "Artist_ Title", how filename-derived titles come out) gives both."""
    title_s = (title or "").strip()
    artist_s = (artist or "").strip()
    album_s = (album or "").strip()
    if not artist_s and title_s:
        m = _ARTIST_IN_TITLE_RE.match(title_s)
        if m:
            artist_s, title_s = m.group(1).strip(), m.group(2).strip()
    out: dict[tuple[str, str], None] = {}
    if artist_s:
        parts = split_credit(artist_s)
        if len(parts) > 1:
            out.setdefault(("credit", artist_s), None)
        for p in parts:
            out.setdefault(("artist", p), None)
    for kind, name in (("title", title_s), ("album", album_s)):
        if name:
            out.setdefault((kind, name), None)
            cleaned = clean_title(name)
            if cleaned != name:
                out.setdefault((kind, cleaned), None)
    return list(out)


__all__ = [
    "KEY_VERSION",
    "MAX_FORMS",
    "SOUNDS_LIKE_MIN_RATIO",
    "alias_key",
    "clean_title",
    "compact",
    "compact_forms",
    "entity_names",
    "fold",
    "int_to_ordinal_words",
    "int_to_words",
    "phonetic_key",
    "same_name",
    "sounds_like",
    "speakable",
    "split_credit",
    "spoken_forms",
]
