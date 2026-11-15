import XCTest

/// Voice ID's "needs to learn voices again" state on the People screens
/// (V061), the port of Android 2095fd5f + 2766a325. Every case is named
/// `testVoiceId_…` and every piece of evidence `voiceid-…`.
///
/// The stub's `voiceid2` fixture (`batch-2026-11-14/voiceid2/stubhouse.patch`)
/// mirrors `people.py:90-160`: `POST /api/_test/voice-id {"mode": M,
/// "people": [ids]}` with M one of `ready` (the default: nobody to relearn),
/// `relearn` (the listed people, default person 4 "alex ", are flagged
/// `voice_relearn` with no samples, and counted), `off` (not ready, with the
/// core's internet-off sentence; the people still flagged) and `missing` (the
/// route is a 404 and the rows have no `voice_relearn`: an older server).
/// The voice-id state is read with the roster, so the mode is set **before**
/// the app launches (``voiceIdLaunch(_:people:)``), and put back to `ready`
/// after.
extension StubHouseUITests {

    /// Spelt out rather than imported, like every string these files match on.
    enum VoiceIdUnderTest {
        static let noticeID = "people.voiceIdNotice"
        static let personNoticeID = "person.voiceRelearnNotice"
        static let offTitle = "Voice recognition is off"
        static let relearnTitle = "Domovoi needs to learn voices again"
        static let onePerson = "1 person still has to."
        static let twoPeople = "2 people still have to."
        /// A piece of the household body with both of its apostrophes: ASCII
        /// in "can't", U+2019 in "won’t".
        static let relearnMiddle = "can't be used. Until each person says \u{201C}my name is \u{2026}\u{201D}"
        static let wont = "Domovoi won\u{2019}t know who is speaking."
        /// The stub's off sentence (the core's own, `speaker_encoder.py`).
        static let offProblem = "access is turned off (Settings \u{2192} Privacy \u{2192} Internet)"
        static let personTitle = "Not recognized by voice yet"
        /// Person 4 is "alex " on the wire: the notice trims it.
        static let alexPossessive = "Domovoi has to learn alex\u{2019}s voice again."
        static let alexSays = "alex can say \u{201C}my name is alex\u{201D} in a room and answer yes."
        static let chevyPossessive = "Domovoi has to learn Chevy\u{2019}s voice again."
        static let dot = "has to say \u{201C}my name is \u{2026}\u{201D} again to be recognized by voice"
        static let roomsLabel = "rooms that know this voice"
    }

    /// Reset the fixtures, set voice ID's mode, then launch into People and
    /// wait for the roster's completed read (its subtitle). Voice ID is read
    /// beside the roster, so it is in by the time the subtitle is, give or
    /// take one main-actor turn; the cases wait on what they assert.
    @MainActor
    func voiceIdLaunch(_ mode: String, people: [Int] = [4]) -> XCUIApplication {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures")
        voiceIdMode(mode, people: people)
        let app = launch(mode: StubHouseUITests.ordinaryTypeSize, push: PeopleUnderTest.title)
        if !app.staticTexts[PeopleUnderTest.subtitle].waitForExistence(timeout: 30) {
            print("=== PEOPLE DID NOT DRAW — tree follows ===")
            print(app.debugDescription)
        }
        return app
    }

    @MainActor
    func voiceIdMode(_ mode: String, people: [Int] = [4]) {
        let ids = people.map(String.init).joined(separator: ",")
        control("voice-id", "{\"mode\":\"\(mode)\",\"people\":[\(ids)]}", port: 6601, what: "voice ID's state")
    }

    /// The household notice, by its identifier.
    @MainActor
    func voiceIdNotice(_ app: XCUIApplication) -> XCUIElement {
        app.descendants(matching: .any).matching(identifier: VoiceIdUnderTest.noticeID).firstMatch
    }

    /// The profile's relearn notice, by its identifier.
    @MainActor
    func voiceIdPersonNotice(_ app: XCUIApplication) -> XCUIElement {
        app.descendants(matching: .any).matching(identifier: VoiceIdUnderTest.personNoticeID).firstMatch
    }

    /// `element`'s label holds every one of `parts`; prints the tree when not.
    @MainActor
    func voiceIdSays(_ app: XCUIApplication, _ element: XCUIElement, _ parts: [String], _ what: String) -> Bool {
        let label = element.exists ? element.label : ""
        let missing = parts.filter { !label.contains($0) }
        if !missing.isEmpty {
            print("=== \(what) DOES NOT SAY \(missing) (label: \(label)) — tree follows ===")
            print(app.debugDescription)
        }
        return missing.isEmpty
    }

