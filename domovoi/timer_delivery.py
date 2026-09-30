"""House-wide delivery of timers and reminders (owner decision 2026-09-30).

When a timer or a reminder goes off, EVERY online satellite announces it —
the room it was set in, and every other room that has not turned on "Only
reminders for this device" (a row in V017's ``timer_own_only_rooms``). The
phone and the dashboard notify from the same record. Plain timers follow
the same rule as reminders.

Before this, a due timer was deleted and spoken once, in its own room, if
that room was connected and idle at that instant; a room mid-reply, a
satellite still reconnecting after a core restart, or a reminder set from
the app with no room meant nobody heard it and nothing said so.

The pieces:

* **The ledger** (:class:`FireLedger`, V017). Each watcher tick moves every
  due ``timers`` row into ``timer_fires`` in ONE transaction, with one
  ``timer_fire_deliveries`` row per room it is going to. Every later step
  (a room's announcement starting, ending, being skipped) is a short
  transaction of its own, and every write NOTIFYs ``timer_fires_changed``
  for the web. Without V017 the core logs one warning and keeps the same
  state in memory (:class:`MemoryFireLedger`): a missing migration never
  stops a timer from firing.

* **The coordinator** (:class:`TimerDelivery`). One task per (fire, room).
  A busy room is waited for, never talked over (``StreamSession.
  announce_block``): a reply, a capture, a call, a wake-word recording, an
  announcement already playing. The softer holds (just connected, a
  follow-up question pending, the reply just finished) are honoured for
  ``timer_announce_busy_wait_sec``; after ``timer_announce_max_wait_sec`` of
  continuous hard-busy the room is skipped (``busy_timeout``). A room that
  is offline, or reconnects, gets the fire if it (re)connects within
  ``timer_offline_grace_sec`` — counted from when the server accepts
  satellites again after a restart, so a timer due while the core was down
  is announced once the rooms are back.

* **Never twice in one room.** A room's row is claimed (pending → sending)
  before a single frame is sent, a late joiner is added with ON CONFLICT DO
  NOTHING, and only an announcement that never reached the satellite
  (``AnnounceNotStarted``) goes back to pending.

The wording (:func:`fire_line`): the origin room hears today's line ("Your
10 minute timer is done." / "Reminder: call mom"); every other room hears
where it came from ("From the garage: Your 10 minute timer is done." /
"Reminder from the garage: call mom"), and a line spoken 90 s or more late
says so.

Reminder words are household speech: they are stored in the database only
(``base_text`` / ``spoken_text``), and none of the log lines added here
carries them. The two pre-existing "timer fired" lines are kept exactly —
the owner's journal greps rely on them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.config import settings
from domovoi.db.repositories import (
    TIMER_FIRES_CHANGED_CHANNEL,
    TimerRepository,
    notify_timers_changed,
    utcnow,
)
from domovoi.db.session import session_scope
from domovoi.events import EVENTS, EventBus
from domovoi.workers.timer_watcher import _duration_adjective, _timer_subject

log = logging.getLogger(__name__)
# The two "timer fired" lines keep the watcher's logger name: the owner's
# journal greps and the existing tests key on it.
_FIRED_LOG = logging.getLogger("domovoi.workers.timer_watcher")

# A line spoken this long after its due time says how late it is.
LATE_AFTER_SEC = 90.0
# Fires younger than this when the core restarts are picked up again.
RESUME_WITHIN_SEC = 600.0
# "Stop the timer" this soon after a fire was spoken in the room
# acknowledges it instead of cancelling anything.
ACK_WITHIN_SEC = 30
# A first-sentence TTS failure is retried after this long, at most this
# many attempts in all.
TTS_RETRY_SEC = 5.0
TTS_MAX_ATTEMPTS = 3
# While V017 is missing the core looks for it again this often.
PROBE_RETRY_SEC = 600.0
# The retention prune runs at most this often.
PRUNE_EVERY_SEC = 3600.0

MISSING_V017_WARNING = (
    "timer_fires missing — run Flyway (V017); timers still fire but nothing is recorded"
)

HEARD_OUTCOMES = ("spoken", "interrupted")
LIVE_OUTCOMES = ("pending", "sending")
TERMINAL_OUTCOMES = frozenset(
    {"spoken", "interrupted", "failed", "offline", "busy_timeout", "cancelled"}
)
_WARN_OUTCOMES = frozenset({"offline", "busy_timeout", "failed"})

# (room_id, is_origin, outcome, detail)
DeliveryRow = tuple[str, bool, str, "str | None"]
# (room_id, is_origin, detail) — what targets_for hands pop_due
TargetRow = tuple[str, bool, "str | None"]


class AnnounceNotStarted(RuntimeError):
    """``StreamSession.announce`` sent nothing to the satellite: the room
    started a turn (``reason='responding'``) or the first sentence would not
    synthesize (``reason='tts_failed'``). Safe to try again."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class AnnounceInterrupted(RuntimeError):
    """The announcement had started playing when the room's own capture
    (a wake word, a barge-in) cut it off. Counts as heard; never retried."""


# ─── Wording ─────────────────────────────────────────────────────────────


def spoken_room(room_id: str) -> str:
    """A room id as it is said aloud: ``living-room`` → ``living room``."""
    return room_id.replace("-", " ").replace("_", " ").strip()


