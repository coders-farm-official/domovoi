"""A phone room's battery: what it last said, and the low-battery warnings.

A phone in room mode (``board`` ``android_room``) sends its battery in the
``health`` frame, ``battery`` = ``{percent, charging, on_power}``
(:func:`domovoi.satellite_health.validate_sample`), once a minute and again
when the level moves about 5 % or the charger goes on or off. A phone room
that is not on its charger runs down and goes offline with no warning (the
owner's "Pako", 2026-10-25). The core's side, all here:

* **The last report** is kept per room (``room_battery``, V040), written
  when it changes and at least every :data:`REPORT_REFRESH_SEC`. The room
  list shows it; once the room has gone quiet, the offline row reads it
  (:func:`probably_out_of_battery`).
* **The warnings.** At :data:`WARN_AT` (20 % and again at 10 %), while the
  phone is off its charger, once per discharge cycle: one
  ``room_battery_warnings`` row each. A room's open rows ARE its cycle (the
  thresholds already said), so a core restart neither forgets nor repeats
  one. Charging, or a level back above :data:`RESET_ABOVE` (it charged
  while the core was not listening), ends the cycle. A first report that is
  already under both thresholds warns once, at the lower one.
* **What a warning does.** It is a row, so the dashboard's notice (Home's
  "needs attention" and Health → Rooms) and the phones' notification
  (the app reads ``GET /api/satellites/battery-warnings`` and the live
  ``room_battery.changed``) both come from it, while the room's "Notify
  when the battery is low" is on. And the room says it itself, "This is
  Pako. My battery is low. Please plug me in.", while its "Say when the
  battery is low" is on and it is not quiet hours.
* **Quiet hours** (:mod:`domovoi.quiet_hours`: the house's hours, or the
  room's own, or off for the room). Inside them the room doesn't say it;
  the notice and the notification still go. A warning kept quiet is not
  said later. The announcement is a ``notice``, so StreamSession.announce
  holds it back too should the hours begin between the warning and its
  retries.

Both switches live on the room's ``satellites`` row (V040, default on) and
are read at each warning, so a change counts at once, no restart. Only
phone rooms have them, or show a battery: a Pi has none, and a laptop's
battery stays on its Health card only.

Best-effort throughout, like the health telemetry: a missing V040 or a
database error costs one warning in the journal, never the room's session.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi import quiet_hours

log = logging.getLogger(__name__)

#: The board a phone in room mode names in its hello.
PHONE_BOARD = "android_room"
#: Warn at these levels (percent), highest first, once each per discharge.
WARN_AT: tuple[int, ...] = (20, 10)
#: A level above this ends the cycle even without a charging report.
RESET_ABOVE = 25
#: A room that went offline at or under this, off its charger, is
#: "probably out of battery".
OUT_OF_BATTERY_AT = 5
#: The last report is rewritten at least this often, unchanged or not.
REPORT_REFRESH_SEC = 600.0
#: How often, and how far apart, a spoken warning is tried when the room is
#: busy (mid-reply, listening, in a call).
SPEAK_ATTEMPTS = 4
SPEAK_RETRY_SEC = 20.0
#: A closed warning is deleted this long after it closed.
ENDED_KEEP_DAYS = 30
#: A name longer than this is left out of the spoken line.
SPOKEN_NAME_MAX = 24

NOTIFY_CHANNEL = "room_battery_changed"

SPOKEN_PLAIN = "My battery is low. Please plug me in."


# ─── the rules (pure) ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Reading:
    """One battery report: ``percent`` 0-100 (None: not said), whether it
    is charging, and whether its charger is plugged in."""

    percent: int | None
    charging: bool | None
    on_power: bool | None

    @property
    def on_charger(self) -> bool:
        return self.charging is True or self.on_power is True


def reading_from(battery: Any) -> Reading | None:
    """A validated sample's ``battery`` object -> a :class:`Reading`, or
    None when there is none."""
    if not isinstance(battery, dict):
        return None
    pct = battery.get("percent")
    percent = None
    if isinstance(pct, (int, float)) and not isinstance(pct, bool) and 0 <= pct <= 100:
        percent = int(round(pct))
    charging = battery.get("charging") if isinstance(battery.get("charging"), bool) else None
    on_power = battery.get("on_power") if isinstance(battery.get("on_power"), bool) else None
    if percent is None and charging is None and on_power is None:
        return None
    return Reading(percent, charging, on_power)


def cycle_ends(reading: Reading) -> bool:
    """Whether this report ends a discharge cycle: on the charger, or back
    above every threshold."""
    return reading.on_charger or (reading.percent is not None and reading.percent > RESET_ABOVE)


def warning_due(reading: Reading, warned: set[int] | frozenset[int]) -> int | None:
    """The threshold to warn at now, or None. ``warned``: the thresholds
    this cycle already said. Off the charger only; at or under a threshold
    not said yet; several at once (a first report at 8 %) is one warning,
    at the lowest."""
    if reading.percent is None or reading.on_charger:
        return None
    due = [t for t in WARN_AT if reading.percent <= t and t not in warned]
    if not due:
        return None
    low = min(due)
    # Already said a lower one (8 % first, then a jump back to 15 %)?
    if any(w <= low for w in warned):
        return None
    return low


def display_name(room_id: str, label: str | None = None) -> str:
    """How a notice names the room: its label, else its id with the
    underscores read as spaces, first letter capital ("pako" -> "Pako")."""
    name = " ".join((label or "").split()) or " ".join(
        room_id.replace("_", " ").replace("-", " ").split()
    )
    return name[:1].upper() + name[1:] if name else room_id


def notice_text(name: str, percent: int) -> str:
    """The notification's words: "Pako is at 15%. Plug it in." """
    return f"{name} is at {int(percent)}%. Plug it in."


