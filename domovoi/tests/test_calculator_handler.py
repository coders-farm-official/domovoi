"""CalculatorHandler tests — regex coverage, arithmetic correctness,
percentage / unit / date / tip+split behavior, and anti-poach.

Most tests are pure compute paths and need no DB; a couple of router-
level integration tests run under the existing `requires_db` skip so
they short-circuit when Postgres isn't reachable.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pytest

from domovoi.handlers.calculator import (
    CalculatorHandler,
    _ARITH_BARE_RE,
    _ARITH_PREFIX_RE,
    _DAYS_FUTURE_RE,
    _DAYS_PAST_RE,
    _DAYS_UNTIL_RE,
    _HOURS_FUTURE_RE,
    _HOURS_PAST_RE,
    _IN_N_DAYS_RE,
    _IN_N_HOURS_RE,
    _NEXT_WEEKDAY_RE,
    _PERCENT_ADJUST_RE,
    _PERCENT_INVERSE_RE,
    _PERCENT_OF_RE,
    _SPLIT_RE,
    _SPLIT_TIP_RE,
    _SQRT_RE,
    _TIP_RE,
    _UNIT_CONVERT_RE,
    _UNIT_HOW_MANY_RE,
    _UNIT_SHORT_RE,
    _format_number,
    _normalize_math,
    _safe_eval,
)
from domovoi.models import Context, Intent
from domovoi.tests.conftest import requires_db


# ─── Arithmetic regex coverage ───────────────────────────────────────


@pytest.mark.parametrize(
    "transcript",
    [
        "what's 47 times 89",
        "what is 47 times 89",
        "calculate 5 + 3",
        "compute sqrt(144)",
        "how much is 12 plus 8",
        "what's 5 to the power of 3",
    ],
)
def test_arith_prefix_matches(transcript: str) -> None:
    assert _ARITH_PREFIX_RE.match(transcript), transcript


@pytest.mark.parametrize(
    "transcript",
    [
        "100 divided by 7",
        "47 plus 89 minus 12",
        "5 squared",
        "5 cubed",
        "5 to the power of 3",
        "2 times 2 times 2",
    ],
)
def test_arith_bare_matches(transcript: str) -> None:
    assert _ARITH_BARE_RE.match(transcript), transcript


@pytest.mark.parametrize(
    "transcript",
    [
        # Music + intercom + library/etc neighbors — must NOT poach.
        "play 47 times by some artist",
        "what time is it",
        "what's the weather",
        "what's playing",
        "set a timer for 5 minutes",
        "how many songs in my library",
    ],
)
def test_arith_regexes_do_not_poach(transcript: str) -> None:
    assert not _ARITH_PREFIX_RE.match(transcript), transcript
    assert not _ARITH_BARE_RE.match(transcript), transcript


# ─── Percentage regex coverage ───────────────────────────────────────


@pytest.mark.parametrize(
    "transcript",
    [
        "47% of 89",
        "47 percent of 89",
        "what's 20% of 50",
        "what is 8% of 100",
        "10% of $200",
        "0.5% of 1000",
    ],
)
def test_percent_of_matches(transcript: str) -> None:
    assert _PERCENT_OF_RE.match(transcript), transcript


@pytest.mark.parametrize(
    "transcript",
    [
        "what percent of 80 is 20",
        "what percentage of 100 is 25",
        "what percent of $200 is $50",
    ],
)
def test_percent_inverse_matches(transcript: str) -> None:
    assert _PERCENT_INVERSE_RE.match(transcript), transcript


@pytest.mark.parametrize(
    "transcript",
    [
        "8% tax on $47",
        "20% off $89",
        "5 percent tax on 100",
        "25% off $40",
    ],
)
def test_percent_adjust_matches(transcript: str) -> None:
    assert _PERCENT_ADJUST_RE.match(transcript), transcript


@pytest.mark.parametrize(
    "transcript",
    [
        "what's a percentage point",  # not a calculation
        "the percentage was high",
    ],
)
def test_percent_regexes_do_not_poach(transcript: str) -> None:
    assert not _PERCENT_OF_RE.match(transcript)
    assert not _PERCENT_INVERSE_RE.match(transcript)
    assert not _PERCENT_ADJUST_RE.match(transcript)


# ─── Unit conversion regex coverage ──────────────────────────────────


@pytest.mark.parametrize(
    "transcript",
    [
        "how many oz in 100 grams",
        "how many ounces in 100 grams",
        "how many ounces are in 100 grams",
        "how many cm in 5 inches",
        "how many feet in 100 meters",
        "how many minutes in 2 hours",
    ],
)
def test_unit_how_many_matches(transcript: str) -> None:
    assert _UNIT_HOW_MANY_RE.match(transcript), transcript


@pytest.mark.parametrize(
    "transcript",
    [
        "convert 100 grams to oz",
        "convert 5 feet to cm",
        "convert 1 cup into ml",
    ],
)
def test_unit_convert_matches(transcript: str) -> None:
    assert _UNIT_CONVERT_RE.match(transcript), transcript


@pytest.mark.parametrize(
    "transcript",
    [
        "100 grams in oz",
        "5 feet in cm",
        "2 hours in minutes",
    ],
)
def test_unit_short_matches(transcript: str) -> None:
    assert _UNIT_SHORT_RE.match(transcript), transcript


@pytest.mark.parametrize(
    "transcript",
    [
        # The key anti-poach: "how many songs" / "how many albums" must
        # NOT match the unit-conversion regex even though it shares the
        # "how many X in Y Z" shape, because the words are not units.
        "how many songs in my library",
        "how many albums by radiohead",
        # Plain Q&A.
        "how many people are in the room",
    ],
)
def test_unit_regexes_do_not_poach(transcript: str) -> None:
    assert not _UNIT_HOW_MANY_RE.match(transcript), transcript
    assert not _UNIT_CONVERT_RE.match(transcript), transcript
    assert not _UNIT_SHORT_RE.match(transcript), transcript


# ─── Date math regex coverage ────────────────────────────────────────


@pytest.mark.parametrize(
    "transcript,regex",
    [
        ("90 days from today", _DAYS_FUTURE_RE),
        ("1 day from now", _DAYS_FUTURE_RE),
        ("in 30 days", _IN_N_DAYS_RE),
        ("5 days ago", _DAYS_PAST_RE),
        ("4 hours from now", _HOURS_FUTURE_RE),
        ("in 4 hours", _IN_N_HOURS_RE),
        ("12 hours ago", _HOURS_PAST_RE),
        ("days until christmas", _DAYS_UNTIL_RE),
        ("how many days until halloween", _DAYS_UNTIL_RE),
        ("next monday", _NEXT_WEEKDAY_RE),
        ("next friday", _NEXT_WEEKDAY_RE),
    ],
)
def test_date_math_matches(transcript: str, regex) -> None:
    assert regex.match(transcript), transcript


@pytest.mark.parametrize(
    "transcript",
    [
        # Anti-poach: clock / timer / reminder neighbors.
        "what time is it",
        "what time in 4 hours",  # ambiguous, not the in-N-hours form
        "set a timer for 4 hours",
        "remind me to call mom in 4 hours",
        "in 5 minutes",  # we deliberately only support hours/days here
    ],
)
def test_date_math_does_not_poach(transcript: str) -> None:
    for regex in (
        _DAYS_FUTURE_RE,
        _IN_N_DAYS_RE,
        _DAYS_PAST_RE,
        _HOURS_FUTURE_RE,
        _IN_N_HOURS_RE,
        _HOURS_PAST_RE,
        _DAYS_UNTIL_RE,
        _NEXT_WEEKDAY_RE,
    ):
        assert not regex.match(transcript), (transcript, regex.pattern)


# ─── Tip/split regex coverage ────────────────────────────────────────


@pytest.mark.parametrize(
    "transcript",
    [
        "20% tip on $50",
        "20 percent tip on $50",
        "18% tip on 75",
        "15% tip on $100",
    ],
)
def test_tip_matches(transcript: str) -> None:
    assert _TIP_RE.match(transcript), transcript


@pytest.mark.parametrize(
    "transcript",
    [
        "split $200 4 ways",
        "split 100 2 ways",
        "split $30 3 ways",
    ],
)
def test_split_matches(transcript: str) -> None:
    assert _SPLIT_RE.match(transcript), transcript


@pytest.mark.parametrize(
    "transcript",
    [
        "split $200 4 ways with 20% tip",
        "split 60 3 ways with 15 percent tip",
    ],
)
def test_split_with_tip_matches(transcript: str) -> None:
    assert _SPLIT_TIP_RE.match(transcript), transcript


# ─── Arithmetic computation ──────────────────────────────────────────


def test_arithmetic_integer() -> None:
    assert _safe_eval(_normalize_math("47 times 89")) == 4183


def test_arithmetic_chained() -> None:
    assert _safe_eval(_normalize_math("47 plus 89 minus 12")) == 124


def test_arithmetic_float_division() -> None:
    assert _safe_eval(_normalize_math("100 divided by 7")) == pytest.approx(14.2857142857)


def test_arithmetic_parens() -> None:
    assert _safe_eval("(2 + 3) * 4") == 20


def test_arithmetic_sqrt() -> None:
    assert _safe_eval(_normalize_math("square root of 144")) == 12.0


def test_arithmetic_power() -> None:
    assert _safe_eval(_normalize_math("5 to the power of 3")) == 125
    assert _safe_eval(_normalize_math("5 squared")) == 25


def test_arithmetic_rejects_disallowed_names() -> None:
    """Names other than `sqrt` are rejected before simpleeval gets to
    eval them — defense-in-depth on top of simpleeval's own filtering."""
    with pytest.raises(ValueError):
        _safe_eval("foo + 5")


