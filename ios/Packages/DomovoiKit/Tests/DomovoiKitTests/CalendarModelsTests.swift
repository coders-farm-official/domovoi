import Foundation
import XCTest
@testable import DomovoiKit

/// Calendar's wire shape, its civil date and its draft.
///
/// Android's JVM suite has **no test for any of this**: `CalendarModels.kt` is
/// 178 lines of date arithmetic, formatting and draft rules with no
/// `CalendarModelsTest.kt` next to it, so the week's first day, the `max(9,
/// now.hour)` draft, the duration-preserving start and the two validations are
/// specified here for the first time on either phone.
///
/// Every instant in this file is built from an ISO string through
/// ``isoMs(_:)``, which `HomeClockTests` already pins, rather than from a
/// hand-computed epoch number — so a wrong expectation here is a wrong
/// expectation about the rule and not about arithmetic.
final class CalendarModelsTests: XCTestCase {

    /// Kamron's zone, which is where the interesting cases are: the `-06:00`
    /// offset is what the §2.3 defect shifts an event by, and the
    /// fall-back night is 2026-11-01.
    private let chicago = TimeZone(identifier: "America/Chicago")!
    private let utc = TimeZone(secondsFromGMT: 0)!

    private func ms(_ iso: String) -> Int64 {
        guard let value = isoMs(iso) else {
            XCTFail("the fixture's own timestamp \(iso) did not parse")
            return 0
        }
        return value
    }

    private func date(_ iso: String) -> Date {
        Date(timeIntervalSince1970: Double(ms(iso)) / 1000)
    }

    private func day(_ year: Int, _ month: Int, _ dayOfMonth: Int) -> CalendarDay {
        CalendarDay(year: year, month: month, day: dayOfMonth)
    }

    private func decode(_ json: String) throws -> [CalendarEntry] {
        try JSONDecoder().decode([CalendarEntry].self, from: Data(json.utf8))
    }

    // MARK: - decoding the server's own shape

    /// A **bare JSON array** — `calendar.py:37` returns
    /// `list[CalendarEvent]`, not an envelope, which is the one thing a
    /// wrapper-versus-array mistake would silently turn into an empty screen.
    /// All nine keys, in the server's spellings (`schemas.py:826`).
    func testTheBareArrayDecodesEveryFieldTheServerSends() throws {
        let rows = try decode("""
        [{"id":4,"title":"book club","starts_at":"2026-11-07T19:00:00+00:00",
          "ends_at":"2026-11-07T21:00:00+00:00","location":"the back room",
          "description":"brought over from google","source":"google",
          "external_id":"abc123@google.com","last_synced_at":"2026-11-02T18:45:00+00:00"}]
        """)
        XCTAssertEqual(1, rows.count)
        guard let row = rows.first else { return }
        XCTAssertEqual(4, row.id)
        XCTAssertEqual("book club", row.title)
        XCTAssertEqual("2026-11-07T19:00:00+00:00", row.startsAt)
        XCTAssertEqual("2026-11-07T21:00:00+00:00", row.endsAt)
        XCTAssertEqual("the back room", row.location)
        XCTAssertEqual("brought over from google", row.description)
        XCTAssertEqual("google", row.source)
        XCTAssertEqual("abc123@google.com", row.externalID)
        XCTAssertEqual("2026-11-02T18:45:00+00:00", row.lastSyncedAt)
        XCTAssertTrue(row.isGoogle)
        XCTAssertEqual("google", row.sourceLabel)
    }

    /// Every optional null, which is what a hand-written local event looks
    /// like: four nulls and no source at all.
    func testARowOfNullsDecodesAndSaysLocal() throws {
        let rows = try decode("""
        [{"id":9,"title":"bin collection","starts_at":"2026-11-05T07:00:00+00:00",
          "ends_at":null,"location":null,"description":null,"source":null,
          "external_id":null,"last_synced_at":null}]
        """)
        guard let row = rows.first else { return XCTFail("no row") }
        XCTAssertNil(row.endsAt)
        XCTAssertNil(row.location)
        XCTAssertNil(row.description)
        XCTAssertFalse(row.isGoogle)
        XCTAssertEqual(
            CalendarStrings.localSource,
            row.sourceLabel,
            "a row with no source has to say something, and `local` is what the house would write"
        )
    }

