"""Acquisition queue service (design §4.8) against the real test DB:
enqueue dedup layers, SKIP LOCKED claim + kind/url_matcher filtering,
complete with soft playlist attach, fail/retry/terminal, graceful
absence, reconciliation helper."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from domovoi.acquisitions import ACQUISITIONS, AcquisitionService
from domovoi.capabilities import (
    ACQUISITION_ABSENCE_MESSAGE,
    CAPABILITIES,
    MEDIA_ACQUISITION_FULFILLER,
)
from domovoi.config import settings
from domovoi.tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _clean_fulfillers():
    """Each test starts with no registered fulfillers and leaves none."""
    for slug in list(CAPABILITIES.providers_for(MEDIA_ACQUISITION_FULFILLER)):
        CAPABILITIES.unregister(MEDIA_ACQUISITION_FULFILLER, slug=slug)
    yield
    for slug in list(CAPABILITIES.providers_for(MEDIA_ACQUISITION_FULFILLER)):
        CAPABILITIES.unregister(MEDIA_ACQUISITION_FULFILLER, slug=slug)


# ─── Enqueue + dedup layers ────────────────────────────────────────────────


async def test_enqueue_inserts_pending_row(db_session) -> None:
    result = await ACQUISITIONS.enqueue(
        db_session, kind="query", text="artist song", requested_by="web",
    )
    assert result.outcome == "enqueued"
    assert result.acquisition is not None
    assert result.acquisition.status == "pending"
    assert result.acquisition.requested_by == "web"
    row = (
        await db_session.execute(
            text("SELECT kind, text, status FROM media_acquisitions WHERE id = :id"),
            {"id": result.acquisition.id},
        )
    ).first()
    assert row is not None and row.kind == "query" and row.status == "pending"


async def test_enqueue_absence_message_when_no_fulfiller(db_session) -> None:
    result = await ACQUISITIONS.enqueue(
        db_session, kind="query", text="some song", requested_by="voice:playlist",
    )
    assert result.user_message == ACQUISITION_ABSENCE_MESSAGE


async def test_enqueue_message_when_fulfiller_present(db_session) -> None:
    ACQUISITIONS.register_fulfiller("providerx", kinds={"query"})
    result = await ACQUISITIONS.enqueue(
        db_session, kind="query", text="some song", requested_by="web",
    )
    assert result.outcome == "enqueued"
    assert result.user_message != ACQUISITION_ABSENCE_MESSAGE


async def test_enqueue_library_fuzzy_dedup(db_session) -> None:
    """Layer 1: a fuzzy library hit means no row is inserted."""
    await db_session.execute(
        text(
            "INSERT INTO library_tracks (file_path, title, artist) "
            "VALUES ('/music/Radiohead/Creep.mp3', 'Creep', 'Radiohead')"
        )
    )
    result = await ACQUISITIONS.enqueue(
        db_session, kind="query", text="Creep",
        metadata={"title": "Creep", "artist": "Radiohead"},
        requested_by="web",
    )
    assert result.outcome == "already_in_library"
    assert result.library_match is not None
    assert "already in your library" in result.user_message
    count = (
        await db_session.execute(text("SELECT COUNT(*) FROM media_acquisitions"))
    ).scalar_one()
    assert count == 0


async def test_enqueue_live_dedup_key(db_session) -> None:
    """Layer 2: a live (pending/claimed) row with the same dedup_key
    dedups; a terminal one does not block a re-request."""
    first = await ACQUISITIONS.enqueue(
        db_session, kind="url", text="https://x.test/v/abc",
        requested_by="web", dedup_key="providerx:video:abc",
        skip_library_dedup=True,
    )
    assert first.outcome == "enqueued"

    dup = await ACQUISITIONS.enqueue(
        db_session, kind="url", text="https://x.test/v/abc",
        requested_by="web", dedup_key="providerx:video:abc",
        skip_library_dedup=True,
    )
    assert dup.outcome == "duplicate"
    assert dup.duplicate_of_id == first.acquisition.id

    # Terminal rows leave the partial index — re-request allowed.
    await db_session.execute(
        text("UPDATE media_acquisitions SET status = 'failed' WHERE id = :id"),
        {"id": first.acquisition.id},
    )
    again = await ACQUISITIONS.enqueue(
        db_session, kind="url", text="https://x.test/v/abc",
        requested_by="web", dedup_key="providerx:video:abc",
        skip_library_dedup=True,
    )
    assert again.outcome == "enqueued"


async def test_enqueue_validates_kind(db_session) -> None:
    with pytest.raises(ValueError, match="kind"):
        await ACQUISITIONS.enqueue(
            db_session, kind="magic", text="x", requested_by="web",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="text is required"):
        await ACQUISITIONS.enqueue(
            db_session, kind="query", text="  ", requested_by="web",
        )


# ─── Claim ────────────────────────────────────────────────────────────────


async def test_claim_next_kinds_filter_and_ordering(db_session) -> None:
    ACQUISITIONS.register_fulfiller("providerx", kinds={"query"})
    a = await ACQUISITIONS.enqueue(
        db_session, kind="query", text="first", requested_by="web",
    )
    await ACQUISITIONS.enqueue(
        db_session, kind="url", text="https://x.test/skip-me",
        requested_by="web", skip_library_dedup=True,
    )
    b = await ACQUISITIONS.enqueue(
        db_session, kind="query", text="second", requested_by="web",
    )

    claimed = await ACQUISITIONS.claim_next(db_session, slug="providerx")
    assert claimed is not None
    assert claimed.id == a.acquisition.id          # oldest first
    assert claimed.status == "claimed"
    assert claimed.claimed_by == "providerx"
    assert claimed.attempts == 1

    claimed2 = await ACQUISITIONS.claim_next(db_session, slug="providerx")
    assert claimed2 is not None and claimed2.id == b.acquisition.id

    # Only the url row remains, which this fulfiller can't claim.
    assert await ACQUISITIONS.claim_next(db_session, slug="providerx") is None


async def test_claim_next_url_matcher_filters_in_python(db_session) -> None:
    ACQUISITIONS.register_fulfiller(
        "providerx", kinds={"url"},
        url_matcher=lambda url: "goodhost" in url,
    )
    await ACQUISITIONS.enqueue(
        db_session, kind="url", text="https://badhost.test/v/1",
        requested_by="web", skip_library_dedup=True,
    )
    good = await ACQUISITIONS.enqueue(
        db_session, kind="url", text="https://goodhost.test/v/2",
        requested_by="web", skip_library_dedup=True,
    )
    claimed = await ACQUISITIONS.claim_next(db_session, slug="providerx")
    assert claimed is not None and claimed.id == good.acquisition.id
    # The unmatched URL stays pending for a fulfiller that understands it.
    left = (
        await db_session.execute(
            text("SELECT status FROM media_acquisitions WHERE text LIKE '%badhost%'")
        )
    ).scalar_one()
    assert left == "pending"


async def test_claim_next_requires_registration(db_session) -> None:
    with pytest.raises(ValueError, match="not a registered"):
        await ACQUISITIONS.claim_next(db_session, slug="ghost")


async def test_claim_skip_locked_under_concurrency(db_session) -> None:
    """Two sessions claiming concurrently never get the same row
    (SELECT ... FOR UPDATE SKIP LOCKED)."""
    from domovoi.db.session import SessionLocal

    ACQUISITIONS.register_fulfiller("providerx", kinds={"query"})
    a = await ACQUISITIONS.enqueue(
        db_session, kind="query", text="only row", requested_by="web",
    )
    await db_session.commit()   # make it visible to other connections
    try:
        async with SessionLocal() as s1, SessionLocal() as s2:
            c1 = await ACQUISITIONS.claim_next(s1, slug="providerx")
            # s1 holds the row lock (uncommitted claim); s2 must skip it.
            c2 = await ACQUISITIONS.claim_next(s2, slug="providerx")
            assert c1 is not None and c1.id == a.acquisition.id
            assert c2 is None
            await s1.rollback()
            await s2.rollback()
    finally:
        async with SessionLocal() as s:
            await s.execute(text("DELETE FROM media_acquisitions"))
            await s.commit()


# ─── Complete / fail ──────────────────────────────────────────────────────


async def test_complete_attaches_to_surviving_playlist(db_session) -> None:
    ACQUISITIONS.register_fulfiller("providerx", kinds={"query"})
    pid = (
        await db_session.execute(
            text("INSERT INTO playlists (name) VALUES ('Road trip') RETURNING id")
        )
    ).scalar_one()
    tid = (
        await db_session.execute(
            text(
                "INSERT INTO library_tracks (file_path, title) "
                "VALUES ('/music/x.mp3', 'X') RETURNING id"
            )
        )
    ).scalar_one()
    result = await ACQUISITIONS.enqueue(
        db_session, kind="query", text="x", requested_by="voice:playlist",
        attach_to_playlist_id=int(pid),
    )
    claimed = await ACQUISITIONS.claim_next(db_session, slug="providerx")
    await ACQUISITIONS.complete(
        db_session, claimed.id, library_track_id=int(tid), file_path="/music/x.mp3",
    )
    row = (
        await db_session.execute(
            text(
                "SELECT status, result FROM media_acquisitions WHERE id = :id"
            ),
            {"id": claimed.id},
        )
    ).first()
    assert row.status == "done"
    assert row.result["library_track_id"] == int(tid)
    attached = (
        await db_session.execute(
            text(
                "SELECT 1 FROM playlist_tracks WHERE playlist_id = :p AND track_id = :t"
            ),
            {"p": int(pid), "t": int(tid)},
        )
    ).first()
    assert attached is not None
    assert result.acquisition.attach_to_playlist_id == int(pid)


async def test_complete_skips_attach_when_playlist_died(db_session) -> None:
    """Soft-ref survival (locked 5): a playlist deleted mid-download
    never fails the completion."""
    ACQUISITIONS.register_fulfiller("providerx", kinds={"query"})
    tid = (
        await db_session.execute(
            text(
                "INSERT INTO library_tracks (file_path, title) "
                "VALUES ('/music/y.mp3', 'Y') RETURNING id"
            )
        )
    ).scalar_one()
    await ACQUISITIONS.enqueue(
        db_session, kind="query", text="y", requested_by="voice:playlist",
        attach_to_playlist_id=424242,   # never existed / already deleted
    )
    claimed = await ACQUISITIONS.claim_next(db_session, slug="providerx")
    await ACQUISITIONS.complete(
        db_session, claimed.id, library_track_id=int(tid), file_path="/music/y.mp3",
    )
    status = (
        await db_session.execute(
            text("SELECT status FROM media_acquisitions WHERE id = :id"),
            {"id": claimed.id},
        )
    ).scalar_one()
    assert status == "done"


async def test_fail_retry_then_terminal(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "acquisition_max_attempts", 2)
    ACQUISITIONS.register_fulfiller("providerx", kinds={"query"})
    await ACQUISITIONS.enqueue(
        db_session, kind="query", text="flaky", requested_by="web",
    )

    claimed = await ACQUISITIONS.claim_next(db_session, slug="providerx")
    await ACQUISITIONS.fail(
        db_session, claimed.id, error="boom", retry_in=timedelta(seconds=0),
    )
    row = (
        await db_session.execute(
            text(
                "SELECT status, claimed_by, error FROM media_acquisitions WHERE id = :id"
            ),
            {"id": claimed.id},
        )
    ).first()
    assert row.status == "pending" and row.claimed_by is None and row.error == "boom"

    # Second attempt reaches max_attempts → terminal 'failed'.
    claimed2 = await ACQUISITIONS.claim_next(db_session, slug="providerx")
    assert claimed2 is not None and claimed2.attempts == 2
    await ACQUISITIONS.fail(
        db_session, claimed2.id, error="boom again", retry_in=timedelta(seconds=0),
    )
    status = (
        await db_session.execute(
            text("SELECT status FROM media_acquisitions WHERE id = :id"),
            {"id": claimed2.id},
        )
    ).scalar_one()
    assert status == "failed"


async def test_fail_unfulfillable_terminal(db_session) -> None:
    ACQUISITIONS.register_fulfiller("providerx", kinds={"query"})
    await ACQUISITIONS.enqueue(
        db_session, kind="query", text="dupe", requested_by="web",
    )
    claimed = await ACQUISITIONS.claim_next(db_session, slug="providerx")
    await ACQUISITIONS.fail(
        db_session, claimed.id, error="duplicate post-resolve", unfulfillable=True,
    )
    status = (
        await db_session.execute(
            text("SELECT status FROM media_acquisitions WHERE id = :id"),
            {"id": claimed.id},
        )
    ).scalar_one()
    assert status == "unfulfillable"


# ─── Availability / absence / reconciliation ──────────────────────────────


async def test_availability_reflects_registrations(db_session) -> None:
    service = ACQUISITIONS
    empty = service.availability()
    assert empty.fulfillers == []
    assert not empty.can_fulfill_query and not empty.can_fulfill_url

    service.register_fulfiller("providerx", kinds={"query"})
    avail = service.availability()
    assert avail.fulfillers == ["providerx"]
    assert avail.can_fulfill_query and not avail.can_fulfill_url

    service.unregister_fulfiller("providerx")
    assert service.availability().fulfillers == []


async def test_register_fulfiller_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown kinds"):
        AcquisitionService().register_fulfiller("x", kinds={"torrent"})


async def test_completed_for_origin_reconciliation(db_session) -> None:
    """The §4.9-mandated sweep query: re-correlate completions the bus
    missed, by origin_ref prefix."""
    ACQUISITIONS.register_fulfiller("providerx", kinds={"query"})
    await ACQUISITIONS.enqueue(
        db_session, kind="query", text="a", requested_by="plugin:example",
        origin_ref="plugin_example:mytable:1",
    )
    await ACQUISITIONS.enqueue(
        db_session, kind="query", text="b", requested_by="web",
    )
    claimed = await ACQUISITIONS.claim_next(db_session, slug="providerx")
    while claimed is not None:
        await ACQUISITIONS.complete(
            db_session, claimed.id, library_track_id=1, file_path="/m/x.mp3",
        )
        claimed = await ACQUISITIONS.claim_next(db_session, slug="providerx")

    hits = await ACQUISITIONS.completed_for_origin(
        db_session, origin_ref_prefix="plugin_example:",
    )
    assert len(hits) == 1
    assert hits[0].origin_ref == "plugin_example:mytable:1"
