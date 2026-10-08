"""Podcasts web API.

Browser surface for podcast subscriptions + episodes. Playback itself is the
shared browser mini-player (player.jsx); this router provides the data
(subscriptions, episodes, chapters), Range audio serving for downloaded
episodes, discovery/subscribe (network), a manual poll trigger, and the
per-(device × person × episode) resume-position store.

Every served episode path is containment-checked inside ``podcasts_dir``
with the same realpath / ``relative_to`` guard music.py uses, so a stale or
hand-edited ``file_path`` row can't become an arbitrary-file read.

Subscribing and polling make the server fetch a URL the caller chose, so
both sit on the device tier (``X-Device-Token`` or an admin Bearer) and
every feed URL — typed in, or returned by discovery — goes through
``domovoi.net_safety``: http(s) only, and never an address inside the
house or on the box.

Artwork (fix B11): the ``artwork`` of a subscription row or a discovery
result is a SERVER path (or null), never the publisher's URL — the server
fetches and stores the image (``domovoi.podcast_artwork``) and serves it
from ``/api/podcasts/subscriptions/{id}/artwork`` and
``/api/podcasts/discover/artwork/{key}``. Those two are open GETs, like
episode audio, because an ``<img>`` can't send headers.

Internet access turned off (``INTERNET_ACCESS=never``): discovery,
subscribe-by-name, "poll now" and a thumbnail that would need fetching
answer the 409 internet-off refusal. Subscribing by feed URL still works:
storing a URL resolves nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

# Imported here, at the top, on purpose: the web process refuses new
# domovoi.* imports once it has started (plugin_host's import guard).
from domovoi import egress, net_safety, podcast_artwork
from domovoi.admin_auth import require_device
from domovoi.config import settings as core_settings
from domovoi import spoken_audio as sa
from web.backend.api.audio_serve import safe_download_name, serve_audio_range
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/podcasts", tags=["podcasts"])


# ─── Containment (music.py:349-367 pattern) ─────────────────────────────
def _podcasts_dir() -> Path:
    return Path(core_settings.podcasts_dir).expanduser().resolve(strict=False)


async def _checked_feed_url(url: str) -> str:
    """``url`` if the server may fetch it, else 400 saying why. Storing a
    subscription does not itself resolve the name (a feed whose DNS is
    down, or a household offline, must still be able to subscribe) — the
    poller re-checks with resolution before it fetches."""
    url = (url or "").strip()
    reason = await net_safety.acheck_outbound_url(url, require_resolution=False)
    if reason is not None:
        raise HTTPException(
            status_code=400, detail=f"refusing this feed URL — {reason}"
        )
    return url


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
# How many of a show's newest episodes the poller keeps downloaded (WEB-13).
# Bounded because every one of them is a download of up to
# MAX_ENCLOSURE_BYTES (512 MB) that enforce_keep_n never evicts: an
# unbounded keep_n from one device-tier subscribe was "download the whole
# back catalogue". 50 is far past what anybody listens through between
# polls; the voice subscribe path stores the default (5).
KEEP_N_MAX = 50


class SubscribeRequest(BaseModel):
    feed_url: Optional[str] = None
    query: Optional[str] = None      # discover-by-name (network) if no feed_url
    keep_n: int = Field(5, ge=1, le=KEEP_N_MAX)


class PositionSave(BaseModel):
    device_id: str
    person_id: Optional[int] = None
    position_sec: int
    speed: Optional[float] = None


ARTWORK_CACHE_CONTROL = "max-age=86400"


def _with_server_artwork(row: dict[str, Any]) -> dict[str, Any]:
    """A subscription row with ``artwork`` swapped from the stored source
    URL to the server path of the stored image (or None). A row whose
    image isn't stored yet gets one background fetch per process, so a
    subscription that never polled still shows its artwork."""
    out = dict(row)
    source = out.get("artwork")
    out["artwork"] = podcast_artwork.api_path(out["id"], source) if source else None
    if source and out["artwork"] is None:
        podcast_artwork.schedule_fill_once(out["id"], source)
    return out


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
    return [_with_server_artwork(dict(r)) for r in rows]


@router.post("/subscriptions", dependencies=[Depends(require_device)])
async def subscribe(req: SubscribeRequest) -> dict[str, Any]:
    """Subscribe by RSS URL, or by name via iTunes discovery (network).

    Device tier: ``X-Device-Token`` or an admin Bearer. The feed URL —
    typed in or returned by discovery — must be an http(s) URL outside
    the house's own address space.

    The show's artwork — the directory's, for a name lookup or a feed a
    discovery search returned in this process — is stored as the source
    and fetched in the background; the response's ``artwork`` is the
    server path once it is in (usually null here)."""
    feed_url = (req.feed_url or "").strip()
    title = None
    artwork = None
    if not feed_url and req.query:
        if egress.internet_turned_off():
            raise egress.http_exception("podcast search")
        feed_url, title, artwork = await _itunes_lookup(req.query.strip())
        if not feed_url:
            raise HTTPException(status_code=404, detail=f"no podcast found for {req.query!r}")
    if not feed_url:
        raise HTTPException(status_code=400, detail="feed_url or query required")
    feed_url = await _checked_feed_url(feed_url)
    if artwork is None:
        artwork = podcast_artwork.discovered_artwork_for(feed_url)

    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    """
                    INSERT INTO podcast_subscriptions (feed_url, title, keep_n, artwork)
                    VALUES (:url, :title, :keep, :artwork)
                    ON CONFLICT (feed_url) DO UPDATE
                       SET keep_n = EXCLUDED.keep_n,
                           artwork = COALESCE(podcast_subscriptions.artwork, EXCLUDED.artwork)
                    RETURNING id, feed_url, title, keep_n, artwork
                    """
                ),
                {"url": feed_url, "title": title, "keep": req.keep_n, "artwork": artwork},
            )
        ).mappings().first()
        await s.execute(text("SELECT pg_notify('podcasts_changed', 'subscribe')"))
    out = dict(row)
    if out.get("artwork"):
        podcast_artwork.schedule_ensure(out["id"], out["artwork"])
    out["artwork"] = podcast_artwork.api_path(out["id"], out["artwork"]) if out.get("artwork") else None
    return out


@router.delete(
    "/subscriptions/{sub_id}",
    # Device tier, like the subscribe it undoes.
    dependencies=[Depends(require_device)],
)
async def unsubscribe(sub_id: int) -> dict[str, bool]:
    async with session_scope() as s:
        result = await s.execute(
            text("DELETE FROM podcast_subscriptions WHERE id = :id"), {"id": sub_id}
        )
        await s.execute(text("SELECT pg_notify('podcasts_changed', 'unsubscribe')"))
    if (result.rowcount or 0) == 0:
        raise HTTPException(status_code=404, detail=f"subscription {sub_id} not found")
    podcast_artwork.forget(sub_id)
    return {"deleted": True}


@router.get("/subscriptions/{sub_id}/artwork")
async def subscription_artwork(sub_id: int) -> FileResponse:
    """The show's artwork as the server stored it (JPEG, PNG, WebP or
    GIF), or 404. Open, like episode audio: an ``<img>`` can't send
    headers, and it is only the show's cover."""
    path = podcast_artwork.cached_file(sub_id)
    content_type = podcast_artwork.cached_content_type(sub_id)
    if path is None or content_type is None:
        raise HTTPException(status_code=404, detail="no artwork stored for this subscription")
    return FileResponse(
        path,
        media_type=content_type,
        headers={"Cache-Control": ARTWORK_CACHE_CONTROL},
    )


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
@router.get(
    "/discover",
    # Device tier (WEB-18): every call makes the server fetch Apple's
    # search with the caller's term and mints artwork keys into the bounded
    # discovered-feed map — the same "the server goes and fetches" class as
    # subscribe and poll. The artwork route below stays open for the <img>.
    dependencies=[Depends(require_device)],
)
async def discover(q: str = Query(..., min_length=1)) -> list[dict[str, Any]]:
    """iTunes Search podcast discovery (keyless, rate-limited). Each
    result's ``artwork`` is a server path for a thumbnail the server
    fetches on first request (a key minted here), never iTunes' URL."""
    if egress.internet_turned_off():
        raise egress.http_exception("podcast search")
    try:
        async with egress.async_client(timeout=8.0) as client:
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
        feed_url = it.get("feedUrl")
        # A directory hit is only useful if we could fetch it: hide the
        # ones subscribe would refuse rather than offering a dead button.
        if feed_url and net_safety.is_safe_outbound_url(
            feed_url, require_resolution=False
        ):
            # The big image is what a subscribe stores; the small one is
            # the list's thumbnail.
            podcast_artwork.remember_discovered(
                feed_url, it.get("artworkUrl600") or it.get("artworkUrl100"),
            )
            out.append({
                "title": it.get("collectionName"),
                "author": it.get("artistName"),
                "artwork": podcast_artwork.discover_api_path(
                    it.get("artworkUrl100") or it.get("artworkUrl600")
                ),
                "feed_url": feed_url,
            })
    return out


