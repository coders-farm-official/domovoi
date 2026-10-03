"""Podcast artwork, fetched by the server and served from it (fix B11).

A podcast's artwork is a publisher's URL (the feed's ``<image>``, or
iTunes' ``artworkUrl600``). The browser and the phone used to load it
themselves: every page view sent the household's devices to the
publisher's CDN, the one leak no server-side gate can stop. Now the
server fetches it, once per source URL, through the outbound choke point
(``domovoi.net_safety`` per hop, on an ``egress`` client: public http(s)
only, redirects re-checked, at most :data:`ARTWORK_MAX_BYTES`, refused
when internet access is turned off) and keeps only a sniffed JPEG, PNG,
WebP or GIF. Clients load ``/api/podcasts/subscriptions/{id}/artwork``.

On disk, under ``settings.podcast_artwork_dir``:

* ``<id>.img`` + ``<id>.json`` (``{source_sha256, content_type,
  fetched_at}``) per subscription. A new download happens only when the
  source URL's sha changes.
* ``discover/<key>`` — thumbnails for directory search results. A result's
  artwork is a server path with a key this process minted
  (:func:`discover_key`); the server fetches only URLs it minted keys for,
  never a URL a client sends (no SSRF).

Both the core (the feed poller, a voice subscribe) and the web process
(subscribe, "poll now", the list's background fill, the routes) use this
module; writes are atomic, so the two never see a half-written file.
Nothing here raises to its caller: a refusal or any error leaves the
cache as it was and the client shows its placeholder.

The web process imports this module at the top of
``web/backend/api/podcasts.py`` (the web import guard refuses later
imports).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from domovoi import egress, net_safety
from domovoi.config import settings

log = logging.getLogger(__name__)

ARTWORK_MAX_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT_SEC = 20.0
USER_AGENT = "Domovoi-podcasts/1.0"
# Directory-search thumbnails: keys remembered, and files kept, at most.
DISCOVER_MAX = 512

_KEY_RE = re.compile(r"[0-9a-f]{32}")


def artwork_dir() -> Path:
    return Path(settings.podcast_artwork_dir).expanduser()


def source_sha(url: str) -> str:
    return hashlib.sha256((url or "").strip().encode("utf-8")).hexdigest()


def sniff_image(data: bytes) -> str | None:
    """The content type of an image this module keeps, from its bytes;
    None for anything else (an HTML error page, an SVG, a video)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _paths(sub_id: int) -> tuple[Path, Path]:
    base = artwork_dir()
    n = int(sub_id)
    return base / f"{n}.img", base / f"{n}.json"


def _read_meta(sub_id: int) -> dict[str, Any] | None:
    img, meta = _paths(sub_id)
    if not img.is_file():
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("content_type") or not data.get("source_sha256"):
        return None
    return data


def cached_file(sub_id: int) -> Path | None:
    """The stored image for a subscription, or None."""
    return _paths(sub_id)[0] if _read_meta(sub_id) is not None else None


def cached_content_type(sub_id: int) -> str | None:
    meta = _read_meta(sub_id)
    return str(meta["content_type"]) if meta else None


def api_path(sub_id: int, source_url: str | None = None) -> str | None:
    """``/api/podcasts/subscriptions/{id}/artwork?v=<8 hex of the source
    sha>`` when artwork is stored, else None. With ``source_url``, only
    when what is stored came from that URL (a subscription whose artwork
    URL changed, or an id reused after a database restore, shows the
    placeholder until the new image is in)."""
    meta = _read_meta(sub_id)
    if meta is None:
        return None
    sha = str(meta["source_sha256"])
    if source_url is not None and sha != source_sha(source_url):
        return None
    return f"/api/podcasts/subscriptions/{int(sub_id)}/artwork?v={sha[:8]}"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


async def fetch_image(url: str) -> tuple[bytes, str] | None:
    """Fetch ``url`` through the choke point and return (bytes, content
    type) for an image this module keeps; None for a refusal, an error,
    an HTTP error status, a body over :data:`ARTWORK_MAX_BYTES` or
    anything that isn't a JPEG/PNG/WebP/GIF. Never raises."""
    if egress.internet_turned_off():
        return None
    try:
        client = egress.async_client(timeout=FETCH_TIMEOUT_SEC)
        try:
            result = await net_safety.fetch_bytes(
                url,
                max_bytes=ARTWORK_MAX_BYTES,
                timeout=FETCH_TIMEOUT_SEC,
                headers={"User-Agent": USER_AGENT, "Accept": "image/*"},
                client=client,
            )
        finally:
            await client.aclose()
    except Exception as e:
        log.info("podcast artwork: not fetched from %s: %s", url, e)
        return None
    if result.status_code >= 400:
        log.info("podcast artwork: %s answered HTTP %d", url, result.status_code)
        return None
    content_type = sniff_image(result.content)
    if content_type is None:
        log.info("podcast artwork: %s is not a JPEG, PNG, WebP or GIF; not kept", url)
        return None
    return result.content, content_type


