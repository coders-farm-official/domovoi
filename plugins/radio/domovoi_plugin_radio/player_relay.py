"""A room's music player plays an internet station through the core.

A room's player (an MPD container) would fetch a station URL with its own
DNS lookup and its own redirect following, out of reach of every check in
the core. So it is never handed a station URL. The handler hands it a
one-use URL on the core instead (``GET /v1/plugins/radio/player-relay/
<token>``), and the core fetches the station itself with
:func:`relay.open_audio`, the fetch the dashboard's browser player uses:
http(s) only, every redirect hop checked and pinned to the address its
check judged, audio only (a playlist would make the player open its
entries itself).

The token:

* is random (256 bits), and is good for ONE connection: the first request
  that presents it spends it, whoever made it;
* must be presented within :data:`OPEN_WITHIN_SEC` of being made;
* is bound to one room's player and one station: it is served only to the
  address that room's player calls from (``sdk.playback.player_route``),
  and only ever fetches the station it was made for;
* is replaced when the same room is given a new one, which also ends a
  relay still running for that room.

The relayed stream is bounded: after a :data:`BURST_BYTES` start it is
read no faster than :data:`MAX_BYTES_PER_SEC` (far above any audio
stream; the player reads at playback speed anyway), it ends after
:data:`MAX_DURATION_SEC`, and when the internet answer turns to No. When
playback stops the player closes its connection and the core closes the
station's.

The player asks for ICY metadata (MPD sends ``Icy-MetaData: 1``); that
request is passed to the station and its ``icy-metaint`` answer passed
back with the bytes unchanged, so the song title the station announces
still reaches the room's "now playing".
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from domovoi.sdk import egress

from domovoi_plugin_radio import relay

log = logging.getLogger(__name__)

#: The path, under the plugin's core router, the player is handed.
ROUTE = "/player-relay/{token}"
_PREFIX = "/v1/plugins/radio/player-relay/"

#: How long a new token may wait for the player's request.
OPEN_WITHIN_SEC = 30.0
#: The longest one relayed connection lasts.
MAX_DURATION_SEC = 24 * 3600.0
#: The start a stream may read at full speed, then the steady ceiling.
BURST_BYTES = 8 * 1024 * 1024
MAX_BYTES_PER_SEC = 512 * 1024
_CHUNK = 16 * 1024

#: Station headers passed to the player (ICY: what the station calls
#: itself and how often it sends a title).
_ICY_HEADERS = ("icy-metaint", "icy-name", "icy-genre", "icy-description", "icy-br")


@dataclass
class _Grant:
    room_id: str
    url: str
    route: Any            # domovoi.player_route.PlayerRoute
    open_by: float        # time.monotonic()
    spent: bool = False
    ended: bool = False


#: token -> grant. At most one per room: a new grant replaces the room's.
_GRANTS: dict[str, _Grant] = {}


def _clock() -> float:
    return time.monotonic()


def _room_token(room_id: str) -> str | None:
    for token, grant in _GRANTS.items():
        if grant.room_id == room_id:
            return token
    return None


def revoke_room(room_id: str) -> None:
    """Forget ``room_id``'s token, and end its relay if one is running."""
    token = _room_token(room_id)
    if token is not None:
        _GRANTS.pop(token).ended = True


def mint(room_id: str, url: str, route: Any) -> str:
    """A one-use relay URL that streams ``url`` to ``room_id``'s player,
    which reaches the core by ``route`` (``sdk.playback.player_route``).
    Replaces (and ends) the room's previous one."""
    revoke_room(room_id)
    token = secrets.token_urlsafe(32)
    _GRANTS[token] = _Grant(
        room_id=room_id, url=url, route=route, open_by=_clock() + OPEN_WITHIN_SEC
    )
    return route.url(_PREFIX + token)


def station_url_for(player_url: str | None) -> str | None:
    """The station a relay URL handed to a player streams, while that URL
    is still its room's (the now-playing matcher); None otherwise."""
    if not player_url or _PREFIX not in player_url:
        return None
    grant = _GRANTS.get(player_url.rsplit("/", 1)[-1])
    return grant.url if grant is not None else None


def _take(token: str, caller: str | None) -> _Grant | None:
    """Spend ``token`` for ``caller``: the grant when it is unspent, in
    time and asked for by its room's player; None otherwise. A token is
    spent by the first request that presents it, whoever made it."""
    grant = _GRANTS.get(token)
    if grant is None or grant.spent or grant.ended:
        return None
    grant.spent = True
    if _clock() > grant.open_by:
        return None
    if not grant.route.admits(caller):
        log.warning(
            "radio relay: room %s's token presented from another address; spent",
            grant.room_id,
        )
        return None
    return grant


def _icy_headers(upstream: relay.Upstream, asked: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in _ICY_HEADERS:
        if name == "icy-metaint" and not asked:
            continue
        value = upstream.header(name)
        if not value:
            continue
        value = "".join(c for c in value if c.isascii() and c.isprintable())[:200]
        if name == "icy-metaint" and not value.isdigit():
            continue
        if value:
            out[name] = value
    return out


async def serve(token: str, request: Any) -> StreamingResponse:
    """The player's request for ``token``: 404 for a token that isn't
    good (unknown, spent, late, another caller), 409 / 502 for a station
    that can't be relayed, else the station's audio."""
    caller = request.client.host if request.client else None
    grant = _take(token, caller)
    if grant is None:
        raise HTTPException(status_code=404, detail="not found")
    if egress.check_destination(grant.url) is not None:
        egress.log_refusal(f"radio relay {grant.url[:120]}")
        raise egress.http_exception("internet radio")
    asked = (request.headers.get("icy-metadata") or "").strip() == "1"
    # Identity: the bytes (and the ICY title blocks counted through them)
    # pass to the player exactly as the station sent them.
    headers = {"Icy-MetaData": "1" if asked else "0", "Accept-Encoding": "identity"}
    agent = request.headers.get("user-agent")
    if agent:
        headers["User-Agent"] = agent[:200]
    try:
        upstream = await relay.open_audio(
            grant.url, headers=headers, sniff=True, chunk_size=_CHUNK
        )
    except relay.RelayRefused as e:
        log.warning("radio relay: room %s: %s", grant.room_id, e.detail)
        raise HTTPException(status_code=e.status, detail=e.detail) from e

    async def _gen():
        began = _clock()
        sent = 0
        try:
            async for chunk in upstream.body():
                if grant.ended:
                    break
                if egress.check_destination(grant.url) is not None:
                    egress.log_refusal(f"radio relay {grant.url[:120]}")
                    break
                elapsed = _clock() - began
                if elapsed > MAX_DURATION_SEC:
                    log.info("radio relay: room %s reached its time limit", grant.room_id)
                    break
                sent += len(chunk)
                ahead = (sent - BURST_BYTES) / MAX_BYTES_PER_SEC - elapsed
                if ahead > 0:
                    await asyncio.sleep(ahead)
                yield chunk
        finally:
            grant.ended = True
            await upstream.aclose()

    try:
        return StreamingResponse(
            _gen(), media_type=upstream.media_type,
            headers={"Cache-Control": "no-store", **_icy_headers(upstream, asked)},
        )
    except Exception:
        await upstream.aclose()
        raise


def reset_for_tests() -> None:
    _GRANTS.clear()