    /// A key this app has never heard of — `all_day` is in the table and in no
    /// schema (`V001__baseline.sql:231`), so the day it reaches the API this
    /// decoder must not start failing. And a key the app *expects* missing
    /// entirely must not fail either: a strict decoder blanks the whole screen
    /// over one row.
    func testAnUnknownKeyAndAMissingKeyBothDecode() throws {
        let rows = try decode("""
        [{"id":1,"title":"dentist","starts_at":"2026-11-06T09:00:00+00:00","all_day":false},
         {"id":2,"starts_at":"2026-11-06T10:00:00+00:00"}]
        """)
        XCTAssertEqual(2, rows.count)
        XCTAssertEqual("dentist", rows.first?.title)
        XCTAssertEqual("", rows.last?.title, "a missing title is empty, not a thrown decode")
    }

    // MARK: - when an event is

    /// An unreadable `starts_at` sorts last and falls outside every window.
    ///
    /// **Android puts it at `LocalDateTime.now()`** (`CalendarModels.kt:34`),
    /// so over there an event the phone cannot read appears today at whatever
    /// time it happens to be. That is not copied, and this is the case that
    /// says so.
    func testAnUnreadableStartIsDroppedRatherThanDrawnAtNow() {
        let broken = CalendarEntry(id: 3, title: "who knows", startsAt: "not a time")
        XCTAssertEqual(Int64.max, calendarStartMs(broken))
        XCTAssertNil(CalendarDraft(broken), "and no editor opens on it")
    }

    /// An open-ended event **runs an hour**, exactly as Home's does, and the
    /// drawn end still falls back to the start.
    ///
    /// ### Why this case turned over
    ///
    /// It used to assert the opposite: `calendarEndMs` fell back to the
    /// **start**, which is Android's `endLocal()` (`CalendarModels.kt:36`) and
    /// what its agenda floor filters on, so an event with no `ends_at`
    /// dropped out of the agenda the instant it began while Home's card
    /// beside it kept the same row for an hour. Two rules for one event,
    /// raised as DECISIONS §8.
    ///
    /// **Kamron's answer 17 (2026-11-03): "yes: match Home, keep an
    /// open-ended event on screen for an hour."** So the divergence is gone
    /// and this case now pins the agreement — asserted against
    /// ``eventEndMs(_:)`` itself rather than against the literal hour, so the
    /// two cannot drift apart again without failing here.
    ///
    /// The second half is the split Home also keeps: the invented hour is for
    /// filtering, and ``calendarBlockEndMs(_:)`` is what a clock or a day-grid
    /// block uses (`TodayRow.endMs` is nil over there for the same reason).
    func testAnOpenEndedEventRunsAnHourLikeHomesAndIsStillDrawnWithNoEnd() {
        let startsAt = "2026-11-04T15:00:00+00:00"
        let open = CalendarEntry(id: 5, title: "all afternoon", startsAt: startsAt)
        let asHomeSeesIt = HomeEvent(id: 5, title: "all afternoon", startsAt: startsAt)
        XCTAssertEqual(
            eventEndMs(asHomeSeesIt),
            calendarEndMs(open),
            "answer 17: the calendar's window end is Home's, not Android's"
        )
        XCTAssertEqual(3_600_000, calendarEndMs(open) - calendarStartMs(open))

        XCTAssertEqual(
            calendarStartMs(open),
            calendarBlockEndMs(open),
            "the hour is for filtering; a drawn end is still the start, so no "
                + "row shows a range the house never gave"
        )
    }

