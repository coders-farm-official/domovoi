import Foundation

// ---------------------------------------------------------------------------
// How the Calendar screen writes a time down — `CalendarModels.kt:100-146`
// and `EventEditor.kt:282`.
//
// **Hand-written tables, not locale formatters**, for the reason
// `homeClock(ms:timeZone:)` and `homeDate(ms:timeZone:)` already give: the
// Kotlin formats in `Locale.US` and lower-cases the result, so a phone set to
// a 24-hour or non-English locale still reads `9:00am` and `wed, nov 4` here —
// the same strings the house's dashboard writes. A `DateFormatter` with a
// localized template would spell both differently per region while the same
// house's dashboard did not, and the two would disagree about one event.
//
// There is **no second clock**: `fmtClock` is `h:mm a` in `Locale.US`,
// lower-cased with the space removed, which is exactly
// `homeClock(ms:timeZone:)`, so ``calendarClock(ms:timeZone:)`` delegates.
// ---------------------------------------------------------------------------

// MARK: - the clock

/// `fmtClock(t)` (`CalendarModels.kt:116`) — `9:00am`, `12:00am`, `12pm`'s
/// sibling.
///
/// One line, on purpose: it is ``homeClock(ms:timeZone:)``. Two
/// implementations of one format in one module is how two screens come to
/// disagree about the same minute, which is the argument ``relTimeMs(_:nowMs:)``
/// makes for its own split.
public func calendarClock(ms: Int64, timeZone: TimeZone) -> String {
    homeClock(ms: ms, timeZone: timeZone)
}

/// `fmtRange(e)` (`CalendarModels.kt:119`) — `9:00am – 10:00am`.
///
/// The dash is an **en dash, U+2013**, with a space either side, as the
/// Kotlin's literal is. The end falls back to the start when `ends_at` is
/// null, through ``calendarBlockEndMs(_:)``, which is why a view only draws
/// this when `ends_at` is there (`CalendarScreen.kt:393`, `EventEditor.kt:93`).
///
/// ``calendarBlockEndMs(_:)`` and **not** ``calendarEndMs(_:)``, which since
/// answer 17 gives an open-ended event an hour: that hour is what the agenda's
/// floor measures against, and a clock built on it would read
/// `3:00pm – 4:00pm` for a row the house never gave an end for.
public func calendarRange(_ entry: CalendarEntry, in timeZone: TimeZone) -> String {
    let start = calendarClock(ms: calendarStartMs(entry), timeZone: timeZone)
    let end = calendarClock(ms: calendarBlockEndMs(entry), timeZone: timeZone)
    return "\(start) – \(end)"
}

/// `hourLabel(h)` (`CalendarModels.kt:146`) — `6am`, `12pm`, `10pm`.
///
/// `((h + 11) % 12) + 1` is the Kotlin's arithmetic, kept rather than
/// rewritten, because it is the one that makes midnight and noon both `12`.
public func calendarHourLabel(_ hour: Int) -> String {
    "\(((hour + 11) % 12) + 1)\(hour < 12 ? "am" : "pm")"
}

// MARK: - the dates

/// `fmtDayLabel(d)` (`CalendarModels.kt:122`) — `wed, nov 4`.
public func calendarDayLabel(_ day: CalendarDay) -> String {
    "\(calendarWeekdaysShort[day.weekdayIndex]), \(homeMonths[day.month - 1]) \(day.day)"
}

/// `fmtDateLong(d)` (`CalendarModels.kt:124`) — `wednesday, november 4, 2026`.
/// The detail sheet's line under the title.
public func calendarDateLong(_ day: CalendarDay) -> String {
    "\(calendarWeekdaysLong[day.weekdayIndex]), "
        + "\(calendarMonthsLong[day.month - 1]) \(day.day), \(day.year)"
}

/// `fmtMonthYr(d)` (`CalendarModels.kt:126`) — `november 2026`, the month
/// view's range label.
public func calendarMonthYear(_ day: CalendarDay) -> String {
    "\(calendarMonthsLong[day.month - 1]) \(day.year)"
}

/// `value.format(dateBtnFmt).lowercase()` (`EventEditor.kt:282,:291`) —
/// `nov 4, 2026`, the editor's date button.
public func calendarDateButton(_ day: CalendarDay) -> String {
    "\(homeMonths[day.month - 1]) \(day.day), \(day.year)"
}

/// The same for an instant, which is what a draft holds.
public func calendarDateButton(ms: Int64, in timeZone: TimeZone) -> String {
    calendarDateButton(CalendarDay(ms: ms, in: timeZone))
}

/// `rangeLabel(view, anchor)` (`CalendarModels.kt:128-143`) — what sits
/// between the two chevrons.
///
/// The week branch is ``calendarWeekRangeLabel(_:)``, which was written and
/// tested before there was a week view to use it (answer 14 added the view;
/// its label needed nothing new).
public func calendarRangeLabel(mode: CalendarViewMode, anchor: CalendarDay) -> String {
    switch mode {
    case .month:
        return calendarMonthYear(anchor)
    case .week:
        return calendarWeekRangeLabel(anchor)
    case .day:
        // `EEEE, MMMM d` — no year, unlike `fmtDateLong` (`:141` vs `:124`).
        return "\(calendarWeekdaysLong[anchor.weekdayIndex]), "
            + "\(calendarMonthsLong[anchor.month - 1]) \(anchor.day)"
    case .agenda:
        return "from \(calendarDayLabel(anchor))"
    }
}

