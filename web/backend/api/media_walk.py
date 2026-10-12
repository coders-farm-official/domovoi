"""Shared recursive walk of a media library for one file family.

The Videos tab enumerates "every matching file across every Files library"
without a DB index. This module owns that walk so every caller keeps the
same security posture: hidden + secret-shaped dirs are pruned, a symlinked
directory is never followed, a directory (a Windows junction, say) whose
realpath leaves the root is dropped whole, a file that is a link is dropped
when its realpath leaves the root, and a folder reached twice (a junction
loop) is walked once.

**No count caps (owner decision 2026-10-12).** The walk used to stop at
2,000 files and 2,000 folders per library, so one request could never
become unbounded work while the whole list went out in one answer. Every
list is paged now and the walk is cached between pages (``listing_cache``),
so the walk enumerates the whole library. What stays is a TIME budget,
:data:`WALK_TIME_BUDGET_SEC`: a walk still going after that long (a huge or
very slow removable drive) stops, keeps what it found, and says so
(``meta["truncated"]``, ``meta["stopped"] = "time"``) — the Videos page
tells the person rather than hanging a request on it.

Cost (Windows dev box): 10,000 videos in 100 folders ~0.1 s (2026-10-11);
50,000 in 500 folders ~1 s for the walk and the first page together
(2026-10-12, with other test runs loading the box).
The realpath check runs once per DIRECTORY — a directory whose realpath is
inside the root holds files whose realpath is too, unless the file is
itself a link, and those few are still resolved one by one — and the
relative path is built as a string. ``meta`` hands ``listing_cache`` each
visited directory's mtime so it can tell when the walk is stale without
walking again.

Sync — callers run it in a worker thread (``anyio.to_thread``).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from web.backend.api.files_security import MediaLibrary, is_sensitive_name

log = logging.getLogger(__name__)

# The one remaining safety limit: a walk running longer than this stops and
# reports itself truncated. 50,000 files take well under a second on a
# local disk; minutes means a slow or enormous drive, and the page should
# say so rather than keep a request waiting indefinitely.
WALK_TIME_BUDGET_SEC = 120.0


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_link(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        is_junction = getattr(entry, "is_junction", None)   # 3.12+, Windows
        return bool(is_junction and is_junction())
    except OSError:
        return True   # can't tell — treat as a link, i.e. resolve it


def walk_library_files(
    lib: MediaLibrary,
    extensions: frozenset[str],
    max_files: Optional[int] = None,
    meta: Optional[dict[str, Any]] = None,
    *,
    time_budget: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Every file under ``lib.root_path`` whose suffix is in ``extensions``,
    as ``{library_id, library_label, rel, name, size, mtime}`` rows.

    ``max_files`` is an optional cap for a caller that wants one (none of
    the paged lists does). ``time_budget`` (default
    :data:`WALK_TIME_BUDGET_SEC`) bounds the walk's wall time. ``meta``,
    when given, is filled with ``dirs`` ({visited dir → its st_mtime_ns}),
    ``truncated`` (the walk stopped early) and ``stopped`` (``"time"`` /
    ``"files"`` / None)."""
    root = lib.root_path
    budget = WALK_TIME_BUDGET_SEC if time_budget is None else time_budget
    deadline = time.monotonic() + budget
    out: list[dict[str, Any]] = []
    dirs: dict[str, int] = {}
    stopped: Optional[str] = None
    seen_real: set[Path] = set()
    # (directory, its path relative to the root as "a/b/" — built as a
    # string, because PurePath.relative_to costs ~0.1 ms a call on Windows)
    stack: list[tuple[Path, str]] = [(root, "")]
    while stack:
        if time.monotonic() > deadline:
            stopped = "time"
            log.warning("media walk of %s stopped after %.0f s — listing truncated", lib.id, budget)
            break
        if max_files is not None and len(out) >= max_files:
            stopped = "files"
            break
        here, prefix = stack.pop()
        # One realpath per directory: a junction (Windows) or any other
        # redirect that lands outside the root drops the whole directory,
        # and a loop back to a folder already walked is not walked twice.
        real_here = here.resolve(strict=False)
        if not _inside(real_here, root) or real_here in seen_real:
            continue
        seen_real.add(real_here)
        try:
            dirs[str(here)] = os.stat(here).st_mtime_ns
            with os.scandir(here) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        subdirs: list[tuple[Path, str]] = []
        for entry in entries:
            name = entry.name
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if is_dir:
                # Prune hidden + secret-shaped dirs so the walk never
                # descends; a symlinked dir is never followed (os.walk's
                # followlinks=False, kept).
                if name.startswith(".") or is_sensitive_name(name):
                    continue
                try:
                    if entry.is_symlink():
                        continue
                except OSError:
                    continue
                subdirs.append((here / name, f"{prefix}{name}/"))
                continue
            if max_files is not None and len(out) >= max_files:
                stopped = "files"
                break
            if os.path.splitext(name)[1].lower() not in extensions:
                continue
            if name.startswith(".") or is_sensitive_name(name):
                continue
            if _is_link(entry) and not _inside((here / name).resolve(strict=False), root):
                continue  # symlink escaping the root
            try:
                st = entry.stat()
            except OSError:
                continue
            out.append(
                {
                    "library_id": lib.id,
                    "library_label": lib.label,
                    "rel": prefix + name,
                    "name": name,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
        # Reverse so the stack pops them in name order (stable walk order).
        stack.extend(reversed(subdirs))
    if meta is not None:
        meta["dirs"] = dirs
        meta["truncated"] = stopped is not None
        meta["stopped"] = stopped
    return out
