"""The read-rate bucket: every byte the media runner reads from a library
in Python goes through one token bucket (20 MB/s, design §5 "Disk I/O"),
so a first pass over a big library never starves the house's disk (it is
shared with WSL on Windows hosts). Helper programs get ``ionice -c 3``
instead (:func:`domovoi.chat_video.media._argv`).

One bucket per process (:data:`BUCKET`); tests make their own with a fake
clock.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from typing import Awaitable, Callable

RATE_BYTES_PER_S = 20 * 1024 * 1024
#: Largest single read; also the bucket's burst.
CHUNK = 1024 * 1024
#: A fingerprint reads this much from each end of the file.
FINGERPRINT_EDGE = 1024 * 1024


class TokenBucket:
    """``rate`` bytes a second, at most ``burst`` saved up."""

    def __init__(self, rate: float = RATE_BYTES_PER_S, burst: float | None = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = float(rate)
        self.burst = float(burst if burst is not None else CHUNK)
        self._clock = clock
        self._sleep = sleep
        self._tokens = self.burst
        self._t = clock()
        self._lock = asyncio.Lock()
        #: Bytes let through (for tests and the log).
        self.taken = 0

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.burst, self._tokens + (now - self._t) * self.rate)
        self._t = now

    async def take(self, n: int) -> None:
        """Wait until ``n`` bytes may be read (a request larger than the
        burst is let through once the bucket has paid for it in full)."""
        if n <= 0:
            return
        async with self._lock:
            self._refill()
            self._tokens -= n
            if self._tokens < 0:
                await self._sleep(-self._tokens / self.rate)
                self._refill()
            self.taken += n


BUCKET = TokenBucket()


async def read_range(path: Path, offset: int, length: int, *, bucket: TokenBucket | None = None) -> bytearray:
    """``length`` bytes of ``path`` from ``offset``, in :data:`CHUNK`
    pieces, each paid for in the bucket first."""
    bucket = bucket or BUCKET
    out = bytearray(max(0, length))
    view = memoryview(out)
    got = 0
    # The open and every read run in a worker thread: a sleeping disk or a
    # hung mount stalls this item, never the event loop (voice).
    f = await asyncio.to_thread(open, path, "rb")
    try:
        await asyncio.to_thread(f.seek, offset)
        while got < length:
            n = min(CHUNK, length - got)
            await bucket.take(n)
            k = await asyncio.to_thread(f.readinto, view[got:got + n])
            if not k:
                break
            got += k
    finally:
        view.release()
        await asyncio.to_thread(f.close)
    if got < length:
        del out[got:]
    return out          # a bytearray: one copy of the file in memory, not two


async def fingerprint(path: Path, *, bucket: TokenBucket | None = None) -> str:
    """Size + SHA-256 of the first and last 1 MB (design §5): enough to
    match a moved or renamed file, at most 2 MB read."""
    size = (await asyncio.to_thread(os.stat, path)).st_size
    h = hashlib.sha256()
    h.update(str(size).encode())
    h.update(await read_range(path, 0, min(size, FINGERPRINT_EDGE), bucket=bucket))
    if size > FINGERPRINT_EDGE:
        tail = min(FINGERPRINT_EDGE, size - FINGERPRINT_EDGE)
        h.update(await read_range(path, size - tail, tail, bucket=bucket))
    return f"{size}:{h.hexdigest()}"
