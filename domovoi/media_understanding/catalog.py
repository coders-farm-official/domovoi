"""The media catalogue: which image and video files the house has, kept in
``media_items`` (V078) by a cheap walk.

* **stat() only.** The walk lists folders and reads each file's size and
  modified time; it never opens a file. (A renamed file's fingerprint is
  the one read, and it goes through the 20 MB/s bucket.)
* **New, changed, gone.** A file is new or changed when its size or
  modified time differ; a change re-queues it and drops the old results
  (unless only the modified time moved and the fingerprint is the same:
  then the results, confirmed faces included, stay).
* **Missing is not deleted** (security review 2026-11-23, M10). A file
  not seen is first marked missing (``missing_since``, ``missed_walks``,
  V079) and its row, results, face confirmations and names stay. It is
  deleted only after :data:`MISSING_GRACE_WALKS` complete walks and
  :data:`MISSING_GRACE_S` (:data:`MASS_LOSS_GRACE_S` when one walk lost more
  than :data:`MASS_LOSS_SHARE` of the library). Nothing is even counted
  as missing when the walk was not complete: a folder that could not be
  listed, or the :data:`MAX_FILES` cap reached. A library whose root is
  not there, or cannot be listed, or walks EMPTY while it has rows (an
  empty mount point: a USB drive or a NAS not up yet) is marked not
  present and keeps every row: it may come back.
* **Moved or renamed.** A new path whose size matches a vanished or
  missing row with a fingerprint (in this library or, for a missing row,
  any other) is fingerprinted; a match moves the row, results and all.
* **Excluded folders** (``media_excluded``): never catalogued, and their
  rows (with every result) are deleted.
* **New arrivals.** Files found after a library's first complete walk are
  ``new_arrival`` (the daytime trickle takes only those); the first walk's
  files are the backlog, done at night.

**Which libraries** (:func:`libraries`, design §2: photos are "any image
in any library the household leaves in", videos "each file the Videos tab
lists"): the core Pictures library (images and videos) and Videos library
(videos) first, then every other Files library
(``web/backend/api/files_security.py``): the other core folders (Music,
Audiobooks, Podcasts, Documents: their videos only, since album covers and
scanned pages aren't photos), enabled plugins' media libraries and present
removable drives (images and videos). A root inside one already listed,
or holding one, is left out, so no file is catalogued twice. Any of them,
or any folder in one, can be excluded (``media_excluded``).

Core-internal; the web's settings routes read :func:`libraries` too.
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
#: A file not seen is deleted only after this many complete walks...
MISSING_GRACE_WALKS = 3
#: ...and this long since it was first missed.
MISSING_GRACE_S = 24 * 3600.0
#: When one walk loses more than this share of a library's rows (and at
#: least :data:`MASS_LOSS_MIN` of them), the grace is :data:`MASS_LOSS_GRACE_S`.
MASS_LOSS_SHARE = 0.25
MASS_LOSS_MIN = 20
MASS_LOSS_GRACE_S = 14 * 24 * 3600.0


@dataclass(frozen=True)
class Library:
    id: str
    root: Path
    kinds: frozenset[str]
    label: str = ""

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
    for lib_id, attr, kinds, label in (("core:pictures", "pictures_dir", {"image", "video"}, "Pictures"),
                                       ("core:videos", "videos_dir", {"video"}, "Videos")):
        raw = getattr(settings, attr, None)
        if not raw:
            continue
        root = Path(str(raw)).expanduser().resolve(strict=False)
        if any(_within(root, o.root) for o in out):
            continue
        out.append(Library(lib_id, root, frozenset(kinds), label))
    return out


#: Core Files libraries whose images are not photos (album covers, scanned
#: pages): only their videos are catalogued.
VIDEO_ONLY_CORE = frozenset({"core:music", "core:audiobooks", "core:podcasts", "core:documents"})
#: Files views that are not folders of their own.
_NOT_FOLDERS = frozenset({"core:other"})
#: How long the list of the other Files libraries is kept (it reads the
#: plugins table and looks for removable drives).
OTHER_LIBRARIES_TTL_S = 60.0
_other_cache: tuple[float, list[Library]] | None = None


async def _plugin_rows(s: Any) -> list[dict[str, Any]]:
    import json

    if s is None:
        from domovoi.db.session import session_scope

        async with session_scope() as own:
            return await _plugin_rows(own)
    try:
        rows = (await s.execute(text(
            "SELECT slug, name, enabled, status, install_dir, manifest FROM plugins ORDER BY slug"))).mappings().all()
    except Exception as e:  # noqa: BLE001 — no plugins table: no plugin libraries
        log.info("media catalogue: plugins unreadable: %s", type(e).__name__)
        await s.rollback()
        return []
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("manifest"), str):
            try:
                d["manifest"] = json.loads(d["manifest"])
            except ValueError:
                d["manifest"] = {}
        out.append(d)
    return out


async def other_libraries(s: Any = None) -> list[Library]:
    """The Files libraries besides core Pictures and Videos (see the module
    docstring), as the Files tab lists them. Empty when they can't be
    listed (the core's own two still are)."""
    try:
        from web.backend.api import files_security as fs

        allowed = fs._allowed_under_config()
        found = fs.build_core_libraries(allowed)
        found += fs.build_plugin_libraries(await _plugin_rows(s), allowed)
        found += await asyncio.to_thread(fs.build_removable_libraries)
    except Exception as e:  # noqa: BLE001 — Pictures and Videos still walk
        log.warning("media catalogue: the other libraries could not be listed: %s", type(e).__name__)
        return []
    out = []
    for m in found:
        if m.id in ("core:pictures", "core:videos") or m.id in _NOT_FOLDERS:
            continue
        kinds = {"video"} if m.id in VIDEO_ONLY_CORE else {"image", "video"}
        out.append(Library(m.id, Path(m.root_path), frozenset(kinds), m.label))
    return out


async def libraries(s: Any = None, *, fresh: bool = False) -> list[Library]:
    """Every library the catalogue walks: :func:`core_libraries`, then
    :func:`other_libraries` (kept for :data:`OTHER_LIBRARIES_TTL_S`). A root
    inside one already listed, or holding one, is left out."""
    global _other_cache
    out = list(core_libraries())
    now = time.monotonic()
    if fresh or _other_cache is None or now - _other_cache[0] >= OTHER_LIBRARIES_TTL_S:
        _other_cache = (now, await other_libraries(s))
    for lib in _other_cache[1]:
        root = lib.root.expanduser().resolve(strict=False)
        if any(o.id == lib.id or _within(root, o.root) or _within(o.root, root) for o in out):
            continue
        out.append(Library(lib.id, root, lib.kinds, lib.label))
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


@dataclass
class Walk:
    """One walk's result. ``complete`` is False when a folder could not be
    listed or the cap stopped it: then what wasn't found may still be
    there, and nothing is counted as missing. ``root_ok`` is False when
    the library's own root could not be listed."""

    found: dict[str, tuple[int, float, str]]
    complete: bool = True
    root_ok: bool = True
    unreadable: int = 0


def walk(lib: Library, excluded: Iterable[str] = (), *, limit: int = MAX_FILES) -> dict[str, tuple[int, float, str]]:
    """``{rel_path: (size, mtime, kind)}`` for the library's image/video
    files (:func:`walk_full`'s ``found``)."""
    return walk_full(lib, excluded, limit=limit).found


def walk_full(lib: Library, excluded: Iterable[str] = (), *, limit: int = MAX_FILES) -> Walk:
    """The library's image/video files, by ``os.scandir`` + ``stat`` (no
    file is opened), and whether the walk saw everything. Hidden entries,
    symlinks and anything that is not a regular file are skipped; excluded
    folders are not entered."""
    import stat as stat_mod

    prefixes = [p.strip("/") for p in excluded]
    if "" in prefixes:
        return Walk({})
    found: dict[str, tuple[int, float, str]] = {}
    out = Walk(found)
    stack = [(lib.root, "")]
    while stack:
        if len(found) >= limit:
            out.complete = False
            break
        folder, rel_dir = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            out.complete = False
            out.unreadable += 1
            if not rel_dir:
                out.root_ok = False
            continue
        for e in entries:
            if e.name.startswith("."):
                continue
            try:
                e.name.encode("utf-8")
            except UnicodeEncodeError:
                continue                          # a name that isn't UTF-8 can't be stored: skipped
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
                if not stat_mod.S_ISREG(st.st_mode):
                    continue                      # a pipe or a device named .jpg: never read
            except OSError:
                out.complete = False
                continue
            found[rel] = (int(st.st_size), float(st.st_mtime), kind)
            if len(found) >= limit:
                break
    if stack and len(found) >= limit:
        out.complete = False
    return out


async def excluded_prefixes(s: Any, library_id: str) -> list[str]:
    rows = (await s.execute(text("SELECT prefix FROM media_excluded WHERE library_id = :l"),
                            {"l": library_id})).scalars().all()
    return list(rows)


def _where_under(library_id: str, prefix: str) -> tuple[str, dict[str, Any]]:
    """The ``media_items`` rows in folder ``prefix`` ('' is the whole library)."""
    prefix = prefix.strip("/")
    if not prefix:
        return "library_id = :l", {"l": library_id}
    like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
    return ("library_id = :l AND (rel_path = :p OR rel_path LIKE :like)",
            {"l": library_id, "p": prefix, "like": like})


async def count_under(s: Any, library_id: str, prefix: str) -> int:
    """How many catalogued files are in ``prefix`` (what excluding it deletes)."""
    where, params = _where_under(library_id, prefix)
    return int((await s.execute(text(f"SELECT count(*) FROM media_items WHERE {where}"), params)).scalar() or 0)


async def delete_under(s: Any, library_id: str, prefix: str) -> int:
    """Delete every catalogued file in ``prefix`` with all its results: the
    captions, tags and text, transcripts and summaries, the place and the
    search words (on the row), and its faces, whose groups are worked out
    again from the faces left (a group left with none goes)."""
    where, params = _where_under(library_id, prefix)
    gids = (await s.execute(text(
        "SELECT DISTINCT group_id FROM media_faces WHERE group_id IS NOT NULL "
        f"AND item_id IN (SELECT id FROM media_items WHERE {where})"), params)).scalars().all()
    r = await s.execute(text(f"DELETE FROM media_items WHERE {where}"), params)
    if gids:
        from domovoi.media_understanding import faces

        await faces.refresh(s, gids)
    return int(r.rowcount or 0)


_RESULT_TABLES = ("media_item_stages", "media_frames", "media_segments", "media_summaries", "media_faces")


async def _forget_results(s: Any, ids: list[int]) -> None:
    for t in _RESULT_TABLES:
        await s.execute(text(f"DELETE FROM {t} WHERE item_id = ANY(:ids)"), {"ids": ids})


async def _mark_absent(s: Any, lib: Library) -> None:
    await s.execute(text(
        "INSERT INTO media_libraries (library_id, present, updated_at) VALUES (:l, false, now()) "
        "ON CONFLICT (library_id) DO UPDATE SET present = false, updated_at = now()"), {"l": lib.id})


async def sync_library(s: Any, lib: Library, *, bucket: ratelimit.TokenBucket | None = None,
                       found: dict[str, tuple[int, float, str]] | Walk | None = None) -> dict[str, int]:
    """Bring ``media_items`` in line with one library; the counts of what
    changed. ``found`` is the walk (made here when not given; a plain dict
    counts as a complete walk). A file not seen is only marked missing; it
    is deleted after the grace (module docstring, "Missing is not
    deleted")."""
    stats = {"added": 0, "changed": 0, "deleted": 0, "moved": 0, "excluded": 0, "files": 0,
             "missing": 0, "held": 0}
    excluded = await excluded_prefixes(s, lib.id)
    for p in excluded:
        stats["excluded"] += await delete_under(s, lib.id, p)
    present = is_present(lib)
    if not present:
        await _mark_absent(s, lib)
        return stats
    if found is None:
        w = await asyncio.to_thread(walk_full, lib, excluded)
    elif isinstance(found, Walk):
        w = found
    else:
        w = Walk(found)
    found = w.found
    if not w.root_ok:
        await _mark_absent(s, lib)
        return stats
    stats["files"] = len(found)
    walked_before = (await s.execute(text(
        "SELECT walked_at IS NOT NULL FROM media_libraries WHERE library_id = :l"), {"l": lib.id})).scalar()
    rows = (await s.execute(text(
        "SELECT id, rel_path, size_bytes, mtime, fingerprint, missing_since FROM media_items "
        "WHERE library_id = :l"), {"l": lib.id})).mappings().all()
    existing = {r["rel_path"]: r for r in rows}
    if existing and not found:
        # An empty mount point looks exactly like this: keep every row.
        log.warning("media catalogue: %s walked empty but has %d files catalogued; "
                    "treated as not mounted, nothing deleted", lib.id, len(existing))
        await _mark_absent(s, lib)
        stats["held"] = len(existing)
        return stats

    gone = {rel: r for rel, r in existing.items() if rel not in found}
    new = {rel: v for rel, v in found.items() if rel not in existing}

    # Moved or renamed: same size, same fingerprint; a row of this library
    # not seen now, or a row of any library already marked missing.
    by_size: dict[int, list[Any]] = {}
    for r in gone.values():
        if r["fingerprint"]:
            by_size.setdefault(int(r["size_bytes"]), []).append(dict(r, library_id=lib.id))
    if new:
        sizes = sorted({v[0] for v in new.values()})
        elsewhere = (await s.execute(text(
            "SELECT id, library_id, rel_path, size_bytes, fingerprint FROM media_items "
            "WHERE library_id <> :l AND missing_since IS NOT NULL AND fingerprint IS NOT NULL "
            "AND size_bytes = ANY(:sz)"), {"l": lib.id, "sz": sizes})).mappings().all()
        for r in elsewhere:
            by_size.setdefault(int(r["size_bytes"]), []).append(dict(r))
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
        if match["library_id"] == lib.id:
            gone.pop(match["rel_path"], None)
        new.pop(rel)
        await s.execute(text(
            "UPDATE media_items SET library_id = :l, rel_path = :r, mtime = :m, missing_since = NULL, "
            "missed_walks = 0, updated_at = now() WHERE id = :id"),
            {"l": lib.id, "r": rel, "m": mtime, "id": match["id"]})
        stats["moved"] += 1

    # Seen again: no longer missing.
    back = [int(r["id"]) for rel, r in existing.items() if rel in found and r["missing_since"] is not None]
    if back:
        await s.execute(text(
            "UPDATE media_items SET missing_since = NULL, missed_walks = 0 WHERE id = ANY(:ids)"), {"ids": back})

    if gone and not w.complete:
        log.warning("media catalogue: %s walk incomplete (%d unreadable folder(s)%s); "
                    "%d unseen file(s) kept, not counted as missing", lib.id, w.unreadable,
                    ", file cap reached" if len(found) >= MAX_FILES else "", len(gone))
        stats["held"] = len(gone)
    elif gone:
        mass = len(gone) >= MASS_LOSS_MIN and len(gone) > MASS_LOSS_SHARE * len(existing)
        grace = MASS_LOSS_GRACE_S if mass else MISSING_GRACE_S
        if mass:
            log.warning("media catalogue: %s lost %d of %d files in one walk; held for %d days "
                        "before anything is deleted", lib.id, len(gone), len(existing), int(grace // 86400))
        ids = [int(r["id"]) for r in gone.values()]
        await s.execute(text(
            "UPDATE media_items SET missing_since = COALESCE(missing_since, now()), "
            "missed_walks = missed_walks + 1 WHERE id = ANY(:ids)"), {"ids": ids})
        stats["missing"] = len(ids)
        r = await s.execute(text(
            "DELETE FROM media_items WHERE id = ANY(:ids) AND missed_walks >= :w "
            "AND missing_since <= now() - make_interval(secs => :g)"),
            {"ids": ids, "w": MISSING_GRACE_WALKS, "g": float(grace)})
        stats["deleted"] = int(r.rowcount or 0)

    changed = [(existing[rel]["id"], size, mtime) for rel, (size, mtime, _k) in found.items()
               if rel in existing and (int(existing[rel]["size_bytes"]) != size
                                       or abs(float(existing[rel]["mtime"]) - mtime) > 1e-6)]
    # Only the modified time moved and the content's fingerprint is the
    # same (a copy-back, a tag tool touching the date): keep the results.
    same = []
    by_id = {r["id"]: r for r in existing.values()}
    for c in list(changed):
        row = by_id[c[0]]
        if not row["fingerprint"] or int(row["size_bytes"]) != c[1]:
            continue
        try:
            fp = await ratelimit.fingerprint(lib.root / row["rel_path"], bucket=bucket)
        except OSError:
            continue
        if fp == row["fingerprint"]:
            changed.remove(c)
            same.append(c)
    for item_id, _size, mtime in same:
        await s.execute(text("UPDATE media_items SET mtime = :m, updated_at = now() WHERE id = :id"),
                        {"m": mtime, "id": item_id})
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

    async def libraries(self, s: Any = None) -> list[Library]:
        return self._libraries if self._libraries is not None else await libraries(s, fresh=True)

    async def due(self, s: Any) -> bool:
        if self._last is None or self._clock() - self._last >= self.every_s:
            return True
        row = (await s.execute(text("SELECT rescan_requested FROM media_runner_state WHERE id = 1"))).first()
        return bool(row and row[0])

    async def tick(self, session_scope: Any, *, force: bool = False) -> dict[str, dict[str, int]] | None:
        async with session_scope() as s:
            if not force and not await self.due(s):
                return None
            libs = await self.libraries(s)
        out: dict[str, dict[str, int]] = {}
        for lib in libs:
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
