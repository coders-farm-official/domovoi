"""Quiet hours: when a room speaks no unprompted announcement.

Owner request, 2026-10-25. One house-wide setting (Settings → Voice &
speech: ``quiet_hours_enabled``, ``quiet_hours_start``, ``quiet_hours_end``;
on, 22:00-07:00 by default) and, per room, one of three choices
(``satellites.quiet_mode``, V040):

* ``house`` (the default): the house's hours;
* ``custom``: the room's own start and end (``quiet_start``/``quiet_end``);
* ``off``: never quiet.

The clock is the house's clock, :mod:`domovoi.household_time`: the zone the
installer configured for the server (``--timezone`` on a Linux box, the
Windows zone inside the Windows install's WSL distro), the same clock
"remind me at 6" and "what time is it" read. Not a separate setting.

**The one gate.** :meth:`domovoi.streaming.StreamSession.announce` asks
:func:`holds_back` before anything is synthesized, for every announcement
whose ``kind`` is not exempt. The kinds:

* ``notice`` (the default, and the only kind a plugin can reach: the SDK's
  ``speech.announce`` never passes another): a low battery, a printer
  problem, a voice that finished downloading, song recognition done, a
  plugin's news. Held back during quiet hours: nothing is
  spoken then or later; the dashboard's notice and the phones'
  notification (where the feature has one) still go;
* ``alarm``: timers, alarms and reminders. Never held back;
* ``user``: somebody asked for it right now ("say dinner's ready in the
  kitchen", the dashboard's Broadcast, a voice change they picked, the
  end of a letter they just dictated in the room). Never held back.

Answers to a question are replies, not announcements, and never pass here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

log = logging.getLogger(__name__)

#: Announcement kinds quiet hours never hold back.
EXEMPT_KINDS = frozenset({"alarm", "user"})
#: Every kind StreamSession.announce takes.
KINDS = frozenset({"notice"}) | EXEMPT_KINDS
#: A room's choices.
MODES = ("house", "custom", "off")
DEFAULT_START = "22:00"
DEFAULT_END = "07:00"

_CLOCK_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def normalize_clock(value: Any) -> str | None:
    """``"7:05"`` / ``"07:05"`` -> ``"07:05"``; None when it isn't one."""
    if not isinstance(value, str):
        return None
    m = _CLOCK_RE.match(value.strip())
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def minutes(clock: str) -> int:
    h, m = clock.split(":")
    return int(h) * 60 + int(m)


def in_window(local: datetime, start: str, end: str) -> bool:
    """Whether wall-clock ``local`` is inside start..end (start included,
    end not). Start after end crosses midnight; start == end is no window."""
    s, e, t = minutes(start), minutes(end), local.hour * 60 + local.minute
    if s == e:
        return False
    if s < e:
        return s <= t < e
    return t >= s or t < e


@dataclass(frozen=True)
class RoomChoice:
    mode: str = "house"
    start: str | None = None
    end: str | None = None


def house_hours(settings: Any = None) -> tuple[str, str] | None:
    """The house's hours, or None while house-wide quiet hours are off."""
    if settings is None:
        from domovoi.config import settings as live

        settings = live
    if not bool(getattr(settings, "quiet_hours_enabled", True)):
        return None
    start = normalize_clock(getattr(settings, "quiet_hours_start", DEFAULT_START)) or DEFAULT_START
    end = normalize_clock(getattr(settings, "quiet_hours_end", DEFAULT_END)) or DEFAULT_END
    return start, end


def hours_for(choice: RoomChoice, settings: Any = None) -> tuple[str, str] | None:
    """The hours that apply to a room, or None when it is never quiet."""
    if choice.mode == "off":
        return None
    if choice.mode == "custom":
        start, end = normalize_clock(choice.start), normalize_clock(choice.end)
        if start and end:
            return start, end
    return house_hours(settings)


async def room_choice(s: Any, room_id: str) -> RoomChoice:
    """The room's stored choice; ``house`` for a room with no row."""
    row = (await s.execute(
        text("SELECT quiet_mode, quiet_start, quiet_end FROM satellites WHERE room_id = :r"),
        {"r": room_id},
    )).first()
    if row is None or row[0] not in MODES:
        return RoomChoice()
    return RoomChoice(row[0], row[1], row[2])


async def set_room_choice(s: Any, room_id: str, choice: RoomChoice) -> RoomChoice:
    """Store a room's choice (an upsert). ``custom`` needs both times."""
    if choice.mode not in MODES:
        raise ValueError(f"quiet mode must be one of {', '.join(MODES)}")
    start = end = None
    if choice.mode == "custom":
        start, end = normalize_clock(choice.start), normalize_clock(choice.end)
        if not start or not end:
            raise ValueError("custom quiet hours need a start and an end, like 22:00")
    elif choice.mode == "house":
        start, end = None, None
    await s.execute(
        text(
            "INSERT INTO satellites (room_id, quiet_mode, quiet_start, quiet_end, adopted_via) "
            "VALUES (:r, :m, :s, :e, 'manual') ON CONFLICT (room_id) DO UPDATE SET "
            "quiet_mode = EXCLUDED.quiet_mode, quiet_start = EXCLUDED.quiet_start, "
            "quiet_end = EXCLUDED.quiet_end, updated_at = now()"
        ),
        {"r": room_id, "m": choice.mode, "s": start, "e": end},
    )
    return RoomChoice(choice.mode, start, end)


def _local_now() -> datetime:
    from domovoi import household_time

    return household_time.now()


async def is_quiet(room_id: str, *, now: datetime | None = None, settings: Any = None,
                   scope: Any = None) -> bool:
    """Whether ``room_id`` is in quiet hours now. A database that can't be
    read (no V040) counts as the house's hours."""
    choice = RoomChoice()
    try:
        if scope is None:
            from domovoi.db.session import session_scope as scope
        async with scope() as s:
            choice = await room_choice(s, room_id)
    except Exception as e:  # noqa: BLE001
        log.debug("quiet hours for %s: room choice unreadable (%s); the house's", room_id, e)
    hours = hours_for(choice, settings)
    if hours is None:
        return False
    return in_window(now or _local_now(), *hours)


async def holds_back(room_id: str, kind: str, **kw: Any) -> bool:
    """The gate: True when an announcement of ``kind`` must not be spoken in
    ``room_id`` now. An unknown kind counts as a notice."""
    if kind in EXEMPT_KINDS:
        return False
    return await is_quiet(room_id, **kw)
