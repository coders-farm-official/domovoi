"""Read an iCalendar (ICS) feed into plain events: the parser behind
"calendars by link" (domovoi/calendar_feeds.py).

Standard library only, on purpose: the common Python iCalendar stacks
either pull in a recurrence engine under the LGPL or a date library this
project doesn't otherwise carry, and a feed is a small, line-based text
format (RFC 5545). What is read:

* ``VEVENT``: ``UID``, ``SUMMARY``, ``LOCATION``, ``DTSTART`` / ``DTEND``
  or ``DURATION``, ``STATUS`` (``CANCELLED`` events are dropped),
  ``RRULE``, ``RDATE``, ``EXDATE``, and ``RECURRENCE-ID`` overrides (a
  moved or cancelled single occurrence of a series). Descriptions,
  attendees and alarms are never read: the house shows when and where,
  not who or why.
* Times: ``DATE`` values are all-day; a trailing ``Z`` is UTC; ``TZID``
  is resolved as an IANA zone (``America/New_York``), then a Windows zone
  name as Outlook writes it (``Eastern Standard Time``), then the feed's
  own ``VTIMEZONE`` definition, then the household's own zone. A time
  with none of these ("floating") is the feed's ``X-WR-TIMEZONE`` or the
  household's wall clock.
* ``RRULE``: ``FREQ`` DAILY / WEEKLY / MONTHLY / YEARLY with ``INTERVAL``,
  ``COUNT``, ``UNTIL``, ``BYDAY`` (ordinals such as ``2TU`` / ``-1FR``
  included), ``BYMONTHDAY``, ``BYMONTH``, ``BYSETPOS`` and ``WKST``. Rules
  using parts outside that set (``BYYEARDAY``, ``BYWEEKNO``, ``BYHOUR``…,
  or HOURLY and finer) are read as their first occurrence only, which is
  shown rather than guessed at.

Everything is bounded: the caller caps the bytes, and here the number of
events (:data:`MAX_EVENTS`), the occurrences of one series
(:data:`MAX_OCCURRENCES`) and the rule iterations
(:data:`MAX_RULE_STEPS`) are capped, so a hostile or broken feed costs a
fixed amount of work. Occurrences are expanded only inside the caller's
window.

Output: :class:`IcsEvent` rows with aware UTC ``start`` / ``end`` (an
all-day event's are the household's local midnights, which is how
``calendar_events`` keeps all-day rows), sorted by start.
"""

from __future__ import annotations

import calendar as _cal
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Callable, Iterable, Iterator

from domovoi import household_time

log = logging.getLogger(__name__)

MAX_EVENTS = 5000
MAX_OCCURRENCES = 1500
MAX_RULE_STEPS = 20000
MAX_TEXT = 200

_WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


class IcsError(ValueError):
    """The document is not an iCalendar feed at all."""


@dataclass(frozen=True)
class IcsEvent:
    uid: str
    title: str
    start: datetime           # aware UTC
    end: datetime | None      # aware UTC
    all_day: bool
    location: str | None


@dataclass
class IcsCalendar:
    name: str | None
    events: list[IcsEvent]
    truncated: bool = False   # more events than MAX_EVENTS in the window


# ─── Lines and properties ────────────────────────────────────────────────


@dataclass
class _Prop:
    name: str
    params: dict[str, str]
    value: str


def _unfold(text: str) -> Iterator[str]:
    """Logical lines: a line starting with a space or a tab continues the
    one before (RFC 5545 §3.1)."""
    current: str | None = None
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and current is not None:
            current += raw[1:]
            continue
        if current is not None:
            yield current
        current = raw
    if current is not None:
        yield current


