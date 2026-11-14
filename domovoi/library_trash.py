"""Domovoi's trash folder for music: where the duplicates finder moves the
copies a household doesn't keep (V058). Nothing is deleted.

The folder is ``<MUSIC_DIR>/.domovoi-trash/`` (:data:`TRASH_DIR_NAME`):
inside the music folder, so a move is a rename on the same drive (instant,
nothing copied), and a person can see and empty it in a file manager or the
Files page. Each move keeps the file's path below the music folder under a
folder named for the moment of the move
(``.domovoi-trash/20261114-153000/Artist/Album/Song.mp3``), so two copies
with one name never collide and Restore knows where it came from.

The library indexer, the playlist import and the tag backfill never look
inside it, and an ``.mpdignore`` there keeps the rooms' music players out.

Restore (:func:`restore_file`) moves the file back where it was, unless
something now has that name (then it is refused, never overwritten).

Pure file work, blocking (callers run it in a worker thread); the library
rows and playlist entries are the caller's (``web/backend/api/library_tools.py``).
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

TRASH_DIR_NAME = ".domovoi-trash"


class TrashError(Exception):
    """A move or restore that can't be done; the message is for people."""


def music_root(music_dir: str | os.PathLike[str]) -> Path:
    return Path(music_dir).expanduser().resolve(strict=False)


def trash_root(music_dir: str | os.PathLike[str]) -> Path:
    return music_root(music_dir) / TRASH_DIR_NAME


def in_trash(path: str | os.PathLike[str]) -> bool:
    return TRASH_DIR_NAME in Path(path).parts


def _ensure_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    ignore = root / ".mpdignore"
    if not ignore.exists():
        try:
            ignore.write_text("*\n", encoding="utf-8")
        except OSError:
            pass


def move_to_trash(path: str | os.PathLike[str], music_dir: str | os.PathLike[str],
                  *, stamp: str | None = None) -> Path:
    """Move ``path`` (a file inside the music folder) into the trash;
    returns where it is now. Raises :class:`TrashError` for a file outside
    the music folder, already in the trash, or that can't be moved."""
    root = music_root(music_dir)
    src = Path(path).expanduser().resolve(strict=False)
    try:
        rel = src.relative_to(root)
    except ValueError:
        raise TrashError("the file isn't inside the music folder") from None
    if in_trash(rel):
        raise TrashError("the file is already in the trash")
    if not src.is_file():
        raise TrashError("the file isn't there any more")
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    troot = root / TRASH_DIR_NAME
    _ensure_root(troot)
    dest = troot / stamp / rel
    n = 1
    while dest.exists():
        dest = troot / f"{stamp}-{n}" / rel
        n += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dest) if _same_device(src, dest.parent) else shutil.move(str(src), str(dest))
    except OSError as e:
        raise TrashError(f"couldn't move it: {e.strerror or e}") from e
    return dest


def _same_device(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return False


def restore_file(trash_path: str | os.PathLike[str], original_path: str | os.PathLike[str],
                 music_dir: str | os.PathLike[str]) -> Path:
    """Move a trashed file back to ``original_path``. Refused (TrashError)
    when it isn't in the trash any more, when the original place is taken,
    or when either path is outside the music folder."""
    root = music_root(music_dir)
    src = Path(trash_path).resolve(strict=False)
    dest = Path(original_path).expanduser().resolve(strict=False)
    try:
        src.relative_to(root / TRASH_DIR_NAME)
        dest.relative_to(root)
    except ValueError:
        raise TrashError("that isn't a place in the music folder") from None
    if in_trash(dest.relative_to(root)):
        raise TrashError("that isn't a place in the music folder")
    if not src.is_file():
        raise TrashError("the file isn't in the trash any more")
    if dest.exists():
        raise TrashError("something else now has that name; move it first")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dest) if _same_device(src, dest.parent) else shutil.move(str(src), str(dest))
    except OSError as e:
        raise TrashError(f"couldn't move it back: {e.strerror or e}") from e
    # Tidy empty folders the move left in the trash.
    parent = src.parent
    troot = root / TRASH_DIR_NAME
    while parent != troot and troot in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    return dest


__all__ = ["TRASH_DIR_NAME", "TrashError", "in_trash", "move_to_trash", "restore_file", "trash_root"]
