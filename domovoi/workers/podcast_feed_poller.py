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

Every URL this worker fetches — the feed itself and each enclosure —
comes from outside, so both go through ``domovoi.net_safety``: http(s)
only, resolved, never an address inside the house or on the box, and
re-checked on every redirect. The feed body is fetched here and handed to
feedparser as bytes rather than letting feedparser open the URL itself,
and an enclosure download stops at ``MAX_ENCLOSURE_BYTES``.

A feed longer than ``MAX_FEED_BYTES`` (a show with thousands of episodes
can run to 30 MB) is not refused: only its first ``MAX_FEED_BYTES`` are
read, cut after the last complete item (``domovoi.feed_prefix``) and
parsed, at most ``MAX_ITEMS_FROM_A_CUT_FEED`` of them. Feeds list the
newest episodes first, so that is hundreds of recent episodes, far
beyond any keep-N. A feed that lists them oldest
first is logged: what was read (its oldest episodes) is still recorded.
Each feed's ETag / Last-Modified is remembered in memory, per process,
so an unchanged feed answers 304 and is not read again.

``tick()`` returns which shows could not be read and why, in plain words,
for the dashboard's "poll now".

``requires network`` — the whole worker is gated OFF by default
(``podcast_feed_poller_enabled``; it follows ``INTERNET_ACCESS`` when not
set by hand), skipped under USE_STUBS, and ``requires_online``: it doesn't
tick while the connectivity probe says offline (or internet access is
turned off). Downloaded episodes still play fully offline (that's the
SpokenAudioHandler's ``fallback_offline`` path); only polling/downloading
needs the network.

A download that fails because the internet is unreachable or turned off
is not a verdict on the episode: it goes back to ``pending``, never
``failed``, and the pass moves on to the next episode — one show's dead
or slow host must not hold up every other show. The pass stops
downloading only when the line itself is gone (internet access turned
off, the probe reading offline after the failure, or three such failures
in a row). A refused or broken URL, an HTTP error and an over-cap body
are still ``failed``.

Each poll also stores the show's artwork on the server
(``domovoi.podcast_artwork``), which is what the clients display.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text

from domovoi import connectivity, egress, feed_prefix, net_safety, podcast_artwork
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.mpd_library_sync import update_rooms_for
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# A podcast feed is XML in the low hundreds of KB; a show with a decade of
# episodes and long show notes can reach a few MB, and a daily show with
# its whole back catalogue tens of MB. At most this much of a feed is read
# and kept in memory; a longer one is cut after its last complete item.
MAX_FEED_BYTES = 8 * 1024 * 1024
# A feed cut at MAX_FEED_BYTES keeps at most this many of its first
# (newest) episodes, far above any keep-N: parsing and upserting every
# item of an 8 MB prefix (1,300+ episodes, ~16 s of feedparser) on each
# poll costs far more than keep-N will ever use. Paging links (RFC 5005
# rel="next" / "prev-archive") are not followed: the newest page is all
# keep-N needs.
MAX_ITEMS_FROM_A_CUT_FEED = 200
# One episode. Long-form shows run 100-200 MB; 512 MB is far above any real
# episode and stops a "download" that is really a firehose.
MAX_ENCLOSURE_BYTES = 512 * 1024 * 1024


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


class FeedUnreadable(Exception):
    """A feed that was fetched but can't be used. ``reason`` is one of
    :data:`FAILURE_WORDS`' keys; the message is for the log."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


# Why a show couldn't be read, in the words the dashboard shows.
FAILURE_WORDS = {
    "too_large": "feed too large",
    "not_found": "feed not found",
    "http_error": "the feed's server answered with an error",
    "not_a_feed": "not a podcast feed",
    "unreachable": "couldn't reach the feed",
    "refused": "the feed's address isn't allowed",
    "error": "couldn't read the feed",
}


@dataclass(frozen=True)
class FeedDocument:
    content: bytes          # empty when ``not_modified``
    not_modified: bool = False
    truncated: bool = False
    etag: str | None = None
    last_modified: str | None = None


# Cache validators from each feed's last successful poll, per process (no
# DB column holds them): feed URL -> (etag, last_modified). A fetch files
# its answer's validators under _FETCHED; tick() moves them to _VALIDATORS
# only once that poll's episodes are committed, so a poll that failed
# halfway never turns the next one into a 304 that skips them.
_VALIDATORS: dict[str, tuple[str | None, str | None]] = {}
_FETCHED: dict[str, tuple[str | None, str | None]] = {}
_VALIDATORS_LOCK = threading.Lock()


def _conditional_headers(feed_url: str) -> dict[str, str]:
    with _VALIDATORS_LOCK:
        etag, modified = _VALIDATORS.get(feed_url, (None, None))
    out: dict[str, str] = {}
    if etag:
        out["If-None-Match"] = etag
    if modified:
        out["If-Modified-Since"] = modified
    return out


def confirm_validators(feed_url: str) -> None:
    """The poll of ``feed_url`` is committed: its fetch's validators may
    make the next fetch conditional."""
    with _VALIDATORS_LOCK:
        got = _FETCHED.pop(feed_url, None)
        if got is not None and (got[0] or got[1]):
            _VALIDATORS[feed_url] = got


def forget_validators(feed_url: str | None = None) -> None:
    """Drop the remembered validators (one feed, or all): its next poll
    reads the whole document again."""
    with _VALIDATORS_LOCK:
        if feed_url is None:
            _VALIDATORS.clear()
            _FETCHED.clear()
        else:
            _VALIDATORS.pop(feed_url, None)
            _FETCHED.pop(feed_url, None)


def fetch_feed(feed_url: str, *, conditional: bool = True) -> FeedDocument:
    """The feed document, fetched through the outbound-URL check (http(s)
    only, public addresses only, redirects re-checked). At most
    :data:`MAX_FEED_BYTES` are read; a longer feed comes back cut after its
    last complete item (``truncated``), or raises :class:`FeedUnreadable`
    ('too_large') when not even one item fits. With ``conditional``, the
    validators of the last committed poll are sent and an unchanged feed
    comes back ``not_modified``. Blocking: call it in a thread."""
    headers = {"User-Agent": "Domovoi-podcasts/1.0"}
    if conditional:
        headers.update(_conditional_headers(feed_url))
    result = net_safety.fetch_bytes_sync(
        feed_url,
        max_bytes=MAX_FEED_BYTES,
        timeout=30.0,
        headers=headers,
        truncate=True,
    )
    if result.status_code == 304 and conditional:
        return FeedDocument(content=b"", not_modified=True)
    if result.status_code >= 400:
        reason = "not_found" if result.status_code in (404, 410) else "http_error"
        raise FeedUnreadable(reason, f"feed returned {result.status_code}")
    content = result.content
    if result.truncated:
        try:
            content = feed_prefix.cut_to_last_item(
                content, max_items=MAX_ITEMS_FROM_A_CUT_FEED
            )
        except feed_prefix.NoCompleteItem as e:
            raise FeedUnreadable(
                "too_large",
                f"feed is larger than {MAX_FEED_BYTES} bytes and {e}",
            ) from e
    with _VALIDATORS_LOCK:
        _FETCHED[feed_url] = (result.etag, result.last_modified)
    return FeedDocument(
        content=content,
        truncated=result.truncated,
        etag=result.etag,
        last_modified=result.last_modified,
    )


def fetch_feed_bytes(feed_url: str) -> bytes:
    """The whole feed document (unconditional; cut as in
    :func:`fetch_feed` when it is longer than :data:`MAX_FEED_BYTES`)."""
    return fetch_feed(feed_url, conditional=False).content


# What :func:`_parse_feed_blocking` answers for a feed that hasn't changed.
NOT_MODIFIED = object()


def _published_ts(entry: Any) -> float | None:
    pp = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if pp is None:
        return None
    import calendar

    return float(calendar.timegm(pp))


def _lists_oldest_first(entries: list[Any]) -> bool:
    dated = [t for t in (_published_ts(e) for e in entries) if t is not None]
    return len(dated) >= 2 and dated[0] < dated[-1]


def _parse_feed_blocking(feed_url: str) -> Any:
    """Fetch the feed ourselves, then parse the BYTES. feedparser would
    otherwise open the URL itself, and it accepts local paths and
    non-http schemes — this keeps every podcast fetch on the one checked
    path. Answers :data:`NOT_MODIFIED` when the feed hasn't changed since
    the last committed poll.

    A cut feed is read on the promise that it lists newest first. When its
    dates say oldest first, the part read is the show's OLDEST episodes:
    they are still recorded (and the newest of THEM downloaded under
    keep-N), the recent ones are missing, and the log says so."""
    import feedparser  # local import — optional dep

    doc = fetch_feed(feed_url)
    if doc.not_modified:
        return NOT_MODIFIED
    parsed = feedparser.parse(doc.content)
    entries = list(getattr(parsed, "entries", []) or [])
    feed = getattr(parsed, "feed", {}) or {}
    if not entries and not feed.get("title") and getattr(parsed, "bozo", False):
        raise FeedUnreadable("not_a_feed", "the document is not a feed")
    if doc.truncated:
        if _lists_oldest_first(entries):
            log.warning(
                "podcast feed %s is larger than %d bytes and lists its oldest "
                "episodes first: only its oldest %d were read, newer ones are missing",
                feed_url, MAX_FEED_BYTES, len(entries),
            )
        else:
            log.info(
                "podcast feed %s is larger than %d bytes: read its newest %d episodes",
                feed_url, MAX_FEED_BYTES, len(entries),
            )
    return parsed


def describe_poll_failure(e: BaseException) -> str:
    """The :data:`FAILURE_WORDS` key for why a poll failed."""
    if isinstance(e, FeedUnreadable):
        return e.reason
    if isinstance(e, net_safety.ResponseTooLarge):
        return "too_large"
    if is_transient_fetch_error(e):
        return "unreachable"
    if isinstance(e, net_safety.UnsafeOutboundURL):
        return "refused"
    if isinstance(e, net_safety.OutboundFetchError):
        return "unreachable"
    return "error"


def _show_name(sub: dict[str, Any]) -> str:
    """The show's title; a show never read yet has its feed URL there, so
    name its host instead."""
    from urllib.parse import urlsplit

    title = (sub.get("title") or "").strip()
    feed_url = sub.get("feed_url") or ""
    if title and title != feed_url:
        return title
    host = urlsplit(feed_url).hostname if feed_url else None
    return f"the show at {host}" if host else "a show"


async def poll_subscription(session, sub: dict[str, Any]) -> int:
    """Fetch + parse one subscription's feed, upsert episodes. Returns the
    number of NEW episodes recorded. Network + parse happen off the event
    loop. Feed metadata (title/author/artwork) is refreshed on the sub,
    and its artwork is stored on the server (fetched again only when the
    artwork URL changed)."""
    feed_url = sub["feed_url"]
    parsed = await asyncio.to_thread(_parse_feed_blocking, feed_url)
    if parsed is NOT_MODIFIED:
        await session.execute(
            text("UPDATE podcast_subscriptions SET last_polled_at = now() WHERE id = :id"),
            {"id": sub["id"]},
        )
        return 0
    feed = getattr(parsed, "feed", {}) or {}

    # Refresh subscription-level metadata.
    artwork_row = (
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
             RETURNING artwork
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
    ).first()
    # The feed's image, else the directory's artwork stored at subscribe.
    artwork_url = artwork_row[0] if artwork_row is not None else None
    if artwork_url:
        await podcast_artwork.ensure_artwork(sub["id"], artwork_url)

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
    """ID3 CHAP frames → ordered chapter list (``domovoi.media_tags``);
    a chapter's title is its TIT2 sub-frame. Best-effort: None when the
    file has no ID3 tag or no chapters."""
    from domovoi import media_tags

    try:
        tags = media_tags.read_id3(path)
    except Exception:
        return None
    if tags is None:
        return None
    chaps = []
    for frame in tags.getall("CHAP"):
        title = ""
        for sub in frame.sub_frames:
            if sub.id == "TIT2":
                title = str(sub.text[0]) if getattr(sub, "text", None) else ""
        chaps.append({"title": title, "start_sec": int(frame.start_time / 1000)})
    chaps.sort(key=lambda c: c["start_sec"])
    return chaps or None


def is_transient_fetch_error(e: BaseException) -> bool:
    """A failure that says nothing about the episode: internet access is
    turned off for this box, or the line itself failed (no route, a name
    that doesn't resolve while offline, a timeout, a dropped connection).
    Such a download goes back to 'pending' — the durable-failure rule."""
    import httpx

    if isinstance(e, egress.InternetTurnedOff):
        return True
    if isinstance(e, net_safety.UnsafeOutboundURL):
        # Offline, every name "does not resolve"; any other refusal (a
        # house-local address, a bad scheme) is about the URL itself.
        return str(getattr(e, "reason", "")).endswith("does not resolve")
    if isinstance(
        e,
        (httpx.TimeoutException, httpx.NetworkError, httpx.ProxyError, httpx.RemoteProtocolError),
    ):
        return True
    return isinstance(e, (ConnectionError, TimeoutError))


async def download_episode(session, episode: dict[str, Any]) -> bool | None:
    """Download one episode's enclosure into ``podcasts_dir/<sub_slug>/``.
    Updates the row to 'downloaded' (file_path set) and returns True, or
    to 'failed' and returns False — or, when the failure is the line and
    not the episode (:func:`is_transient_fetch_error`), back to 'pending'
    and returns None, so the next pass retries it. Chapters from the
    downloaded file's ID3 tags supersede any the feed carried.

    The enclosure URL comes from the feed, so it goes through the same
    outbound-URL check as the feed (redirects re-checked hop by hop) and
    the download stops at :data:`MAX_ENCLOSURE_BYTES`."""
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
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await net_safety.open_stream(client, url)
            try:
                r.raise_for_status()
                with dest.open("wb") as fh:
                    async for chunk in net_safety.iter_capped(
                        r, MAX_ENCLOSURE_BYTES
                    ):
                        # Switching the box to "never" stops a download
                        # that is already running (back to pending).
                        egress.require_destination(url)
                        fh.write(chunk)
            finally:
                await r.aclose()
    except Exception as e:
        transient = is_transient_fetch_error(e)
        log.warning(
            "podcast download %s ep=%s url=%s: %s",
            "deferred (no internet)" if transient else "failed", ep_id, url, e,
        )
        # A partial file from a refused or over-cap transfer must not be
        # left behind looking like an episode.
        try:
            if dest.exists():
                dest.unlink()
        except OSError:
            pass
        if transient:
            # Not a verdict on the episode: the next pass retries it.
            await session.execute(
                text("UPDATE podcast_episodes SET download_status='pending', error=:e WHERE id=:id"),
                {"id": ep_id, "e": str(e)[:2000]},
            )
            return None
        await session.execute(
            text("UPDATE podcast_episodes SET download_status='failed', error=:e WHERE id=:id"),
            {"id": ep_id, "e": str(e)[:2000]},
        )
        return False

    # In the tags pool, never on the event loop: the file is whatever the
    # feed served (2026-10-16 delta review B1).
    from domovoi import executors

    chapters = await executors.run("tags", _read_id3_chapters, dest)
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


# A pass gives up on the rest of its downloads after this many transient
# failures in a row (the line is probably down even if the probe hasn't
# noticed yet). One dead host is not a down line: the others go on.
MAX_TRANSIENT_FAILURES_IN_A_ROW = 3


async def _line_is_down() -> bool:
    """After a transient download failure: is the internet itself gone
    (turned off for this box, or the connectivity probe now reads
    offline), rather than this one episode's host?"""
    if egress.internet_turned_off():
        return True
    probe = connectivity.current_probe()
    if probe is None:
        return False
    try:
        await probe.check_now()
    except Exception as e:  # noqa: BLE001 — a probe failure says nothing
        log.debug("podcast poller: probe re-check failed: %s", e)
        return False
    return not probe.online


async def download_pending(session, pending: list[dict[str, Any]]) -> int:
    """Download ``pending`` episodes in order; return how many finished.

    A transient failure (:func:`is_transient_fetch_error`) leaves that
    episode pending and moves on to the next one — one show's dead or
    slow host must not starve every other show. The pass stops early only
    when the line itself is gone: internet access turned off, the probe
    reading offline after the failure, or
    :data:`MAX_TRANSIENT_FAILURES_IN_A_ROW` transient failures in a row."""
    downloaded = 0
    transient_run = 0
    for ep in pending:
        ok = await download_episode(session, ep)
        if ok:
            downloaded += 1
            transient_run = 0
        elif ok is None:
            transient_run += 1
            if transient_run >= MAX_TRANSIENT_FAILURES_IN_A_ROW or await _line_is_down():
                # Leave the rest pending for the next pass.
                log.info(
                    "podcast poller: the internet looks unavailable; leaving "
                    "the remaining downloads for the next pass"
                )
                break
        else:
            transient_run = 0
    return downloaded


class PodcastFeedPoller(Worker):
    """Background worker: poll feeds, download newest-N, evict older.

    Declarative registration (design §4.5): gated by
    ``podcast_feed_poller_enabled``, suppressed under stubs so it never
    spins in tests (``tick()`` is unit-tested directly)."""

    name = "podcast_feed_poller"
    enabled_setting = "podcast_feed_poller_enabled"
    interval_setting = "podcast_feed_poller_interval_sec"
    stub_suppressed = True
    # Polling and downloading need the internet: skip ticks (keeping the
    # cadence) while the probe says offline, instead of failing every feed.
    requires_online = True

    async def tick(self) -> dict[str, Any]:
        """One full pass: poll every subscription, enqueue+download newest-N,
        evict older. Returns coarse counts for logging/tests, plus
        ``failed`` and ``failures``: the shows whose feed couldn't be read
        (``subscription_id``, ``title``, ``reason`` code, ``message`` in
        plain words, never exception text). Does nothing when internet
        access is turned off for this box (the web's "poll now" runs this
        directly, outside the worker's online gate)."""
        polled = new_eps = downloaded = evicted = 0
        failures: list[dict[str, Any]] = []
        if egress.internet_turned_off():
            log.info("podcast poller: internet access is turned off; not polling")
            return {"polled": 0, "new": 0, "downloaded": 0, "evicted": 0,
                    "failed": 0, "failures": []}
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

            read: list[str] = []
            for sub in subs:
                try:
                    new_eps += await poll_subscription(session, dict(sub))
                    polled += 1
                except Exception as e:
                    reason = describe_poll_failure(e)
                    log.warning("poll failed for sub=%s (%s): %s", sub["id"], reason, e)
                    failures.append({
                        "subscription_id": sub["id"],
                        "title": _show_name(dict(sub)),
                        "reason": reason,
                        "message": FAILURE_WORDS[reason],
                    })
                    continue
                read.append(sub["feed_url"])
                await enqueue_newest(session, sub["id"], sub["keep_n"])
            await session.commit()
            for feed_url in read:
                confirm_validators(feed_url)

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
            downloaded += await download_pending(session, [dict(ep) for ep in pending])

            for sub in subs:
                evicted += await enforce_keep_n(session, sub["id"], sub["keep_n"])

            if new_eps or downloaded or evicted:
                await session.execute(
                    text("SELECT pg_notify('podcasts_changed', :p)"),
                    {"p": f"new={new_eps} dl={downloaded} evict={evicted}"},
                )
            await session.commit()

        log.info(
            "podcast poller: polled=%d new=%d downloaded=%d evicted=%d failed=%d",
            polled, new_eps, downloaded, evicted, len(failures),
        )
        if downloaded or evicted:
            # The rooms' MPDs play an episode from their own database, which
            # a Windows or network folder never tells about new or removed
            # files (F-V023): ask them to read the podcasts folder.
            await update_rooms_for(tops=["podcasts"], reason="podcast poller")
        return {
            "polled": polled, "new": new_eps, "downloaded": downloaded, "evicted": evicted,
            "failed": len(failures), "failures": failures,
        }
