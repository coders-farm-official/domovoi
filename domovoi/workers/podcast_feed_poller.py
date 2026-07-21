"""Podcast feed poller + downloader.

One worker owns the whole podcast ingest pipeline, mirroring how the
acquisition fulfillers own music downloads:

  1. POLL every subscription's RSS feed (``feedparser``), upserting each
     feed item into ``podcast_episodes`` (dedup by ``(subscription_id,
     guid)``). Parses PSC / ID3 chapters where the feed advertises them.
  2. ENQUEUE the newest-N (per subscription ``keep_n``) that aren't
     downloaded yet — flip their ``download_status`` to 'pending'.
  3. DOWNLOAD pending episodes straight from the enclosure URL into
     ``podcasts_dir/<sub_slug>/`` (podcast enclosures are direct audio
     files — no downloader tooling needed), then re-read chapters from the file's
     ID3 tags.
  4. EVICT: enforce keep-N per subscription — older downloaded episodes
     beyond the window have their file deleted and row flipped to
     'skipped' (LRU by published_at).

``requires network`` — the whole worker is gated OFF by default
(``podcast_feed_poller_enabled``) and skipped under USE_STUBS, exactly like
the radio sampler / wake-word trainer. Downloaded episodes still play fully
offline (that's the SpokenAudioHandler's ``fallback_offline`` path); only
polling/downloading needs the network.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text

from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text_: str) -> str:
    s = _SLUG_RE.sub("-", (text_ or "").lower()).strip("-")
    return s or "podcast"


def _parse_chapters_from_entry(entry: Any) -> list[dict[str, Any]] | None:
    """Pull PSC (Podlove Simple Chapters) markers out of a feedparser entry.

    Feeds that carry ``<psc:chapters>`` expose them via feedparser as
    ``entry.psc_chapters`` (namespace-dependent). Best-effort — most feeds
    have none, and that's fine (the handler degrades to time-skip).
    """
    psc = getattr(entry, "psc_chapters", None) or entry.get("psc_chapters") if hasattr(entry, "get") else None
    if not psc:
        return None
    raw = psc.get("chapters") if isinstance(psc, dict) else getattr(psc, "chapters", None)
    if not raw:
        return None
    out: list[dict[str, Any]] = []
    for ch in raw:
        start = ch.get("start") if isinstance(ch, dict) else getattr(ch, "start", None)
        title = ch.get("title") if isinstance(ch, dict) else getattr(ch, "title", None)
        sec = _hms_to_sec(start)
        if sec is not None:
            out.append({"title": str(title or ""), "start_sec": sec})
    return out or None


def _hms_to_sec(value: Any) -> int | None:
    """'01:02:03.5' / '02:03' / '123' → integer seconds. None if unparseable."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        parts = s.split(":")
        parts = [float(p) for p in parts]
    except ValueError:
        return None
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return int(sec)


def _entry_duration_sec(entry: Any) -> int | None:
    dur = getattr(entry, "itunes_duration", None)
    if dur is None and hasattr(entry, "get"):
        dur = entry.get("itunes_duration")
    return _hms_to_sec(dur)


def _entry_enclosure(entry: Any) -> str | None:
    for enc in getattr(entry, "enclosures", None) or []:
        href = enc.get("href") if isinstance(enc, dict) else getattr(enc, "href", None)
        if href:
            return href
    links = getattr(entry, "links", None) or []
    for link in links:
        if (link.get("rel") if isinstance(link, dict) else None) == "enclosure":
            return link.get("href")
    return None