def _parse_line(line: str) -> _Prop | None:
    """``NAME;P1=V1;P2="V:2":value`` -> a property, or None for junk."""
    i, n = 0, len(line)
    in_quotes = False
    colon = -1
    while i < n:
        ch = line[i]
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ":" and not in_quotes:
            colon = i
            break
        i += 1
    if colon <= 0:
        return None
    head, value = line[:colon], line[colon + 1:]
    parts: list[str] = []
    buf, in_quotes = "", False
    for ch in head:
        if ch == '"':
            in_quotes = not in_quotes
            continue
        if ch == ";" and not in_quotes:
            parts.append(buf)
            buf = ""
            continue
        buf += ch
    parts.append(buf)
    name = parts[0].strip().upper()
    if not name:
        return None
    params: dict[str, str] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip().upper()] = v.strip()
    return _Prop(name, params, value)


def _unescape(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(_unescape(value).split())
    return text[:MAX_TEXT] or None


@dataclass
class _Component:
    name: str
    props: list[_Prop] = field(default_factory=list)
    children: list["_Component"] = field(default_factory=list)

    def first(self, name: str) -> _Prop | None:
        for p in self.props:
            if p.name == name:
                return p
        return None

    def all(self, name: str) -> list[_Prop]:
        return [p for p in self.props if p.name == name]


def _components(text: str) -> _Component:
    root = _Component("ROOT")
    stack = [root]
    for line in _unfold(text):
        if not line.strip():
            continue
        prop = _parse_line(line)
        if prop is None:
            continue
        if prop.name == "BEGIN":
            comp = _Component(prop.value.strip().upper())
            stack[-1].children.append(comp)
            stack.append(comp)
        elif prop.name == "END":
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].props.append(prop)
    return root


# ─── Time zones ──────────────────────────────────────────────────────────

# The Windows zone names Outlook and Exchange write as TZID, mapped to the
# IANA zone they mean (CLDR's windowsZones, the territory "001" column, for
# the zones people actually live in).
WINDOWS_ZONES: dict[str, str] = {
    "Dateline Standard Time": "Etc/GMT+12",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "Alaskan Standard Time": "America/Anchorage",
    "Pacific Standard Time": "America/Los_Angeles",
    "Pacific Standard Time (Mexico)": "America/Tijuana",
    "US Mountain Standard Time": "America/Phoenix",
    "Mountain Standard Time": "America/Denver",
    "Mountain Standard Time (Mexico)": "America/Mazatlan",
    "Central Standard Time": "America/Chicago",
    "Central Standard Time (Mexico)": "America/Mexico_City",
    "Canada Central Standard Time": "America/Regina",
    "Central America Standard Time": "America/Guatemala",
    "Eastern Standard Time": "America/New_York",
    "Eastern Standard Time (Mexico)": "America/Cancun",
    "US Eastern Standard Time": "America/Indianapolis",
    "SA Pacific Standard Time": "America/Bogota",
    "Atlantic Standard Time": "America/Halifax",
    "Newfoundland Standard Time": "America/St_Johns",
    "E. South America Standard Time": "America/Sao_Paulo",
    "Argentina Standard Time": "America/Buenos_Aires",
    "Pacific SA Standard Time": "America/Santiago",
    "UTC": "Etc/UTC",
    "Coordinated Universal Time": "Etc/UTC",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "Romance Standard Time": "Europe/Paris",
    "E. Europe Standard Time": "Europe/Chisinau",
    "FLE Standard Time": "Europe/Kiev",
    "GTB Standard Time": "Europe/Bucharest",
    "Israel Standard Time": "Asia/Jerusalem",
    "Turkey Standard Time": "Europe/Istanbul",
    "Russian Standard Time": "Europe/Moscow",
    "South Africa Standard Time": "Africa/Johannesburg",
    "Egypt Standard Time": "Africa/Cairo",
    "Arabian Standard Time": "Asia/Dubai",
    "Arab Standard Time": "Asia/Riyadh",
    "Iran Standard Time": "Asia/Tehran",
    "Pakistan Standard Time": "Asia/Karachi",
    "India Standard Time": "Asia/Calcutta",
    "Nepal Standard Time": "Asia/Katmandu",
    "Bangladesh Standard Time": "Asia/Dhaka",
    "SE Asia Standard Time": "Asia/Bangkok",
    "China Standard Time": "Asia/Shanghai",
    "Singapore Standard Time": "Asia/Singapore",
    "Taipei Standard Time": "Asia/Taipei",
    "W. Australia Standard Time": "Australia/Perth",
    "Korea Standard Time": "Asia/Seoul",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "AUS Central Standard Time": "Australia/Darwin",
    "E. Australia Standard Time": "Australia/Brisbane",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "Tasmania Standard Time": "Australia/Hobart",
    "New Zealand Standard Time": "Pacific/Auckland",
}

