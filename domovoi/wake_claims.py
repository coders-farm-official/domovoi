"""One turn per wake word, from the best microphone (rooms slice 3, 1.5.0).

Several satellites in a room (or in rooms linked as hearing each other,
``room_hear_links``) hear one "hey Domovoi". The wire contract is in
docs/uml/satellite-protocol.md, "One turn per wake word"; in short:

* the FIRST ``wake_claim`` of a group owns the turn, answered at once
  (``wake_grant`` role owner) — no window, no wait;
* later claims while the turn captures are quiet: no chime, and up to
  :data:`STREAM_MS` of the command streamed (within :data:`JOIN_WINDOW_SEC`
  of the first claim; after it, or when the owner can't be compared, they
  stand down with ``stream_ms: 0``);
* each stream is scored on its first :data:`SCORE_FRAMES` frames: the
  90th-percentile frame level over that device's own pre-wake floor
  (:func:`mic_score`, spike 0a's ``adaptive_gate.mic_score``); one stream
  is picked, the rest are dropped unread and never stored;
* a claim while the group's answer plays is a barge-in: the answer stops on
  every speaker and the claim owns a new turn;
* an older device (no ``wake_claim``) whose capture starts while another
  device's turn is capturing in its group is a duplicate: dropped.

A room with one satellite and no links never sees any of this: its devices
are told ``room.claim: false`` and wake as before.

The arbiter holds sessions (``streaming.StreamSession``) but touches only a
few of their attributes, so the tests drive it with fakes.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from domovoi.endpointing import pcm_dbfs

log = logging.getLogger(__name__)

ABILITY = "wake_claim"
#: How much of the command a quiet listener streams.
STREAM_MS = 2000
#: Claims this soon after a turn's first claim join it as quiet streams.
JOIN_WINDOW_SEC = 1.5
FRAME_MS = 30
#: The score reads each stream's first 1.5 s; the pick is made as soon as
#: any stream has that much (or the owner's capture ends).
SCORE_FRAMES = 1500 // FRAME_MS
#: A stream with less than this by the pick is not scored.
MIN_SCORED_FRAMES = 300 // FRAME_MS
LEVEL_PERCENTILE = 90.0
#: How long a turn waits for a picked quiet stream's ``quiet_end``.
HANDOVER_WAIT_SEC = 15.0
#: A capture that never ended (its owner went quiet) stops holding the group.
STALE_CAPTURE_SEC = 40.0
#: Cap on a kept quiet stream (the owner's own cap is 60 s).
MAX_STREAM_BYTES = 60 * 16_000 * 2


# ── the score ─────────────────────────────────────────────────────────────


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile of the finite values (None if none)."""
    xs = sorted(v for v in values if not math.isinf(v) and not math.isnan(v))
    if not xs:
        return None
    k = min(len(xs) - 1, max(0, int(round(pct / 100.0 * (len(xs) - 1)))))
    return xs[k]


def mic_score(floor_dbfs: float | None, levels: list[float]) -> float | None:
    """The command's loudness over this device's own background, in dB:
    the 90th-percentile frame level of the first :data:`SCORE_FRAMES`
    frames minus the pre-wake floor the device reported. None when either
    is unknown or there is too little of the command."""
    head = levels[:SCORE_FRAMES]
    if floor_dbfs is None or len(head) < MIN_SCORED_FRAMES:
        return None
    level = percentile(head, LEVEL_PERCENTILE)
    return None if level is None else level - floor_dbfs