async def poll_subscription(session, sub: dict[str, Any]) -> int:
    """Fetch + parse one subscription's feed, upsert episodes. Returns the
    number of NEW episodes recorded. Network + parse happen off the event
    loop. Feed metadata (title/author/artwork) is refreshed on the sub."""
    import feedparser  # local import — optional dep

    feed_url = sub["feed_url"]
    parsed = await asyncio.to_thread(feedparser.parse, feed_url)
    feed = getattr(parsed, "feed", {}) or {}

    # Refresh subscription-level metadata.
    await session.execute(
        text(
            """
            UPDATE podcast_subscriptions
               SET title = COALESCE(:title, title),
                   author = COALESCE(:author, author),
                   artwork = COALESCE(:artwork, artwork),
                   description = COALESCE(:description, description),
                   last_polled_at = now()
             WHERE id = :id
            """
        ),
        {
            "id": sub["id"],
            "title": feed.get("title"),
            "author": feed.get("author"),
            "artwork": (feed.get("image") or {}).get("href"),
            "description": feed.get("subtitle") or feed.get("description"),
        },
    )

    new_count = 0
    for entry in getattr(parsed, "entries", []) or []:
        guid = getattr(entry, "id", None) or getattr(entry, "link", None) or getattr(entry, "title", None)
        if not guid:
            continue
        published = None
        pp = getattr(entry, "published_parsed", None)
        if pp is not None:
            import calendar
            from datetime import datetime, timezone
            published = datetime.fromtimestamp(calendar.timegm(pp), tz=timezone.utc)
        chapters = _parse_chapters_from_entry(entry)
        result = await session.execute(
            text(
                """
                INSERT INTO podcast_episodes
                    (subscription_id, guid, title, description, enclosure_url,
                     published_at, duration_sec, chapters)
                VALUES
                    (:sid, :guid, :title, :descr, :enc, :pub, :dur,
                     CAST(:chapters AS JSONB))
                ON CONFLICT (subscription_id, guid) DO NOTHING
                """
            ),
            {
                "sid": sub["id"],
                "guid": str(guid),
                "title": getattr(entry, "title", None),
                "descr": getattr(entry, "summary", None),
                "enc": _entry_enclosure(entry),
                "pub": published,
                "dur": _entry_duration_sec(entry),
                "chapters": _json_or_none(chapters),
            },
        )
        if (result.rowcount or 0) > 0:
            new_count += 1
    return new_count


def _json_or_none(value: Any) -> str | None:
    import json
    return json.dumps(value) if value else None


async def enqueue_newest(session, subscription_id: int, keep_n: int) -> int:
    """Mark the newest-N not-yet-downloaded episodes as 'pending' so the
    downloader picks them up. Returns how many were newly enqueued.

    Episodes already 'downloaded'/'downloading'/'failed' are left alone;
    only 'skipped'/'pending' rows inside the newest-N window are (re)armed.
    """
    result = await session.execute(
        text(
            """
            UPDATE podcast_episodes
               SET download_status = 'pending', error = NULL
             WHERE id IN (
                 SELECT id FROM podcast_episodes
                  WHERE subscription_id = :sid
                  ORDER BY published_at DESC NULLS LAST, id DESC
                  LIMIT :keep
             )
             AND download_status IN ('skipped')
            """
        ),
        {"sid": subscription_id, "keep": keep_n},
    )
    return result.rowcount or 0


async def enforce_keep_n(session, subscription_id: int, keep_n: int) -> int:
    """LRU-evict downloaded episodes beyond the newest-N window.

    For episodes ordered newest-first, any 'downloaded' row ranked at or
    beyond ``keep_n`` has its file deleted and status flipped to 'skipped'
    (file_path cleared). Returns the number evicted. This is the keep-N
    contract the feature promises ("auto-download newest, keep N")."""
    rows = (
        await session.execute(
            text(
                """
                SELECT id, file_path, download_status
                  FROM podcast_episodes
                 WHERE subscription_id = :sid
                 ORDER BY published_at DESC NULLS LAST, id DESC
                """
            ),
            {"sid": subscription_id},
        )
    ).all()

    evicted = 0
    for rank, (ep_id, file_path, status) in enumerate(rows):
        if rank < keep_n:
            continue
        if status != "downloaded":
            continue
        # Delete the file (best-effort) then flip the row.
        if file_path:
            try:
                p = Path(file_path)
                if p.exists():
                    p.unlink()
            except OSError as e:
                log.warning("keep-N: couldn't delete %s: %s", file_path, e)
        await session.execute(
            text(
                "UPDATE podcast_episodes "
                "SET download_status = 'skipped', file_path = NULL "
                "WHERE id = :id"
            ),
            {"id": ep_id},
        )
        evicted += 1
    return evicted


def _read_id3_chapters(path: Path) -> list[dict[str, Any]] | None:
    """ID3 CHAP frames → ordered chapter list. Best-effort via mutagen."""
    try:
        from mutagen.id3 import ID3
    except ImportError:
        return None
    try:
        tags = ID3(str(path))
    except Exception:
        return None
    chaps = []
    for frame in tags.getall("CHAP"):
        title = ""
        for sub in getattr(frame, "sub_frames", {}).values() if hasattr(frame, "sub_frames") else []:
            if getattr(sub, "FrameID", "") == "TIT2":
                title = str(sub.text[0]) if sub.text else ""
        start_ms = getattr(frame, "start_time", None)
        if start_ms is not None:
            chaps.append({"title": title, "start_sec": int(start_ms / 1000)})
    chaps.sort(key=lambda c: c["start_sec"])
    return chaps or None


