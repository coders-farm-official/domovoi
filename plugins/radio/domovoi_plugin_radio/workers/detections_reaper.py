"""Detections retention reaper + soft-ref reconciliation sweep.

Two jobs in one cheap tick (design §9.2/§9.3 item 6 — locked 19, and
§4.9's normative bus/sweep pairing):

1. **Retention**: delete ``radio_detections`` older than
   ``RADIO_DETECTIONS_RETENTION_DAYS`` (0 = keep forever). Without
   retention the table grows without bound — one row per favorited
   station × poll interval × forever.

2. **Reconciliation** (the "truth" half of the event-bus coupling —
   the bus is fire-and-forget, so a crash between commit and delivery
   may drop events; this sweep bounds the staleness to one interval):

   * delete ``track_fingerprints`` whose ``library_track_id`` no longer
     resolves against ``public.library_tracks`` (the deleted-track
     cleanup the ``core.library_track_deleted`` subscription normally
     does immediately);
   * null detection ``library_track_id`` soft refs that no longer
     resolve.

The read-only subqueries against ``public.library_tracks`` are soft-ref
resolution (see the note in ``clients/fingerprint.py``).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from domovoi.sdk import PluginSDK, Worker

log = logging.getLogger(__name__)


class RadioDetectionsReaper(Worker):
    name = "radio_detections_reaper"
    enabled_setting = None       # the sweep half must always run
    interval_setting = "detections_reaper_interval_sec"
    stub_suppressed = True
    requires_online = False

    def __init__(self, sdk: PluginSDK) -> None:
        self.sdk = sdk

    @property
    def _config(self) -> Any:
        return self.sdk.config

    async def tick(self) -> dict[str, int]:
        stats = {
            "detections_reaped": 0,
            "fingerprints_orphaned": 0,
            "track_refs_nulled": 0,
        }
        stats["detections_reaped"] = await self._reap_old_detections()
        orphans, nulled = await self._sweep_soft_refs()
        stats["fingerprints_orphaned"] = orphans
        stats["track_refs_nulled"] = nulled
        if any(stats.values()):
            log.info("radio reaper: %s", stats)
        return stats

    # ─── 1. retention ──────────────────────────────────────────────────

    async def _reap_old_detections(self) -> int:
        days = int(self._config.detections_retention_days)
        if days <= 0:
            return 0
        async with self.sdk.db.session_scope() as s:
            result = await s.execute(
                text(
                    """
                    DELETE FROM radio_detections
                    WHERE detected_at < NOW() - (:days * INTERVAL '1 day')
                    """
                ),
                {"days": days},
            )
            reaped = result.rowcount or 0
            if reaped:
                await self.sdk.realtime.notify(s, "detections_changed", "reaped")
            return reaped

    # ─── 2a. dead soft refs ────────────────────────────────────────────

    async def _sweep_soft_refs(self) -> tuple[int, int]:
        async with self.sdk.db.session_scope() as s:
            orphans = (
                await s.execute(
                    text(
                        """
                        DELETE FROM track_fingerprints f
                        WHERE NOT EXISTS (
                            SELECT 1 FROM public.library_tracks t
                            WHERE t.id = f.library_track_id
                        )
                        """
                    )
                )
            ).rowcount or 0
            nulled = (
                await s.execute(
                    text(
                        """
                        UPDATE radio_detections d
                        SET library_track_id = NULL
                        WHERE library_track_id IS NOT NULL
                          AND NOT EXISTS (
                            SELECT 1 FROM public.library_tracks t
                            WHERE t.id = d.library_track_id
                          )
                        """
                    )
                )
            ).rowcount or 0
            return orphans, nulled
