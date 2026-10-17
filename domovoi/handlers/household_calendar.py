"""CalendarHandler — read the household calendar by voice.

"what's on my calendar today / tomorrow / this week", "what's next on my
calendar", "do I have anything on Friday", "am I free tomorrow". Before
this handler a calendar question reached the answer model's volatile gate
and was offered as a web search ("I'd need the internet to get you that -
want me to check my calendar today?", manual-content finding, 2026-10-17).

It reads ``calendar_events``, the table the dashboard's and the app's
Calendar page keeps (web/backend/api/calendar.py): local events and any
synced ones. **Read-only**: nothing is added, moved or deleted by voice; a
request to ("add the dentist to my calendar") is answered with where to do
it instead. Offline: one query against the local database.

Days are the household's (domovoi/household_time.py, the server's own
zone): "today" is from this midnight to the next on the wall clock, DST
days included.

People: ``calendar_events`` has no owner per event, so every voice reads
the one household calendar; "my calendar" and "the calendar" are the same
thing. When events carry a person, filter on ``ctx.person_id`` here.

The tool is offered to the routing model only for an utterance that names
a calendar, an agenda, an appointment or a schedule (``offers_tool``), so
no other turn's tool list changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi import household_time
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.handlers.shared.clock_time import WEEKDAYS, spoken_clock, spoken_day
from domovoi.models import Context, Intent, Response

# ─── Fast-path regexes ────────────────────────────────────────────────────

_CAL = r"(?:calendar|schedule|agenda|diary)"
_MY = r"(?:my|the|our)"
_WEEKDAY = "(?:" + "|".join(WEEKDAYS) + ")"
# A day or a span of days, with the "on"/"for" that can lead it.
_RANGE = (
    r"(?:(?:on |for )?(?:today|tonight|tomorrow|the day after tomorrow"
    r"|this week|next week|this weekend|the weekend|next weekend"
    rf"|(?:this |next |the )?{_WEEKDAY}))"
)
_R = rf"(?P<range>{_RANGE})"
_WHAT_IS = r"(?:what(?:'s| is)|whats)"

# "what's on my calendar today" / "what is on the calendar for friday" /
# "what do i have on my calendar tomorrow" / "what's my schedule like
# tomorrow" / "read me my calendar" / "check the calendar for this week" /
# "what's on today" / "what do i have tomorrow". One pattern per shape:
# each names its own range group.
_WHATS_ON_PATTERNS = tuple(re.compile(p) for p in (
    rf"^(?:{_WHAT_IS}|what do (?:i|we) have|what have (?:i|we) got) (?:on|in) {_MY} {_CAL}"
    rf"(?: like)?(?:,? {_R})?$",
    rf"^{_WHAT_IS} {_MY} {_CAL}(?: like)?(?:,? {_R})?$",
    rf"^(?:read|tell|give|go over|run through|check)(?: me| us)? {_MY} {_CAL}(?:,? {_R})?$",
    rf"^{_WHAT_IS} on {_R}$",
    rf"^what do (?:i|we) have (?:on |going on |planned |scheduled )?{_R}$",
    rf"^what(?:'s| is| have (?:i|we) got) (?:planned|scheduled|going on) {_R}$",
))

# "what's next on my calendar" / "what's coming up on the calendar" /
# "what's my next appointment" / "when's my next event"
_NEXT_RE = re.compile(
    rf"^{_WHAT_IS} (?:next|coming up|up next) (?:on|in) {_MY} {_CAL}$"
    rf"|^(?:{_WHAT_IS}|when(?:'s| is)) {_MY} next (?:appointment|event|meeting|calendar event)$"
)

# "do i have anything on friday" / "is there anything on my calendar
# tomorrow" / "do we have any plans this weekend" / "am i free on friday".
# A day must close it: "do i have anything by weezer" is the library's.
_ANYTHING_RE = re.compile(
    r"^(?:do (?:i|we) have|have (?:i|we) got|is there|are there)"
    r" (?:anything|something|any (?:appointments?|events?|meetings?|plans)|plans)"
    rf"(?: (?:planned|scheduled|going on|on {_MY} {_CAL}|in {_MY} {_CAL}))?,? {_R}$"
)
_FREE_RE = re.compile(rf"^(?:am i|are we) (?P<free>free|busy) {_R}$")

# "add the dentist to my calendar", "put soccer on the calendar for friday",
# "cancel the dentist on my calendar": a write. Answered, never done.
_WRITE_RE = re.compile(
    r"^(?:add|put|schedule|create|make|book|set up|enter|write|move|reschedule"
    r"|cancel|delete|remove|clear|change)\b.* (?:to|on|in|onto|into|from|off) "
    rf"{_MY} {_CAL}\b.*$"
)

# Words that say the utterance is about the calendar: the tool gate.
_CALENDAR_CUE_RE = re.compile(
    r"\b(?:calendar|agenda|diary|appointments?"
    r"|(?:my|our|the|today's|tomorrow's|this week's) schedule)\b"
)

# Said at most, then "and N more".
_MAX_SPOKEN = 6


@dataclass(frozen=True)
class _Span:
    start: datetime          # wall clock, naive, inclusive
    end: datetime            # wall clock, naive, exclusive
    said: str                # "today", "tomorrow", "on Friday", "this week"
    single_day: bool
    today: bool = False      # "today": only what's still to come is read


def _span(spoken: str | None, now: datetime) -> _Span:
    """The wall-clock span a range phrase names, said at ``now``. None or
    an unknown phrase is today."""
    t = (spoken or "today").strip().lower()
    t = re.sub(r"^(?:on|for) ", "", t)
    today = now.date()

    def day(d: date, said: str, *, is_today: bool = False) -> _Span:
        start = datetime.combine(d, time())
        return _Span(start, start + timedelta(days=1), said, True, is_today)

    monday = today - timedelta(days=today.weekday())
    if t in ("today", ""):
        return day(today, "today", is_today=True)
    if t == "tonight":
        start = datetime.combine(today, time(17))
        return _Span(start, datetime.combine(today + timedelta(days=1), time(5)),
                     "tonight", True, True)
    if t == "tomorrow":
        return day(today + timedelta(days=1), "tomorrow")
    if t == "the day after tomorrow":
        d = today + timedelta(days=2)
        return day(d, spoken_day(d, today))
    if t == "this week":
        return _Span(now, datetime.combine(monday + timedelta(days=7), time()),
                     "this week", False)
    if t == "next week":
        start = datetime.combine(monday + timedelta(days=7), time())
        return _Span(start, start + timedelta(days=7), "next week", False)
    if t in ("this weekend", "the weekend", "next weekend"):
        saturday = monday + timedelta(days=5)
        if t == "next weekend":
            saturday += timedelta(days=7)
        start = max(now, datetime.combine(saturday, time()))
        return _Span(start, datetime.combine(saturday + timedelta(days=2), time()),
                     "next weekend" if t == "next weekend" else "this weekend", False)
    m = re.fullmatch(rf"(?:(this|next|the) )?({_WEEKDAY})", t)
    if m:
        ahead = (WEEKDAYS.index(m.group(2)) - today.weekday()) % 7
        if m.group(1) == "next" and ahead == 0:
            ahead = 7
        d = today + timedelta(days=ahead)
        said = "today" if ahead == 0 else spoken_day(d, today)
        return day(d, said, is_today=ahead == 0)
    return day(today, "today", is_today=True)


@dataclass(frozen=True)
class _Event:
    title: str
    start: datetime          # wall clock, naive
    end: datetime | None
    all_day: bool


def _and_join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _item(e: _Event) -> str:
    """"Dentist at 10 AM", "Mom's birthday, all day"."""
    title = " ".join(e.title.split()) or "something"
    if e.all_day:
        return f"{title}, all day"
    return f"{title} at {spoken_clock(e.start)}"


def _capitalized(s: str) -> str:
    return s[:1].upper() + s[1:]


class CalendarHandler(Handler):
    name = "calendar"
    # band rationale: utility cluster after clock (170): "what's tomorrow"
    #   (the date) stays the clock's, and "do i have anything on friday" is
    #   taken here, ahead of the library's "do i have X" (310), which would
    #   look for a song called "anything on friday". Every pattern is
    #   anchored on calendar words or ends in a closed day phrase.
    priority_band = 175
    display = HandlerDisplay(label="Calendar", tone="info")
    example_phrases = (
        "what's on my calendar today",
        "what's next on my calendar",
        "do I have anything on Friday",
    )
    requires_network = "no"

    tool_schema = {
        "name": "calendar",
        "description": (
            "Read the household calendar: what is on it for a day or a week, "
            "or the next event. Read-only: it cannot add, move or delete events."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "when": {
                    "type": "string",
                    "description": (
                        "today, tomorrow, this week, next week, this weekend, a "
                        "weekday such as friday, or next for the next event"
                    ),
                },
            },
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_NEXT_RE, CalendarHandler._next_from_match),
            *(FastPath(p, CalendarHandler._whats_on_from_match) for p in _WHATS_ON_PATTERNS),
            FastPath(_ANYTHING_RE, CalendarHandler._anything_from_match),
            FastPath(_FREE_RE, CalendarHandler._anything_from_match),
            FastPath(_WRITE_RE, CalendarHandler._write_from_match),
        ]

    def offers_tool(self, transcript: str) -> bool:
        # Offered only when the calendar is named: everything else's tool
        # list (and the routing model's cached prompt) stays as it was.
        return bool(_CALENDAR_CUE_RE.search(transcript or ""))

    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        return await self._whats_on(None, ctx, session)

    async def execute_from_tool(
        self, args: dict[str, Any], ctx: Context, session: AsyncSession
    ) -> Response:
        when = str(args.get("when") or "").strip().lower()
        if when in ("next", "next event", "the next event", "upcoming"):
            return await self._next(ctx, session)
        return await self._whats_on(when or None, ctx, session)

    # ─── Fast-path adapters ───────────────────────────────────────────
    async def _whats_on_from_match(self, m, ctx, session) -> Response:
        return await self._whats_on(m.group("range"), ctx, session)

    async def _next_from_match(self, m, ctx, session) -> Response:
        return await self._next(ctx, session)

    async def _anything_from_match(self, m, ctx, session) -> Response:
        return await self._anything(m.group("range"), m.groupdict().get("free"), ctx, session)

    async def _write_from_match(self, m, ctx, session) -> Response:
        return self._reply(
            ctx,
            "I can only read the calendar, not change it. Add or change "
            "events on the Calendar page, on the dashboard or in the app.",
        )

    # ─── Reading ──────────────────────────────────────────────────────
    def _reply(self, ctx: Context, text_: str) -> Response:
        return Response(text=text_, session_id=ctx.session_id, matched_handler=self.name)

    async def _events(
        self, session: AsyncSession, start: datetime, end: datetime | None, *, limit: int = 50
    ) -> list[_Event]:
        """Events starting in [start, end) on the household wall clock."""
        params: dict[str, Any] = {"start": household_time.to_utc(start), "limit": limit}
        where = "starts_at >= :start"
        if end is not None:
            where += " AND starts_at < :end"
            params["end"] = household_time.to_utc(end)
        rows = await session.execute(
            text(
                f"""
                SELECT title, starts_at, ends_at, all_day
                FROM calendar_events
                WHERE {where}
                ORDER BY starts_at ASC, id ASC
                LIMIT :limit
                """
            ),
            params,
        )
        return [
            _Event(
                title=str(r[0] or ""),
                start=household_time.to_local(r[1]),
                end=household_time.to_local(r[2]) if r[2] is not None else None,
                all_day=bool(r[3]),
            )
            for r in rows.all()
        ]

    @staticmethod
    def _still_to_come(e: _Event, now: datetime) -> bool:
        if e.all_day:
            return True
        return (e.end or e.start + timedelta(hours=1)) > now

    def _list_text(self, events: list[_Event], span: _Span, now: datetime) -> str:
        if span.single_day:
            items = [_item(e) for e in events[:_MAX_SPOKEN]]
            more = len(events) - len(items)
            if more:
                items.append(f"{more} more")
            lead = _capitalized(span.said)
            if len(events) == 1:
                return f"{lead} you have {items[0]}."
            return f"{lead} you have {len(events)} things: {_and_join(items)}."
        # Several days: grouped by day, each named.
        groups: list[str] = []
        said = 0
        by_day: dict[date, list[_Event]] = {}
        for e in events:
            by_day.setdefault(e.start.date(), []).append(e)
        for d, day_events in by_day.items():
            if said >= _MAX_SPOKEN:
                break
            take = day_events[: _MAX_SPOKEN - said]
            said += len(take)
            groups.append(f"{spoken_day(d, now.date())}, {_and_join([_item(e) for e in take])}")
        text_ = f"{_capitalized(span.said)}: " + "; ".join(groups) + "."
        if len(events) > said:
            text_ += f" And {len(events) - said} more on the calendar page."
        return text_

    async def _whats_on(self, spoken: str | None, ctx: Context, session: AsyncSession) -> Response:
        now = household_time.now()
        span = _span(spoken, now)
        events = await self._events(session, span.start, span.end)
        if span.today:
            upcoming = [e for e in events if self._still_to_come(e, now)]
            if not events:
                return self._reply(ctx, await self._nothing_then_next(span, now, session))
            if not upcoming:
                return self._reply(ctx, f"That's everything for {span.said}: nothing else on the calendar.")
            if len(upcoming) < len(events):
                items = [_item(e) for e in upcoming[:_MAX_SPOKEN]]
                if len(upcoming) > len(items):
                    items.append(f"{len(upcoming) - len(items)} more")
                return self._reply(ctx, f"Still to come {span.said}: {_and_join(items)}.")
        if not events:
            return self._reply(ctx, await self._nothing_then_next(span, now, session))
        return self._reply(ctx, self._list_text(events, span, now))

    async def _nothing_then_next(self, span: _Span, now: datetime, session: AsyncSession) -> str:
        """"Nothing on the calendar tomorrow." - plus the next event, when
        there is one within a week of the span's end."""
        text_ = f"Nothing on the calendar {span.said}."
        nxt = await self._events(session, max(span.end, now), span.end + timedelta(days=7), limit=1)
        if nxt:
            text_ += f" The next thing is {_item(nxt[0])} {self._day_of(nxt[0], now)}."
        return text_

    @staticmethod
    def _day_of(e: _Event, now: datetime) -> str:
        return spoken_day(e.start.date(), now.date())

    async def _next(self, ctx: Context, session: AsyncSession) -> Response:
        now = household_time.now()
        events = await self._events(session, now, None, limit=1)
        if not events:
            return self._reply(ctx, "Nothing coming up on the calendar.")
        e = events[0]
        return self._reply(ctx, f"Next on the calendar: {_item(e)} {self._day_of(e, now)}.")

    async def _anything(
        self, spoken: str, free: str | None, ctx: Context, session: AsyncSession
    ) -> Response:
        now = household_time.now()
        span = _span(spoken, now)
        events = await self._events(session, span.start, span.end)
        if span.today:
            events = [e for e in events if self._still_to_come(e, now)]
        if not events:
            if free == "busy":
                return self._reply(ctx, f"No, nothing on the calendar {span.said}.")
            if free == "free":
                return self._reply(ctx, f"Yes, nothing on the calendar {span.said}.")
            return self._reply(ctx, f"No, nothing on the calendar {span.said}.")
        listed = self._list_text(events, span, now)
        if free == "free":
            return self._reply(ctx, f"Not completely. {listed}")
        return self._reply(ctx, f"Yes. {listed}")
