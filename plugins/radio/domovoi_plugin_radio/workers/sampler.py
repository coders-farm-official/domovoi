"""Passive song-detection sampler for favorited radio stations.

A poll-shape :class:`~domovoi.sdk.Worker`: the core runner calls
``tick()`` every ``RADIO_SAMPLER_INNER_LOOP_SEC`` seconds; the tick
SELECTs favorited stations whose ``last_sampled_at +
sample_interval_sec`` has come due and runs the **two-tier identify
chain** on each:

1. ``fingerprint.match_sample`` — the local landmark-hash matcher. Wins
   on un-tagged library files title matching would miss; free; fastest.
2. ``shazam_stream.identify_wav`` — the online identification service.
   Catches anything in its catalog that we don't already own.

On a positive identification: dedup within the window, write a
``radio_detections`` row, fuzzy-check the library, and (config-gated)
enqueue an acquisition for novel songs — all via
:mod:`.detection_store`, shared with the ICY poller.

``asyncio.Semaphore(RADIO_SAMPLE_CONCURRENCY)`` caps concurrent ffmpeg
subprocesses; ``last_sampled_at`` is bumped on every attempt (including
ffmpeg failures) so a dead stream URL doesn't get hammered every tick.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, NamedTuple

from sqlalchemy import text

from domovoi.sdk import PluginSDK, Worker

from domovoi_plugin_radio.clients import fingerprint as fp
from domovoi_plugin_radio.clients import shazam_stream
from domovoi_plugin_radio.workers import detection_store

log = logging.getLogger(__name__)


class _StationRow(NamedTuple):
    """One favorited station the sampler is about to grab."""

    id: int
    name: str | None
    stream_url: str


class RadioSampler(Worker):
    name = "radio_sampler"
    enabled_setting = "sampler_enabled"
    interval_setting = "sampler_inner_loop_sec"
    stub_suppressed = True
    requires_online = False      # tier 1 (local fingerprints) works offline

    def __init__(self, sdk: PluginSDK) -> None:
        self.sdk = sdk
        # Cap concurrent ffmpeg grabs — enough for every station coming
        # due on the same tick without saturating the host.
        self._sem = asyncio.Semaphore(
            int(getattr(sdk.config, "sample_concurrency", 5))
        )

    @property
    def _config(self) -> Any:
        return self.sdk.config

    def _shazam(self):
        return shazam_stream.get_shazam_stream_client(
            use_stubs=bool(self.sdk.core_config.use_stubs),
            ffmpeg_timeout_sec=float(self._config.ffmpeg_timeout_sec),
        )

    async def tick(self) -> int:
        """Sample every due station. Returns how many stations were
        sampled this tick (regardless of identifications)."""
        due = await self._select_due_stations()
        if not due:
            return 0
        await asyncio.gather(
            *(self._sample_one(row) for row in due),
            return_exceptions=True,
        )
        return len(due)

    # ─── Per-station pipeline ──────────────────────────────────────────

    async def _select_due_stations(self) -> list[_StationRow]:
        """Favorited stations due for a sample, longest-stale first.

        ``icy_supported = TRUE`` stations are excluded: the much cheaper
        ICY poller serves them directly — paying an ffmpeg + online-
        identify roundtrip for a station with a working metadata channel
        is wasted work. NULL (never probed) and FALSE (confirmed no ICY)
        still get sampled.
        """
        async with self.sdk.db.session_scope() as s:
            rows = await s.execute(
                text(
                    """
                    SELECT id, name, stream_url
                    FROM radio_stations
                    WHERE favorited
                      AND stream_url IS NOT NULL
                      AND (icy_supported IS DISTINCT FROM TRUE)
                      AND (last_sampled_at IS NULL
                           OR last_sampled_at
                              + (sample_interval_sec * INTERVAL '1 second') < NOW())
                    ORDER BY last_sampled_at NULLS FIRST, id
                    LIMIT 50
                    """
                )
            )
            return [
                _StationRow(id=int(r[0]), name=r[1], stream_url=str(r[2]))
                for r in rows.all()
            ]

    async def _sample_one(self, row: _StationRow) -> None:
        async with self._sem:
            await self._do_sample(row)

    async def _do_sample(self, row: _StationRow) -> None:
        # 1. ffmpeg grab → tempfile (the shazam_stream helper owns the
        #    ffmpeg/timeout/cleanup details).
        wav_path = await shazam_stream.grab_to_tempfile(
            row.stream_url,
            duration_sec=15,
            timeout_sec=float(self._config.ffmpeg_timeout_sec),
        )
        if wav_path is None:
            # Bump anyway so a dead stream doesn't burn a slot per tick.
            await self._bump_last_sampled(row.id)
            return

        try:
            identity, source, library_track_id = await self._identify(wav_path)
            await self._bump_last_sampled(row.id)
            if identity is None:
                return                    # talk segment / ad / unknown
            await self._record_identification(
                station=row,
                identity=identity,
                source=source or "shazam",
                library_track_id=library_track_id,
            )
        finally:
            # Cleanup ALWAYS — even on an exception inside _identify.
            try:
                os.unlink(wav_path)
            except OSError:
                pass

    async def _identify(
        self, wav_path: str
    ) -> tuple[Any | None, str | None, int | None]:
        """The two-tier chain → (identity, source_tag, library_track_id).
        ``source_tag`` is ``'local'`` or ``'shazam'``; None/None when
        both tiers came up empty."""
        # Tier 1: local fingerprints. Fresh session per call — the
        # matcher queries track_fingerprints and we don't want to hold a
        # connection across the whole tick.
        try:
            async with self.sdk.db.session_scope() as s:
                local = await fp.match_sample(
                    s, wav_path,
                    min_confidence=int(self._config.fingerprinter_match_threshold),
                )
            if local is not None:
                return local.to_identity(), "local", local.library_track_id
        except Exception as e:
            log.debug("fingerprint match failed (continuing online): %s", e)

        # Tier 2: the online identification service.
        try:
            shazam = await self._shazam().identify_wav(wav_path)
            if shazam is not None:
                return shazam, "shazam", None
        except Exception as e:
            log.debug("online identify failed: %s", e)

        return None, None, None

    # ─── Detection + dedup + enqueue ───────────────────────────────────

    async def _record_identification(
        self,
        *,
        station: _StationRow,
        identity: Any,
        source: str,
        library_track_id: int | None = None,
    ) -> None:
        title = (identity.title or "").strip()
        artist = (identity.artist or "").strip() or None
        if not title:
            return

        async with self.sdk.db.session_scope() as s:
            if await detection_store.is_recent_dupe(
                s,
                station_id=station.id,
                artist=artist,
                title=title,
                window_sec=int(self._config.dedup_window_sec),
            ):
                return

            # A tier-1 hit IS a library track by definition.
            in_library = (
                source == "local"
                or await detection_store.is_in_library_fuzzy(
                    self.sdk, s, title, artist
                )
            )

            det_id = await detection_store.insert_detection(
                s,
                station_id=station.id,
                artist=artist,
                title=title,
                fingerprint_source=source,
                in_library=in_library,
                library_track_id=library_track_id,
            )

            # Nudge the dashboard — a detection row appeared. Commit-
            # coupled NOTIFY on the open session (design §4.12).
            await self.sdk.realtime.notify(s, "detections_changed", "new")

        if det_id is not None:
            # Broadcast the observation AFTER commit. Radio only reports
            # what it heard; whether any subscriber acts on it is that
            # subscriber's business. A fingerprint-identified sample is a
            # song by construction, so likely_song is always true here.
            self.sdk.events.emit(
                "detection_recorded",
                {
                    "detection_id": det_id,
                    "station_id": station.id,
                    "station_name": station.name,
                    "artist": artist,
                    "title": title,
                    "fingerprint_source": source,
                    "in_library": in_library,
                    "library_track_id": library_track_id,
                    "likely_song": True,
                },
            )

    async def _bump_last_sampled(self, station_id: int) -> None:
        async with self.sdk.db.session_scope() as s:
            await s.execute(
                text(
                    "UPDATE radio_stations SET last_sampled_at = NOW() "
                    "WHERE id = :id"
                ),
                {"id": station_id},
            )
