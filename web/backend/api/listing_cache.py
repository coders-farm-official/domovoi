"""Cached filesystem listings behind the paged Videos and Files lists.

Videos and Files have no database index: the answer is whatever is on disk.
Before paging that was one walk per request, and a request was the whole
list. With paging a list is read a page at a time, so walking the tree for
every page would multiply the cost by the number of pages. A cold walk of
10,000 videos took ~2 s on the Windows dev box and a cold listing of one
10,000-entry folder ~6 s (measured 2026-10-11); this module makes every
page after the first a lookup.

**What is cached.** A :class:`Snapshot` — the walked rows, a lowercase
search string per row, and the mtime of every directory the walk read —
per key: ``(library id, root, …)`` for a library walk (Videos), and
``(library id, root, folder)`` for one folder (Files). Sorted views are
built lazily, once per sort, and kept on the snapshot.

**When it is thrown away** (checked on every request; the check is one
``stat`` per directory, ~0.5 ms for a 100-folder library):

* any directory the walk read has a different mtime — a file or folder
  added, removed or renamed in it, by anyone, through any process (the
  core, a plugin, a person at the server's desktop);
* a directory it read is gone;
* it is older than :data:`MAX_AGE_SEC` — the backstop for a change no
  directory mtime records (a file rewritten in place changes its own size
  and mtime but not its folder's; some removable-drive filesystems don't
  keep folder mtimes);
* :func:`invalidate` — the Files writes (upload / delete / move / import)
  and every Documents save drop that library's snapshots at once, so the
  writer's next page shows its own change even when no folder mtime moved
  (a document saved in place). The Music upload and delete only add or
  remove files, which their folder's mtime already says;
* ``refresh=true`` on the request (the pages' Rescan / pull-to-refresh).

The existing bounds stay where they were (``media_walk``'s directory and
file caps); the cache only stops the same bounded work being repeated.
Memory is bounded by :data:`MAX_SNAPSHOTS` and :data:`MAX_CACHED_ROWS`
(least recently used goes first).
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable, Optional

MAX_AGE_SEC = 300.0
MAX_SNAPSHOTS = 64
# And at most this many rows across all of them (~1 KB a row with its
# sorted views): a few huge folders browsed in a row must not pin hundreds
# of MB. The least recently used snapshots go first; the one just built
# always stays.
MAX_CACHED_ROWS = 60_000


@dataclass
class Snapshot:
    rows: list[dict[str, Any]]
    hay: list[str]                       # lowercase search text, parallel to rows
    dirs: dict[str, int]                 # directory → st_mtime_ns when read
    truncated: bool = False
    built_at: float = field(default_factory=time.monotonic)
    expired: bool = False                # set by invalidate() on a snapshot in use
    _views: dict[str, tuple[list[dict[str, Any]], list[tuple], list[int]]] = field(
        default_factory=dict
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def view(
        self, sort: str, key: Callable[[dict[str, Any]], tuple],
    ) -> tuple[list[dict[str, Any]], list[tuple], list[int]]:
        """(rows sorted by ``key``, their keys, each one's index into
        ``rows``/``hay``). Built once per sort per snapshot."""
        with self._lock:
            got = self._views.get(sort)
            if got is None:
                order = sorted(range(len(self.rows)), key=lambda i: key(self.rows[i]))
                got = (
                    [self.rows[i] for i in order],
                    [key(self.rows[i]) for i in order],
                    order,
                )
                self._views[sort] = got
            return got

    def fresh(self) -> bool:
        if self.expired or time.monotonic() - self.built_at > MAX_AGE_SEC:
            return False
        for path, mtime_ns in self.dirs.items():
            try:
                if os.stat(path).st_mtime_ns != mtime_ns:
                    return False
            except OSError:
                return False
        return True


_lock = threading.Lock()
_snapshots: "OrderedDict[Hashable, Snapshot]" = OrderedDict()
_build_locks: dict[Hashable, threading.Lock] = {}
# Bumped per library by invalidate(); a build that started before the bump
# is handed back to its caller but not kept.
_generations: dict[str, int] = {}
_global_generation = 0
stats = {"hits": 0, "builds": 0}


def _library_of(key: Hashable) -> Optional[str]:
    return key[0] if isinstance(key, tuple) and key and isinstance(key[0], str) else None


def get(
    key: Hashable,
    build: Callable[[], Snapshot],
    *,
    force: bool = False,
) -> Snapshot:
    """The snapshot for ``key``, built (or rebuilt) when missing, stale or
    ``force``. Sync — callers run it in a worker thread. One build per key
    at a time: concurrent page requests for a cold list wait for the one
    walk instead of each starting their own. ``key[0]`` must be the
    library id (that is what :func:`invalidate` matches)."""
    lib = _library_of(key)
    with _lock:
        build_lock = _build_locks.setdefault(key, threading.Lock())
    with build_lock:
        with _lock:
            snap = _snapshots.get(key)
            if snap is not None:
                _snapshots.move_to_end(key)
        if snap is not None and not force and snap.fresh():
            stats["hits"] += 1
            return snap
        with _lock:
            gen = (_global_generation, _generations.get(lib, 0) if lib else 0)
        snap = build()
        stats["builds"] += 1
        with _lock:
            now = (_global_generation, _generations.get(lib, 0) if lib else 0)
            if now == gen:
                _snapshots[key] = snap
                _snapshots.move_to_end(key)
                rows = sum(len(x.rows) for x in _snapshots.values())
                while len(_snapshots) > 1 and (
                    len(_snapshots) > MAX_SNAPSHOTS or rows > MAX_CACHED_ROWS
                ):
                    old, gone = _snapshots.popitem(last=False)
                    rows -= len(gone.rows)
                    _build_locks.pop(old, None)
        return snap


def invalidate(library_id: Optional[str] = None) -> None:
    """Forget every snapshot of ``library_id`` (all of them when None). Call
    after any write this process makes into a library."""
    global _global_generation
    with _lock:
        for key in list(_snapshots):
            if library_id is None or _library_of(key) == library_id:
                _snapshots.pop(key).expired = True
        if library_id is None:
            _global_generation += 1
        else:
            _generations[library_id] = _generations.get(library_id, 0) + 1


def clear() -> None:
    """Drop everything (tests)."""
    global _global_generation
    with _lock:
        _global_generation += 1
        _snapshots.clear()
        _build_locks.clear()
        _generations.clear()
        stats["hits"] = 0
        stats["builds"] = 0
