"""Podcasts web API.

Browser surface for podcast subscriptions + episodes. Playback itself is the
shared browser mini-player (player.jsx); this router provides the data
(subscriptions, episodes, chapters), Range audio serving for downloaded
episodes, discovery/subscribe (network), a manual poll trigger, and the
per-(device × person × episode) resume-position store.

Every served episode path is containment-checked inside ``podcasts_dir``
with the same realpath / ``relative_to`` guard music.py uses, so a stale or
hand-edited ``file_path`` row can't become an arbitrary-file read.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text

from domovoi.config import settings as core_settings
from domovoi import spoken_audio as sa
from web.backend.api.audio_serve import safe_download_name, serve_audio_range
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/podcasts", tags=["podcasts"])


# ─── Containment (music.py:349-367 pattern) ─────────────────────────────
def _podcasts_dir() -> Path:
    return Path(core_settings.podcasts_dir).expanduser().resolve(strict=False)


def _safe_episode_path(file_path: str) -> Path:
    base = _podcasts_dir()
    target = Path(file_path).expanduser().resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"refusing to serve {file_path!r}: not inside PODCASTS_DIR",
        )
    if not target.is_file():
        raise HTTPException(status_code=404, detail="episode file missing on disk")
    return target


# ─── Schemas ────────────────────────────────────────────────────────────
class SubscribeRequest(BaseModel):
    feed_url: Optional[str] = None
    query: Optional[str] = None      # discover-by-name (network) if no feed_url
    keep_n: int = 5


class PositionSave(BaseModel):
    device_id: str
    person_id: Optional[int] = None
    position_sec: int
    speed: Optional[float] = None


# ─── Subscriptions ──────────────────────────────────────────────────────
@router.get("/subscriptions")
async def list_subscriptions() -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT sub.id, sub.feed_url, sub.title, sub.author, sub.artwork,
                           sub.description, sub.keep_n, sub.last_polled_at, sub.added_at,
                           COALESCE(ep.n, 0) AS episode_count,
                           COALESCE(ep.dl, 0) AS downloaded_count
                      FROM podcast_subscriptions sub
                      LEFT JOIN (
                          SELECT subscription_id,
                                 COUNT(*) AS n,
                                 COUNT(*) FILTER (WHERE download_status = 'downloaded') AS dl
                            FROM podcast_episodes GROUP BY subscription_id
                      ) ep ON ep.subscription_id = sub.id
                     ORDER BY LOWER(COALESCE(sub.title, sub.feed_url))
                    """
                )
            )
        ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/subscriptions")
async def subscribe(req: SubscribeRequest) -> dict[str, Any]:
    """Subscribe by RSS URL, or by name via iTunes discovery (network)."""
    feed_url = (req.feed_url or "").strip()
    title = None
    if not feed_url and req.query:
        feed_url, title = await _itunes_lookup(req.query.strip())
        if not feed_url:
            raise HTTPException(status_code=404, detail=f"no podcast found for {req.query!r}")
    if not feed_url:
        raise HTTPException(status_code=400, detail="feed_url or query required")

    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    """
                    INSERT INTO podcast_subscriptions (feed_url, title, keep_n)
                    VALUES (:url, :title, :keep)
                    ON CONFLICT (feed_url) DO UPDATE SET keep_n = EXCLUDED.keep_n
                    RETURNING id, feed_url, title, keep_n
                    """
                ),
                {"url": feed_url, "title": title, "keep": max(1, req.keep_n)},
            )
        ).mappings().first()
        await s.execute(text("SELECT pg_notify('podcasts_changed', 'subscribe')"))
    return dict(row)


@router.delete("/subscriptions/{sub_id}")
async def unsubscribe(sub_id: int) -> dict[str, bool]:
    async with session_scope() as s:
        result = await s.execute(
            text("DELETE FROM podcast_subscriptions WHERE id = :id"), {"id": sub_id}
        )
        await s.execute(text("SELECT pg_notify('podcasts_changed', 'unsubscribe')"))
    if (result.rowcount or 0) == 0:
        raise HTTPException(status_code=404, detail=f"subscription {sub_id} not found")
    return {"deleted": True}


