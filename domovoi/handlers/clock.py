"""Clock handler — current time, date, day-of-week, year, month, plus the
date of yesterday/tomorrow.

Reads the Domovoi server host's local wall clock — no timezone arguments.
For a homelab where every Pi is on the same LAN as the Domovoi server, the
host's local time is the right answer to "what time is it?". Cross-zone
queries ("what time is it in Tokyo?") would need an Ollama-side handoff,
which is out of scope here.

Fully local; declares ``requires_network="no"``.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


# ─── Fast-path regexes ────────────────────────────────────────────────────
# Router lower-cases + strips trailing punctuation before dispatching, so
# patterns are anchored on a normalized lower-case transcript.

_TIME_RE = re.compile(
    r"^(?:"
    r"what(?:'s| is) the time"
    r"|what time is it"
    r"|tell me the time"
    r"|current time"
    r"|time check"
    r"|do you (?:have|know) the time"
    r"|do you know what time it is"
    r")$"
)

# Full date — "Monday, May 4th, 2026". Covers the spoken variants for
# "what's the date" plus the bare "what's today" which conversationally
# wants the date, not just the day-of-week.
_FULL_DATE_RE = re.compile(
    r"^(?:"
    r"what(?:'s| is) (?:today's|the) date(?: today)?"
    r"|what(?:'s| is) today(?:'s date)?"
    r"|today's date"
    r"|what date is it(?: today)?"
    r")$"
)

# Day of week — "Monday". Distinct from FULL_DATE so users who want
# just the day name get a tight answer.
_DAY_OF_WEEK_RE = re.compile(
    r"^(?:"
    r"what day is (?:it|today)"
    r"|what day of the week is it"
    r")$"
)

_YEAR_RE = re.compile(
    r"^(?:"
    r"what year is it"
    r"|what(?:'s| is) (?:the |this )?year"
    r"|current year"
    r")$"
)

_MONTH_RE = re.compile(
    r"^(?:"
    r"what month is it"
    r"|what(?:'s| is) (?:the |this )?month"
    r"|current month"
    r")$"
)

_TOMORROW_RE = re.compile(
    r"^(?:"
    r"what(?:'s| is) tomorrow(?:'s date)?"
    r"|tomorrow's date"
    r"|what day is tomorrow"
    r")$"
)

_YESTERDAY_RE = re.compile(
    r"^(?:"
    r"what (?:was|is) yesterday(?:'s date)?"
    r"|yesterday's date"
    r"|what day was yesterday"
    r")$"
)


# ─── Formatting helpers ───────────────────────────────────────────────────

def _ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 4th, ..., 21st, 22nd, ...

    The 11/12/13 carve-out is the irregular case: "11th" not "11st".
    Generalizes correctly past 100 (111th, 112th, 113th).
    """
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    suffix = ["th", "st", "nd", "rd", "th", "th", "th", "th", "th", "th"][n % 10]
    return f"{n}{suffix}"


def _format_time(now: datetime) -> str:
    """12-hour clock without leading zero, e.g. '3:47 PM' or '12:00 AM'.

    Built manually rather than via strftime because the cross-platform
    leading-zero strip flag varies (``%-I`` on POSIX, ``%#I`` on Windows)
    and ``%I`` alone gives "03:47 PM", which sounds clipped through TTS.
    """
    h = now.hour
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{now.minute:02d} {suffix}"


def _format_full_date(d: date) -> str:
    """'Monday, May 4th, 2026'."""
    return f"{d.strftime('%A, %B')} {_ordinal(d.day)}, {d.year}"


def _format_short_date(d: date) -> str:
    """'Monday, May 4th' — for tomorrow / yesterday where the year is
    rarely useful (and would distract on Dec 31 → "Tuesday, January 1st,
    2027" feels overspecified). Year-changing edge cases are 2 days a year;
    not worth special-casing.
    """
    return f"{d.strftime('%A, %B')} {_ordinal(d.day)}"


def _now() -> datetime:
    """Local wall-clock. Tiny indirection so tests can monkeypatch."""
    return datetime.now()


# ─── Handler ──────────────────────────────────────────────────────────────

class ClockHandler(Handler):
    name = "clock"
    # band rationale: utility cluster; no known collisions.
    priority_band = 170
    display = HandlerDisplay(label="Clock", tone="info")
    requires_network = "no"

    tool_schema = {
        "name": "clock",
        "description": (
            "Report the current time, date, day of the week, year, month, "
            "or the date of yesterday/tomorrow against the local wall clock. "
            "No timezone arguments — pick the closest 'kind' to what the "
            "user asked for."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "time",
                        "date",
                        "day_of_week",
                        "year",
                        "month",
                        "tomorrow",
                        "yesterday",
                    ],
                },
            },
            "required": ["kind"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_TIME_RE, ClockHandler._time_from_match),
            FastPath(_FULL_DATE_RE, ClockHandler._date_from_match),
            FastPath(_DAY_OF_WEEK_RE, ClockHandler._day_of_week_from_match),
            FastPath(_YEAR_RE, ClockHandler._year_from_match),
            FastPath(_MONTH_RE, ClockHandler._month_from_match),
            FastPath(_TOMORROW_RE, ClockHandler._tomorrow_from_match),
            FastPath(_YESTERDAY_RE, ClockHandler._yesterday_from_match),
        ]

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return Response(
            text="I didn't catch a time or date question.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        kind = args.get("kind")
        if kind == "time":
            return self._time_response(ctx)
        if kind == "date":
            return self._full_date_response(ctx)
        if kind == "day_of_week":
            return self._day_of_week_response(ctx)
        if kind == "year":
            return self._year_response(ctx)
        if kind == "month":
            return self._month_response(ctx)
        if kind == "tomorrow":
            return self._tomorrow_response(ctx)
        if kind == "yesterday":
            return self._yesterday_response(ctx)
        return Response(
            text=f"I don't know how to report {kind!r}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    # ─── Fast-path adapters ───────────────────────────────────────────
    async def _time_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._time_response(ctx)

    async def _date_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._full_date_response(ctx)

    async def _day_of_week_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._day_of_week_response(ctx)

    async def _year_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._year_response(ctx)

    async def _month_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._month_response(ctx)

    async def _tomorrow_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._tomorrow_response(ctx)

    async def _yesterday_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._yesterday_response(ctx)

    # ─── Core responses ───────────────────────────────────────────────
    def _time_response(self, ctx: Context) -> Response:
        return Response(
            text=f"It's {_format_time(_now())}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    def _full_date_response(self, ctx: Context) -> Response:
        return Response(
            text=f"It's {_format_full_date(_now().date())}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    def _day_of_week_response(self, ctx: Context) -> Response:
        return Response(
            text=f"It's {_now().strftime('%A')}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    def _year_response(self, ctx: Context) -> Response:
        return Response(
            text=f"It's {_now().year}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    def _month_response(self, ctx: Context) -> Response:
        return Response(
            text=f"It's {_now().strftime('%B')}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    def _tomorrow_response(self, ctx: Context) -> Response:
        tomorrow = _now().date() + timedelta(days=1)
        return Response(
            text=f"Tomorrow is {_format_short_date(tomorrow)}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    def _yesterday_response(self, ctx: Context) -> Response:
        yesterday = _now().date() - timedelta(days=1)
        return Response(
            text=f"Yesterday was {_format_short_date(yesterday)}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )
