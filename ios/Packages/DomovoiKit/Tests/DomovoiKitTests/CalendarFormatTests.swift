import Foundation
import XCTest
@testable import DomovoiKit

/// How the Calendar screen writes a time down — every string
/// `CalendarModels.kt:100-146` produces, and the one this port deliberately
/// changed.
///
/// Whole strings, never containment: a clause dropped off either end of
/// `wed, nov 4` would pass a `contains` check, which is why
/// `ShellStringsTests` compares whole strings too.
final class CalendarFormatTests: XCTestCase {

    private let chicago = TimeZone(identifier: "America/Chicago")!
    private let tokyo = TimeZone(identifier: "Asia/Tokyo")!

    private func ms(_ iso: String) -> Int64 {
        guard let value = isoMs(iso) else {
            XCTFail("the fixture's own timestamp \(iso) did not parse")
            return 0
        }
        return value
    }

    private func day(_ year: Int, _ month: Int, _ dayOfMonth: Int) -> CalendarDay {
        CalendarDay(year: year, month: month, day: dayOfMonth)
    }

    // MARK: - the clock

    /// `h:mm a` in `Locale.US`, lower-cased, space removed — and midnight and
    /// noon are `12`, which is what `h` does where `K` would give `0`.
    func testTheClockIsTheHouseSpellingAndNotTheDevicesLocale() {
        XCTAssertEqual("9:00am", calendarClock(ms: ms("2026-11-04T09:00:00-06:00"), timeZone: chicago))
        XCTAssertEqual("2:05pm", calendarClock(ms: ms("2026-11-04T14:05:00-06:00"), timeZone: chicago))
        XCTAssertEqual("12:00am", calendarClock(ms: ms("2026-11-04T00:00:00-06:00"), timeZone: chicago))
        XCTAssertEqual("12:30pm", calendarClock(ms: ms("2026-11-04T12:30:00-06:00"), timeZone: chicago))
        XCTAssertEqual("11:59pm", calendarClock(ms: ms("2026-11-04T23:59:00-06:00"), timeZone: chicago))
    }

    /// The same instant, two zones: the clock is the *reader's*, which is why
    /// the zone is a parameter everywhere in this file.
    func testTheSameInstantReadsDifferentlyInTwoZones() {
        let instant = ms("2026-11-04T20:30:00+00:00")
        XCTAssertEqual("2:30pm", calendarClock(ms: instant, timeZone: chicago))
        XCTAssertEqual("5:30am", calendarClock(ms: instant, timeZone: tokyo))
    }

    /// `fmtRange` — the dash is an **en dash, U+2013**, which no amount of
    /// reading the source aloud distinguishes from a hyphen.
    func testTheRangeUsesAnEnDashWithSpaces() {
        let entry = CalendarEntry(
            id: 1,
            title: "book club",
            startsAt: "2026-11-07T19:00:00+00:00",
            endsAt: "2026-11-07T21:00:00+00:00"
        )
        let text = calendarRange(entry, in: TimeZone(secondsFromGMT: 0)!)
        XCTAssertEqual("7:00pm – 9:00pm", text)
        XCTAssertTrue(text.unicodeScalars.contains("\u{2013}"), "a hyphen crept in: \(text)")
    }

    /// With no `ends_at` the range is the start twice, through
    /// ``calendarBlockEndMs(_:)`` — which is why the views only draw it when
    /// `ends_at` is there.
    ///
    /// Through the **block** end and not ``calendarEndMs(_:)``, which since
    /// answer 17 invents an hour for the agenda's floor: this is the case that
    /// fails if that hour ever reaches a clock.
    func testARangeWithNoEndRepeatsTheStart() {
        let open = CalendarEntry(id: 2, title: "all afternoon", startsAt: "2026-11-07T19:00:00+00:00")
        XCTAssertEqual("7:00pm – 7:00pm", calendarRange(open, in: TimeZone(secondsFromGMT: 0)!))
    }

    func testTheHourLabelsAreTheGridsSixteenRowsAndMidnightIsTwelve() {
        XCTAssertEqual("12am", calendarHourLabel(0))
        XCTAssertEqual("6am", calendarHourLabel(6))
        XCTAssertEqual("11am", calendarHourLabel(11))
        XCTAssertEqual("12pm", calendarHourLabel(12))
        XCTAssertEqual("1pm", calendarHourLabel(13))
        XCTAssertEqual("10pm", calendarHourLabel(22))
    }

    // MARK: - the dates

    func testTheDateSpellingsAreLowerCaseUsEnglish() {
        let wednesday = day(2026, 11, 4)
        XCTAssertEqual("wed, nov 4", calendarDayLabel(wednesday))
        XCTAssertEqual("wednesday, november 4, 2026", calendarDateLong(wednesday))
        XCTAssertEqual("november 2026", calendarMonthYear(wednesday))
        XCTAssertEqual("nov 4, 2026", calendarDateButton(wednesday))
    }

    /// Every month and every weekday, because a table with one wrong entry is
    /// wrong on one day or one month in twelve and reads fine the rest of the
    /// time.
    func testEveryMonthAndEveryWeekdayIsSpeltOnce() {
        XCTAssertEqual(12, Set(calendarMonthsLong).count)
        XCTAssertEqual(12, Set(homeMonths).count)
        XCTAssertEqual(7, Set(calendarWeekdaysLong).count)
        XCTAssertEqual(7, Set(calendarWeekdaysShort).count)
        for index in 0..<12 {
            XCTAssertTrue(
                calendarMonthsLong[index].hasPrefix(homeMonths[index]),
                "\(calendarMonthsLong[index]) is not the long form of \(homeMonths[index])"
            )
        }
        for index in 0..<7 {
            XCTAssertTrue(
                calendarWeekdaysLong[index].hasPrefix(calendarWeekdaysShort[index]),
                "\(calendarWeekdaysLong[index]) / \(calendarWeekdaysShort[index])"
            )
        }
        // Sunday first, which is what makes the grid agree with the dashboard.
        XCTAssertEqual("sun", calendarWeekdaysShort.first)
        XCTAssertEqual("sat", calendarWeekdaysShort.last)
    }

    func testTheDateButtonForAnInstantReadsItInTheGivenZone() {
        // 02:00Z on the 5th is still the 4th in Chicago.
        let instant = ms("2026-11-05T02:00:00+00:00")
        XCTAssertEqual("nov 4, 2026", calendarDateButton(ms: instant, in: chicago))
        XCTAssertEqual("nov 5, 2026", calendarDateButton(ms: instant, in: TimeZone(secondsFromGMT: 0)!))
    }

    // MARK: - the range label

    func testTheRangeLabelIsPerView() {
        let anchor = day(2026, 11, 4)
        XCTAssertEqual("november 2026", calendarRangeLabel(mode: .month, anchor: anchor))
        XCTAssertEqual(
            "wednesday, november 4",
            calendarRangeLabel(mode: .day, anchor: anchor),
            "day has no year, unlike the sheet's `fmtDateLong`"
        )
        XCTAssertEqual("from wed, nov 4", calendarRangeLabel(mode: .agenda, anchor: anchor))
    }

    /// Both week spellings, which is the half of the week view this port
    /// keeps. The collapsed one drops the month from the right, so a week that
    /// crossed a boundary and used it would read `nov 29 – 5, 2026`.
    func testTheWeekLabelCollapsesInsideOneMonthAndSpellsBothAcrossTwo() {
        XCTAssertEqual("nov 1 – 7, 2026", calendarWeekRangeLabel(day(2026, 11, 4)))
        XCTAssertEqual(
            "nov 1 – 7, 2026",
            calendarWeekRangeLabel(day(2026, 11, 1)),
            "the sunday itself anchors its own week"
        )
        XCTAssertEqual("nov 29 – dec 5, 2026", calendarWeekRangeLabel(day(2026, 11, 30)))
        XCTAssertEqual(
            "dec 27 – jan 2, 2027",
            calendarWeekRangeLabel(day(2026, 12, 31)),
            "the year on the right is the end's, not the anchor's"
        )
    }

    // MARK: - the instant a save sends

    /// **The §2.3 fix, pinned.**
    ///
    /// Android sends `yyyy-MM-dd'T'HH:mm:ss` with no offset
    /// (`CalendarModels.kt:113`) into a `TIMESTAMPTZ` column with no session
    /// zone, so the same wall-clock time from two phones in two zones becomes
    /// the same stored instant — and neither is the one the person meant. This
    /// sends the instant, so the two differ by exactly their offsets, which is
    /// what the dashboard does (`calendar.jsx:36`).
    func testASaveSendsTheAbsoluteInstantAndNotAWallClockTime() {
        let chicagoAfternoon = Date(
            timeIntervalSince1970: Double(ms("2026-11-04T14:30:00-06:00")) / 1000
        )
        let tokyoAfternoon = Date(
            timeIntervalSince1970: Double(ms("2026-11-04T14:30:00+09:00")) / 1000
        )
        XCTAssertEqual("2026-11-04T20:30:00Z", calendarInstant(chicagoAfternoon))
        XCTAssertEqual("2026-11-04T05:30:00Z", calendarInstant(tokyoAfternoon))
        XCTAssertNotEqual(
            calendarInstant(chicagoAfternoon),
            calendarInstant(tokyoAfternoon),
            "Android sends 2026-11-04T14:30:00 for both, which is the defect in DECISIONS §2"
        )
    }

    /// Zero-padded in every field, and seconds always written — a `datetime`
    /// the server parses, not a shape that depends on the number's width.
    func testTheInstantIsZeroPaddedAndAlwaysCarriesSeconds() {
        let early = Date(timeIntervalSince1970: Double(ms("2026-01-05T03:04:05+00:00")) / 1000)
        XCTAssertEqual("2026-01-05T03:04:05Z", calendarInstant(early))
        XCTAssertEqual(20, calendarInstant(early).count)
    }

    // MARK: - the view words

    /// The toggle's words are the Kotlin's `CalView.label`s, **week among
    /// them** since the owner's answer 14. The order is the toggle's order.
    ///
    /// It used to assert that `week` was not a case at all (DECISIONS §3,
    /// which asked whether the week was wanted as a grouped list rather than
    /// as Android's grid). Answer 14: "build the week view (agenda rows under
    /// seven day headers)", so it is here — between `day` and `month`, where
    /// its span puts it.
    func testTheToggleOffersAgendaDayWeekMonthInThatOrder() {
        XCTAssertEqual(
            ["agenda", "day", "week", "month"],
            CalendarViewMode.allCases.map(\.label)
        )
        XCTAssertEqual(.week, CalendarViewMode(rawValue: "week"))
    }
}
