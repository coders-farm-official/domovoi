"""A daylight-saving zone for tests, with no tzdata needed.

``EASTERN`` is US Eastern time (UTC-5, UTC-4 from the second Sunday in
March to the first Sunday in November, both at 2:00 local), written out
the way the Python docs' ``USTimeZone`` example is: the Windows test boxes
have no ``tzdata`` package, so ``zoneinfo`` can't load "America/New_York"
there, and the DST cases must run everywhere. PEP 495 folds included: the
repeated hour in November has a first (fold=0, EDT) and second (fold=1,
EST) reading; a time in the March gap reads with the offset before it.

``at(...)`` pins the household zone AND clock (domovoi.household_time):

    with at(2026, 10, 16, 23, 20):
        ...  # it is 23:20 on the household's wall clock, US Eastern
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Iterator

from domovoi import household_time

ZERO = timedelta(0)
HOUR = timedelta(hours=1)


def _first_sunday_on_or_after(dt: datetime) -> datetime:
    days = 6 - dt.weekday()
    return dt + timedelta(days) if days else dt


def _dst_range(year: int) -> tuple[datetime, datetime]:
    """Naive local start and end of DST: 2:00 on the second Sunday of
    March and the first Sunday of November."""
    start = _first_sunday_on_or_after(datetime(year, 3, 8, 2))
    end = _first_sunday_on_or_after(datetime(year, 11, 1, 2))
    return start, end


class USEastern(tzinfo):
    std = timedelta(hours=-5)

    def __repr__(self) -> str:
        return "USEastern()"

    def tzname(self, dt: datetime | None) -> str:
        return "EDT" if self.dst(dt) else "EST"

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return self.std + self.dst(dt)

    def dst(self, dt: datetime | None) -> timedelta:
        if dt is None or dt.tzinfo is None:
            return ZERO
        start, end = _dst_range(dt.year)
        naive = dt.replace(tzinfo=None)
        if start + HOUR <= naive < end - HOUR:
            return HOUR
        if end - HOUR <= naive < end:
            # The repeated hour: the first reading is still DST.
            return ZERO if dt.fold else HOUR
        if start <= naive < start + HOUR:
            # The gap: a time that doesn't exist reads with the old offset.
            return HOUR if dt.fold else ZERO
        return ZERO

    def fromutc(self, dt: datetime) -> datetime:
        start, end = _dst_range(dt.year)
        start = start.replace(tzinfo=self)
        end = end.replace(tzinfo=self)
        std_time = dt + self.std
        dst_time = std_time + HOUR
        if end <= dst_time < end + HOUR:
            return std_time.replace(fold=1)
        if std_time < start or dst_time >= end:
            return std_time
        if start <= std_time < end - HOUR:
            return dst_time
        return std_time


EASTERN = USEastern()


@contextmanager
def at(*wall: int, zone: tzinfo = EASTERN) -> Iterator[datetime]:
    """Pin the household clock to the wall-clock time ``wall`` (year,
    month, day, hour, minute) in ``zone``. Yields that instant, aware UTC."""
    instant = datetime(*wall).replace(tzinfo=zone).astimezone(timezone.utc)
    with household_time.use_zone(zone, lambda: instant):
        yield instant