def spoken_text(name: str) -> str:
    """What the room itself says: its own name first when that is short."""
    if name and len(name) <= SPOKEN_NAME_MAX:
        return f"This is {name}. {SPOKEN_PLAIN}"
    return SPOKEN_PLAIN


def probably_out_of_battery(online: bool, reading: Reading | None) -> bool:
    """An offline phone room whose last report was at or under
    :data:`OUT_OF_BATTERY_AT` and off its charger."""
    return (
        not online
        and reading is not None
        and reading.percent is not None
        and reading.percent <= OUT_OF_BATTERY_AT
        and not reading.on_charger
    )


# ─── the database ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Switches:
    say: bool = True
    notify: bool = True


async def switches(s: AsyncSession, room_id: str) -> Switches:
    """The room's two switches; both on when it has no ``satellites`` row."""
    row = (await s.execute(
        text("SELECT battery_say, battery_notify FROM satellites WHERE room_id = :r"),
        {"r": room_id},
    )).first()
    if row is None:
        return Switches()
    return Switches(say=bool(row[0]), notify=bool(row[1]))


async def set_switches(
    s: AsyncSession, room_id: str, *, say: bool | None = None, notify: bool | None = None
) -> Switches:
    """Change one or both switches (an upsert: a room with no row gets
    one), and say so on the live socket. Returns what they are now."""
    cur = await switches(s, room_id)
    new = Switches(say=cur.say if say is None else bool(say),
                   notify=cur.notify if notify is None else bool(notify))
    await s.execute(
        text(
            "INSERT INTO satellites (room_id, battery_say, battery_notify, adopted_via) "
            "VALUES (:r, :say, :notify, 'manual') "
            "ON CONFLICT (room_id) DO UPDATE SET battery_say = EXCLUDED.battery_say, "
            "battery_notify = EXCLUDED.battery_notify, updated_at = now()"
        ),
        {"r": room_id, "say": new.say, "notify": new.notify},
    )
    await notify_changed(s, f"switches:{room_id}")
    return new


async def notify_changed(s: AsyncSession, reason: str) -> None:
    await s.execute(text("SELECT pg_notify(:c, :r)"), {"c": NOTIFY_CHANNEL, "r": reason[:120]})


async def last_reports(s: AsyncSession, room_id: str | None = None) -> dict[str, dict[str, Any]]:
    """room -> ``{percent, charging, on_power, reported_at}``."""
    sql = "SELECT room_id, percent, charging, on_power, reported_at FROM room_battery"
    params: dict[str, Any] = {}
    if room_id is not None:
        sql += " WHERE room_id = :r"
        params["r"] = room_id
    rows = (await s.execute(text(sql), params)).all()
    return {
        r[0]: {"percent": r[1], "charging": r[2], "on_power": r[3], "reported_at": r[4]}
        for r in rows
    }


async def open_warnings(s: AsyncSession, room_id: str | None = None,
                        *, notify_only: bool = False) -> list[dict[str, Any]]:
    """The open warnings, oldest first: ``{id, room_id, threshold, percent,
    spoken, created_at}`` and the room's label. ``notify_only``: only rooms
    whose "Notify when the battery is low" is on (what the notice and the
    phones show)."""
    sql = (
        "SELECT w.id, w.room_id, w.threshold, w.percent, w.spoken, w.created_at, "
        "sat.room_label, COALESCE(sat.battery_notify, TRUE) AS notify "
        "FROM room_battery_warnings w LEFT JOIN satellites sat ON sat.room_id = w.room_id "
        "WHERE w.ended_at IS NULL"
    )
    params: dict[str, Any] = {}
    if room_id is not None:
        sql += " AND w.room_id = :r"
        params["r"] = room_id
    if notify_only:
        sql += " AND COALESCE(sat.battery_notify, TRUE)"
    sql += " ORDER BY w.id"
    rows = (await s.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


def warning_json(row: dict[str, Any]) -> dict[str, Any]:
    """A warning as the web serves it (and the phones read it)."""
    name = display_name(row["room_id"], row.get("room_label"))
    created = row.get("created_at")
    return {
        "id": int(row["id"]),
        "room_id": row["room_id"],
        "name": name,
        "threshold": int(row["threshold"]),
        "percent": int(row["percent"]),
        "text": notice_text(name, int(row["percent"])),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
    }


# ─── the watch ────────────────────────────────────────────────────────────


#: How a spoken warning reaches the room: (room_id, text) -> True when it
#: played, False to try again later (busy, not connected), None when it
#: never will (the device declared no announcements).
Speaker = Callable[[str, str], Awaitable["bool | None"]]


@dataclass
class Outcome:
    """What one report did (for the tests and the journal)."""

    wrote_report: bool = False
    ended: int = 0
    warned: int | None = None
    warning_id: int | None = None
    speak: str | None = None
    quiet: bool = False


class BatteryWatch:
    """One per core (``app.state.room_battery``). :meth:`observe` takes
    each phone room's battery report, in order per room."""

    def __init__(
        self,
        *,
        scope: Callable[[], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
        speaker: Speaker | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._scope = scope
        self._clock = clock
        self.speaker = speaker
        self._mono = monotonic
        self._locks: dict[str, asyncio.Lock] = {}
        self._written: dict[str, tuple[Reading, float]] = {}
        self._missing_said = False
        self._tasks: set[asyncio.Task] = set()

    def _session(self) -> Any:
        if self._scope is not None:
            return self._scope()
        from domovoi.db.session import session_scope

        return session_scope()

    def _local_now(self) -> datetime:
        if self._clock is not None:
            return self._clock()
        from domovoi import household_time

        return household_time.now()

    async def observe(self, room_id: str, board: str | None, battery: Any) -> Outcome | None:
        """A health sample's ``battery`` from ``room_id``. Phone rooms only;
        never raises."""
        if board != PHONE_BOARD:
            return None
        reading = reading_from(battery)
        if reading is None:
            return None
        lock = self._locks.setdefault(room_id, asyncio.Lock())
        async with lock:
            try:
                out = await self._observe(room_id, reading)
            except Exception as e:  # noqa: BLE001 — best-effort, like health
                self._db_failed(e)
                return None
        if out.speak:
            self._spawn(self._speak(room_id, out.speak, out.warning_id))
        return out

    async def _observe(self, room_id: str, reading: Reading) -> Outcome:
        out = Outcome()
        now = self._mono()
        held = self._written.get(room_id)
        changed = False
        async with self._session() as s:
            if held is None or held[0] != reading or now - held[1] >= REPORT_REFRESH_SEC:
                await s.execute(
                    text(
                        "INSERT INTO room_battery (room_id, percent, charging, on_power, reported_at) "
                        "VALUES (:r, :p, :c, :o, now()) ON CONFLICT (room_id) DO UPDATE SET "
                        "percent = EXCLUDED.percent, charging = EXCLUDED.charging, "
                        "on_power = EXCLUDED.on_power, reported_at = now()"
                    ),
                    {"r": room_id, "p": reading.percent, "c": reading.charging, "o": reading.on_power},
                )
                out.wrote_report = True
                changed = held is None or held[0] != reading
            rows = await open_warnings(s, room_id)
            warned = {int(r["threshold"]) for r in rows}
            if rows and cycle_ends(reading):
                res = await s.execute(
                    text("UPDATE room_battery_warnings SET ended_at = now() "
                         "WHERE room_id = :r AND ended_at IS NULL"),
                    {"r": room_id},
                )
                out.ended = int(res.rowcount or 0)
                warned = set()
                log.info("room %s battery: on the charger (or back up), warnings cleared", room_id)
            due = None if out.ended else warning_due(reading, warned)
            if due is not None:
                sw = await switches(s, room_id)
                label = await _label(s, room_id)
                quiet = quiet_hours.in_window(self._local_now(), *hours) if (
                    hours := quiet_hours.hours_for(await quiet_hours.room_choice(s, room_id))
                ) else False
                new_id = (await s.execute(
                    text("INSERT INTO room_battery_warnings (room_id, threshold, percent) "
                         "VALUES (:r, :t, :p) RETURNING id"),
                    {"r": room_id, "t": due, "p": reading.percent},
                )).scalar_one()
                out.warned, out.warning_id, out.quiet = due, int(new_id), quiet
                # Closed warnings are history only: kept ENDED_KEEP_DAYS.
                await s.execute(
                    text("DELETE FROM room_battery_warnings WHERE ended_at IS NOT NULL "
                         "AND ended_at < now() - make_interval(days => :d)"),
                    {"d": ENDED_KEEP_DAYS},
                )
                if sw.say and not quiet:
                    out.speak = spoken_text(display_name(room_id, label))
                log.warning(
                    "room %s battery at %s%% (under %s%%), off its charger: warning %s%s",
                    room_id, reading.percent, due, new_id,
                    "" if sw.say and not quiet else
                    (" (not spoken: the room's switch is off)" if not sw.say else " (not spoken: quiet hours)"),
                )
            if out.ended or out.warned is not None or changed:
                await notify_changed(s, f"{room_id}:{reading.percent}")
        if out.wrote_report:
            self._written[room_id] = (reading, now)
        return out

    async def _speak(self, room_id: str, words: str, warning_id: int | None) -> None:
        speaker = self.speaker
        if speaker is None:
            return
        for attempt in range(SPEAK_ATTEMPTS):
            try:
                heard = await speaker(room_id, words)
            except Exception as e:  # noqa: BLE001
                log.info("room %s battery warning not spoken: %s", room_id, e)
                heard = False
            if heard is None:
                return
            if heard:
                if warning_id is not None:
                    try:
                        async with self._session() as s:
                            await s.execute(text("UPDATE room_battery_warnings SET spoken = TRUE WHERE id = :i"),
                                            {"i": warning_id})
                    except Exception as e:  # noqa: BLE001
                        self._db_failed(e)
                return
            if attempt + 1 < SPEAK_ATTEMPTS:
                await asyncio.sleep(SPEAK_RETRY_SEC)

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _db_failed(self, e: Exception) -> None:
        text_ = str(e).lower()
        if "room_battery" in text_ or "battery_say" in text_ or "battery_notify" in text_:
            if not self._missing_said:
                self._missing_said = True
                log.warning("phone room battery warnings are off: run the V040 migration (Flyway)")
            return
        log.warning("phone room battery: %s", e)


async def _label(s: AsyncSession, room_id: str) -> str | None:
    row = (await s.execute(text("SELECT room_label FROM satellites WHERE room_id = :r"),
                           {"r": room_id})).first()
    return row[0] if row else None


def session_speaker(app: Any) -> Speaker:
    """The core's speaker: the room's own live session, as an out-of-turn
    announcement that waits for a capture or a call to finish (the timer
    delivery's rule). True when it played (an interruption counts)."""
    from domovoi.timer_delivery import AnnounceInterrupted, AnnounceNotStarted

    async def speak(room_id: str, words: str) -> bool | None:
        session = (getattr(app.state, "active_sessions", None) or {}).get(room_id)
        if session is None:
            return False
        try:
            await session.announce(words, defer_to_capture=True, kind="notice")
        except AnnounceInterrupted:
            return True
        except AnnounceNotStarted as e:
            if getattr(e, "reason", "") in ("unsupported", "quiet_hours"):
                return None  # never will (or not now, and never later): stop trying
            return False
        return True

    return speak