    /// The `Int64.max` sentinel survives the invented hour.
    ///
    /// `Int64.max + 3_600_000` overflows, and in Swift that traps rather than
    /// wrapping — a crash on any house with one unparseable `starts_at`. Home
    /// guards it the same way and this is the case that says so on both.
    func testTheUnreadableSentinelIsNotGivenAnHour() {
        let broken = CalendarEntry(id: 7, title: "who knows", startsAt: "not a time")
        XCTAssertEqual(Int64.max, calendarEndMs(broken))
        XCTAssertEqual(Int64.max, calendarBlockEndMs(broken))
    }

    // MARK: - the civil date

    /// Civil arithmetic over a month end, and the clamp `LocalDate.plusMonths`
    /// has: a month after 31 january is 28 february, not 3 march.
    func testAddingDaysAndMonthsIsCivilAndClamps() {
        XCTAssertEqual(day(2026, 12, 1), day(2026, 11, 30).adding(days: 1))
        XCTAssertEqual(day(2026, 11, 30), day(2026, 12, 1).adding(days: -1))
        XCTAssertEqual(day(2026, 2, 28), day(2026, 1, 31).adding(months: 1))
        XCTAssertEqual(day(2027, 1, 15), day(2026, 12, 15).adding(months: 1))
        XCTAssertEqual(day(2026, 12, 13), day(2026, 11, 1).adding(days: 42))
    }

    /// The whole week, because an off-by-one in a Sunday-first index reads
    /// correctly and is wrong on one day in seven — the same trap
    /// ``homeDate(ms:timeZone:)`` walks a week for.
    func testWeekdayIndexIsSundayFirstForEveryDayOfOneWeek() {
        let sunday = day(2026, 11, 1)
        for offset in 0..<7 {
            XCTAssertEqual(
                offset,
                sunday.adding(days: offset).weekdayIndex,
                "\(sunday.adding(days: offset).iso) is not index \(offset)"
            )
        }
    }

    func testTheIsoSpellingIsZeroPaddedAndDatesCompareInOrder() {
        XCTAssertEqual("2026-01-05", day(2026, 1, 5).iso)
        XCTAssertTrue(day(2026, 1, 31) < day(2026, 2, 1))
        XCTAssertTrue(day(2025, 12, 31) < day(2026, 1, 1))
        XCTAssertFalse(day(2026, 3, 1) < day(2026, 3, 1))
    }

    /// **Midnight to midnight across the fall-back is twenty-five hours.**
    ///
    /// 2026-11-01 is the night the US puts its clocks back, so a `plusDays`
    /// written as `+ 86_400_000` would put the 2nd's grid row an hour into the
    /// 1st — which is the bug `dayStartMs(ms:timeZone:plusDays:)` already
    /// names, here for the calendar's own anchor.
    func testADayAcrossTheFallBackIsTwentyFiveHoursLong() {
        let first = day(2026, 11, 1).startMs(in: chicago)
        let second = day(2026, 11, 2).startMs(in: chicago)
        XCTAssertEqual(25 * 3_600_000, second - first)
        XCTAssertEqual(ms("2026-11-01T00:00:00-05:00"), first)
        XCTAssertEqual(ms("2026-11-02T00:00:00-06:00"), second)
    }

    func testAWallClockTimeOnADateIsThatDatesLocalInstant() {
        XCTAssertEqual(
            ms("2026-11-04T09:00:00-06:00"),
            day(2026, 11, 4).at(hour: 9, minute: 0, in: chicago)
        )
        XCTAssertEqual(
            ms("2026-11-04T09:00:00+00:00"),
            day(2026, 11, 4).at(hour: 9, minute: 0, in: utc)
        )
    }

    func testTheLocalDateOfAnInstantFollowsTheZone() {
        // 2026-11-05T02:00:00Z is still the 4th in Chicago.
        let late = ms("2026-11-05T02:00:00+00:00")
        XCTAssertEqual(day(2026, 11, 4), CalendarDay(ms: late, in: chicago))
        XCTAssertEqual(day(2026, 11, 5), CalendarDay(ms: late, in: utc))
    }

    // MARK: - the blank draft

