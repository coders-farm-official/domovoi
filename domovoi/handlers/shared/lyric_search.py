"""Lyric search: a library song found from words in its lyrics.

"Play the song that goes …", "what's the song that goes …", and — when
"play <name>" finds nothing by that name — the words as a lyric (people
often say a line thinking it is the title). The public face is
:func:`domovoi.handlers.shared.library_match.resolve_lyrics`; this module
is its machinery (lyrics-build contract §9):

* **the line index** (:func:`build_lines`, written by
  ``domovoi/workers/lyrics_index.py`` into V021's ``track_lyric_lines``):
  every distinct line of a song's shown lyrics and every pair of
  consecutive lines, normalized by :func:`spoken_names.lyric_words`, with
  how often it is sung — the chorus is what people remember;
* **retrieval** (:func:`fetch_rows`): candidate lines by trigram word
  similarity, through the GIN index (the ``<%`` operator);
* **scoring** (:func:`align`, :func:`score_rows`): a word alignment that
  accounts for every word the person said, forgives a word left out, two
  words swapped, a word replaced or misheard, and lets the line run on
  before and after the phrase; a line sung once counts
  :data:`VERSE_FACTOR` of a repeated one, so the chorus wins ties;
* **deciding** (:func:`decide`): the SAME play / ask bands as names
  (``music_match_play_threshold`` / ``music_match_ask_threshold``), with
  guards of its own — a phrase of fewer than :data:`MIN_WORDS` words is not
  looked up, an explicit request plays on :data:`PLAY_MIN_WORDS` words or
  more, the last resort plays only an EXACT phrase of
  :data:`FALLBACK_PLAY_MIN_WORDS` words or more, and words that more than
  :data:`MAX_ASK` songs share equally well are "too common".

Every constant was fixed in the contract before any evaluation data
existed, and is never moved to make an evaluation pass (the spoken-match
precedent).

Lyrics are the household's copyrighted data: nothing here logs, raises or
returns a line or the phrase in a message — ids, counts and scores only.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from rapidfuzz import fuzz, process
from sqlalchemy import text

from domovoi.handlers.shared.library_match import Candidate, EntityRef, LyricMode, Resolution
from domovoi.handlers.shared.spoken_names import (
    LYRIC_NORM_VERSION,
    lyric_words,
    same_name,
    sounds_like,
    speakable,
    split_credit,
)

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


# ─── Fixed constants (contract [Q25]; never fitted to an evaluation) ───────

#: Candidate retrieval: pg_trgm's word-similarity threshold for ``<%``.
TRGM_THRESHOLD = 0.45
#: Rows fetched / rows aligned (plus every row holding the phrase whole).
CANDIDATE_ROWS = 300
SCORED_ROWS = 80
#: Fewer words, or fewer letters and digits, than this: "too short".
MIN_WORDS = 3
MIN_CHARS = 12
#: A longer phrase is cut to its first this-many words.
MAX_WORDS = 30
#: "play the song that goes …" / "what's the song that goes …": fewer words
#: never play (at most ask).
PLAY_MIN_WORDS = 4
#: The last resort ("play <words>" that named nothing): fewer words are not
#: looked up at all, and it plays only an EXACT phrase this long or longer.
FALLBACK_MIN_WORDS = 4
FALLBACK_PLAY_MIN_WORDS = 6
#: A word said and a word of the line count as a near spelling (an ASR
#: variant: a plural, a dropped ending) when rapidfuzz's ratio is at least
#: this (0-1).
FUZZY_WORD_MIN = 0.75
#: A line sung once counts this much of a line sung twice or more.
VERSE_FACTOR = 0.97
#: Songs within this of the best are as good as it.
MARGIN = 0.05
#: Most songs one "did you mean …?" offers (spoken_index.MAX_ASK).
MAX_ASK = 3

#: The line index ([Q1]): a line's normalized text is cut to this many
#: characters, and at most this many rows are kept per span.
MAX_KEY_CHARS = 1000
MAX_ROWS_PER_SPAN = 1000
#: A pair of lines is cut to this (track_lyric_lines' CHECK allows 2000
#: characters; two cut lines and a space would be 2001).
MAX_PAIR_CHARS = 2000
#: track_lyric_lines.line_no and .repeats are SMALLINT.
_SMALLINT_MAX = 32767

# ─── The line index (pure) ────────────────────────────────────────────────

# A line that is only a bracketed label: "[Chorus]", "(x2)", "[Verse 2]".
_BRACKET_LINE_RE = re.compile(r"^\s*[\[(][^\])]*[\])]\s*$")


@dataclass(frozen=True, slots=True)
class LineRow:
    """One ``track_lyric_lines`` row: ``span`` 1 (a line) or 2 (that line
    and the next), ``line_no`` the 0-based position of its first
    occurrence among the song's kept lines, ``repeats`` how many times it
    occurs, ``text`` its :func:`lyric_words` joined by single spaces."""

    span: int
    line_no: int
    repeats: int
    text: str


def line_key(line: str) -> str:
    """A lyric line as the index stores it ([Q1] step 2): its
    :func:`lyric_words` joined by single spaces, cut to
    :data:`MAX_KEY_CHARS`. Empty for a line with no words."""
    return " ".join(lyric_words(line))[:MAX_KEY_CHARS].rstrip()


def build_lines(plain: str | None) -> list[LineRow]:
    """The index rows of a song's shown plain lyrics ([Q1]): its non-empty,
    non-label lines in order (a stanza break resets nothing), one span-1
    row per distinct line and one span-2 row per distinct pair of
    consecutive lines, each with its repeat count and first position; at
    most :data:`MAX_ROWS_PER_SPAN` per span (the earliest kept); sorted by
    (span, line_no)."""
    keys: list[str] = []
    for raw in (plain or "").split("\n"):
        if not raw.strip() or _BRACKET_LINE_RE.match(raw):
            continue
        key = line_key(raw)
        if key:
            keys.append(key)
    pairs = [f"{a} {b}"[:MAX_PAIR_CHARS].rstrip() for a, b in zip(keys, keys[1:])]
    rows: list[LineRow] = []
    for span, items in ((1, keys), (2, pairs)):
        first: dict[str, int] = {}
        count: dict[str, int] = {}
        for pos, key in enumerate(items):
            first.setdefault(key, pos)
            count[key] = count.get(key, 0) + 1
        ordered = sorted((pos, key) for key, pos in first.items())[:MAX_ROWS_PER_SPAN]
        rows.extend(
            LineRow(span, pos, min(count[key], _SMALLINT_MAX), key)
            for pos, key in ordered
            if pos <= _SMALLINT_MAX
        )
    return rows


# ─── The phrase (pure) ────────────────────────────────────────────────────

_LEADING_FILLER = frozenset({"uh", "um", "er", "erm", "hmm", "mm", "like"})
# "do I have the song that goes … in my library"
_TRAILING_CARRIERS: tuple[tuple[str, ...], ...] = (
    ("in", "my", "library"),
    ("in", "the", "library"),
    ("in", "my", "music"),
    ("in", "my", "collection"),
)


@dataclass(frozen=True, slots=True)
class LyricQuery:
    """A spoken lyric phrase as lyric search reads it ([Q26])."""

    words: tuple[str, ...]

    @property
    def text(self) -> str:
        return " ".join(self.words)

    @property
    def chars(self) -> int:
        """Letters and digits in the kept words."""
        return sum(len(w) for w in self.words)

    def __len__(self) -> int:
        return len(self.words)


def lyric_query(phrase: str | None) -> LyricQuery:
    """``phrase`` as :func:`lyric_words`, leading filler ("uh", "um",
    "like" …) dropped, one trailing carrier ("in my library" …) dropped,
    cut to :data:`MAX_WORDS` words."""
    words = lyric_words(phrase)
    start = 0
    while start < len(words) and words[start] in _LEADING_FILLER:
        start += 1
    words = words[start:]
    for carrier in _TRAILING_CARRIERS:
        if len(words) >= len(carrier) and tuple(words[-len(carrier):]) == carrier:
            words = words[: -len(carrier)]
            break
    return LyricQuery(tuple(words[:MAX_WORDS]))


def too_short(query: LyricQuery, mode: str) -> bool:
    """The length guards of [Q40], which need no database."""
    if len(query) < MIN_WORDS or query.chars < MIN_CHARS:
        return True
    return mode == "fallback" and len(query) < FALLBACK_MIN_WORDS


# ─── Scoring (pure) ───────────────────────────────────────────────────────

_FUZZY_RATIO_MIN = FUZZY_WORD_MIN * 100.0


def _sub(a: str, b: str) -> float:
    """What replacing word ``a`` (said) by ``b`` (sung) costs: nothing for
    the same word, a fraction for a near spelling, one for anything else."""
    if a == b:
        return 0.0
    r = fuzz.ratio(a, b)
    if r >= _FUZZY_RATIO_MIN:
        return 1.0 - r / 100.0
    return 1.0


def align(q: Sequence[str], r: Sequence[str]) -> tuple[float, bool]:
    """(score, exact) of the words said ``q`` against a line's words ``r``
    ([Q21]): a semi-global word alignment — every word said is accounted
    for, the line's words before and after the matched stretch are free.
    A word said that the line lacks, a word of the line left out, a word
    replaced and two words swapped cost 1 each; a near spelling costs
    ``1 - ratio``. score = max(0, 1 - cost / len(q)); exact = no cost.

    The reference implementation: :func:`score_rows` uses a cached twin
    (:func:`_align_matrix`) that a test holds to this one exactly."""
    n, m = len(q), len(r)
    if n == 0:
        return 0.0, False
    d = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        d[i][0] = float(i)
        for j in range(1, m + 1):
            best = d[i - 1][j - 1] + _sub(q[i - 1], r[j - 1])
            v = d[i - 1][j] + 1.0
            if v < best:
                best = v
            v = d[i][j - 1] + 1.0
            if v < best:
                best = v
            if (
                i >= 2 and j >= 2
                and q[i - 1] == r[j - 2] and q[i - 2] == r[j - 1]
                and q[i - 1] != q[i - 2]
            ):
                v = d[i - 2][j - 2] + 1.0
                if v < best:
                    best = v
            d[i][j] = best
    cost = min(d[n])
    return max(0.0, 1.0 - cost / n), cost == 0.0


def _align_matrix(q: Sequence[str], r: Sequence[str], sub: Sequence[Sequence[float]]) -> tuple[float, bool]:
    """:func:`align` with every ``_sub(q[i], r[j])`` given as ``sub[i][j]``
    (the same numbers, the same operations in the same order: the same
    result, to the last bit — tested)."""
    n, m = len(q), len(r)
    if n == 0:
        return 0.0, False
    prev2: list[float] | None = None
    prev = [0.0] * (m + 1)
    for i in range(1, n + 1):
        cur = [0.0] * (m + 1)
        cur[0] = float(i)
        qi, qp = q[i - 1], (q[i - 2] if i >= 2 else None)
        srow = sub[i - 1]
        for j in range(1, m + 1):
            best = prev[j - 1] + srow[j - 1]
            v = prev[j] + 1.0
            if v < best:
                best = v
            v = cur[j - 1] + 1.0
            if v < best:
                best = v
            if (
                prev2 is not None and j >= 2
                and qi == r[j - 2] and qp == r[j - 1] and qi != qp
            ):
                v = prev2[j - 2] + 1.0
                if v < best:
                    best = v
            cur[j] = best
        prev2, prev = prev, cur
    cost = min(prev)
    return max(0.0, 1.0 - cost / n), cost == 0.0


class _SubCache:
    """``_sub`` memoized for one query: lyric lines share most of their
    words, so a query's few words meet the same line words again and again."""

    __slots__ = ("_cache", "_query")

    def __init__(self, query: Sequence[str]) -> None:
        self._query = tuple(query)
        self._cache: dict[str, tuple[float, ...]] = {}

    def column(self, word: str) -> tuple[float, ...]:
        """``_sub(q, word)`` for every query word ``q``, in query order."""
        col = self._cache.get(word)
        if col is None:
            col = tuple(_sub(qw, word) for qw in self._query)
            self._cache[word] = col
        return col

    def matrix(self, r: Sequence[str]) -> list[list[float]]:
        cols = [self.column(w) for w in r]
        return [[col[i] for col in cols] for i in range(len(self._query))]


