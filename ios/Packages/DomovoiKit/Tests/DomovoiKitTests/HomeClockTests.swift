import XCTest
@testable import DomovoiKit

/// Home's two clocks, the calendar window, and the two formatters the
/// attention card needs — §2.2, `HomeModels.kt:231-305` and
/// `ui/components/Format.kt`.
///
/// The zone is `America/Chicago` throughout, which is the zone
/// `HomeModelsTest.kt` uses, so a figure here can be compared to the Kotlin's
/// own assertion rather than to a second reading of the same code. It is also
/// a zone with daylight saving in it, which the week-long calendar window
/// needs.
final class HomeClockTests: XCTestCase {

    private let zone = TimeZone(identifier: "America/Chicago")!

    private func ms(_ iso: String) -> Int64 {
        guard let value = isoMs(iso) else {
            XCTFail("could not parse \(iso)")
            return 0
        }
        return value
    }

    // MARK: - the calendar window (`HomeModelsTest.calendarReadStartsAtLocalMidnightForAWeek`)

    /// The Kotlin's own assertion, character for character — including the
    /// `%3A`s, which is why the path is built by hand rather than by
    /// `addingPercentEncoding` (whose `urlQueryAllowed` set leaves `:` alone).
    func testTheCalendarReadStartsAtLocalMidnightForAWeek() {
        let day = dayStartMs(ms: ms("2026-09-28T19:00:00Z"), timeZone: zone)
        XCTAssertEqual(ms("2026-09-28T05:00:00Z"), day, "local midnight in Chicago, in September")
        XCTAssertEqual(
            "/api/calendar/events?start=2026-09-28T05%3A00%3A00Z&end=2026-10-05T05%3A00%3A00Z&limit=20",
            calendarPath(dayStartMs: day, timeZone: zone)
        )
    }

    /// The window is **a week** so that an empty today can still name the
    /// next event, and the start is midnight rather than now because the
    /// server filters on `starts_at` — an event already under way would drop
    /// out of a window that began now.
    func testTheWindowIsSevenCalendarDaysAndNotSevenTimesTwentyFourHours() {
        // 1 November 2026 is the Sunday the clocks go back in Chicago, so
        // the week from 29 October is 7 × 24 h **plus** an hour. A
        // `+ 7 * 86_400_000` would be an hour short and the last day's events
        // would be cut off.
        let day = dayStartMs(ms: ms("2026-10-29T12:00:00Z"), timeZone: zone)
        let end = dayStartMs(ms: day, timeZone: zone, plusDays: 7)
        XCTAssertEqual(ms("2026-11-05T06:00:00Z"), end, "a week later, in standard time")
        XCTAssertEqual(
            7 * 86_400_000 + 3_600_000, end - day,
            "which is an hour more than seven days: the clocks went back inside it"
        )
    }

    /// …and the other direction, which was untested: the week the clocks go
    /// **forward** is an hour short of seven times twenty-four.
    ///
    /// 8 March 2026 is the second Sunday in March, so the week from Thursday
    /// 5 March crosses it. A `+ 7 * 86_400_000` would be an hour long here and
    /// an hour short in the autumn — the same bug reading correct in one
    /// direction, which is why one direction is not coverage.
    func testTheWindowLosesAnHourWhenTheClocksGoForward() {
        let day = dayStartMs(ms: ms("2026-03-05T12:00:00Z"), timeZone: zone)
        let end = dayStartMs(ms: day, timeZone: zone, plusDays: 7)
        XCTAssertEqual(
            7 * 86_400_000 - 3_600_000, end - day,
            "an hour less than seven days: the clocks went forward inside it"
        )
        XCTAssertEqual(ms("2026-03-12T05:00:00Z"), end, "a week later, in daylight time")
    }