def test_arithmetic_rejects_disallowed_functions() -> None:
    with pytest.raises(ValueError):
        _safe_eval("abs(-5)")


def test_format_number_integer() -> None:
    assert _format_number(4183) == "4,183"
    assert _format_number(1_000_000) == "1,000,000"


def test_format_number_float_rounds_to_4_sig_figs() -> None:
    # 14.28571... → 14.29 (4 sig figs).
    assert _format_number(14.285714) == "14.29"
    # 0.001234 → 0.001234 (4 sig figs).
    assert _format_number(0.001234) == "0.001234"


def test_format_number_strips_trailing_zeros() -> None:
    assert _format_number(12.0) == "12"
    assert _format_number(12.50) == "12.5"


@pytest.mark.asyncio
async def test_arithmetic_handler_divide_by_zero_responds_gracefully() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_arithmetic("10 divided by 0", ctx)
    assert "can't divide by zero" in resp.text.lower()


@pytest.mark.asyncio
async def test_arithmetic_handler_oversized_operand_responds_gracefully() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_arithmetic("99999999999999999 plus 1", ctx)
    # Either rejected on operand-magnitude check ("couldn't work that
    # out") or returns a number-too-big response — both are graceful.
    assert "couldn't work that out" in resp.text.lower() or "too large" in resp.text.lower()