def _cost_floor(q: Sequence[str], r: Sequence[str]) -> int:
    """A lower bound on the alignment cost: every word said that has no
    near spelling anywhere in the line costs at least 1 (substituted or
    unmatched — a swap pairs only words the line has exactly)."""
    words = list(dict.fromkeys(r))
    lost = 0
    for w in q:
        if w in words:
            continue
        if process.extractOne(w, words, scorer=fuzz.ratio, score_cutoff=_FUZZY_RATIO_MIN) is None:
            lost += 1
    return lost


@dataclass(frozen=True, slots=True)
class RowHit:
    """A candidate line from :func:`fetch_rows` (``ws``: pg_trgm's
    word similarity of the phrase to it)."""

    track_id: int
    span: int
    line_no: int
    repeats: int
    text: str
    ws: float = 0.0


@dataclass(frozen=True, slots=True)
class RowScore:
    """A scored candidate line: ``score`` already carries the verse factor."""

    track_id: int
    score: float
    exact: bool
    repeats: int
    span: int
    line_no: int


def _chosen_rows(query: LyricQuery, rows: Sequence[RowHit]) -> list[RowHit]:
    """[Q20]: the first :data:`SCORED_ROWS` rows by word similarity, plus
    every other row that holds the phrase as whole words (an exact
    candidate is never cut off)."""
    whole = f" {query.text} "
    return [r for k, r in enumerate(rows) if k < SCORED_ROWS or whole in f" {r.text} "]