_OFFSET_RE = re.compile(r"^([+-])(\d{2})(\d{2})(\d{2})?$")


def _offset(value: str) -> timedelta | None:
    m = _OFFSET_RE.match(value.strip())
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    return sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3)),
                            seconds=int(m.group(4) or 0))


class VTimezone(tzinfo):
    """A zone defined by the feed's own ``VTIMEZONE``: its ``STANDARD`` and
    ``DAYLIGHT`` observances, each an onset (``DTSTART``, a yearly
    ``RRULE`` or ``RDATE`` list) and the offset it switches to."""

    def __init__(self, tzid: str, observances: list[tuple[datetime, timedelta, timedelta, str | None, list[datetime], str]]):
        # (dtstart local, offset_from, offset_to, rrule, rdates, name)
        self._tzid = tzid
        self._obs = observances
        self._years: dict[int, list[tuple[datetime, timedelta, str]]] = {}

    def _transitions(self, year: int) -> list[tuple[datetime, timedelta, str]]:
        cached = self._years.get(year)
        if cached is not None:
            return cached
        out: list[tuple[datetime, timedelta, str]] = []
        lo, hi = datetime(year - 1, 1, 1), datetime(year + 1, 1, 1)
        for start, _frm, to, rule, rdates, name in self._obs:
            onsets: list[datetime] = []
            if rule:
                rr = _Rule.parse(rule)
                if rr is not None and rr.supported:
                    onsets = list(rr.occurrences(start, lo, hi, utc_until=None))
            if not onsets and start < hi:
                onsets = [start]
            onsets += [d for d in rdates if lo <= d < hi]
            out += [(o, to, name) for o in onsets if o >= start or o == start]
        out.sort(key=lambda t: t[0])
        self._years[year] = out
        return out

    def _at(self, dt: datetime) -> tuple[timedelta, str]:
        naive = dt.replace(tzinfo=None)
        best: tuple[datetime, timedelta, str] | None = None
        for t in self._transitions(naive.year):
            if t[0] <= naive:
                best = t
        if best is None:
            # Before every onset: the earliest observance's "from" offset.
            first = min(self._obs, key=lambda o: o[0]) if self._obs else None
            return (first[1] if first else timedelta(0)), (first[5] if first else self._tzid)
        return best[1], best[2]

    def utcoffset(self, dt: datetime | None) -> timedelta:
        if dt is None:
            return timedelta(0)
        return self._at(dt)[0]

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        if dt is None:
            return self._tzid
        return self._at(dt)[1]


def _vtimezone(comp: _Component) -> VTimezone | None:
    tzid = (comp.first("TZID").value.strip() if comp.first("TZID") else "")
    if not tzid:
        return None
    obs = []
    for child in comp.children:
        if child.name not in ("STANDARD", "DAYLIGHT"):
            continue
        ds, fr, to = child.first("DTSTART"), child.first("TZOFFSETFROM"), child.first("TZOFFSETTO")
        if ds is None or to is None:
            continue
        start = _parse_naive(ds.value)
        off_to = _offset(to.value)
        off_from = _offset(fr.value) if fr is not None else off_to
        if start is None or off_to is None or off_from is None:
            continue
        rule = child.first("RRULE")
        rdates: list[datetime] = []
        for rd in child.all("RDATE"):
            for piece in rd.value.split(","):
                d = _parse_naive(piece)
                if d is not None:
                    rdates.append(d)
        name = child.first("TZNAME")
        obs.append((start, off_from, off_to, rule.value if rule else None, rdates,
                    name.value.strip() if name else tzid))
    return VTimezone(tzid, obs) if obs else None


