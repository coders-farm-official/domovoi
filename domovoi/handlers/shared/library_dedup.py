"""Shared fuzzy library-dedup helper.

Single source of truth for "do we already have this song?" via the
``pg_trgm word_similarity`` path. Replaces the ad-hoc copies that used
to live in individual handlers and the radio samplers
(each provider re-implemented "is it already in the library?").

The helper takes an incoming ``title`` and an optional ``artist`` and
returns the matched ``library_tracks`` row's (title, artist) on hit,
or ``None`` on miss. Two-part gate, gracefully degrading:

* The title-similarity threshold (:py:data:`FUZZY_TITLE_THRESHOLD`)
  always applies. That's what keeps "Creep" matching "Radiohead - Creep
  (Remastered)" — and what kept the title-only matcher useful enough
  to skip a download in the first place.

* When **both** sides have a non-empty artist field, an additional
  artist-similarity threshold (:py:data:`FUZZY_ARTIST_THRESHOLD`) is
  enforced. That's what stops Radiohead's "Creep" from being mistaken
  for TLC's "Creep" — both pass the title check at 1.0, but
  ``word_similarity('radiohead', 'tlc')`` ≈ 0, so the row is rejected.

* When **either** side lacks artist (NULL or empty string) the artist
  gate is skipped and the call collapses to title-only matching. That
  preserves the current behavior for manually-indexed library rows
  where ``library_tracks.artist`` is NULL — which is the bulk of
  pre-enrichment rows, and the only place title-only matches still
  make sense.

Artist threshold is looser than title threshold by design — artist
strings have more variance ("The Beatles" vs "Beatles", "Beyoncé"
vs "Beyonce", "DJ Shadow" vs "DJ Shadow & Cut Chemist"). 0.5 lets
those variants resolve to the same person without letting genuinely
different artists slip through.

The cleaned-title preprocessing (`(Official Music Video)` stripping
etc.) lives in :py:func:`clean_title_for_match` and is applied to the
incoming title only — library rows are stored already-cleaned by the
indexer / enricher pipelines, so applying it to both sides would
double-strip.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.library_naming import parse_title_artist


# ─── Tuning constants ────────────────────────────────────────────────────


# Minimum library-row title length to be considered a fuzzy match
# candidate. `word_similarity` already scores 0 on too-short titles,
# but this is the explicit guard — rules out trivially-short titles
# ("AI", "U2", "OK", "Run") whose trigrams can land inside any longer
# string. Matches the pre-extraction `_FUZZY_MIN_LIBRARY_TITLE_LEN`.
MIN_LIBRARY_TITLE_LEN = 5

# Title-similarity threshold. Long-form rationale lived above the
# pre-extraction ``_FUZZY_WORD_SIM_THRESHOLD``; the short version:
# real dupes ("Creep" inside "Radiohead - Creep") score 1.0, false
# positives top out around 0.57.
FUZZY_TITLE_THRESHOLD = 0.6

# Artist-similarity threshold. Looser than title because:
#
#   * Artist names commonly include / drop a leading "The"
#     (`The Beatles` vs `Beatles` → word_similarity ≈ 0.58)
#   * Diacritics get re-normalized inconsistently across sources
#     (`Beyoncé` vs `Beyonce` → word_similarity ≈ 0.78)
#   * Featured-artist conventions vary
#     (`Daft Punk` vs `Daft Punk feat. Pharrell` → word_similarity ≈ 0.45,
#      so this one specifically benefits from 0.5 instead of 0.6)
#   * Different artists with similar spellings (`Tobacco` vs `Tobacca`)
#     would need to score above 0.5 to false-match, which empirically
#     doesn't happen — they share at most one trigram.
#
# 0.5 keeps the "are these the same person?" question loose enough to
# tolerate normal variance and tight enough to reject obvious
# different-person cases. Tighten to 0.6 if dedup ever silently merges
# distinct artists; loosen to 0.4 if "The Beatles" / "Beatles" stops
# matching for some unforeseen reason.
FUZZY_ARTIST_THRESHOLD = 0.5


# ─── Title cruft stripper ────────────────────────────────────────────────


# Parenthetical / bracketed groups containing one of these keywords are
# stripped before matching. The list is intentionally narrow — we want
# to strip "Creep (Official Video)" → "Creep" but NOT
# "Money (That's What I Want)" → "Money" (that's a different song).
#
# Empirically tuned against the user's library; lifted verbatim from
# the pre-extraction provider-handler copy. Don't reorder or remove
# keywords without re-running the dedup test suite — the false-positive
# rate is sensitive to which keywords are recognized.
_TITLE_NOISE_RE = re.compile(
    r"\s*[\(\[][^\)\]]*?(?:official|lyrics?|hd|hq|audio|video|remaster|"
    r"live|acoustic|extended|version|edit|mix|remix|cover|explicit|clean|"
    r"4k|\d+\s*kbps)[^\)\]]*?[\)\]]",
    re.IGNORECASE,
)


def clean_title_for_match(raw: str) -> str:
    """Strip common external-result title cruft ("(Official Video)"
    and friends) for fuzzy library matching.

    Only removes parenthetical / bracketed groups containing noise
    keywords; titles whose parens don't contain noise pass through
    untouched.
    """
    cleaned = _TITLE_NOISE_RE.sub("", raw or "")
    return re.sub(r"\s+", " ", cleaned).strip(" -|:")


# ─── Main entry point ────────────────────────────────────────────────────


async def find_fuzzy_library_match(
    session: AsyncSession,
    *,
    title: str,
    artist: str | None = None,
    clean_title: bool = True,
) -> dict[str, str | None] | None:
    """Return the best fuzzy ``library_tracks`` match for the given
    title (and optionally artist), or ``None`` if no row clears the
    thresholds.

    Parameters
    ----------
    session
        Active async SQLAlchemy session. The query reuses it (one
        round-trip per reading) rather than opening its own scope, so the
        caller controls transaction boundaries.
    title
        Incoming title to match. Whitespace-only or empty returns
        None immediately. An external "Artist - Title" string is split
        first (:func:`domovoi.library_naming.parse_title_artist`): its
        title is compared with library titles, and its artist stands in
        for a missing ``artist`` (in both readings: the split title, then
        the whole string).
    artist
        Optional incoming artist. If non-empty AND the matched
        library row also has a non-empty artist, the artist
        similarity must clear :py:data:`FUZZY_ARTIST_THRESHOLD`. If
        either side is empty/NULL the artist gate is skipped.
    clean_title
        Whether to run :py:func:`clean_title_for_match` over the
        incoming title before matching. Provider-search paths almost
        always want this (their titles carry "(Official Video)"-style
        cruft); radio paths typically don't (song identification
        returns already-clean titles). Default True for backwards-
        compat with the original pre-extraction behavior.

    A library title found INSIDE the incoming one ("Creep" inside
    "Creep by Radiohead") is a duplicate only when the rest of the
    incoming words name the artist (the row's, or the one passed in):
    a library song called "Bunny" is not "Bad Bunny - DtMF" (owner
    report 2026-11-05: that download was refused as "already in your
    library").

    Returns
    -------
    ``{"title": str, "artist": str | None}`` for the best match, or
    ``None`` if nothing crosses the thresholds. The returned title /
    artist are the library row's values (not the incoming ones) so
    callers can use them in user-facing confirmation messages.
    """
    if not title:
        return None
    cleaned = clean_title_for_match(title) if clean_title else title.strip()
    if not cleaned or len(cleaned) < 3:
        return None

    incoming_artist = (artist or "").strip() or None
    readings: list[tuple[str, str | None]] = []
    parsed_artist, parsed_title = parse_title_artist(cleaned)
    if parsed_artist:
        incoming_artist = incoming_artist or parsed_artist
        if parsed_title and len(parsed_title) >= 3:
            readings.append((parsed_title, incoming_artist))
    readings.append((cleaned, incoming_artist))

    for query, query_artist in readings:
        match = await _match_one(session, query, query_artist)
        if match is not None:
            return match
    return None


#: How many title-similar rows one reading weighs in Python.
_CANDIDATES = 25
#: Words that may sit beside a title without making it another song.
_FILLER_WORDS = frozenset({"by", "ft", "feat", "featuring", "and", "the", "a", "with", "x"})
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _words(s: str | None) -> list[str]:
    return _WORD_RE.findall((s or "").lower())


def _rest_names_artist(query: str, lib_title: str, artists: list[str]) -> bool:
    """Whether the incoming words left over once the library title's are
    taken out name one of ``artists`` (or are only filler): "Creep by
    Radiohead" against "Creep" by Radiohead, yes; "Bad Bunny DtMF"
    against "Bunny" by someone else, no."""
    taken = set(_words(lib_title))
    rest = [w for w in _words(query) if w not in taken and w not in _FILLER_WORDS]
    if not rest:
        return True
    for name in artists:
        known = set(_words(name))
        if known and all(w in known for w in rest):
            return True
    return False


async def _match_one(
    session: AsyncSession, query: str, incoming_artist: str | None
) -> dict[str, str | None] | None:
    rows = (
        await session.execute(
            text(
                """
                SELECT title, artist,
                       word_similarity(LOWER(:cleaned), LOWER(title)) AS query_in_title,
                       word_similarity(LOWER(title), LOWER(:cleaned)) AS title_in_query
                FROM library_tracks
                WHERE LENGTH(title) >= :min_lib_len
                  AND GREATEST(
                        word_similarity(LOWER(title), LOWER(:cleaned)),
                        word_similarity(LOWER(:cleaned), LOWER(title))
                  ) >= :title_threshold
                  AND (
                    -- Either side lacks artist → fall back to
                    -- title-only matching (today's behavior for the
                    -- many manually-indexed rows with NULL artist).
                    -- The ::text cast is for asyncpg: a bare
                    -- `:incoming_artist IS NULL` predicate doesn't give
                    -- the planner a column-side type to infer the
                    -- parameter's type from, so prepared-statement
                    -- parsing raises AmbiguousParameterError.
                    (:incoming_artist)::text IS NULL
                    OR artist IS NULL
                    OR LENGTH(TRIM(artist)) = 0
                    -- Both sides have artist → require artist
                    -- similarity above the looser threshold too.
                    OR GREATEST(
                         word_similarity(LOWER(artist), LOWER((:incoming_artist)::text)),
                         word_similarity(LOWER((:incoming_artist)::text), LOWER(artist))
                       ) >= :artist_threshold
                  )
                ORDER BY GREATEST(
                          word_similarity(LOWER(title), LOWER(:cleaned)),
                          word_similarity(LOWER(:cleaned), LOWER(title))
                       ) DESC
                LIMIT :limit
                """
            ),
            {
                "cleaned": query,
                "incoming_artist": incoming_artist,
                "min_lib_len": MIN_LIBRARY_TITLE_LEN,
                "title_threshold": FUZZY_TITLE_THRESHOLD,
                "artist_threshold": FUZZY_ARTIST_THRESHOLD,
                "limit": _CANDIDATES,
            },
        )
    ).all()
    for row in rows:
        # The incoming title (nearly) whole inside the library's: "Creep"
        # against "Creep (Remastered)" or a row titled "Radiohead - Creep".
        if float(row.query_in_title) >= FUZZY_TITLE_THRESHOLD:
            return {"title": row.title, "artist": row.artist}
        # The library title inside the incoming one: a duplicate only when
        # the other words are the artist, never a fragment of another name.
        artists = [a for a in (row.artist, incoming_artist) if a]
        if _rest_names_artist(query, row.title, artists):
            return {"title": row.title, "artist": row.artist}
    return None