async def ensure_artwork(sub_id: int, source_url: str | None) -> Path | None:
    """Make sure subscription ``sub_id``'s artwork from ``source_url`` is
    stored; return its path, or None. Downloads only when nothing is
    stored or the stored image came from another URL. Never raises: a
    refusal (internet off) or any error returns None and changes
    nothing."""
    try:
        url = (source_url or "").strip()
        if not url:
            return cached_file(sub_id)
        sha = source_sha(url)
        meta = _read_meta(sub_id)
        if meta is not None and meta.get("source_sha256") == sha:
            return _paths(sub_id)[0]
        fetched = await fetch_image(url)
        if fetched is None:
            return None
        data, content_type = fetched
        img, meta_path = _paths(sub_id)
        _atomic_write(img, data)
        _atomic_write(meta_path, json.dumps({
            "source_sha256": sha,
            "content_type": content_type,
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        }).encode("utf-8"))
        log.info("podcast artwork: stored for subscription %s (%d bytes)", sub_id, len(data))
        return img
    except Exception as e:
        log.warning("podcast artwork: couldn't store for subscription %s: %s", sub_id, e)
        return None


def forget(sub_id: int) -> None:
    """Delete a subscription's stored artwork (on unsubscribe)."""
    for p in _paths(sub_id):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.debug("podcast artwork: couldn't delete %s: %s", p, e)


# ─── Background fills ──────────────────────────────────────────────────

_tasks: set[asyncio.Task[Any]] = set()
_fill_tried: set[tuple[int, str]] = set()


def schedule_ensure(sub_id: int, source_url: str | None) -> bool:
    """Fire-and-forget :func:`ensure_artwork` on the running loop. Returns
    whether a task was started (False with no running loop, no URL, or
    internet access turned off)."""
    if not (source_url or "").strip() or egress.internet_turned_off():
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    task = loop.create_task(ensure_artwork(sub_id, source_url), name=f"podcast-artwork-{sub_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


def schedule_fill_once(sub_id: int, source_url: str | None) -> bool:
    """:func:`schedule_ensure` at most once per (subscription, source URL)
    per process — the subscriptions list calls it on every read, so a
    subscription that never polled (the poller is off) still gets its
    artwork, without a fetch per page view."""
    if not (source_url or "").strip() or egress.internet_turned_off():
        return False
    marker = (int(sub_id), source_sha(source_url or ""))
    if marker in _fill_tried:
        return False
    _fill_tried.add(marker)
    return schedule_ensure(sub_id, source_url)


# ─── Directory-search thumbnails ───────────────────────────────────────

# key -> source URL, for keys this process minted (bounded LRU).
_discover_urls: "OrderedDict[str, str]" = OrderedDict()
# feed URL -> the directory's artwork URL for it (bounded LRU), so a
# subscribe to a search result stores its artwork without trusting a URL
# from the client.
_discovered_feeds: "OrderedDict[str, str]" = OrderedDict()


def _remember(store: "OrderedDict[str, str]", key: str, value: str) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > DISCOVER_MAX:
        store.popitem(last=False)


def discover_key(url: str) -> str:
    """Mint (and remember) the key a directory-search thumbnail is served
    under: the first 32 hex of sha256(url)."""
    url = (url or "").strip()
    key = source_sha(url)[:32]
    _remember(_discover_urls, key, url)
    return key


def discover_api_path(url: str | None) -> str | None:
    if not (url or "").strip():
        return None
    return f"/api/podcasts/discover/artwork/{discover_key(url or '')}"


def remember_discovered(feed_url: str | None, artwork_url: str | None) -> None:
    """Note the directory's artwork URL for a feed it returned."""
    if (feed_url or "").strip() and (artwork_url or "").strip():
        _remember(_discovered_feeds, (feed_url or "").strip(), (artwork_url or "").strip())


def discovered_artwork_for(feed_url: str | None) -> str | None:
    """The artwork URL a directory search returned for ``feed_url`` in
    this process, if any."""
    return _discovered_feeds.get((feed_url or "").strip())


def _discover_dir() -> Path:
    return artwork_dir() / "discover"


def discover_cached(key: str) -> tuple[bytes, str] | None:
    """A stored thumbnail for a key this process minted, without fetching."""
    if not _KEY_RE.fullmatch(key or "") or key not in _discover_urls:
        return None
    path = _discover_dir() / key
    try:
        data = path.read_bytes()
    except OSError:
        return None
    content_type = sniff_image(data)
    return (data, content_type) if content_type else None


def _prune_discover() -> None:
    try:
        files = sorted(
            (p for p in _discover_dir().iterdir() if p.is_file() and _KEY_RE.fullmatch(p.name)),
            key=lambda p: p.stat().st_mtime,
        )
    except OSError:
        return
    for p in files[: max(0, len(files) - DISCOVER_MAX)]:
        try:
            p.unlink()
        except OSError:
            pass


async def discover_artwork(key: str) -> tuple[bytes, str] | None:
    """The thumbnail for a key this process minted: from disk, else
    fetched from the URL it was minted for (and stored). An unknown key —
    one this process never minted — is None and fetches nothing."""
    cached = discover_cached(key)
    if cached is not None:
        return cached
    url = _discover_urls.get(key or "") if _KEY_RE.fullmatch(key or "") else None
    if url is None:
        return None
    fetched = await fetch_image(url)
    if fetched is None:
        return None
    try:
        _atomic_write(_discover_dir() / key, fetched[0])
        _prune_discover()
    except Exception as e:
        log.debug("podcast artwork: couldn't keep thumbnail %s: %s", key, e)
    return fetched
