"""Podcast episodes that moved into the Windows Podcasts folder (2026-11-11).

The first Windows installs kept downloaded episodes inside Domovoi's Linux
system, in the core's default ``podcasts_dir`` (``~/.domovoi/podcasts``).
Podcasts is now one of the media folders: provision.sh copies the episodes
into ``/srv/media/podcasts`` (the Windows folder, mounted), checks the copy
and only then points ``PODCASTS_DIR`` there (scripts/linux/provision.sh,
``move_podcasts``). Two things are left for the core, at its first start on
the new folder:

* ``podcast_episodes.file_path`` holds each episode's ABSOLUTE path, under
  the old folder. The web serves an episode only from inside
  ``podcasts_dir`` (web/backend/api/podcasts.py) and the rooms play it by
  its path relative to it (spoken_audio.mpd_uri_for), so every row is
  pointed at its copy: the same path below the folder. An episode the
  core finished downloading into the old folder after provision.sh's copy
  (the core keeps running while provision.sh works) is copied over here,
  first. Only rows under the old default folder are touched, and only a
  file from there is ever copied: a row naming any other path is left as
  it is (a hand-edited row must not become a way to copy an arbitrary file
  into a served folder).
* The old copies. Each old file is removed only when the new folder holds
  a file at the same place below it with the same bytes (compared in
  full), and no row points at the old one any more. Anything else stays,
  and is logged once. Removing them is why this exists at all: the old
  copies sit in the distro's virtual disk, which never shrinks.

It runs only where the move happened: ``podcasts_dir`` is under the media
mount base (``/srv/media``) and the old default folder is another folder.
A Linux server, whose ``PODCASTS_DIR`` is whatever the household set, is
never touched.
"""

from __future__ import annotations

import filecmp
import logging
import os
import shutil
from pathlib import Path, PurePosixPath

from sqlalchemy import text

log = logging.getLogger(__name__)

#: The core's own default (config.Settings.podcasts_dir), where the first
#: Windows installs kept the episodes.
OLD_DEFAULT = "~/.domovoi/podcasts"
#: The Windows install's media mounts (media_folders.MOUNT_BASE).
MOUNT_BASE = "/srv/media"
_is_mount = os.path.ismount


def _dirs(podcasts_dir: str, old_dir: str | None = None) -> tuple[Path, Path] | None:
    """``(old, new)`` when the episodes moved from the default folder to a
    mounted media folder; None otherwise (nothing to do)."""
    new = Path(os.path.expanduser(podcasts_dir))
    old = Path(os.path.expanduser(old_dir or OLD_DEFAULT))
    try:
        PurePosixPath(new.as_posix()).relative_to(MOUNT_BASE)
    except ValueError:
        return None
    if not _is_mount(new.as_posix()):
        # The Windows folder isn't connected: the empty mount point is not
        # where the episodes are.
        return None
    if os.path.realpath(old) == os.path.realpath(new) or not old.is_dir():
        return None
    return old, new


def _inside(path: Path, base: Path) -> Path | None:
    """``path`` below ``base`` (both resolved), or None."""
    try:
        return Path(os.path.realpath(path)).relative_to(os.path.realpath(base))
    except ValueError:
        return None