def is_target(room_id: str, origin_room_id: str | None, own_only: set[str]) -> bool:
    """Whether ``room_id`` announces a fire set in ``origin_room_id``: its
    own always, anybody's unless the room turned on "Only reminders for
    this device"."""
    return room_id == origin_room_id or room_id not in own_only


def lateness_phrase(late_sec: float) -> str | None:
    """``None`` under 90 s late; else "N minute(s) ago" below an hour and
    "N hour(s) ago" from there (both rounded down, never "0 minutes")."""
    if late_sec < LATE_AFTER_SEC:
        return None
    if late_sec < 3600:
        minutes = max(1, int(late_sec // 60))
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = int(late_sec // 3600)
    return f"{hours} hour{'s' if hours != 1 else ''} ago"


def fire_line(
    kind: str,
    *,
    label: str | None,
    message: str | None,
    duration_sec: int,
    origin_room_id: str | None,
    target_room_id: str,
    late_sec: float,
) -> str:
    """What ``target_room_id`` says when a timer or reminder set in
    ``origin_room_id`` goes off (contract §3.1 R2).

    The origin room — and every room, for one set with no room — hears
    today's line; any other room hears the room it came from first. From
    90 s late the line says how late it is."""
    late = lateness_phrase(late_sec)
    elsewhere = origin_room_id is not None and target_room_id != origin_room_id
    origin = spoken_room(origin_room_id) if elsewhere and origin_room_id else ""
    if kind == "reminder":
        words = message or ""
        if not words.strip():
            adjective = _duration_adjective(duration_sec)
            core = f"Here's your {adjective} reminder" if adjective else "Here's your reminder"
            line = f"{core}, from {late}." if late else f"{core}."
            return f"From the {origin}: {line}" if elsewhere else line
        if elsewhere:
            if late:
                return f"Reminder from the {origin}, {late}: {words}"
            return f"Reminder from the {origin}: {words}"
        return f"Reminder from {late}: {words}" if late else f"Reminder: {words}"
    subject = _timer_subject(label, duration_sec)
    line = f"{subject} went off {late}." if late else f"{subject} is done."
    return f"From the {origin}: {line}" if elsewhere else line


# ─── The ledger ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FireRecord:
    fire_id: int            # MemoryFireLedger: negative, decreasing from -1
    timer_id: int
    kind: str               # 'timer' | 'reminder'
    label: str | None
    message: str | None
    origin_room_id: str | None
    created_at: datetime
    due_at: datetime
    fired_at: datetime
    base_text: str

    @property
    def duration_sec(self) -> int:
        return round((self.due_at - self.created_at).total_seconds())


def _kind(message: str | None) -> str:
    # Everywhere: a fire is a reminder iff it has a message, even an empty
    # one (forward compatibility with the reminder-parse fix).
    return "reminder" if message is not None else "timer"


def _base_text(
    label: str | None, message: str | None, origin: str | None,
    created_at: datetime, due_at: datetime,
) -> str:
    return fire_line(
        _kind(message), label=label, message=message,
        duration_sec=round((due_at - created_at).total_seconds()),
        origin_room_id=origin, target_room_id=origin or "", late_sec=0.0,
    )


async def _notify_fires(s: AsyncSession, payload: str) -> None:
    await s.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": TIMER_FIRES_CHANGED_CHANNEL, "payload": payload},
    )


async def _probe_v017() -> bool:
    """Whether all three V017 tables exist. Raises when the database is
    unreachable (the caller decides what that means)."""
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    "SELECT to_regclass('public.timer_fires') IS NOT NULL "
                    "AND to_regclass('public.timer_fire_deliveries') IS NOT NULL "
                    "AND to_regclass('public.timer_own_only_rooms') IS NOT NULL"
                )
            )
        ).scalar()
    return bool(row)


_FIRE_COLUMNS = (
    "id, timer_id, kind, label, message, origin_room_id, created_at, due_at, "
    "fired_at, base_text"
)


def _record(row: Any) -> FireRecord:
    return FireRecord(
        fire_id=int(row[0]), timer_id=int(row[1]), kind=row[2], label=row[3],
        message=row[4], origin_room_id=row[5], created_at=row[6], due_at=row[7],
        fired_at=row[8], base_text=row[9],
    )


