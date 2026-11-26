"""Open a station's stream for the dashboard's browser player (``web.py``).

The web process relays a station's audio to the browser instead of letting
the browser fetch it. (A room's music player is relayed by the core itself,
``sdk.playback.relay_url``, SDK 1.17, which makes the same checks.) The
station is opened here, through ``net_safety.open_raw_stream``: http(s) only,
every redirect hop re-checked and each connection pinned to the address its
check judged; it asks for ``identity`` and refuses a compressed answer, and
the body is read raw (``net_safety.iter_raw``), never decoded. Only an
audio stream is relayed (``stream_types``): a page, a script or a playlist
(which a player would open entry by entry on its own) is refused before a
byte moves.

Imports only ``domovoi.webkit`` and this package's stdlib modules, so the
web process may import it (see ``web.py``'s import rules).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from domovoi.webkit import net_safety

from domovoi_plugin_radio.stream_types import audio_media_type, looks_like_playlist

#: The connect / first-answer timeout for a station's stream.
CONNECT_TIMEOUT = 10.0


class RelayRefused(Exception):
    """The stream can't be relayed: ``status`` 409 (the address is one the
    server won't fetch) or 502 (unreachable, an error, not audio)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class Upstream:
    """An open station stream: the caller owns it (``aclose``)."""

    client: Any
    response: Any
    media_type: str
    #: Bytes already read to look at the start of the body.
    head: bytes = b""
    _closed: bool = field(default=False, repr=False)
    chunk_size: int = 64 * 1024
    _chunks: Any = field(default=None, repr=False)

    def _iterator(self) -> Any:
        if self._chunks is None:
            self._chunks = net_safety.iter_raw(self.response, self.chunk_size)
        return self._chunks

    async def body(self):
        """The body in pieces of at most :attr:`chunk_size`, starting with
        :attr:`head`."""
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


def _shown_type(declared: str | None) -> str:
    """The upstream's type for an error message, only when it is a plain
    ASCII type token."""
    base = (declared or "").split(";", 1)[0].strip().lower()[:60]
    if base and all(c.isascii() and (c.isalnum() or c in "+-./") for c in base):
        return base
    return "(unrecognised)"


async def open_audio(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    sniff: bool = False,
    chunk_size: int = 64 * 1024,
) -> Upstream:
    """Open ``url`` for relaying, or raise :class:`RelayRefused`.

    ``sniff``: also read the first chunk and refuse a body that is a
    playlist whatever type it was served under (kept in ``Upstream.head``
    for the caller to send first)."""
    import httpx

    client = httpx.AsyncClient(timeout=httpx.Timeout(CONNECT_TIMEOUT, read=None))
    try:
        resp = await net_safety.open_raw_stream(client, url, headers=headers)
    except net_safety.UnsupportedContentEncoding as e:
        # A station stream is relayed as it came; a compressed one is not.
        await client.aclose()
        raise RelayRefused(502, "upstream sent a compressed stream") from e
    except net_safety.OutboundFetchError as e:
        await client.aclose()
        raise RelayRefused(409, str(e)) from e
    except Exception as e:
        await client.aclose()
        raise RelayRefused(502, f"upstream stream unreachable: {e}") from e
    upstream = Upstream(client=client, response=resp, media_type="", chunk_size=chunk_size)
    try:
        if resp.status_code >= 400:
            raise RelayRefused(502, f"upstream returned {resp.status_code}")
        declared = resp.headers.get("content-type")
        media_type = audio_media_type(declared)
        if media_type is None:
            raise RelayRefused(
                502,
                f"upstream is not an audio stream (content type {_shown_type(declared)})",
            )
        upstream.media_type = media_type
        if sniff:
            chunks = upstream._iterator()
            while not upstream.head:
                try:
                    upstream.head = await chunks.__anext__()
                except StopAsyncIteration:
                    break
            if looks_like_playlist(upstream.head):
                raise RelayRefused(502, "upstream is a playlist, not an audio stream")
    except BaseException:
        await upstream.aclose()
        raise
    return upstream
