"""Crash-safe writes to the card's boot partition.

The boot partition (``/boot/firmware`` on Pi OS since bookworm, ``/boot``
before it) is FAT: no journal, and a satellite is usually switched off by
pulling its plug. A file rewritten in place there and caught by a power cut
is not merely stale, it can be unreadable: on 2026-10-23 a card pulled after
a failed Wi-Fi join held a ``domovoi/device-info.json`` Windows called
"corrupted and unreadable".

Every write the satellite makes to the boot partition goes through
:func:`write_bytes` (the repo guard test in
``satellite/tests/test_boot_write.py`` holds every module and script to
that). It:

1. does nothing when the file already holds exactly these bytes, so a
   caller that reports the same thing twice costs the card nothing;
2. writes a temporary file beside the target, flushes and ``fsync``s it;
3. ``os.replace``s it onto the target, so a reader (or a power cut) sees
   the old file or the new one, never half of either;
4. ``fsync``s the directory and then calls ``os.sync()``. On vfat a
   directory fsync is close to a no-op (the rename lives in the directory
   entry and the FAT, which the kernel writes back on its own schedule), so
   the sync is what actually puts the rename on the card before anyone can
   pull it. ``os.sync`` rather than ``syncfs`` because the standard library
   has no ``syncfs``; on a satellite the only other dirty data is the root
   filesystem's, which is cheap to flush and is written this rarely.

A crash between 2 and 3 leaves the old file intact and a stray ``.tmp``
beside it, which the next write simply overwrites.

The mount itself is left as Pi OS ships it (``vfat defaults``): see
"The boot partition is FAT" in ``satellite/PROVISIONING.md`` for why
neither ``sync`` nor ``flush`` is added.

Stdlib-only and importable on its own, like everything provisioning mode
imports.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

TMP_SUFFIX = ".tmp"


def _sync_dir(folder: Path) -> None:
    try:
        fd = os.open(str(folder), os.O_RDONLY)
    except OSError:
        return          # Windows, or a directory we may not open: the sync below
    try:
        os.fsync(fd)
    except OSError:
        pass            # vfat may refuse; the sync below is what counts
    finally:
        os.close(fd)


def _sync_all() -> None:
    sync = getattr(os, "sync", None)
    if sync is not None:
        sync()


def write_bytes(path: Path | str, data: bytes) -> bool:
    """Replace ``path`` with ``data``, crash-safely. True when the file was
    written, False when it already held exactly ``data``. Raises OSError
    (the old file left intact) when the card can't be written."""
    path = Path(path)
    try:
        if path.is_file() and path.read_bytes() == data:
            return False
    except OSError:
        pass            # unreadable: replace it
    tmp = path.with_name(path.name + TMP_SUFFIX)
    try:
        with open(tmp, "wb") as fh:          # binary: no CRLF, whatever the OS
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _sync_dir(path.parent)
    _sync_all()
    return True


def write_text(path: Path | str, text: str) -> bool:
    """:func:`write_bytes` for text: UTF-8, LF line endings as given."""
    return write_bytes(path, text.encode("utf-8"))


def write_json(path: Path | str, doc: Any) -> bool:
    """:func:`write_bytes` for a JSON document, indented and readable as
    typed (non-ASCII is not escaped)."""
    return write_text(path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path | str) -> dict[str, Any] | None:
    """A JSON object from the card, or None when the file is missing,
    unreadable, truncated, not JSON, or not an object. Never raises: a
    damaged card file must never stop the device."""
    try:
        doc = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None
