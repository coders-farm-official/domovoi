"""TrackFingerprinter worker — claim/insert/sentinel/resume mechanics
(the hashing itself is covered by test_fingerprint.py; here it's
monkeypatched so the worker logic tests run without the audio stack)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db

from domovoi_plugin_radio.clients.fingerprint import FingerprintRow
from domovoi_plugin_radio.workers import track_fingerprinter as tf_mod
from domovoi_plugin_radio.workers.track_fingerprinter import TrackFingerprinter

pytestmark = requires_db


async def _insert_track(session, path: str) -> int:
    row = await session.execute(
        text(
            "INSERT INTO library_tracks (file_path, title, source, added_via) "
            "VALUES (:p, 'T', 'manual', 'manual') RETURNING id"
        ),
        {"p": path},
    )
    await session.commit()
    return int(row.scalar_one())


async def _fingerprint_rows(session):
    rows = await session.execute(
        text(
            "SELECT library_track_id, hash, offset_ms "
            "FROM plugin_radio.track_fingerprints ORDER BY library_track_id"
        )
    )
    return rows.all()


async def test_tick_fingerprints_one_track(radio_sdk, db_session, monkeypatch) -> None:
    tid = await _insert_track(db_session, "C:/music/a.mp3")

    async def fake_fp(path):
        assert path == "C:/music/a.mp3"
        return [
            FingerprintRow(hash_bytes=b"\x01" * 8, offset_ms=0),
            FingerprintRow(hash_bytes=b"\x02" * 8, offset_ms=1000),
        ]

    monkeypatch.setattr(tf_mod, "fingerprint_file", fake_fp)
    inserted = await TrackFingerprinter(radio_sdk).tick()
    assert inserted == 2
    rows = await _fingerprint_rows(db_session)
    assert [r.library_track_id for r in rows] == [tid, tid]


async def test_sentinel_marks_unfingerprintable_track(
    radio_sdk, db_session, monkeypatch
) -> None:
    tid = await _insert_track(db_session, "C:/music/broken.mp3")

    async def fake_fp(path):
        return []

    monkeypatch.setattr(tf_mod, "fingerprint_file", fake_fp)
    worker = TrackFingerprinter(radio_sdk)
    assert await worker.tick() == 0
    (row,) = await _fingerprint_rows(db_session)
    assert row.library_track_id == tid
    assert bytes(row.hash) == b""            # empty-BYTEA sentinel
    # The NOT EXISTS gate now skips it — nothing due next tick.
    assert await worker._claim_next_track() is None


async def test_resumes_where_it_left_off(radio_sdk, db_session, monkeypatch) -> None:
    a = await _insert_track(db_session, "C:/music/a.mp3")
    b = await _insert_track(db_session, "C:/music/b.mp3")

    seen: list[str] = []

    async def fake_fp(path):
        seen.append(path)
        return [FingerprintRow(hash_bytes=b"\x03" * 8, offset_ms=0)]

    monkeypatch.setattr(tf_mod, "fingerprint_file", fake_fp)
    worker = TrackFingerprinter(radio_sdk)
    await worker.tick()     # track a
    await worker.tick()     # track b
    assert seen == ["C:/music/a.mp3", "C:/music/b.mp3"]
    rows = await _fingerprint_rows(db_session)
    assert {r.library_track_id for r in rows} == {a, b}
    assert await worker._claim_next_track() is None


async def test_insert_is_idempotent_across_crashes(
    radio_sdk, db_session, monkeypatch
) -> None:
    await _insert_track(db_session, "C:/music/a.mp3")

    async def fake_fp(path):
        return [FingerprintRow(hash_bytes=b"\x04" * 8, offset_ms=0)]

    monkeypatch.setattr(tf_mod, "fingerprint_file", fake_fp)
    worker = TrackFingerprinter(radio_sdk)
    await worker.tick()
    # Simulate a partially-fingerprinted crash rerun: force the same
    # rows through _insert_rows again — ON CONFLICT keeps one copy.
    tid_rows = await _fingerprint_rows(db_session)
    await worker._insert_rows(
        tid_rows[0].library_track_id,
        [FingerprintRow(hash_bytes=b"\x04" * 8, offset_ms=0)],
    )
    assert len(await _fingerprint_rows(db_session)) == 1


async def test_worker_declaration_matches_manifest(radio_sdk) -> None:
    w = TrackFingerprinter(radio_sdk)
    assert w.name == "track_fingerprinter"
    assert w.enabled_setting == "fingerprinter_enabled"
    assert w.interval_setting == "fingerprinter_interval_sec"
