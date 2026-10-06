"""LRCLIB (lrclib.net): timed lyrics for songs that have none of their own
(contract §7.1, [R1]–[R8]).

Opt-in (``settings.lyrics_lrclib_enabled``; the worker
``domovoi/workers/lyrics_fetch.py`` decides when to ask). What leaves the
box, and only to lrclib.net: a song's title, its artist credit (and primary
performer), its album and its length in whole seconds ([R31]).

* ``GET /api/get?track_name&artist_name[&album_name][&duration]`` — the
  track LRCLIB matches (names compared lower-cased with punctuation as
  spaces, duration within ±2 s); ``404`` = no such track.
* ``GET /api/search?track_name[&artist_name]`` — candidates, which the
  worker accepts only when the length and the names agree.

Records are camelCase: ``id, trackName, artistName, albumName, duration,
instrumental, plainLyrics, syncedLyrics`` (snake_case is accepted too).

**Polite and gated.** One process-wide client: at least one second between
the starts of two requests, the Domovoi User-Agent, and every request
through the egress choke point (``egress.require_destination`` +
``egress.async_client``) — under ``INTERNET_ACCESS=never``
:class:`~domovoi.egress.InternetTurnedOff` reaches the caller and no
request is made. Only a 200 with the expected JSON (or ``/get``'s 404) is
an answer; everything else raises, so the worker can back off instead of
recording "LRCLIB has no lyrics for this":

* :class:`LrclibRateLimited` — 429 / 503 (``Retry-After`` honoured);
* :class:`LrclibUnavailable` — no answer, a timeout, a redirect, another
  5xx, a 200 that is not the expected JSON, a body over 2 MiB;
* :class:`LrclibRejected` — any other 4xx.

The client never retries, never logs a body, and no exception it raises
carries one (lyrics are copyrighted and never logged, [C3]/[C4]): errors
log the status and byte counts at DEBUG.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

from domovoi import egress
from domovoi.lyrics import LoopLocalLock, domovoi_version

log = logging.getLogger(__name__)

BASE = "https://lrclib.net/api"
PROJECT_URL = "https://github.com/coders-farm-official/domovoi"
#: At least this long between the starts of two requests ([R3]).
MIN_INTERVAL_SEC = 1.0
#: A response body bigger than this is not read ([R6]).
MAX_BODY_BYTES = 2 * 1024 * 1024
#: Search records used ([R5]).
SEARCH_RECORDS = 20
CONNECT_TIMEOUT_SEC = 5.0
TIMEOUT_SEC = 15.0


def user_agent() -> str:
    """[R2]: ``Domovoi v<version> (<project url>)``."""
    return f"Domovoi v{domovoi_version()} ({PROJECT_URL})"


class _LrclibRequestsOnlyAtDebug(logging.Filter):
    """httpx logs every request at INFO with its URL — and an LRCLIB URL
    carries a song's title and artist, which never go to the INFO log
    ([R30]). Those lines are kept only while the server logs at DEBUG
    (the web's ``_SnapshotPollOnlyAtDebug`` pattern); every other httpx
    line passes unchanged."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        if "lrclib.net" not in msg:
            return True
        return logging.getLogger().getEffectiveLevel() <= logging.DEBUG


def _quiet_httpx_request_lines() -> None:
    httpx_log = logging.getLogger("httpx")
    if not any(isinstance(f, _LrclibRequestsOnlyAtDebug) for f in httpx_log.filters):
        httpx_log.addFilter(_LrclibRequestsOnlyAtDebug())


_quiet_httpx_request_lines()


@dataclass(frozen=True)
class LrclibRecord:
    id: int
    track_name: str
    artist_name: str
    album_name: str | None
    duration: float | None
    instrumental: bool
    plain: str | None        # plainLyrics (blank → None)
    synced: str | None       # syncedLyrics, LRC text (blank → None)

    def __repr__(self) -> str:  # never the lyrics, not even in a traceback
        return (
            f"LrclibRecord(id={self.id}, duration={self.duration}, "
            f"instrumental={self.instrumental}, plain={len(self.plain or '')} chars, "
            f"synced={len(self.synced or '')} chars)"
        )


class LrclibError(Exception):
    """Base of the client's failures. Never carries a response body."""


class LrclibRateLimited(LrclibError):
    """429 / 503: slow down (``retry_after`` seconds when LRCLIB said)."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


class LrclibUnavailable(LrclibError):
    """No usable answer: ``code`` is ``network``, ``timeout``,
    ``http_<status>`` (a redirect or a 5xx other than 503), ``not_json``,
    ``shape`` or ``too_large``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LrclibRejected(LrclibError):
    """LRCLIB refused the request: a 4xx other than ``/get``'s 404 (and
    429)."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


class LrclibClient(Protocol):
    async def get(self, *, track: str, artist: str, album: str | None,
                  duration: int | None) -> LrclibRecord | None: ...

    async def search(self, *, track: str, artist: str | None) -> list[LrclibRecord]: ...


class LrclibStubClient:
    """``USE_STUBS``: LRCLIB knows nothing."""

    async def get(self, *, track: str, artist: str, album: str | None,
                  duration: int | None) -> LrclibRecord | None:
        return None

    async def search(self, *, track: str, artist: str | None) -> list[LrclibRecord]:
        return []


# ─── Records ([R7]) ───────────────────────────────────────────────────────


def _field(raw: Mapping[str, Any], camel: str, snake: str) -> Any:
    if camel in raw:
        return raw[camel]
    return raw.get(snake)


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def parse_record(raw: Any) -> LrclibRecord | None:
    """One LRCLIB record, or None when it is not one: ``id`` must be an
    integer and the track and artist names strings."""
    if not isinstance(raw, Mapping):
        return None
    rid = raw.get("id")
    if not isinstance(rid, int) or isinstance(rid, bool):
        return None
    track = _field(raw, "trackName", "track_name")
    artist = _field(raw, "artistName", "artist_name")
    if not isinstance(track, str) or not isinstance(artist, str):
        return None
    album = _field(raw, "albumName", "album_name")
    duration = raw.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        duration = None
    instrumental = raw.get("instrumental")
    return LrclibRecord(
        id=rid,
        track_name=track,
        artist_name=artist,
        album_name=album if isinstance(album, str) and album.strip() else None,
        duration=float(duration) if duration is not None else None,
        instrumental=bool(instrumental) if isinstance(instrumental, (bool, int)) else False,
        plain=_text_or_none(_field(raw, "plainLyrics", "plain_lyrics")),
        synced=_text_or_none(_field(raw, "syncedLyrics", "synced_lyrics")),
    )


def parse_retry_after(value: str | None) -> float | None:
    """``Retry-After`` in seconds (the HTTP-date form is not used by
    LRCLIB and reads as unknown)."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


# ─── The HTTP client ([R1]–[R8]) ──────────────────────────────────────────


class HttpLrclibClient:
    """The live client. ``transport`` (an ``httpx`` transport), ``clock``
    and ``sleep`` are seams for tests; the egress hook is installed either
    way, so a test transport sees exactly what the internet would."""

    def __init__(
        self,
        *,
        base: str = BASE,
        transport: Any = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._base = base.rstrip("/")
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._lock = LoopLocalLock()
        self._last_start: float | None = None
        self._clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any]" = (
            weakref.WeakKeyDictionary()
        )

    # -- API --------------------------------------------------------------

    async def get(self, *, track: str, artist: str, album: str | None,
                  duration: int | None) -> LrclibRecord | None:
        """The track LRCLIB matches, or None (404)."""
        params: dict[str, str] = {"track_name": track, "artist_name": artist}
        if album and album.strip():
            params["album_name"] = album
        if duration is not None:
            d = int(duration)
            if 1 <= d <= 3600:
                params["duration"] = str(d)
        status, data = await self._fetch("get", params)
        if status == 404:
            return None
        record = parse_record(data)
        if record is None:
            raise LrclibUnavailable("shape")
        return record

    async def search(self, *, track: str, artist: str | None) -> list[LrclibRecord]:
        """LRCLIB's candidates (at most :data:`SEARCH_RECORDS`)."""
        params: dict[str, str] = {"track_name": track}
        if artist and artist.strip():
            params["artist_name"] = artist
        _status, data = await self._fetch("search", params)
        if not isinstance(data, list):
            raise LrclibUnavailable("shape")
        raw = data[:SEARCH_RECORDS]
        records = [r for r in (parse_record(item) for item in raw) if r is not None]
        if raw and not records:
            raise LrclibUnavailable("shape")
        return records

    async def aclose(self) -> None:
        for client in list(self._clients.values()):
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()

    # -- internals --------------------------------------------------------

    def _client(self) -> Any:
        """One ``httpx.AsyncClient`` per event loop (connections are kept
        alive between requests), built by the egress choke point."""
        import httpx

        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client is None:
            kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(TIMEOUT_SEC, connect=CONNECT_TIMEOUT_SEC),
                "follow_redirects": False,
                "headers": {"User-Agent": user_agent(), "Accept": "application/json"},
            }
            if self._transport is not None:
                kwargs["transport"] = self._transport
            client = egress.async_client(**kwargs)
            self._clients[loop] = client
        return client

    async def _pace(self) -> None:
        if self._last_start is not None:
            wait = MIN_INTERVAL_SEC - (self._clock() - self._last_start)
            if wait > 0:
                await self._sleep(wait)
        self._last_start = self._clock()

    async def _fetch(self, path: str, params: dict[str, str]) -> tuple[int, Any]:
        """``(status, parsed JSON)`` for a 200, ``(404, None)`` for
        ``/get``'s 404; every other outcome raises (see the module
        docstring)."""
        import httpx

        url = f"{self._base}/{path}"
        egress.require_destination(url)                 # InternetTurnedOff → caller
        async with self._lock:
            await self._pace()
            egress.require_destination(url)             # the answer may have changed meanwhile
            client = self._client()
            try:
                async with client.stream("GET", url, params=params) as resp:
                    status = resp.status_code
                    if status == 200:
                        body = await _read_capped(resp)
                    else:
                        body = b""
                    retry_after = resp.headers.get("Retry-After")
            except egress.InternetTurnedOff:
                raise
            except LrclibError:
                raise
            except httpx.TimeoutException:
                log.debug("LRCLIB %s: timed out", path)
                raise LrclibUnavailable("timeout") from None
            except Exception as e:  # noqa: BLE001 — httpx / TLS / socket: no answer
                log.debug("LRCLIB %s: no answer (%s)", path, type(e).__name__)
                raise LrclibUnavailable("network") from None
        if status == 200:
            try:
                return 200, json.loads(body)
            except (ValueError, UnicodeDecodeError):
                log.debug("LRCLIB %s: 200 with %d bytes that are not JSON", path, len(body))
                raise LrclibUnavailable("not_json") from None
        log.debug("LRCLIB %s: HTTP %d", path, status)
        if status == 404 and path == "get":
            return 404, None
        if status in (429, 503):
            raise LrclibRateLimited(parse_retry_after(retry_after))
        if 400 <= status < 500:
            raise LrclibRejected(status)
        raise LrclibUnavailable(f"http_{status}")


