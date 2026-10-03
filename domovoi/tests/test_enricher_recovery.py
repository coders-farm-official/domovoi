"""B3's one-off recovery: tracks stamped "done" before any provider could
answer get a real try once a provider exists — and nothing that was
genuinely matched, or corrected by hand, is redone or overwritten.

How the rows are told apart (V020 + ``library_enricher.requeue_legacy``):

* V020 adds ``enrich_outcome`` and marks every row that was stamped AND
  carries a ``musicbrainz_recording_id`` as ``matched``: an AcoustID match
  always wrote one, so those are proven answers. They are never requeued.
* A stamped row with no outcome and no MusicBrainz id is a LEGACY stamp:
  the old enricher's "no provider / network error / no match" (the
  Beelink's 5,232 rows), a pre-V020 dashboard edit, or a Shazam match
  (Shazam gives no MusicBrainz id). The data can't tell those apart, so
  the recovery requeues them ONCE (outcome ``recheck``) — and only after a
  provider has actually answered for some file in that sweep — and a
  recheck match only FILLS EMPTY fields: a hand correction and a Shazam
  match's tags survive; the cost of a legacy Shazam match is one more
  lookup.
* Every stamp the new code writes carries an outcome, so a legacy row
  exists only until its first post-V020 judgement: the recovery is
  one-off by construction. A dashboard edit since V020 writes ``manual``.
* Nothing is requeued while no provider is available.

These run on the lane's database with V020 applied (and re-applied after
the legacy rows are inserted, which is what Flyway does on a live box:
the migration's UPDATE sees the existing rows).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from domovoi.tests.enrich_outcome_testkit import apply_v020
from domovoi.workers import library_enricher as le
from domovoi.workers.library_enricher import EnrichmentResult, Lookup, Verdict, enrich_library

ENRICHER = "domovoi.workers.library_enricher"


@pytest.fixture
async def lane(db_session, monkeypatch):
    await apply_v020()
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_enabled", True)
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_delay_sec", 0)
    yield db_session


async def _seed_live_like(session, tmp_path) -> dict[str, int]:
    """One row of every kind a pre-V020 library holds, as the old code
    left them. Files exist (so the sweep would look them up)."""
    def f(name: str) -> str:
        p = tmp_path / name
        p.write_bytes(b"")
        return str(p)

    rows = {
        # stamped with no provider configured: the Beelink's 5,232
        "legacy_unanswered": (f("01.mp3"), "01 - track", None, None, "NOW()"),
        # genuinely matched by AcoustID: carries an MB id
        "acoustid_matched": (f("02.mp3"), "Creep", "Radiohead", "mb-creep", "NOW()"),
        # corrected by hand in the dashboard before V020 (stamped, no MB id)
        "hand_edited": (f("03.mp3"), "My Corrected Title", None, None, "NOW()"),
        # never enriched yet
        "unenriched": (f("04.mp3"), "04 - track", None, None, "NULL"),
    }
    ids = {}
    for key, (fp, title, artist, mb, stamp) in rows.items():
        row = (await session.execute(
            text(
                "INSERT INTO library_tracks (file_path, title, artist, musicbrainz_recording_id, enriched_at) "
                f"VALUES (:fp, :t, :a, :mb, {stamp}) RETURNING id"
            ),
            {"fp": fp, "t": title, "a": artist, "mb": mb},
        )).first()
        ids[key] = int(row[0])
    await session.commit()
    # Flyway applying V020 to a database that already holds these rows.
    await apply_v020()
    return ids


async def _row(session, track_id: int):
    return (await session.execute(
        text(
            "SELECT title, artist, album, musicbrainz_recording_id, enriched_at, enrich_outcome "
            "FROM library_tracks WHERE id = :id"
        ),
        {"id": track_id},
    )).first()


def _recording(monkeypatch, verdict_for):
    """Patch both providers; AcoustID answers ``verdict_for(path)``,
    Shazam is not installed. Returns the list of files asked about."""
    asked: list[str] = []

    async def _ac(path):
        asked.append(path.name)
        return verdict_for(path)

    async def _sh(_path):
        return Lookup(Verdict.NOT_ASKED, detail="shazamio not installed")

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", _ac)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", _sh)
    return asked


@requires_db
@pytest.mark.asyncio
async def test_v020_marks_only_rows_with_a_musicbrainz_id_matched(lane, tmp_path) -> None:
    ids = await _seed_live_like(lane, tmp_path)
    assert (await _row(lane, ids["acoustid_matched"])).enrich_outcome == "matched"
    for key in ("legacy_unanswered", "hand_edited", "unenriched"):
        assert (await _row(lane, ids[key])).enrich_outcome is None, key


@requires_db
@pytest.mark.asyncio
async def test_no_provider_requeues_nothing(lane, monkeypatch, tmp_path) -> None:
    ids = await _seed_live_like(lane, tmp_path)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: False)
    asked = _recording(monkeypatch, lambda _p: pytest.fail("asked without a provider"))
    counts = await enrich_library()
    assert counts["skipped"] == "no_provider" and counts["requeued_legacy"] == 0
    assert asked == []
    legacy = await _row(lane, ids["legacy_unanswered"])
    assert legacy.enriched_at is not None and legacy.enrich_outcome is None


@requires_db
@pytest.mark.asyncio
async def test_with_a_provider_legacy_rows_get_one_real_try(lane, monkeypatch, tmp_path) -> None:
    ids = await _seed_live_like(lane, tmp_path)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    found = {
        "01.mp3": Lookup(Verdict.MATCH, result=EnrichmentResult(
            title="Real Title", artist="Real Artist", musicbrainz_recording_id="mb-01", source="acoustid")),
        "03.mp3": Lookup(Verdict.MATCH, result=EnrichmentResult(
            title="Catalog Title", artist="Catalog Artist", musicbrainz_recording_id="mb-03", source="acoustid")),
        "04.mp3": Lookup(Verdict.NO_MATCH, detail="nothing"),
    }
    asked = _recording(monkeypatch, lambda p: found[p.name])
    before_matched = await _row(lane, ids["acoustid_matched"])

    counts = await enrich_library()

    assert counts["requeued_legacy"] == 2          # legacy_unanswered + hand_edited
    assert sorted(asked) == ["01.mp3", "03.mp3", "04.mp3"]
    assert "02.mp3" not in asked                   # the genuine match is never redone
    assert await _row(lane, ids["acoustid_matched"]) == before_matched

    legacy = await _row(lane, ids["legacy_unanswered"])
    # Fill-only: an existing title is kept, the empty artist and MB id filled.
    assert (legacy.title, legacy.artist, legacy.musicbrainz_recording_id) == (
        "01 - track", "Real Artist", "mb-01",
    )
    assert legacy.enrich_outcome == "matched" and legacy.enriched_at is not None

    hand = await _row(lane, ids["hand_edited"])
    assert hand.title == "My Corrected Title"      # the hand correction survives
    assert (hand.artist, hand.musicbrainz_recording_id, hand.enrich_outcome) == (
        "Catalog Artist", "mb-03", "matched",
    )

    new = await _row(lane, ids["unenriched"])
    assert new.enrich_outcome == "no_match" and new.enriched_at is not None


@requires_db
@pytest.mark.asyncio
async def test_the_recovery_is_one_off(lane, monkeypatch, tmp_path) -> None:
    await _seed_live_like(lane, tmp_path)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    _recording(monkeypatch, lambda _p: Lookup(Verdict.NO_MATCH))
    first = await enrich_library()
    assert first["requeued_legacy"] == 2
    asked = _recording(monkeypatch, lambda _p: pytest.fail("asked again"))
    second = await enrich_library()
    assert second["requeued_legacy"] == 0
    assert second["scanned"] == 0 and asked == []


@requires_db
@pytest.mark.asyncio
async def test_a_recheck_that_errors_waits_for_the_next_sweep(lane, monkeypatch, tmp_path) -> None:
    ids = await _seed_live_like(lane, tmp_path)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    _recording(monkeypatch, lambda _p: Lookup(Verdict.ERROR, detail="down", transient=True))
    counts = await enrich_library()
    # Nothing answered, so nothing was requeued (2026-10-03 review): the
    # legacy rows stay exactly as they were, and are never stamped by an
    # error.
    assert counts["requeued_legacy"] == 0 and counts["errors"] == 3
    legacy = await _row(lane, ids["legacy_unanswered"])
    assert legacy.enriched_at is not None and legacy.enrich_outcome is None
    # The next sweep with an answer requeues them then, and they are still
    # fill-only.
    _recording(monkeypatch, lambda _p: Lookup(Verdict.MATCH, result=EnrichmentResult(
        title="Overwrite?", artist="Filled", source="shazam")))
    again = await enrich_library()
    assert again["requeued_legacy"] == 2 and again["matched"] == 3
    legacy = await _row(lane, ids["legacy_unanswered"])
    assert (legacy.title, legacy.artist, legacy.enrich_outcome) == ("01 - track", "Filled", "matched")


@requires_db
@pytest.mark.asyncio
async def test_rows_with_an_outcome_are_never_requeued(lane, monkeypatch, tmp_path) -> None:
    for i, outcome in enumerate(("matched", "no_match", "missing_file", "manual")):
        p = tmp_path / f"o{i}.mp3"
        p.write_bytes(b"")
        await lane.execute(
            text(
                "INSERT INTO library_tracks (file_path, title, enriched_at, enrich_outcome) "
                "VALUES (:fp, 't', NOW(), :o)"
            ),
            {"fp": str(p), "o": outcome},
        )
    await lane.commit()
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    asked = _recording(monkeypatch, lambda _p: pytest.fail("asked about a decided row"))
    counts = await enrich_library()
    assert counts["requeued_legacy"] == 0 and counts["scanned"] == 0 and asked == []


@requires_db
@pytest.mark.asyncio
async def test_a_dashboard_edit_is_manual_and_never_requeued(lane, monkeypatch, tmp_path) -> None:
    from web.backend.api import music as music_api
    from web.backend.schemas import TrackPatch

    p = tmp_path / "mine.mp3"
    p.write_bytes(b"")
    row = (await lane.execute(
        text("INSERT INTO library_tracks (file_path, title) VALUES (:fp, 'typo') RETURNING id"),
        {"fp": str(p)},
    )).first()
    await lane.commit()
    track_id = int(row[0])

    out = await music_api.patch_track(track_id, TrackPatch(title="Fixed By Hand"))
    assert out.title == "Fixed By Hand"
    edited = await _row(lane, track_id)
    assert edited.enrich_outcome == "manual" and edited.enriched_at is not None

    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    asked = _recording(monkeypatch, lambda _p: pytest.fail("asked about a hand edit"))
    counts = await enrich_library()
    assert counts["requeued_legacy"] == 0 and asked == []
    assert (await _row(lane, track_id)).title == "Fixed By Hand"

    # A favorite flip is not a metadata edit: it records no outcome.
    other = (await lane.execute(
        text("INSERT INTO library_tracks (file_path, title) VALUES ('/m/fav.mp3', 'fav') RETURNING id"),
    )).first()
    await lane.commit()
    await music_api.patch_track(int(other[0]), TrackPatch(favorited=True))
    fav = await _row(lane, int(other[0]))
    assert fav.enrich_outcome is None and fav.enriched_at is None


@requires_db
@pytest.mark.asyncio
async def test_requeue_sql_matches_the_documented_condition(lane, tmp_path) -> None:
    """``requeue_legacy`` and ``waiting_count`` share one condition."""
    await _seed_live_like(lane, tmp_path)
    assert await le.waiting_count(lane) == 3   # unenriched + 2 legacy
    assert await le.requeue_legacy(lane) == 2
    await lane.commit()
    assert await le.requeue_legacy(lane) == 0
    assert await le.waiting_count(lane) == 3   # now all three are unenriched
