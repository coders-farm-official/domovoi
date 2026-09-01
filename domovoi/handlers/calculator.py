"""CalculatorHandler — local arithmetic, percentages, unit conversion,
date/time math, tip/split. All deterministic, all offline.

The Ollama tool model is fast but wrong on multi-step arithmetic
surprisingly often ("47 times 89" → "4083" instead of 4183). The fast
paths here intercept math questions BEFORE they reach the LLM and
guarantee correct answers from the math library. `requires_network="no"`
because every computation is local.

Security: arithmetic uses `simpleeval`, never `eval()` or `exec()` on
user input. Allowed grammar is +, -, *, /, **, parens, and a single
`sqrt` function. Operand magnitude is capped on top of simpleeval's
own MAX_POWER so a misheard "googolplex" can't hang the worker.

Deferred — future follow-up, NOT shipped here:
  * Stats over a list ("mean of 10, 20, 30")
  * Geometry formulas ("area of a circle radius 5")
  * Ratios / proportions
  * Number-base conversions ("47 in binary")
"""

from __future__ import annotations

import ast
import logging
import math
import re
from datetime import date, datetime, timedelta

from simpleeval import (
    InvalidExpression,
    NumberTooHigh,
    SimpleEval,
)
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.clients.holidays import next_occurrence
from domovoi.clients.units import UNITS, convert as unit_convert, lookup as unit_lookup
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


# ─── Number / money formatting ───────────────────────────────────────

_MAX_OPERAND = 1e15


def _format_number(value: float | int) -> str:
    """Voice-friendly number rendering. Integers get thousands
    separators. Floats round to 4 significant figures, trailing zeros
    stripped, thousands separators applied."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int) or (isinstance(value, float) and value == int(value)):
        return f"{int(value):,}"
    if value == 0:
        return "0"
    sig = 4
    magnitude = math.floor(math.log10(abs(value)))
    decimals = max(0, sig - 1 - magnitude)
    rounded = round(value, decimals)
    formatted = f"{rounded:,.{decimals}f}"
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def _format_money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


# ─── Arithmetic ──────────────────────────────────────────────────────

_MATH_WORDS = (
    r"(?:plus|minus|times|divided by|multiplied by|squared|cubed"
    r"|to the power of|square root of)"
)

# "what's 47 times 89" / "calculate 5 ** 2" / "compute sqrt(144)".
# Requires a math token in the body so "what's the weather" can't poach.
_ARITH_PREFIX_RE = re.compile(
    rf"^(?:what(?:'?s| is)|calculate|compute|how much is)\s+"
    rf"(?P<expr>(?=.*(?:{_MATH_WORDS}|sqrt|\*|\/|\+)).+)$"
)

# Bare form: starts with a digit, contains a math word/operator.
# Anchored on a leading digit so "play 47 times by ..." can't poach
# (starts with "play", not a digit) — the MusicHandler later in the
# chain still wins for play-prefixed transcripts.
_ARITH_BARE_RE = re.compile(
    rf"^(?P<expr>\d[\d\.\s\(\)\+\-\*\/]*?\s*{_MATH_WORDS}(?:\b.*)?)$"
)

# "square root of 144" — also covered by the prefix path via the
# lookahead, but a bare "square root of N" is common enough to deserve
# its own anchor.
_SQRT_RE = re.compile(r"^square root of (?P<n>\d+(?:\.\d+)?)$")


def _normalize_math(expr: str) -> str:
    """Convert spoken math vocabulary into operators simpleeval
    understands. Order matters — replace multi-word phrases first."""
    s = expr.strip()
    # Drop a leading article. The spoken-prefix path hands us whatever
    # followed "what is" / "calculate", so "what is the square root of 144"
    # arrives here as "the square root of 144". _safe_eval strips ALL
    # whitespace before parsing, so a surviving article fuses onto the next
    # token — "the sqrt(144)" becomes "thesqrt(144)", "the 12 times 9"
    # becomes "the12*9" — and both are rejected as unknown names. The user
    # just hears "I couldn't work that out" for a perfectly ordinary phrasing.
    s = re.sub(r"^(?:the|a)\s+", "", s, flags=re.I)
    # Multi-word first.
    s = re.sub(r"\bsquare root of\b", "sqrt ", s)
    s = re.sub(r"\bto the power of\b", " ** ", s)
    s = re.sub(r"\bdivided by\b", " / ", s)
    s = re.sub(r"\bmultiplied by\b", " * ", s)
    # Single-word operators.
    s = re.sub(r"\btimes\b", " * ", s)
    s = re.sub(r"\bplus\b", " + ", s)
    s = re.sub(r"\bminus\b", " - ", s)
    s = re.sub(r"\bsquared\b", " ** 2", s)
    s = re.sub(r"\bcubed\b", " ** 3", s)
    # Wrap bare sqrt operand in parens: "sqrt 144" → "sqrt(144)".
    s = re.sub(r"sqrt\s*(\d+(?:\.\d+)?)", r"sqrt(\1)", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _check_operand_magnitudes(expr: str) -> None:
    """Reject any numeric literal whose magnitude exceeds _MAX_OPERAND.
    Cheaper than letting simpleeval start crunching on 10**googol."""
    for token in re.findall(r"\d+(?:\.\d+)?", expr):
        if abs(float(token)) > _MAX_OPERAND:
            raise ValueError("operand too large")


# simpleeval's MAX_POWER guard only bounds the OPERANDS of `**` (each must be
# < 4,000,000), not the RESULT — so e.g. "3999999 ** 3999999" passes both that
# guard and _check_operand_magnitudes, then pins the single event loop for
# ~45 s computing an 88-million-bit integer. Bound the result statically first.
_MAX_POWER_RESULT_DIGITS = 1000


def _flatten_const(node: ast.AST) -> float | None:
    """Numeric value of a plain constant node (a number, or unary ±number),
    else None. Deliberately does NOT recurse into operations — we never want
    to *compute* a nested power here just to inspect it."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _flatten_const(node.operand)
        return -v if v is not None else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _flatten_const(node.operand)
    return None