    /// A roster row's value (subtitle, plus the dot's words when drawn).
    @MainActor
    func voiceIdRowValue(_ app: XCUIApplication, _ name: String) -> String? {
        let row = peopleRow(app, name)
        guard row.waitForExistence(timeout: 15) || peopleScroll(app, to: row) else {
            print("=== NO ROSTER ROW \(name) — tree follows ===")
            print(app.debugDescription)
            return nil
        }
        return row.value as? String
    }

    /// The notice is nowhere, after giving it the seconds it would need.
    @MainActor
    func voiceIdNoNotice(_ app: XCUIApplication, _ what: String) {
        let appeared = voiceIdNotice(app).waitForExistence(timeout: 6)
        let stray = containing(app, VoiceIdUnderTest.relearnTitle).exists
            || containing(app, VoiceIdUnderTest.offTitle).exists
        if appeared || stray {
            print("=== A VOICE ID NOTICE \(what) — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertFalse(appeared || stray, "a voice ID notice is drawn \(what)")
    }

    // MARK: - the household notice

    /// One person to relearn: the title, the body with Android's own
    /// characters, and the singular.
    @MainActor
    func testVoiceId_HouseholdNoticeSaysOnePersonStillHasTo() throws {
        let app = voiceIdLaunch("relearn")
        defer { voiceIdMode("ready") }
        let notice = voiceIdNotice(app)
        let shown = notice.waitForExistence(timeout: 15)
        evidence(app, "voiceid-household-one")
        XCTAssertTrue(shown, "no household notice with somebody to relearn")
        XCTAssertTrue(
            voiceIdSays(
                app, notice,
                [VoiceIdUnderTest.relearnTitle, VoiceIdUnderTest.relearnMiddle,
                 VoiceIdUnderTest.wont, VoiceIdUnderTest.onePerson],
                "THE HOUSEHOLD NOTICE"
            ),
            "the household notice does not say what Android says"
        )
    }

    @MainActor
    func testVoiceId_HouseholdNoticeSaysSeveralPeopleStillHaveTo() throws {
        let app = voiceIdLaunch("relearn", people: [2, 4])
        defer { voiceIdMode("ready") }
        let notice = voiceIdNotice(app)
        let shown = notice.waitForExistence(timeout: 15)
        evidence(app, "voiceid-household-two")
        XCTAssertTrue(shown, "no household notice with two people to relearn")
        XCTAssertTrue(
            voiceIdSays(app, notice, [VoiceIdUnderTest.relearnTitle, VoiceIdUnderTest.twoPeople], "THE PLURAL NOTICE"),
            "the notice does not count two people"
        )
    }

    /// Off: the server's problem as it is, nobody asked to introduce
    /// themselves, no dot, and no notice on the person's profile.
    @MainActor
    func testVoiceId_OffSaysSoAndAsksNobody() throws {
        let app = voiceIdLaunch("off")
        defer { voiceIdMode("ready") }
        let notice = voiceIdNotice(app)
        let shown = notice.waitForExistence(timeout: 15)
        evidence(app, "voiceid-household-off")
        XCTAssertTrue(shown, "no notice that voice recognition is off")
        XCTAssertTrue(
            voiceIdSays(app, notice, [VoiceIdUnderTest.offTitle, VoiceIdUnderTest.offProblem], "THE OFF NOTICE"),
            "the off notice does not carry the server's problem"
        )
        XCTAssertFalse(
            containing(app, VoiceIdUnderTest.relearnTitle).exists,
            "voice ID is off and the page still asks people to introduce themselves"
        )
        let value = voiceIdRowValue(app, PeopleUnderTest.alexLower) ?? ""
        XCTAssertFalse(value.contains(VoiceIdUnderTest.dot), "the relearn dot is drawn while voice ID is off: \(value)")

        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.alexLower), "alex's profile did not open")
        let personNotice = voiceIdPersonNotice(app).waitForExistence(timeout: 6)
        evidence(app, "voiceid-person-off")
        if personNotice {
            print("=== A RELEARN NOTICE WHILE OFF — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertFalse(personNotice, "the profile asks alex to introduce themselves while voice ID is off")
    }

    /// Ready with nobody to relearn: nothing new on the page.
    @MainActor
    func testVoiceId_NothingWhenReadyAndNobodyRelearns() throws {
        let app = voiceIdLaunch("ready")
        voiceIdNoNotice(app, "with nobody to relearn")
        evidence(app, "voiceid-household-ready")
        let value = voiceIdRowValue(app, PeopleUnderTest.alexLower) ?? ""
        XCTAssertFalse(value.contains(VoiceIdUnderTest.dot), "a relearn dot with nobody to relearn: \(value)")
    }

    // MARK: - the roster dot

    @MainActor
    func testVoiceId_RosterDotOnlyOnThePersonWhoRelearns() throws {
        let app = voiceIdLaunch("relearn")
        defer { voiceIdMode("ready") }
        // The notice is the sign voice ID's state is in.
        _ = voiceIdNotice(app).waitForExistence(timeout: 15)
        let alex = voiceIdRowValue(app, PeopleUnderTest.alexLower) ?? ""
        let jordan = voiceIdRowValue(app, PeopleUnderTest.jordan) ?? ""
        evidence(app, "voiceid-roster-dot")
        if !alex.contains(VoiceIdUnderTest.dot) || jordan.contains(VoiceIdUnderTest.dot) {
            print("=== THE DOT IS ON THE WRONG ROWS — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertTrue(alex.contains(VoiceIdUnderTest.dot), "alex's row does not say they have to relearn: \(alex)")
        XCTAssertFalse(jordan.contains(VoiceIdUnderTest.dot), "Jordan's row says they have to relearn: \(jordan)")
    }

    // MARK: - the person

    /// The profile's notice names them (trimmed, with the possessive), and
    /// the rooms section is not drawn while they relearn.
    @MainActor
    func testVoiceId_PersonNoticeNamesThemAndHidesTheRooms() throws {
        let app = voiceIdLaunch("relearn")
        defer { voiceIdMode("ready") }
        _ = voiceIdNotice(app).waitForExistence(timeout: 15)
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.alexLower), "alex's profile did not open")
        let notice = voiceIdPersonNotice(app)
        let shown = notice.waitForExistence(timeout: 15) || peopleScroll(app, to: notice)
        evidence(app, "voiceid-person-alex")
        XCTAssertTrue(shown, "alex's profile has no relearn notice")
        XCTAssertTrue(
            voiceIdSays(
                app, notice,
                [VoiceIdUnderTest.personTitle, VoiceIdUnderTest.alexPossessive, VoiceIdUnderTest.alexSays],
                "THE PERSON NOTICE"
            ),
            "the person notice does not name alex"
        )
        let rooms = app.staticTexts[VoiceIdUnderTest.roomsLabel].waitForExistence(timeout: 6)
        if rooms {
            print("=== ROOMS DRAWN FOR SOMEBODY WHO RELEARNS — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertFalse(rooms, "the rooms section is drawn for somebody voice ID has to relearn")
    }

    /// Chevy has rooms (the stub lists kitchen and study for everyone): with
    /// Chevy relearning, the section goes and the notice takes its place.
    @MainActor
    func testVoiceId_RoomsHiddenWhileThePersonRelearns() throws {
        let app = voiceIdLaunch("relearn", people: [2])
        defer { voiceIdMode("ready") }
        _ = voiceIdNotice(app).waitForExistence(timeout: 15)
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.chevy), "Chevy's profile did not open")
        let notice = voiceIdPersonNotice(app)
        let shown = notice.waitForExistence(timeout: 15) || peopleScroll(app, to: notice)
        XCTAssertTrue(shown, "Chevy's profile has no relearn notice")
        XCTAssertTrue(
            voiceIdSays(app, notice, [VoiceIdUnderTest.chevyPossessive], "CHEVY'S NOTICE"),
            "the notice does not name Chevy"
        )
        let rooms = app.staticTexts[VoiceIdUnderTest.roomsLabel].waitForExistence(timeout: 6)
            || peopleScroll(app, to: app.staticTexts[VoiceIdUnderTest.roomsLabel], flicks: 4)
        evidence(app, "voiceid-rooms-hidden-chevy")
        XCTAssertFalse(rooms, "Chevy's rooms are drawn while Chevy relearns")
    }

    // MARK: - an older server

    /// A server from before V061 (404, no `voice_relearn`): no notice, no dot,
    /// no person notice, and the rooms section as it was.
    @MainActor
    func testVoiceId_AnOlderServerShowsNothingNew() throws {
        let app = voiceIdLaunch("missing")
        defer { voiceIdMode("ready") }
        voiceIdNoNotice(app, "for a server without the route")
        evidence(app, "voiceid-older-server")
        let value = voiceIdRowValue(app, PeopleUnderTest.alexLower) ?? ""
        XCTAssertFalse(value.contains(VoiceIdUnderTest.dot), "a relearn dot from an older server: \(value)")
        XCTAssertFalse(
            containing(app, "voice-id").exists || containing(app, "Not Found").exists,
            "the voice-id 404 is on screen"
        )

        let label = app.staticTexts[VoiceIdUnderTest.roomsLabel]
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's profile did not open")
        let rooms = label.waitForExistence(timeout: 15) || peopleScroll(app, to: label)
        XCTAssertTrue(rooms, "Jordan's rooms are gone on an older server")
        XCTAssertFalse(voiceIdPersonNotice(app).exists, "a relearn notice from an older server")
    }
}
