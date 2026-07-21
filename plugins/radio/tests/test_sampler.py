"""RadioSampler — due-station selection, the two-tier identify chain,
dedup, detection rows, and the post-commit observation event."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db

from domovoi_plugin_radio.clients import shazam_stream
from domovoi_plugin_radio.clients.shazam_stream import TrackIdentity
from domovoi_plugin_radio.workers.sampler import RadioSampler

pytestmark = requires_db


async def _insert_station(session, **kw) -> int:
    defaults = {
        "name": "KEXP",
        "source": "online",
        "stream_url": "http://kexp.example/stream.mp3",
        "favorited": True,
        "icy": None,
        "last_sampled": None,
    }
    defaults.update(kw)
    row = await session.execute(
        text(
            """
            INSERT INTO plugin_radio.radio_stations
                (name, source, stream_url, favorited, icy_supported,
                 last_sampled_at)
            VALUES (:name, :source, :stream_url, :favorited, :icy,
                    :last_sampled)
            RETURNING id
            """
        ),
        defaults,
    )
    await session.commit()
    return int(row.scalar_one())


async def _detections(session) -> list:
    rows = await session.execute(
        text(
            "SELECT station_id, artist, title, fingerprint_source, "
            "in_library FROM plugin_radio.radio_detections "
            "ORDER BY id"
        )
    )
    return rows.all()


def _sampler(radio_sdk) -> RadioSampler:
    return RadioSampler(radio_sdk)


def _capture_detection_events(radio_sdk) -> list:
    """Subscribe to the observation broadcast; returns the capture list."""
    seen: list = []

    async def _cb(event) -> None:
        seen.append(event)

    radio_sdk.events.subscribe("plugin.radio.detection_recorded", _cb)
    return seen


async def _flush_bus() -> None:
    """Emitted events deliver as their own tasks — let them run."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ─── Due-station selection ───────────────────────────────────────────────


async def test_selects_never_sampled_favorites(radio_sdk, db_session) -> None:
    sid = await _insert_station(db_session)
    due = await _sampler(radio_sdk)._select_due_stations()
    assert [r.id for r in due] == [sid]


async def test_excludes_icy_supported_stations(radio_sdk, db_session) -> None:
    """Stations the cheap metadata poller serves are never sampled;
    NULL (unprobed) and FALSE (confirmed no ICY) still are."""
    a = await _insert_station(db_session, name="A", icy=None)
    await _insert_station(db_session, name="B", icy=True)
    c = await _insert_station(db_session, name="C", icy=False)
    due = await _sampler(radio_sdk)._select_due_stations()
    assert sorted(r.id for r in due) == sorted([a, c])


async def test_excludes_unfavorited_and_recently_sampled(
    radio_sdk, db_session
) -> None:
    await _insert_station(db_session, name="A", favorited=False)
    await db_session.execute(
        text(
            "INSERT INTO plugin_radio.radio_stations "
            "(name, source, stream_url, favorited, last_sampled_at) "
            "VALUES ('B', 'online', 'http://b/x', TRUE, NOW())"
        )
    )
    await db_session.commit()
    assert await _sampler(radio_sdk)._select_due_stations() == []


# ─── Identification pipeline ─────────────────────────────────────────────


async def test_tick_records_detection_and_emits_event(
    radio_sdk, db_session, monkeypatch
) -> None:
    sid = await _insert_station(db_session)
    events = _capture_detection_events(radio_sdk)

    async def fake_grab(url, duration_sec, *, timeout_sec=20.0):
        return "fake.wav"

    monkeypatch.setattr(shazam_stream, "grab_to_tempfile", fake_grab)
    monkeypatch.setattr(
        "domovoi_plugin_radio.workers.sampler.shazam_stream.grab_to_tempfile",
        fake_grab,
    )

    sampler = _sampler(radio_sdk)

    async def fake_identify(wav_path):
        return TrackIdentity(title="Creep", artist="Radiohead"), "shazam", None

    monkeypatch.setattr(sampler, "_identify", fake_identify)
    monkeypatch.setattr("os.unlink", lambda *_: None)

    sampled = await sampler.tick()
    assert sampled == 1

    dets = await _detections(db_session)
    assert len(dets) == 1
    det = dets[0]
    assert det.station_id == sid
    assert det.artist == "Radiohead"
    assert det.title == "Creep"
    assert det.fingerprint_source == "shazam"
    assert det.in_library is False
    # The observation broadcast: one post-commit event carrying the full
    # detection — radio reports, it never acts.
    await _flush_bus()
    assert len(events) == 1
    payload = events[0].payload
    assert payload["station_id"] == sid
    assert payload["artist"] == "Radiohead"
    assert payload["title"] == "Creep"
    assert payload["fingerprint_source"] == "shazam"
    assert payload["in_library"] is False
    assert payload["likely_song"] is True
    # Radio itself never touches the acquisition queue.
    count = (
        await db_session.execute(text("SELECT COUNT(*) FROM media_acquisitions"))
    ).scalar_one()
    assert count == 0
    # last_sampled_at bumped.
    bumped = (
        await db_session.execute(
            text(
                "SELECT last_sampled_at FROM plugin_radio.radio_stations "
                "WHERE id = :id"
            ),
            {"id": sid},
        )
    ).scalar_one()
    assert bumped is not None


