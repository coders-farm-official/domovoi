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

    async def fake_fp(path, **_kw):
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

    async def fake_fp(path, **_kw):
        return []

    monkeypatch.setattr(tf_mod, "fingerprint_file", fake_fp)
    worker = TrackFingerprinter(radio_sdk)
    assert await worker.tick() == 0
    (row,) = await _fingerprint_rows(db_session)
    assert row.library_track_id == tid
    assert bytes(row.hash) == b""            # empty-BYTEA sentinel
    # The NOT EXISTS gate now skips it — nothing due next tick.
    assert await worker._claim_next_track() is None


async def test_a_track_that_keeps_running_out_of_time_is_marked_and_skipped(
    radio_sdk, db_session, monkeypatch
) -> None:
    """Incident 2026-10-14: a decode that never ends is killed by its time
    limit. Review 2026-10-15: one timeout says little about the file (a
    busy box hits the limit too), so the track is tried again later; only
    after TIMEOUTS_BEFORE_MARK of them is it recorded (empty hash,
    offset_ms -1) and never tried again. Other tracks go on meanwhile."""
    slow = await _insert_track(db_session, "C:/music/slow.mp3")
    radio_sdk.config.fingerprinter_timeout_sec = 7.0
    clock = {"t": 1000.0}
    calls: list[tuple[str, float]] = []

    async def fake_fp(path, *, timeout_sec):
        calls.append((path, timeout_sec))
        if path.endswith("slow.mp3"):
            raise tf_mod.FingerprintTimeout("took too long")
        return [FingerprintRow(hash_bytes=b"\x05" * 8, offset_ms=0)]

    monkeypatch.setattr(tf_mod, "fingerprint_file", fake_fp)
    worker = TrackFingerprinter(radio_sdk, clock=lambda: clock["t"])
    nexts = []
    for i in range(tf_mod.TIMEOUTS_BEFORE_MARK - 1):
        assert await worker.tick() == 0                     # timed out, not marked
        assert slow not in {r.library_track_id for r in await _fingerprint_rows(db_session)}
        nexts.append(await _insert_track(db_session, f"C:/music/next{i}.mp3"))
        assert await worker.tick() == 1                     # another track meanwhile
        clock["t"] += tf_mod.RETRY_MAX_SEC + 1              # its retry is due
    assert worker.status_note is None                        # tracks worked between
    assert await worker.tick() == 0                          # the last timeout: marked
    rows = [(r.library_track_id, bytes(r.hash), r.offset_ms) for r in await _fingerprint_rows(db_session)]
    assert (slow, b"", -1) in rows       # the timed-out mark, told apart from "no hashes" (0)
    assert tf_mod.SENTINEL_TIMED_OUT == -1 and tf_mod.SENTINEL_NO_HASHES == 0
    assert [c for c in calls if c[0].endswith("slow.mp3")] == [
        ("C:/music/slow.mp3", 7.0)] * tf_mod.TIMEOUTS_BEFORE_MARK
    clock["t"] += tf_mod.RETRY_MAX_SEC + 1
    assert await worker._claim_next_track() is None          # never tried again
    assert {r.library_track_id for r in await _fingerprint_rows(db_session)} == {slow, *nexts}


async def test_a_failed_attempt_is_tried_again_later_not_marked(
    radio_sdk, db_session, monkeypatch
) -> None:
    """Review 2026-10-15: one failed run (the child SIGTERMed by a normal
    `systemctl stop`, a crash, a child that couldn't start) used to mark
    the track for good. Now nothing is written about the file; the worker
    moves on and tries it again after RETRY_FIRST_SEC."""
    flaky = await _insert_track(db_session, "C:/music/flaky.mp3")
    fine = await _insert_track(db_session, "C:/music/fine.mp3")
    clock = {"t": 1000.0}
    calls: list[str] = []

    async def fake_fp(path, *, timeout_sec):
        calls.append(path)
        if path.endswith("flaky.mp3") and calls.count(path) == 1:
            raise tf_mod.FingerprintFailed("stopped by signal 15")
        return [FingerprintRow(hash_bytes=b"\x06" * 8, offset_ms=0)]

    monkeypatch.setattr(tf_mod, "fingerprint_file", fake_fp)
    worker = TrackFingerprinter(radio_sdk, clock=lambda: clock["t"])
    assert await worker.tick() == 0
    assert await _fingerprint_rows(db_session) == []        # nothing said about the file
    assert await worker.tick() == 1                           # the next track meanwhile
    assert await worker.tick() == 0                           # flaky waits its turn
    assert calls == ["C:/music/flaky.mp3", "C:/music/fine.mp3"]
    clock["t"] += tf_mod.RETRY_FIRST_SEC + 1
    assert await worker.tick() == 1                           # tried again, and it works
    assert {r.library_track_id for r in await _fingerprint_rows(db_session)} == {flaky, fine}
    assert worker.status_note is None


async def test_failures_in_a_row_pause_the_worker(radio_sdk, db_session, monkeypatch) -> None:
    """A wall every track hits (the audio libraries missing, say) pauses
    the worker after FAILURES_BEFORE_PAUSE tracks instead of walking the
    library; the pause is the worker's status note, and nothing is marked."""
    from domovoi.plugins_runtime.workers import _note_of

    for i in range(5):
        await _insert_track(db_session, f"C:/music/t{i}.mp3")
    clock = {"t": 0.0}
    calls: list[str] = []

    async def broken(path, *, timeout_sec):
        calls.append(path)
        raise tf_mod.FingerprintFailed("the audio libraries are missing")

    monkeypatch.setattr(tf_mod, "fingerprint_file", broken)
    worker = TrackFingerprinter(radio_sdk, clock=lambda: clock["t"])
    for _ in range(tf_mod.FAILURES_BEFORE_PAUSE):
        assert await worker.tick() == 0
    assert len(calls) == 3
    assert worker.status_note and "paused for 1 h" in worker.status_note
    assert "the audio libraries are missing" in worker.status_note
    assert _note_of(worker) == worker.status_note             # what the plugin's worker status shows
    assert await worker.tick() == 0 and len(calls) == 3       # paused: nothing tried
    assert await _fingerprint_rows(db_session) == []          # and nothing marked for good

    async def works(path, *, timeout_sec):
        calls.append(path)
        return [FingerprintRow(hash_bytes=b"\x07" * 8, offset_ms=0)]

    monkeypatch.setattr(tf_mod, "fingerprint_file", works)
    clock["t"] += tf_mod.PAUSE_FIRST_SEC + 1
    assert await worker.tick() == 1
    assert worker.status_note is None and _note_of(worker) is None


async def test_resumes_where_it_left_off(radio_sdk, db_session, monkeypatch) -> None:
    a = await _insert_track(db_session, "C:/music/a.mp3")
    b = await _insert_track(db_session, "C:/music/b.mp3")

    seen: list[str] = []

    async def fake_fp(path, **_kw):
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

    async def fake_fp(path, **_kw):
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