class FireLedger:
    """The V017-backed ledger. Every method is its own short transaction;
    every write that changed something NOTIFYs ``timer_fires_changed``."""

    async def pop_due(
        self, targets_for: Callable[[str | None, set[str]], list[TargetRow]],
    ) -> list[tuple[FireRecord, list[DeliveryRow]]]:
        """Move every due timer into the ledger, atomically: the DELETE from
        ``timers`` and the INSERTs here commit together or not at all (a
        failure leaves the timers due for the next tick)."""
        try:
            async with session_scope() as s:
                popped = (
                    await s.execute(
                        text(
                            "DELETE FROM timers WHERE expires_at <= NOW() "
                            "RETURNING id, label, message, room_id, created_at, expires_at"
                        )
                    )
                ).all()
                if not popped:
                    return []
                # Due order: two fires for one room are announced in it one
                # after the other, earliest first.
                popped = sorted(popped, key=lambda r: (r[5], r[0]))
                own_only = {
                    r[0] for r in (
                        await s.execute(text("SELECT room_id FROM timer_own_only_rooms"))
                    ).all()
                }
                out: list[tuple[FireRecord, list[DeliveryRow]]] = []
                for tid, label, message, room_id, created_at, expires_at in popped:
                    base = _base_text(label, message, room_id, created_at, expires_at)
                    inserted = (
                        await s.execute(
                            text(
                                """
                                INSERT INTO timer_fires
                                    (timer_id, kind, label, message, origin_room_id,
                                     created_at, due_at, base_text)
                                VALUES (:tid, :kind, :label, :message, :origin,
                                        :created_at, :due_at, :base)
                                RETURNING id, fired_at
                                """
                            ),
                            {
                                "tid": int(tid), "kind": _kind(message), "label": label,
                                "message": message, "origin": room_id,
                                "created_at": created_at, "due_at": expires_at,
                                "base": base,
                            },
                        )
                    ).one()
                    rec = FireRecord(
                        fire_id=int(inserted[0]), timer_id=int(tid), kind=_kind(message),
                        label=label, message=message, origin_room_id=room_id,
                        created_at=created_at, due_at=expires_at, fired_at=inserted[1],
                        base_text=base,
                    )
                    rows: list[DeliveryRow] = []
                    for target, is_origin, detail in targets_for(room_id, own_only):
                        await s.execute(
                            text(
                                """
                                INSERT INTO timer_fire_deliveries
                                    (fire_id, room_id, is_origin, detail)
                                VALUES (:fid, :room, :origin, :detail)
                                """
                            ),
                            {"fid": rec.fire_id, "room": target, "origin": is_origin,
                             "detail": detail},
                        )
                        rows.append((target, is_origin, "pending", detail))
                    out.append((rec, rows))
                await notify_timers_changed(s, "fired")
                await _notify_fires(s, ",".join(str(r.fire_id) for r, _ in out))
                return out
        except Exception as e:
            log.warning(
                "timer fires: recording the due timers failed; they stay due "
                "for the next tick: %s", e,
            )
            raise

    async def own_only_rooms(self) -> set[str]:
        async with session_scope() as s:
            rows = (await s.execute(text("SELECT room_id FROM timer_own_only_rooms"))).all()
        return {r[0] for r in rows}

    async def _write(self, fire_id: int, sql: str, params: dict[str, Any]) -> bool:
        """Run one UPDATE/INSERT ... RETURNING 1; NOTIFY when it changed a row."""
        async with session_scope() as s:
            changed = (await s.execute(text(sql), params)).first() is not None
            if changed:
                await _notify_fires(s, str(fire_id))
        return changed

    async def add_target(self, fire_id: int, room_id: str, is_origin: bool) -> bool:
        return await self._write(
            fire_id,
            """
            INSERT INTO timer_fire_deliveries (fire_id, room_id, is_origin)
            VALUES (:f, :r, :o)
            ON CONFLICT (fire_id, room_id) DO NOTHING
            RETURNING 1
            """,
            {"f": fire_id, "r": room_id, "o": is_origin},
        )

    async def claim(self, fire_id: int, room_id: str) -> bool:
        return await self._write(
            fire_id,
            """
            UPDATE timer_fire_deliveries
               SET outcome = 'sending', started_at = now(), attempts = attempts + 1
             WHERE fire_id = :f AND room_id = :r AND outcome = 'pending'
            RETURNING 1
            """,
            {"f": fire_id, "r": room_id},
        )

    async def release(
        self, fire_id: int, room_id: str, detail: str, *, count_attempt: bool,
    ) -> None:
        # The claim counted an attempt; a release that must not count one
        # (a turn started under us) gives it back.
        await self._write(
            fire_id,
            """
            UPDATE timer_fire_deliveries
               SET outcome = 'pending', detail = :d, started_at = NULL,
                   attempts = CASE WHEN :count THEN attempts
                                   ELSE GREATEST(attempts - 1, 0) END
             WHERE fire_id = :f AND room_id = :r AND outcome = 'sending'
            RETURNING 1
            """,
            {"f": fire_id, "r": room_id, "d": detail, "count": count_attempt},
        )

    async def finish(
        self, fire_id: int, room_id: str, outcome: str, detail: str | None,
        spoken_text: str | None = None,
    ) -> bool:
        """Record a room's final outcome. Only a live row (pending or
        sending) changes: an outcome already recorded — a room acknowledged
        elsewhere, say — is never overwritten. True when it changed."""
        return await self._write(
            fire_id,
            """
            UPDATE timer_fire_deliveries
               SET outcome = :o, detail = :d, finished_at = now(),
                   spoken_text = COALESCE(:t, spoken_text)
             WHERE fire_id = :f AND room_id = :r AND outcome IN ('pending', 'sending')
            RETURNING 1
            """,
            {"f": fire_id, "r": room_id, "o": outcome, "d": detail, "t": spoken_text},
        )

    async def note_detail(self, fire_id: int, room_id: str, detail: str) -> None:
        await self._write(
            fire_id,
            """
            UPDATE timer_fire_deliveries SET detail = :d
             WHERE fire_id = :f AND room_id = :r AND outcome = 'pending'
               AND detail IS DISTINCT FROM :d
            RETURNING 1
            """,
            {"f": fire_id, "r": room_id, "d": detail},
        )

    async def rows(self, fire_id: int) -> list[DeliveryRow]:
        async with session_scope() as s:
            result = (
                await s.execute(
                    text(
                        """
                        SELECT room_id, is_origin, outcome, detail
                          FROM timer_fire_deliveries
                         WHERE fire_id = :f
                         ORDER BY is_origin DESC, room_id
                        """
                    ),
                    {"f": fire_id},
                )
            ).all()
        return [(r[0], bool(r[1]), r[2], r[3]) for r in result]

    async def settle(self, fire_id: int) -> str | None:
        """Mark the fire settled; returns who acknowledged it (or None)."""
        async with session_scope() as s:
            row = (
                await s.execute(
                    text(
                        """
                        UPDATE timer_fires SET settled_at = COALESCE(settled_at, now())
                         WHERE id = :f
                        RETURNING acked_by
                        """
                    ),
                    {"f": fire_id},
                )
            ).first()
            await _notify_fires(s, str(fire_id))
        return row[0] if row is not None else None

    async def unsettled(self) -> list[tuple[FireRecord, list[DeliveryRow]]]:
        async with session_scope() as s:
            fires = (
                await s.execute(
                    text(
                        f"SELECT {_FIRE_COLUMNS} FROM timer_fires "
                        "WHERE settled_at IS NULL ORDER BY fired_at, id"
                    )
                )
            ).all()
        out = []
        for row in fires:
            rec = _record(row)
            out.append((rec, await self.rows(rec.fire_id)))
        return out

    async def prune(self, days: int) -> int:
        async with session_scope() as s:
            n = len(
                (
                    await s.execute(
                        text(
                            "DELETE FROM timer_fires "
                            "WHERE fired_at < now() - make_interval(days => :d) "
                            "RETURNING 1"
                        ),
                        {"d": int(days)},
                    )
                ).all()
            )
            if n:
                await _notify_fires(s, "pruned")
        return n


