"""Library enricher tests — verify the AcoustID-then-Shazam fallback
chain, rate-limit timing, idempotency via enriched_at, and the
COALESCE-on-update behavior that preserves indexer-derived data when
the API returns NULL for a field.

External APIs are mocked at the module level (acoustid.match,
shazamio.Shazam) so tests don't hit the network.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db

# shazamio is an OPTIONAL extra (see pyproject's `shazam`). find_spec only
# checks presence — it deliberately does NOT import, so this costs nothing and
# cannot itself trigger the failure below.
#
# It guards ABSENCE, not brokenness. A source-built shazamio-core can segfault
# at import (CPython 3.14, observed 2026-09-01), and nothing inside Python can
# catch that — the process dies. The real defence is not installing it, which
# is why it left `real-clients` and `dev` for its own extra. If you install
# `shazam` on a Python without a real wheel, this suite will still die here.
requires_shazamio = pytest.mark.skipif(
    importlib.util.find_spec("shazamio") is None,
    reason="shazamio not installed (optional `shazam` extra)",
)
from domovoi.workers.library_enricher import (
    EnrichmentResult,
    _enrich_via_acoustid,
    _enrich_via_shazam,
    enrich_library,
)


# ─── Module helpers ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_acoustid_skipped_when_no_api_key(monkeypatch, tmp_path) -> None:
    """An empty ACOUSTID_API_KEY means we skip the AcoustID layer
    entirely — no network call. The enricher cascades to Shazam."""
    monkeypatch.setattr(
        "domovoi.workers.library_enricher.settings.acoustid_api_key", ""
    )
    f = tmp_path / "track.mp3"
    f.write_bytes(b"")
    result = await _enrich_via_acoustid(f)
    assert result is None


@pytest.mark.asyncio
async def test_acoustid_handles_missing_fpcalc_binary(monkeypatch, tmp_path) -> None:
    """fpcalc not on PATH → acoustid raises NoBackendError → we log
    and return None (caller falls back to Shazam)."""
    monkeypatch.setattr(
        "domovoi.workers.library_enricher.settings.acoustid_api_key", "fakekey"
    )

    import acoustid as _ac
    def _raise(*a, **kw):
        raise _ac.NoBackendError("fpcalc not found")

    f = tmp_path / "track.mp3"
    f.write_bytes(b"")
    with patch("acoustid.match", _raise):
        result = await _enrich_via_acoustid(f)
    assert result is None


@pytest.mark.asyncio
async def test_acoustid_drops_low_score_match(monkeypatch, tmp_path) -> None:
    """Below the configured min-score, AcoustID matches are discarded
    (the catalog occasionally returns confident-but-wrong matches on
    short or noisy fingerprints)."""
    monkeypatch.setattr(
        "domovoi.workers.library_enricher.settings.acoustid_api_key", "fakekey"
    )
    monkeypatch.setattr(
        "domovoi.workers.library_enricher.settings.library_enricher_acoustid_min_score",
        0.7,
    )
    f = tmp_path / "track.mp3"
    f.write_bytes(b"")
    # Score 0.5 is below the 0.7 threshold → return None.
    with patch("acoustid.match", lambda *a, **kw: iter([(0.5, "rec-id", "Bad Match", "Wrong Artist")])):
        result = await _enrich_via_acoustid(f)
    assert result is None


@pytest.mark.asyncio
async def test_acoustid_returns_high_score_match(monkeypatch, tmp_path) -> None:
    """A good AcoustID match (above threshold) returns title + artist
    + MusicBrainz recording ID."""
    monkeypatch.setattr(
        "domovoi.workers.library_enricher.settings.acoustid_api_key", "fakekey"
    )
    monkeypatch.setattr(
        "domovoi.workers.library_enricher.settings.library_enricher_acoustid_min_score",
        0.7,
    )
    f = tmp_path / "track.mp3"
    f.write_bytes(b"")
    with patch(
        "acoustid.match",
        lambda *a, **kw: iter([(0.92, "mb-recording-uuid", "Creep", "Radiohead")]),
    ):
        result = await _enrich_via_acoustid(f)
    assert result is not None
    assert result.title == "Creep"
    assert result.artist == "Radiohead"
    assert result.musicbrainz_recording_id == "mb-recording-uuid"
    assert result.source == "acoustid"


@requires_shazamio
@pytest.mark.asyncio
async def test_shazam_parses_track_dict(monkeypatch, tmp_path) -> None:
    """shazamio returns a nested dict — verify the parser pulls title
    out of `track.title` and artist out of `track.subtitle`."""
    f = tmp_path / "track.mp3"
    f.write_bytes(b"")

    class _FakeShazam:
        async def recognize(self, path: str) -> dict:
            return {
                "track": {
                    "title": "Hotel California",
                    "subtitle": "Eagles",
                    "sections": [
                        {
                            "metadata": [
                                {"title": "Album", "text": "Hotel California"},
                                {"title": "Released", "text": "1976"},
                            ],
                        },
                    ],
                },
            }

    with patch("shazamio.Shazam", _FakeShazam):
        result = await _enrich_via_shazam(f)
    assert result is not None
    assert result.title == "Hotel California"
    assert result.artist == "Eagles"
    assert result.album == "Hotel California"
    assert result.source == "shazam"


@requires_shazamio
@pytest.mark.asyncio
async def test_shazam_handles_no_track(monkeypatch, tmp_path) -> None:
    """shazamio returning an empty / track-less response → None."""
    f = tmp_path / "track.mp3"
    f.write_bytes(b"")

    class _NoMatchShazam:
        async def recognize(self, path: str) -> dict:
            return {"matches": [], "track": None}

    with patch("shazamio.Shazam", _NoMatchShazam):
        result = await _enrich_via_shazam(f)
    assert result is None


# ─── Full enrich_library sweep ────────────────────────────────────────────

@requires_db
@pytest.mark.asyncio
async def test_enrich_library_skips_when_disabled(db_session, monkeypatch) -> None:
    monkeypatch.setattr(
        "domovoi.workers.library_enricher.settings.library_enricher_enabled", False
    )
    counts = await enrich_library()
    assert counts["scanned"] == 0


@requires_db
@pytest.mark.asyncio
async def test_enrich_library_only_processes_unenriched_rows(
    db_session, monkeypatch, tmp_path,
) -> None:
    """The enriched_at filter — already-enriched rows must be
    skipped. Idempotency on re-runs."""
    monkeypatch.setattr(
        "domovoi.workers.library_enricher.settings.library_enricher_delay_sec", 0
    )
    f = tmp_path / "track.mp3"
    f.write_bytes(b"")
    await db_session.execute(
        text(
            "INSERT INTO library_tracks "
            "(file_path, title, artist, enriched_at) "
            "VALUES (:fp, :t, :a, NOW())"
        ),
        {"fp": str(f), "t": "Already Done", "a": "Some Artist"},
    )
    await db_session.commit()

    # Mock both layers — should never get called.
    async def _explode(*a, **kw):
        raise AssertionError("API hit on already-enriched row")

    with patch(
        "domovoi.workers.library_enricher._enrich_via_acoustid",
        _explode,
    ), patch(
        "domovoi.workers.library_enricher._enrich_via_shazam",
        _explode,
    ):
        counts = await enrich_library()

    assert counts["scanned"] == 0
    assert counts["matched"] == 0


@requires_db
@pytest.mark.asyncio
async def test_enrich_library_updates_with_acoustid_match(
    db_session, monkeypatch, tmp_path,
) -> None:
    """Happy path: AcoustID returns a match, the row gets canonical
    title/artist/MB-ID, enriched_at is stamped."""
    monkeypatch.setattr(
        "domovoi.workers.library_enricher.settings.library_enricher_delay_sec", 0
    )
    f = tmp_path / "ugly_filename.mp3"
    f.write_bytes(b"")
    await db_session.execute(
        text(
            "INSERT INTO library_tracks (file_path, title, artist) "
            "VALUES (:fp, :t, :a)"
        ),
        {"fp": str(f), "t": "ugly_filename", "a": None},
    )
    await db_session.commit()

    async def _ac_match(file_path):
        return EnrichmentResult(
            title="Real Title", artist="Real Artist",
            musicbrainz_recording_id="mb-uuid",
            source="acoustid",
        )

    async def _shz_match(file_path):
        raise AssertionError("Shazam should not be called when AcoustID hits")

    with patch(
        "domovoi.workers.library_enricher._enrich_via_acoustid",
        _ac_match,
    ), patch(
        "domovoi.workers.library_enricher._enrich_via_shazam",
        _shz_match,
    ):
        counts = await enrich_library()

    assert counts["matched"] == 1
    row = (await db_session.execute(
        text("SELECT title, artist, musicbrainz_recording_id, enriched_at FROM library_tracks")
    )).first()
    assert row.title == "Real Title"
    assert row.artist == "Real Artist"
    assert row.musicbrainz_recording_id == "mb-uuid"
    assert row.enriched_at is not None


@requires_db
@pytest.mark.asyncio
async def test_enrich_library_falls_through_to_shazam(
    db_session, monkeypatch, tmp_path,
) -> None:
    """When AcoustID returns None, the chain falls through to Shazam.
    A Shazam hit should also update the row."""
    monkeypatch.setattr(
        "domovoi.workers.library_enricher.settings.library_enricher_delay_sec", 0
    )
    f = tmp_path / "track.mp3"
    f.write_bytes(b"")
    await db_session.execute(
        text("INSERT INTO library_tracks (file_path, title) VALUES (:fp, :t)"),
        {"fp": str(f), "t": "track"},
    )
    await db_session.commit()

    async def _ac_miss(file_path):
        return None

    async def _shz_match(file_path):
        return EnrichmentResult(
            title="Shazam Title", artist="Shazam Artist",
            album="Shazam Album",
            source="shazam",
        )

    with patch(
        "domovoi.workers.library_enricher._enrich_via_acoustid",
        _ac_miss,
    ), patch(
        "domovoi.workers.library_enricher._enrich_via_shazam",
        _shz_match,
    ):
        counts = await enrich_library()

    assert counts["matched"] == 1
    row = (await db_session.execute(
        text("SELECT title, artist, album FROM library_tracks")
    )).first()
    assert row.title == "Shazam Title"
    assert row.artist == "Shazam Artist"
    assert row.album == "Shazam Album"


@requires_db
@pytest.mark.asyncio
async def test_enrich_library_stamps_no_match_so_we_dont_retry(
    db_session, monkeypatch, tmp_path,
) -> None:
    """When neither AcoustID nor Shazam can place the track, we still
    stamp enriched_at — otherwise every restart would re-hammer the
    APIs for tracks they can't identify."""
    monkeypatch.setattr(
        "domovoi.workers.library_enricher.settings.library_enricher_delay_sec", 0
    )
    f = tmp_path / "obscure.mp3"
    f.write_bytes(b"")
    await db_session.execute(
        text("INSERT INTO library_tracks (file_path, title) VALUES (:fp, :t)"),
        {"fp": str(f), "t": "obscure"},
    )
    await db_session.commit()

    async def _miss(file_path):
        return None

    with patch(
        "domovoi.workers.library_enricher._enrich_via_acoustid",
        _miss,
    ), patch(
        "domovoi.workers.library_enricher._enrich_via_shazam",
        _miss,
    ):
        counts = await enrich_library()

    assert counts["no_match"] == 1
    assert counts["matched"] == 0
    row = (await db_session.execute(
        text("SELECT enriched_at, title FROM library_tracks")
    )).first()
    assert row.enriched_at is not None  # stamped even on miss
    assert row.title == "obscure"  # untouched


