"""A library song taken out of the library leaves every room's player
(S2-18, the owner's rule, 2026-11-16).

Found on the Mac test sitting: a song deleted while it played stayed in the
dashboard's Now Playing, and the room went on playing it (a deleted file
stays readable to the player that has it open). The rule now, for every
room's own player (one MPD per room; a group's rooms hear their host's):

* the song is playing or paused, and something comes after it in the
  room's queue: the room goes on to that song;
* nothing comes after it: the room stops (the satellite is told, as a
  dashboard Stop would);
* every copy of the song leaves the queue either way, so it can't come up
  later as a file the player can no longer open.

The web process deletes the row (and with ``also_file`` the file) and then
asks the core (``POST /v1/admin/music/tracks-removed``), which alone can
reach the rooms' players. Never raises: the delete happened either way.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePath
from typing import Any, Iterable

from domovoi.config import settings

log = logging.getLogger(__name__)

#: At most this many paths in one call (a duplicates clean-up sends a
#: handful; the dashboard deletes one at a time).
MAX_PATHS = 200


def mpd_uri_for_library_path(file_path: str) -> str | None:
    """The MPD URI a room's player knows a library file by: its path under
    the music folder, POSIX. None outside it."""
    if not file_path or not settings.music_dir:
        return None
    try:
        base = Path(settings.music_dir).expanduser().resolve(strict=False)
        target = Path(file_path).expanduser().resolve(strict=False)
        rel = target.relative_to(base)
    except (OSError, ValueError):
        return None
    uri = PurePath(rel).as_posix()
    return uri if uri and uri != "." else None


async def drop_from_room(room_id: str, mpd: Any, uris: set[str]) -> dict[str, Any] | None:
    """Apply the rule to one room's player. Returns what was done (None when
    the room's queue doesn't hold any of ``uris``)."""
    queue = await mpd.queue_list()
    hits = [e for e in queue if str(e.get("file") or "") in uris]
    snap = await mpd.playback_snapshot()
    current_file = str(snap.get("file") or "")
    playing_gone = snap.get("state") in ("play", "pause") and current_file in uris
    if not hits and not playing_gone:
        return None
    action = None
    if playing_gone:
        pos = snap.get("pos")
        after = [e for e in queue
                 if pos is not None and int(e.get("pos", -1)) > int(pos)
                 and str(e.get("file") or "") not in uris]
        if after:
            await mpd.play_id(int(after[0]["id"]), paused=snap.get("state") == "pause")
            action = "next"
        else:
            await mpd.stop()
            action = "stop"
    removed = await mpd.queue_remove([int(e["id"]) for e in hits if e.get("id") is not None])
    return {"room_id": room_id, "action": action, "removed": len(removed)}


async def drop_removed_tracks(app: Any, file_paths: Iterable[str]) -> list[dict[str, Any]]:
    """The rule for every room, for these library paths. A room whose
    player stopped gets the satellite's music_stop (``dispatch_music``)."""
    from domovoi.clients.mpd import iter_mpd_clients
    from domovoi.models import Response
    from domovoi.room_music import dispatch_music

    uris = {u for u in (mpd_uri_for_library_path(p) for p in list(file_paths)[:MAX_PATHS]) if u}
    if not uris:
        return []
    done: list[dict[str, Any]] = []
    for room_id, mpd in iter_mpd_clients():
        try:
            out = await drop_from_room(room_id, mpd, uris)
        except Exception as e:  # noqa: BLE001 — one room's player down: the others go on
            log.warning("removed tracks: room=%s: %s", room_id, e)
            continue
        if out is None:
            continue
        done.append(out)
        log.info("removed tracks: room=%s %s, %d queue entr%s removed", room_id,
                 {"next": "went on to the next song", "stop": "stopped"}.get(out["action"], "kept playing"),
                 out["removed"], "y" if out["removed"] == 1 else "ies")
        if out["action"] == "stop" and app is not None:
            try:
                await dispatch_music(app, Response(text="", music_action="stop"), room_id)
            except Exception as e:  # noqa: BLE001 — the player stopped; the room hears silence
                log.warning("removed tracks: music_stop to room=%s: %s", room_id, e)
    return done