class MemoryFireLedger:
    """The same ledger in memory, for a database without V017: timers still
    fire and fan out, nothing is recorded, nothing is NOTIFYed on
    ``timer_fires_changed`` (``timers_changed`` still is, by the pop)."""

    def __init__(self) -> None:
        self._next_id = -1
        self._fires: dict[int, FireRecord] = {}
        self._rows: dict[int, dict[str, dict[str, Any]]] = {}
        self._settled: set[int] = set()

    async def _pop_expired(
        self,
    ) -> list[tuple[int, str | None, str | None, str | None, datetime, datetime]]:
        async with session_scope() as s:
            return await TimerRepository(s).pop_expired()

    async def pop_due(
        self, targets_for: Callable[[str | None, set[str]], list[TargetRow]],
    ) -> list[tuple[FireRecord, list[DeliveryRow]]]:
        popped = sorted(await self._pop_expired(), key=lambda r: (r[5], r[0]))
        own_only = await self.own_only_rooms()
        fired_at = utcnow()
        out: list[tuple[FireRecord, list[DeliveryRow]]] = []
        for tid, label, message, room_id, created_at, expires_at in popped:
            fid = self._next_id
            self._next_id -= 1
            rec = FireRecord(
                fire_id=fid, timer_id=int(tid), kind=_kind(message), label=label,
                message=message, origin_room_id=room_id, created_at=created_at,
                due_at=expires_at, fired_at=fired_at,
                base_text=_base_text(label, message, room_id, created_at, expires_at),
            )
            self._fires[fid] = rec
            rows = self._rows.setdefault(fid, {})
            for target, is_origin, detail in targets_for(room_id, own_only):
                rows[target] = {"is_origin": is_origin, "outcome": "pending",
                                "detail": detail, "attempts": 0, "spoken_text": None}
            out.append((rec, self._rows_of(fid)))
        return out

    async def own_only_rooms(self) -> set[str]:
        return set()

    def _rows_of(self, fire_id: int) -> list[DeliveryRow]:
        rows = self._rows.get(fire_id, {})
        ordered = sorted(rows.items(), key=lambda kv: (not kv[1]["is_origin"], kv[0]))
        return [(room, r["is_origin"], r["outcome"], r["detail"]) for room, r in ordered]

    async def add_target(self, fire_id: int, room_id: str, is_origin: bool) -> bool:
        rows = self._rows.setdefault(fire_id, {})
        if room_id in rows:
            return False
        rows[room_id] = {"is_origin": is_origin, "outcome": "pending", "detail": None,
                         "attempts": 0, "spoken_text": None}
        return True

    async def claim(self, fire_id: int, room_id: str) -> bool:
        row = self._rows.get(fire_id, {}).get(room_id)
        if row is None or row["outcome"] != "pending":
            return False
        row["outcome"] = "sending"
        row["attempts"] += 1
        return True

    async def release(
        self, fire_id: int, room_id: str, detail: str, *, count_attempt: bool,
    ) -> None:
        row = self._rows.get(fire_id, {}).get(room_id)
        if row is None or row["outcome"] != "sending":
            return
        row["outcome"] = "pending"
        row["detail"] = detail
        if not count_attempt:
            row["attempts"] = max(0, row["attempts"] - 1)

    async def finish(
        self, fire_id: int, room_id: str, outcome: str, detail: str | None,
        spoken_text: str | None = None,
    ) -> bool:
        row = self._rows.get(fire_id, {}).get(room_id)
        if row is None or row["outcome"] not in LIVE_OUTCOMES:
            return False
        row["outcome"] = outcome
        row["detail"] = detail
        if spoken_text is not None:
            row["spoken_text"] = spoken_text
        return True

    async def note_detail(self, fire_id: int, room_id: str, detail: str) -> None:
        row = self._rows.get(fire_id, {}).get(room_id)
        if row is not None and row["outcome"] == "pending":
            row["detail"] = detail

    async def rows(self, fire_id: int) -> list[DeliveryRow]:
        return self._rows_of(fire_id)

    async def settle(self, fire_id: int) -> str | None:
        self._settled.add(fire_id)
        return None

    async def unsettled(self) -> list[tuple[FireRecord, list[DeliveryRow]]]:
        return [
            (rec, self._rows_of(fid))
            for fid, rec in self._fires.items() if fid not in self._settled
        ]

    async def prune(self, days: int) -> int:
        cutoff = utcnow().timestamp() - int(days) * 86400
        old = [fid for fid, rec in self._fires.items() if rec.fired_at.timestamp() < cutoff]
        for fid in old:
            self._fires.pop(fid, None)
            self._rows.pop(fid, None)
            self._settled.discard(fid)
        return len(old)


