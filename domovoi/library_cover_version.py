"""A library track's cover version (V059): ``library_tracks.cover_rev``.

The API hands the counter out as ``cover_v`` and puts it on the cover URL
(``/api/music/library/{id}/cover?v=<cover_v>``), so a client's cached
picture is replaced exactly when there is a new one, and the
``library_cover_changed`` NOTIFY (realtime channel ``library.cover``) tells
an open page or app which tracks moved.

It moves when:

* the Cover Art Archive worker writes art into the file or keeps Domovoi's
  own copy (``workers/library_cover_art.py``),
* an Undo takes it out again (``web/backend/api/library_tools.py``),
* a rescan finds the file or its album-folder image changed
  (:func:`cover_signature` against ``cover_sig``;
  ``workers/library_indexer.py``).

The signature is stats only (the file's mtime and size, the folder
image's name, mtime and size): no tag is parsed, so a rescan stays a walk.
A tag edit that leaves the picture alone also moves it; that costs a
client one revalidation, never a stale picture.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text

log = logging.getLogger(__name__)

NOTIFY_CHANNEL = "library_cover_changed"

# Album-folder image names, as the cover route takes them
# (web/backend/api/music.py ``_folder_image``), in order of preference.
FOLDER_STEMS = ("cover", "folder", "front", "album")
FOLDER_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _folder_entry(folder: Path) -> str:
    """``name:mtime_ns:size`` of the folder image the cover route would
    take (by name; the route also checks the bytes), or ``""``."""
    found: list[tuple[tuple[int, int, str], os.DirEntry]] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                stem, ext = os.path.splitext(entry.name)
                stem, ext = stem.lower(), ext.lower()
                if stem in FOLDER_STEMS and ext in FOLDER_EXTS:
                    found.append(((FOLDER_STEMS.index(stem), FOLDER_EXTS.index(ext), entry.name), entry))
    except OSError:
        return ""
    for _, entry in sorted(found, key=lambda f: f[0]):
        try:
            st = entry.stat()
        except OSError:
            continue
        if st.st_size > 0:
            return f"{entry.name}:{st.st_mtime_ns}:{st.st_size}"
    return ""


def cover_signature(path: Path | str, folder_cache: dict[str, str] | None = None) -> str | None:
    """A short hash of what the track's art is read from, or None when the
    file can't be stat'ed. ``folder_cache`` (folder → entry) lets a sweep
    list each folder once. Blocking — run it in a worker thread."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = str(p.parent)
    if folder_cache is not None and key in folder_cache:
        folder = folder_cache[key]
    else:
        folder = _folder_entry(p.parent)
        if folder_cache is not None:
            folder_cache[key] = folder
    raw = f"{st.st_mtime_ns}:{st.st_size}|{folder}"
    return hashlib.sha256(raw.encode("utf-8", "surrogateescape")).hexdigest()[:24]


def cover_signatures(paths: Iterable[Path | str]) -> dict[str, str | None]:
    """:func:`cover_signature` for many files, each folder listed once."""
    cache: dict[str, str] = {}
    return {str(p): cover_signature(p, cache) for p in paths}


async def bump(session: Any, track_ids: Iterable[int],
               sigs: dict[int, str | None] | None = None) -> int:
    """Move ``cover_rev`` for ``track_ids`` (and record ``cover_sig`` where
    given) and fire the ``library_cover_changed`` NOTIFY on the caller's
    session, so it lands with the caller's commit. Returns the rows moved."""
    ids = sorted({int(t) for t in track_ids})
    if not ids:
        return 0
    sigs = sigs or {}
    res = await session.execute(
        text(
            """
            UPDATE library_tracks t
            SET cover_rev = t.cover_rev + 1,
                cover_changed_at = now(),
                cover_sig = COALESCE(v.sig, t.cover_sig)
            FROM unnest(CAST(:ids AS int[]), CAST(:sigs AS text[])) AS v(id, sig)
            WHERE t.id = v.id
            """
        ),
        {"ids": ids, "sigs": [sigs.get(i) for i in ids]},
    )
    moved = int(res.rowcount or 0)
    if moved:
        # The web process's LISTEN task re-reads the channel's snapshot; the
        # payload is only for a log line (NOTIFY payloads cap at 8000 bytes).
        payload = ",".join(str(i) for i in ids)
        await session.execute(text("SELECT pg_notify(:ch, :p)"),
                              {"ch": NOTIFY_CHANNEL, "p": payload[:7000]})
    return moved


async def record_signatures(session: Any, sigs: dict[int, str | None]) -> None:
    """Store first-time signatures without moving ``cover_rev``."""
    pairs = [(i, s) for i, s in sigs.items() if s is not None]
    if not pairs:
        return
    await session.execute(
        text(
            """
            UPDATE library_tracks t SET cover_sig = v.sig
            FROM unnest(CAST(:ids AS int[]), CAST(:sigs AS text[])) AS v(id, sig)
            WHERE t.id = v.id
            """
        ),
        {"ids": [p[0] for p in pairs], "sigs": [p[1] for p in pairs]},
    )


def cover_url(track_id: int, cover_v: int | None) -> str:
    """The cover route for a track, versioned so caches bust on a change."""
    return f"/api/music/library/{int(track_id)}/cover?v={int(cover_v or 0)}"


__all__ = [
    "NOTIFY_CHANNEL", "bump", "cover_signature", "cover_signatures",
    "cover_url", "record_signatures",
]
