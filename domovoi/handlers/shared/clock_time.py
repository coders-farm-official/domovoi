"""Spoken clock times and days, and the moment they next come.

"remind me at 6 pm", "wake me up at 7", "remind me tomorrow morning",
"what's on my calendar on Friday": the words, as Whisper writes them, and
the household wall-clock time they mean (domovoi/household_time.py).

Like ``number_words``, this module exports regex FRAGMENTS with no
capturing groups, which handlers embed in their own anchored fast paths,
and parsers for the text a fragment matched:

* :data:`TIME_PATTERN` - a clock time: "6 pm", "6:30 p.m.", "18:00",
  "seven", "six thirty", "half past six", "quarter to seven", "noon",
  "midnight", "7 o'clock", with an optional "in the morning" / "at night".
* :data:`MARKED_TIME_PATTERN` - a clock time that can't be a bare number:
  it has am/pm, minutes, "o'clock", a part of the day, or is noon or
  midnight. "set a reminder for 7" might be minutes; "for 7 pm" is a time.
* :data:`DAY_PATTERN` - "today", "tonight", "tomorrow (morning)", "the day
  after tomorrow", "this evening", "in the morning", "(on) (next) Friday
  (night)".
* :data:`WHEN_PATTERN` - what follows "remind me" or "wake me up": "at
  TIME (DAY)" or "DAY (at TIME)".
* :func:`parse_when` - the matched text as a :class:`SpokenWhen`.
* :func:`resolve` - a :class:`SpokenWhen` and the wall clock now to the
  wall-clock datetime it means, or a :class:`WhenProblem`.
* :func:`spoken_clock` / :func:`spoken_when` - how a reply says it: "6 PM",
  "tomorrow at 7 AM", "on Friday at 5:30 PM", "at midnight".

The rules (owner, 2026-10-17):

* A time with no AM/PM is the next time that comes, AM or PM, unless the
  person says which: "at 7" at 23:20 is 7 AM tomorrow, at 14:00 it is 7 PM
  today; "at 6 pm" at 23:20 is tomorrow's. "wake me up at 5" is no
  exception: at 15:00 it is 5 PM (a nap), at 23:00 5 AM. "am", "pm", "in
  the morning", "tonight" and the like always win.
* A day with no time takes the part of day's usual hour (morning 9 AM,
  afternoon 2 PM, evening 6 PM, tonight 8 PM) or 9 AM; the reply always
  says the clock time, so a wrong guess is heard.
* "tomorrow at 7" is 7 AM: the first time it comes on that day.
* A time already gone on a day that was named ("today at 6 am" at noon,
  "tonight at 9" at 22:00) is refused, not moved to another day; "today"
  with no time, or "this evening" once 6 PM has gone, asks for a time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

# ─── Fragments ──────────────────────────────────────────────────────────────

_HOUR_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_ONES = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9}
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
          "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
          "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}

WEEKDAYS: tuple[str, ...] = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)

_HOUR_WORD = "(?:" + "|".join(sorted(_HOUR_WORDS, key=len, reverse=True)) + ")"
# 10..59 in words: "fifteen", "thirty", "forty five", "fifty-nine".
_MINUTE_WORD = (
    "(?:(?:" + "|".join(_TENS) + ")(?:[ -](?:" + "|".join(_ONES) + "))?"
    "|" + "|".join(sorted(_TEENS, key=len, reverse=True)) + ")"
)
# "6 pm", "6pm", "6 p.m.", "6 a.m" (the router strips a final full stop).
_AMPM = r"(?:a\.?m\.?|p\.?m\.?)(?![a-z])"
_OCLOCK = r"o'? ?clock"
_PART_SUFFIX = r"(?: in the (?:morning|afternoon|evening)| at night)"
_DIGIT_HOUR = r"(?:[01]?\d|2[0-3])(?!\d)"

# "half past six", "quarter to seven", "ten past six", "20 minutes to 8".
_RELATIVE = (
    rf"(?:(?:a )?quarter|half|(?:\d{{1,2}}|{_MINUTE_WORD}|{'|'.join(_ONES)})(?: minutes?)?)"
    rf" (?:past|after|to|till|before|of) (?:{_DIGIT_HOUR}|{_HOUR_WORD}|noon|midday|midnight)"
)
_DIGIT_TIME = rf"{_DIGIT_HOUR}(?:[:.][0-5]\d)?(?: {_OCLOCK})?(?: ?{_AMPM})?"
_WORD_TIME = (
    rf"{_HOUR_WORD}(?: (?:{_OCLOCK}|oh (?:{'|'.join(_ONES)})|{_MINUTE_WORD}))?(?: {_AMPM})?"
)
_SPECIAL = r"(?:(?:twelve |12 )?noon|midday|midnight)"

#: A clock time, digits or words, with an optional am/pm and part of day.
TIME_PATTERN = (
    rf"(?:(?:{_RELATIVE}|{_DIGIT_TIME}|{_WORD_TIME})(?:{_PART_SUFFIX})?|{_SPECIAL})"
)

# The same, but only the forms that can't be a bare count of something.
_MARKED_DIGIT = (
    rf"{_DIGIT_HOUR}(?:(?:[:.][0-5]\d)(?: ?{_AMPM})?|(?: {_OCLOCK})?(?: ?{_AMPM})|"
    rf" {_OCLOCK})"
)
_MARKED_WORD = (
    rf"{_HOUR_WORD}(?:(?: (?:oh (?:{'|'.join(_ONES)})|{_MINUTE_WORD}))(?: {_AMPM})?"
    rf"|(?: {_OCLOCK})?(?: {_AMPM})| {_OCLOCK})"
)
#: A clock time with something that makes it one: am/pm, minutes,
#: "o'clock", a part of the day, "past"/"to", noon or midnight.
MARKED_TIME_PATTERN = (
    rf"(?:{_RELATIVE}(?:{_PART_SUFFIX})?|{_MARKED_DIGIT}(?:{_PART_SUFFIX})?"
    rf"|{_MARKED_WORD}(?:{_PART_SUFFIX})?"
    rf"|(?:{_DIGIT_HOUR}|{_HOUR_WORD}){_PART_SUFFIX}|{_SPECIAL})"
)

_PART = r"(?:morning|afternoon|evening|night)"
_WEEKDAY = "(?:" + "|".join(WEEKDAYS) + ")"
#: A day, or a part of one.
DAY_PATTERN = (
    rf"(?:today|tonight"
    rf"|(?:tomorrow|the day after tomorrow)(?: {_PART})?"
    rf"|this (?:morning|afternoon|evening)"
    rf"|in the (?:morning|afternoon|evening)"
    rf"|(?:on )?(?:this |next |the )?{_WEEKDAY}(?: {_PART})?)"
)

#: What follows "remind me" / "wake me up": "at 6 pm", "at 7 tomorrow",
#: "tomorrow", "tomorrow at 7", "on friday at 5 pm", "tonight at 9".
WHEN_PATTERN = (
    rf"(?:at {TIME_PATTERN}(?:,? {DAY_PATTERN})?"
    rf"|{DAY_PATTERN}(?:,? at {TIME_PATTERN})?)"
)

#: After "a reminder for" / "an alarm for": WHEN, or a marked clock time
#: with no "at" ("for 6 pm", "for 7 o'clock tomorrow").
WHEN_FOR_PATTERN = rf"(?:{WHEN_PATTERN}|{MARKED_TIME_PATTERN}(?:,? {DAY_PATTERN})?)"

#: After "an alarm for": any clock time, bare numbers included ("set an
#: alarm for 7" is a time; "for 7 minutes" doesn't fit, nothing may follow).
ALARM_WHEN_PATTERN = rf"(?:{WHEN_PATTERN}|{TIME_PATTERN}(?:,? {DAY_PATTERN})?)"


# ─── Parsing ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SpokenWhen:
    """A spoken time and/or day, before it is placed on the calendar.

    ``hour`` is as said (1-12 for a 12-hour form, 0-23 when ``h24``), or
    None when only a day was said."""

    hour: int | None = None
    minute: int = 0
    meridiem: str | None = None    # "am" | "pm"
    h24: bool = False              # "18:00", "07:30", noon, midnight
    midnight: bool = False
    day: str | None = None         # today | tonight | tomorrow | day_after | weekday
    weekday: int | None = None     # 0 = Monday
    next_week: bool = False        # "next friday" said on a Friday
    part: str | None = None        # morning | afternoon | evening | night


@dataclass(frozen=True)
class WhenProblem:
    """Why a spoken time can't be set: ``"past"`` (named day, time gone)
    or ``"need_time"`` ("today" with no time). ``target`` is the time that
    has gone, for the reply."""

    kind: str
    target: datetime | None = None


def _number(word: str) -> int | None:
    w = word.strip()
    if w.isdigit():
        return int(w)
    parts = re.split(r"[ -]", w)
    if len(parts) == 1:
        p = parts[0]
        return _HOUR_WORDS.get(p) or _TEENS.get(p) or _TENS.get(p) or _ONES.get(p)
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _ONES:
        return _TENS[parts[0]] + _ONES[parts[1]]
    return None


_AMPM_SEARCH = re.compile(r"(?:^|[\d ])(?P<ap>[ap])\.?m\.?(?![a-z])")
_PART_SEARCH = re.compile(
    r"\b(?:in the (?P<p1>morning|afternoon|evening)|at (?P<p2>night)"
    r"|(?:tomorrow|today|the day after tomorrow|"
    + _WEEKDAY + r") (?P<p3>morning|afternoon|evening|night)"
    r"|this (?P<p4>morning|afternoon|evening))\b"
)
_DAY_SEARCH = re.compile(
    r"\b(?P<d>the day after tomorrow|tomorrow|tonight|today"
    r"|(?P<next>next |this |the )?(?P<wd>" + _WEEKDAY + r"))\b"
)
_RELATIVE_SEARCH = re.compile(
    rf"(?P<amt>(?:a )?quarter|half|\d{{1,2}}|{_MINUTE_WORD}|{'|'.join(_ONES)})(?: minutes?)?"
    rf" (?P<dir>past|after|to|till|before|of) (?P<hr>\d{{1,2}}|{_HOUR_WORD}|noon|midday|midnight)\b"
)
_DIGIT_SEARCH = re.compile(r"(?<![\d:.])(?P<h>\d{1,2})(?:[:.](?P<m>[0-5]\d))?(?!\d)")
_WORD_SEARCH = re.compile(
    rf"\b(?P<h>{_HOUR_WORD})(?: (?:oh (?P<oh>{'|'.join(_ONES)})|(?P<m>{_MINUTE_WORD})))?\b"
)
_SPECIAL_SEARCH = re.compile(r"\b(?P<s>noon|midday|midnight)\b")


def _time_text(text: str) -> str:
    """The part of a WHEN text that holds the time: what follows "at", or
    all of it (a bare time after "for")."""
    m = re.search(r"\bat (?!night\b)", text)
    return text[m.end():] if m else text


def parse_when(text: str) -> SpokenWhen | None:
    """Read a WHEN / TIME / DAY fragment's text. None when it holds
    nothing usable (an hour past 23, minutes past 59)."""
    t = " ".join(text.lower().split())
    fields: dict = {}

    pm = _PART_SEARCH.search(t)
    if pm:
        fields["part"] = next(g for g in pm.groups() if g)
    dm = _DAY_SEARCH.search(t)
    if dm:
        word = dm.group("d")
        if dm.group("wd"):
            fields["day"] = "weekday"
            fields["weekday"] = WEEKDAYS.index(dm.group("wd"))
            fields["next_week"] = (dm.group("next") or "").strip() == "next"
        elif word == "the day after tomorrow":
            fields["day"] = "day_after"
        else:
            fields["day"] = word
    elif re.search(r"\bthis (?:morning|afternoon|evening)\b", t):
        fields["day"] = "today"
    if fields.get("day") == "tonight" and "part" not in fields:
        fields["part"] = "night"

    tt = _time_text(t)
    sm = _SPECIAL_SEARCH.search(tt)
    rm = _RELATIVE_SEARCH.search(tt)
    if rm and rm.group("hr") in ("noon", "midday", "midnight"):
        # "ten till noon" is 11:50, "ten past midnight" 0:10: a 24-hour
        # reading ("of" is "to", as in "quarter of seven").
        amt = rm.group("amt")
        minutes = 15 if "quarter" in amt else 30 if amt == "half" else _number(amt)
        if minutes is None or minutes >= 60:
            return None
        base = 24 * 60 if rm.group("hr") == "midnight" else 12 * 60
        total = (base + (minutes if rm.group("dir") in ("past", "after") else -minutes)) % (24 * 60)
        fields.update(hour=total // 60, minute=total % 60, h24=True)
    elif sm:
        if sm.group("s") == "midnight":
            fields.update(hour=0, minute=0, h24=True, midnight=True)
        else:
            fields.update(hour=12, minute=0, h24=True)
    elif rm:
        amt = rm.group("amt")
        if "quarter" in amt:
            minutes = 15
        elif amt == "half":
            minutes = 30
        else:
            minutes = _number(amt)
        hour = _number(rm.group("hr"))
        if minutes is None or hour is None or not 1 <= hour <= 12 or minutes >= 60:
            return None
        if rm.group("dir") in ("past", "after"):
            total = (hour % 12) * 60 + minutes
        else:
            total = ((hour % 12) * 60 - minutes) % 720
        fields.update(hour=(total // 60) or 12, minute=total % 60)
    else:
        dg = _DIGIT_SEARCH.search(tt)
        wm = None if dg else _WORD_SEARCH.search(tt)
        if dg:
            hour = int(dg.group("h"))
            minute = int(dg.group("m") or 0)
            if hour > 23:
                return None
            # "18:00", "0:30" and "07:30" are 24-hour clock readings.
            h24 = hour > 12 or hour == 0 or (dg.group("h").startswith("0") and dg.group("m") is not None)
            fields.update(hour=hour, minute=minute, h24=h24)
        elif wm:
            hour = _HOUR_WORDS[wm.group("h")]
            if wm.group("oh"):
                minute = _ONES[wm.group("oh")]
            elif wm.group("m"):
                minute = _number(wm.group("m")) or 0
            else:
                minute = 0
            if minute >= 60:
                return None
            fields.update(hour=hour, minute=minute)
    if fields.get("hour") is not None and not fields.get("h24"):
        am = _AMPM_SEARCH.search(tt)
        if am:
            fields["meridiem"] = "am" if am.group("ap") == "a" else "pm"
    if fields.get("hour") is None and "day" not in fields and "part" not in fields:
        return None
    return SpokenWhen(**fields)


# ─── Placing it on the calendar ─────────────────────────────────────────────

#: The hour a part of the day means when no time is said.
DEFAULT_PART_HOUR: dict[str, int] = {"morning": 9, "afternoon": 14, "evening": 18, "night": 20}
#: The hour a day means when neither a time nor a part of it is said.
DEFAULT_DAY_HOUR = 9


def candidate_hours(w: SpokenWhen) -> list[tuple[int, int]]:
    """(hour 0-23, extra days) in the order to try: the extra day is for a
    time that belongs to the night after the named day (midnight, "1 at
    night")."""
    if w.hour is None:
        if w.part:
            return [(DEFAULT_PART_HOUR[w.part], 0)]
        return [(DEFAULT_DAY_HOUR, 0)]
    h = w.hour
    if w.midnight:
        return [(0, 1 if w.day is not None else 0)]
    if w.h24:
        return [(h, 0)]
    if w.meridiem == "am":
        return [(h % 12, 0)]
    if w.meridiem == "pm":
        return [(h % 12 + 12, 0)]
    if w.part == "morning":
        return [(h % 12, 0)]
    if w.part in ("afternoon", "evening"):
        return [(h % 12 + 12, 0)]
    if w.part == "night":
        if h == 12 or h <= 4:
            # "tonight at 12", "1 at night": the small hours after that day.
            return [(h % 12, 1 if w.day is not None else 0)]
        return [(h % 12 + 12, 0)]
    # No AM/PM and no part of the day: whichever comes first.
    return [(h % 12, 0), (h % 12 + 12, 0)]


def resolve(w: SpokenWhen, now: datetime) -> datetime | WhenProblem:
    """The wall-clock datetime ``w`` means, said at wall-clock ``now``
    (both naive, household zone): the first candidate after ``now``."""
    today = now.date()
    if w.day == "today" and w.hour is None and w.part is None:
        return WhenProblem("need_time")
    if w.day is None:
        days: list[date] = [today, today + timedelta(days=1)]
    elif w.day in ("today", "tonight"):
        days = [today]
    elif w.day == "tomorrow":
        days = [today + timedelta(days=1)]
    elif w.day == "day_after":
        days = [today + timedelta(days=2)]
    else:
        ahead = (int(w.weekday or 0) - today.weekday()) % 7
        if w.next_week and ahead == 0:
            ahead = 7
        days = [today + timedelta(days=ahead), today + timedelta(days=ahead + 7)]
    candidates = candidate_hours(w)
    gone: datetime | None = None
    for d in days:
        options = sorted(
            datetime.combine(d + timedelta(days=extra), time(hour, w.minute))
            for hour, extra in candidates
        )
        for target in options:
            if target > now:
                return target
            gone = target
    if w.hour is None:
        # "this evening" at 23:00: its usual hour has gone, but the person
        # may still mean a time later tonight. Ask for one.
        return WhenProblem("need_time")
    return WhenProblem("past", gone)


# ─── Saying it ──────────────────────────────────────────────────────────────


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th', 'th', 'th', 'th', 'th', 'th'][n % 10]}"


def spoken_clock(t: datetime) -> str:
    """"6 PM", "6:30 AM", "noon", "midnight"."""
    if t.minute == 0 and t.hour == 12:
        return "noon"
    if t.minute == 0 and t.hour == 0:
        return "midnight"
    h12 = t.hour % 12 or 12
    suffix = "AM" if t.hour < 12 else "PM"
    if t.minute == 0:
        return f"{h12} {suffix}"
    return f"{h12}:{t.minute:02d} {suffix}"


def spoken_day(d: date, today: date) -> str:
    """"today", "tomorrow", "on Friday", "on Friday, October 23rd"."""
    days = (d - today).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    name = d.strftime("%A")
    if 1 < days < 7:
        return f"on {name}"
    return f"on {name}, {d.strftime('%B')} {_ordinal(d.day)}"


def spoken_when(target: datetime, now: datetime) -> str:
    """When ``target`` is, said from ``now``: "at 6 PM", "tomorrow at 7
    AM", "on Friday at 5 PM", "at midnight" (tonight's), "at midnight on
    Friday night"."""
    clock = spoken_clock(target)
    today = now.date()
    if clock == "midnight":
        eve = target.date() - timedelta(days=1)
        if eve == today:
            return "at midnight"
        if eve == today + timedelta(days=1):
            return "at midnight tomorrow night"
        return f"at midnight {spoken_day(eve, today)} night"
    day = spoken_day(target.date(), today)
    if day == "today":
        return f"at {clock}"
    return f"{day} at {clock}"


def spoken_time_and_day(target: datetime, now: datetime) -> str:
    """The time first, for "set for ...": "7 AM", "7 AM tomorrow", "5 PM
    on Friday", "midnight", "midnight tomorrow night"."""
    clock = spoken_clock(target)
    if clock == "midnight":
        return spoken_when(target, now)[len("at "):]
    day = spoken_day(target.date(), now.date())
    return clock if day == "today" else f"{clock} {day}"