def score_rows(
    query: LyricQuery, rows: Sequence[RowHit], *, floor: float | None = None
) -> list[RowScore]:
    """Score the candidate lines ([Q20]–[Q22]). With ``floor`` (the lowest
    score that can matter, ``min(ask, play)``), a line that cannot reach it
    is not aligned at all — it would only ever be dropped (see
    :func:`decide`): the optimization of [Q24], tested against the plain
    alignment."""
    q = query.words
    cache = _SubCache(q)
    out: list[RowScore] = []
    n = len(q)
    for row in _chosen_rows(query, rows):
        r = row.text.split()
        factor = 1.0 if row.repeats >= 2 else VERSE_FACTOR
        if floor is not None and n:
            ceiling = max(0.0, 1.0 - _cost_floor(q, r) / n) * factor
            if ceiling < floor:
                continue
        score, exact = _align_matrix(q, r, cache.matrix(r))
        out.append(RowScore(
            track_id=row.track_id, score=score * factor, exact=exact,
            repeats=row.repeats, span=row.span, line_no=row.line_no,
        ))
    return out


# ─── Deciding (pure) ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class LyricEntity:
    """A library entity the lines point at ([Q31], [Q32]): its candidate
    (``via="lyrics"``, ``score`` = its best line's), whether that line
    matched exactly, and how often that line is sung."""

    candidate: Candidate
    score: float
    exact: bool
    repeats: int


