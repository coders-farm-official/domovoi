"""Shared bounded recursive walk of a media library for one file family.

The Videos and Images tabs both enumerate "every matching file across every
Files library" without a DB index. This module owns that walk so the two
routers stay in lockstep on the security posture: hidden + secret-shaped
dirs are pruned, symlinks that escape the root are dropped (files.py
guard), and both the directories visited and the files returned are capped
so a whole removable drive can't turn one request into unbounded work.

Cost (measured 2026-10-11 on the Windows dev box, 10,000 videos in 100
folders): resolving every FILE's realpath made one walk ~2 s; it is ~0.3 s
now. The realpath check runs once per DIRECTORY — a directory whose
realpath is inside the root holds files whose realpath is too, unless the
file is itself a link, and those few are still resolved one by one — and
the relative path is built as a string. Same answer, a fraction of the
system calls. ``listing_cache`` keeps the result between pages; the
``meta`` out-parameter hands it each visited directory's mtime so it can
tell when the walk is stale without walking again.

Sync — callers run it in a worker thread (``anyio.to_thread``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from web.backend.api.files_security import MediaLibrary, is_sensitive_name

log = logging.getLogger(__name__)

_MAX_DIRS_PER_LIBRARY = 2000


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
    max_files: int,
    meta: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Every file under ``lib.root_path`` whose suffix is in ``extensions``,
    as ``{library_id, library_label, rel, name, size, mtime}`` rows.

    ``meta``, when given, is filled with ``dirs`` ({visited dir → its
    st_mtime_ns}) and ``truncated`` (a cap was hit)."""
    root = lib.root_path
    out: list[dict[str, Any]] = []
    dirs: dict[str, int] = {}
    truncated = False
    seen_real: set[Path] = set()
    # (directory, its path relative to the root as "a/b/" — built as a
    # string, because PurePath.relative_to costs ~0.1 ms a call on Windows)
    stack: list[tuple[Path, str]] = [(root, "")]
    dirs_seen = 0
    while stack:
        here, prefix = stack.pop()
        dirs_seen += 1
        if dirs_seen > _MAX_DIRS_PER_LIBRARY or len(out) >= max_files:
            truncated = True
            log.warning("media walk cap hit in %s — listing truncated", lib.id)
            break
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
            if len(out) >= max_files:
                truncated = True
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
        meta["truncated"] = truncated
    return out