@router.get("/subscriptions/{sub_id}/episodes")
async def list_episodes(sub_id: int) -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT id, subscription_id, guid, title, description,
                           published_at, duration_sec, chapters, download_status,
                           downloaded_at, file_path, (file_path IS NOT NULL) AS has_file
                      FROM podcast_episodes
                     WHERE subscription_id = :sid
                     ORDER BY published_at DESC NULLS LAST, id DESC
                    """
                ),
                {"sid": sub_id},
            )
        ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        # Clients need the extension to name a save-to-device file, but the
        # server path itself stays private.
        fp = d.pop("file_path")
        d["file_ext"] = Path(fp).suffix.lower() if fp else None
        out.append(d)
    return out


# ─── Discovery (network) ────────────────────────────────────────────────
@router.get("/discover")
async def discover(q: str = Query(..., min_length=1)) -> list[dict[str, Any]]:
    """iTunes Search podcast discovery (keyless, rate-limited)."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                "https://itunes.apple.com/search",
                params={"term": q, "media": "podcast", "limit": 15},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("podcast discovery failed for %r: %s", q, e)
        raise HTTPException(status_code=502, detail="discovery unavailable (offline?)")
    out = []
    for it in data.get("results", []):
        if it.get("feedUrl"):
            out.append({
                "title": it.get("collectionName"),
                "author": it.get("artistName"),
                "artwork": it.get("artworkUrl600") or it.get("artworkUrl100"),
                "feed_url": it.get("feedUrl"),
            })
    return out


async def _itunes_lookup(name: str) -> tuple[Optional[str], Optional[str]]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                "https://itunes.apple.com/search",
                params={"term": name, "media": "podcast", "limit": 1},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("itunes lookup failed for %r: %s", name, e)
        return None, None
    results = data.get("results") or []
    if not results:
        return None, None
    return results[0].get("feedUrl"), results[0].get("collectionName")


# ─── Manual poll trigger ────────────────────────────────────────────────
@router.post("/poll")
async def poll_now() -> dict[str, int]:
    """Run one feed-poll + download + keep-N pass immediately (network).
    The web process runs the poller functions directly against the shared DB
    — the same code the background worker ticks."""
    from domovoi.workers.podcast_feed_poller import PodcastFeedPoller

    try:
        return await PodcastFeedPoller().tick()
    except Exception as e:
        log.warning("manual podcast poll failed: %s", e)
        raise HTTPException(status_code=502, detail=f"poll failed: {e}")


# ─── Episode audio (Range) ──────────────────────────────────────────────
@router.get("/episodes/{episode_id}/audio")
async def episode_audio(
    episode_id: int,
    request: Request,
    download: bool = Query(False, description="serve as attachment (save to device)"),
) -> StreamingResponse:
    async with session_scope() as s:
        row = (
            await s.execute(
                text("SELECT file_path, title FROM podcast_episodes WHERE id = :id"),
                {"id": episode_id},
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"episode {episode_id} not found")
    if not row[0]:
        raise HTTPException(status_code=409, detail="episode not downloaded yet")
    target = _safe_episode_path(row[0])
    name = None
    if download:
        name = safe_download_name(row[1] or target.stem, fallback="episode") + target.suffix.lower()
    return serve_audio_range(target, request, download_name=name)


# ─── Resume positions (per device × person × episode) ───────────────────
@router.get("/positions/{episode_id}")
async def get_position(
    episode_id: int,
    device_id: str = Query(...),
    person_id: Optional[int] = Query(None),
) -> dict[str, Any]:
    async with session_scope() as s:
        pos = await sa.get_position(
            s, item_type=sa.ITEM_PODCAST, item_id=episode_id,
            device_id=device_id, person_id=person_id,
        )
    return pos or {"position_sec": 0, "speed": 1.0}


@router.post("/positions/{episode_id}")
async def save_position(episode_id: int, body: PositionSave) -> dict[str, bool]:
    async with session_scope() as s:
        await sa.upsert_position(
            s, item_type=sa.ITEM_PODCAST, item_id=episode_id,
            device_id=body.device_id, person_id=body.person_id,
            position_sec=body.position_sec, speed=body.speed,
        )
        await s.execute(text("SELECT pg_notify('podcast_positions_changed', :p)"),
                        {"p": str(episode_id)})
    return {"saved": True}