/// The week's own label — `CalendarModels.kt:130-140`.
///
/// `nov 1 – 7, 2026` inside one month, `nov 29 – dec 5, 2026` across two. The
/// two spellings are not cosmetic: the collapsed one drops the month from the
/// right-hand side, so a week that crosses a boundary and used it would read
/// `nov 29 – 5, 2026`. En dash, U+2013, in both.
///
/// `ws.month == we.month` is the Kotlin's test and it compares the **month
/// only**, so the last week of december 2026 and a hypothetical same-month
/// week a year apart would collapse. That cannot arise from
/// ``calendarWeekStart(_:)``, whose two ends are six days apart, and it is
/// kept as written rather than "fixed" into a different function.
public func calendarWeekRangeLabel(_ anchor: CalendarDay) -> String {
    let start = calendarWeekStart(anchor)
    let end = start.adding(days: 6)
    if start.month == end.month {
        return "\(homeMonths[start.month - 1]) \(start.day) – \(end.day), \(end.year)"
    }
    return "\(homeMonths[start.month - 1]) \(start.day) – "
        + "\(homeMonths[end.month - 1]) \(end.day), \(end.year)"
}

// MARK: - what a write sends

/// The `starts_at` / `ends_at` a save puts on the wire —
/// `2026-11-04T19:30:00Z`, the instant in **UTC**.
///
/// **This is where this port deliberately differs from Android.**
/// `CalendarModels.kt:113` is `yyyy-MM-dd'T'HH:mm:ss` with no offset at all,
/// sent at `CalendarScreen.kt:104-105`. The column is `TIMESTAMPTZ`
/// (`V001__baseline.sql:231`), and asyncpg encodes a naive `datetime` with
/// `astimezone(utc)` (`pgproto/codecs/datetime.pyx:222`), which reads it in
/// the **web backend process's** zone. On a house the installer set up that
/// is the household's zone, so an Android save lands right at home. It is
/// wrong whenever the phone's zone is not the server's (travelling, or a
/// server never given `--timezone`): the save then means that wall clock in
/// the *server's* zone and reads back shifted by the difference.
///
/// The dashboard this screen is a port of sends `new Date(s).toISOString()`
/// (`calendar.jsx:36`), an absolute instant that depends on no one's zone, and
/// so does this. The Android side is `ANDROID-DEFECTS-FOUND-BY-IOS.md` #27.
///
/// Spelt out by `DateComponents` rather than left to `ISO8601DateFormatter`
/// for ``calendarPath(dayStartMs:timeZone:)``'s reason: a test pins the whole
/// string, and a formatter's defaults are not the thing under test. Seconds
/// are always written and the fraction never is — the server parses either
/// (`CalendarEventCreate.starts_at` is a `datetime`) and a draft's seconds are
/// always zero anyway, since the picker has no seconds field.
public func calendarInstant(_ date: Date) -> String {
    var calendar = Foundation.Calendar(identifier: .gregorian)
    calendar.timeZone = TimeZone(secondsFromGMT: 0) ?? .gmt
    let p = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
    return String(
        format: "%04d-%02d-%02dT%02d:%02d:%02dZ",
        p.year ?? 1970, p.month ?? 1, p.day ?? 1, p.hour ?? 0, p.minute ?? 0, p.second ?? 0
    )
}

/// ``calendarInstant(_:)`` with the colons percent-encoded, which is what a
/// query string needs.
///
/// Built on ``calendarInstant(_:)`` rather than beside it: one instant
/// formatter in this file, already pinned character for character by
/// `CalendarFormatTests`, and one substitution over it. `HomeReads.swift` has
/// its own `isoInstantEncoded`, file-private and not reachable from here, and
/// a second hand-rolled `DateComponents` formatter is exactly the duplicate
/// that makes two screens disagree about one minute.
///
/// `:` is the only character that needs it: `-`, `T` and `Z` are all left
/// alone by `URLEncoder`, which is what the server's other caller sends and
/// what `HomeModelsTest.calendarReadStartsAtLocalMidnightForAWeek` pins.
public func calendarInstantEncoded(ms: Int64) -> String {
    calendarInstant(Date(timeIntervalSince1970: Double(ms) / 1000))
        .replacingOccurrences(of: ":", with: "%3A")
}

/// `GET /api/calendar/events` for one window — the owner's answer 16.
///
/// `start` always, `end` only when the window has a ceiling (the route's
/// docstring: "omit ``end`` for everything from ``start`` onwards"), and
/// `limit` always, so that a read which came back full can be recognised as
/// possibly cut (``CalendarPage/truncated``).
///
/// The three parameters go in the route's own order — `start`, `end`, `limit`
/// (`calendar.py:38-41`) — because a test asserts the whole string, which is
/// the same reason ``calendarInstant(_:)`` is spelt out by hand.
public func calendarEventsPath(
    _ window: CalendarWindow,
    limit: Int = calendarWindowLimit
) -> String {
    var path = "/api/calendar/events?start=\(calendarInstantEncoded(ms: window.startMs))"
    if !window.isOpenEnded {
        path += "&end=\(calendarInstantEncoded(ms: window.endMs))"
    }
    return path + "&limit=\(limit)"
}

// MARK: - the tables

/// `EEE` in `Locale.US`, lower-cased, **Sunday first** — the index
/// ``CalendarDay/weekdayIndex`` produces, and the same seven words
/// `CalendarGrids.kt:71` draws as the month grid's heads.
public let calendarWeekdaysShort = CalendarStrings.weekdayHeads

/// `EEEE` in `Locale.US`, lower-cased, Sunday first.
public let calendarWeekdaysLong = [
    "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
]

/// `MMMM` in `Locale.US`, lower-cased. `MMM` is ``homeMonths``, which this
/// file reuses rather than copying — one table, so the calendar and Home's
/// header cannot disagree about how november is spelt.
public let calendarMonthsLong = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]
