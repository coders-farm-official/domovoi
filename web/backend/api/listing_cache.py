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
``stat`` per directory, ~0.5 ms for a 100-folder library — a listing that
read more than :data:`VALIDATE_ALWAYS_DIRS` folders is re-checked at most
every :data:`VALIDATE_EVERY_SEC`, so scrolling a 5,000-folder library
doesn't stat 5,000 folders per page):

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

**Telling the pages.** Every invalidation counts as a change of that
library (:func:`changes`), and the state poll's tick looks for changes on
disk under the listings people are viewing (:func:`sweep_in_use`). The
``files`` realtime channel pushes the counts as ``files.changed``, and the
Videos and Files pages (web and app) re-read when it moves: an upload from
a phone, a delete, a picture a plugin saved, shows up without leaving the
page.

**Memory** (owner decision 2026-10-12: the walk has no count caps any
more, so one library can be very large). The budget is in bytes,
:data:`MAX_CACHE_BYTES`, estimated per row: measured 2026-10-12 at 50,000
rows, a walked video row with its search text is ~520 B and a folder entry
~560 B (:data:`ROW_BYTES` = 600), and each sorted view adds ~190 B a row
(:data:`VIEW_ROW_BYTES` = 200). Past the budget — or past
:data:`MAX_SNAPSHOTS` listings — the least recently used listings go,
except that a listing read in the last :data:`IN_USE_SEC` is never evicted
to make room: a library bigger than the whole budget stays cached alone
while someone scrolls it (the budget is exceeded, and a warning logged)
rather than being re-walked on every page. It goes once it idles.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable, Optional

log = logging.getLogger(__name__)

MAX_AGE_SEC = 300.0
MAX_SNAPSHOTS = 64
# The memory budget and its per-row estimate (see the module docstring).
MAX_CACHE_BYTES = 256 * 1024 * 1024
ROW_BYTES = 600
VIEW_ROW_BYTES = 200
# A listing read this recently is in use: never evicted to make room.
IN_USE_SEC = 120.0
# A listing that read more folders than this re-checks their mtimes at most
# this often (a smaller one checks on every request).
VALIDATE_ALWAYS_DIRS = 2000
VALIDATE_EVERY_SEC = 2.0
# Filter results kept per listing (a search scrolled page by page).
_MEMO_MAX = 8


@dataclass
class Snapshot:
    rows: list[dict[str, Any]]
    hay: list[str]                       # lowercase search text, parallel to rows
    dirs: dict[str, int]                 # directory → st_mtime_ns when read
    truncated: bool = False
    built_at: float = field(default_factory=time.monotonic)
    expired: bool = False                # set by invalidate() on a snapshot in use
    stopped: Optional[str] = None        # why a truncated walk stopped ("time")
    used_at: float = field(default_factory=time.monotonic)
    validated_at: float = field(default_factory=time.monotonic)
    _views: dict[str, tuple[list[dict[str, Any]], list[tuple], list[int]]] = field(
        default_factory=dict
    )
    _memo: "OrderedDict[Hashable, Any]" = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def est_bytes(self) -> int:
        return len(self.rows) * (ROW_BYTES + VIEW_ROW_BYTES * len(self._views))

    def memo(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        """``compute()`` once per ``key`` for this snapshot (a search's
        matching rows, say), the last few kept: scrolling a filtered list
        doesn't re-scan every row per page."""
        with self._lock:
            if key in self._memo:
                self._memo.move_to_end(key)
                return self._memo[key]
        value = compute()
        with self._lock:
            self._memo[key] = value
            while len(self._memo) > _MEMO_MAX:
                self._memo.popitem(last=False)
        return value

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
        now = time.monotonic()
        if self.expired or now - self.built_at > MAX_AGE_SEC:
            return False
        if len(self.dirs) > VALIDATE_ALWAYS_DIRS and now - self.validated_at < VALIDATE_EVERY_SEC:
            return True
        for path, mtime_ns in self.dirs.items():
            try:
                if os.stat(path).st_mtime_ns != mtime_ns:
                    return False
            except OSError:
                return False
        self.validated_at = now
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
            snap.used_at = time.monotonic()
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
                _evict(keep=key)
        return snap


def _evict(keep: Hashable) -> None:
    """Make room (under ``_lock``): least recently used first, never ``keep``
    (just built), and never a listing in use for want of BYTES — only the
    count bound may take one of those."""
    now = time.monotonic()
    total = sum(x.est_bytes() for x in _snapshots.values())
    for key in list(_snapshots):
        if total <= MAX_CACHE_BYTES and len(_snapshots) <= MAX_SNAPSHOTS:
            return
        if key == keep:
            continue
        snap = _snapshots[key]
        if now - snap.used_at < IN_USE_SEC and len(_snapshots) <= MAX_SNAPSHOTS:
            continue
        total -= snap.est_bytes()
        del _snapshots[key]
        _build_locks.pop(key, None)
    if total > MAX_CACHE_BYTES:
        log.warning(
            "listing cache over its budget (~%d MB of %d MB) — every listing in it is in use",
            total // (1024 * 1024), MAX_CACHE_BYTES // (1024 * 1024),
        )


def cached_bytes() -> int:
    """The cache's estimated size (tests, diagnostics)."""
    with _lock:
        return sum(x.est_bytes() for x in _snapshots.values())


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


def changes() -> dict[str, int]:
    """How many times each library changed, as far as this process knows:
    ``{library id: count}``, plus ``"*"`` for an invalidate-everything. The
    state socket's ``files`` channel (``web/backend/realtime.py``) pushes it,
    so a Videos or Files page on screen re-reads when the number moves:
    after a write here (:func:`invalidate`) or a change on disk that
    :func:`sweep_in_use` saw."""
    with _lock:
        out = dict(_generations)
        if _global_generation:
            out["*"] = _global_generation
        return out


def sweep_in_use() -> list[str]:
    """The libraries whose listing someone is looking at (read in the last
    :data:`IN_USE_SEC`) that changed on disk since it was walked: a folder
    it read has a new mtime, or is gone — a file landed or left through any
    process (the core, a plugin, a person at the server). Each one is
    invalidated, which moves :func:`changes`; nothing else is read.

    Called on the state poll's tick. A listing that read more than
    :data:`VALIDATE_ALWAYS_DIRS` folders is checked at most every
    :data:`VALIDATE_EVERY_SEC`, as a request would; its age alone
    (:data:`MAX_AGE_SEC`) is not a change, so an idle page is not re-read
    every few minutes for nothing."""
    now = time.monotonic()
    with _lock:
        candidates = [
            (key, snap) for key, snap in _snapshots.items()
            if not snap.expired and now - snap.used_at < IN_USE_SEC and _library_of(key)
        ]
    changed: list[str] = []
    for key, snap in candidates:
        lib = _library_of(key)
        if lib in changed:
            continue
        if len(snap.dirs) > VALIDATE_ALWAYS_DIRS and now - snap.validated_at < VALIDATE_EVERY_SEC:
            continue
        moved = False
        for path, mtime_ns in list(snap.dirs.items()):
            try:
                if os.stat(path).st_mtime_ns != mtime_ns:
                    moved = True
                    break
            except OSError:
                moved = True
                break
        if moved:
            changed.append(lib)
        else:
            snap.validated_at = now
    for lib in changed:
        invalidate(lib)
    return changed


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