def _iana(name: str) -> tzinfo | None:
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # pragma: no cover — stdlib from 3.9
        return None
    candidate = name.strip().strip('"')
    # "/mozilla.org/20050126_1/America/New_York" and other prefixed forms.
    if candidate.startswith("/"):
        bits = candidate.strip("/").split("/")
        candidate = "/".join(bits[-2:]) if len(bits) >= 2 else bits[-1]
    try:
        return ZoneInfo(candidate)
    except Exception:  # noqa: BLE001 — unknown key, no tzdata, bad path
        return None


class _Zones:
    """TZID -> tzinfo for one feed. ``None`` means the household's own
    wall clock (domovoi/household_time.py)."""

    def __init__(self, defined: dict[str, VTimezone], default: str | None) -> None:
        self._defined = defined
        self._cache: dict[str, tzinfo | None] = {}
        self.default = self.get(default) if default else None

    def get(self, tzid: str | None) -> tzinfo | None:
        if not tzid:
            return None
        if tzid in self._cache:
            return self._cache[tzid]
        zone = _iana(tzid)
        if zone is None and tzid.strip('"') in WINDOWS_ZONES:
            zone = _iana(WINDOWS_ZONES[tzid.strip('"')])
        if zone is None:
            zone = self._defined.get(tzid) or self._defined.get(tzid.strip('"'))
        if zone is None and tzid.strip('"').upper() in ("UTC", "GMT", "Z", "ETC/UTC"):
            zone = timezone.utc
        self._cache[tzid] = zone
        return zone


# ─── Date-time values ────────────────────────────────────────────────────

_DT_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z)?)?$")


def _parse_naive(value: str) -> datetime | None:
    m = _DT_RE.match(value.strip())
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh = int(m.group(4) or 0)
    mm = int(m.group(5) or 0)
    ss = int(m.group(6) or 0)
    try:
        return datetime(y, mo, d, hh, mm, min(ss, 59))
    except ValueError:
        return None


@dataclass(frozen=True)
class _When:
    """A DTSTART-like value: the wall clock as written, how to place it."""

    local: datetime           # naive, in ``zone`` (or UTC when ``utc``)
    all_day: bool
    utc: bool
    zone: tzinfo | None       # None: the household's zone

    def instant(self, local: datetime | None = None) -> datetime:
        wall = self.local if local is None else local
        if self.utc:
            return wall.replace(tzinfo=timezone.utc)
        if self.zone is None:
            return household_time.to_utc(wall)
        return wall.replace(tzinfo=self.zone).astimezone(timezone.utc)


def _when(prop: _Prop | None, zones: _Zones) -> _When | None:
    if prop is None:
        return None
    raw = prop.value.strip().split(",")[0]
    is_date = prop.params.get("VALUE", "").upper() == "DATE" or bool(re.fullmatch(r"\d{8}", raw))
    local = _parse_naive(raw)
    if local is None:
        return None
    if is_date:
        return _When(local, True, False, None)
    utc = raw.upper().endswith("Z")
    zone = None if utc else (zones.get(prop.params.get("TZID")) if prop.params.get("TZID") else zones.default)
    return _When(local, False, utc, zone)


_DURATION_RE = re.compile(
    r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$"
)


def _duration(value: str) -> timedelta | None:
    m = _DURATION_RE.match(value.strip().upper())
    if not m or value.strip().upper() in ("P", "PT"):
        return None
    sign = -1 if m.group(1) == "-" else 1
    w, d, h, mi, s = (int(g or 0) for g in m.groups()[1:])
    return sign * timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


# ─── Recurrence rules ────────────────────────────────────────────────────

_SUPPORTED_PARTS = frozenset({
    "FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH",
    "BYSETPOS", "WKST",
})


