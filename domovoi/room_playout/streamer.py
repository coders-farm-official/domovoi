"""The room streamer: one room's timeline, cut into stamped 20 ms chunks.

Chunk ``n`` (timeline index ``n * 960``) is sent ``playout_ms`` before it
plays, to every speaker subscribed to the room. Music comes from a
``MusicFeed`` (the room's MPD stream, or its shared queue's host's, decoded
once); speech and other sounds are mixed in with :meth:`RoomStreamer.say`,
music ducked under them. Nothing is sent while there is nothing to play:
the timeline just has a gap.

Rooms that hear one host (a shared queue, a group) each keep their own
streamer, so an answer, a duck or a speaker of one room stays in that room,
but read their music from the host's ONE feed, by timeline index. Every
streamer of a playout service shares one ``t0_us``, so index ``n`` is the
same server moment in every room: the same sample of the song plays at the
same time on every speaker of every room following the host.

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


#: Decoded chunks a feed keeps for the rooms reading it (4 s): enough for
#: a room joining mid-song, and for rooms with different playout buffers.
FEED_KEEP_CHUNKS = 200


class MusicFeed:
    """One host's music, decoded once and addressed by timeline index: the
    first room to ask for chunk ``n`` pulls it from the source, every other
    room following the host gets the same samples for ``n``.

    Readers are the room streamers playing it; the source closes when the
    last one leaves."""

    def __init__(self, host: str, source: PcmSource) -> None:
        self.host = host
        self.source = source
        self.readers: set["RoomStreamer"] = set()
        self.closed = False
        self._chunks: dict[int, tuple[bytes, str]] = {}
        self._next: int | None = None        # first index not yet pulled
        self._ended_at: int | None = None

    @property
    def alive(self) -> bool:
        return not self.closed and self._ended_at is None

    def chunk(self, index: int) -> tuple[bytes, str]:
        """The music for chunk ``index`` and how it went: ``ok``; ``short``
        (an underrun), ``wait`` (not started yet) or ``gap`` (this room
        asked after the others moved past it): silence, still playing;
        ``end``: the source ended for good."""
        got = self._chunks.get(index)
        if got is not None:
            return got
        if self._ended_at is not None and index >= self._ended_at:
            return b"", "end"
        if self._next is not None and index < self._next:
            return b"", "gap"
        data = self.source.read(P.CHUNK_BYTES)
        status = "ok"
        if len(data) < P.CHUNK_BYTES:
            if not self.source.active:
                status = "end"
                self._ended_at = index
            else:
                status = "short" if self.source.started else "wait"
        self._chunks[index] = (data, status)
        self._next = index + P.CHUNK_FRAMES
        while len(self._chunks) > FEED_KEEP_CHUNKS:
            del self._chunks[next(iter(self._chunks))]
        return data, status

    def first_cached_from(self, index: int) -> int | None:
        """The earliest chunk already decoded at or after ``index`` (a
        joining room starts there: where the song is now)."""
        ok = [i for i, (_, st) in self._chunks.items() if i >= index and st != "end"]
        return min(ok) if ok else None

    def drop_from(self, index: int) -> None:
        """Forget what was decoded for ``index`` on (a pause or a skip at
        that moment): the next ask pulls the source again."""
        for i in [i for i in self._chunks if i >= index]:
            del self._chunks[i]
        if self._next is not None:
            self._next = min(self._next, index)

    def drain(self) -> None:
        """Forget what the decoder holds (decoded before a pause or a skip:
        no longer the music)."""
        self.source.read(1 << 24)

    def keep_drained(self) -> None:
        self.source.read(1 << 20)

    def shared_with(self, streamer: "RoomStreamer") -> bool:
        """Another room is playing this feed now."""
        return any(r is not streamer and r.next_index is not None and not r.paused
                   for r in self.readers)

    def leave(self, streamer: "RoomStreamer") -> None:
        self.readers.discard(streamer)
        if not self.readers and not self.closed:
            self.closed = True
            self.source.close()


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
                 clock: Callable[[], int] = now_us, playout_ms: int = 500,
                 t0_us: int | None = None) -> None:
        self.room_id = room_id
        self.timeline_id = timeline_id
        self.clock = clock
        self.playout_ms = playout_ms
        # The playout service gives every room the same t0: one index is
        # one moment in every room (rooms following one host stay in step).
        self.t0_us = clock() if t0_us is None else int(t0_us)
        self.next_index: int | None = None      # first index not yet sent
        self.feed: MusicFeed | None = None
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
        return self.index_at_from(self.t0_us, t_us)

    @staticmethod
    def index_at_from(t0_us: float, t_us: float) -> int:
        raw = (t_us - t0_us) * P.RATE / 1_000_000
        k = -(-int(np.ceil(raw)) // P.CHUNK_FRAMES)
        return k * P.CHUNK_FRAMES

    @property
    def music(self) -> PcmSource | None:
        """The source of the room's music (its feed's), None when none."""
        return self.feed.source if self.feed is not None else None

    def _start(self, index: int | None = None, *, after_us: float | None = None) -> None:
        if self.next_index is None:
            if index is None:
                now = self.clock()
                index = self.index_at(now + self.playout_us)
                f = self.feed
                if f is not None and not self.paused and f.shared_with(self):
                    # Joining rooms already playing this host: start where
                    # the song is now, with the samples they were sent.
                    floor = max(now + MIN_LEAD_US + P.CHUNK_FRAMES * 1e6 / P.RATE,
                                after_us or 0)
                    early = f.first_cached_from(self.index_at(floor))
                    if early is not None and early < index:
                        index = early
            self.next_index = index
        self._wake.set()

    def _leave_feed(self) -> None:
        if self.feed is not None:
            self.feed.leave(self)
        self.feed = None

    @property
    def active(self) -> bool:
        return self.next_index is not None

    # ── what plays ─────────────────────────────────────────────────────
    def play_stream(self, source: "PcmSource | MusicFeed") -> None:
        """Play a source (the room's own feed) or a host's shared feed. A
        room switching feeds while it plays (joining or leaving a group)
        cuts over at one timed moment on every speaker."""
        feed = source if isinstance(source, MusicFeed) else MusicFeed(self.room_id, source)
        after: int | None = None
        if self.feed is not feed:
            if self.feed is not None and self.next_index is not None:
                after = self._at(None)
                self._flush(after)
                self.next_index = None
                for msg in self._speech_spans(after):
                    self._broadcast(msg)
            self._leave_feed()
            self.feed = feed
            feed.readers.add(self)
        self.paused = False
        self._start(after_us=after)

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
        span = self.play_at(start), self.play_at(start + len(a))
        self._broadcast(P.speech_msg(*span))
        return span

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
        self._leave_feed()
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
        if self.feed is not None:
            self.feed.drop_from(max(0, self.index_at(at)))
        return at

    def resume(self) -> None:
        self.paused = False
        if self.music is not None:
            self._start()

    # ── music-only controls (slice 5b): sounds mixed in play on ──────
    def _speech_spans(self, from_us: float | None = None) -> list[dict[str, Any]]:
        """``speech`` messages for the sounds still to end (from ``from_us``)."""
        now = self.clock() if from_us is None else from_us
        out = []
        for s, a in self._speech:
            start, end = self.play_at(s), self.play_at(s + len(a))
            if end > now:
                out.append(P.speech_msg(max(start, now), end))
        return out

    def _rerender_from(self, at: int) -> None:
        """After a flush at ``at``: send the timeline again from there, so
        a sound that was mixed into the flushed chunks (an answer, a timer)
        plays on without the music it was mixed with. The flush cut its
        ``speech`` span on the speakers, so the span is sent again."""
        if self.next_index is None:
            return
        from_index = max(0, self.index_at(at))
        speech_left = any(s + len(a) > from_index for s, a in self._speech)
        if speech_left or (self.music is not None and not self.paused):
            self.next_index = min(self.next_index, from_index)
        else:
            self.next_index = None
        for msg in self._speech_spans(at):
            self._broadcast(msg)

    def stop_music(self, at_us: float | None = None) -> int:
        """The room's music ends at ``at_us`` on every speaker; a sound
        mixed over it plays on."""
        at = self._at(at_us)
        self._leave_feed()      # other rooms on the same host play on
        self.paused = False
        self._flush(at)
        self._rerender_from(at)
        return at

    def pause_music(self, at_us: float | None = None) -> int:
        """Like :meth:`stop_music`, but the source stays attached (drained,
        not played) until :meth:`resume_music`."""
        at = self._at(at_us)
        self.paused = True
        self._flush(at)
        if self.feed is not None:
            self.feed.drop_from(max(0, self.index_at(at)))
        self._rerender_from(at)
        return at

    def resume_music(self, start_index: int | None = None) -> bool:
        """Play the attached source again from what it decodes now (from
        ``start_index`` when given: the one start of every room of a
        group). False when there is nothing attached."""
        if self.feed is None:
            return False
        if self.paused:
            self.feed.drain()
        self.paused = False
        self._start(start_index)
        return True

    def skip_music(self, at_us: float | None = None) -> int:
        """The song changed (a skip, a jump in the queue): what is already
        sent of the old one stops at ``at_us`` on every speaker, and the
        timeline carries on from there with the new one."""
        at = self._at(at_us)
        self._flush(at)
        if self.feed is not None:
            self.feed.drain()
            self.feed.drop_from(max(0, self.index_at(at)))
        self._rerender_from(at)
        return at

    def duck(self, gain: float, at_us: float | None = None, ramp_ms: int = 50) -> int:
        at = self._at(at_us)
        self._broadcast(P.gain_msg(at, max(0.0, min(1.0, gain)), ramp_ms))
        return at

    # ── speakers ───────────────────────────────────────────────────────
    def subscribe(self, sink: Sink) -> None:
        sink.send_control(P.timeline_msg(self.timeline_id, self.t0_us))
        for msg in self._speech_spans():
            sink.send_control(msg)
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
        if self.feed is not None:
            if self.paused:
                self.feed.keep_drained()
            else:
                data, status = self.feed.chunk(index)
                if data:
                    m = np.frombuffer(data, dtype="<i2").reshape(-1, 2)
                    out[: len(m)] = m
                    playing = True
                if status == "end":
                    self._leave_feed()
                elif status != "ok":
                    playing = True
                    if status == "short":
                        self.underruns += 1
        end = index + P.CHUNK_FRAMES
        # Kept until it has PLAYED (not just been sent): a flush can ask
        # for the chunks after it again (`_rerender_from`).
        played = self.index_at(self.clock()) - P.CHUNK_FRAMES
        self._speech = [(s, a) for s, a in self._speech if s + len(a) > played]
        mixed = False
        for start, a in self._speech:
            lo, hi = max(start, index), min(start + len(a), end)
            if hi <= lo:
                continue
            if not mixed:
                out *= MUSIC_UNDER_SPEECH
                mixed = True
            out[lo - index: hi - index] += a[lo - start: hi - start]
        if not playing and not any(s + len(a) > index for s, a in self._speech):
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