def _rank_key(e: LyricEntity) -> tuple:
    ref = e.candidate.ref
    return (-e.score, -e.repeats, -len(e.candidate.track_ids), ref.key, ref.type)


def decide(
    query: LyricQuery,
    entities: Iterable[LyricEntity],
    *,
    mode: str,
    play: float,
    ask: float,
    heard: str = "",
) -> Resolution:
    """Play, ask or nothing ([Q40]). An ask bar at or above the play bar
    never asks."""
    n = len(query)
    if too_short(query, mode):
        return Resolution.none("too_short", heard)
    floor = min(ask, play)
    ranked = sorted(entities, key=_rank_key)
    if not ranked or ranked[0].score < floor:
        return Resolution.none("below_ask", heard)
    top = ranked[0]
    near = [e for e in ranked if e.score >= max(floor, top.score - MARGIN)]
    if len(near) > MAX_ASK:
        return Resolution.none("too_common", heard)
    if top.score >= play and len(near) == 1:
        if mode in ("explicit", "identify") and n >= PLAY_MIN_WORDS:
            return Resolution("play", (top.candidate,), heard=heard, reason="lyrics")
        if mode == "fallback" and top.exact and n >= FALLBACK_PLAY_MIN_WORDS:
            return Resolution("play", (top.candidate,), heard=heard, reason="lyrics_exact")
    if ask < play and top.score >= ask:
        return Resolution(
            "ask", tuple(e.candidate for e in near[:MAX_ASK]), heard=heard,
            reason="tie" if top.score >= play else "lyrics",
        )
    return Resolution.none("below_play", heard)


# ─── Retrieval (SQL) ──────────────────────────────────────────────────────