def _check_power_safety(expr: str) -> None:
    """Reject a `**` whose result would be astronomically large BEFORE it is
    evaluated. simpleeval bounds operands, not results, and an unbounded
    result blocks the single event loop for tens of seconds (a DoS)."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return  # _safe_eval will reject it with a proper message
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow)):
            continue
        exp = _flatten_const(node.right)
        if exp is None:
            # Non-constant exponent (e.g. a nested power like 9**9**9): refuse
            # rather than risk evaluating a huge intermediate. Voice math only
            # ever yields literal exponents ("squared", "to the power of N").
            raise ValueError("power exponent too complex to bound safely")
        base = _flatten_const(node.left)
        if base is None:
            # Compound base (e.g. (2+3)**n): bound by the exponent alone.
            if abs(exp) > _MAX_POWER_RESULT_DIGITS:
                raise ValueError("power result too large")
            continue
        if abs(base) <= 1 or exp <= 0:
            continue  # result stays small (or shrinks)
        if exp * math.log10(abs(base)) > _MAX_POWER_RESULT_DIGITS:
            raise ValueError("power result too large")


def _safe_eval(expr: str) -> float | int:
    """Evaluate ``expr`` using simpleeval with only +, -, *, /, **,
    parens, and sqrt() exposed. Never uses builtin eval/exec."""
    s = SimpleEval()
    s.functions = {"sqrt": math.sqrt}
    s.names = {}
    # Strip whitespace to make pattern matching upstream cleaner.
    normalized = expr.replace(" ", "")
    # Restrict the AST: only allow numeric literals, the operators we
    # whitelist, and the sqrt name. This is a defense-in-depth layer
    # over simpleeval's own AST filtering.
    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Constant,
        # NOTE: no ast.Num here. It was a pre-3.8 alias for Constant, and
        # referencing it is an AttributeError on modern CPython (removed by
        # 3.14) — which broke every arithmetic turn, not just the tests.
        # ast.Constant already covers numeric literals.
        ast.Load,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Call,
        ast.Name,
    )
    try:
        tree = ast.parse(normalized, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"could not parse: {expr}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            raise ValueError(f"disallowed expression: {expr}")
        if isinstance(node, ast.Name) and node.id != "sqrt":
            raise ValueError(f"unknown name: {node.id}")
        if isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Name) and node.func.id == "sqrt"):
                raise ValueError("only sqrt() is allowed")
    return s.eval(expr)


# ─── Percentages ─────────────────────────────────────────────────────

# "47% of 89" / "what's 47% of 89" / "47 percent of 89"
_PERCENT_OF_RE = re.compile(
    r"^(?:what(?:'?s| is)\s+)?"
    r"(?P<pct>\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+"
    r"\$?(?P<value>\d+(?:\.\d+)?)$"
)

# "what percent of 89 is 47" / "what percentage of 89 is 47"
_PERCENT_INVERSE_RE = re.compile(
    r"^what\s+(?:percent|percentage)\s+of\s+"
    r"\$?(?P<total>\d+(?:\.\d+)?)\s+is\s+"
    r"\$?(?P<part>\d+(?:\.\d+)?)$"
)

# "8% tax on $47" / "20% off $89"
_PERCENT_ADJUST_RE = re.compile(
    r"^(?P<pct>\d+(?:\.\d+)?)\s*(?:%|percent)\s+"
    r"(?P<direction>tax\s+on|off)\s+"
    r"\$?(?P<value>\d+(?:\.\d+)?)$"
)


# ─── Unit conversion ─────────────────────────────────────────────────

# Build a regex alternation of every known unit name (plus their plural
# forms). Sorted long-to-short so "milliliter" matches before "m".
def _build_unit_alternation() -> str:
    names: set[str] = set()
    for name in UNITS.keys():
        names.add(name)
        # Add naive plural forms; the canonicalizer handles them.
        if name.isalpha() and len(name) > 1:
            names.add(name + "s")
    # Irregular plurals: feet, inches, etc. are already separate keys.
    names |= {
        "feet", "inches", "grams", "kilograms", "pounds", "ounces",
        "miles", "millimeters", "centimeters", "meters", "kilometers",
        "yards", "teaspoons", "tablespoons", "cups", "pints", "quarts",
        "gallons", "milliliters", "liters", "litres", "metres",
        "minutes", "seconds", "hours", "days", "weeks",
    }
    return "|".join(sorted(names, key=len, reverse=True))


_UNIT_ALT = _build_unit_alternation()

# "how many oz in 100 grams" / "how many ounces are in 100 grams"
_UNIT_HOW_MANY_RE = re.compile(
    rf"^how many\s+(?P<dst>{_UNIT_ALT})\s+(?:are\s+)?in\s+"
    rf"(?P<value>\d+(?:\.\d+)?)\s+(?P<src>{_UNIT_ALT})$"
)

# "convert 100 grams to oz" / "convert 100 grams into oz"
_UNIT_CONVERT_RE = re.compile(
    rf"^convert\s+(?P<value>\d+(?:\.\d+)?)\s+"
    rf"(?P<src>{_UNIT_ALT})\s+(?:to|into)\s+(?P<dst>{_UNIT_ALT})$"
)

# Short form "100 grams in oz".
_UNIT_SHORT_RE = re.compile(
    rf"^(?P<value>\d+(?:\.\d+)?)\s+"
    rf"(?P<src>{_UNIT_ALT})\s+in\s+(?P<dst>{_UNIT_ALT})$"
)


# ─── Date / time math ────────────────────────────────────────────────

_DAYS_FUTURE_RE = re.compile(
    r"^(?P<n>\d+)\s+days?\s+from\s+(?:today|now)$"
)
_IN_N_DAYS_RE = re.compile(r"^in\s+(?P<n>\d+)\s+days?$")
_DAYS_PAST_RE = re.compile(r"^(?P<n>\d+)\s+days?\s+ago$")

_HOURS_FUTURE_RE = re.compile(r"^(?P<n>\d+)\s+hours?\s+from\s+now$")
_IN_N_HOURS_RE = re.compile(r"^in\s+(?P<n>\d+)\s+hours?$")
_HOURS_PAST_RE = re.compile(r"^(?P<n>\d+)\s+hours?\s+ago$")

# "days until christmas" / "how many days until christmas" / "days till
# new year's" / "days to halloween". Holiday name is a bare label that
# we look up via clients.holidays.next_occurrence; an unknown name gets
# a graceful "I don't know that holiday" instead of a wrong answer.
_DAYS_UNTIL_RE = re.compile(
    r"^(?:how many\s+)?days?\s+(?:until|till|to|'til)\s+(?P<holiday>[a-z][a-z'\s]*?)$"
)

# "next monday" / "next friday". Always returns the next future
# occurrence — today never counts (so "next monday" said on a Monday
# rolls to the following Monday).
_NEXT_WEEKDAY_RE = re.compile(
    r"^next\s+(?P<weekday>monday|tuesday|wednesday|thursday|friday|saturday|sunday)$"
)


_WEEKDAY_TO_INT = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _ordinal(n: int) -> str:
    """Spoken-friendly ordinal: 1 → '1st', 22 → '22nd', etc."""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _format_date_spoken(d: date) -> str:
    """'October 5th, 2026'-style date that reads cleanly out loud."""
    return f"{_MONTH_NAMES[d.month - 1]} {_ordinal(d.day)}, {d.year}"


def _format_datetime_spoken(dt: datetime) -> str:
    """'October 5th, 2026 at 3:42 PM'."""
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{_format_date_spoken(dt.date())} at {hour}:{dt.minute:02d} {ampm}"


# ─── Tip / split ─────────────────────────────────────────────────────

# "20% tip on $50" / "20 percent tip on $50"
_TIP_RE = re.compile(
    r"^(?P<pct>\d+(?:\.\d+)?)\s*(?:%|percent)\s+tip\s+on\s+"
    r"\$?(?P<amount>\d+(?:\.\d+)?)$"
)

# "split $200 4 ways" / "split 200 4 ways"
_SPLIT_RE = re.compile(
    r"^split\s+\$?(?P<amount>\d+(?:\.\d+)?)\s+(?P<n>\d+)\s+ways?$"
)

# "split $200 4 ways with 20% tip"
_SPLIT_TIP_RE = re.compile(
    r"^split\s+\$?(?P<amount>\d+(?:\.\d+)?)\s+(?P<n>\d+)\s+ways?\s+"
    r"with\s+(?P<pct>\d+(?:\.\d+)?)\s*(?:%|percent)\s+tip$"
)


# ─── Handler ─────────────────────────────────────────────────────────


class CalculatorHandler(Handler):
    name = "calculator"
    # band rationale: after reminder, well before media: the bare arithmetic regex is
    #   digit-anchored ("5 plus 3"), so music's "^play (.+)$" could never
    #   poach it — but slotting calculator early keeps anything future-added
    #   in the media neighborhood from grabbing math queries first.
    priority_band = 150
    display = HandlerDisplay(label="Calculator", tone="info")
    requires_network = "no"

    tool_schema = {
        "name": "calculator",
        "description": (
            "Deterministic calculator: arithmetic, percentages, unit "
            "conversion, date arithmetic, tip/split. Use this instead "
            "of computing math yourself — your arithmetic is unreliable "
            "above two-digit operands."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "arithmetic",
                        "percentage",
                        "unit_convert",
                        "date_math",
                        "tip_split",
                    ],
                },
                "expression": {
                    "type": "string",
                    "description": "Arithmetic expression for action=arithmetic.",
                },
                "percent": {"type": "number"},
                "value": {"type": "number"},
                "part": {"type": "number"},
                "kind": {
                    "type": "string",
                    "description": (
                        "For percentage: 'of'|'inverse'|'tax'|'off'. "
                        "For date_math: 'days_forward'|'days_back'|"
                        "'hours_forward'|'hours_back'|'holiday'|"
                        "'next_weekday'."
                    ),
                },
                "src_unit": {"type": "string"},
                "dst_unit": {"type": "string"},
                "amount": {"type": "number"},
                "people": {"type": "integer"},
                "tip_percent": {"type": "number"},
                "n": {"type": "integer"},
                "label": {
                    "type": "string",
                    "description": "Holiday or weekday name for date_math.",
                },
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            # Order within the handler: tip/split before percentages
            # (so "20% tip on $50" can't be eaten by the percent-tax
            # adjust path); unit conversions before arithmetic (so
            # "convert 5 ft to cm" doesn't get treated as an algebra
            # expression by accident); date math before arithmetic
            # (so "in 4 hours" doesn't tangle with the bare arithmetic
            # form). Arithmetic prefix path before bare so a "what's"
            # prefix wins when both could fire.
            FastPath(_SPLIT_TIP_RE, CalculatorHandler._tip_split_from_match),
            FastPath(_SPLIT_RE, CalculatorHandler._tip_split_from_match),
            FastPath(_TIP_RE, CalculatorHandler._tip_split_from_match),
            FastPath(_PERCENT_ADJUST_RE, CalculatorHandler._percent_from_match),
            FastPath(_PERCENT_INVERSE_RE, CalculatorHandler._percent_from_match),
            FastPath(_PERCENT_OF_RE, CalculatorHandler._percent_from_match),
            FastPath(_UNIT_HOW_MANY_RE, CalculatorHandler._unit_from_match),
            FastPath(_UNIT_CONVERT_RE, CalculatorHandler._unit_from_match),
            FastPath(_UNIT_SHORT_RE, CalculatorHandler._unit_from_match),
            FastPath(_DAYS_FUTURE_RE, CalculatorHandler._date_days_forward),
            FastPath(_IN_N_DAYS_RE, CalculatorHandler._date_days_forward),
            FastPath(_DAYS_PAST_RE, CalculatorHandler._date_days_back),
            FastPath(_HOURS_FUTURE_RE, CalculatorHandler._date_hours_forward),
            FastPath(_IN_N_HOURS_RE, CalculatorHandler._date_hours_forward),
            FastPath(_HOURS_PAST_RE, CalculatorHandler._date_hours_back),
            FastPath(_DAYS_UNTIL_RE, CalculatorHandler._date_days_until),
            FastPath(_NEXT_WEEKDAY_RE, CalculatorHandler._date_next_weekday),
            FastPath(_SQRT_RE, CalculatorHandler._arith_sqrt),
            FastPath(_ARITH_PREFIX_RE, CalculatorHandler._arith_from_match),
            FastPath(_ARITH_BARE_RE, CalculatorHandler._arith_from_match),
        ]

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        # Reached only when the LLM tool router returns calculator with
        # no usable args — handler doesn't have a meaningful free-form
        # entry point.
        return Response(
            text="What would you like me to calculate?",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        action = args.get("action")
        if action == "arithmetic":
            expr = (args.get("expression") or "").strip()
            return self._respond_arithmetic(expr, ctx)
        if action == "percentage":
            kind = args.get("kind")
            if kind == "of":
                return self._respond_percent_of(
                    float(args.get("percent") or 0),
                    float(args.get("value") or 0),
                    money=False,
                    ctx=ctx,
                )
            if kind == "inverse":
                return self._respond_percent_inverse(
                    float(args.get("value") or 0),
                    float(args.get("part") or 0),
                    ctx=ctx,
                )
            if kind in ("tax", "off"):
                return self._respond_percent_adjust(
                    float(args.get("percent") or 0),
                    float(args.get("value") or 0),
                    is_tax=(kind == "tax"),
                    ctx=ctx,
                )
        if action == "unit_convert":
            return self._respond_unit_convert(
                float(args.get("value") or 0),
                str(args.get("src_unit") or ""),
                str(args.get("dst_unit") or ""),
                ctx=ctx,
            )
        if action == "date_math":
            kind = args.get("kind")
            n = int(args.get("n") or 0)
            label = (args.get("label") or "").strip()
            if kind == "days_forward":
                return self._respond_days_offset(n, ctx)
            if kind == "days_back":
                return self._respond_days_offset(-n, ctx)
            if kind == "hours_forward":
                return self._respond_hours_offset(n, ctx)
            if kind == "hours_back":
                return self._respond_hours_offset(-n, ctx)
            if kind == "holiday":
                return self._respond_holiday(label, ctx)
            if kind == "next_weekday":
                return self._respond_next_weekday(label, ctx)
        if action == "tip_split":
            amount = float(args.get("amount") or 0)
            tip_pct = float(args.get("tip_percent") or 0)
            people = int(args.get("people") or 0)
            if people and tip_pct:
                return self._respond_split_with_tip(amount, people, tip_pct, ctx)
            if people:
                return self._respond_split(amount, people, ctx)
            if tip_pct:
                return self._respond_tip(amount, tip_pct, ctx)
        return Response(
            text="I'm not sure what to calculate.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    # ─── Fast-path adapters ──────────────────────────────────────────
    async def _arith_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._respond_arithmetic(m.group("expr"), ctx)

    async def _arith_sqrt(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._respond_arithmetic(f"square root of {m.group('n')}", ctx)

    async def _percent_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        gd = m.groupdict()
        if "direction" in gd and gd.get("direction"):
            return self._respond_percent_adjust(
                float(gd["pct"]),
                float(gd["value"]),
                is_tax="tax" in gd["direction"],
                ctx=ctx,
            )
        if "part" in gd and gd.get("part") is not None:
            return self._respond_percent_inverse(
                float(gd["total"]), float(gd["part"]), ctx=ctx,
            )
        # "X% of Y" — detect a leading $ to decide on money formatting.
        money = "$" in (m.string or "")
        return self._respond_percent_of(
            float(gd["pct"]), float(gd["value"]), money=money, ctx=ctx,
        )

    async def _unit_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        gd = m.groupdict()
        return self._respond_unit_convert(
            float(gd["value"]), gd["src"], gd["dst"], ctx=ctx,
        )

    async def _date_days_forward(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._respond_days_offset(int(m.group("n")), ctx)

    async def _date_days_back(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._respond_days_offset(-int(m.group("n")), ctx)

    async def _date_hours_forward(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._respond_hours_offset(int(m.group("n")), ctx)

    async def _date_hours_back(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._respond_hours_offset(-int(m.group("n")), ctx)

    async def _date_days_until(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._respond_holiday(m.group("holiday"), ctx)

    async def _date_next_weekday(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._respond_next_weekday(m.group("weekday"), ctx)

    async def _tip_split_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        gd = m.groupdict()
        amount = float(gd["amount"])
        if "n" in gd and "pct" in gd:
            return self._respond_split_with_tip(
                amount, int(gd["n"]), float(gd["pct"]), ctx,
            )
        if "n" in gd:
            return self._respond_split(amount, int(gd["n"]), ctx)
        return self._respond_tip(amount, float(gd["pct"]), ctx)

    # ─── Core compute + format ───────────────────────────────────────
    def _respond_arithmetic(self, expr: str, ctx: Context) -> Response:
        try:
            normalized = _normalize_math(expr)
            _check_operand_magnitudes(normalized)
            _check_power_safety(normalized)
            result = _safe_eval(normalized)
        except ZeroDivisionError:
            return Response(
                text="I can't divide by zero.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        except (ValueError, SyntaxError, InvalidExpression, NumberTooHigh) as exc:
            log.info("Calculator arithmetic failed: %s (expr=%r)", exc, expr)
            return Response(
                text="I couldn't work that out.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        # Defensive bounds check on the result too — a chain of moderate
        # operations could still overflow.
        if isinstance(result, (int, float)) and abs(result) > _MAX_OPERAND * 100:
            return Response(
                text="That answer is too large to make sense out loud.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return Response(
            text=f"That's {_format_number(result)}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"expression": expr, "normalized": normalized, "result": result},
        )

    def _respond_percent_of(
        self, pct: float, value: float, *, money: bool, ctx: Context,
    ) -> Response:
        result = pct / 100.0 * value
        text = (
            f"That's {_format_money(result)}." if money
            else f"That's {round(result, 1)}."
        )
        return Response(
            text=text,
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"percent": pct, "value": value, "result": result},
        )

    def _respond_percent_inverse(
        self, total: float, part: float, *, ctx: Context,
    ) -> Response:
        if total == 0:
            return Response(
                text="I can't divide by zero.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        result = part / total * 100.0
        return Response(
            text=f"That's {round(result, 1)}%.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"total": total, "part": part, "result_percent": result},
        )

    def _respond_percent_adjust(
        self, pct: float, value: float, *, is_tax: bool, ctx: Context,
    ) -> Response:
        delta = value * pct / 100.0
        final = value + delta if is_tax else value - delta
        word = "in tax" if is_tax else "off"
        text = (
            f"That's {_format_money(final)} — {_format_money(abs(delta))} {word}."
        )
        return Response(
            text=text,
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={
                "percent": pct, "value": value,
                "delta": delta, "final": final,
                "is_tax": is_tax,
            },
        )

    def _respond_unit_convert(
        self, value: float, src: str, dst: str, *, ctx: Context,
    ) -> Response:
        if unit_lookup(src) is None or unit_lookup(dst) is None:
            unknown = src if unit_lookup(src) is None else dst
            return Response(
                text=f"I don't know the unit '{unknown}'.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        try:
            result = unit_convert(value, src, dst)
        except ValueError as exc:
            return Response(
                text=f"I can't do that conversion — {exc}.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        rounded = round(result, 2)
        return Response(
            text=f"That's {rounded:,.2f} {dst}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"value": value, "src": src, "dst": dst, "result": result},
        )

    def _respond_days_offset(self, n: int, ctx: Context) -> Response:
        today = self._today(ctx)
        target = today + timedelta(days=n)
        direction = "from now" if n >= 0 else "ago"
        return Response(
            text=(
                f"{abs(n)} day{'s' if abs(n) != 1 else ''} {direction} is "
                f"{_format_date_spoken(target)}."
            ),
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"target": target.isoformat(), "n_days": n},
        )

    def _respond_hours_offset(self, n: int, ctx: Context) -> Response:
        now = self._now(ctx)
        target = now + timedelta(hours=n)
        direction = "from now" if n >= 0 else "ago"
        return Response(
            text=(
                f"{abs(n)} hour{'s' if abs(n) != 1 else ''} {direction} "
                f"is {_format_datetime_spoken(target)}."
            ),
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"target": target.isoformat(), "n_hours": n},
        )

    def _respond_holiday(self, label: str, ctx: Context) -> Response:
        today = self._today(ctx)
        # Normalize spoken aliases: "xmas" → "christmas", "new years" /
        # "new year" → "new year's", "july fourth" → "july 4th".
        norm = label.strip().lower()
        norm = re.sub(r"\bxmas\b", "christmas", norm)
        norm = re.sub(r"^new years?$", "new year's", norm)
        norm = re.sub(r"^july fourth$", "july 4th", norm)
        norm = re.sub(r"^the fourth(?: of july)?$", "fourth of july", norm)
        target = next_occurrence(norm, today)
        if target is None:
            return Response(
                text=f"I don't know the date of {label.strip()}.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        delta = (target - today).days
        if delta == 0:
            return Response(
                text=f"{label.strip().title()} is today.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return Response(
            text=(
                f"{delta} day{'s' if delta != 1 else ''} until "
                f"{label.strip().title()} — that's "
                f"{_format_date_spoken(target)}."
            ),
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"target": target.isoformat(), "days": delta, "holiday": norm},
        )

    def _respond_next_weekday(self, weekday: str, ctx: Context) -> Response:
        today = self._today(ctx)
        target_idx = _WEEKDAY_TO_INT[weekday.lower()]
        # Always at least 1 day forward — "next monday" on a Monday
        # rolls to the FOLLOWING Monday, never today.
        delta = (target_idx - today.weekday()) % 7
        if delta == 0:
            delta = 7
        target = today + timedelta(days=delta)
        return Response(
            text=(
                f"Next {weekday.title()} is "
                f"{_MONTH_NAMES[target.month - 1]} {_ordinal(target.day)}."
            ),
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"target": target.isoformat(), "weekday": weekday},
        )

    def _respond_tip(self, amount: float, pct: float, ctx: Context) -> Response:
        tip = amount * pct / 100.0
        total = amount + tip
        return Response(
            text=(
                f"{_format_money(tip)} tip on {_format_money(amount)}, "
                f"total {_format_money(total)}."
            ),
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"amount": amount, "percent": pct, "tip": tip, "total": total},
        )

    def _respond_split(self, amount: float, n: int, ctx: Context) -> Response:
        if n <= 0:
            return Response(
                text="I can't split that — I need at least one person.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        each = amount / n
        return Response(
            text=f"Each person owes {_format_money(each)}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"amount": amount, "people": n, "each": each},
        )

    def _respond_split_with_tip(
        self, amount: float, n: int, pct: float, ctx: Context,
    ) -> Response:
        if n <= 0:
            return Response(
                text="I can't split that — I need at least one person.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        total = amount + amount * pct / 100.0
        each = total / n
        return Response(
            text=(
                f"With a {pct:g}% tip, that's {_format_money(total)} total — "
                f"{_format_money(each)} per person."
            ),
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={
                "amount": amount, "people": n,
                "percent": pct, "total": total, "each": each,
            },
        )

    # ─── Time / date sources ─────────────────────────────────────────
    # v1 uses Domovoi's local wall clock for both "now" and "today".
    # Per-person timezones from a future preferences table would slot
    # in here without touching any caller — the spec describes that as
    # a v2 hook.
    def _today(self, ctx: Context) -> date:
        return datetime.now().date()

    def _now(self, ctx: Context) -> datetime:
        return datetime.now()