    /// `blankDraft` (`CalendarModels.kt:160-165`), all three branches: nine in
    /// the morning, the current hour when the anchor is today and it is later
    /// than nine, and the 23 cap.
    func testABlankDraftStartsAtNineOrTheCurrentHourCappedAtEleven() {
        let today = day(2026, 11, 4)

        let morning = CalendarDraft.blank(
            anchor: today,
            nowMs: ms("2026-11-04T08:00:00-06:00"),
            timeZone: chicago
        )
        XCTAssertEqual("9:00am", calendarClock(ms: msOf(morning.start), timeZone: chicago))

        let afternoon = CalendarDraft.blank(
            anchor: today,
            nowMs: ms("2026-11-04T14:00:00-06:00"),
            timeZone: chicago
        )
        XCTAssertEqual("2:00pm", calendarClock(ms: msOf(afternoon.start), timeZone: chicago))

        let lateNight = CalendarDraft.blank(
            anchor: today,
            nowMs: ms("2026-11-04T23:40:00-06:00"),
            timeZone: chicago
        )
        XCTAssertEqual(
            "11:00pm",
            calendarClock(ms: msOf(lateNight.start), timeZone: chicago),
            "the cap is 23, so a draft opened at 23:40 does not start tomorrow"
        )

        let otherDay = CalendarDraft.blank(
            anchor: day(2026, 11, 9),
            nowMs: ms("2026-11-04T14:00:00-06:00"),
            timeZone: chicago
        )
        XCTAssertEqual(
            "9:00am",
            calendarClock(ms: msOf(otherDay.start), timeZone: chicago),
            "the current hour only applies to today's anchor"
        )
        XCTAssertEqual(day(2026, 11, 9), CalendarDay(ms: msOf(otherDay.start), in: chicago))
    }

    func testABlankDraftIsOneHourLongAndHasNoId() {
        let draft = CalendarDraft.blank(
            anchor: day(2026, 11, 4),
            nowMs: ms("2026-11-04T08:00:00-06:00"),
            timeZone: chicago
        )
        XCTAssertNil(draft.id, "no id is what chooses POST over PATCH")
        XCTAssertEqual(3600, draft.end.timeIntervalSince(draft.start))
        XCTAssertEqual("", draft.title)
    }

    // MARK: - a draft of an existing event

    /// `draftOf(e)` (`:167-178`) — including the hour it invents when
    /// `ends_at` is null, which is the one place this screen gives an
    /// open-ended event a length.
    func testADraftOfAnEventWithNoEndGetsAnHour() {
        let entry = CalendarEntry(
            id: 11,
            title: "call Mum",
            startsAt: "2026-11-04T23:15:00+00:00",
            location: "   ",
            description: nil
        )
        guard let draft = CalendarDraft(entry) else { return XCTFail("no draft") }
        XCTAssertEqual(11, draft.id)
        XCTAssertEqual("call Mum", draft.title)
        XCTAssertEqual(3600, draft.end.timeIntervalSince(draft.start))
        XCTAssertEqual("   ", draft.location, "the field is carried as typed; the trim is in `body`")
        XCTAssertEqual("", draft.description, "a null note is an empty field, not the word null")
    }

    func testADraftOfAnEventWithAnEndKeepsIt() {
        let entry = CalendarEntry(
            id: 12,
            title: "dentist",
            startsAt: "2026-11-06T09:00:00+00:00",
            endsAt: "2026-11-06T09:30:00+00:00"
        )
        guard let draft = CalendarDraft(entry) else { return XCTFail("no draft") }
        XCTAssertEqual(1800, draft.end.timeIntervalSince(draft.start))
    }

    // MARK: - moving the start

    /// `EventEditor.kt:224-231` — the end moves with the start, which is the
    /// rule a person notices immediately and no test over there covers.
    func testMovingTheStartKeepsTheDuration() {
        let draft = CalendarDraft(
            title: "dinner",
            start: date("2026-11-04T18:00:00-06:00"),
            end: date("2026-11-04T20:30:00-06:00")
        )
        let moved = draft.movingStart(to: date("2026-11-05T12:00:00-06:00"))
        XCTAssertEqual(draft.end.timeIntervalSince(draft.start),
                       moved.end.timeIntervalSince(moved.start))
        XCTAssertEqual(date("2026-11-05T14:30:00-06:00"), moved.end)
    }

