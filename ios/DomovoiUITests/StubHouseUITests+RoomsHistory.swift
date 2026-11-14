import XCTest

/// A room's history, answered or not — server V043/V044 (2026-11-09), driven
/// against the stub house.
///
/// ### Where the turns come from
///
/// The **study**'s conversations, which this batch's history patch to the
/// house serves (`design-notes/ios/batch-2026-11-09/history/stubhouse.patch`):
/// one turn in each state the server writes, newest first —
///
/// | id  | status / reason          | what it must draw                              |
/// |-----|--------------------------|------------------------------------------------|
/// | 301 | pending → answered       | `waiting for an answer…`, then the answer      |
/// | 302 | no_answer / overheard    | `ignored` pill, `ignored — sounded like …`     |
/// | 303 | no_answer / timed_out    | `no answer — timed out`                        |
/// | 304 | unheard / error, 3400 ms | `couldn’t make out … · 3.4 s recording`        |
/// | 305 | no_answer / `gremlins`   | a plain `no answer`                            |
/// | 306 | (no status: pre-V043)    | its answer, as before                          |
///
/// The kitchen's four turns — which `testRooms_SessionsTabExpandsOneSessionsOwnTurns`
/// and `testRooms_ConversationsSearchFiltersByWord` count — are untouched:
/// the patch answers the study's one path ahead of `fixtures/rooms.py` and
/// nothing else.
///
/// Turn 301 answers on the house's **fourth** read of that list. The tab's own
/// read is the first; the app reads it again every three seconds while a turn
/// is pending, so the answer fills in about nine seconds after the tab opens
/// — with no tap. A case that needs it to stay pending arms
/// `history-pending` with a large count after ``roomsOpen(mode:pushDelay:roster:micSeam:)``
/// (which resets the house first).
///
/// Every line and pill is found by **its turn's identifier**
/// (`rooms.turn.<id>.said` / `.reply` / `.pill`), not by its words: `no answer`
/// is three pills' label and one reply's, and a query by words would pass on
/// the wrong row.
extension StubHouseUITests {

    /// The study open on its conversations tab, with every turn drawn.
    @MainActor
    func historyOpen(holdPending: Bool, pushDelay: Double = 3) -> XCUIApplication? {
        let app = roomsOpen(pushDelay: pushDelay)
        if holdPending {
            control(
                "history-pending",
                "{\"reads\": 100000}",
                port: 6601,
                what: "the study's pending turn held pending"
            )
        }
        guard roomsOpenRoom(app, "study") else { return nil }
        guard roomsTab(app, RoomsHistoryUnderTest.tabConversations) else { return nil }
        // The oldest row is the last one drawn, so its answer being on screen
        // says the whole list landed.
        guard historyWait(app, 306, .reply, RoomsHistoryUnderTest.lampAnswer) else { return nil }
        return app
    }

    enum HistoryLine: String {
        case said
        case reply
        case pill
    }

    @MainActor
    func historyLine(_ app: XCUIApplication, _ turn: Int, _ line: HistoryLine) -> XCUIElement {
        app.staticTexts["rooms.turn.\(turn).\(line.rawValue)"]
    }