@dataclass
class _Rule:
    freq: str
    interval: int = 1
    count: int | None = None
    until: datetime | None = None          # naive as written
    until_utc: bool = False
    until_is_date: bool = False
    byday: list[tuple[int, int]] = field(default_factory=list)   # (ordinal or 0, weekday)
    bymonthday: list[int] = field(default_factory=list)
    bymonth: list[int] = field(default_factory=list)
    bysetpos: list[int] = field(default_factory=list)
    wkst: int = 0
    supported: bool = True

    @classmethod
    def parse(cls, value: str) -> "_Rule | None":
        parts: dict[str, str] = {}
        for piece in value.strip().split(";"):
            if "=" in piece:
                k, v = piece.split("=", 1)
                parts[k.strip().upper()] = v.strip().upper()
        freq = parts.get("FREQ", "")
        if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY", "HOURLY", "MINUTELY", "SECONDLY"):
            return None
        rule = cls(freq=freq)
        rule.supported = freq in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY") and set(parts) <= _SUPPORTED_PARTS
        try:
            rule.interval = max(1, int(parts.get("INTERVAL", "1")))
            if "COUNT" in parts:
                rule.count = max(0, int(parts["COUNT"]))
            if "UNTIL" in parts:
                rule.until = _parse_naive(parts["UNTIL"])
                rule.until_utc = parts["UNTIL"].endswith("Z")
                rule.until_is_date = len(parts["UNTIL"]) == 8
            for item in filter(None, parts.get("BYDAY", "").split(",")):
                m = re.fullmatch(r"([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)", item)
                if not m:
                    rule.supported = False
                    continue
                rule.byday.append((int(m.group(1) or 0), _WEEKDAYS.index(m.group(2))))
            rule.bymonthday = [int(x) for x in filter(None, parts.get("BYMONTHDAY", "").split(","))]
            rule.bymonth = [int(x) for x in filter(None, parts.get("BYMONTH", "").split(","))]
            rule.bysetpos = [int(x) for x in filter(None, parts.get("BYSETPOS", "").split(","))]
            if parts.get("WKST") in _WEEKDAYS:
                rule.wkst = _WEEKDAYS.index(parts["WKST"])
        except ValueError:
            return None
        if any(not 1 <= m <= 12 for m in rule.bymonth) or any(
            d == 0 or abs(d) > 31 for d in rule.bymonthday
        ):
            rule.supported = False
        return rule

    # Candidate days of one period, sorted.
    def _month_days(self, year: int, month: int, dtstart: datetime, *, ordinal_in_month: bool = True) -> list[date]:
        ndays = _cal.monthrange(year, month)[1]
        days: set[date] | None = None
        if self.bymonthday:
            md = set()
            for d in self.bymonthday:
                n = d if d > 0 else ndays + d + 1
                if 1 <= n <= ndays:
                    md.add(date(year, month, n))
            days = md
        if self.byday:
            bd = set()
            for ordn, wd in self.byday:
                matches = [date(year, month, n) for n in range(1, ndays + 1)
                           if date(year, month, n).weekday() == wd]
                if ordn == 0 or not ordinal_in_month:
                    bd.update(matches)
                elif 0 < ordn <= len(matches):
                    bd.add(matches[ordn - 1])
                elif ordn < 0 and -ordn <= len(matches):
                    bd.add(matches[ordn])
            days = bd if days is None else (days & bd)
        if days is None:
            if dtstart.day <= ndays:
                days = {date(year, month, dtstart.day)}
            else:
                days = set()
        return sorted(days)

    def _period_days(self, anchor: date, dtstart: datetime) -> list[date]:
        f = self.freq
        if f == "DAILY":
            d = anchor
            if self.bymonth and d.month not in self.bymonth:
                return []
            if self.bymonthday:
                ndays = _cal.monthrange(d.year, d.month)[1]
                if not any((x if x > 0 else ndays + x + 1) == d.day for x in self.bymonthday):
                    return []
            if self.byday and d.weekday() not in {wd for _o, wd in self.byday}:
                return []
            return [d]
        if f == "WEEKLY":
            wds = sorted({wd for _o, wd in self.byday}) if self.byday else [dtstart.weekday()]
            out = []
            for i in range(7):
                d = anchor + timedelta(days=i)
                if d.weekday() in wds and (not self.bymonth or d.month in self.bymonth):
                    out.append(d)
            return out
        if f == "MONTHLY":
            if self.bymonth and anchor.month not in self.bymonth:
                return []
            return self._month_days(anchor.year, anchor.month, dtstart)
        # YEARLY
        months = self.bymonth or ([dtstart.month] if not (self.byday and not self.bymonthday) else [])
        if months:
            out = []
            for m in sorted(months):
                if self.byday or self.bymonthday:
                    out += self._month_days(anchor.year, m, dtstart)
                elif dtstart.day <= _cal.monthrange(anchor.year, m)[1]:
                    out.append(date(anchor.year, m, dtstart.day))
            return sorted(out)
        # BYDAY with no BYMONTH: ordinals count within the year.
        out = []
        year_days = [date(anchor.year, 1, 1) + timedelta(days=i)
                     for i in range(366 if _cal.isleap(anchor.year) else 365)]
        for ordn, wd in self.byday:
            matches = [d for d in year_days if d.weekday() == wd]
            if ordn == 0:
                out += matches
            elif 0 < ordn <= len(matches):
                out.append(matches[ordn - 1])
            elif ordn < 0 and -ordn <= len(matches):
                out.append(matches[ordn])
        return sorted(set(out))

    def _apply_setpos(self, days: list[date]) -> list[date]:
        if not self.bysetpos or not days:
            return days
        out = set()
        for p in self.bysetpos:
            if 0 < p <= len(days):
                out.add(days[p - 1])
            elif p < 0 and -p <= len(days):
                out.add(days[p])
        return sorted(out)

    def _skip(self, dtstart: datetime, window_start: datetime) -> int:
        """Periods that can be stepped over without looking: none when
        COUNT must be counted from DTSTART, else all but one before the
        window (a series begun decades ago costs nothing)."""
        if self.count is not None or window_start <= dtstart:
            return 0
        d0, lo = dtstart.date(), window_start.date()
        if self.freq == "DAILY":
            n = (lo - d0).days // self.interval
        elif self.freq == "WEEKLY":
            n = (lo - d0).days // (7 * self.interval)
        elif self.freq == "MONTHLY":
            n = ((lo.year - d0.year) * 12 + lo.month - d0.month) // self.interval
        else:
            n = (lo.year - d0.year) // self.interval
        return max(0, n - 1)

    def _anchors(self, dtstart: datetime, k: int = 0) -> Iterator[date]:
        d0 = dtstart.date()
        while True:
            if self.freq == "DAILY":
                yield d0 + timedelta(days=k * self.interval)
            elif self.freq == "WEEKLY":
                week0 = d0 - timedelta(days=(d0.weekday() - self.wkst) % 7)
                yield week0 + timedelta(weeks=k * self.interval)
            elif self.freq == "MONTHLY":
                total = d0.month - 1 + k * self.interval
                y = d0.year + total // 12
                if y > 9998:
                    return
                yield date(y, total % 12 + 1, 1)
            else:
                y = d0.year + k * self.interval
                if y > 9998:
                    return
                yield date(y, 1, 1)
            k += 1

    def occurrences(
        self,
        dtstart: datetime,
        window_start: datetime,
        window_end: datetime,
        *,
        utc_until: Callable[[datetime], datetime] | None,
    ) -> Iterator[datetime]:
        """Occurrence wall-clock starts (naive, DTSTART's frame) in
        ``[window_start, window_end)``; ``COUNT`` counts from DTSTART, which
        is always the first occurrence. ``utc_until`` turns a wall-clock
        start into UTC, needed when ``UNTIL`` is written in UTC."""
        if not self.supported:
            if window_start <= dtstart < window_end:
                yield dtstart
            return
        emitted = 0
        steps = 0
        produced = 0
        tod = dtstart.time()

        def past_until(occ: datetime) -> bool:
            if self.until is None:
                return False
            if self.until_utc and utc_until is not None:
                return utc_until(occ).replace(tzinfo=None) > self.until
            limit = self.until
            if self.until_is_date:
                # UNTIL written as a DATE: that whole day is in.
                limit = datetime.combine(self.until.date(), time(23, 59, 59))
            return occ > limit

        for anchor in self._anchors(dtstart, self._skip(dtstart, window_start)):
            steps += 1
            if steps > MAX_RULE_STEPS:
                return
            # A period that begins past the window ends the series.
            if datetime.combine(anchor, time()) >= window_end:
                return
            days = self._apply_setpos(self._period_days(anchor, dtstart))
            for d in days:
                occ = datetime.combine(d, tod)
                if occ < dtstart:
                    continue
                if past_until(occ):
                    return
                if self.count is not None and produced >= self.count:
                    return
                produced += 1
                if occ >= window_end:
                    return
                if occ >= window_start:
                    yield occ
                    emitted += 1
                    if emitted >= MAX_OCCURRENCES:
                        return