    /// The Kotlin's `if (dur.isZero || dur.isNegative) Duration.ofHours(1)` —
    /// a draft whose end was dragged before its start does not stay inverted
    /// when the start moves, which would make the refusal unclearable.
    func testMovingTheStartOfAnInvertedDraftGivesItAnHour() {
        for end in ["2026-11-04T18:00:00-06:00", "2026-11-04T17:00:00-06:00"] {
            let inverted = CalendarDraft(
                title: "dinner",
                start: date("2026-11-04T18:00:00-06:00"),
                end: date(end)
            )
            let moved = inverted.movingStart(to: date("2026-11-05T09:00:00-06:00"))
            XCTAssertEqual(3600, moved.end.timeIntervalSince(moved.start), "end was \(end)")
            XCTAssertNil(moved.refusal, "and the draft is now saveable")
        }
    }

    // MARK: - the two refusals

    func testTheTwoValidationsAreTheKotlinsWordsInTheKotlinsOrder() {
        let start = date("2026-11-04T18:00:00-06:00")
        let blankTitle = CalendarDraft(
            title: "   \n ",
            start: start,
            end: start.addingTimeInterval(3600)
        )
        XCTAssertEqual(CalendarStrings.titleRequired, blankTitle.refusal)

        let inverted = CalendarDraft(title: "dinner", start: start, end: start.addingTimeInterval(-60))
        XCTAssertEqual(CalendarStrings.endAfterStart, inverted.refusal)

        let zero = CalendarDraft(title: "dinner", start: start, end: start)
        XCTAssertEqual(
            CalendarStrings.endAfterStart,
            zero.refusal,
            "`!end.isAfter(start)` refuses equal, not only earlier"
        )

        // Both wrong: the title's message wins, as the Kotlin's order does.
        let both = CalendarDraft(title: " ", start: start, end: start)
        XCTAssertEqual(CalendarStrings.titleRequired, both.refusal)

        XCTAssertNil(
            CalendarDraft(title: "dinner", start: start, end: start.addingTimeInterval(60)).refusal
        )
    }

    // MARK: - what goes on the wire

    /// The body, byte for byte: five keys always, the title trimmed, a blank
    /// optional sent as **null** so a PATCH clears the column
    /// (`calendar.py:146`'s `exclude_unset`), and the timestamps as absolute
    /// instants — the §2.3 fix, visible in the bytes.
    func testTheBodyIsFiveKeysWithNullsForTheBlankOnes() {
        let draft = CalendarDraft(
            title: "  dinner with mom  ",
            start: date("2026-11-04T18:00:00-06:00"),
            end: date("2026-11-04T19:00:00-06:00"),
            location: "   ",
            description: "  bring the bread  "
        )
        let expected = #"{"title":"dinner with mom","starts_at":"2026-11-05T00:00:00Z","#
            + #""ends_at":"2026-11-05T01:00:00Z","location":null,"description":"bring the bread"}"#
        XCTAssertEqual(expected, String(decoding: draft.body, as: UTF8.self))
    }

    /// A quote and a backslash in a person's own words, which `jsonLiteral`
    /// escapes — a title is typed text and one straight quote would otherwise
    /// make the body unparseable.
    func testAQuoteInATitleIsEscaped() {
        let draft = CalendarDraft(
            title: #"the "good" room \ upstairs"#,
            start: date("2026-11-04T18:00:00+00:00"),
            end: date("2026-11-04T19:00:00+00:00")
        )
        let text = String(decoding: draft.body, as: UTF8.self)
        XCTAssertTrue(text.contains(#"\"good\""#), text)
        XCTAssertNotNil(
            try? JSONSerialization.jsonObject(with: draft.body),
            "the body has to be parseable JSON: \(text)"
        )
    }

    private func msOf(_ date: Date) -> Int64 {
        Int64((date.timeIntervalSince1970 * 1000).rounded())
    }
}