_PRESENT_SQL = text("SELECT to_regclass('public.track_lyric_lines') IS NOT NULL")
_ANY_LINES_SQL = text("SELECT EXISTS (SELECT 1 FROM track_lyric_lines)")
_THRESHOLD_SQL = text(
    "SELECT set_config('pg_trgm.word_similarity_threshold', :threshold, true)"
)
# The <% operator form is what uses the GIN index (a word_similarity(...)
# > x in the WHERE scans the table).
_ROWS_SQL = text(
    "SELECT l.track_id, l.span, l.line_no, l.repeats, l.text,"
    " word_similarity(:q, l.text) AS ws"
    " FROM track_lyric_lines l"
    " WHERE :q <% l.text"
    " ORDER BY ws DESC, l.repeats DESC, l.track_id, l.span, l.line_no"
    " LIMIT :limit"
)

#: V021's tables exist: None = not checked yet; once seen, never re-checked
#: (a table does not disappear under a running core).
_tables_seen = False


async def lyrics_tables_present(session: "AsyncSession") -> bool:
    global _tables_seen
    if _tables_seen:
        return True
    present = bool((await session.execute(_PRESENT_SQL)).scalar())
    if present:
        _tables_seen = True
    return present


async def fetch_rows(session: "AsyncSession", query: LyricQuery) -> list[RowHit]:
    """Candidate lines for ``query`` ([Q10]), in the caller's transaction:
    the trigram threshold is set for this transaction only."""
    await session.execute(_THRESHOLD_SQL, {"threshold": f"{TRGM_THRESHOLD:.2f}"})
    res = await session.execute(_ROWS_SQL, {"q": query.text, "limit": CANDIDATE_ROWS})
    return [
        RowHit(
            track_id=int(r.track_id), span=int(r.span), line_no=int(r.line_no),
            repeats=int(r.repeats), text=str(r.text), ws=float(r.ws or 0.0),
        )
        for r in res
    ]


# ─── From lines to library entities ───────────────────────────────────────


@dataclass
class _Best:
    score: float
    exact: bool
    repeats: int

    def beats(self, other: "_Best") -> bool:
        """[Q32]: the higher score; between equal scores, an exact line,
        then the more repeated one."""
        return (self.score, self.exact, self.repeats) > (other.score, other.exact, other.repeats)


def _artist_ok(said: str, labels: Iterable[str]) -> bool:
    """[Q34]: one of the entity's credited names sounds like (or is) the
    artist that was said."""
    for label in labels:
        if label and (same_name(said, label) or sounds_like(said, label)):
            return True
    return False


async def to_entities(
    session: "AsyncSession",
    scored: Sequence[RowScore],
    *,
    artist: str | None = None,
    avoid: Sequence[EntityRef] = (),
) -> tuple[list[LyricEntity], bool]:
    """The entities the scored lines point at ([Q30]–[Q35]) and whether
    ``avoid`` dropped any: a line's song is its title entity in the
    spoken-name index (copies of one song are one candidate, said with the
    household's name for it), else its own track. The ``artist`` filter
    keeps an entity only when one of its credited names sounds like the
    artist said."""
    from domovoi.handlers.shared import library_match

    impl: Any = None
    try:
        async with session.begin_nested():
            impl = (await library_match.current_index(session))._impl
    except Exception as e:  # noqa: BLE001 — every hit is a track candidate instead
        log.warning("lyric match: the spoken-name index is unavailable (%s)", type(e).__name__)
        impl = None

    titles: dict[int, _Best] = {}
    tracks: dict[int, _Best] = {}
    for s in scored:
        i = impl.title_entity_for_track(s.track_id) if impl is not None else None
        bucket, key = (titles, i) if i is not None else (tracks, s.track_id)
        cur = _Best(s.score, s.exact, s.repeats)
        prev = bucket.get(key)
        if prev is None or cur.beats(prev):
            bucket[key] = cur

    # Track candidates: the row's own title and primary performer, from the
    # index when it holds the row, else from the library table.
    need_db = [
        tid for tid in tracks
        if impl is None or tid not in impl.rows or not impl.rows[tid].title
    ]
    db_rows: dict[int, Any] = {}
    if need_db:
        for row in await library_match.rows_by_id(session, need_db):
            db_rows[row.id] = row

    out: list[LyricEntity] = []
    dropped = False

    def keep(cand: Candidate, best: _Best, labels: Iterable[str]) -> None:
        nonlocal dropped
        if artist and not _artist_ok(artist, labels):
            return
        if cand.ref in avoid:
            dropped = True
            return
        out.append(LyricEntity(cand, best.score, best.exact, best.repeats))

    for i, best in titles.items():
        e = impl.entities[i]
        cand = impl.candidate(i, best.score, "lyrics")
        keep(cand, best, [impl.entities[c].label for c in e.credited])

    for tid, best in tracks.items():
        info = impl.rows.get(tid) if impl is not None else None
        db = db_rows.get(tid)
        if info is not None and info.title:
            title, primary = info.title, info.primary
            labels = [impl.entities[c].label for c in info.credited]
        elif db is not None:
            parts = split_credit(db.artist)
            title = (db.title or "").strip() or PurePath(db.file_path or "").stem
            primary = parts[0] if parts else ""
            labels = [*parts, db.artist or ""]
        else:
            continue  # gone from the library since the lines were read
        cand = Candidate(
            ref=EntityRef(type="track", key=str(tid), label=title, artist_label=primary or None),
            score=float(best.score), via="lyrics", speak=speakable(title), track_ids=(tid,),
        )
        keep(cand, best, labels)
    return out, dropped