# ─── Events ──────────────────────────────────────────────────────────────


def _dates_of(props: Iterable[_Prop], zones: _Zones) -> list[_When]:
    out: list[_When] = []
    for p in props:
        for piece in p.value.split(","):
            w = _when(_Prop(p.name, p.params, piece), zones)
            if w is not None:
                out.append(w)
    return out


def _key(w: _When) -> datetime:
    """A comparable instant for EXDATE / RECURRENCE-ID matching (an
    all-day value compares by its date)."""
    if w.all_day:
        return datetime.combine(w.local.date(), time()).replace(tzinfo=timezone.utc)
    return w.instant()


def parse_ics(
    text: str,
    *,
    window_start: datetime,
    window_end: datetime,
    max_events: int = MAX_EVENTS,
) -> IcsCalendar:
    """Every event occurrence starting before ``window_end`` and ending
    after ``window_start`` (aware datetimes). Raises :class:`IcsError`
    when ``text`` is not a VCALENDAR."""
    if "BEGIN:VCALENDAR" not in text[:4096].upper():
        raise IcsError("not an iCalendar feed")
    root = _components(text)
    cal = next((c for c in root.children if c.name == "VCALENDAR"), None)
    if cal is None:
        raise IcsError("not an iCalendar feed")
    name_prop = cal.first("X-WR-CALNAME")
    tz_prop = cal.first("X-WR-TIMEZONE")
    defined = {}
    for c in cal.children:
        if c.name == "VTIMEZONE":
            vt = _vtimezone(c)
            if vt is not None:
                defined[vt._tzid] = vt
    zones = _Zones(defined, tz_prop.value.strip() if tz_prop else None)

    masters: dict[str, _Component] = {}
    overrides: dict[str, list[_Component]] = {}
    singles: list[_Component] = []
    for c in cal.children:
        if c.name != "VEVENT":
            continue
        uid = (c.first("UID").value.strip() if c.first("UID") else "")
        if c.first("RECURRENCE-ID") is not None and uid:
            overrides.setdefault(uid, []).append(c)
        elif uid and uid not in masters:
            masters[uid] = c
        else:
            singles.append(c)

    # Overrides whose series isn't in the feed (one shared occurrence):
    # shown as they are.
    for uid in [u for u in overrides if u not in masters]:
        singles.extend(overrides.pop(uid))

    out: list[IcsEvent] = []
    truncated = False

    def add(ev: IcsEvent) -> bool:
        nonlocal truncated
        if len(out) >= max_events:
            truncated = True
            return False
        out.append(ev)
        return True

    def cancelled(c: _Component) -> bool:
        st = c.first("STATUS")
        return st is not None and st.value.strip().upper() == "CANCELLED"

    def span(c: _Component, start: _When) -> timedelta | None:
        end = _when(c.first("DTEND"), zones)
        if end is not None:
            if start.all_day:
                return timedelta(days=max(1, (end.local.date() - start.local.date()).days))
            delta = end.instant() - start.instant()
            return delta if delta >= timedelta(0) else None
        dur = c.first("DURATION")
        if dur is not None:
            d = _duration(dur.value)
            if d is not None and d >= timedelta(0):
                return d
        return timedelta(days=1) if start.all_day else None

    def emit(c: _Component, uid: str, start: _When, local: datetime, length: timedelta | None, suffix: str) -> bool:
        title = _clean_text(c.first("SUMMARY").value if c.first("SUMMARY") else None) or "(no title)"
        location = _clean_text(c.first("LOCATION").value if c.first("LOCATION") else None)
        if start.all_day:
            s = household_time.to_utc(datetime.combine(local.date(), time()))
            e = household_time.to_utc(datetime.combine(local.date() + (length or timedelta(days=1)), time()))
        else:
            s = start.instant(local)
            e = s + length if length is not None else None
        if s >= window_end or (e or s) < window_start:
            return True
        return add(IcsEvent(uid=f"{uid}{suffix}"[:300], title=title, start=s, end=e,
                            all_day=start.all_day, location=location))

    for c in singles:
        if cancelled(c):
            continue
        start = _when(c.first("DTSTART"), zones)
        if start is None:
            continue
        uid = c.first("UID").value.strip() if c.first("UID") else f"anon-{len(out)}"
        if not emit(c, uid, start, start.local, span(c, start), ""):
            break

    for uid, c in masters.items():
        if truncated:
            break
        start = _when(c.first("DTSTART"), zones)
        if start is None:
            continue
        length = span(c, start)
        moved: dict[datetime, _Component] = {}
        for o in overrides.get(uid, []):
            rid = _when(o.first("RECURRENCE-ID"), zones)
            if rid is not None:
                moved[_key(rid)] = o
        if cancelled(c):
            continue
        rule = _Rule.parse(c.first("RRULE").value) if c.first("RRULE") else None
        rdates = _dates_of(c.all("RDATE"), zones)
        if rule is None and not rdates:
            if not emit(c, uid, start, start.local, length, ""):
                break
            continue
        exdates = {_key(w) for w in _dates_of(c.all("EXDATE"), zones)}
        # Expand in DTSTART's own frame, a margin either side of the window
        # (an occurrence that starts before the window may still be on).
        margin = (length or timedelta(0)) + timedelta(days=2)
        lo_i, hi_i = window_start - margin, window_end + timedelta(days=2)
        if start.utc:
            lo, hi = lo_i.astimezone(timezone.utc).replace(tzinfo=None), hi_i.astimezone(timezone.utc).replace(tzinfo=None)
        elif start.zone is not None and not start.all_day:
            lo, hi = lo_i.astimezone(start.zone).replace(tzinfo=None), hi_i.astimezone(start.zone).replace(tzinfo=None)
        else:
            lo, hi = household_time.to_local(lo_i), household_time.to_local(hi_i)
        starts: list[datetime] = []
        if rule is not None:
            starts = list(rule.occurrences(
                start.local, lo, hi,
                utc_until=lambda occ, _s=start: _s.instant(occ),
            ))
        elif lo <= start.local < hi:
            starts = [start.local]
        starts += [w.local for w in rdates if lo <= w.local < hi]
        for local in sorted(set(starts)):
            k = _key(_When(local, start.all_day, start.utc, start.zone))
            if k in exdates:
                continue
            o = moved.pop(k, None)
            if o is not None:
                if cancelled(o):
                    continue
                ostart = _when(o.first("DTSTART"), zones) or start
                if not emit(o, uid, ostart, ostart.local, span(o, ostart) or length,
                            f"@{local.isoformat()}"):
                    break
                continue
            if not emit(c, uid, start, local, length, f"@{local.isoformat()}"):
                break
        # An override moved INTO the window from an occurrence outside it.
        for o in moved.values():
            if truncated or cancelled(o):
                continue
            ostart = _when(o.first("DTSTART"), zones)
            if ostart is not None:
                rid = _when(o.first("RECURRENCE-ID"), zones)
                emit(o, uid, ostart, ostart.local, span(o, ostart) or length,
                     f"@{(rid.local if rid else ostart.local).isoformat()}")

    out.sort(key=lambda e: (e.start, e.uid))
    name = _clean_text(name_prop.value) if name_prop else None
    return IcsCalendar(name=name, events=out, truncated=truncated)
