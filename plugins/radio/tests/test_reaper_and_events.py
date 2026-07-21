"""Detections reaper: retention (locked 19) and soft-ref sweeps — plus
the live bus subscriptions the sweep backs up (design §4.9: the bus is
latency, the sweep is truth)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db

from domovoi_plugin_radio.workers.detections_reaper import RadioDetectionsReaper

pytestmark = requires_db


async def _insert_station(session) -> int:
    row = await session.execute(
        text(
            "INSERT INTO plugin_radio.radio_stations (name, source, favorited) "
            "VALUES ('KEXP', 'online', TRUE) RETURNING id"
        )
    )
    await session.commit()
    return int(row.scalar_one())


async def _insert_detection(
    session, station_id: int, *, age_days: int = 0,
    library_track_id: int | None = None, in_library: bool = False,
) -> int:
    row = await session.execute(
        text(
            """
            INSERT INTO plugin_radio.radio_detections
                (station_id, artist, title, fingerprint_source, in_library,
                 library_track_id, detected_at)
            VALUES (:sid, 'A', 'T', 'icy', :in_lib, :lt,
                    NOW() - (:age * INTERVAL '1 day'))
            RETURNING id
            """
        ),
        {"sid": station_id, "age": age_days, "lt": library_track_id,
         "in_lib": in_library},
    )
    await session.commit()
    return int(row.scalar_one())


async def _insert_library_track(session, title="T") -> int:
    row = await session.execute(
        text(
            "INSERT INTO library_tracks (file_path, title, source, added_via) "
            "VALUES ('C:/music/t.mp3', :t, 'manual', 'manual') RETURNING id"
        ),
        {"t": title},
    )
    await session.commit()
    return int(row.scalar_one())


async def test_retention_reaps_old_rows_only(radio_sdk, db_session) -> None:
    sid = await _insert_station(db_session)
    old = await _insert_detection(db_session, sid, age_days=120)
    fresh = await _insert_detection(db_session, sid, age_days=1)
    stats = await RadioDetectionsReaper(radio_sdk).tick()
    assert stats["detections_reaped"] == 1
    remaining = (
        await db_session.execute(
            text("SELECT id FROM plugin_radio.radio_detections")
        )
    ).scalars().all()
    assert remaining == [fresh]
    assert old not in remaining


async def test_retention_zero_keeps_forever(
    radio_sdk, db_session, radio_settings
) -> None:
    radio_settings.detections_retention_days = 0
    sid = await _insert_station(db_session)
    await _insert_detection(db_session, sid, age_days=5000)
    stats = await RadioDetectionsReaper(radio_sdk).tick()
    assert stats["detections_reaped"] == 0


async def test_sweep_deletes_orphan_fingerprints_and_nulls_refs(
    radio_sdk, db_session
) -> None:
    track = await _insert_library_track(db_session)
    sid = await _insert_station(db_session)
    live_det = await _insert_detection(db_session, sid, library_track_id=track)
    dead_det = await _insert_detection(db_session, sid, library_track_id=999999)
    await db_session.execute(
        text(
            "INSERT INTO plugin_radio.track_fingerprints "
            "(library_track_id, hash, offset_ms) VALUES "
            "(:live, '\\x01'::bytea, 0), (999999, '\\x02'::bytea, 0)"
        ),
        {"live": track},
    )
    await db_session.commit()

    stats = await RadioDetectionsReaper(radio_sdk).tick()
    assert stats["fingerprints_orphaned"] == 1
    assert stats["track_refs_nulled"] == 1
    kept = (
        await db_session.execute(
            text(
                "SELECT library_track_id FROM plugin_radio.track_fingerprints"
            )
        )
    ).scalars().all()
    assert kept == [track]
    refs = dict(
        (
            await db_session.execute(
                text(
                    "SELECT id, library_track_id "
                    "FROM plugin_radio.radio_detections ORDER BY id"
                )
            )
        ).all()
    )
    assert refs[live_det] == track
    assert refs[dead_det] is None


async def test_live_bus_subscription_cleans_deleted_track(
    db_session,
) -> None:
    """The fast path: core.library_track_deleted → fingerprints dropped
    + detection refs nulled, without waiting for the sweep."""
    from domovoi.events import EVENTS
    from domovoi.plugins_runtime.loader import LOADER
    from domovoi.plugins_runtime.manifest import parse_manifest_dir
    from pathlib import Path

    plugin_dir = Path(__file__).resolve().parents[1]
    manifest = parse_manifest_dir(plugin_dir)
    await LOADER.load_plugin(
        slug="radio", install_dir=plugin_dir, manifest=manifest,
        foreign_corpus=[], update_registry_status=False,
    )
    try:
        sid = await _insert_station(db_session)
        det = await _insert_detection(db_session, sid, library_track_id=777)
        await db_session.execute(
            text(
                "INSERT INTO plugin_radio.track_fingerprints "
                "(library_track_id, hash, offset_ms) "
                "VALUES (777, '\\x03'::bytea, 0)"
            )
        )
        await db_session.commit()

        EVENTS.emit("core.library_track_deleted", {"track_id": 777})
        # Subscribers run as their own tasks — give them a beat.
        for _ in range(50):
            await asyncio.sleep(0.02)
            count = (
                await db_session.execute(
                    text(
                        "SELECT COUNT(*) FROM plugin_radio.track_fingerprints "
                        "WHERE library_track_id = 777"
                    )
                )
            ).scalar_one()
            if count == 0:
                break
        assert count == 0
        ref = (
            await db_session.execute(
                text(
                    "SELECT library_track_id "
                    "FROM plugin_radio.radio_detections WHERE id = :id"
                ),
                {"id": det},
            )
        ).scalar_one()
        assert ref is None
    finally:
        await LOADER.unload_plugin("radio")