@requires_db
@pytest.mark.asyncio
async def test_enrich_library_coalesce_preserves_existing_data(
    db_session, monkeypatch, tmp_path,
) -> None:
    """If the API returns title but no artist/album, the COALESCE on
    UPDATE must preserve the existing artist/album rather than
    overwriting them with NULL. Defends the indexer's filename-parse
    output when the API result is partial."""
    monkeypatch.setattr(
        "domovoi.workers.library_enricher.settings.library_enricher_delay_sec", 0
    )
    f = tmp_path / "track.mp3"
    f.write_bytes(b"")
    await db_session.execute(
        text(
            "INSERT INTO library_tracks (file_path, title, artist, album) "
            "VALUES (:fp, :t, :a, :al)"
        ),
        {"fp": str(f), "t": "Old Title", "a": "Old Artist", "al": "Old Album"},
    )
    await db_session.commit()

    async def _partial(file_path):
        # Title-only result; artist/album NULL.
        return EnrichmentResult(title="New Title", source="acoustid")

    async def _miss(file_path):
        return None

    with patch(
        "domovoi.workers.library_enricher._enrich_via_acoustid",
        _partial,
    ), patch(
        "domovoi.workers.library_enricher._enrich_via_shazam",
        _miss,
    ):
        await enrich_library()

    row = (await db_session.execute(
        text("SELECT title, artist, album FROM library_tracks")
    )).first()
    assert row.title == "New Title"   # updated
    assert row.artist == "Old Artist"  # preserved
    assert row.album == "Old Album"    # preserved


@requires_db
@pytest.mark.asyncio
async def test_enrich_library_skips_missing_files(
    db_session, monkeypatch, tmp_path,
) -> None:
    """A library_tracks row pointing at a deleted file shouldn't try
    to fingerprint a nonexistent path. Stamp enriched_at so we don't
    re-evaluate; counts surface the skip."""
    monkeypatch.setattr(
        "domovoi.workers.library_enricher.settings.library_enricher_delay_sec", 0
    )
    ghost = tmp_path / "deleted.mp3"  # never created
    await db_session.execute(
        text("INSERT INTO library_tracks (file_path, title) VALUES (:fp, :t)"),
        {"fp": str(ghost), "t": "deleted"},
    )
    await db_session.commit()

    async def _explode(file_path):
        raise AssertionError("must not call API for nonexistent file")

    with patch(
        "domovoi.workers.library_enricher._enrich_via_acoustid",
        _explode,
    ), patch(
        "domovoi.workers.library_enricher._enrich_via_shazam",
        _explode,
    ):
        counts = await enrich_library()

    assert counts["skipped_missing_file"] == 1
    assert counts["matched"] == 0
    assert counts["no_match"] == 0
