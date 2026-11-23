"""The media catalogue: which image and video files the house has, kept in
``media_items`` (V078) by a cheap walk.

* **stat() only.** The walk lists folders and reads each file's size and
  modified time; it never opens a file. (A renamed file's fingerprint is
  the one read, and it goes through the 20 MB/s bucket.)
* **New, changed, gone.** A file is new or changed when its size or
  modified time differ; a change re-queues it and drops the old results.
  A file gone from a library that is **present** is deleted with all its
  results (``ON DELETE CASCADE``). A library whose root is not there (a
  drive unplugged) keeps its rows: it may come back.
* **Moved or renamed.** A new path whose size matches a vanished row with a
  fingerprint is fingerprinted; a match moves the row, results and all.
* **Excluded folders** (``media_excluded``): never catalogued, and their
  rows (with every result) are deleted.
* **New arrivals.** Files found after a library's first complete walk are
  ``new_arrival`` (the daytime trickle takes only those); the first walk's
  files are the backlog, done at night.

Slice 1 walks the core Pictures library (images and videos) and the core
Videos library (videos). Core-internal.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text

from domovoi.media_understanding import ratelimit

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp",
                              ".tif", ".tiff", ".avif"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mpg", ".mpeg",
                              ".3gp", ".wmv", ".ts", ".mts", ".m2ts"})
#: One walk lists at most this many files per library (a runaway tree
#: can't hold the catalogue up forever; the rest waits for the next walk).
MAX_FILES = 500_000
#: How often a full walk is due without a "Rescan".
WALK_EVERY_S = 3600.0


@dataclass(frozen=True)
class Library:
    id: str
    root: Path
    kinds: frozenset[str]

    def kind_of(self, name: str) -> str | None:
        ext = os.path.splitext(name)[1].lower()
        if "image" in self.kinds and ext in IMAGE_EXTENSIONS:
            return "image"
        if "video" in self.kinds and ext in VIDEO_EXTENSIONS:
            return "video"
        return None


def core_libraries(settings: Any = None) -> list[Library]:
    """Pictures (images and videos) and Videos (videos). A root inside the
    other one is left out, so no file is catalogued twice."""
    if settings is None:
        from domovoi.config import settings as live

        settings = live
    out: list[Library] = []
    for lib_id, attr, kinds in (("core:pictures", "pictures_dir", {"image", "video"}),
                                ("core:videos", "videos_dir", {"video"})):
        raw = getattr(settings, attr, None)
        if not raw:
            continue
        root = Path(str(raw)).expanduser().resolve(strict=False)
        if any(_within(root, o.root) for o in out):
            continue
        out.append(Library(lib_id, root, frozenset(kinds)))
    return out


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def is_present(lib: Library) -> bool:
    try:
        return lib.root.is_dir()
    except OSError:
        return False


def under(rel_path: str, prefix: str) -> bool:
    """Whether ``rel_path`` is in the excluded folder ``prefix`` ('' is the
    whole library)."""
    prefix = prefix.strip("/")
    return not prefix or rel_path == prefix or rel_path.startswith(prefix + "/")


def walk(lib: Library, excluded: Iterable[str] = (), *, limit: int = MAX_FILES) -> dict[str, tuple[int, float, str]]:
    """``{rel_path: (size, mtime, kind)}`` for the library's image/video
    files, by ``os.scandir`` + ``stat`` (no file is opened). Hidden
    entries and symlinks are skipped; excluded folders are not entered."""
    prefixes = [p.strip("/") for p in excluded]
    if "" in prefixes:
        return {}
    found: dict[str, tuple[int, float, str]] = {}
    stack = [(lib.root, "")]
    while stack and len(found) < limit:
        folder, rel_dir = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            if e.name.startswith("."):
                continue
            rel = f"{rel_dir}/{e.name}" if rel_dir else e.name
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    if not any(under(rel, p) for p in prefixes):
                        stack.append((Path(e.path), rel))
                    continue
                kind = lib.kind_of(e.name)
                if kind is None or any(under(rel, p) for p in prefixes):
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            found[rel] = (int(st.st_size), float(st.st_mtime), kind)
            if len(found) >= limit:
                break
    return found


async def excluded_prefixes(s: Any, library_id: str) -> list[str]:
    rows = (await s.execute(text("SELECT prefix FROM media_excluded WHERE library_id = :l"),
                            {"l": library_id})).scalars().all()
    return list(rows)


async def delete_under(s: Any, library_id: str, prefix: str) -> int:
    """Delete every catalogued file (and its results) in ``prefix``."""
    prefix = prefix.strip("/")
    if not prefix:
        r = await s.execute(text("DELETE FROM media_items WHERE library_id = :l"), {"l": library_id})
    else:
        like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
        r = await s.execute(text(
            "DELETE FROM media_items WHERE library_id = :l AND (rel_path = :p OR rel_path LIKE :like)"),
            {"l": library_id, "p": prefix, "like": like})
    return int(r.rowcount or 0)


_RESULT_TABLES = ("media_item_stages", "media_frames", "media_segments", "media_summaries", "media_faces")


async def _forget_results(s: Any, ids: list[int]) -> None:
    for t in _RESULT_TABLES:
        await s.execute(text(f"DELETE FROM {t} WHERE item_id = ANY(:ids)"), {"ids": ids})


async def sync_library(s: Any, lib: Library, *, bucket: ratelimit.TokenBucket | None = None,
                       found: dict[str, tuple[int, float, str]] | None = None) -> dict[str, int]:
    """Bring ``media_items`` in line with one library; the counts of what
    changed. ``found`` is the walk (made here when not given)."""
    stats = {"added": 0, "changed": 0, "deleted": 0, "moved": 0, "excluded": 0, "files": 0}
    excluded = await excluded_prefixes(s, lib.id)
    for p in excluded:
        stats["excluded"] += await delete_under(s, lib.id, p)
    present = is_present(lib)
    if not present:
        await s.execute(text(
            "INSERT INTO media_libraries (library_id, present, updated_at) VALUES (:l, false, now()) "
            "ON CONFLICT (library_id) DO UPDATE SET present = false, updated_at = now()"), {"l": lib.id})
        return stats
    if found is None:
        found = await asyncio.to_thread(walk, lib, excluded)
    stats["files"] = len(found)
    walked_before = (await s.execute(text(
        "SELECT walked_at IS NOT NULL FROM media_libraries WHERE library_id = :l"), {"l": lib.id})).scalar()
    rows = (await s.execute(text(
        "SELECT id, rel_path, size_bytes, mtime, fingerprint FROM media_items WHERE library_id = :l"),
        {"l": lib.id})).mappings().all()
    existing = {r["rel_path"]: r for r in rows}

    gone = {rel: r for rel, r in existing.items() if rel not in found}
    new = {rel: v for rel, v in found.items() if rel not in existing}

    # Moved or renamed: same size, same fingerprint.
    by_size: dict[int, list[Any]] = {}
    for r in gone.values():
        if r["fingerprint"]:
            by_size.setdefault(int(r["size_bytes"]), []).append(r)
    for rel, (size, mtime, _kind) in list(new.items()):
        cands = by_size.get(size)
        if not cands:
            continue
        try:
            fp = await ratelimit.fingerprint(lib.root / rel, bucket=bucket)
        except OSError:
            continue
        match = next((c for c in cands if c["fingerprint"] == fp), None)
        if match is None:
            continue
        cands.remove(match)
        gone.pop(match["rel_path"], None)
        new.pop(rel)
        await s.execute(text(
            "UPDATE media_items SET rel_path = :r, mtime = :m, updated_at = now() WHERE id = :id"),
            {"r": rel, "m": mtime, "id": match["id"]})
        stats["moved"] += 1

    if gone:
        await s.execute(text("DELETE FROM media_items WHERE id = ANY(:ids)"),
                        {"ids": [int(r["id"]) for r in gone.values()]})
        stats["deleted"] = len(gone)

    changed = [(existing[rel]["id"], size, mtime) for rel, (size, mtime, _k) in found.items()
               if rel in existing and (int(existing[rel]["size_bytes"]) != size
                                       or abs(float(existing[rel]["mtime"]) - mtime) > 1e-6)]
    if changed:
        ids = [int(c[0]) for c in changed]
        await _forget_results(s, ids)
        for item_id, size, mtime in changed:
            await s.execute(text(
                "UPDATE media_items SET size_bytes = :sz, mtime = :m, fingerprint = NULL, state = 'pending', "
                "stage = NULL, attempts = 0, error_code = NULL, frame_pass = NULL, analysed_at = NULL, "
                "vision_model = NULL, search_doc = NULL, taken_at = NULL, updated_at = now() WHERE id = :id"),
                {"sz": size, "m": mtime, "id": item_id})
        stats["changed"] = len(changed)

    if new:
        await s.execute(text(
            "INSERT INTO media_items (library_id, rel_path, kind, size_bytes, mtime, new_arrival) "
            "VALUES (:l, :r, :k, :sz, :m, :n) ON CONFLICT (library_id, rel_path) DO NOTHING"),
            [{"l": lib.id, "r": rel, "k": kind, "sz": size, "m": mtime, "n": bool(walked_before)}
             for rel, (size, mtime, kind) in new.items()])
        stats["added"] = len(new)

    # Files the runner found missing (a drive away) and that are back.
    await s.execute(text(
        "UPDATE media_items SET state = 'pending', error_code = NULL, updated_at = now() "
        "WHERE library_id = :l AND state = 'skipped' AND error_code = 'media-missing'"), {"l": lib.id})
    await s.execute(text(
        "INSERT INTO media_libraries (library_id, walked_at, present, files, updated_at) "
        "VALUES (:l, now(), true, :f, now()) "
        "ON CONFLICT (library_id) DO UPDATE SET walked_at = now(), present = true, files = :f, updated_at = now()"),
        {"l": lib.id, "f": len(found)})
    return stats


async def forget_paths(s: Any, library_id: str, rel_paths: Iterable[str]) -> int:
    """Files deleted through Files: their results go at once (a folder's
    whole tree too)."""
    n = 0
    for rel in rel_paths:
        n += await delete_under(s, library_id, rel)
    return n


class Catalog:
    """The walker: a full walk once an hour, sooner after "Rescan"
    (``media_runner_state.rescan_requested``)."""

    def __init__(self, libraries: list[Library] | None = None, *,
                 every_s: float = WALK_EVERY_S, clock: Any = time.monotonic) -> None:
        self._libraries = libraries
        self.every_s = every_s
        self._clock = clock
        self._last: float | None = None

    def libraries(self) -> list[Library]:
        return self._libraries if self._libraries is not None else core_libraries()

    async def due(self, s: Any) -> bool:
        if self._last is None or self._clock() - self._last >= self.every_s:
            return True
        row = (await s.execute(text("SELECT rescan_requested FROM media_runner_state WHERE id = 1"))).first()
        return bool(row and row[0])

    async def tick(self, session_scope: Any, *, force: bool = False) -> dict[str, dict[str, int]] | None:
        async with session_scope() as s:
            if not force and not await self.due(s):
                return None
        out: dict[str, dict[str, int]] = {}
        for lib in self.libraries():
            try:
                async with session_scope() as s:
                    out[lib.id] = await sync_library(s, lib)
            except Exception as e:  # noqa: BLE001 — one library never stops the others
                log.warning("media catalogue: %s failed: %s", lib.id, type(e).__name__)
        async with session_scope() as s:
            await s.execute(text("UPDATE media_runner_state SET rescan_requested = false WHERE id = 1"))
            await s.execute(text("SELECT pg_notify('media_understanding_changed', 'catalog')"))
        self._last = self._clock()
        return out
