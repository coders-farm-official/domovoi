"""Shared bounded recursive walk of a media library for one file family.

The Videos and Images tabs both enumerate "every matching file across every
Files library" without a DB index. This module owns that walk so the two
routers stay in lockstep on the security posture: hidden + secret-shaped
dirs are pruned, symlinks that escape the root are dropped (files.py
guard), and both the directories visited and the files returned are capped
so a whole removable drive can't turn one request into unbounded work.

Sync — callers run it in a worker thread (``anyio.to_thread``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from web.backend.api.files_security import MediaLibrary, is_sensitive_name

log = logging.getLogger(__name__)

_MAX_DIRS_PER_LIBRARY = 2000


def walk_library_files(
    lib: MediaLibrary,
    extensions: frozenset[str],
    max_files: int,
) -> list[dict[str, Any]]:
    """Every file under ``lib.root_path`` whose suffix is in ``extensions``,
    as ``{library_id, library_label, rel, name, size, mtime}`` rows."""
    root = lib.root_path
    out: list[dict[str, Any]] = []
    dirs_seen = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirs_seen += 1
        if dirs_seen > _MAX_DIRS_PER_LIBRARY or len(out) >= max_files:
            dirnames[:] = []
            log.warning("media walk cap hit in %s — listing truncated", lib.id)
            break
        # Prune hidden + secret-shaped dirs in place so walk never descends.
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and not is_sensitive_name(d)
        ]
        for name in filenames:
            if len(out) >= max_files:
                break
            if Path(name).suffix.lower() not in extensions:
                continue
            if name.startswith(".") or is_sensitive_name(name):
                continue
            entry = Path(dirpath) / name
            real = entry.resolve(strict=False)
            try:
                real.relative_to(root)
            except ValueError:
                continue  # symlink escaping the root
            try:
                st = entry.stat()
            except OSError:
                continue
            out.append(
                {
                    "library_id": lib.id,
                    "library_label": lib.label,
                    "rel": entry.relative_to(root).as_posix(),
                    "name": name,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
    return out