@pytest.mark.asyncio
async def test_arithmetic_handler_full_response_formatting() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_arithmetic("47 times 89", ctx)
    assert resp.text == "That's 4,183."
    assert resp.matched_handler == "calculator"


# ─── Percentages ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_percent_of_dollar_amount() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_percent_of(20.0, 50.0, money=True, ctx=ctx)
    assert "$10.00" in resp.text


@pytest.mark.asyncio
async def test_percent_inverse() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_percent_inverse(80.0, 20.0, ctx=ctx)
    assert "25" in resp.text and "%" in resp.text


@pytest.mark.asyncio
async def test_percent_tax_adds_delta_to_text() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_percent_adjust(8.0, 45.0, is_tax=True, ctx=ctx)
    assert "$48.60" in resp.text
    assert "$3.60" in resp.text
    assert "tax" in resp.text


@pytest.mark.asyncio
async def test_percent_off_subtracts_delta_in_text() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_percent_adjust(25.0, 40.0, is_tax=False, ctx=ctx)
    assert "$30.00" in resp.text
    assert "$10.00" in resp.text
    assert "off" in resp.text


# ─── Unit conversions ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unit_oz_to_g_round_trip() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_unit_convert(1.0, "oz", "g", ctx=ctx)
    assert "28.35" in resp.text  # 1 oz = 28.349...g, rounded to 2 dp


