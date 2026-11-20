"""Is this core asking the language models for something right now?

Why: on a Mac the speech service (whisper.cpp on Metal) and the
household's Ollama share one graphics chip. A voice turn decoded there
while Ollama is generating waits behind it (measured: the GPU contention
gate failed with Ollama at 61%), so with ``whisper_remote_shares_gpu`` the
speech-to-text client (clients/whisper_remote.py) hears that turn on the
VM's own processor instead. This module is how it knows.

The signal is a request from this core that is still open: Ollama doesn't
say it is busy, but every generation the core asks for is an HTTP request
that stays open until the answer is read (or the reader stops). Counted:

* every request :class:`~domovoi.clients.ollama.RealOllamaClient` makes to
  a generating endpoint (``/api/chat``, ``/api/generate``, ``/api/embed``,
  ``/api/embeddings``), through :class:`TrackingTransport`, from the
  moment it is sent until its response body is closed: the router's
  streamed call stops counting when it stops reading, which is when Ollama
  stops generating;
* :func:`~domovoi.clients.ollama.chat_stream` (chat videos, scanned
  letters) and a Letta chat (Letta's model is the same Ollama), through
  :func:`generating`.

Not counted: anything another process asks the same Ollama (the web
dashboard's typed chat runs in the web process; a plugin's own
container), and model listing, pulls and loads by ``/api/show``. Those
can still slow a turn down; they never route it.

A request left open by a bug stops counting after
:data:`MAX_REQUEST_SEC`, and the gap between two calls of the same answer
(the router's, then the answer's) is bridged by :data:`QUIET_SEC`, so the
signal doesn't flicker inside one turn. Process-local, no I/O.
"""

from __future__ import annotations

import contextlib
import itertools
import time
from typing import Any, AsyncIterator

import httpx

#: Paths whose requests make Ollama generate (or embed) on the GPU.
GENERATING_PATHS = ("/api/chat", "/api/generate", "/api/embed", "/api/embeddings")
#: A request open longer than this no longer counts (a long document
#: summary on a slow model takes minutes; nothing real takes ten).
MAX_REQUEST_SEC = 600.0
#: After the last request ends, still busy for this long: a turn's next
#: call (the answer after the router) starts within milliseconds.
QUIET_SEC = 1.0

_open: dict[int, tuple[str, float]] = {}
_ids = itertools.count(1)
_last_end: float | None = None


def _start(kind: str) -> int:
    key = next(_ids)
    _open[key] = (kind, time.monotonic())
    return key


def _end(key: int) -> None:
    global _last_end
    if _open.pop(key, None) is not None:
        _last_end = time.monotonic()


@contextlib.asynccontextmanager
async def generating(kind: str = "chat") -> AsyncIterator[None]:
    """Counts as one generation in flight for as long as the block runs."""
    key = _start(kind)
    try:
        yield
    finally:
        _end(key)


def in_flight(now: float | None = None) -> int:
    """Generations open right now (stale ones left out)."""
    now = time.monotonic() if now is None else now
    return sum(1 for _kind, t0 in list(_open.values()) if now - t0 < MAX_REQUEST_SEC)


def busy(now: float | None = None) -> bool:
    """A generation is open, or one ended less than :data:`QUIET_SEC` ago."""
    now = time.monotonic() if now is None else now
    if in_flight(now):
        return True
    return _last_end is not None and now - _last_end < QUIET_SEC


def reset() -> None:
    """Forget everything (tests)."""
    global _last_end
    _open.clear()
    _last_end = None


def _generates(request: httpx.Request) -> bool:
    return request.method == "POST" and request.url.path.rstrip("/").endswith(GENERATING_PATHS)


class _CountedStream(httpx.AsyncByteStream):
    """A response body that ends the count when it is closed (read to the
    end, or the reader stopped early: httpx closes it either way)."""

    def __init__(self, inner: Any, key: int) -> None:
        self._inner = inner
        self._key = key

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for part in self._inner:
            yield part

    async def aclose(self) -> None:
        try:
            await self._inner.aclose()
        finally:
            _end(self._key)


class TrackingTransport(httpx.AsyncBaseTransport):
    """``inner`` (by default httpx's own), counting each generating request
    from send to the close of its response body."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = inner if inner is not None else httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not _generates(request):
            return await self._inner.handle_async_request(request)
        key = _start("ollama")
        try:
            response = await self._inner.handle_async_request(request)
        except BaseException:
            _end(key)
            raise
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_CountedStream(response.stream, key),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()
