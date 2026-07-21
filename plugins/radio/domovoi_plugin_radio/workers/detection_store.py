"""Shared detection bookkeeping for the two passive detectors.

Both the audio sampler and the ICY poller end their pipelines the same
way: dedup against recent detections, write a ``radio_detections`` row,
and check the library. That tail lives here so the two workers can't
drift.

Radio is strictly an OBSERVER (design §9.3): it records what a station
played and whether the library already has it, then broadcasts the
observation as a ``plugin.radio.detection_recorded`` bus event (emitted
by the workers post-commit). It never initiates downloads and holds no
acquisition state — what (if anything) a subscriber does with a
detection is entirely that subscriber's business.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from domovoi_plugin_radio import SCHEMA

log = logging.getLogger(__name__)


async def is_recent_dupe(
    session: AsyncSession,
    *,
    station_id: int,
    artist: str | None,
    title: str,
    window_sec: int,
) -> bool:
    """Same (station, artist, title) within the dedup window already?
    Catches "song plays for 4 minutes at a 60 s cadence" producing four
    duplicate rows.

    ``:win * INTERVAL '1 second'`` (multiplicative) rather than the
    string-concat form — asyncpg is strict about ``int || text``.
    """
    result = await session.execute(
        text(
            f"""
            SELECT 1 FROM {SCHEMA}.radio_detections
            WHERE station_id = :sid
              AND COALESCE(artist, '') = COALESCE(:artist, '')
              AND title = :title
              AND detected_at > NOW() - (:win * INTERVAL '1 second')
            LIMIT 1
            """
        ),
        {"sid": station_id, "artist": artist, "title": title, "win": window_sec},
    )
    return result.first() is not None


async def insert_detection(
    session: AsyncSession,
    *,
    station_id: int,
    artist: str | None,
    title: str,
    fingerprint_source: str,     # app-validated: 'local' | 'shazam' | 'icy'
    in_library: bool,
    library_track_id: int | None = None,
) -> int | None:
    result = await session.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.radio_detections
                (station_id, artist, title, fingerprint_source, in_library,
                 library_track_id)
            VALUES (:sid, :artist, :title, :source, :in_lib, :lt_id)
            RETURNING id
            """
        ),
        {
            "sid": station_id,
            "artist": artist,
            "title": title,
            "source": fingerprint_source,
            "in_lib": in_library,
            "lt_id": library_track_id,
        },
    )
    row = result.first()
    return int(row[0]) if row is not None else None


async def is_in_library_fuzzy(
    sdk: Any, session: AsyncSession, title: str, artist: str | None
) -> bool:
    """pg_trgm "do we already own this?" via the SDK. The artist gate is
    what distinguishes Radiohead's "Creep" from TLC's "Creep" without
    breaking looser title-only matches on rows lacking artist tags."""
    if not title:
        return False
    if sdk.library is None:      # stubbed unit contexts
        return False
    try:
        match = await sdk.library.find_fuzzy_match(session, title, artist)
    except Exception as e:
        log.debug("library fuzzy match failed: %s", e)
        return False
    return match is not None
