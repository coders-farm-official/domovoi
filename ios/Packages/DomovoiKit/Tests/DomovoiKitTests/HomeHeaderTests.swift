import XCTest
@testable import DomovoiKit

/// Home's header: the name, the date, the three explanations and the pair
/// link. The words themselves are checked against the Kotlin by
/// `ShellStringsTests.testTheHomeHeaderSaysWhatTheKotlinSays`; this is what
/// chooses between them.
final class HomeHeaderTests: XCTestCase {

    // MARK: - the name

    func testABlankBotNameIsNotAName() {
        XCTAssertEqual("domovoi", HomeConfig.name(of: nil))
        XCTAssertEqual("domovoi", HomeConfig.name(of: HomeConfig()))
        XCTAssertEqual("domovoi", HomeConfig.name(of: HomeConfig(botName: "")))
        XCTAssertEqual("domovoi", HomeConfig.name(of: HomeConfig(botName: "   ")))
        XCTAssertEqual("the burrow", HomeConfig.name(of: HomeConfig(botName: "the burrow")))
    }

    func testTheConfigDecodesTheTwoFieldsHomeReads() throws {
        let json = #"{"bot_name":"the burrow","home_problems_visibility":"admins","tts_voice":"x"}"#
        let config = try JSONDecoder().decode(HomeConfig.self, from: Data(json.utf8))
        XCTAssertEqual("the burrow", config.botName)
        XCTAssertEqual("admins", config.homeProblemsVisibility)
        // A field missing is nil, not a decode failure: `/api/config` is
        // shared with the dashboard and grows.
        let sparse = try JSONDecoder().decode(HomeConfig.self, from: Data("{}".utf8))
        XCTAssertNil(sparse.botName)
    }