    /// **A zone whose DST transition is at midnight, so local midnight does
    /// not exist.**
    ///
    /// Chile's clocks go forward at 24:00 on the first Saturday in September,
    /// which is 00:00 on Sunday 6 September 2026 jumping to 01:00. There is no
    /// 2026-09-06 00:00 in `America/Santiago`. Java's
    /// `toLocalDate().plusDays(7).atStartOfDay(zone)` answers **01:00 local**
    /// — `atStartOfDay` is documented to return the first valid instant of the
    /// day — and `Calendar.startOfDay(for:)` is the Foundation call this port
    /// uses in its place.
    ///
    /// Both ways in must land on the same instant: a week counted from the
    /// Sunday before, and the day itself. 01:00 at UTC-03 is 04:00 Z.
    ///
    /// This is the case the review could not execute. It is pinned rather than
    /// reasoned about because the calendar read's **key** is this number: a
    /// window that starts an hour early on one day a year in one country is
    /// the kind of thing nobody ever traces back.
    func testAZoneWhoseMidnightDoesNotExistStartsTheDayAtTheFirstValidInstant() {
        let santiago = TimeZone(identifier: "America/Santiago")!
        let expected = ms("2026-09-06T04:00:00Z")

        let sundayBefore = ms("2026-08-30T16:00:00Z")  // 2026-08-30 noon, UTC-04
        XCTAssertEqual(
            expected,
            dayStartMs(ms: sundayBefore, timeZone: santiago, plusDays: 7),
            "a week later is the first instant of 6 September, which is 01:00 local"
        )
        let onTheDay = ms("2026-09-06T15:00:00Z")  // 2026-09-06 noon, UTC-03
        XCTAssertEqual(
            expected,
            dayStartMs(ms: onTheDay, timeZone: santiago),
            "and the day itself starts at the same instant, so the window does not shift"
        )
        // The transition itself, so a wrong answer cannot be right by
        // coincidence: the day before starts at local 00:00 under UTC-04.
        XCTAssertEqual(
            ms("2026-09-05T04:00:00Z"),
            dayStartMs(ms: ms("2026-09-05T16:00:00Z"), timeZone: santiago),
            "5 September has a midnight, and it is 04:00 Z"
        )
    }

    /// Midnight is midnight **in the given zone**, not in the device's.
    func testLocalMidnightIsTheGivenZonesMidnight() {
        let noonUTC = ms("2026-06-15T12:00:00Z")
        XCTAssertEqual(ms("2026-06-15T05:00:00Z"), dayStartMs(ms: noonUTC, timeZone: zone))
        XCTAssertEqual(
            ms("2026-06-15T00:00:00Z"),
            dayStartMs(ms: noonUTC, timeZone: TimeZone(secondsFromGMT: 0)!)
        )
        // …and an instant that is already a different day in one zone and not
        // in the other.
        let lateNight = ms("2026-06-16T02:00:00Z")
        XCTAssertEqual(ms("2026-06-15T05:00:00Z"), dayStartMs(ms: lateNight, timeZone: zone))
        XCTAssertEqual(
            ms("2026-06-16T00:00:00Z"),
            dayStartMs(ms: lateNight, timeZone: TimeZone(secondsFromGMT: 0)!),
            "the day had already turned in UTC"
        )
    }

    // MARK: - the two clocks

    /// `nowMs = maxOf(tick, now)`: a redraw for any other reason still sees a
    /// current clock, so "a fresh read never renders against a stale second".
    func testTheScreenClockNeverGoesBackwards() {
        XCTAssertEqual(200, HomeClock.nowMs(tick: 100, wall: 200), "the wall clock has moved on")
        XCTAssertEqual(300, HomeClock.nowMs(tick: 300, wall: 200), "and the tick wins when it is ahead")
        XCTAssertEqual(100, HomeClock.nowMs(tick: 100, wall: 100))
    }

    /// 1 s while something counts, 30 s otherwise (`HomeScreen.kt:266`).
    func testTheTickIsASecondOnlyWhileSomethingIsCounting() {
        XCTAssertEqual(1_000, HomeClock.tickPeriodMs(counting: true))
        XCTAssertEqual(30_000, HomeClock.tickPeriodMs(counting: false))
    }

