"""A room's music player that restarted puts its music back where it was.

A room's player is an MPD daemon in its own container, restarted by Docker
(``--restart unless-stopped``) or by the container guard when it dies. MPD
comes back from its state file, and that file is not where the music was:
MPD writes it ``state_file_interval`` (120 s) after a change to the queue
or the player — a song starting, a pause — and not again until the next
change. So the file almost always says "the song that was playing, two
minutes in". A daemon that dies as a song ends (MPD 0.23.12 dies on some
moves while paused, ``RealMPDClient._unpause_before_moving``; a decoder can
crash on a damaged file) comes back PLAYING the song that just finished,
from its middle — and in a group every room hears it, since they all play
the host's stream (owner, 2026-11-17: "the song finished, then it went back
to the middle of the song"; the dashboard showed the player there too).

The listen logger reads every room's player every few seconds (its
``playback_snapshot``, which carries MPD's ``uptime``). This module keeps
the last look per room; when a look's ``uptime`` is smaller than the one
before, the daemon is a new one, and when it is playing an older place than
the room had reached, the music is put back:

* the song the room was playing had ended, or was in its last seconds (the
  daemon died as it ended): the next song in the queue plays from its top,
  and when there is none the player stops, as it would have;
* otherwise: the same song, where it had got to (the last look plus the
  time since).

Only music that was playing at the last look, seen no more than
:data:`MAX_GAP_SEC` before, is moved; a paused or stopped room, or a
restored queue that no longer holds the song, is left as the daemon came
back (and logged).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger(__name__)

#: A look older than this says too little about where the music is now.
MAX_GAP_SEC = 60.0

#: A song this close to its end at the time of the restart had ended: the
#: next one plays from its top rather than the last seconds of this one.
END_SLACK_SEC = 3.0


@dataclass(frozen=True)
class _Look:
    at: float  # monotonic
    uptime: float | None
    state: str
    pos: int | None
    file: str | None
    elapsed: float
    duration: float | None


def _look(snap: dict[str, Any], at: float) -> _Look:
    def _num(raw: Any) -> float | None:
        try:
            return float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            return None

    pos = snap.get("pos")
    return _Look(
        at=at,
        uptime=_num(snap.get("uptime")),
        state=str(snap.get("state") or "stop"),
        pos=int(pos) if isinstance(pos, (int, float)) else None,
        file=snap.get("file"),
        elapsed=_num(snap.get("elapsed")) or 0.0,
        duration=_num(snap.get("duration")),
    )


class RestartWatch:
    """The last look at each room's player, and the put-back after a
    restart (:meth:`look`)."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last: dict[str, _Look] = {}

    def forget(self, room_id: str) -> None:
        self._last.pop(room_id, None)

    async def look(self, room_id: str, client: Any, snap: dict[str, Any]) -> bool:
        """One look at ``room_id``'s player (``snap``, its
        ``playback_snapshot``). Returns True when it restarted and its music
        was moved back to where the room was (the caller reads it again)."""
        now = self._clock()
        cur = _look(snap, now)
        prev = self._last.get(room_id)
        self._last[room_id] = cur
        if prev is None or prev.uptime is None or cur.uptime is None:
            return False
        if cur.uptime >= prev.uptime:
            return False  # the same daemon: its uptime only grows
        gap = now - prev.at
        log.warning(
            "music player for room=%s restarted (up %.0f s; it had been up %.0f s at "
            "the last look %.0f s ago): it came back %s, queue entry %s at %s; the room "
            "was %s, entry %s at %s",
            room_id, cur.uptime, prev.uptime, gap, cur.state, cur.pos,
            _clock_str(cur.elapsed), prev.state, prev.pos, _clock_str(prev.elapsed),
        )
        if prev.state != "play" or prev.pos is None or gap > MAX_GAP_SEC:
            return False
        if cur.state not in ("play", "pause"):
            return False  # it came back stopped: nothing plays from an old place
        try:
            return await self._put_back(room_id, client, prev, cur, gap)
        except Exception as e:  # noqa: BLE001 — the daemon plays on as it came back
            log.warning("music player for room=%s: couldn't put its music back: %s", room_id, e)
            return False

    async def _put_back(self, room_id: str, client: Any, prev: _Look, cur: _Look,
                        gap: float) -> bool:
        queue = await client.queue_list()
        ids: list[tuple[int, str]] = []
        for e in queue:
            if e.get("id") is None:
                continue
            ids.append((int(e["id"]), str(e.get("file") or "")))
        at = _find(ids, prev.pos, prev.file)
        if at is None:
            log.warning(
                "music player for room=%s: its restored queue no longer holds %r; "
                "left where it came back", room_id, prev.file,
            )
            return False
        reached = prev.elapsed + gap
        if prev.duration is not None and reached >= prev.duration - END_SLACK_SEC:
            if at + 1 >= len(ids):
                await client.stop()
                log.warning(
                    "music player for room=%s: the song it was playing had ended and "
                    "nothing came after it; stopped", room_id,
                )
                return True
            await client.play_id(ids[at + 1][0])
            log.warning(
                "music player for room=%s: the song it was playing had ended; "
                "the next one plays from its top", room_id,
            )
            return True
        if cur.pos != at or cur.state != "play":
            await client.play_id(ids[at][0])
        await client.seek_to(reached)
        log.warning(
            "music player for room=%s: put back at %s in the song it was playing",
            room_id, _clock_str(reached),
        )
        return True


def _find(ids: list[tuple[int, str]], pos: int | None, file: str | None) -> int | None:
    """Where the song the room was playing is in the restored queue: at its
    old position when that entry is the same file, else the first entry
    that is (None: gone)."""
    if pos is not None and 0 <= pos < len(ids) and (file is None or ids[pos][1] == file):
        return pos
    if file:
        for i, (_, f) in enumerate(ids):
            if f == file:
                return i
    return None


def _clock_str(sec: float | None) -> str:
    s = int(max(0.0, sec or 0.0))
    return f"{s // 60}:{s % 60:02d}"