async def _read_capped(resp: Any) -> bytes:
    declared = resp.headers.get("Content-Length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY_BYTES:
                log.debug("LRCLIB: a %s-byte body is not read", declared)
                raise LrclibUnavailable("too_large")
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            log.debug("LRCLIB: body over %d bytes, not read", MAX_BODY_BYTES)
            raise LrclibUnavailable("too_large")
        chunks.append(chunk)
    return b"".join(chunks)


_client: LrclibClient | None = None


def get_lrclib_client() -> LrclibClient:
    """The one process-wide client (so the pacing is process-wide);
    ``USE_STUBS`` gets the stub."""
    global _client
    if _client is not None:
        return _client
    from domovoi.config import settings

    _client = LrclibStubClient() if settings.use_stubs else HttpLrclibClient()
    return _client


__all__ = [
    "BASE",
    "HttpLrclibClient",
    "LrclibClient",
    "LrclibError",
    "LrclibRateLimited",
    "LrclibRecord",
    "LrclibRejected",
    "LrclibStubClient",
    "LrclibUnavailable",
    "MAX_BODY_BYTES",
    "MIN_INTERVAL_SEC",
    "SEARCH_RECORDS",
    "get_lrclib_client",
    "parse_record",
    "parse_retry_after",
    "user_agent",
]