    /// The **server's** clock minus this phone's: a phone minutes out of sync
    /// still counts down to the second.
    func testTheServerOffsetIsTheServersClockMinusThisOne() {
        let received = ms("2026-10-29T12:00:00Z")
        XCTAssertEqual(
            90_000,
            serverOffsetMs(serverNow: "2026-10-29T12:01:30Z", receivedAtMs: received),
            "the server is a minute and a half ahead of this phone"
        )
        XCTAssertEqual(
            -5_000,
            serverOffsetMs(serverNow: "2026-10-29T11:59:55Z", receivedAtMs: received),
            "and it can be behind"
        )
        XCTAssertEqual(
            0, serverOffsetMs(serverNow: nil, receivedAtMs: received),
            "a server that did not say means count against this phone, not against nothing"
        )
        XCTAssertEqual(
            0, serverOffsetMs(serverNow: "not a time", receivedAtMs: received),
            "and so does one that said something unreadable"
        )
    }

    // MARK: - parsing what the server sends

    /// Three shapes, because the web backend sends all three and a strict ISO
    /// parser returns nil for the third — which reads downstream as "no time"
    /// rather than as "a time I could not read".
    func testEveryShapeTheServerSendsIsParsed() {
        XCTAssertEqual(ms("2026-10-29T12:00:00Z"), isoMs("2026-10-29T12:00:00Z"))
        XCTAssertEqual(ms("2026-10-29T12:00:00Z"), isoMs("2026-10-29T07:00:00-05:00"), "an offset")
        XCTAssertEqual(
            ms("2026-10-29T12:00:00Z") + 250,
            isoMs("2026-10-29T12:00:00.250Z"),
            "fractional seconds"
        )
        // No zone at all: read in the device's zone, which is what
        // `LocalDateTime.parse(iso).atZone(ZoneId.systemDefault())` does. The
        // value therefore depends on where the phone is, so the assertion is
        // that it parsed rather than what it came to.
        XCTAssertNotNil(isoMs("2026-10-29T12:00:00"), "a timestamp with no zone still parses")

        XCTAssertNil(isoMs(nil))
        XCTAssertNil(isoMs(""))
        XCTAssertNil(isoMs("   "))
        XCTAssertNil(isoMs("tomorrow"))
    }

    // MARK: - `relTime`, which an attention row draws

    func testRelTimeSaysHowLongAgoInTheWordsTheWebUses() {
        let now = ms("2026-10-29T12:00:00Z")
        func ago(_ seconds: Int64) -> String {
            relTime(isoString(now - seconds * 1000), nowMs: now)
        }
        XCTAssertEqual("just now", ago(0))
        XCTAssertEqual("just now", ago(44), "the bound is 45 s")
        XCTAssertEqual("1m ago", ago(45), "and 45 s rounds to a minute, not to nothing")
        XCTAssertEqual("5m ago", ago(300))
        XCTAssertEqual("60m ago", ago(3_599), "minutes right up to the hour")
        XCTAssertEqual("1h ago", ago(3_600))
        XCTAssertEqual("2h ago", ago(7_200))
        XCTAssertEqual("24h ago", ago(86_399))
        XCTAssertEqual("1d ago", ago(86_400))
        XCTAssertEqual("3d ago", ago(86_400 * 3))
        // Past thirty days it becomes a date, in the JVM's `MMM d, yyyy`.
        XCTAssertEqual("Sep 29, 2026", relTime("2026-09-29T12:00:00Z", nowMs: now))
        XCTAssertEqual("—", relTime(nil, nowMs: now), "an em dash, U+2014, not a hyphen")
        XCTAssertEqual("\u{2014}", relTime(nil, nowMs: now))
    }

    /// A time in the future reads `in 5m`, which a server a few seconds ahead
    /// of this phone really does produce.
    func testRelTimeHandlesAServerThatIsAheadOfThisPhone() {
        let now = ms("2026-10-29T12:00:00Z")
        XCTAssertEqual("in 30s", relTime(isoString(now + 30_000), nowMs: now))
        XCTAssertEqual("in 5m", relTime(isoString(now + 300_000), nowMs: now))
        XCTAssertEqual("in 2h", relTime(isoString(now + 7_200_000), nowMs: now))
        XCTAssertEqual("in 3d", relTime(isoString(now + 86_400_000 * 3), nowMs: now))
    }

