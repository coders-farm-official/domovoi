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

CPU-bound (~10-30 s per song). One track per tick keeps the wall-clock
cost bounded, holds a DB connection only briefly, and lets cancellation
land cleanly between tracks. Gated by ``RADIO_FINGERPRINTER_ENABLED``
(design §9.1): switching it off loses nothing except that
the sampler falls back to online identification for everything.

The un-fingerprinted-track SELECT reads ``public.library_tracks``
read-only (soft-ref scan; see the note in ``clients/fingerprint.py``).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from domovoi.sdk import PluginSDK, Worker

from domovoi_plugin_radio.clients.fingerprint import fingerprint_file

log = logging.getLogger(__name__)


class TrackFingerprinter(Worker):
    name = "track_fingerprinter"
    enabled_setting = "fingerprinter_enabled"
    interval_setting = "fingerprinter_interval_sec"
    stub_suppressed = True
    requires_online = False      # fully local

    def __init__(self, sdk: PluginSDK) -> None:
        self.sdk = sdk

    @property
    def _config(self) -> Any:
        return self.sdk.config

    async def tick(self) -> int:
        """Fingerprint one un-fingerprinted track. Returns rows inserted
        (0 when nothing was due or the file was unreadable)."""
        target = await self._claim_next_track()
        if target is None:
            return 0
        track_id, file_path = target
        rows = await fingerprint_file(file_path)
        if not rows:
            # Nothing produced — unreadable file, too short, or the
            # audio deps are missing. Insert one sentinel row (empty
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

    async def _claim_next_track(self) -> tuple[int, str] | None:
        """One library track with no fingerprint rows yet. NOT EXISTS
        keeps the query index-friendly at millions of fingerprint rows;
        ordered by id so re-runs are deterministic and progress is easy
        to verify in logs."""
        async with self.sdk.db.session_scope() as s:
            row = await s.execute(
                text(
                    """
                    SELECT id, file_path FROM public.library_tracks t
                    WHERE NOT EXISTS (
                        SELECT 1 FROM track_fingerprints f
                        WHERE f.library_track_id = t.id
                    )
                    ORDER BY id ASC
                    LIMIT 1
                    """
                )
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

    async def _insert_sentinel(self, track_id: int) -> None:
        async with self.sdk.db.session_scope() as s:
            await s.execute(
                text(
                    """
                    INSERT INTO track_fingerprints
                        (library_track_id, hash, offset_ms)
                    VALUES (:track_id, ''::bytea, 0)
                    ON CONFLICT (library_track_id, hash, offset_ms) DO NOTHING
                    """
                ),
                {"track_id": track_id},
            )