async def download_episode(session, episode: dict[str, Any]) -> bool:
    """Download one episode's enclosure into ``podcasts_dir/<sub_slug>/``.
    Updates the row to 'downloaded' (file_path set) or 'failed'. Returns
    success. Chapters from the downloaded file's ID3 tags supersede any the
    feed carried."""
    import httpx

    ep_id = episode["id"]
    url = episode.get("enclosure_url")
    if not url:
        await session.execute(
            text("UPDATE podcast_episodes SET download_status='failed', error='no enclosure' WHERE id=:id"),
            {"id": ep_id},
        )
        return False

    sub_slug = _slug(episode.get("sub_title") or f"sub-{episode['subscription_id']}")
    dest_dir = Path(settings.podcasts_dir).expanduser() / sub_slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(url.split("?")[0]).suffix.lower() or ".mp3"
    if ext not in (".mp3", ".m4a", ".ogg", ".opus", ".aac", ".flac"):
        ext = ".mp3"
    dest = dest_dir / f"{_slug(episode.get('title') or episode['guid'])[:80]}-{ep_id}{ext}"

    await session.execute(
        text("UPDATE podcast_episodes SET download_status='downloading' WHERE id=:id"),
        {"id": ep_id},
    )
    await session.commit()

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                with dest.open("wb") as fh:
                    async for chunk in r.aiter_bytes(65536):
                        fh.write(chunk)
    except Exception as e:
        log.warning("podcast download failed ep=%s url=%s: %s", ep_id, url, e)
        await session.execute(
            text("UPDATE podcast_episodes SET download_status='failed', error=:e WHERE id=:id"),
            {"id": ep_id, "e": str(e)[:2000]},
        )
        return False

    chapters = _read_id3_chapters(dest)
    await session.execute(
        text(
            """
            UPDATE podcast_episodes
               SET download_status='downloaded',
                   file_path=:fp,
                   downloaded_at=now(),
                   error=NULL,
                   chapters=COALESCE(CAST(:chapters AS JSONB), chapters)
             WHERE id=:id
            """
        ),
        {"id": ep_id, "fp": str(dest), "chapters": _json_or_none(chapters)},
    )
    log.info("podcast download complete ep=%s -> %s", ep_id, dest)
    return True


class PodcastFeedPoller(Worker):
    """Background worker: poll feeds, download newest-N, evict older.

    Declarative registration (design §4.5): gated by
    ``podcast_feed_poller_enabled``, suppressed under stubs so it never
    spins in tests (``tick()`` is unit-tested directly)."""

    name = "podcast_feed_poller"
    enabled_setting = "podcast_feed_poller_enabled"
    interval_setting = "podcast_feed_poller_interval_sec"
    stub_suppressed = True

    async def tick(self) -> dict[str, int]:
        """One full pass: poll every subscription, enqueue+download newest-N,
        evict older. Returns coarse counts for logging/tests."""
        polled = new_eps = downloaded = evicted = 0
        async with session_scope() as session:
            subs = (
                await session.execute(
                    text(
                        "SELECT id, feed_url, title, "
                        "COALESCE(keep_n, :default_keep) AS keep_n "
                        "FROM podcast_subscriptions ORDER BY id"
                    ),
                    {"default_keep": settings.podcast_keep_n},
                )
            ).mappings().all()

            for sub in subs:
                try:
                    new_eps += await poll_subscription(session, dict(sub))
                    polled += 1
                except Exception as e:
                    log.warning("poll failed for sub=%s: %s", sub["id"], e)
                    continue
                await enqueue_newest(session, sub["id"], sub["keep_n"])
            await session.commit()

            # Download pending episodes (carry the sub title for the dest path).
            pending = (
                await session.execute(
                    text(
                        """
                        SELECT e.id, e.subscription_id, e.guid, e.title,
                               e.enclosure_url, s.title AS sub_title
                          FROM podcast_episodes e
                          JOIN podcast_subscriptions s ON s.id = e.subscription_id
                         WHERE e.download_status = 'pending'
                         ORDER BY e.published_at DESC NULLS LAST, e.id DESC
                        """
                    )
                )
            ).mappings().all()
            for ep in pending:
                if await download_episode(session, dict(ep)):
                    downloaded += 1

            for sub in subs:
                evicted += await enforce_keep_n(session, sub["id"], sub["keep_n"])

            if new_eps or downloaded or evicted:
                await session.execute(
                    text("SELECT pg_notify('podcasts_changed', :p)"),
                    {"p": f"new={new_eps} dl={downloaded} evict={evicted}"},
                )
            await session.commit()

        log.info(
            "podcast poller: polled=%d new=%d downloaded=%d evicted=%d",
            polled, new_eps, downloaded, evicted,
        )
        return {"polled": polled, "new": new_eps, "downloaded": downloaded, "evicted": evicted}
