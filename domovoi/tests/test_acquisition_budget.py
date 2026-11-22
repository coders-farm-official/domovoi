"""Acquisitions get a time and size budget, whatever the fulfilling plugin's
own limits are (domovoi/acquisitions.py, "The budget").

* a claimed row past ``acquisition_max_seconds`` is failed for good by the
  sweep (the core worker's tick), and a late report never revives it;
* ``complete()`` refuses a result past the time budget, or whose file is
  bigger than ``acquisition_max_bytes``, removes the file, and the
  fulfiller's failure report then ends the row for good;
* add-by-URL without an admin session queues one link at a time, never a
  backlog: past ``acquisition_open_url_pending_cap`` waiting, it is a 429.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import main as core_main
from domovoi.acquisitions import (
    ACQUISITIONS,
    OVER_SIZE_BUDGET,
    OVER_TIME_BUDGET,
    AcquisitionOverBudget,
)
from domovoi.admin_auth import OutboundFetchDecision
from domovoi.capabilities import CAPABILITIES, MEDIA_ACQUISITION_FULFILLER
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.tests.conftest import requires_db
from domovoi.workers.acquisition_budget import AcquisitionBudgetSweeper

pytestmark = [requires_db, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _fulfiller():
    for slug in list(CAPABILITIES.providers_for(MEDIA_ACQUISITION_FULFILLER)):
        CAPABILITIES.unregister(MEDIA_ACQUISITION_FULFILLER, slug=slug)
    ACQUISITIONS.register_fulfiller("providerx", kinds={"query", "url"})
    ACQUISITIONS._over_budget.clear()
    yield
    CAPABILITIES.unregister(MEDIA_ACQUISITION_FULFILLER, slug="providerx")
    ACQUISITIONS._over_budget.clear()


async def _claimed(session, *, started_ago_sec: float = 0.0):
    await ACQUISITIONS.enqueue(session, kind="query", text="a song", requested_by="web")
    acq = await ACQUISITIONS.claim_next(session, slug="providerx")
    if started_ago_sec:
        await session.execute(
            text(
                "UPDATE media_acquisitions SET claimed_at = NOW() - "
                "make_interval(secs => :s) WHERE id = :id"
            ),
            {"s": started_ago_sec, "id": acq.id},
        )
    return acq


async def _status(session, acq_id):
    return (
        await session.execute(
            text("SELECT status, error FROM media_acquisitions WHERE id = :id"),
            {"id": acq_id},
        )
    ).first()


async def _track(session, path) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO library_tracks (file_path, title) "
                    "VALUES (:p, 'X') RETURNING id"
                ),
                {"p": str(path)},
            )
        ).scalar_one()
    )


async def test_a_row_past_its_time_budget_is_failed_by_the_sweep(
    db_session, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "acquisition_max_seconds", 600.0)
    stuck = await _claimed(db_session, started_ago_sec=601)
    fresh = await _claimed(db_session)
    assert await ACQUISITIONS.sweep_over_budget(db_session) == 1
    assert tuple(await _status(db_session, stuck.id)) == ("failed", OVER_TIME_BUDGET)
    assert (await _status(db_session, fresh.id)).status == "claimed"

    # The stuck fulfiller reports its failure late, asking for a retry:
    # the row stays failed.
    await ACQUISITIONS.fail(
        db_session, stuck.id, error="boom", retry_in=timedelta(seconds=0)
    )
    assert tuple(await _status(db_session, stuck.id)) == ("failed", OVER_TIME_BUDGET)


async def test_the_core_worker_runs_the_sweep(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "acquisition_max_seconds", 60.0)
    async with session_scope() as s:
        stuck = await _claimed(s, started_ago_sec=120)
        await s.commit()
    assert await AcquisitionBudgetSweeper().tick() == 1
    assert (await _status(db_session, stuck.id)).status == "failed"


async def test_a_completion_past_the_time_budget_is_refused_and_its_file_removed(
    db_session, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "music_dir", str(tmp_path))
    monkeypatch.setattr(settings, "acquisition_max_seconds", 600.0)
    acq = await _claimed(db_session, started_ago_sec=900)
    f = tmp_path / "Artist" / "Song.mp3"
    f.parent.mkdir()
    f.write_bytes(b"x" * 10)
    tid = await _track(db_session, f)
    with pytest.raises(AcquisitionOverBudget, match="time"):
        await ACQUISITIONS.complete(
            db_session, acq.id, library_track_id=tid, file_path=str(f)
        )
    assert not f.exists()
    # The fulfiller reports the failure and asks for a retry; the budget
    # makes it final.
    await ACQUISITIONS.fail(
        db_session, acq.id, error="refused", retry_in=timedelta(seconds=0)
    )
    assert tuple(await _status(db_session, acq.id)) == ("failed", OVER_TIME_BUDGET)


async def test_a_completion_past_the_size_budget_is_refused_and_its_file_removed(
    db_session, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "music_dir", str(tmp_path))
    monkeypatch.setattr(settings, "acquisition_max_bytes", 1000)
    acq = await _claimed(db_session)
    big = tmp_path / "big.mp3"
    big.write_bytes(b"x" * 1001)
    tid = await _track(db_session, big)
    with pytest.raises(AcquisitionOverBudget, match="size"):
        await ACQUISITIONS.complete(
            db_session, acq.id, library_track_id=tid, file_path=str(big)
        )
    assert not big.exists()
    await ACQUISITIONS.fail(
        db_session, acq.id, error="refused", retry_in=timedelta(seconds=0)
    )
    assert tuple(await _status(db_session, acq.id)) == ("failed", OVER_SIZE_BUDGET)


async def test_a_completion_within_budget_is_done(db_session, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "music_dir", str(tmp_path))
    monkeypatch.setattr(settings, "acquisition_max_bytes", 1000)
    acq = await _claimed(db_session, started_ago_sec=5)
    ok = tmp_path / "ok.mp3"
    ok.write_bytes(b"x" * 1000)
    tid = await _track(db_session, ok)
    await ACQUISITIONS.complete(db_session, acq.id, library_track_id=tid, file_path=str(ok))
    assert (await _status(db_session, acq.id)).status == "done"
    assert ok.exists()


async def test_a_refused_file_outside_the_music_folder_is_left_alone(
    db_session, monkeypatch, tmp_path
) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr(settings, "music_dir", str(music))
    monkeypatch.setattr(settings, "acquisition_max_bytes", 10)
    acq = await _claimed(db_session)
    elsewhere = tmp_path / "other.bin"
    elsewhere.write_bytes(b"x" * 100)
    tid = await _track(db_session, elsewhere)
    with pytest.raises(AcquisitionOverBudget):
        await ACQUISITIONS.complete(
            db_session, acq.id, library_track_id=tid, file_path=str(elsewhere)
        )
    assert elsewhere.exists()


# ─── add-by-URL without an admin session ─────────────────────────────────


async def _add(url: str):
    async with AsyncClient(
        transport=ASGITransport(app=core_main.app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        return await c.post(
            "/v1/admin/music/add-by-url", json={"room_id": "kitchen", "url": url}
        )


@pytest.fixture
def open_path(monkeypatch):
    """The outbound-fetch gate let a caller without an admin session in."""
    monkeypatch.setattr(settings, "acquisition_open_url_pending_cap", 2)

    async def _allowed(request, url, *, url_allowed_by_fulfillers):
        return OutboundFetchDecision(True)

    monkeypatch.setattr(core_main, "check_outbound_fetch", _allowed)


async def test_an_open_add_by_url_queues_one_link_but_not_a_backlog(
    db_session, open_path
) -> None:
    first = await _add("https://93.184.215.14/watch?v=1")
    second = await _add("https://93.184.215.14/watch?v=2")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    third = await _add("https://93.184.215.14/watch?v=3")
    assert third.status_code == 429, third.text
    waiting = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM media_acquisitions WHERE kind = 'url'")
        )
    ).scalar_one()
    assert waiting == 2


async def test_an_admin_add_by_url_is_not_capped(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "acquisition_open_url_pending_cap", 1)

    async def _admin(request, url, *, url_allowed_by_fulfillers):
        return OutboundFetchDecision(True, admin=True)

    monkeypatch.setattr(core_main, "check_outbound_fetch", _admin)
    for n in range(3):
        r = await _add(f"https://93.184.215.14/watch?v={n}")
        assert r.status_code == 200, r.text