@pytest.mark.asyncio
async def test_unit_in_to_cm() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_unit_convert(1.0, "in", "cm", ctx=ctx)
    assert "2.54" in resp.text


@pytest.mark.asyncio
async def test_unit_fahrenheit_celsius_affine() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_unit_convert(32.0, "f", "c", ctx=ctx)
    assert "0.00" in resp.text
    resp = handler._respond_unit_convert(100.0, "c", "f", ctx=ctx)
    assert "212.00" in resp.text


@pytest.mark.asyncio
async def test_unit_cup_to_ml() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_unit_convert(1.0, "cup", "ml", ctx=ctx)
    assert "236.59" in resp.text


@pytest.mark.asyncio
async def test_unit_hr_to_min() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_unit_convert(1.0, "hr", "min", ctx=ctx)
    assert "60" in resp.text


@pytest.mark.asyncio
async def test_unit_unknown_responds_gracefully() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_unit_convert(1.0, "smoots", "g", ctx=ctx)
    assert "don't know" in resp.text.lower()


# ─── Date math ───────────────────────────────────────────────────────


def _patch_today(d: date):
    """Patch CalculatorHandler._today + _now so the date-math tests are
    deterministic regardless of the wall clock."""
    h = CalculatorHandler
    return patch.multiple(
        h,
        _today=lambda self, ctx: d,
        _now=lambda self, ctx: datetime(d.year, d.month, d.day, 12, 0, 0),
    )


@pytest.mark.asyncio
async def test_date_90_days_from_today() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    with _patch_today(date(2026, 5, 11)):
        resp = handler._respond_days_offset(90, ctx)
    # 2026-05-11 + 90 days = 2026-08-09
    assert "August" in resp.text and "2026" in resp.text


@pytest.mark.asyncio
async def test_date_days_until_christmas_fixed_date() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    with _patch_today(date(2026, 12, 1)):
        resp = handler._respond_holiday("christmas", ctx)
    assert "24" in resp.text  # 24 days until Dec 25
    assert "Christmas" in resp.text


@pytest.mark.asyncio
async def test_date_days_until_unknown_holiday_responds_gracefully() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    with _patch_today(date(2026, 5, 11)):
        resp = handler._respond_holiday("dragonday", ctx)
    assert "don't know" in resp.text.lower()


@pytest.mark.asyncio
async def test_date_next_friday() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    # 2026-05-11 is a Monday; next Friday is 2026-05-15.
    with _patch_today(date(2026, 5, 11)):
        resp = handler._respond_next_weekday("friday", ctx)
    assert "Friday" in resp.text
    assert "May" in resp.text and "15th" in resp.text


@pytest.mark.asyncio
async def test_date_in_4_hours_includes_wall_clock_time() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    with _patch_today(date(2026, 5, 11)):
        resp = handler._respond_hours_offset(4, ctx)
    # 2026-05-11 12:00 + 4 hours = 4:00 PM same day.
    assert "4:00 PM" in resp.text
    assert "May" in resp.text


# ─── Tip / split ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tip_only() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_tip(50.0, 20.0, ctx)
    assert "$10.00" in resp.text
    assert "$60.00" in resp.text


@pytest.mark.asyncio
async def test_split_only() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_split(200.0, 4, ctx)
    assert "$50.00" in resp.text