async def test_dedup_window_suppresses_repeat(
    radio_sdk, db_session
) -> None:
    sid = await _insert_station(db_session)
    sampler = _sampler(radio_sdk)
    from domovoi_plugin_radio.workers.sampler import _StationRow

    row = _StationRow(id=sid, name="KEXP", stream_url="http://x")
    ident = TrackIdentity(title="Creep", artist="Radiohead")
    await sampler._record_identification(
        station=row, identity=ident, source="shazam"
    )
    await sampler._record_identification(
        station=row, identity=ident, source="shazam"
    )
    dets = await _detections(db_session)
    assert len(dets) == 1


async def test_local_tier_hit_marks_in_library(
    radio_sdk, db_session
) -> None:
    sid = await _insert_station(db_session)
    events = _capture_detection_events(radio_sdk)
    sampler = _sampler(radio_sdk)
    from domovoi_plugin_radio.workers.sampler import _StationRow

    row = _StationRow(id=sid, name="KEXP", stream_url="http://x")
    await sampler._record_identification(
        station=row,
        identity=TrackIdentity(title="Creep", artist="Radiohead"),
        source="local",
        library_track_id=42,
    )
    (det,) = await _detections(db_session)
    assert det.fingerprint_source == "local"
    assert det.in_library is True
    await _flush_bus()
    assert len(events) == 1
    assert events[0].payload["in_library"] is True
    assert events[0].payload["library_track_id"] == 42


async def test_radio_never_writes_acquisitions(
    radio_sdk, db_session
) -> None:
    """The observation boundary: the same novel song on two stations
    produces two detection rows and two events — and zero acquisition
    rows. Acting on a detection is a subscriber's job, never radio's."""
    a = await _insert_station(db_session, name="A", stream_url="http://a/x")
    b = await _insert_station(db_session, name="B", stream_url="http://b/x")
    events = _capture_detection_events(radio_sdk)
    sampler = _sampler(radio_sdk)
    from domovoi_plugin_radio.workers.sampler import _StationRow

    ident = TrackIdentity(title="Creep", artist="Radiohead")
    await sampler._record_identification(
        station=_StationRow(id=a, name="A", stream_url="http://a/x"),
        identity=ident, source="shazam",
    )
    await sampler._record_identification(
        station=_StationRow(id=b, name="B", stream_url="http://b/x"),
        identity=ident, source="shazam",
    )
    dets = await _detections(db_session)
    assert len(dets) == 2
    await _flush_bus()
    assert len(events) == 2
    count = (
        await db_session.execute(text("SELECT COUNT(*) FROM media_acquisitions"))
    ).scalar_one()
    assert count == 0


async def test_failed_grab_bumps_last_sampled(
    radio_sdk, db_session, monkeypatch
) -> None:
    sid = await _insert_station(db_session)

    async def fake_grab(url, duration_sec, *, timeout_sec=20.0):
        return None      # dead stream

    monkeypatch.setattr(
        "domovoi_plugin_radio.workers.sampler.shazam_stream.grab_to_tempfile",
        fake_grab,
    )
    sampler = _sampler(radio_sdk)
    await sampler.tick()
    bumped = (
        await db_session.execute(
            text(
                "SELECT last_sampled_at FROM plugin_radio.radio_stations "
                "WHERE id = :id"
            ),
            {"id": sid},
        )
    ).scalar_one()
    assert bumped is not None
    assert await _detections(db_session) == []