# ─── The whole search ─────────────────────────────────────────────────────


async def search(
    session: "AsyncSession",
    phrase: str,
    *,
    mode: str,
    play: float,
    ask: float,
    artist: str | None = None,
    avoid: Sequence[EntityRef] = (),
) -> tuple[Resolution, int]:
    """(the Resolution, the phrase's word count) — everything
    :func:`library_match.resolve_lyrics` does but its settings, logging and
    error handling. The database work runs inside a SAVEPOINT, so a failure
    here never poisons the caller's transaction (the turn goes on to record
    its play)."""
    heard = phrase
    query = lyric_query(phrase)
    n = len(query)
    if too_short(query, mode):
        return Resolution.none("too_short", heard), n
    async with session.begin_nested():
        if not await lyrics_tables_present(session):
            return Resolution.none("no_lyrics", heard), n
        rows = await fetch_rows(session, query)
        if not rows:
            empty = not bool((await session.execute(_ANY_LINES_SQL)).scalar())
            return Resolution.none("no_lyrics" if empty else "below_ask", heard), n
    scored = await asyncio.to_thread(score_rows, query, rows, floor=min(ask, play))
    async with session.begin_nested():
        entities, dropped = await to_entities(session, scored, artist=artist, avoid=avoid)
    if not entities and dropped:
        return Resolution.none("declined", heard), n
    return decide(query, entities, mode=mode, play=play, ask=ask, heard=heard), n


def describe(res: Resolution) -> str:
    """The candidate part of [Q50]'s log line: ``" track:<ids> <score>"``
    or ``" title:<first track id> <score>"`` — ids and a score, never a
    title or a word."""
    best = res.best
    if best is None:
        return ""
    if best.ref.type == "track":
        ids = ",".join(str(t) for t in best.track_ids) or best.ref.key
        return f" track:{ids} {best.score:.3f}"
    first = best.track_ids[0] if best.track_ids else "?"
    return f" {best.ref.type}:{first} {best.score:.3f}"


def reset_for_tests() -> None:
    global _tables_seen
    _tables_seen = False


__all__ = [
    "CANDIDATE_ROWS",
    "FALLBACK_MIN_WORDS",
    "FALLBACK_PLAY_MIN_WORDS",
    "FUZZY_WORD_MIN",
    "LYRIC_NORM_VERSION",
    "LineRow",
    "LyricEntity",
    "LyricMode",
    "LyricQuery",
    "MARGIN",
    "MAX_ASK",
    "MAX_KEY_CHARS",
    "MAX_ROWS_PER_SPAN",
    "MAX_WORDS",
    "MIN_CHARS",
    "MIN_WORDS",
    "PLAY_MIN_WORDS",
    "RowHit",
    "RowScore",
    "SCORED_ROWS",
    "TRGM_THRESHOLD",
    "VERSE_FACTOR",
    "align",
    "build_lines",
    "decide",
    "describe",
    "fetch_rows",
    "line_key",
    "lyric_query",
    "lyrics_tables_present",
    "score_rows",
    "search",
    "to_entities",
    "too_short",
]
