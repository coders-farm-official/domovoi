"""Background worker that fingerprints every library track.

Poll-shape :class:`~domovoi.sdk.Worker` (the runner owns the loop).
Each tick:

1. SELECT one library track with no rows in ``track_fingerprints`` yet
   (resumable across restarts — NOT EXISTS naturally picks up where a
   previous run left off).
2. Compute landmark hashes via
   :func:`domovoi_plugin_radio.clients.fingerprint.fingerprint_file`.
3. Batch-INSERT with ON CONFLICT DO NOTHING — idempotent if a track is
   partially fingerprinted across crashes.

CPU-bound (~10-30 s per song), so the decode and the hashing run in a
child process under ``fingerprinter_timeout_sec`` (clients/fingerprint.py),
never on the core's event loop. Only a clean verdict about the FILE is
final at once: the file is there and gave no hashes (the decoder refused
it, too short, silent: a sentinel row with offset_ms 0); it isn't tried
again (delete its row to retry). Any other failure (the file isn't there
to read, as with a media disk not mounted; the child was stopped, as every
``systemctl stop`` does, or crashed, couldn't start; the audio libraries
are missing; or it ran past the time limit, which a busy box can cause
too) is tried again later, after 10 minutes and then twice as long each
time (at most a day). A track that runs past the time limit
:data:`TIMEOUTS_BEFORE_MARK` times is marked then (offset_ms -1), so a
file too long to decode doesn't cost the limit every retry, forever. After
:data:`FAILURES_BEFORE_PAUSE` failures in a row, timeouts included, the
worker pauses (an hour, then twice as long each time, at most a day)
instead of walking the whole library into the same wall; the pause is
logged and is the worker's ``status_note`` (shown on the dashboard's
Plugins page). One track per tick keeps
the wall-clock cost bounded, holds a DB connection only briefly, and lets
cancellation land cleanly between tracks (a cancelled tick kills its
child). Gated by ``RADIO_FINGERPRINTER_ENABLED``
(design §9.1): switching it off loses nothing except that
the sampler falls back to online identification for everything.

The un-fingerprinted-track SELECT reads ``public.library_tracks``
read-only (soft-ref scan; see the note in ``clients/fingerprint.py``).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import text

from domovoi.sdk import PluginSDK, Worker

from domovoi_plugin_radio.clients.fingerprint import (
    FINGERPRINT_TIMEOUT_SEC,
    FingerprintFailed,
    FingerprintTimeout,
    fingerprint_file,
)

log = logging.getLogger(__name__)

# offset_ms of the sentinel row (empty hash) that marks a track as done
# without hashes: nothing usable came out of it, or it ran out of time.
SENTINEL_NO_HASHES = 0
SENTINEL_TIMED_OUT = -1

# A failure that says nothing about the file: when the track is tried
# again, and how many of those in a row pause the worker, for how long.
RETRY_FIRST_SEC = 600.0
RETRY_MAX_SEC = 86400.0
FAILURES_BEFORE_PAUSE = 3
TIMEOUTS_BEFORE_MARK = 3
PAUSE_FIRST_SEC = 3600.0
PAUSE_MAX_SEC = 86400.0


def _doubling(first: float, n: int, cap: float) -> float:
    return min(cap, first * (2 ** max(0, n - 1)))


class TrackFingerprinter(Worker):
    name = "track_fingerprinter"
    enabled_setting = "fingerprinter_enabled"
    interval_setting = "fingerprinter_interval_sec"
    stub_suppressed = True
    requires_online = False      # fully local

    def __init__(self, sdk: PluginSDK, *, clock=time.monotonic) -> None:
        self.sdk = sdk
        self._clock = clock
        # track id -> (failures so far, not before this monotonic time)
        self._retry: dict[int, tuple[int, float]] = {}
        self._timeouts: dict[int, int] = {}   # track id -> times it ran out of time
        self._streak = 0          # failures in a row, any track
        self._pauses = 0          # pauses in a row
        self._paused_until: float | None = None
        # Shown with the worker's state (WorkerRunner.status): why it is
        # paused, or None.
        self.status_note: str | None = None

    @property
    def _config(self) -> Any:
        return self.sdk.config

    async def tick(self) -> int:
        """Fingerprint one un-fingerprinted track. Returns rows inserted
        (0 when nothing was due or the file was unreadable)."""
        now = self._clock()
        if self._paused_until is not None:
            if now < self._paused_until:
                return 0
            self._paused_until = None
            log.info("fingerprinter: the pause is over; trying again")
        skip = [tid for tid, (_n, not_before) in self._retry.items() if not_before > now]
        target = await self._claim_next_track(skip)
        if target is None:
            return 0
        track_id, file_path = target
        timeout = float(
            getattr(self._config, "fingerprinter_timeout_sec", FINGERPRINT_TIMEOUT_SEC)
            or FINGERPRINT_TIMEOUT_SEC
        )
        try:
            rows = await fingerprint_file(file_path, timeout_sec=timeout)
        except FingerprintTimeout:
            n = self._timeouts.get(track_id, 0) + 1
            why = f"it took longer than {timeout:.0f} s to decode ({n} of {TIMEOUTS_BEFORE_MARK})"
            if n < TIMEOUTS_BEFORE_MARK:
                self._timeouts[track_id] = n
                self._failed(track_id, file_path, why)
                return 0
            self._timeouts.pop(track_id, None)
            self._retry.pop(track_id, None)
            await self._insert_sentinel(track_id, SENTINEL_TIMED_OUT)
            log.warning(
                "fingerprinter: track #%d at %s took longer than %.0f s to decode "
                "%d times and was stopped; it won't be tried again (delete its row "
                "in plugin_radio.track_fingerprints to retry)",
                track_id, file_path, timeout, n,
            )
            self._count_failure(why)
            return 0
        except FingerprintFailed as e:
            self._failed(track_id, file_path, str(e))
            return 0
        self._worked(track_id)
        if not rows:
            # Nothing produced from a file that is there: the decoder
            # refused it, or it is too short. Insert one sentinel row (empty
            # BYTEA — a real hash is always 8 bytes) so the NOT EXISTS
            # gate skips this track next tick instead of retrying
            # forever. The matcher's `= ANY(...)` can never include
            # empty bytes (samples always produce real hashes).
            await self._insert_sentinel(track_id)
            log.info(
                "fingerprinter: no hashes for track #%d at %s; marked",
                track_id, file_path,
            )
            return 0

        inserted = await self._insert_rows(track_id, rows)
        log.info(
            "fingerprinter: %d hashes for track #%d (%s)",
            inserted, track_id, file_path,
        )
        return inserted

    def _worked(self, track_id: int) -> None:
        """The child ran and gave a verdict: the worker works."""
        self._retry.pop(track_id, None)
        self._timeouts.pop(track_id, None)
        self._streak = 0
        self._pauses = 0
        self.status_note = None

    def _failed(self, track_id: int, file_path: str, why: str) -> None:
        now = self._clock()
        n = self._retry.get(track_id, (0, 0.0))[0] + 1
        wait = _doubling(RETRY_FIRST_SEC, n, RETRY_MAX_SEC)
        self._retry[track_id] = (n, now + wait)
        log.warning(
            "fingerprinter: track #%d at %s failed (%s); it will be tried again in %.0f min",
            track_id, file_path, why, wait / 60,
        )
        self._count_failure(why)

    def _count_failure(self, why: str) -> None:
        """One more failure in a row; enough of them pause the worker."""
        now = self._clock()
        self._streak += 1
        if self._streak >= FAILURES_BEFORE_PAUSE:
            self._pauses += 1
            pause = _doubling(PAUSE_FIRST_SEC, self._pauses, PAUSE_MAX_SEC)
            self._paused_until = now + pause
            self._streak = 0
            self.status_note = (
                f"paused for {pause / 3600:.0f} h: {FAILURES_BEFORE_PAUSE} tracks in a row "
                f"failed, the last because {why}"
            )
            log.warning("fingerprinter: %s", self.status_note)

    async def _claim_next_track(self, skip: list[int] | None = None) -> tuple[int, str] | None:
        """One library track with no fingerprint rows yet, other than the
        ones in ``skip`` (waiting to be tried again). NOT EXISTS keeps the
        query index-friendly at millions of fingerprint rows; ordered by id
        so re-runs are deterministic and progress is easy to verify in logs."""
        async with self.sdk.db.session_scope() as s:
            row = await s.execute(
                text(
                    """
                    SELECT id, file_path FROM public.library_tracks t
                    WHERE NOT EXISTS (
                        SELECT 1 FROM track_fingerprints f
                        WHERE f.library_track_id = t.id
                    )
                    AND NOT (t.id = ANY(CAST(:skip AS bigint[])))
                    ORDER BY id ASC
                    LIMIT 1
                    """
                ),
                {"skip": list(skip or [])},
            )
            result = row.first()
        if result is None:
            return None
        return int(result[0]), str(result[1])

    async def _insert_rows(self, track_id: int, rows: list) -> int:
        if not rows:
            return 0
        params = [
            {
                "track_id": track_id,
                "hash": r.hash_bytes,
                "offset_ms": r.offset_ms,
            }
            for r in rows
        ]
        async with self.sdk.db.session_scope() as s:
            # executemany via SQLAlchemy: list of param dicts against a
            # single text() statement.
            await s.execute(
                text(
                    """
                    INSERT INTO track_fingerprints
                        (library_track_id, hash, offset_ms)
                    VALUES (:track_id, :hash, :offset_ms)
                    ON CONFLICT (library_track_id, hash, offset_ms) DO NOTHING
                    """
                ),
                params,
            )
        return len(params)

    async def _insert_sentinel(
        self, track_id: int, offset_ms: int = SENTINEL_NO_HASHES
    ) -> None:
        async with self.sdk.db.session_scope() as s:
            await s.execute(
                text(
                    """
                    INSERT INTO track_fingerprints
                        (library_track_id, hash, offset_ms)
                    VALUES (:track_id, ''::bytea, :offset_ms)
                    ON CONFLICT (library_track_id, hash, offset_ms) DO NOTHING
                    """
                ),
                {"track_id": track_id, "offset_ms": offset_ms},
            )