    /// Wait for one turn's line to read exactly `want`, and print the tree
    /// when it never does.
    @MainActor
    func historyWait(
        _ app: XCUIApplication,
        _ turn: Int,
        _ line: HistoryLine,
        _ want: String,
        timeout: Double = 20
    ) -> Bool {
        let element = historyLine(app, turn, line)
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if element.exists && element.label == want { return true }
            usleep(150_000)
        }
        let had = element.exists ? "“\(element.label)”" : "nothing"
        print("=== TURN \(turn) \(line.rawValue) NEVER READ “\(want)” (it read \(had)) — tree follows ===")
        print(app.debugDescription)
        return false
    }

    /// How many times the house has served the study's conversations since
    /// the last reset — the poll's footprint, read from the house's side.
    @MainActor
    func historyReads() -> Int {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:6601/api/_test/history-pending")!)
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        let answered = expectation(description: "the stub said how often the history was read")
        var reads = -1
        URLSession.shared.dataTask(with: request) { data, _, _ in
            if let data,
               let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let count = body["reads"] as? Int {
                reads = count
            }
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 15)
        return reads
    }

    // MARK: - the cases

    /// Each state draws Android's words (`satTurnUnanswered` / `satTurnSaid`,
    /// `SatModels.kt:176-200`) and pill (`SatHistoryTabs.kt:170-175`) — and a
    /// row from before V043 still draws its answer.
    @MainActor
    func testRoomsHistory_EachTurnStateSaysWhatHappened() throws {
        guard let app = historyOpen(holdPending: true) else {
            XCTFail("the study's conversations never drew")
            return
        }
        evidence(app, "rooms-history-01-states")

        XCTAssertTrue(
            historyWait(app, 301, .reply, RoomsHistoryUnderTest.waiting),
            "a pending turn does not say it is waiting for its answer"
        )
        XCTAssertTrue(
            historyWait(app, 301, .said, RoomsHistoryUnderTest.pendingSaid),
            "a pending turn does not show what was said"
        )
        XCTAssertTrue(
            historyWait(app, 301, .pill, RoomsHistoryUnderTest.pillWaiting),
            "the pending turn's pill is not `waiting`"
        )

        XCTAssertTrue(
            historyWait(app, 302, .reply, RoomsHistoryUnderTest.ignored),
            "an overheard turn does not say it was ignored as background talk"
        )
        XCTAssertTrue(
            historyWait(app, 302, .pill, RoomsHistoryUnderTest.pillIgnored),
            "the overheard turn's pill is not `ignored` — a `no answer` here is "
                + "background talk drawn as a failure"
        )

        XCTAssertTrue(
            historyWait(app, 303, .reply, RoomsHistoryUnderTest.timedOut),
            "a timed-out turn does not say so"
        )
        XCTAssertTrue(
            historyWait(app, 303, .pill, RoomsHistoryUnderTest.pillNoAnswer),
            "the timed-out turn's pill is not `no answer`"
        )

        XCTAssertTrue(
            historyWait(app, 304, .said, RoomsHistoryUnderTest.unheardSaid),
            "an unheard turn does not say it could not be made out, with its recording's length"
        )
        XCTAssertTrue(
            historyWait(app, 304, .reply, RoomsHistoryUnderTest.somethingWentWrong),
            "an unheard turn does not say why there was no answer"
        )
        XCTAssertTrue(
            historyWait(app, 304, .pill, RoomsHistoryUnderTest.pillNoAnswer),
            "the unheard turn's pill is not `no answer`"
        )

        XCTAssertTrue(
            historyWait(app, 305, .reply, RoomsHistoryUnderTest.noAnswer),
            "a reason no app knows is not a plain `no answer`"
        )
        XCTAssertTrue(
            historyWait(app, 305, .said, RoomsHistoryUnderTest.unknownSaid),
            "the unknown-reason turn lost what was said"
        )
        XCTAssertTrue(
            historyWait(app, 305, .pill, RoomsHistoryUnderTest.pillNoAnswer),
            "the unknown-reason turn's pill is not `no answer`"
        )

        XCTAssertTrue(
            historyWait(app, 306, .reply, RoomsHistoryUnderTest.lampAnswer),
            "a row from before V043 no longer draws its answer"
        )
        XCTAssertTrue(
            historyWait(app, 306, .pill, RoomsHistoryUnderTest.pillLights),
            "a row from before V043 lost its handler pill"
        )
        XCTAssertTrue(
            roomsWaitFor(app, RoomsHistoryUnderTest.sixOfSix),
            "the count line does not say six of six"
        )
    }

    /// The pending turn fills in **by itself**: the app reads the list again
    /// every three seconds while a turn is waiting (`RefreshWhilePending`,
    /// `SatHistoryTabs.kt:227-243`), and the house answers it on its fourth
    /// read. **The evidence is the answer appearing with nothing touched**:
    /// this case makes no tap, pull or tab switch after the list first draws,
    /// and the house sends no push, so a re-read the app made on its own is
    /// the only way the words can change.
    @MainActor
    func testRoomsHistory_APendingTurnFillsInItsAnswer() throws {
        guard let app = historyOpen(holdPending: false) else {
            XCTFail("the study's conversations never drew")
            return
        }
        // The first look may already be late — the house answers on its
        // fourth read, about nine seconds in — so "waiting" is printed, not
        // asserted. The assertion is the answer arriving untouched.
        let first = historyLine(app, 301, .reply)
        let sawWaiting = first.exists && first.label == RoomsHistoryUnderTest.waiting
        print("ROOMS HISTORY pending: waiting drawn at first look = \(sawWaiting)")
        evidence(app, "rooms-history-02-pending")

        XCTAssertTrue(
            historyWait(app, 301, .reply, RoomsHistoryUnderTest.lisbonAnswer, timeout: 40),
            "the pending turn never filled in its answer while the tab was open"
        )
        XCTAssertTrue(
            historyWait(app, 301, .pill, RoomsHistoryUnderTest.pillTime),
            "the answered turn did not take its handler's pill"
        )
        XCTAssertFalse(
            app.staticTexts[RoomsHistoryUnderTest.waiting].exists,
            "`waiting for an answer…` is still drawn after the answer came"
        )
        evidence(app, "rooms-history-03-answered")
        // The house's count, for the stop check below and for the log.
        let reads = historyReads()
        print("ROOMS HISTORY pending: the house served the study's list \(reads) times")

        // And it stops: nothing is pending, so nine more seconds read nothing.
        sleep(9)
        XCTAssertEqual(
            reads,
            historyReads(),
            "the app kept reading the history after the last pending turn was answered"
        )
    }

    /// Apple's audit, on the conversations tab with every state drawn — the
    /// pending turn **held** pending, so the page cannot change under it.
    ///
    /// Judged the way ``testRooms_Audit`` judges an opened room: the measured
    /// floor of anonymous findings on a pushed screen, and contrast findings
    /// against elements with no pixels on the window set aside by
    /// ``roomsContrastWithNoPixels(_:on:after:)`` (printed, never dropped).
    /// Everything else is a defect.
    @MainActor
    func testRoomsHistory_Audit() throws {
        guard let app = historyOpen(holdPending: true, pushDelay: Self.pushDelay) else {
            XCTFail("the study's conversations never drew, so there is nothing to audit")
            return
        }
        XCTAssertTrue(
            historyWait(app, 301, .reply, RoomsHistoryUnderTest.waiting),
            "the held turn is not pending, so the audit would not see the waiting state"
        )
        evidence(app, "rooms-history-04-audit")
        let shot = roomsWindowShot(app)
        let found = try auditIssues(app, "rooms-history")
        let split = Self.floorAndRest(found)
        let seen = roomsContrastWithNoPixels(split.rest, on: shot, after: app)
        let rest = roomsOursOnly(seen.measured)
        print(
            "ROOMS HISTORY AUDIT floor=\(split.floor) rest=\(split.rest.count) "
                + "no-pixels=\(seen.noPixels.count) ours=\(rest.count)"
        )
        XCTAssertTrue(
            rest.isEmpty,
            "Apple's audit has findings against the history tab beyond the measured floor: "
                + rest.joined(separator: " / ")
        )
        XCTAssertEqual(
            split.floor,
            Self.knownPushedBarFloor,
            "the history tab's anonymous floor is \(split.floor), not the known "
                + "\(Self.knownPushedBarFloor) of a pushed screen. This tab has never been "
                + "audited before this case, so the expectation is the pushed-screen floor and "
                + "not a measurement of this tab; if they are all `element: nil` bar findings, "
                + "this is the number to re-pin: "
                + found.joined(separator: " | ")
        )
    }
}

/// The words these cases match, spelt out rather than imported — a query that
/// followed a renamed constant would stop testing what it claims to. The
/// app's are ``RoomsStrings``' (`RoomsTurnStatus.swift`); the turns' own are
/// the house's (`history/stubhouse.patch`).
enum RoomsHistoryUnderTest {
    static let tabConversations = "conversations"

    static let pillWaiting = "waiting"
    static let pillIgnored = "ignored"
    static let pillLights = "lights"
    static let pillNoAnswer = "no answer"
    static let pillTime = "time"

    static let waiting = "waiting for an answer…"
    static let ignored = "ignored — sounded like background talk"
    static let timedOut = "no answer — timed out"
    static let somethingWentWrong = "no answer — something went wrong"
    static let noAnswer = "no answer"
    static let unheardSaid = "couldn’t make out what was said · 3.4 s recording"

    static let pendingSaid = "“what time is it in Lisbon”"
    static let unknownSaid = "“what is on the calendar tomorrow”"
    static let lisbonAnswer = "It's half past seven in Lisbon."
    static let lampAnswer = "Done, the study lamp is on."
    static let sixOfSix = "6 of 6"
}
