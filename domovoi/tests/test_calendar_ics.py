"""The ICS reader behind calendars by link (domovoi/calendar_ics.py).

DB-free and tzdata-free: zones are the feed's own VTIMEZONE (what Outlook
sends alongside its Windows zone names), UTC, or a pinned household zone.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from domovoi import calendar_ics, household_time

EST = timezone(timedelta(hours=-5))
UTC = timezone.utc

VTZ = """BEGIN:VTIMEZONE
TZID:Eastern Standard Time
BEGIN:STANDARD
DTSTART:16010101T020000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=1SU;BYMONTH=11
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010101T020000
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=2SU;BYMONTH=3
END:DAYLIGHT
END:VTIMEZONE
"""


def cal(*events: str, head: str = "") -> str:
    body = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + head
    for e in events:
        body += "BEGIN:VEVENT\r\n" + e.strip().replace("\n", "\r\n") + "\r\nEND:VEVENT\r\n"
    return body + "END:VCALENDAR\r\n"


def parse(text: str, start=datetime(2026, 10, 1, tzinfo=UTC), end=datetime(2027, 3, 1, tzinfo=UTC), **kw):
    with household_time.use_zone(EST):
        return calendar_ics.parse_ics(text, window_start=start, window_end=end, **kw)


def starts(result) -> list[datetime]:
    return [e.start for e in result.events]


def test_not_a_calendar_is_refused() -> None:
    with pytest.raises(calendar_ics.IcsError):
        parse("<html>sign in</html>")


def test_a_single_event_with_folding_escapes_and_a_name() -> None:
    text = cal(
        "UID:1\nSUMMARY:Dinner\\, with Mom\nLOCATION:12 Main St\\; \n back door\n"
        "DTSTART:20261105T230000Z\nDTEND:20261106T010000Z",
        head="X-WR-CALNAME:Family\r\n",
    )
    r = parse(text)
    assert r.name == "Family"
    [e] = r.events
    assert e.title == "Dinner, with Mom"
    assert e.location == "12 Main St; back door"
    assert e.start == datetime(2026, 11, 5, 23, tzinfo=UTC)
    assert e.end == datetime(2026, 11, 6, 1, tzinfo=UTC)
    assert not e.all_day


def test_an_all_day_event_is_the_households_local_midnights() -> None:
    r = parse(cal("UID:h\nSUMMARY:Holiday\nDTSTART;VALUE=DATE:20261126\nDTEND;VALUE=DATE:20261128"))
    [e] = r.events
    assert e.all_day
    assert e.start == datetime(2026, 11, 26, 5, tzinfo=UTC)
    assert e.end == datetime(2026, 11, 28, 5, tzinfo=UTC)


def test_a_windows_zone_name_is_read_from_the_feeds_vtimezone() -> None:
    # 9 AM in New York is 13:00Z before the November change, 14:00Z after.
    r = parse(cal(
        "UID:a\nSUMMARY:Standup\nDTSTART;TZID=Eastern Standard Time:20261030T090000\n"
        "DTEND;TZID=Eastern Standard Time:20261030T091500\nRRULE:FREQ=DAILY;COUNT=4",
        head=VTZ.replace("\n", "\r\n"),
    ))
    assert starts(r) == [
        datetime(2026, 10, 30, 13, tzinfo=UTC), datetime(2026, 10, 31, 13, tzinfo=UTC),
        datetime(2026, 11, 1, 14, tzinfo=UTC), datetime(2026, 11, 2, 14, tzinfo=UTC),
    ]
    assert all(e.end - e.start == timedelta(minutes=15) for e in r.events)


def test_weekly_count_exdate_and_a_moved_and_a_cancelled_occurrence() -> None:
    text = cal(
        "UID:s\nSUMMARY:Practice\nDTSTART:20261102T220000Z\nDURATION:PT1H\n"
        "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=7\nEXDATE:20261104T220000Z",
        "UID:s\nRECURRENCE-ID:20261106T220000Z\nSUMMARY:Practice (gym)\nDTSTART:20261106T230000Z\nDURATION:PT1H",
        "UID:s\nRECURRENCE-ID:20261109T220000Z\nSTATUS:CANCELLED\nDTSTART:20261109T220000Z",
    )
    r = parse(text)
    assert [(e.start.day, e.start.hour, e.title) for e in r.events] == [
        (2, 22, "Practice"), (6, 23, "Practice (gym)"), (11, 22, "Practice"),
        (13, 22, "Practice"), (16, 22, "Practice"),
    ]
    # Every occurrence has its own id.
    assert len({e.uid for e in r.events}) == 5


def test_monthly_last_friday_and_bysetpos_and_negative_monthday() -> None:
    r = parse(cal("UID:m\nSUMMARY:Payday\nDTSTART:20261030T150000Z\nRRULE:FREQ=MONTHLY;BYDAY=-1FR;COUNT=3"))
    assert [e.start.date().isoformat() for e in r.events] == ["2026-10-30", "2026-11-27", "2026-12-25"]
    r = parse(cal("UID:w\nSUMMARY:Last workday\nDTSTART:20261030T150000Z\n"
                  "RRULE:FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT=3"))
    assert [e.start.date().isoformat() for e in r.events] == ["2026-10-30", "2026-11-30", "2026-12-31"]
    r = parse(cal("UID:b\nSUMMARY:Bills\nDTSTART:20200131T170000Z\nRRULE:FREQ=MONTHLY;BYMONTHDAY=-1"),
              end=datetime(2027, 1, 1, tzinfo=UTC))
    assert [e.start.date().isoformat() for e in r.events] == ["2026-10-31", "2026-11-30", "2026-12-31"]


def test_yearly_birthday_and_until() -> None:
    r = parse(cal("UID:y\nSUMMARY:Birthday\nDTSTART;VALUE=DATE:19900214\nRRULE:FREQ=YEARLY"),
              end=datetime(2028, 3, 1, tzinfo=UTC))
    assert [e.start.date().isoformat() for e in r.events] == ["2027-02-14", "2028-02-14"]
    r = parse(cal("UID:u\nSUMMARY:Class\nDTSTART:20261001T140000Z\nRRULE:FREQ=WEEKLY;UNTIL=20261015T140000Z"))
    assert len(r.events) == 3
    r = parse(cal("UID:v\nSUMMARY:Class\nDTSTART:20261001T140000Z\nRRULE:FREQ=WEEKLY;UNTIL=20261015"))
    assert len(r.events) == 3


def test_a_series_begun_decades_ago_costs_nothing_and_counts_right() -> None:
    r = parse(cal("UID:d\nSUMMARY:Pills\nDTSTART:19700101T130000Z\nRRULE:FREQ=DAILY"),
              start=datetime(2026, 11, 1, tzinfo=UTC), end=datetime(2026, 11, 8, tzinfo=UTC))
    assert len(r.events) == 7
    r = parse(cal("UID:i\nSUMMARY:Every other day\nDTSTART:20261028T130000Z\nRRULE:FREQ=DAILY;INTERVAL=2"),
              start=datetime(2026, 11, 1, tzinfo=UTC), end=datetime(2026, 11, 8, tzinfo=UTC))
    assert [e.start.day for e in r.events] == [1, 3, 5, 7]


def test_a_rule_outside_the_supported_set_is_its_first_occurrence_only() -> None:
    r = parse(cal("UID:x\nSUMMARY:Odd\nDTSTART:20261105T130000Z\nRRULE:FREQ=YEARLY;BYWEEKNO=20"))
    assert starts(r) == [datetime(2026, 11, 5, 13, tzinfo=UTC)]


def test_cancelled_events_and_events_outside_the_window_are_left_out() -> None:
    r = parse(cal(
        "UID:c\nSUMMARY:Gone\nSTATUS:CANCELLED\nDTSTART:20261110T170000Z",
        "UID:o\nSUMMARY:Old\nDTSTART:20250110T170000Z",
        "UID:k\nDTSTART:20261111T170000Z",
    ))
    assert [e.title for e in r.events] == ["(no title)"]


def test_floating_times_are_the_households_wall_clock() -> None:
    r = parse(cal("UID:f\nSUMMARY:Lunch\nDTSTART:20261105T120000"))
    assert starts(r) == [datetime(2026, 11, 5, 17, tzinfo=UTC)]
    # An unknown TZID falls back the same way rather than dropping the event.
    r = parse(cal("UID:g\nSUMMARY:Lunch\nDTSTART;TZID=Nowhere/Special:20261105T120000"))
    assert starts(r) == [datetime(2026, 11, 5, 17, tzinfo=UTC)]


def test_event_and_occurrence_caps() -> None:
    many = [f"UID:{i}\nSUMMARY:E{i}\nDTSTART:20261105T{(i % 24):02d}0000Z" for i in range(30)]
    r = parse(cal(*many), max_events=10)
    assert len(r.events) == 10 and r.truncated
    r = parse(cal("UID:h\nSUMMARY:Hourly-ish\nDTSTART:20261001T000000Z\nRRULE:FREQ=DAILY"),
              end=datetime(2040, 1, 1, tzinfo=UTC))
    assert len(r.events) == calendar_ics.MAX_OCCURRENCES
