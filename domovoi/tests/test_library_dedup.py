"""Tests for the shared fuzzy library-dedup helper.

The helper is the single source of truth for "do we already have this
song?" — used by provider add-to-library paths, radio detection, and
radio_icy_poller. The tests below cover the artist-gating logic the
helper added on top of the pre-extraction title-only behavior:

* Same-title-different-artist (Radiohead's "Creep" vs TLC's "Creep") →
  no match. This is the bug the artist gate fixes.
* Same-artist-with-variant-spelling ("The Beatles" vs "Beatles") →
  match.
* Library row with NULL/empty artist → falls back to title-only
  matching (the historical pre-extraction behavior; protects
  manually-indexed older rows).
* Incoming with NULL/empty artist → falls back to title-only matching
  (protects the voice-add path that doesn't know artist).
* clean_title flag: title cruft stripped when True, passed through
  when False.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.handlers.shared.library_dedup import (
    FUZZY_ARTIST_THRESHOLD,
    FUZZY_TITLE_THRESHOLD,
    MIN_LIBRARY_TITLE_LEN,
    clean_title_for_match,
    find_fuzzy_library_match,
)
from domovoi.tests.conftest import requires_db


# ─── Pure helpers (no DB) ───────────────────────────────────────────────


def test_clean_title_strips_official_video():
    assert clean_title_for_match("Creep (Official Music Video)") == "Creep"


def test_clean_title_strips_brackets_too():
    assert clean_title_for_match("Creep [HD]") == "Creep"


def test_clean_title_preserves_legitimate_parens():
    """Non-cruft parens — like an actual part of the song title —
    must survive. The classic gotcha case."""
    assert clean_title_for_match("Money (That's What I Want)") == "Money (That's What I Want)"


def test_clean_title_strips_remaster_tag():
    assert clean_title_for_match("Creep (Remastered)") == "Creep"


def test_clean_title_handles_empty_string():
    assert clean_title_for_match("") == ""
    assert clean_title_for_match(None) == ""  # type: ignore[arg-type]


# ─── DB-backed: the artist gate's core discrimination ───────────────────


async def _seed_library_track(s, *, title: str, artist: str | None = None) -> int:
    result = await s.execute(
        text(
            "INSERT INTO library_tracks (file_path, title, artist, added_at) "
            "VALUES (:fp, :t, :a, NOW()) RETURNING id"
        ),
        {"fp": f"/music/{title}.mp3", "t": title, "a": artist},
    )
    sid = int(result.scalar_one())
    await s.commit()
    return sid


@requires_db
@pytest.mark.asyncio
async def test_same_title_different_artist_does_not_match(db_session) -> None:
    """The bug-fix test. Radiohead's "Creep" is in the library; a
    Shazam/ICY identification of TLC's "Creep" should NOT count as
    "already have it." Both score 1.0 on title similarity; the
    artist gate is what separates them."""
    await _seed_library_track(db_session, title="Creep", artist="Radiohead")

    match = await find_fuzzy_library_match(
        db_session, title="Creep", artist="TLC", clean_title=False,
    )
    assert match is None


@requires_db
@pytest.mark.asyncio
async def test_same_artist_with_variant_spelling_still_matches(db_session) -> None:
    """The opposite end of the artist gate: "Beatles" vs "The Beatles"
    must still resolve to the same person. The looser artist
    threshold (vs title threshold) is what makes this work."""
    await _seed_library_track(db_session, title="Yesterday", artist="The Beatles")

    match = await find_fuzzy_library_match(
        db_session, title="Yesterday", artist="Beatles", clean_title=False,
    )
    assert match is not None
    assert match["artist"] == "The Beatles"


@requires_db
@pytest.mark.asyncio
async def test_same_artist_with_diacritic_variance_matches(db_session) -> None:
    """`Beyoncé` vs `Beyonce` — common diacritic variance from
    inconsistent encoding. word_similarity scores 0.75 across the
    accent flip, well above the artist threshold."""
    await _seed_library_track(db_session, title="Crazy in Love", artist="Beyoncé")

    match = await find_fuzzy_library_match(
        db_session, title="Crazy in Love", artist="Beyonce", clean_title=False,
    )
    assert match is not None


@requires_db
@pytest.mark.asyncio
async def test_genuinely_different_artists_with_same_title_rejected(
    db_session,
) -> None:
    """A second confirmation case: two songs called "Yesterday" by
    completely different artists. Title both 1.0; artist
    word_similarity('the beatles', 'toni braxton') is essentially 0."""
    await _seed_library_track(db_session, title="Yesterday", artist="The Beatles")

    match = await find_fuzzy_library_match(
        db_session, title="Yesterday", artist="Toni Braxton", clean_title=False,
    )
    assert match is None


# ─── DB-backed: graceful degradation when artist is missing ─────────────


@requires_db
@pytest.mark.asyncio
async def test_library_row_with_null_artist_falls_back_to_title_only(
    db_session,
) -> None:
    """Manually-indexed library rows often have NULL artist. The
    helper must NOT gate on artist when the library side has none —
    otherwise we'd start re-downloading everything pre-enrichment.
    Title-only matching keeps that behavior intact."""
    await _seed_library_track(db_session, title="Creep", artist=None)

    # Even with a specific incoming artist, NULL on the library side
    # collapses to title-only matching.
    match = await find_fuzzy_library_match(
        db_session, title="Creep", artist="Radiohead", clean_title=False,
    )
    assert match is not None
    assert match["artist"] is None  # we got the NULL-artist row back


@requires_db
@pytest.mark.asyncio
async def test_library_row_with_empty_artist_falls_back_to_title_only(
    db_session,
) -> None:
    """Empty-string artist (vs NULL) — same fallback rules. Some
    enrichment paths stamp `''` rather than NULL when they couldn't
    resolve an artist; both must behave identically here."""
    await _seed_library_track(db_session, title="Creep", artist="")

    match = await find_fuzzy_library_match(
        db_session, title="Creep", artist="Radiohead", clean_title=False,
    )
    assert match is not None


@requires_db
@pytest.mark.asyncio
async def test_incoming_with_no_artist_falls_back_to_title_only(db_session) -> None:
    """The provider voice-add path doesn't separately know artist
    (it's embedded in the upload title). When the caller passes
    artist=None, we must keep matching on title alone — otherwise
    every voice add would suddenly re-download whatever's in the
    library."""
    await _seed_library_track(db_session, title="Creep", artist="Radiohead")

    match = await find_fuzzy_library_match(
        db_session, title="Creep", artist=None, clean_title=False,
    )
    assert match is not None


@requires_db
@pytest.mark.asyncio
async def test_incoming_with_empty_artist_falls_back_to_title_only(
    db_session,
) -> None:
    """Empty-string artist on the incoming side must behave like
    None — the caller may have built the artist field from a noisy
    parse that left it empty."""
    await _seed_library_track(db_session, title="Creep", artist="Radiohead")

    match = await find_fuzzy_library_match(
        db_session, title="Creep", artist="", clean_title=False,
    )
    assert match is not None


# ─── DB-backed: title-clean flag + length floor + miss cases ────────────


@requires_db
@pytest.mark.asyncio
async def test_clean_title_strips_cruft_before_matching(db_session) -> None:
    """The provider path passes clean_title=True so that
    "Creep (Official Music Video)" still resolves to "Creep"."""
    await _seed_library_track(db_session, title="Creep", artist="Radiohead")

    match = await find_fuzzy_library_match(
        db_session, title="Creep (Official Music Video)", artist="Radiohead",
        clean_title=True,
    )
    assert match is not None


@requires_db
@pytest.mark.asyncio
async def test_short_library_title_below_floor_does_not_match(db_session) -> None:
    """`MIN_LIBRARY_TITLE_LEN` = 5 guards against accidental trigram
    overlap on trivially-short titles ("OK", "U2", "AI"). The
    historical bug — `library_tracks.title = "AI"` false-matching
    "Lofi Rain Playlist" — must stay fixed."""
    await _seed_library_track(db_session, title="AI", artist="Ghostemane")

    match = await find_fuzzy_library_match(
        db_session, title="AI", artist="Ghostemane", clean_title=False,
    )
    assert match is None


@requires_db
@pytest.mark.asyncio
async def test_unrelated_titles_do_not_match(db_session) -> None:
    """Sanity check: a completely-unrelated incoming title shouldn't
    match an existing library row even when artist happens to align."""
    await _seed_library_track(db_session, title="Karma Police", artist="Radiohead")

    match = await find_fuzzy_library_match(
        db_session, title="Smooth Criminal", artist="Michael Jackson",
        clean_title=False,
    )
    assert match is None


@requires_db
@pytest.mark.asyncio
async def test_empty_incoming_title_returns_none(db_session) -> None:
    match = await find_fuzzy_library_match(
        db_session, title="", artist="Radiohead", clean_title=False,
    )
    assert match is None


# ─── Constant smoke tests ───────────────────────────────────────────────


def test_artist_threshold_is_looser_than_title_threshold():
    """Encoding the design invariant: artist matching is intentionally
    looser than title matching to absorb naming variance. If somebody
    swaps them, this test catches it."""
    assert FUZZY_ARTIST_THRESHOLD < FUZZY_TITLE_THRESHOLD


def test_min_library_title_len_is_reasonable():
    """Below 4 we re-introduce the AI / U2 / OK trigram-overlap class
    of bug. Above 6 we'd start rejecting legitimate short titles like
    "Creep" / "Hello"."""
    assert 4 <= MIN_LIBRARY_TITLE_LEN <= 6
