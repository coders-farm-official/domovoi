"""Tell every room's MPD about files an indexer just added (F-V023).

Each room's MPD keeps its own database of ``/music`` (the music library,
with ``podcasts_dir`` and ``audiobooks_dir`` mounted inside it as
``/music/podcasts`` and ``/music/audiobooks``) and learns of new files by
watching the folders (``auto_update``, i.e. inotify). A Docker Desktop
bind mount of a Windows folder delivers no such events, and neither does
a network share, so on those hosts a file added after the room's MPD
started was indexed by the core and still couldn't be played: "I found
The Hearth Spirit but the player couldn't load it", until someone said
"rescan my library".

So every indexer that adds files asks the rooms' MPDs to read the folders
those files are in: ``update <folder>`` per folder, a handful at most
(more collapse to the library's top folders), one connection per room.
Cheap and idempotent: MPD reads only what changed in those folders, and
asking again finds nothing new. Never raises: the files are indexed either
way, and "rescan my library" still reads everything.

Only the core process has the rooms (``clients.mpd`` learns their ports
from the provisioner); called from the web process it does nothing, and
the web asks the core instead (``POST /v1/admin/music/mpd-rescan``).
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePath
from typing import Iterable

from domovoi.config import settings

log = logging.getLogger(__name__)

#: More folders than this in one batch are read as their top folders
#: (the whole library, ``audiobooks``, ``podcasts``) instead: MPD queues at
#: most 32 update jobs, and a first sweep of a big library touches
#: hundreds of folders.
MAX_FOLDERS = 8

# (host dir setting, MPD subfolder) — the nested mounts first, so a
# spoken-audio folder inside the music folder maps the way spoken_audio's
# mpd_uri_for plays it.
_ROOTS: tuple[tuple[str, str], ...] = (
    ("audiobooks_dir", "audiobooks"),
    ("podcasts_dir", "podcasts"),
    ("music_dir", ""),
)


def _join(top: str, rel: str) -> str:
    rel = "" if rel == "." else rel
    return "/".join(p for p in (top, rel) if p)


def mpd_folder_for(path: str | Path) -> tuple[str, str] | None:
    """``(top, uri)`` for a host path an indexer added: ``uri`` is the MPD
    URI of the folder MPD must read to see it (the path itself when it is
    a folder, such as a book of chapter files; else the folder it is in;
    ``""`` is the whole library) and ``top`` is the library folder it is
    under (``""``, ``"audiobooks"`` or ``"podcasts"``). None for a path
    outside every library folder."""
    try:
        p = Path(path).expanduser()
        target = p.resolve(strict=False)
        folder = target if p.is_dir() else target.parent
    except (OSError, ValueError):
        return None
    for setting, top in _ROOTS:
        raw = getattr(settings, setting, None)
        if not raw:
            continue
        try:
            base = Path(raw).expanduser().resolve(strict=False)
            rel = folder.relative_to(base)
        except (OSError, ValueError):
            continue
        return top, _join(top, PurePath(rel).as_posix())
    return None


def folders_to_update(paths: Iterable[str | Path] = (), *, tops: Iterable[str] = ()) -> list[str]:
    """The MPD URIs to ``update`` for ``paths`` (plus whole library folders
    ``tops``, such as ``"podcasts"``): each folder once, a folder inside
    another one asked for dropped, and more than :data:`MAX_FOLDERS`
    collapsed to their top folders (``""`` covers everything)."""
    wanted: set[str] = set(tops)
    used_tops: set[str] = set(tops)
    for raw in paths:
        hit = mpd_folder_for(raw)
        if hit is None:
            continue
        used_tops.add(hit[0])
        wanted.add(hit[1])
    if len(wanted) > MAX_FOLDERS:
        wanted = used_tops
    if "" in wanted:
        return [""]
    ordered = sorted(wanted)
    return [u for u in ordered if not any(u.startswith(o + "/") for o in ordered if o != u)]


async def update_rooms_for(
    paths: Iterable[str | Path] = (), *, tops: Iterable[str] = (), reason: str = "indexer"
) -> int:
    """Ask every room's MPD to read the folders of ``paths`` (and the whole
    ``tops``). Returns how many rooms took the request. Never raises."""
    uris = folders_to_update(paths, tops=tops)
    if not uris:
        return 0
    try:
        from domovoi.clients.mpd import iter_mpd_clients

        rooms = iter_mpd_clients()
    except Exception as e:  # noqa: BLE001 — never fails the indexer
        log.warning("%s: couldn't list the rooms to update their players: %s", reason, e)
        return 0
    done = 0
    for room_id, client in rooms:
        try:
            await client.update_paths(uris)
            done += 1
        except Exception as e:  # noqa: BLE001 — a room that is down misses it
            log.warning("%s: room=%s's player couldn't be asked to read %d new folder(s): %s",
                        reason, room_id, len(uris), e)
    if rooms:
        log.info("%s: asked %d of %d room player(s) to read %d folder(s)",
                 reason, done, len(rooms), len(uris))
    return done


__all__ = ["MAX_FOLDERS", "folders_to_update", "mpd_folder_for", "update_rooms_for"]