    /// **`GET /api/config` is an Open route, so its 200 must not clear a
    /// pairing screen.**
    ///
    /// `docs/API_REFERENCE.md:1107` lists its tier as Open and
    /// `web/backend/api/config.py:71` takes no dependency, so the answer says
    /// nothing about whether this phone is paired. Android reads it tracked
    /// (`HomeIo.kt:29`) and a tracked 200 clears `pairingRequired`
    /// (`ApiClient.kt:334`) — the same defect `d1fd984c` fixed for
    /// `/api/capabilities` the day before. Reported, not fixed there; this is
    /// the iOS side of it, and this test is what stops the word being
    /// "corrected" back.
    func testTheConfigReadCannotClearThePairingScreen() async throws {
        StubURLProtocol.reset()
        defer { StubURLProtocol.reset() }
        StubURLProtocol.dispatch { _ in .json(#"{"bot_name":"the burrow"}"#) }
        let address = "http://10.0.0.42:6369"
        let client = DomovoiClient(
            book: ServerBook(activeServer: { address }, tokenForServer: { _ in nil }),
            gate: AdmitsAnyServer(),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session(timeout: 1)
        )
        client.notePossiblePairingRefusal(status: 401, body: "X-Device-Token or admin session required")
        XCTAssertTrue(client.pairingRequired, "a household read was refused")

        let config = try await HomeApi.config(client)
        XCTAssertEqual("the burrow", config.botName)
        XCTAssertTrue(client.pairingRequired, "an Open route's 200 is not evidence of pairing")
    }

    // MARK: - the date

    /// `mon 28 sep`: lower case, no padding, and the weekday table is
    /// **Monday-first** where Foundation's `.weekday` is Sunday-first. A
    /// whole week, because an off-by-one here reads correctly six days out of
    /// seven.
    func testTheDateNamesEveryWeekdayCorrectly() {
        // 2026-09-28 is a Monday. 12:00 UTC, so no zone in use moves the day.
        var day = DateComponents()
        day.year = 2026
        day.month = 9
        day.day = 28
        day.hour = 12
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let monday = calendar.date(from: day)!
        let expected = [
            "mon 28 sep", "tue 29 sep", "wed 30 sep",
            "thu 1 oct", "fri 2 oct", "sat 3 oct", "sun 4 oct",
        ]
        for (offset, want) in expected.enumerated() {
            let ms = Int64((monday.timeIntervalSince1970 + Double(offset) * 86_400) * 1000)
            XCTAssertEqual(want, homeDate(ms: ms, timeZone: TimeZone(identifier: "UTC")!))
        }
    }

    /// The zone decides which day it is, and the phone's clock is what the
    /// date is read from (the *server's* clock drives the timers, which are
    /// part 2's).
    func testTheDateIsReadInTheGivenZone() {
        // 2026-01-01 00:30 UTC is still 2025-12-31 in Los Angeles.
        let ms: Int64 = 1_767_227_400_000
        XCTAssertEqual("thu 1 jan", homeDate(ms: ms, timeZone: TimeZone(identifier: "UTC")!))
        XCTAssertEqual("wed 31 dec", homeDate(ms: ms, timeZone: TimeZone(identifier: "America/Los_Angeles")!))
    }

    func testTheTablesAreTheKotlinTables() {
        XCTAssertEqual(["mon", "tue", "wed", "thu", "fri", "sat", "sun"], homeWeekdays)
        XCTAssertEqual(12, homeMonths.count)
        XCTAssertEqual("jan", homeMonths.first)
        XCTAssertEqual("dec", homeMonths.last)
    }

    // MARK: - the dot, its sentence and the link

    /// **The branch order is the Kotlin's**: `live` wins, then `!paired`,
    /// then "the live connection is down" (`HomeSections.kt:169-173`). The
    /// first row is the one that order decides: an unpaired phone whose
    /// socket is somehow up reads the live sentence, because the sentence is
    /// about what is happening and not about why it might not be.
    func testTheExplanationFollowsTheKotlinBranchOrder() {
        func sentence(live: Bool, paired: Bool) -> String {
            HomeHeaderState(name: "domovoi", date: "mon 28 sep", live: live, paired: paired).explanation
        }
        XCTAssertEqual(HomeHeaderStrings.explainLive, sentence(live: true, paired: true))
        XCTAssertEqual(HomeHeaderStrings.explainLive, sentence(live: true, paired: false))
        XCTAssertEqual(HomeHeaderStrings.explainUnpaired, sentence(live: false, paired: false))
        XCTAssertEqual(HomeHeaderStrings.explainDown, sentence(live: false, paired: true))
    }

    func testTheLabelAndTheLinkFollowLiveAndPaired() {
        let live = HomeHeaderState(name: "d", date: "mon 28 sep", live: true, paired: true)
        XCTAssertEqual("live", live.liveLabel)
        XCTAssertFalse(live.showsPairLink)

        let unpaired = HomeHeaderState(name: "d", date: "mon 28 sep", live: false, paired: false)
        XCTAssertEqual(HomeHeaderStrings.notLive, unpaired.liveLabel)
        XCTAssertTrue(unpaired.showsPairLink, "the link is shown whenever there is no token")
    }

    /// The line VoiceOver reads: the date, then each count behind the
    /// separator. With no counts it is the date alone, which is the state
    /// this batch ships in — the rooms and timer reads are part 2's.
    func testTheSpokenLineJoinsTheCountsWithTheSeparator() {
        let bare = HomeHeaderState(name: "d", date: "mon 28 sep", live: true, paired: true)
        XCTAssertEqual("mon 28 sep", bare.spokenLine)

        let full = HomeHeaderState(
            name: "d",
            date: "mon 28 sep",
            counts: ["3 online", "1 timer"],
            countsReady: true,
            live: true,
            paired: true
        )
        XCTAssertEqual("mon 28 sep  ·  3 online  ·  1 timer", full.spokenLine)
    }

    /// **A skeleton is a promise, so it is drawn only when a read is
    /// pending.** The old version of this test read `countsReady` back out of
    /// the initialiser it had just been passed to, which could only fail if
    /// it stopped being a stored property; nothing about the skeleton was
    /// tested at all. What decides is `showsCountsSkeleton`, and the state
    /// that mattered — a screen with no rooms read — is the one this batch
    /// shipped drawing a skeleton for ever.
    func testTheSkeletonIsDrawnForAPendingReadAndNotForAnAbsentOne() {
        let pending = HomeHeaderState(name: "d", date: "mon 28 sep", countsReady: false, live: false, paired: true)
        XCTAssertTrue(pending.showsCountsSkeleton, "the rooms read has not answered: a grey box, not a spinner")
        XCTAssertEqual("mon 28 sep", pending.spokenLine, "and nothing to say beside the date yet")

        // What Home passes at this commit: there is no rooms read, so
        // nothing is pending and the date stands alone.
        let nothingToWaitFor = HomeHeaderState(
            name: "d", date: "mon 28 sep", counts: [], countsReady: true, live: false, paired: true
        )
        XCTAssertFalse(
            nothingToWaitFor.showsCountsSkeleton,
            "no read to wait on, so no promise that one is coming"
        )
        XCTAssertEqual("mon 28 sep", nothingToWaitFor.spokenLine)

        // And when a read has answered with something, the counts are drawn
        // rather than the box.
        let answered = HomeHeaderState(
            name: "d", date: "mon 28 sep", counts: ["3 online"], countsReady: true, live: false, paired: true
        )
        XCTAssertFalse(answered.showsCountsSkeleton)
        XCTAssertEqual("mon 28 sep  ·  3 online", answered.spokenLine)
    }
}
