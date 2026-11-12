"""Up next in the rooms: the ``UpNextExtender`` worker (plan §5, phase P4).

It IS the rooms' listen logger (:class:`~domovoi.workers.listen_logger.ListenLogger`,
P1) with Up next on top, so each room's music player is read once per tick
(``playback_sweeper_interval_sec``, 5 s) for both:

* **the listening history** — as before: a song that ends becomes one
  ``music_listens`` row. Now the row carries the room's Up next session
  and its origin (``auto`` for a pick), and the song feeds the session
  (drift seeds, the early-skip rule, the pick marked played or skipped)
  even under "Keep listening history: Don't keep"
  (:func:`domovoi.up_next.rooms.song_ended`);
* **the room's Up next** — for a room with an active session, after the
  look: the session's picks found in the queue, picks removed or jumped
  past noted, picks taken out (less like this, Autoplay off) removed from
  the queue, the next batch appended once 5 or fewer songs are left, old
  played songs trimmed, and the session ended when the room stopped, sat
  idle for two hours or had its queue replaced by something else
  (:func:`domovoi.up_next.rooms.look`).

A poll, not MPD's ``idle``: the refill threshold is five songs, about a
quarter of an hour, so five seconds of latency costs nothing, and a
per-room idle connection would hold a socket per room for no gain.

Registered in place of the plain listen logger (domovoi/main.py). Skipped
under ``USE_STUBS``; the tick is tested with stub players
(domovoi/tests/test_up_next_rooms.py).
"""

from __future__ import annotations

import logging
from typing import Any

from domovoi.up_next import rooms
from domovoi.workers.listen_logger import ListenLogger, _Song

log = logging.getLogger(__name__)


class UpNextExtender(ListenLogger):
    name = "up_next_extender"
    enabled_setting = None
    interval_setting = "playback_sweeper_interval_sec"
    stub_suppressed = True

    async def _after_look(self, room_id: str, client: Any, snap: dict[str, Any]) -> None:
        try:
            await rooms.look(room_id, client, snap, now=self._clock())
        except Exception as e:  # noqa: BLE001 — one room's Up next never stops the rest
            log.debug("up next extender: room %s: %s", room_id, e)

    async def _song_ended(self, room_id: str, song: _Song, played: float,
                          outcome: str) -> tuple[str | None, str]:
        return await rooms.song_ended(
            room_id, songid=song.songid, track_id=song.track_id, played_sec=played,
            duration_sec=song.duration, outcome=outcome)
