"""Lyrics for the library's songs (V021: ``track_lyrics``, the
``track_lyrics_shown`` view, ``track_lyric_lines``).

Three sources, in the household's order (owner decisions 2026-10-03):

1. a ``.lrc`` beside the song with the same base name, any case
   (:mod:`domovoi.lyrics.sources`), re-read when it changes;
2. lyrics inside the file: ID3 USLT / SYLT, Vorbis LYRICS /
   UNSYNCEDLYRICS, the MP4 lyrics atom, WM/Lyrics, APE Lyrics;
3. LRCLIB (lrclib.net), only while the household has it switched on
   (:mod:`domovoi.clients.lrclib`, :mod:`domovoi.workers.lyrics_fetch`),
   whose timed lyrics are also saved as a Domovoi-marked ``.lrc`` next to a
   song that has none (:mod:`domovoi.lyrics.sidecar`).

Modules: :mod:`.lrc` (the format, pure), :mod:`.sources` (reading the
local sources), :mod:`.sidecar` (writing ``.lrc`` files),
:mod:`.store` (the database writes), :mod:`.status` (the core snapshot's
``"lyrics"`` key). Nothing here logs a lyric's text: ids, counts and
lengths only.

Importable by both processes: this package module is stdlib only.
"""

from __future__ import annotations

import asyncio
import weakref
from typing import Any

#: Version of the local scan's reading rules. ``track_lyrics.scan_version``
#: records the version that read a row; bumping it re-reads every song.
SCAN_VERSION = 1


def domovoi_version() -> str:
    """The running Domovoi's version string, for the LRCLIB User-Agent and
    the ``[ve:]`` tag of a ``.lrc`` Domovoi writes (contract [F6])."""
    try:
        from importlib.metadata import version

        return version("domovoi")
    except Exception:  # noqa: BLE001 — not installed as a distribution
        return "1.0.0"


class LoopLocalLock:
    """An :class:`asyncio.Lock` per running event loop, used as
    ``async with LOCK:``.

    A plain module-level ``asyncio.Lock`` binds itself to the first loop
    that has to wait on it, and then refuses every other loop. The core has
    one loop for its whole life, so there this is exactly one lock; the
    test suite runs a fresh loop per test, and a lock contended in one test
    must still work in the next."""

    def __init__(self) -> None:
        self._locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
            weakref.WeakKeyDictionary()
        )

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[loop] = lock
        return lock

    def locked(self) -> bool:
        """Whether the current loop's lock is held (False outside a loop)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        lock = self._locks.get(loop)
        return lock is not None and lock.locked()

    async def __aenter__(self) -> "LoopLocalLock":
        await self._lock().acquire()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self._lock().release()


__all__ = ["SCAN_VERSION", "LoopLocalLock", "domovoi_version"]
