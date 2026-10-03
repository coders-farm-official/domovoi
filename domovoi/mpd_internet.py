"""Keep the rooms' music players off the internet under ``INTERNET_ACCESS=never``.

Each room has its own MPD container (``domovoi/mpd_provisioner.py``). MPD
fetches a queued stream URL by itself, so its traffic is out of sight of
every in-process gate. Two things keep it dark:

* nothing new goes in: ``sdk.playback.play_url`` and the MPD client's
  ``play_url`` / ``prepare_url`` refuse an internet URL under never
  (``domovoi/clients/mpd.py`` ``refuses_stream_url``);
* nothing old comes back: MPD keeps its queue and play state in its state
  file and, with ``restart: unless-stopped``, resumes a station that was
  playing after any container restart (a host reboot, a Docker Desktop
  restart) with no Domovoi code involved. This module stops and removes
  the internet entries of every room's queue:

  * when an admin saves the answer ``never`` (the ``internet_access``
    reapply hook, :func:`schedule_purge`), so a station that is playing
    stops and a queued one can't come back on "resume";
  * at core start under never (:func:`schedule_boot_purge`, right after the
    rooms are warmed), for a box whose answer was changed while the core
    was down. A container that is still starting is retried for a while.

A room's library songs stay queued; only entries whose URL host is not on
this network (:func:`domovoi.egress.check_destination`) are removed, and
the room's now-playing stamp is cleared when it named one. Under any other
answer this module does nothing: unanswered is exactly today's behaviour.

Everything is best effort: an unreachable room is logged and skipped, and
nothing raises into the caller.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from domovoi import egress

log = logging.getLogger(__name__)

# A room container that is still starting (right after a host reboot) is
# asked again every few seconds for this long at core start.
BOOT_RETRY_SEC = 90.0
RETRY_INTERVAL_SEC = 3.0

_PENDING: set[asyncio.Task] = set()


def is_internet_entry(uri: object) -> bool:
    """A queue entry MPD would fetch from the internet: a URL (it has a
    scheme) whose host is not on this network, under never. Library
    files are relative paths and never match."""
    if not isinstance(uri, str) or "://" not in uri:
        return False
    return egress.check_destination(uri) is not None


async def purge_room(room_id: str, client: Any) -> int:
    """Stop ``client`` if it is on an internet stream and delete every
    internet entry from its queue. Returns how many entries it removed.
    Raises when the daemon can't be reached (the caller decides whether
    to retry)."""
    if not egress.internet_turned_off():
        return 0
    entries = await client.queue_list()
    doomed = [e for e in entries if is_internet_entry(e.get("file"))]
    if not doomed:
        return 0
    stopped = False
    current: dict[str, Any] | None = None
    try:
        current = await client.current_song()
    except Exception as e:  # noqa: BLE001 — best effort; stop anyway below
        log.debug("internet off: room=%s currentsong failed: %s", room_id, e)
    if current is None or is_internet_entry(current.get("file")):
        # Deleting the song that is playing would only move MPD on to the
        # next entry; stop first.
        await client.stop()
        stopped = True
    removed = await client.queue_remove(
        [int(e["id"]) for e in doomed if e.get("id") is not None]
    )
    try:
        from domovoi.now_playing import NOW_PLAYING

        stamp = NOW_PLAYING.get(room_id)
        if stamp is not None and (
            stopped or is_internet_entry((stamp.data or {}).get("stream_url"))
        ):
            NOW_PLAYING.clear(room_id)
    except Exception as e:  # noqa: BLE001
        log.debug("internet off: room=%s now-playing clear failed: %s", room_id, e)
    log.info(
        "internet off: room=%s removed %d internet stream(s) from its music "
        "queue%s", room_id, len(removed), " and stopped it" if stopped else "",
    )
    return len(removed)


async def purge_internet_streams(*, retry_sec: float = 0.0) -> dict[str, int]:
    """Purge every provisioned room. ``retry_sec`` > 0 keeps asking a room
    that can't be reached yet (a container still starting) for that long.
    Returns ``{room_id: removed}`` for the rooms it reached. Never raises;
    a no-op unless the answer is ``never``."""
    out: dict[str, int] = {}
    if not egress.internet_turned_off():
        return out
    try:
        from domovoi.clients.mpd import iter_mpd_clients

        rooms = iter_mpd_clients()
    except Exception as e:  # noqa: BLE001
        log.warning("internet off: couldn't list the music rooms: %s", e)
        return out
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, retry_sec)
    for room_id, client in rooms:
        while True:
            try:
                out[room_id] = await purge_room(room_id, client)
                break
            except Exception as e:  # noqa: BLE001 — unreachable room
                if loop.time() + RETRY_INTERVAL_SEC > deadline:
                    log.warning(
                        "internet off: couldn't check room=%s's music queue "
                        "for internet streams: %s", room_id, e,
                    )
                    break
                await asyncio.sleep(RETRY_INTERVAL_SEC)
            if not egress.internet_turned_off():
                break
    return out


def _schedule(retry_sec: float, name: str) -> asyncio.Task | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.debug("internet off: no running loop; music queue check skipped")
        return None
    task = loop.create_task(purge_internet_streams(retry_sec=retry_sec), name=name)
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
    return task


def schedule_purge() -> None:
    """The ``internet_access`` reapply hook: under never, purge every
    room's queue in the background. A no-op for any other answer."""
    if egress.internet_turned_off():
        _schedule(0.0, "mpd-internet-purge")


def schedule_boot_purge() -> asyncio.Task | None:
    """Core start under never: purge in the background, retrying rooms
    whose containers are still starting. Never delays the boot."""
    if not egress.internet_turned_off():
        return None
    return _schedule(BOOT_RETRY_SEC, "mpd-internet-boot-purge")