    /// `relTime` reads its clock from a parameter rather than from
    /// `Date()`, which is the whole reason the boundaries above can be
    /// asserted at all: Android's reads `Instant.now()` inside itself, so its
    /// 45-second bound is untestable without waiting for it.
    func testTheClockIsAParameterAndNotTheWallClock() {
        let then = "2026-01-01T00:00:00Z"
        XCTAssertEqual("just now", relTime(then, nowMs: ms(then)))
        XCTAssertEqual("1h ago", relTime(then, nowMs: ms(then) + 3_600_000))
    }

    // MARK: - what makes the clock tick every second (3b's addition)

    /// ``homeCounting(timers:rooms:)`` — all three terms, each on its own.
    ///
    /// The `done` term is the one worth a test: a `done · kitchen` line
    /// lingers for ``homeDoneMs`` and nothing else on the screen may be
    /// moving, so a rule that only looked at the active timers and the playing
    /// rooms would leave the clock at 30 s and the line would vanish up to
    /// thirty seconds late.
    func testTheClockCountsForATimerADoneLineOrAPlayingRoom() {
        let quiet = HomeRoom(roomID: "kitchen", status: "online")
        let playing = HomeRoom(
            roomID: "office",
            status: "online",
            nowPlaying: HomeNowPlaying(state: "play", song: HomeSong(title: "a"))
        )
        let running = HomeTimer(id: 1, expiresAt: "2026-10-29T12:10:00Z")
        let done = DoneTimer(timer: running, doneAtMs: 0)

        XCTAssertFalse(homeCounting(timers: TimerView(), rooms: []))
        XCTAssertFalse(
            homeCounting(timers: TimerView(), rooms: [quiet]),
            "a quiet house does not need a one-second clock"
        )
        XCTAssertTrue(homeCounting(timers: TimerView(active: [running]), rooms: []))
        XCTAssertTrue(
            homeCounting(timers: TimerView(done: [done]), rooms: []),
            "a done line is counting too: it has sixty seconds to live"
        )
        XCTAssertTrue(homeCounting(timers: TimerView(), rooms: [quiet, playing]))

        // And the one that reads through `roomRank`: a *stopped* room whose
        // player still names a track is not playing, so it does not make the
        // screen tick every second for nothing.
        let stopped = HomeRoom(
            roomID: "hall",
            status: "online",
            nowPlaying: HomeNowPlaying(state: "stop", song: HomeSong(title: "a"))
        )
        XCTAssertFalse(homeCounting(timers: TimerView(), rooms: [stopped]))

        XCTAssertEqual(1_000, HomeClock.tickPeriodMs(counting: true))
        XCTAssertEqual(30_000, HomeClock.tickPeriodMs(counting: false))
    }

    /// ``homeMoreLabel(extra:)`` — the one `+N more` both capped cards use.
    ///
    /// 3a gave the attention card its own copy and the timers card none, so
    /// the timers header had no way to say these words without a literal in a
    /// view. The tail `" more"` is pinned against the Kotlin by
    /// `ShellStringsTests.testTheMoreLabelIsTheKotlinsMoreLabel`; this is the
    /// composition.
    func testTheMoreLabelIsBuiltOnceForBothCards() {
        XCTAssertNil(homeMoreLabel(extra: 0), "nothing hidden, no link")
        XCTAssertNil(homeMoreLabel(extra: -2), "and a cap larger than the list is not a link either")
        XCTAssertEqual("+1 more", homeMoreLabel(extra: 1))
        XCTAssertEqual("+12 more", homeMoreLabel(extra: 12))
        // The attention card's own label goes through it, so the two cards
        // cannot drift apart.
        let five = HomeAttentionState(
            view: .rows((0..<5).map {
                AttentionRow(key: "k\($0)", tone: .warn, text: "x", target: .settings("health"))
            }),
            compact: true
        )
        XCTAssertEqual(homeMoreLabel(extra: 5 - homePhoneRows), five.moreLabel)
    }

    private func isoString(_ epochMs: Int64) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        return formatter.string(from: Date(timeIntervalSince1970: Double(epochMs) / 1000))
    }
}
