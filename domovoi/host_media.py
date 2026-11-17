"""Media folders the host changed: the host side's media watcher tells the core.

A server inside a VM sees its media folders through a share from the host:
virtiofs on a Mac (mac-installer PLAN 4.6, risk R9), drvfs on Windows. Files
the household drops in from the host's own file manager raise no inotify
event inside the VM, so nothing notices them: each room's MPD keeps its old
database (``auto_update`` is inotify) and ``library_tracks`` gains the file
only at the next boot sweep or "rescan my library". The host side watches
the folders itself (FSEvents on a Mac) and calls
``POST /v1/host/media-changed`` with the paths that changed, as this VM
names them (``/srv/media/music/Artist/Album/01.mp3``). This module turns
those paths into the same bounded work the core already does after an
upload:

* music: the audio files that exist are indexed
  (``library_indexer.index_paths``), and every room's MPD reads the folders
  they are in (``mpd_library_sync.update_rooms_for``), which also drops the
  files that went away;
* audiobooks (when the audiobook indexer is on): the audiobook sweep, which
  prunes removed books too, and the rooms' MPDs read those folders;
* podcasts: the rooms' MPDs read those folders;
* pictures, videos, documents: nothing. Those pages read the folder when
  they are opened.

A path that IS a library folder means "the whole folder" (the watcher says
that when FSEvents tells it events were dropped): a full sweep of that
library, at most once per :data:`FULL_SWEEP_MIN_INTERVAL_SEC`. Requests are
merged: one background run at a time, and paths that arrive while it runs
go into the next one.

The route answers a loopback peer only, with no forwarding header
(:func:`domovoi.local_peer.from_this_pc`, the rule the Windows tray's routes
share): the host's helper reaches it through the VM's loopback forward.
That is the shared-host boundary of docs/WINDOWS_HOST.md: any account on
the host can ask for this, and what it asks for is strictly less than the
"rescan my library" voice command a paired device can send. Paths outside
every library folder, ``..`` and links that leave the folder are refused.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Iterable

from domovoi.config import settings

log = logging.getLogger(__name__)

#: At most this many paths in one request (the watcher collapses a bigger
#: batch to the folder itself).
MAX_PATHS = 256
#: A path longer than this is refused (Linux's PATH_MAX).
MAX_PATH_LEN = 4096
#: A whole-library sweep at most this often; one asked for sooner waits.
FULL_SWEEP_MIN_INTERVAL_SEC = 300.0

#: The library folders, by the setting that names each, most specific
#: first is decided at run time (the longest path wins: a spoken-audio
#: folder may sit inside the music folder).
LIBRARY_SETTINGS = ("music_dir", "audiobooks_dir", "podcasts_dir", "pictures_dir", "videos_dir", "documents_dir")

# Patched by tests.
_clock = time.monotonic
_sleep = asyncio.sleep


def _roots() -> list[tuple[str, Path]]:
    """``(setting, resolved folder)`` for every library folder that is set,
    longest path first."""
    out: list[tuple[str, Path]] = []
    for name in LIBRARY_SETTINGS:
        raw = getattr(settings, name, None)
        if not raw:
            continue
        try:
            out.append((name, Path(raw).expanduser().resolve(strict=False)))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda t: len(str(t[1])), reverse=True)
    return out


def classify(paths: Iterable[str]) -> tuple[dict[str, set[Path]], int]:
    """Groups ``paths`` by the library folder each is in:
    ``({setting: {resolved path, ...}}, refused)``. A path equal to the
    library folder stands for the whole folder. Relative paths, ``..``,
    paths that resolve outside every library folder (a link out) and
    over-long ones are refused and counted."""
    roots = _roots()
    groups: dict[str, set[Path]] = {}
    refused = 0
    for raw in paths:
        if not isinstance(raw, str) or len(raw) > MAX_PATH_LEN or "\x00" in raw:
            refused += 1
            continue
        given = Path(raw)
        if not given.is_absolute() or ".." in given.parts:
            refused += 1
            continue
        try:
            p = given.resolve(strict=False)
        except (OSError, ValueError):
            refused += 1
            continue
        for name, root in roots:
            if p == root or root in p.parents:
                groups.setdefault(name, set()).add(p)
                break
        else:
            refused += 1
    return groups, refused


class _Runner:
    """One background run at a time; requests merge into the next run."""

    def __init__(self) -> None:
        self.pending: dict[str, set[Path]] = {}
        self.task: asyncio.Task[None] | None = None
        self.last_full: dict[str, float] = {}
        self.runs = 0
        self.last_result: dict[str, Any] | None = None

    def add(self, groups: dict[str, set[Path]]) -> None:
        for name, ps in groups.items():
            self.pending.setdefault(name, set()).update(ps)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._drain(), name="host-media-changed")

    async def _drain(self) -> None:
        while self.pending:
            batch, self.pending = self.pending, {}
            try:
                self.last_result = await apply(batch, self)
            except Exception as e:  # noqa: BLE001 — the next batch still runs
                log.warning("host media: a batch failed: %s", e)
            self.runs += 1


_runner = _Runner()


def _is_whole(name: str, paths: set[Path]) -> bool:
    root = dict(_roots()).get(name)
    return root is not None and root in paths


async def _wait_for_full(name: str, runner: _Runner) -> None:
    last = runner.last_full.get(name)
    if last is not None:
        wait = FULL_SWEEP_MIN_INTERVAL_SEC - (_clock() - last)
        if wait > 0:
            log.info("host media: a whole-%s sweep ran %.0f s ago; the next waits %.0f s",
                     name.removesuffix("_dir"), FULL_SWEEP_MIN_INTERVAL_SEC - wait, wait)
            await _sleep(wait)
    runner.last_full[name] = _clock()


async def apply(groups: dict[str, set[Path]], runner: _Runner | None = None) -> dict[str, Any]:
    """Does the work for one merged batch (see the module docstring).
    Returns what it did, per library."""
    from domovoi import mpd_library_sync

    runner = runner or _runner
    done: dict[str, Any] = {}
    for name, paths in sorted(groups.items()):
        whole = _is_whole(name, paths)
        if name == "music_dir":
            from domovoi.workers import library_indexer

            if whole:
                await _wait_for_full(name, runner)
                counts = await library_indexer.index_music_dir()
                rooms = await mpd_library_sync.update_rooms_for(tops=[""], reason="host media watcher")
            else:
                files = [p for p in paths if p.is_file()]
                counts = await library_indexer.index_paths(files) if files else {"scanned": 0, "inserted": 0}
                # Every path, the removed ones too: MPD reading the folder
                # drops what is gone. (index_paths already asked for the
                # folders of what it added; asking again finds nothing new.)
                rooms = await mpd_library_sync.update_rooms_for(paths, reason="host media watcher")
            done["music"] = {"whole": whole, "indexed": counts.get("inserted", 0), "rooms": rooms}
        elif name == "audiobooks_dir":
            if settings.audiobook_indexer_enabled:
                from domovoi.workers.audiobook_indexer import index_audiobooks_dir

                if whole:
                    await _wait_for_full(name, runner)
                removed = [str(p) for p in paths if not p.exists()]
                counts = await index_audiobooks_dir(removed=removed)
            else:
                counts = {}
            rooms = await mpd_library_sync.update_rooms_for(
                () if whole else paths, tops=["audiobooks"] if whole else [], reason="host media watcher")
            done["audiobooks"] = {"whole": whole, "indexed": counts.get("inserted", 0), "rooms": rooms}
        elif name == "podcasts_dir":
            rooms = await mpd_library_sync.update_rooms_for(
                () if whole else paths, tops=["podcasts"] if whole else [], reason="host media watcher")
            done["podcasts"] = {"whole": whole, "rooms": rooms}
        else:
            # Read live when their page opens: nothing to refresh.
            done[name.removesuffix("_dir")] = {"whole": whole, "read_live": True}
    log.info("host media: %s", done)
    return done


def accept(paths: list[str]) -> dict[str, Any]:
    """The route's work: refuse what isn't a library path, queue the rest.
    Needs a running event loop. Returns ``{"queued": N, "refused": M}``."""
    if len(paths) > MAX_PATHS:
        raise ValueError(f"at most {MAX_PATHS} paths at once: send the folder instead")
    groups, refused = classify(paths)
    if refused:
        log.warning("host media: refused %d path(s) outside the library folders", refused)
    if groups:
        _runner.add(groups)
    return {"queued": sum(len(v) for v in groups.values()), "refused": refused,
            "libraries": sorted(n.removesuffix("_dir") for n in groups)}


__all__ = ["FULL_SWEEP_MIN_INTERVAL_SEC", "LIBRARY_SETTINGS", "MAX_PATHS", "accept", "apply", "classify"]
