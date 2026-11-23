"""A room's music player plays a remote stream or file through the core (SDK 1.16).

A room's player (an MPD container) would fetch a remote URL with its own
DNS lookup and its own redirect following, out of reach of every check in
the core. So a plugin never hands it one. It asks
``sdk.playback.relay_url(room_id, remote_url, kind=...)`` and hands the
player the URL that comes back: ``GET /v1/player-relay/<token>`` on the
core, which fetches the remote URL itself through
:func:`domovoi.net_safety.open_stream` (http(s) only, every redirect hop
re-checked and pinned to the address its check judged) and passes the
bytes on. Only audio or other media is passed: a page, a script or a
playlist (which a player would open entry by entry, past the relay) is
refused before a byte moves.

The remote URL is fixed when the token is minted; the player's request
chooses nothing but, for a file, a byte range. So the route is not a
proxy anyone can point somewhere.

The token is random (256 bits), bound to one room, one remote URL and the
address that room's player calls from (:func:`domovoi.player_route.player_route`),
and must be first presented within :data:`OPEN_WITHIN_SEC`. A presentation
from any other address ends it. A new token for the room, or a stop in the
room, ends the old one and any stream it is serving. Then by kind:

* ``radio`` (a live stream): good for ONE connection, as the radio plugin's
  own relay was (M11). ICY metadata is asked for when the player asks, and
  ``icy-metaint`` passed back with the bytes unchanged, so the station's
  song titles still reach "now playing". A ``Range`` header is not passed.
* ``file`` (a finite media file): good for any number of requests for
  :data:`FILE_TTL_SEC` after it was minted, because a player that seeks or
  resumes after a pause opens the file again with ``Range: bytes=N-``. One
  plain byte range is passed to the remote server and its ``206`` answer
  (``Content-Range``, ``Content-Length``, ``Accept-Ranges``) passed back. A
  new request ends the token's earlier stream, so one token is never more
  than one open fetch. No ICY is asked for or passed.

Every request re-checks the token's room grant and the caller's address,
and the internet answer: under ``INTERNET_ACCESS=never`` a request is
refused and a running stream ends. Each connection reads no faster than
:data:`MAX_BYTES_PER_SEC` after a :data:`BURST_BYTES` start, and ends
after :data:`MAX_DURATION_SEC`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from domovoi import egress, net_safety
from domovoi.webkit import open_endpoint

log = logging.getLogger(__name__)

KINDS = ("file", "radio")

#: The core route the player is handed.
ROUTE = "/v1/player-relay/{token}"
_PREFIX = "/v1/player-relay/"

#: How long a new token may wait for the player's first request.
OPEN_WITHIN_SEC = 30.0
#: How long a ``file`` token admits requests (seeks, a resume), from minting.
FILE_TTL_SEC = 6 * 3600.0
#: The longest one relayed connection lasts.
MAX_DURATION_SEC = 24 * 3600.0
#: The start a connection may read at full speed, then the steady ceiling.
BURST_BYTES = 8 * 1024 * 1024
MAX_BYTES_PER_SEC = 512 * 1024
#: The most tokens held at once (one per room; this bounds a plugin that
#: mints for room names that never play).
MAX_GRANTS = 256
CONNECT_TIMEOUT = 10.0
_CHUNK = 16 * 1024

#: Station headers passed to the player for ``radio``.
_ICY_HEADERS = ("icy-metaint", "icy-name", "icy-genre", "icy-description", "icy-br")
_RANGE = re.compile(r"bytes=(\d{1,18})?-(\d{1,18})?")
_CONTENT_RANGE = re.compile(r"bytes (\d{1,18})-(\d{1,18})/(\d{1,18}|\*)")

# Media types. A list of other URLs (a playlist or manifest) is never
# passed, whatever it is spelled as.
_PLAYLIST_TYPES = frozenset({
    "application/vnd.apple.mpegurl", "application/x-mpegurl", "application/mpegurl",
    "audio/mpegurl", "audio/x-mpegurl", "audio/scpls", "audio/x-scpls",
    "application/pls+xml", "application/xspf+xml", "application/dash+xml",
    "audio/x-ms-asx", "video/x-ms-asf", "video/vnd.mpeg.dash.mpd",
})
_PLAYLIST_PREFIXES = (b"#EXTM3U", b"[playlist]", b"<?xml", b"<asx", b"<playlist")
_GENERIC_TYPES = frozenset({"", "application/octet-stream"})
_EXTRA_MEDIA_TYPES = frozenset({"application/ogg"})


class RelayUnavailable(RuntimeError):
    """The room's player can't be reached by a relay URL (no player yet,
    Docker didn't answer). Play nothing, or say so."""


class _Refused(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class _Grant:
    room_id: str
    url: str
    kind: str
    route: Any            # domovoi.player_route.PlayerRoute
    open_by: float        # time.monotonic()
    expires_at: float
    opened: bool = False
    ended: bool = False
    #: Bumped by every admitted request: a ``file`` stream ends when a
    #: later request for its token is admitted.
    generation: int = 0


#: token -> grant. At most one per room: a new grant replaces the room's.
_GRANTS: dict[str, _Grant] = {}


def _clock() -> float:
    return time.monotonic()


def _end(token: str) -> None:
    grant = _GRANTS.pop(token, None)
    if grant is not None:
        grant.ended = True


def revoke_room(room_id: str) -> None:
    """End ``room_id``'s token, and any stream it is serving."""
    for token in [t for t, g in _GRANTS.items() if g.room_id == room_id]:
        _end(token)


def _prune(now: float) -> None:
    for token, g in list(_GRANTS.items()):
        if (not g.opened and now > g.open_by) or now > g.expires_at:
            _end(token)
    while len(_GRANTS) >= MAX_GRANTS:
        _end(next(iter(_GRANTS)))


def mint(room_id: str, url: str, route: Any, *, kind: str = "file") -> str:
    """A relay URL that plays ``url`` to ``room_id``'s player, which reaches
    the core by ``route`` (``player_route``). Replaces (and ends) the room's
    previous one. ``ValueError`` for an unknown ``kind``, an empty room or a
    URL that isn't http(s)."""
    if kind not in KINDS:
        raise ValueError(f"relay kind must be one of {KINDS}, not {kind!r}")
    room_id = (room_id or "").strip()
    if not room_id:
        raise ValueError("relay needs a room")
    parts = urlsplit(str(url or ""))
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("relay URL must be an http(s) URL with a host")
    revoke_room(room_id)
    now = _clock()
    _prune(now)
    token = secrets.token_urlsafe(32)
    _GRANTS[token] = _Grant(
        room_id=room_id, url=str(url), kind=kind, route=route,
        open_by=now + OPEN_WITHIN_SEC,
        expires_at=now + (FILE_TTL_SEC if kind == "file" else MAX_DURATION_SEC),
    )
    return route.url(_PREFIX + token)


def target_for(player_url: str | None) -> str | None:
    """The remote URL a relay URL handed to a player stands for, while its
    token is still good; None otherwise (any other URL included)."""
    if not player_url or _PREFIX not in player_url:
        return None
    grant = _GRANTS.get(player_url.rsplit("/", 1)[-1])
    return grant.url if grant is not None and not grant.ended else None


def _admit(token: str, caller: str | None) -> _Grant | None:
    """The grant for ``token`` when this request may use it; None otherwise.
    A ``radio`` token is spent by the first request that presents it,
    whoever made it."""
    grant = _GRANTS.get(token)
    if grant is None or grant.ended:
        return None
    now = _clock()
    if grant.kind == "radio":
        if grant.opened:
            return None
        grant.opened = True
        if now > grant.open_by:
            _end(token)
            return None
    elif (not grant.opened and now > grant.open_by) or now > grant.expires_at:
        _end(token)
        return None
    if not grant.route.admits(caller):
        log.warning(
            "player relay: room %s's token presented from another address; ended",
            grant.room_id,
        )
        _end(token)
        return None
    grant.opened = True
    grant.generation += 1
    return grant


# ─── The upstream fetch ───────────────────────────────────────────────────


def _base(declared: str | None) -> str:
    return (declared or "").split(";", 1)[0].strip().lower()


def _is_token(minor: str) -> bool:
    return bool(minor) and all(c.isascii() and (c.isalnum() or c in "+-.") for c in minor)


def media_type(declared: str | None, kind: str) -> str | None:
    """The type to serve for an upstream that declared ``declared``, or None
    when it is not something to play: ``audio/*`` for both kinds, also
    ``video/*`` for a file (a player plays the sound of one); never a
    playlist. A missing or generic type is ``audio/mpeg`` for radio (a
    player probes it) and ``application/octet-stream`` for a file."""
    base = _base(declared)
    if base in _GENERIC_TYPES:
        return "audio/mpeg" if kind == "radio" else "application/octet-stream"
    if base in _PLAYLIST_TYPES:
        return None
    if base in _EXTRA_MEDIA_TYPES:
        return base
    major, _, minor = base.partition("/")
    allowed = ("audio",) if kind == "radio" else ("audio", "video")
    if major in allowed and _is_token(minor):
        return base
    return None


def looks_like_playlist(head: bytes) -> bool:
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    return any(stripped[: len(p)].lower() == p.lower() for p in _PLAYLIST_PREFIXES)


def _range_header(value: str | None) -> str | None:
    """One plain byte range (``bytes=N-``, ``bytes=N-M``, ``bytes=-N``) as
    sent on, or None (anything else is ignored: the whole file is served)."""
    m = _RANGE.fullmatch((value or "").strip())
    if m is None or (m.group(1) is None and m.group(2) is None):
        return None
    if m.group(1) is not None and m.group(2) is not None and int(m.group(1)) > int(m.group(2)):
        return None
    return f"bytes={m.group(1) or ''}-{m.group(2) or ''}"


def _shown_type(declared: str | None) -> str:
    base = _base(declared)[:60]
    if base and all(c.isascii() and (c.isalnum() or c in "+-./") for c in base):
        return base
    return "(unrecognised)"


@dataclass
class _Upstream:
    client: Any
    response: Any
    media_type: str = ""
    head: bytes = b""
    _chunks: Any = None
    _closed: bool = False

    def _iterator(self) -> Any:
        if self._chunks is None:
            self._chunks = self.response.aiter_bytes(chunk_size=_CHUNK)
        return self._chunks

    async def body(self):
        if self.head:
            yield self.head
        async for chunk in self._iterator():
            yield chunk

    def header(self, name: str) -> str | None:
        return self.response.headers.get(name)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.response.aclose()
        finally:
            await self.client.aclose()


async def _open(url: str, headers: dict[str, str], kind: str, ranged: bool) -> _Upstream:
    import httpx

    client = httpx.AsyncClient(timeout=httpx.Timeout(CONNECT_TIMEOUT, read=None))
    try:
        resp = await net_safety.open_stream(client, url, headers=headers)
    except net_safety.OutboundFetchError as e:
        await client.aclose()
        raise _Refused(409, str(e)) from e
    except Exception as e:
        await client.aclose()
        raise _Refused(502, f"upstream unreachable: {e}") from e
    up = _Upstream(client=client, response=resp)
    try:
        if resp.status_code == 416 and ranged:
            raise _Refused(416, "range not satisfiable")
        if resp.status_code >= 400 or resp.status_code in (204, 205):
            raise _Refused(502, f"upstream returned {resp.status_code}")
        if resp.status_code == 206 and not ranged:
            raise _Refused(502, "upstream sent a range nobody asked for")
        declared = resp.headers.get("content-type")
        served = media_type(declared, kind)
        if served is None:
            raise _Refused(502, f"upstream is not playable media (content type {_shown_type(declared)})")
        up.media_type = served
        chunks = up._iterator()
        while not up.head:
            try:
                up.head = await chunks.__anext__()
            except StopAsyncIteration:
                break
        if looks_like_playlist(up.head):
            raise _Refused(502, "upstream is a playlist, not media")
    except BaseException:
        await up.aclose()
        raise
    return up


def _clean(value: str | None) -> str:
    return "".join(c for c in (value or "") if c.isascii() and c.isprintable())[:200]


def _icy_headers(up: _Upstream, asked: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in _ICY_HEADERS:
        if name == "icy-metaint" and not asked:
            continue
        value = _clean(up.header(name))
        if name == "icy-metaint" and not value.isdigit():
            continue
        if value:
            out[name] = value
    return out


def _file_headers(up: _Upstream) -> dict[str, str]:
    out: dict[str, str] = {}
    length = _clean(up.header("content-length"))
    if length.isdigit():
        out["content-length"] = length
    if up.response.status_code == 206:
        cr = _clean(up.header("content-range"))
        if _CONTENT_RANGE.fullmatch(cr):
            out["content-range"] = cr
    if _clean(up.header("accept-ranges")).lower() == "bytes":
        out["accept-ranges"] = "bytes"
    return out


async def serve(token: str, request: Any) -> StreamingResponse:
    """The player's request for ``token``: 404 for a token that isn't good
    (unknown, spent, late, expired, another caller), 409 for a remote URL
    the server won't fetch or internet set to No, 416 for a range the
    remote server can't satisfy, 502 for one that can't be relayed, else
    the bytes."""
    caller = request.client.host if request.client else None
    grant = _admit(token, caller)
    if grant is None:
        raise HTTPException(status_code=404, detail="not found")
    if egress.check_destination(grant.url) is not None:
        egress.log_refusal(f"player relay {grant.url[:120]}")
        raise egress.http_exception("a room's stream")
    # Identity: the bytes (and any ICY title blocks counted through them)
    # pass exactly as the remote server sent them.
    headers = {"Accept-Encoding": "identity"}
    agent = request.headers.get("user-agent")
    if agent:
        headers["User-Agent"] = _clean(agent)
    asked = False
    ranged = False
    if grant.kind == "radio":
        asked = (request.headers.get("icy-metadata") or "").strip() == "1"
        headers["Icy-MetaData"] = "1" if asked else "0"
    else:
        wanted = _range_header(request.headers.get("range"))
        if wanted is not None:
            headers["Range"] = wanted
            ranged = True
    try:
        up = await _open(grant.url, headers, grant.kind, ranged)
    except _Refused as e:
        log.warning("player relay: room %s: %s", grant.room_id, e.detail)
        raise HTTPException(status_code=e.status, detail=e.detail) from e

    mine = grant.generation

    async def _gen():
        began = _clock()
        sent = 0
        try:
            async for chunk in up.body():
                if grant.ended or grant.generation != mine:
                    break
                if egress.check_destination(grant.url) is not None:
                    egress.log_refusal(f"player relay {grant.url[:120]}")
                    break
                elapsed = _clock() - began
                if elapsed > MAX_DURATION_SEC:
                    log.info("player relay: room %s reached its time limit", grant.room_id)
                    break
                sent += len(chunk)
                ahead = (sent - BURST_BYTES) / MAX_BYTES_PER_SEC - elapsed
                if ahead > 0:
                    await asyncio.sleep(ahead)
                yield chunk
        finally:
            if grant.kind == "radio":
                grant.ended = True
            await up.aclose()

    extra = _icy_headers(up, asked) if grant.kind == "radio" else _file_headers(up)
    try:
        return StreamingResponse(
            _gen(), status_code=up.response.status_code if ranged else 200,
            media_type=up.media_type,
            headers={"Cache-Control": "no-store", **extra},
        )
    except Exception:
        await up.aclose()
        raise


router = APIRouter()


@router.get(ROUTE)
@open_endpoint
async def player_relay_stream(token: str, request: Request):
    """A room's music player fetching a remote stream or file through the
    core. No credential: the player has none. The token in the path is the
    credential; it is answered only to the address that room's player
    calls from, and only ever fetches the URL it was minted for. Anything
    else is a 404."""
    return await serve(token, request)


def reset_for_tests() -> None:
    _GRANTS.clear()