def _copy_in(src: Path, dst: Path) -> bool:
    """One file into the new folder through a temporary name; True when
    it is there at its size."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".domovoi-copy")
    try:
        shutil.copy2(src, tmp)
        if tmp.stat().st_size != src.stat().st_size:
            raise OSError("the copy is not the same size")
        os.replace(tmp, dst)
        return True
    except OSError as e:
        log.warning("podcasts: could not copy %s into %s: %s", src, dst, e)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _point_rows_at_copies(rows: list[tuple[int, str]], old: Path, new: Path) -> list[tuple[int, str, str]]:
    """The rows to rewrite as ``(id, old path, new path)``; copies an
    episode that is only in the old folder. Runs in a thread."""
    out = []
    for ep_id, fp in rows:
        rel = _inside(Path(fp), old)
        if rel is None or not rel.parts or ".." in rel.parts:
            continue
        src = Path(os.path.realpath(old)) / rel
        dst = new / rel
        try:
            if not dst.is_file():
                if not src.is_file() or not _copy_in(src, dst):
                    continue
            elif src.is_file() and (dst.is_symlink() or not filecmp.cmp(src, dst, shallow=False)):
                # Not this episode's copy (a file of the household's at
                # that name): the row stays on the old file, which stays too.
                log.warning("podcasts: %s is not the copy of %s; that episode stays where it is", dst, src)
                continue
        except OSError as e:
            log.warning("podcasts: could not check %s against %s: %s", dst, src, e)
            continue
        out.append((ep_id, fp, str(dst)))
    return out


def _remove_matching_old_files(old: Path, new: Path, still_used: set[str]) -> tuple[int, int]:
    """Old files with a byte-identical copy in the new folder removed, then
    the folders left empty; ``(removed, kept)``. Runs in a thread."""
    removed = kept = 0
    used = {os.path.realpath(p) for p in still_used}
    for dirpath, _dirs_, files in os.walk(old, topdown=False, followlinks=False):
        for name in files:
            src = Path(dirpath) / name
            rel = src.relative_to(old)
            dst = new / rel
            try:
                same = (
                    src.is_file() and not src.is_symlink() and dst.is_file() and not dst.is_symlink()
                    # A copy that is the old file itself (a link back) is no copy.
                    and os.path.realpath(dst) != os.path.realpath(src)
                    and os.path.realpath(src) not in used
                    and filecmp.cmp(src, dst, shallow=False)
                )
            except OSError:
                same = False
            if not same:
                kept += 1
                continue
            try:
                src.unlink()
                removed += 1
            except OSError as e:
                kept += 1
                log.warning("podcasts: could not remove the old copy %s: %s", src, e)
        if Path(dirpath) != old:
            try:
                os.rmdir(dirpath)  # only when empty
            except OSError:
                pass
    return removed, kept


async def relocate_episodes(podcasts_dir: str | None = None, *, old_dir: str | None = None) -> dict[str, int] | None:
    """Rows pointed at the moved episodes, and the old copies removed.
    None when there is nothing to do here; never raises."""
    import asyncio

    from domovoi.config import settings
    from domovoi.db.session import session_scope

    try:
        pair = _dirs(podcasts_dir or settings.podcasts_dir, old_dir)
        if pair is None:
            return None
        old, new = pair
        async with session_scope() as s:
            rows = [
                (int(r[0]), str(r[1]))
                for r in (await s.execute(text(
                    "SELECT id, file_path FROM podcast_episodes WHERE file_path IS NOT NULL"
                ))).all()
            ]
            moves = await asyncio.to_thread(_point_rows_at_copies, rows, old, new)
            for ep_id, was, now in moves:
                # Only while the row still names the old file: a row the
                # poller changed meanwhile is the poller's.
                await s.execute(
                    text("UPDATE podcast_episodes SET file_path = :now WHERE id = :id AND file_path = :was"),
                    {"now": now, "id": ep_id, "was": was},
                )
        async with session_scope() as s:
            still = {
                str(r[0])
                for r in (await s.execute(text(
                    "SELECT file_path FROM podcast_episodes WHERE file_path IS NOT NULL"
                ))).all()
                if _inside(Path(str(r[0])), old) is not None
            }
        removed, kept = await asyncio.to_thread(_remove_matching_old_files, old, new, still)
        if moves or removed or kept:
            log.info(
                "podcasts: %d episode(s) now play from %s; %d old copy(ies) removed from %s, %d kept "
                "(no identical copy in the new folder, or still in use)",
                len(moves), new, removed, old, kept,
            )
        return {"rows": len(moves), "removed": removed, "kept": kept}
    except Exception as e:  # noqa: BLE001 — a start never fails on this
        log.warning("podcasts: moving the episode records to the new folder failed: %s", e)
        return None
