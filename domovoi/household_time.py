"""The household's wall clock: the Domovoi server's own local time zone.

"What time is it" (handlers/clock.py) reads the server's local wall clock,
and so does everything that turns a spoken clock time or day into an
instant: "remind me at 6 pm" (handlers/reminder.py), "what's on my
calendar tomorrow" (handlers/calendar.py). The zone is the server's own:
the ``TZ`` environment variable or the operating system's zone setting
(``timedatectl set-timezone`` on Linux, Windows' time zone setting) - the
same zone GET /v1/time hands the satellites (domovoi/host_time.py). There
is no separate household zone setting.

Wall-clock arithmetic is done on NAIVE local datetimes and converted at the
edges, so "tomorrow at 7" is 7 on tomorrow's wall clock even when tonight is
a daylight-saving change (a 23 or 25 hour day):

* :func:`now` - the local wall clock, naive.
* :func:`utc_now` - the same instant, aware UTC.
* :func:`to_utc` - a naive local wall-clock time to its aware UTC instant.
  A time in the spring-forward gap (2:30 on the night 2:00 jumps to 3:00)
  is read with the offset before the change, which is 3:30 on the new wall
  clock; a time the fall-back hour repeats means its first occurrence.
* :func:`to_local` - an aware instant to the naive local wall clock.

Tests pin the zone and the clock with :func:`use_zone` (any ``tzinfo``,
including a hand-written DST zone), so the DST cases run without tzdata on
the machine.
"""

from __future__ import annotations

import contextlib
import time
from datetime import datetime, timezone, tzinfo
from typing import Callable, Iterator

# (zone, clock) pairs pushed by use_zone(); the innermost wins.
_overrides: list[tuple[tzinfo, Callable[[], datetime] | None]] = []


@contextlib.contextmanager
def use_zone(
    zone: tzinfo, clock: Callable[[], datetime] | None = None
) -> Iterator[None]:
    """Pin the household zone (and optionally the clock: a callable that
    returns the aware current instant) for a block. Tests only."""
    _overrides.append((zone, clock))
    try:
        yield
    finally:
        _overrides.pop()


def utc_now() -> datetime:
    """The current instant, aware UTC."""
    if _overrides:
        clock = _overrides[-1][1]
        if clock is not None:
            return clock().astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def to_local(instant: datetime) -> datetime:
    """An aware instant on the household's wall clock (naive)."""
    if _overrides:
        return instant.astimezone(_overrides[-1][0]).replace(tzinfo=None)
    # astimezone() with no argument converts with the operating system's
    # own rules for that instant (DST included), like datetime.now().
    return instant.astimezone().replace(tzinfo=None)


def now() -> datetime:
    """The household's wall clock now (naive)."""
    return to_local(utc_now())


def to_utc(local: datetime) -> datetime:
    """A naive wall-clock time in the household's zone, as an aware UTC
    instant (see the module note for the DST gap and overlap)."""
    if _overrides:
        zone = _overrides[-1][0]
        return local.replace(tzinfo=zone, fold=0).astimezone(timezone.utc)
    # mktime applies the operating system's zone rules for that date;
    # tm_isdst=-1 lets it decide whether DST is in force then.
    stamp = time.mktime((
        local.year, local.month, local.day, local.hour, local.minute,
        local.second, 0, 0, -1,
    ))
    return datetime.fromtimestamp(stamp, timezone.utc).replace(
        microsecond=local.microsecond
    )
