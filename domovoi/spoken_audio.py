"""Shared helpers for spoken audio (podcasts + audiobooks).

Pure DB + path helpers used by BOTH the core's SpokenAudioHandler
(voice / satellite playback) and the web backend's podcasts/audiobooks
routers (browser playback). Kept here — importable from both processes —
so the ``playback_positions`` upsert semantics and the MPD-URI mapping have
a single source of truth.

Two subtle contracts encoded here:

  * ``playback_positions`` uses TWO partial unique indexes (person / anon)
    because Postgres treats NULL person_id as distinct. ``upsert_position``
    picks the right ON CONFLICT target so an identified listener and an
    unidentified one on the same device never clobber each other AND an
    unidentified listener has exactly one slot per item.
  * MPD indexes one ``music_directory`` (/music). ``podcasts_dir`` and
    ``audiobooks_dir`` are mounted as nested subdirs (/music/podcasts,
    /music/audiobooks), so a host file path maps to an MPD URI by making
    it relative to its dir and prefixing the subdir name.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePath
from typing import Any

from sqlalchemy import text

from domovoi.config import settings

log = logging.getLogger(__name__)

ITEM_PODCAST = "podcast_episode"
ITEM_AUDIOBOOK = "audiobook"

# MPD container subdir for each item type (see mpd_provisioner nested mounts).
_MPD_SUBDIR = {ITEM_PODCAST: "podcasts", ITEM_AUDIOBOOK: "audiobooks"}
_HOST_DIR = {
    ITEM_PODCAST: lambda: settings.podcasts_dir,
    ITEM_AUDIOBOOK: lambda: settings.audiobooks_dir,
}


def mpd_uri_for(item_type: str, file_path: str) -> str | None:
    """Map a host ``file_path`` to the MPD URI the per-room daemon indexes.

    Returns ``<subdir>/<relative>`` (POSIX) or None if the path isn't inside
    the configured dir (containment — a stale/hand-edited row pointing wild
    must not become an arbitrary-file play primitive)."""
    if item_type not in _MPD_SUBDIR or not file_path:
        return None
    try:
        base = Path(_HOST_DIR[item_type]()).expanduser().resolve(strict=False)
        target = Path(file_path).expanduser().resolve(strict=False)
        rel = target.relative_to(base)
    except (ValueError, OSError):
        return None
    return f"{_MPD_SUBDIR[item_type]}/{PurePath(rel).as_posix()}"


async def upsert_position(
    session,
    *,
    item_type: str,
    item_id: int,
    device_id: str,
    person_id: int | None,
    position_sec: int,
    speed: float | None = None,
) -> None:
    """Insert or update the resume position for (item, device, person).

    Targets the person-scoped partial index when ``person_id`` is set and
    the anon one otherwise, so both paths are a real upsert. ``speed`` is
    left unchanged when None (a position save shouldn't wipe speed memory).
    """
    params = {
        "it": item_type,
        "iid": item_id,
        "dev": device_id,
        "pid": person_id,
        "pos": int(position_sec),
        "spd": speed,
    }
    if person_id is not None:
        conflict = "(item_type, item_id, device_id, person_id) WHERE person_id IS NOT NULL"
    else:
        conflict = "(item_type, item_id, device_id) WHERE person_id IS NULL"
    await session.execute(
        text(
            f"""
            INSERT INTO playback_positions
                (item_type, item_id, device_id, person_id, position_sec, speed)
            VALUES
                (:it, :iid, :dev, :pid, :pos, COALESCE(:spd, 1.0))
            ON CONFLICT {conflict} DO UPDATE SET
                position_sec = EXCLUDED.position_sec,
                speed = COALESCE(:spd, playback_positions.speed),
                updated_at = now()
            """
        ),
        params,
    )


async def set_speed(
    session,
    *,
    item_type: str,
    item_id: int,
    device_id: str,
    person_id: int | None,
    speed: float,
) -> None:
    """Persist per-item speed memory WITHOUT moving the position. Creates the
    row at position 0 if none exists yet."""
    if person_id is not None:
        conflict = "(item_type, item_id, device_id, person_id) WHERE person_id IS NOT NULL"
    else:
        conflict = "(item_type, item_id, device_id) WHERE person_id IS NULL"
    await session.execute(
        text(
            f"""
            INSERT INTO playback_positions
                (item_type, item_id, device_id, person_id, position_sec, speed)
            VALUES (:it, :iid, :dev, :pid, 0, :spd)
            ON CONFLICT {conflict} DO UPDATE SET
                speed = EXCLUDED.speed,
                updated_at = now()
            """
        ),
        {"it": item_type, "iid": item_id, "dev": device_id, "pid": person_id, "spd": speed},
    )


async def get_position(
    session,
    *,
    item_type: str,
    item_id: int,
    device_id: str,
    person_id: int | None,
) -> dict[str, Any] | None:
    """Return {position_sec, speed, updated_at} for (item, device, person),
    or None. NULL person_id matches the anon slot exactly (IS NOT DISTINCT
    FROM handles the NULL comparison)."""
    row = (
        await session.execute(
            text(
                """
                SELECT position_sec, speed, updated_at
                  FROM playback_positions
                 WHERE item_type = :it AND item_id = :iid AND device_id = :dev
                   AND person_id IS NOT DISTINCT FROM :pid
                """
            ),
            {"it": item_type, "iid": item_id, "dev": device_id, "pid": person_id},
        )
    ).first()
    if row is None:
        return None
    return {"position_sec": int(row[0]), "speed": float(row[1]), "updated_at": row[2]}


async def most_recent_in_progress(
    session,
    *,
    device_id: str,
    person_id: int | None,
    item_type: str | None = None,
) -> dict[str, Any] | None:
    """The item this device/person most recently had a position on (for
    "resume my book" / "resume my podcast"). Filters by item_type when
    given; joins to fetch the item's title + file_path so the caller can
    play it straight away."""
    type_clause = "AND p.item_type = :it" if item_type else ""
    row = (
        await session.execute(
            text(
                f"""
                SELECT p.item_type, p.item_id, p.position_sec, p.speed
                  FROM playback_positions p
                 WHERE p.device_id = :dev
                   AND p.person_id IS NOT DISTINCT FROM :pid
                   {type_clause}
                 ORDER BY p.updated_at DESC
                 LIMIT 1
                """
            ),
            {"dev": device_id, "pid": person_id, "it": item_type},
        )
    ).first()
    if row is None:
        return None
    return {
        "item_type": row[0],
        "item_id": int(row[1]),
        "position_sec": int(row[2]),
        "speed": float(row[3]),
    }


async def find_audiobook(session, query: str) -> dict[str, Any] | None:
    """Fuzzy-find one audiobook by title/author substring."""
    like = f"%{query.lower().strip()}%"
    row = (
        await session.execute(
            text(
                """
                SELECT id, title, author, file_path, duration_sec, chapters, is_folder
                  FROM audiobooks
                 WHERE LOWER(title) LIKE :q OR LOWER(COALESCE(author,'')) LIKE :q
                 ORDER BY (LOWER(title) = :exact) DESC, LOWER(title) ASC
                 LIMIT 1
                """
            ),
            {"q": like, "exact": query.lower().strip()},
        )
    ).mappings().first()
    return dict(row) if row else None


async def latest_episode_for_show(session, query: str) -> dict[str, Any] | None:
    """Newest DOWNLOADED episode of a subscribed show matching ``query``
    (by subscription title). Falls back to the newest episode regardless of
    download state so "play the latest X" reports something useful even when
    the download hasn't landed yet."""
    like = f"%{query.lower().strip()}%"
    row = (
        await session.execute(
            text(
                """
                SELECT e.id, e.title, e.file_path, e.duration_sec, e.chapters,
                       e.download_status, s.title AS show_title
                  FROM podcast_episodes e
                  JOIN podcast_subscriptions s ON s.id = e.subscription_id
                 WHERE LOWER(COALESCE(s.title,'')) LIKE :q
                 ORDER BY (e.download_status = 'downloaded') DESC,
                          e.published_at DESC NULLS LAST, e.id DESC
                 LIMIT 1
                """
            ),
            {"q": like},
        )
    ).mappings().first()
    return dict(row) if row else None
