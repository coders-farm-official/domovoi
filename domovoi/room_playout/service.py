"""The playout service: one streamer per playing room, and the speakers
subscribed to it.

Room membership comes from a ``RoomRegistry``: the app's live registry
(``domovoi/room_devices.py``), which also calls :meth:`RoomPlayout.rebind`
after a satellite moves. ``InMemoryRoomRegistry`` stands in for it in
unit tests: every satellite is its own room (id = room id), unless the
test assigns it elsewhere.

The calls slice 5b routes room sound through:

* ``play_stream(room)``: the room's music, decoded from its MPD stream;
* ``say(room, pcm, rate, channels)``: a reply, chime, timer or announcement,
  mixed in (music ducked); returns when it plays;
* ``stop / pause / resume / duck(room, ...)``: timed, every speaker at once;
* ``cut_speech(room)``: a barge-in stops the room's answer, music plays on.

Speaker calibration (``domovoi/room_calibration.py``) reaches the devices
through ``send_to`` and keeps each device's output latency here
(``set_latency``: the ``latency`` frame, protocol §5.5). A move clears it.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from typing import Any, Callable, Protocol

from domovoi.room_playout.clock import now_us
from domovoi.room_playout.sources import FfmpegStreamSource, loopback_stream_url
from domovoi.room_playout.streamer import PcmSource, RoomStreamer, Sink

log = logging.getLogger(__name__)

DEFAULT_PLAYOUT_MS = 500


class RoomRegistry(Protocol):
    """What playout needs to know about rooms (``room_devices.RoomDevices``)."""

    def room_of(self, satellite_id: str) -> str | None: ...
    def devices_in(self, room_id: str) -> list[str]: ...


class InMemoryRoomRegistry:
    def __init__(self, assignments: dict[str, str] | None = None) -> None:
        self._room: dict[str, str] = dict(assignments or {})

    def assign(self, satellite_id: str, room_id: str) -> None:
        self._room[satellite_id] = room_id

    def room_of(self, satellite_id: str) -> str | None:
        return self._room.get(satellite_id, satellite_id)

    def devices_in(self, room_id: str) -> list[str]:
        # An unassigned satellite is in the room of its own id (today's house).
        named = [s for s, r in self._room.items() if r == room_id and s != room_id]
        return ([room_id] if self.room_of(room_id) == room_id else []) + named


async def _mpd_source(room_id: str) -> PcmSource | None:
    url = loopback_stream_url(room_id)
    if url is None:
        return None
    src = FfmpegStreamSource(url)
    await src.start()
    return src


class RoomPlayout:
    def __init__(self, registry: RoomRegistry | None = None, *,
                 clock: Callable[[], int] = now_us,
                 playout_ms: int = DEFAULT_PLAYOUT_MS,
                 source_for_room: Callable[[str], Any] = _mpd_source,
                 run_tasks: bool = True) -> None:
        self.registry: RoomRegistry = registry or InMemoryRoomRegistry()
        self.clock = clock
        self.playout_ms = playout_ms
        self._source_for_room = source_for_room
        self._run_tasks = run_tasks
        self._timeline_ids = itertools.count(1)
        self.streamers: dict[str, RoomStreamer] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._subs: dict[str, tuple[str, Sink]] = {}     # satellite -> (room, sink)
        self.stats: dict[str, dict[str, Any]] = {}       # satellite -> latest stats
        self.latency: dict[str, int] = {}                # satellite -> output latency sent
        self._supports: dict[str, frozenset[str]] = {}   # satellite -> hello's `supports`
        #: ``room_calibration.RoomCalibration`` once one is made for this playout.
        self.calibration: Any = None

    # ── rooms ──────────────────────────────────────────────────────────
    def streamer(self, room_id: str) -> RoomStreamer:
        st = self.streamers.get(room_id)
        if st is None:
            self.reap_idle()
            st = RoomStreamer(room_id, next(self._timeline_ids), clock=self.clock,
                              playout_ms=self.playout_ms)
            self.streamers[room_id] = st
            if self._run_tasks:
                self._tasks[room_id] = asyncio.get_running_loop().create_task(st.run())
        return st

    async def play_stream(self, room_id: str, source: PcmSource | None = None) -> bool:
        """Start the room's music on every speaker. False when the room has
        no stream to play."""
        src = source if source is not None else await self._source_for_room(room_id)
        if src is None:
            return False
        self.streamer(room_id).play_stream(src)
        return True

    def say(self, room_id: str, pcm: bytes, rate: int, channels: int = 1) -> tuple[float, float]:
        return self.streamer(room_id).say(pcm, rate, channels)

    def stop(self, room_id: str, at_us: float | None = None) -> int:
        return self.streamer(room_id).stop(at_us)

    def cut_speech(self, room_id: str, at_us: float | None = None) -> int:
        """Stop the room's speech on every speaker (a barge-in, slice 3)."""
        return self.streamer(room_id).cut_speech(at_us)

    def pause(self, room_id: str, at_us: float | None = None) -> int:
        return self.streamer(room_id).pause(at_us)

    def resume(self, room_id: str) -> None:
        self.streamer(room_id).resume()

    def duck(self, room_id: str, gain: float, at_us: float | None = None, ramp_ms: int = 50) -> int:
        return self.streamer(room_id).duck(gain, at_us, ramp_ms)

    # ── speakers ───────────────────────────────────────────────────────
    def attach(self, satellite_id: str, sink: Sink) -> str | None:
        """Subscribe a speaker to its room. Returns the room, or None when
        the device is in no room."""
        room_id = self.registry.room_of(satellite_id)
        if room_id is None:
            return None
        self.detach(satellite_id)
        self.streamer(room_id).subscribe(sink)
        self._subs[satellite_id] = (room_id, sink)
        return room_id

    def detach(self, satellite_id: str, sink: Sink | None = None) -> None:
        cur = self._subs.get(satellite_id)
        if cur is None or (sink is not None and cur[1] is not sink):
            return
        del self._subs[satellite_id]
        self.latency.pop(satellite_id, None)
        self._supports.pop(satellite_id, None)
        st = self.streamers.get(cur[0])
        if st is not None:
            st.unsubscribe(cur[1])
        self.reap_idle()

    def reap_idle(self) -> list[str]:
        """Drop the streamers of rooms with no speaker and nothing to play
        (no music attached, paused or not, and no sound queued). Returns the
        rooms dropped; the next call for one makes it a new timeline."""
        gone = [r for r, st in self.streamers.items()
                if not st.sinks and not st.active and st.music is None]
        for room_id in gone:
            del self.streamers[room_id]
            task = self._tasks.pop(room_id, None)
            if task is not None:
                task.cancel()
        return gone

    def rebind(self, satellite_id: str) -> str | None:
        """After a move (the registry calls this): re-subscribe the device to its
        new room's timeline; the new ``timeline`` message replaces the old."""
        cur = self._subs.get(satellite_id)
        if cur is None:
            return None
        had = self.latency.get(satellite_id, 0)
        supports = self._supports.get(satellite_id)
        room = self.attach(satellite_id, cur[1])     # (re-subscribing forgets both)
        if room is not None:
            if supports is not None:
                self._supports[satellite_id] = supports
            # The calibration belonged to the old room (the DB row is
            # cleared by the move itself).
            self.latency[satellite_id] = had
            self.set_latency(satellite_id, 0)
            self.joined(satellite_id, room, calibrated=False)
        return room

    # ── calibration ────────────────────────────────────────────────────
    def declare(self, satellite_id: str, supports: Any) -> None:
        """The optional features a device's hello listed (``supports``)."""
        if isinstance(supports, (list, tuple)):
            self._supports[satellite_id] = frozenset(x for x in supports if isinstance(x, str))

    def can_calibrate(self, satellite_id: str) -> bool:
        return "calibrate" in self._supports.get(satellite_id, ())

    def send_to(self, satellite_id: str, msg: dict[str, Any]) -> bool:
        """A control frame to one subscribed device; False when it has none."""
        cur = self._subs.get(satellite_id)
        if cur is None:
            return False
        cur[1].send_control(msg)
        return True

    def set_latency(self, satellite_id: str, latency_us: int) -> None:
        """The device's calibrated output latency: kept, and sent to it when
        it changes (a device starts every connection at 0)."""
        us = max(0, int(latency_us))
        prev = self.latency.get(satellite_id, 0)
        self.latency[satellite_id] = us
        if us != prev:
            self.send_to(satellite_id, {"type": "latency", "output_latency_us": us})

    def joined(self, satellite_id: str, room_id: str, *, calibrated: bool) -> None:
        """A speaker is in ``room_id`` now: the automatic calibration may
        want to run (it decides)."""
        cal = self.calibration
        if cal is not None:
            try:
                cal.on_joined(satellite_id, room_id, calibrated)
            except Exception as e:  # noqa: BLE001 — never breaks a join
                log.warning("calibration: join hook failed for %s: %s", satellite_id, e)

    def calibration_result(self, satellite_id: str, msg: dict[str, Any]) -> None:
        cal = self.calibration
        if cal is not None:
            cal.on_result(satellite_id, msg)

    def speakers(self, room_id: str) -> list[str]:
        return [s for s, (r, _) in self._subs.items() if r == room_id]

    async def close(self) -> None:
        for st in self.streamers.values():
            st.stop()
        for t in self._tasks.values():
            t.cancel()
        self._tasks.clear()


def get_playout(app: Any) -> RoomPlayout:
    """The app's playout service, created on first use."""
    pl = getattr(app.state, "room_playout", None)
    if pl is None:
        from domovoi import room_devices

        reg = room_devices.registry(app)
        pl = RoomPlayout(reg)
        reg.on_move(pl.rebind)
        app.state.room_playout = pl
    return pl
