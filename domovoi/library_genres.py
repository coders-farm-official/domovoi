"""Genre and year from a music file's tags: the rules, in one place.

Pure (no database, no I/O) and importable by both processes: the core's
indexer and tag backfill write ``library_tracks.genre`` / ``genre_keys`` /
``year`` with it (V050), and the web's Genres tab names a genre with
:func:`genre_label`.

**Genres.** A file may hold several genre values (Vorbis ``GENRE`` twice,
ID3v2.4's NUL-separated list) and a value may hold several genres
("Rock; Indie"). :func:`genre_keys` turns them into normalised keys:

1. A value that names ID3v1 genres by number is turned into names: "(17)",
   "(17)Rock" (ID3v2.3: the reference, then optionally the same as text),
   a bare "17", "(RX)" (remix) and "(CR)" (cover).
2. A whole value that is a known name is kept whole, so "Drum & Bass",
   "Rock & Roll", "Rhythm and Blues" and "Singer/Songwriter" are one
   genre each. Any other value is split on ``;`` ``/`` ``,`` ``|`` and
   " & " (an ampersand with spaces round it: "R&B" stays one).
3. Each part: Unicode NFKC, case folded, ``_`` and hyphens read as a
   space ("Hip-Hop" = "hip hop", "Post-Rock" = "post rock"), runs of
   space as one, trimmed.
4. Then :data:`ALIASES` maps common spellings onto one key: "hip hop",
   "hiphop", "rap" -> ``hip hop``; "r&b", "rnb", "r and b", "rhythm and
   blues" -> ``r&b``; "alt rock" -> ``alternative rock``; "electronica"
   -> ``electronic``; and a few more. Small on purpose: it joins spellings
   of one genre and never decides that two genres are one (no "indie
   rock" -> "rock" taxonomy in v1; Up next matches on any shared key).
5. Placeholders ("unknown", "none", "genre", "other", "misc", "n/a", "-")
   and parts longer than :data:`MAX_KEY_CHARS` are dropped; a key is kept
   once, in first-seen order, at most :data:`MAX_KEYS` per track.

``library_tracks.genre`` is the first value as tagged (after step 1),
trimmed to :data:`MAX_GENRE_CHARS`, for display; :func:`genre_label` is
how the Genres tab names a key ("hip hop" -> "Hip Hop", "r&b" -> "R&B").

**Year.** :func:`parse_year`: the first four-digit number between
:data:`MIN_YEAR` and next year in a tag value ("1997", "1997-05-21",
"1997-05-21T07:00:00Z", "℗ 1997"), else None.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Iterable

from domovoi.media_tags._id3 import ID3V1_GENRES

#: The version of what the tag reader reads for these fields, stamped on
#: every row whose genre and year it read (``library_tracks.tags_version``,
#: V050). The indexer writes it on new rows; the tag backfill
#: (``domovoi/workers/library_tag_backfill.py``) reads every row below it,
#: and the web counts rows at it for the Genres tab's progress. Bump it
#: when the reader learns something the rows already there should get
#: (and give V050's partial index ``idx_library_tracks_tags_pending`` a
#: wider twin in that change's migration).
TAGS_VERSION = 1

#: Most genre keys kept per track (V050's CHECK allows 16).
MAX_KEYS = 8
#: Longest genre key kept; a longer "genre" is a comment, not a genre.
MAX_KEY_CHARS = 40
#: Longest ``library_tracks.genre`` (V050's CHECK allows 200).
MAX_GENRE_CHARS = 100
#: Earliest year believed (a "0000" or a "1066" track number is not one).
MIN_YEAR = 1900

#: Spellings of one genre -> its key. Keys of the left side are already
#: normalised by step 3 (lower case, hyphens as spaces).
ALIASES: dict[str, str] = {
    "hip hop": "hip hop",
    "hiphop": "hip hop",
    "hip hop rap": "hip hop",
    "rap": "hip hop",
    "rap hip hop": "hip hop",
    "r&b": "r&b",
    "rnb": "r&b",
    "r n b": "r&b",
    "r and b": "r&b",
    "r & b": "r&b",
    "rhythm and blues": "r&b",
    "rhythm & blues": "r&b",
    "r&b soul": "r&b",
    "alt rock": "alternative rock",
    "alternrock": "alternative rock",
    "alternative & punk": "alternative",
    "alt": "alternative",
    "electronica": "electronic",
    "electronic dance music": "edm",
    "drum & bass": "drum and bass",
    "drum n bass": "drum and bass",
    "drum'n'bass": "drum and bass",
    "dnb": "drum and bass",
    "d&b": "drum and bass",
    "rock & roll": "rock and roll",
    "rock n roll": "rock and roll",
    "rock'n'roll": "rock and roll",
    "singer/songwriter": "singer songwriter",
    "singer songwriter": "singer songwriter",
    "lofi": "lo fi",
    "synth pop": "synthpop",
    "jpop": "j pop",
    "kpop": "k pop",
    "britpop": "britpop",
    "soundtracks": "soundtrack",
    "ost": "soundtrack",
    "original soundtrack": "soundtrack",
    "classic": "classical",
    "country & western": "country",
    "country and western": "country",
    "jazz+funk": "jazz funk",
    "pop/funk": "pop funk",
    "world": "world music",
    "christmas music": "christmas",
    "holiday": "christmas",
}

#: How :func:`genre_label` writes keys that are not just capitalised words.
LABELS: dict[str, str] = {
    "hip hop": "Hip Hop",
    "r&b": "R&B",
    "edm": "EDM",
    "ebm": "EBM",
    "idm": "IDM",
    "j pop": "J-Pop",
    "k pop": "K-Pop",
    "lo fi": "Lo-Fi",
    "g funk": "G-Funk",
    "uk garage": "UK Garage",
    "a cappella": "A Cappella",
    "post rock": "Post-Rock",
    "post punk": "Post-Punk",
    "trip hop": "Trip-Hop",
    "singer songwriter": "Singer-Songwriter",
    "top 40": "Top 40",
    "drum and bass": "Drum and Bass",
    "rock and roll": "Rock and Roll",
}

_PLACEHOLDERS = frozenset({
    "", "unknown", "unknown genre", "none", "genre", "other", "misc", "miscellaneous",
    "n/a", "na", "null", "undefined", "default", "various", "-", "?", "<unknown>",
})

_REF_RE = re.compile(r"\((\d{1,3}|RX|CR)\)")
_BARE_NUMBER_RE = re.compile(r"\d{1,3}")
_SPLIT_RE = re.compile(r"\s*(?:[;/,|]|\s&\s)\s*")
_SPACE_RE = re.compile(r"[\s_\-‐-―]+")
_YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")


def _id3v1_name(n: int) -> str | None:
    return ID3V1_GENRES[n] if 0 <= n < len(ID3V1_GENRES) else None


def resolve_numeric(value: str) -> list[str]:
    """Step 1: the genre names a value means once ID3 number references are
    read. "(17)" -> ["Rock"]; "(17)Rock" -> ["Rock"] (the text after the
    references, when there is some, is the genre); "(4)(9)" -> ["Disco",
    "Metal"]; "17" -> ["Rock"]; "(RX)" -> ["Remix"]; "((Foo)" (ID3v2.3's
    escape for a text starting with "(") -> ["(Foo)"]; anything else is
    itself."""
    v = value.strip()
    if not v:
        return []
    if v.startswith("(("):
        return [v[1:]]
    if _BARE_NUMBER_RE.fullmatch(v):
        name = _id3v1_name(int(v))
        return [name] if name else []
    refs: list[str] = []
    pos = 0
    while True:
        m = _REF_RE.match(v, pos)
        if not m:
            break
        ref = m.group(1)
        if ref == "RX":
            refs.append("Remix")
        elif ref == "CR":
            refs.append("Cover")
        else:
            name = _id3v1_name(int(ref))
            if name:
                refs.append(name)
        pos = m.end()
    if pos == 0:
        return [v]
    rest = v[pos:].strip()
    return [rest] if rest else refs


def _norm(part: str) -> str:
    s = unicodedata.normalize("NFKC", part).casefold()
    s = _SPACE_RE.sub(" ", s).strip()
    return s


def _key_of(part: str) -> str | None:
    k = _norm(part)
    k = ALIASES.get(k, k)
    if k in _PLACEHOLDERS or len(k) > MAX_KEY_CHARS:
        return None
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in k):
        return None
    return k


def genre_keys(values: Iterable[str]) -> list[str]:
    """The normalised genre keys of a file's genre values (module
    docstring, steps 1-5). [] when none survive."""
    out: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            continue
        for name in resolve_numeric(raw):
            whole = _norm(name)
            parts = [name] if whole in ALIASES or whole in LABELS else _SPLIT_RE.split(name)
            for part in parts:
                key = _key_of(part)
                if key and key not in out:
                    out.append(key)
                    if len(out) >= MAX_KEYS:
                        return out
    return out


def display_genre(values: Iterable[str]) -> str | None:
    """``library_tracks.genre``: the first value as tagged, its ID3
    numbers read, trimmed; None when the file has no genre worth showing."""
    for raw in values:
        if not isinstance(raw, str):
            continue
        for name in resolve_numeric(raw):
            text = " ".join(name.split())
            if text and genre_keys([text]):
                return text[:MAX_GENRE_CHARS]
    return None


def genre_label(key: str) -> str:
    """How the Genres tab names a key: :data:`LABELS`, else each word
    capitalised ("alternative rock" -> "Alternative Rock")."""
    if key in LABELS:
        return LABELS[key]
    return " ".join(w[:1].upper() + w[1:] for w in key.split(" "))


def parse_year(value: object, *, today: date | None = None) -> int | None:
    """The year a tag value names: its first four-digit number from
    :data:`MIN_YEAR` to next year (a release can be dated ahead), else
    None. Ints are accepted as they are (an MP4 or ASF numeric year)."""
    latest = (today or date.today()).year + 1
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if MIN_YEAR <= value <= latest else None
    if not isinstance(value, str):
        return None
    for m in _YEAR_RE.finditer(value):
        y = int(m.group(1))
        if MIN_YEAR <= y <= latest:
            return y
    return None


__all__ = [
    "ALIASES",
    "LABELS",
    "MAX_KEYS",
    "display_genre",
    "genre_keys",
    "genre_label",
    "parse_year",
    "resolve_numeric",
]
