"""The room streamer: one room's timeline, cut into stamped 20 ms chunks.

Chunk ``n`` (timeline index ``n * 960``) is sent ``playout_ms`` before it
plays, to every speaker subscribed to the room. Music comes from a
``PcmSource`` (the room's MPD stream, decoded); speech and other sounds are
mixed in with :meth:`RoomStreamer.say`, music ducked under them. Nothing is
sent while there is nothing to play: the timeline just has a gap.

The streamer never awaits: :meth:`tick` does all the work for "now", so the
tests drive it with a fake clock and the core runs it from :meth:`run`.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any, Callable, Protocol

import numpy as np

from domovoi.room_playout import protocol as P
from domovoi.room_playout.clock import now_us

log = logging.getLogger(__name__)

MIN_LEAD_US = 20_000        # a chunk due sooner than this is late: skip ahead
CONTROL_LEAD_US = 80_000    # timed controls land this far ahead by default
MUSIC_UNDER_SPEECH = 0.3    # music gain while a sound is mixed over it


class Sink(Protocol):
    """One subscribed speaker. Both calls must return at once."""

    def send_audio(self, frame: bytes) -> None: ...
    def send_control(self, msg: dict[str, Any]) -> None: ...


class PcmSource(Protocol):
    """48 kHz stereo s16le, pulled without waiting."""

    @property
    def active(self) -> bool: ...      # False once it has ended for good
    @property
    def started(self) -> bool: ...     # has delivered its first audio
    def read(self, n: int) -> bytes: ...   # up to n bytes, what is buffered
    def close(self) -> None: ...


def to_room_pcm(pcm: bytes, rate: int, channels: int) -> np.ndarray:
    """Any s16le PCM as 48 kHz stereo, shape (frames, 2), int16."""
    a = np.frombuffer(pcm, dtype="<i2")
    a = a[: len(a) - len(a) % channels].reshape(-1, channels).astype(np.float32)
    if channels == 1:
        a = np.repeat(a, 2, axis=1)
    elif channels > 2:
        a = a[:, :2]
    if rate != P.RATE and len(a):
        n_out = int(round(len(a) * P.RATE / rate))
        x_in = np.arange(len(a)) / rate
        x_out = np.arange(n_out) / P.RATE
        a = np.stack([np.interp(x_out, x_in, a[:, c]) for c in range(2)], axis=1)
    return np.clip(np.round(a), -32768, 32767).astype(np.int16)


class RoomStreamer:
    def __init__(self, room_id: str, timeline_id: int, *,
                 clock: Callable[[], int] = now_us, playout_ms: int = 500) -> None:
        self.room_id = room_id
        self.timeline_id = timeline_id
        self.clock = clock
        self.playout_ms = playout_ms
        self.t0_us = clock()
        self.next_index: int | None = None      # first index not yet sent
        self.music: PcmSource | None = None
        self.paused = False
        self._speech: list[tuple[int, np.ndarray]] = []   # (start index, frames x 2)
        self._sinks: set[Sink] = set()
        self._recent: deque[tuple[int, bytes]] = deque()
        self._wake = asyncio.Event()
        self.underruns = 0
        self.late_skips = 0
        self.sent = 0

    # ── timeline arithmetic ────────────────────────────────────────────
    @property
    def playout_us(self) -> int:
        return self.playout_ms * 1000

    def play_at(self, index: int) -> float:
        return P.play_time_us(self.t0_us, index)

    def index_at(self, t_us: float) -> int:
        """The first chunk-aligned index playing at or after ``t_us``."""
        raw = (t_us - self.t0_us) * P.RATE / 1_000_000
        k = -(-int(np.ceil(raw)) // P.CHUNK_FRAMES)
        return k * P.CHUNK_FRAMES

    def _start(self) -> None:
        if self.next_index is None:
            self.next_index = self.index_at(self.clock() + self.playout_us)
        self._wake.set()

    @property
    def active(self) -> bool:
        return self.next_index is not None

    # ── what plays ─────────────────────────────────────────────────────
    def play_stream(self, source: PcmSource) -> None:
        if self.music is not None and self.music is not source:
            self.music.close()
        self.music = source
        self.paused = False
        self._start()

    def say(self, pcm: bytes, rate: int, channels: int = 1) -> tuple[float, float]:
        """Mix a sound into the timeline at the earliest free moment (after
        any sound already queued). Returns its (start, end) server times."""
        a = to_room_pcm(pcm, rate, channels)
        self._start()
        assert self.next_index is not None
        start = self.next_index
        if self._speech:
            last_start, last = self._speech[-1]
            start = max(start, last_start + len(last))
        self._speech.append((start, a))
        return self.play_at(start), self.play_at(start + len(a))

    # ── timed controls ─────────────────────────────────────────────────
    def _at(self, at_us: float | None) -> int:
        return int(at_us if at_us is not None else self.clock() + CONTROL_LEAD_US)

    def _flush(self, at: int) -> None:
        self._broadcast(P.flush_msg(at))
        while self._recent and self.play_at(self._recent[-1][0]) >= at:
            self._recent.pop()

    def stop(self, at_us: float | None = None) -> int:
        """Silence the room at ``at_us``: music detached, sounds dropped."""
        at = self._at(at_us)
        if self.music is not None:
            self.music.close()
        self.music = None
        self._speech.clear()
        self.next_index = None
        self._flush(at)
        return at

    def cut_speech(self, at_us: float | None = None) -> int:
        """A barge-in: the queued and playing sounds (an answer, a timer)
        stop at ``at_us`` on every speaker; music, if any, plays on from
        there (it was ducked under the speech)."""
        at = self._at(at_us)
        self._speech.clear()
        self._flush(at)
        if self.next_index is not None:
            if self.music is not None and not self.paused:
                self.next_index = min(self.next_index, max(0, self.index_at(at)))
            else:
                self.next_index = None
        return at

    def pause(self, at_us: float | None = None) -> int:
        """Like stop, but the music source stays attached (drained, not
        played) until :meth:`resume`."""
        at = self._at(at_us)
        self.paused = True
        self._speech.clear()
        self.next_index = None
        self._flush(at)
        return at

    def resume(self) -> None:
        self.paused = False
        if self.music is not None:
            self._start()

    def duck(self, gain: float, at_us: float | None = None, ramp_ms: int = 50) -> int:
        at = self._at(at_us)
        self._broadcast(P.gain_msg(at, max(0.0, min(1.0, gain)), ramp_ms))
        return at

    # ── speakers ───────────────────────────────────────────────────────
    def subscribe(self, sink: Sink) -> None:
        sink.send_control(P.timeline_msg(self.timeline_id, self.t0_us))
        soon = self.clock() + MIN_LEAD_US
        for index, frame in self._recent:
            if self.play_at(index) > soon:
                sink.send_audio(frame)
        self._sinks.add(sink)

    def unsubscribe(self, sink: Sink) -> None:
        self._sinks.discard(sink)

    @property
    def sinks(self) -> frozenset[Sink]:
        return frozenset(self._sinks)

    def _broadcast(self, msg: dict[str, Any]) -> None:
        for s in list(self._sinks):
            s.send_control(msg)

    # ── the clock-driven part ──────────────────────────────────────────
    def _render(self, index: int) -> bytes | None:
        out = np.zeros((P.CHUNK_FRAMES, 2), dtype=np.float32)
        playing = False
        if self.music is not None:
            if self.paused:
                self.music.read(1 << 20)  # keep the decoder drained
            else:
                data = self.music.read(P.CHUNK_BYTES)
                if data:
                    m = np.frombuffer(data, dtype="<i2").reshape(-1, 2)
                    out[: len(m)] = m
                    playing = True
                if len(data) < P.CHUNK_BYTES:
                    if not self.music.active:
                        self.music = None
                    else:
                        playing = True
                        if self.music.started:
                            self.underruns += 1
        end = index + P.CHUNK_FRAMES
        self._speech = [(s, a) for s, a in self._speech if s + len(a) > index]
        mixed = False
        for start, a in self._speech:
            lo, hi = max(start, index), min(start + len(a), end)
            if hi <= lo:
                continue
            if not mixed:
                out *= MUSIC_UNDER_SPEECH
                mixed = True
            out[lo - index: hi - index] += a[lo - start: hi - start]
        if not playing and not self._speech:
            return None
        return np.clip(out, -32768, 32767).astype("<i2").tobytes()

    def tick(self) -> int:
        """Send every chunk now due. Returns how many were sent."""
        now = self.clock()
        n = 0
        while self.next_index is not None:
            index = self.next_index
            at = self.play_at(index)
            if at - self.playout_us > now:
                break
            if at < now + MIN_LEAD_US:
                # The loop stalled past the cushion: resume in step later.
                self.late_skips += 1
                self.next_index = self.index_at(now + self.playout_us)
                continue
            payload = self._render(index)
            if payload is None:
                self.next_index = None
                break
            frame = P.encode_audio(P.AudioFrame(self.timeline_id, index, int(at), payload))
            for s in list(self._sinks):
                s.send_audio(frame)
            self._recent.append((index, frame))
            self.next_index = index + P.CHUNK_FRAMES
            self.sent += 1
            n += 1
        while self._recent and self.play_at(self._recent[0][0]) < now:
            self._recent.popleft()
        return n

    def wait_us(self) -> float | None:
        """Microseconds until the next chunk is due; None when idle."""
        if self.next_index is None:
            return None
        return max(0.0, self.play_at(self.next_index) - self.playout_us - self.clock())

    async def run(self) -> None:
        """Pace :meth:`tick` in real time until cancelled."""
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - one bad chunk must not end the room
                log.exception("room streamer %s: tick failed", self.room_id)
            wait = self.wait_us()
            if wait is None:
                self._wake.clear()
                await self._wake.wait()
            else:
                await asyncio.sleep(min(wait / 1e6, 0.02))