def clean_floor(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    v = float(raw)
    return v if -130.0 <= v <= 0.0 else None


def clean_claim_id(raw: Any) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return None
    return raw


# ── turns ─────────────────────────────────────────────────────────────────


@dataclass
class Stream:
    """One device's audio of a turn: the owner's capture (levels only, its
    PCM stays in its session) or a quiet stream (levels and PCM, in memory
    until the pick)."""

    session: Any
    claim: int | None
    floor_dbfs: float | None
    quiet: bool
    levels: list[float] = field(default_factory=list)
    pcm: bytearray = field(default_factory=bytearray)
    ended: bool = False
    dropped: bool = False
    done: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def satellite_id(self) -> str:
        return self.session.satellite_id

    def score(self) -> float | None:
        return mic_score(self.floor_dbfs, self.levels)


@dataclass
class Turn:
    id: str
    rooms: frozenset[str]
    owner: Any
    started: float
    comparable: bool
    streams: dict[str, Stream] = field(default_factory=dict)
    capturing: bool = True
    winner: str | None = None
    scores: dict[str, float | None] = field(default_factory=dict)

    @property
    def owner_stream(self) -> Stream:
        return self.streams[self.owner.satellite_id]


Sender = Callable[[Any, dict[str, Any]], Awaitable[None]]


async def _send(session: Any, frame: dict[str, Any]) -> None:
    await session._safe_send_text(frame)


class Arbiter:
    def __init__(self, registry: Any, *, clock: Callable[[], float] = time.monotonic,
                 send: Sender = _send,
                 cut_room_speech: Callable[[str], None] | None = None,
                 near_wake: Callable[[str, str], None] | None = None) -> None:
        self.registry = registry
        self.clock = clock
        self.send = send
        #: Stops the synced answer in a room (room playout), set by the app.
        self.cut_room_speech = cut_room_speech
        self.links: dict[str, set[str]] = {}
        #: Wakes heard in two unlinked rooms at once feed the Rooms page's
        #: "can hear each other" suggestion (domovoi/room_hear_suggestions.py).
        from domovoi.room_hear_suggestions import NearWakes

        self.near = NearWakes(near_wake or (lambda a, b: None))
        self.turns: list[Turn] = []
        self._ids = itertools.count(1)

    # ── groups ─────────────────────────────────────────────────────────
    def set_links(self, pairs: list[tuple[str, str]]) -> None:
        links: dict[str, set[str]] = {}
        for a, b in pairs:
            links.setdefault(a, set()).add(b)
            links.setdefault(b, set()).add(a)
        self.links = links

    async def refresh_links(self) -> None:
        from sqlalchemy import text

        from domovoi.db.session import session_scope

        async with session_scope() as s:
            rows = (await s.execute(text("SELECT room_a, room_b FROM room_hear_links"))).all()
        self.set_links([(r[0], r[1]) for r in rows])

    def group(self, room_id: str) -> frozenset[str]:
        """The room and the rooms marked as hearing it."""
        return frozenset({room_id, *self.links.get(room_id, ())})

    def heard_wake(self, room_id: str) -> None:
        """A wake word in ``room_id`` (claimed or not): another room's wake
        moments before, outside this group, counts toward a link suggestion."""
        self.near.heard(room_id, self.group(room_id), self.clock())

    def claim_flag(self, room_id: str) -> bool:
        """``room.claim``: another satellite is in the room or a linked one."""
        devices: set[str] = set()
        for r in self.group(room_id):
            devices.update(self.registry.devices_in(r))
        return len(devices) > 1

    # ── turn bookkeeping ───────────────────────────────────────────────
    def _live(self, session: Any) -> bool:
        sats = getattr(self.registry, "satellites", None)
        return sats is None or sats.get(session.satellite_id) is session

    def answering(self, turn: Turn) -> bool:
        """The turn's answer is still being worked out or still playing."""
        task = getattr(turn.owner, "_response_task", None)
        if task is not None and not task.done():
            return True
        return bool(getattr(turn.owner, "room_speech_playing", lambda: False)())

    def _prune(self) -> None:
        now = self.clock()
        keep = []
        for t in self.turns:
            if not self._live(t.owner):
                self._release(t)
                continue
            if t.capturing and now - t.started > STALE_CAPTURE_SEC:
                self._release(t)
                continue
            if not t.capturing and not self.answering(t) and not self._waiting(t):
                continue
            keep.append(t)
        self.turns = keep

    def _waiting(self, t: Turn) -> bool:
        """A picked quiet stream the turn still waits for."""
        s = t.streams.get(t.winner or "")
        return s is not None and s.quiet and not s.done.is_set()

    def _release(self, t: Turn) -> None:
        for s in t.streams.values():
            if s.quiet:
                s.pcm.clear()
                s.done.set()
                if getattr(s.session, "quiet_stream", None) is s:
                    s.session.quiet_stream = None

    def turn_of(self, room_id: str) -> Turn | None:
        self._prune()
        for t in self.turns:
            if room_id in t.rooms:
                return t
        return None

    def _new_turn(self, owner: Any, *, floor: float | None, claim: int | None,
                  comparable: bool) -> Turn:
        t = Turn(id=f"t{next(self._ids)}", rooms=self.group(owner.room_id), owner=owner,
                 started=self.clock(), comparable=comparable)
        t.streams[owner.satellite_id] = Stream(owner, claim, floor, quiet=False)
        self.turns.append(t)
        return t

    async def _barge(self, turn: Turn | None, rooms: frozenset[str]) -> None:
        """A new wake in a group whose sound is playing: the answer stops on
        every speaker, the previous turn ends."""
        if turn is not None:
            self.turns.remove(turn)
            self._release(turn)
            task = getattr(turn.owner, "_response_task", None)
            if task is not None and not task.done():
                task.cancel()
        sats = getattr(self.registry, "satellites", {}) or {}
        for sess in list(sats.values()):
            if sess.room_id in rooms:
                cut = getattr(sess, "_cut_announcement", None)
                if cut is not None:
                    cut()
                if hasattr(sess, "_room_play_end_us"):
                    sess._room_play_end_us = 0.0
        if self.cut_room_speech is not None:
            for r in rooms:
                try:
                    self.cut_room_speech(r)
                except Exception as e:  # noqa: BLE001 — a barge-in never fails a wake
                    log.warning("wake claims: cutting %s's answer failed: %s", r, e)

    # ── frames from the devices ────────────────────────────────────────
    async def on_claim(self, session: Any, ctrl: dict[str, Any]) -> dict[str, Any]:
        """A ``wake_claim``: answered at once. Returns the grant sent."""
        claim = clean_claim_id(ctrl.get("claim"))
        floor = clean_floor(ctrl.get("floor_dbfs"))
        self.heard_wake(session.room_id)
        t = self.turn_of(session.room_id)
        if t is not None and t.capturing and t.owner is not session:
            stream_ms = 0
            if (t.comparable and t.winner is None
                    and self.clock() - t.started <= JOIN_WINDOW_SEC
                    and session.satellite_id not in t.streams):
                stream_ms = STREAM_MS
                s = Stream(session, claim, floor, quiet=True)
                t.streams[session.satellite_id] = s
                session.quiet_stream = s
            grant = {"type": "wake_grant", "claim": claim, "turn": t.id,
                     "role": "quiet", "stream_ms": stream_ms}
            await self.send(session, grant)
            return grant
        rooms = self.group(session.room_id)
        # `barge_in: true`: the device heard the wake word over the room's
        # answer (its own, or the synced playout's `speech`); trusted as
        # well as this server's own view of what is playing.
        if t is not None or self._speaking(rooms) or ctrl.get("barge_in") is True:
            await self._barge(t, rooms)
        t = self._new_turn(session, floor=floor, claim=claim, comparable=floor is not None)
        session.owner_claim = claim
        grant = {"type": "wake_grant", "claim": claim, "turn": t.id, "role": "owner"}
        await self.send(session, grant)
        log.info("wake claims: %s owns turn %s (rooms %s)", session.satellite_id, t.id,
                 ",".join(sorted(t.rooms)))
        return grant

    def _speaking(self, rooms: frozenset[str]) -> bool:
        sats = getattr(self.registry, "satellites", {}) or {}
        for sess in sats.values():
            if sess.room_id in rooms and bool(
                getattr(sess, "room_speech_playing", lambda: False)()
            ):
                return True
        return False

    async def on_capture_start(self, session: Any, ctrl: dict[str, Any]) -> bool:
        """An ``utterance_start``. False drops the capture: a duplicate of a
        turn another device owns (an older device, or a claim answered
        quiet after the device failed open)."""
        trigger = ctrl.get("trigger")
        if trigger == "wake_word" and clean_claim_id(ctrl.get("claim")) is None:
            self.heard_wake(session.room_id)  # a device that doesn't claim
        t = self.turn_of(session.room_id)
        if t is not None and t.owner is session:
            if t.capturing or trigger not in ("wake_word", "barge_in"):
                return True  # its own turn's capture, or its follow-up
            # The owner woke again after its turn: a new turn.
            return await self._owner_without_claim(session, ctrl, previous=t)
        if t is not None and t.capturing:
            if trigger in ("followup", "push_to_talk"):
                return True
            log.info("wake claims: dropped %s's capture, a duplicate of turn %s (owner %s)",
                     session.satellite_id, t.id, t.owner.satellite_id)
            return False
        if clean_claim_id(ctrl.get("claim")) is not None:
            return True  # a claim whose turn is gone (pruned): carry on
        return await self._owner_without_claim(session, ctrl, previous=t)

    async def _owner_without_claim(self, session: Any, ctrl: dict[str, Any], *,
                                   previous: Turn | None = None) -> bool:
        """A capture that claimed nothing (an older device, or any device in
        a room whose ``claim`` is false): in a shared group it owns a turn
        nobody can be compared with, so duplicates of it are dropped."""
        if ctrl.get("trigger") not in ("wake_word", "barge_in"):
            return True
        rooms = self.group(session.room_id)
        if previous is not None or (ctrl.get("trigger") == "barge_in" and self._speaking(rooms)):
            await self._barge(previous, rooms)
        if self.claim_flag(session.room_id):
            self._new_turn(session, floor=None, claim=None, comparable=False)
        return True

    async def on_barge_in(self, session: Any) -> None:
        """A ``barge_in`` frame (an older device): the group's answer stops
        on every speaker, whichever device's turn it was."""
        t = self.turn_of(session.room_id)
        if t is not None and t.capturing:
            return
        rooms = self.group(session.room_id)
        if t is not None or self._speaking(rooms):
            await self._barge(t, rooms)

    async def on_owner_audio(self, session: Any, data: bytes) -> None:
        t = self._owned(session)
        if t is None or t.winner is not None:
            return
        s = t.owner_stream
        s.levels.append(pcm_dbfs(data))
        if len(s.levels) >= SCORE_FRAMES and len(t.streams) > 1:
            await self.decide(t)

    async def on_quiet_audio(self, session: Any, data: bytes) -> None:
        s: Stream | None = getattr(session, "quiet_stream", None)
        if s is None or s.dropped:
            return
        t = self._turn_with(s)
        if t is None:
            return
        if t.winner is None:
            s.levels.append(pcm_dbfs(data))
        if len(s.pcm) + len(data) <= MAX_STREAM_BYTES:
            s.pcm.extend(data)
        if t.winner is None and len(s.levels) >= SCORE_FRAMES:
            await self.decide(t)

    def on_quiet_end(self, session: Any, ctrl: dict[str, Any]) -> None:
        s: Stream | None = getattr(session, "quiet_stream", None)
        if s is None:
            return
        s.ended = True
        session.quiet_stream = None
        if s.dropped:
            s.pcm.clear()
        s.done.set()

    async def on_owner_end(self, session: Any) -> "Handover | None":
        """The owner's ``utterance_end``. None: transcribe the owner's own
        capture (as today). Else the picked quiet stream, to wait for."""
        t = self._owned(session)
        if t is None:
            return None
        if t.winner is None and len(t.streams) > 1:
            await self.decide(t)
        t.capturing = False
        if t.winner is None or t.winner == session.satellite_id:
            return None
        return Handover(t, t.streams[t.winner])

    def forget(self, session: Any) -> None:
        """A socket closed: its quiet stream ends; a turn it owned goes."""
        s = getattr(session, "quiet_stream", None)
        if s is not None:
            s.ended = True
            s.done.set()
        for t in list(self.turns):
            if t.owner is session:
                self.turns.remove(t)
                self._release(t)

    # ── the pick ───────────────────────────────────────────────────────
    async def decide(self, t: Turn) -> str:
        """Pick the stream the turn is transcribed from, and tell every
        quiet one whether to keep going. Dropped streams are cleared here:
        their audio is never transcribed or stored."""
        if t.winner is not None:
            return t.winner
        owner_id = t.owner.satellite_id
        scores = {sid: s.score() for sid, s in t.streams.items()}
        best, best_score = owner_id, scores.get(owner_id)
        if best_score is not None:
            for sid, sc in scores.items():
                if sc is not None and sc > best_score and not t.streams[sid].ended:
                    best, best_score = sid, sc
        t.winner = best
        t.scores = scores
        log.info("wake claims: turn %s heard best by %s (%s)", t.id, best,
                 ", ".join(f"{k} {'-' if v is None else f'{v:.1f} dB'}" for k, v in sorted(scores.items())))
        for sid, s in t.streams.items():
            if not s.quiet:
                continue
            if sid == best:
                await self.send(s.session, {"type": "capture_keep", "claim": s.claim, "turn": t.id})
            else:
                s.dropped = True
                s.pcm.clear()
                s.levels.clear()
                await self.send(s.session, {"type": "end_capture", "claim": s.claim})
        if best != owner_id:
            moved = getattr(t.owner, "mic_moved", None)
            if moved is not None:
                moved()
        return best

    def _owned(self, session: Any) -> Turn | None:
        for t in self.turns:
            if t.owner is session and t.capturing:
                return t
        return None

    def _turn_with(self, s: Stream) -> Turn | None:
        for t in self.turns:
            if t.streams.get(s.satellite_id) is s:
                return t
        return None


@dataclass
class Handover:
    """The picked quiet stream: the turn waits for its end, then takes it."""

    turn: Turn
    stream: Stream

    @property
    def satellite_id(self) -> str:
        return self.stream.satellite_id

    async def pcm(self, timeout: float = HANDOVER_WAIT_SEC) -> bytes:
        try:
            await asyncio.wait_for(self.stream.done.wait(), timeout)
        except asyncio.TimeoutError:
            log.warning("wake claims: %s's kept stream never ended; using what came",
                        self.satellite_id)
        pcm = bytes(self.stream.pcm)
        self.stream.pcm.clear()
        return pcm


def arbiter(app: Any) -> Arbiter:
    """The app's arbiter, created on first use."""
    arb = getattr(app.state, "wake_claims", None)
    if not isinstance(arb, Arbiter):
        from domovoi import room_devices

        from domovoi.room_hear_suggestions import persist_in_background

        arb = Arbiter(room_devices.registry(app),
                      cut_room_speech=lambda room: _cut_playout_speech(app, room),
                      near_wake=persist_in_background)
        app.state.wake_claims = arb
    return arb


def _cut_playout_speech(app: Any, room_id: str) -> None:
    pl = getattr(app.state, "room_playout", None)
    if pl is not None and room_id in pl.streamers:
        pl.cut_speech(room_id)