@pytest.mark.asyncio
async def test_split_with_tip() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_split_with_tip(200.0, 4, 20.0, ctx)
    # total = 240, each = 60.
    assert "$240.00" in resp.text
    assert "$60.00" in resp.text


@pytest.mark.asyncio
async def test_split_zero_people_rejected() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_split(200.0, 0, ctx)
    assert "at least one person" in resp.text.lower()


@pytest.mark.asyncio
async def test_split_with_tip_zero_people_rejected() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = handler._respond_split_with_tip(200.0, 0, 20.0, ctx)
    assert "at least one person" in resp.text.lower()


# ─── Tool-call entry point ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_from_tool_arithmetic() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = await handler.execute_from_tool(
        {"action": "arithmetic", "expression": "47 times 89"},
        ctx,
        session=None,
    )
    assert "4,183" in resp.text


@pytest.mark.asyncio
async def test_execute_from_tool_percentage_tax() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = await handler.execute_from_tool(
        {"action": "percentage", "kind": "tax", "percent": 8, "value": 45},
        ctx,
        session=None,
    )
    assert "$48.60" in resp.text


@pytest.mark.asyncio
async def test_execute_from_tool_unit_convert() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = await handler.execute_from_tool(
        {"action": "unit_convert", "value": 1, "src_unit": "oz", "dst_unit": "g"},
        ctx,
        session=None,
    )
    assert "28.35" in resp.text


@pytest.mark.asyncio
async def test_execute_from_tool_tip_split() -> None:
    handler = CalculatorHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = await handler.execute_from_tool(
        {
            "action": "tip_split",
            "amount": 50,
            "tip_percent": 20,
        },
        ctx,
        session=None,
    )
    assert "$10.00" in resp.text


# ─── Registry / local-first contract ─────────────────────────────────


def test_handler_is_local_only() -> None:
    """The whole point of this handler is local determinism — it must
    declare requires_network='no' so the router never gates it on
    connectivity."""
    assert CalculatorHandler().requires_network == "no"


def test_handler_registered_after_reminder_before_music() -> None:
    """Anti-poach precondition: the ordering comment in
    handlers/__init__.py promises calculator slots between
    ReminderHandler and MusicHandler. Lock that in so a future re-sort
    breaks loudly instead of silently re-introducing poach risk."""
    from domovoi.handlers import HANDLERS

    names = [h.name for h in HANDLERS]
    assert "calculator" in names
    assert names.index("reminder") < names.index("calculator")
    assert names.index("calculator") < names.index("music")


# ─── Router-level integration ────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_router_routes_arithmetic_to_calculator(db_session) -> None:
    """End-to-end through the router: "what's 47 times 89" should
    fast-path to calculator, not LLM tool routing or QA."""
    from domovoi.router import route

    intent = Intent(transcript="what's 47 times 89", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()
    assert response.matched_handler == "calculator"
    assert response.matched_path == "fast"
    assert "4,183" in response.text


@requires_db
@pytest.mark.asyncio
async def test_router_play_prefixed_still_routes_to_music_not_calculator(
    db_session,
) -> None:
    """Anti-poach integration: "play 47 times by some artist" must
    reach MusicHandler, not the arithmetic fast path. Calculator runs
    earlier in the chain, so if its bare-arithmetic regex were too
    greedy it would steal this one."""
    from domovoi.router import route

    intent = Intent(
        transcript="play 47 times by some artist", room_id="kitchen"
    )
    ctx = Context(room_id="kitchen", online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()
    assert response.matched_handler == "music"
    assert response.matched_path == "fast"


@requires_db
@pytest.mark.asyncio
async def test_router_routes_unit_conversion(db_session) -> None:
    from domovoi.router import route

    intent = Intent(
        transcript="how many oz in 100 grams", room_id="kitchen"
    )
    ctx = Context(room_id="kitchen", online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()
    assert response.matched_handler == "calculator"
    assert "3.53" in response.text  # 100 g = 3.527 oz, rounded to 2 dp