# ─── "Stop the timer" right after one went off ───────────────────────────


async def ack_recent_fire(
    session: AsyncSession, room_id: str, within_sec: int = ACK_WITHIN_SEC,
) -> int | None:
    """If a fire was spoken in ``room_id`` in the last ``within_sec``
    seconds and nobody acknowledged it yet, acknowledge it: stamp
    ``acked_at``/``acked_by``, and cancel its announcements still waiting
    in other rooms. Returns that fire's id, or None (nothing recent, or
    V017 missing). Runs on the caller's session and transaction, so the
    NOTIFY goes out with the caller's commit.

    Why: "stop the timer" said right after the garage's timer was
    announced in the kitchen means "I heard it", not "delete the kitchen's
    own pasta timer" — which is what it used to do."""
    present = (
        await session.execute(
            text(
                "SELECT to_regclass('public.timer_fires') IS NOT NULL "
                "AND to_regclass('public.timer_fire_deliveries') IS NOT NULL"
            )
        )
    ).scalar()
    if not present:
        return None
    row = (
        await session.execute(
            text(
                """
                SELECT f.id
                  FROM timer_fires f
                  JOIN timer_fire_deliveries d ON d.fire_id = f.id
                 WHERE d.room_id = :room
                   AND d.outcome IN ('sending', 'spoken', 'interrupted')
                   AND COALESCE(d.finished_at, d.started_at)
                       >= now() - make_interval(secs => :within)
                   AND f.acked_at IS NULL
                 ORDER BY f.fired_at DESC, f.id DESC
                 LIMIT 1
                """
            ),
            {"room": room_id, "within": float(within_sec)},
        )
    ).first()
    if row is None:
        return None
    fire_id = int(row[0])
    acked = (
        await session.execute(
            text(
                """
                UPDATE timer_fires SET acked_at = now(), acked_by = :room
                 WHERE id = :f AND acked_at IS NULL
                RETURNING 1
                """
            ),
            {"f": fire_id, "room": room_id},
        )
    ).first()
    if acked is None:
        return None
    await session.execute(
        text(
            """
            UPDATE timer_fire_deliveries
               SET outcome = 'cancelled', detail = :detail, finished_at = now()
             WHERE fire_id = :f AND outcome = 'pending'
            """
        ),
        {"f": fire_id, "detail": f"acknowledged:{room_id}"},
    )
    await _notify_fires(session, str(fire_id))
    log.info("timer fire %d acknowledged in room=%s", fire_id, room_id)
    return fire_id


# ─── The coordinator ─────────────────────────────────────────────────────


@dataclass
class _Fire:
    rec: FireRecord
    ledger: Any
    fired_mono: float
    targets: list[str]


def _iso(value: datetime) -> str:
    return value.isoformat()


class TimerDelivery:
    """Announces every fire in every room it is for; see the module
    docstring. ``app.state.timer_delivery``, built by main.py; the
    TimerWatcher's tick is :meth:`tick`."""

    def __init__(
        self,
        app: Any,
        ledger_factory: Callable[[], Any] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], Any] = asyncio.sleep,
        poll_sec: float = 0.25,
        events: EventBus | None = None,
    ) -> None:
        self.app = app
        self._ledger_factory = ledger_factory
        self._clock = clock
        self._wall = wall
        self._sleep = sleep
        self.poll_sec = poll_sec
        self._events = events if events is not None else EVENTS
        self._ledger: Any = None
        self._probed_at: float | None = None
        self._warned_missing = False
        self._resumed = False
        # With no app there is no server to wait for: the grace clock runs
        # from the start.
        self._accepting_since: float | None = clock() if app is None else None
        self._fires: dict[int, _Fire] = {}
        self._tasks: dict[tuple[int, str], asyncio.Task[None]] = {}
        # One delivery at a time per room, in the order they were started
        # (due order within a tick): the next one then waits out the
        # previous one's playback like any other busy moment.
        self._room_locks: dict[str, asyncio.Lock] = {}
        self._noted: dict[tuple[int, str], str] = {}
        self._aux: set[asyncio.Task[None]] = set()
        self._snapshot: set[str] = set()
        self._last_prune: float | None = None
        self._closed = False

    # ── wiring ──────────────────────────────────────────────────────────

    def _sessions(self) -> dict[str, Any]:
        state = getattr(self.app, "state", None) if self.app is not None else None
        sessions = getattr(state, "active_sessions", None)
        return sessions if sessions is not None else {}

    def set_accepting(self) -> None:
        """The server accepts satellite sockets from now on (main.py calls
        this right before the lifespan yields). Grace windows count from
        here: a fire popped during startup waits for rooms to reconnect."""
        if self._accepting_since is None:
            self._accepting_since = self._clock()

    def _deadline(self, fire: _Fire) -> float | None:
        if self._accepting_since is None:
            return None
        return max(fire.fired_mono, self._accepting_since) + float(
            settings.timer_offline_grace_sec
        )

    def _live(self, key: tuple[int, str]) -> bool:
        task = self._tasks.get(key)
        return task is not None and not task.done()

    async def ensure_ledger(self) -> Any:
        """The ledger to use: V017 when its tables exist, else the
        in-memory one (one warning, a fresh look every 10 minutes)."""
        if self._ledger_factory is not None:
            if self._ledger is None:
                self._ledger = self._ledger_factory()
            return self._ledger
        if isinstance(self._ledger, FireLedger):
            return self._ledger
        now = self._clock()
        if (
            self._ledger is not None
            and self._probed_at is not None
            and now - self._probed_at < PROBE_RETRY_SEC
        ):
            return self._ledger
        try:
            present = await _probe_v017()
        except Exception:
            if self._ledger is None:
                raise
            self._probed_at = now
            return self._ledger
        self._probed_at = now
        if present:
            if self._ledger is not None:
                log.info("timer_fires found (V017); recording timer fires from now on")
            self._ledger = FireLedger()
        else:
            if not self._warned_missing:
                log.warning(MISSING_V017_WARNING)
                self._warned_missing = True
            if self._ledger is None:
                self._ledger = MemoryFireLedger()
        return self._ledger

    def _targets_for(self, origin: str | None, own_only: set[str]) -> list[TargetRow]:
        """The rooms a fire goes to right now (R1): the origin always
        (``offline`` when it isn't connected), plus every connected room
        whose "Only reminders for this device" is off."""
        online = set(self._sessions().keys())
        self._snapshot = online
        out: list[TargetRow] = []
        if origin is not None:
            out.append((origin, True, None if origin in online else "offline"))
        for room in sorted(online):
            if room != origin and is_target(room, origin, own_only):
                out.append((room, False, None))
        return out

    # ── the tick ────────────────────────────────────────────────────────

    async def tick(self) -> int:
        """Pop and record every due timer, start its announcements, then
        sweep. Returns how many fired."""
        ledger = await self.ensure_ledger()
        if not self._resumed:
            self._resumed = True
            try:
                await self.resume_unsettled()
            except Exception as e:  # noqa: BLE001 — never block firing
                log.warning("timer fires: resuming announcements after a restart failed: %s", e)
        popped = await ledger.pop_due(self._targets_for)
        sessions = self._sessions()
        for rec, rows in popped:
            self._log_fired(rec)
            fire = _Fire(rec=rec, ledger=ledger, fired_mono=self._clock(),
                         targets=[r[0] for r in rows])
            self._fires[rec.fire_id] = fire
            self._emit_fired(fire)
            for room, _is_origin, outcome, _detail in rows:
                if outcome == "pending" and room in sessions:
                    self._start(fire, room)
        if popped:
            # A room that connected while the pop ran missed the snapshot.
            for room in set(sessions) - self._snapshot:
                self.on_room_connected(room)
        await self.sweep()
        return len(popped)

    def _log_fired(self, rec: FireRecord) -> None:
        descriptor = rec.label or f"id={rec.timer_id}"
        if rec.message is not None:
            _FIRED_LOG.info(
                "timer fired (reminder): %s room=%s message=%r",
                descriptor, rec.origin_room_id, rec.message,
            )
        else:
            _FIRED_LOG.info("timer fired: %s room=%s", descriptor, rec.origin_room_id)

    def _emit_fired(self, fire: _Fire) -> None:
        rec = fire.rec
        self._events.emit("core.timer_fired", {
            "fire_id": rec.fire_id,
            "timer_id": rec.timer_id,
            "kind": rec.kind,
            "label": rec.label,
            "message": rec.message,
            "origin_room_id": rec.origin_room_id,
            "due_at": _iso(rec.due_at),
            "fired_at": _iso(rec.fired_at),
            "text": rec.base_text,
            "targets": list(fire.targets),
        })

    # ── one room ────────────────────────────────────────────────────────

    def _start(self, fire: _Fire, room_id: str) -> bool:
        """At most one live task per (fire, room)."""
        key = (fire.rec.fire_id, room_id)
        if self._closed or self._live(key):
            return False
        task = asyncio.create_task(
            self._deliver(fire, room_id), name=f"timer-fire-{fire.rec.fire_id}-{room_id}",
        )
        self._tasks[key] = task
        task.add_done_callback(lambda t, key=key: self._task_done(key, t))
        return True

    def _task_done(self, key: tuple[int, str], task: asyncio.Task[None]) -> None:
        if self._tasks.get(key) is task:
            self._tasks.pop(key, None)
        if not task.cancelled() and task.exception() is not None:
            log.warning("timer fire %d room=%s: delivery task failed: %s",
                        key[0], key[1], task.exception())

    async def _note(self, fire: _Fire, room_id: str, reason: str) -> None:
        key = (fire.rec.fire_id, room_id)
        if self._noted.get(key) == reason:
            return
        self._noted[key] = reason
        await fire.ledger.note_detail(fire.rec.fire_id, room_id, reason)

    async def _finish(
        self, fire: _Fire, room_id: str, outcome: str, detail: str | None,
        spoken_text: str | None = None,
    ) -> None:
        self._noted.pop((fire.rec.fire_id, room_id), None)
        if await fire.ledger.finish(fire.rec.fire_id, room_id, outcome, detail, spoken_text):
            self._log_outcome(fire, room_id, outcome, detail)

    def _log_outcome(self, fire: _Fire, room_id: str, outcome: str, detail: str | None) -> None:
        # No message and no label: reminder words stay out of the journal.
        level = logging.WARNING if outcome in _WARN_OUTCOMES else logging.INFO
        log.log(
            level,
            "timer fire %d room=%s origin=%s outcome=%s detail=%s waited=%.1fs",
            fire.rec.fire_id, room_id, fire.rec.origin_room_id, outcome, detail,
            max(0.0, self._clock() - fire.fired_mono),
        )

    async def _deliver(self, fire: _Fire, room_id: str) -> None:
        """Wait until the room can take it, claim the room's row, speak.
        See contract §3.4 and R3/R6/R7."""
        lock = self._room_locks.setdefault(room_id, asyncio.Lock())
        async with lock:
            await self._deliver_locked(fire, room_id)

    async def _deliver_locked(self, fire: _Fire, room_id: str) -> None:
        rec, ledger = fire.rec, fire.ledger
        fid = rec.fire_id
        start = self._clock()
        soft_until = start + float(settings.timer_announce_busy_wait_sec)
        hard_until = start + float(settings.timer_announce_max_wait_sec)
        tts_failures = 0
        try:
            while True:
                forced: str | None = None
                sess = self._sessions().get(room_id)
                if sess is None:
                    # The row stays pending: a reconnect before the grace
                    # deadline starts this again (on_room_connected).
                    await self._note(fire, room_id, "offline")
                    return
                block_fn = getattr(sess, "announce_block", None)
                block = block_fn(self._clock()) if block_fn is not None else None
                now = self._clock()
                if block:
                    reason, hard = block
                    if now >= hard_until:
                        await self._finish(fire, room_id, "busy_timeout", reason)
                        return
                    if hard or now < soft_until:
                        await self._note(fire, room_id, reason)
                        await self._sleep(self.poll_sec)
                        continue
                    forced = reason
                if not await ledger.claim(fid, room_id):
                    return  # spoken, cancelled or acknowledged elsewhere
                self._noted.pop((fid, room_id), None)
                line = fire_line(
                    rec.kind, label=rec.label, message=rec.message,
                    duration_sec=rec.duration_sec, origin_room_id=rec.origin_room_id,
                    target_room_id=room_id,
                    late_sec=(self._wall() - rec.due_at).total_seconds(),
                )
                try:
                    await sess.announce(line)
                except AnnounceNotStarted as e:
                    if e.reason == "tts_failed":
                        tts_failures += 1
                        if tts_failures >= TTS_MAX_ATTEMPTS:
                            await self._finish(fire, room_id, "failed", "tts_failed")
                            return
                        await ledger.release(fid, room_id, "tts_failed", count_attempt=True)
                        self._noted[(fid, room_id)] = "tts_failed"
                        await self._sleep(TTS_RETRY_SEC)
                        continue
                    # A turn started between the check and the call: nothing
                    # was sent, wait for it like any other busy moment.
                    reason = e.reason if e.reason else "responding"
                    await ledger.release(fid, room_id, reason, count_attempt=False)
                    self._noted[(fid, room_id)] = reason
                    await self._sleep(self.poll_sec)
                    continue
                except AnnounceInterrupted:
                    await self._finish(fire, room_id, "interrupted", None, line)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — the socket died mid-send
                    log.debug("timer fire %d room=%s: announce failed: %s", fid, room_id, e)
                    await self._finish(fire, room_id, "failed", "send_failed", line)
                    return
                await self._finish(
                    fire, room_id, "spoken",
                    f"forced_over:{forced}" if forced else None, line,
                )
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — a ledger write failed
            log.warning("timer fire %d room=%s: delivery stopped: %s", fid, room_id, e)

    # ── reconnects, deadlines, restarts ─────────────────────────────────

    def on_room_connected(self, room_id: str) -> None:
        """A satellite just got `ready`. Non-blocking: schedules the check
        for every unsettled fire it should still hear."""
        if self._closed or not self._fires:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(
            self._room_connected(room_id), name=f"timer-fires-connected-{room_id}",
        )
        self._aux.add(task)
        task.add_done_callback(self._aux.discard)

    async def _room_connected(self, room_id: str) -> None:
        own_only: dict[int, set[str]] = {}
        for fire in list(self._fires.values()):
            deadline = self._deadline(fire)
            if deadline is not None and self._clock() >= deadline:
                continue
            fid = fire.rec.fire_id
            if self._live((fid, room_id)):
                continue
            try:
                rows = await fire.ledger.rows(fid)
                row = next((r for r in rows if r[0] == room_id), None)
                if row is not None:
                    if row[2] == "pending":
                        self._start(fire, room_id)
                    continue
                key = id(fire.ledger)
                if key not in own_only:
                    own_only[key] = await fire.ledger.own_only_rooms()
                if not is_target(room_id, fire.rec.origin_room_id, own_only[key]):
                    continue
                if await fire.ledger.add_target(
                    fid, room_id, room_id == fire.rec.origin_room_id,
                ):
                    self._start(fire, room_id)
            except Exception as e:  # noqa: BLE001
                log.warning("timer fire %d: adding room=%s failed: %s", fid, room_id, e)

    async def sweep(self) -> None:
        """Apply the grace deadlines (R5), settle what is finished, and
        prune old history once an hour."""
        now = self._clock()
        for fire in list(self._fires.values()):
            deadline = self._deadline(fire)
            if deadline is None or now < deadline:
                continue
            fid = fire.rec.fire_id
            try:
                busy = False
                for room, _is_origin, outcome, _detail in await fire.ledger.rows(fid):
                    if self._live((fid, room)):
                        busy = True
                    elif outcome == "pending":
                        await self._finish(fire, room, "offline", "offline")
                    elif outcome == "sending":
                        # Its task is gone without recording an end.
                        await self._finish(fire, room, "failed", "send_failed")
                if not busy:
                    await self._settle(fire)
            except Exception as e:  # noqa: BLE001
                log.warning("timer fire %d: sweep failed: %s", fid, e)
        await self._maybe_prune()

    async def _settle(self, fire: _Fire) -> None:
        fid = fire.rec.fire_id
        acked_by = await fire.ledger.settle(fid)
        rows = await fire.ledger.rows(fid)
        self._fires.pop(fid, None)
        for key in [k for k in self._noted if k[0] == fid]:
            self._noted.pop(key, None)
        self._events.emit("core.timer_fire_settled", {
            "fire_id": fid,
            "timer_id": fire.rec.timer_id,
            "kind": fire.rec.kind,
            "origin_room_id": fire.rec.origin_room_id,
            "outcomes": {room: outcome for room, _o, outcome, _d in rows},
            "heard_in": [room for room, _o, outcome, _d in rows if outcome in HEARD_OUTCOMES],
            "acked_by": acked_by,
        })

    async def _maybe_prune(self) -> None:
        now = self._clock()
        if self._ledger is None or (
            self._last_prune is not None and now - self._last_prune < PRUNE_EVERY_SEC
        ):
            return
        self._last_prune = now
        try:
            removed = await self._ledger.prune(int(settings.timer_fire_retention_days))
        except Exception as e:  # noqa: BLE001
            log.warning("timer fires: pruning old history failed: %s", e)
            return
        if removed:
            log.info("timer fires: pruned %d older than %d days",
                     removed, int(settings.timer_fire_retention_days))

    async def resume_unsettled(self) -> None:
        """After a core restart: pick up the fires that had not settled.
        A room caught mid-send is recorded ``failed/core_restarted`` (it
        may or may not have heard it: never risk twice); a fire under 10
        minutes old keeps waiting for its other rooms, with a fresh grace
        window; an older one gives its waiting rooms up as offline."""
        ledger = self._ledger if self._ledger is not None else await self.ensure_ledger()
        now_wall = self._wall()
        sessions = self._sessions()
        for rec, rows in await ledger.unsettled():
            if rec.fire_id in self._fires:
                continue
            fire = _Fire(rec=rec, ledger=ledger, fired_mono=self._clock(),
                         targets=[r[0] for r in rows])
            recent = (now_wall - rec.fired_at).total_seconds() <= RESUME_WITHIN_SEC
            for room, _is_origin, outcome, _detail in rows:
                if outcome == "sending":
                    await self._finish(fire, room, "failed", "core_restarted")
                elif outcome == "pending" and not recent:
                    await self._finish(fire, room, "offline", "core_restarted")
            if not recent:
                await self._settle(fire)
                continue
            self._fires[rec.fire_id] = fire
        # A room already connected (the first tick ran late) is treated as
        # if it had just connected: its waiting rows restart, and a room
        # that qualifies but has no row yet is added.
        for room in list(sessions):
            self.on_room_connected(room)

    async def shutdown(self) -> None:
        """Stop every task. Rows stay pending/sending for the next boot's
        :meth:`resume_unsettled`."""
        self._closed = True
        tasks = [*self._tasks.values(), *self._aux]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._aux.clear()