@router.get("/discover/artwork/{key}")
async def discover_artwork(key: str) -> Response:
    """A discovery result's thumbnail, for a key a ``/discover`` answer in
    this process minted; 404 for any other key (the server never fetches a
    URL a client chose). With internet access turned off only an image
    already stored is served."""
    cached = podcast_artwork.discover_cached(key)
    if cached is None:
        if egress.internet_turned_off():
            raise egress.http_exception("podcast artwork")
        cached = await podcast_artwork.discover_artwork(key)
    if cached is None:
        raise HTTPException(status_code=404, detail="no such artwork")
    data, content_type = cached
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": ARTWORK_CACHE_CONTROL},
    )


async def _itunes_lookup(name: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(feedUrl, collectionName, artworkUrl600) of the top iTunes hit."""
    try:
        async with egress.async_client(timeout=8.0) as client:
            r = await client.get(
                "https://itunes.apple.com/search",
                params={"term": name, "media": "podcast", "limit": 1},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("itunes lookup failed for %r: %s", name, e)
        return None, None, None
    results = data.get("results") or []
    if not results:
        return None, None, None
    top = results[0]
    return (
        top.get("feedUrl"),
        top.get("collectionName"),
        top.get("artworkUrl600") or top.get("artworkUrl100"),
    )


# ─── Manual poll trigger ────────────────────────────────────────────────
@router.post("/poll", dependencies=[Depends(require_device)])
async def poll_now() -> dict[str, int]:
    """Run one feed-poll + download + keep-N pass immediately (network).
    The web process runs the poller functions directly against the shared DB
    — the same code the background worker ticks.

    Device tier: it makes the server fetch every subscribed feed now."""
    from domovoi.workers.podcast_feed_poller import PodcastFeedPoller

    if egress.internet_turned_off():
        raise egress.http_exception("podcast poll")
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


@router.post(
    "/positions/{episode_id}",
    # Device tier: a resume position is written by whichever paired
    # client is listening.
    dependencies=[Depends(require_device)],
)
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
