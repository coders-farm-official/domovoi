import XCTest

/// Rooms that know this voice on a person's profile (1.3.0), the port of
/// Android e6f89545. Every case is named `testVoiceRooms_…` and every piece of
/// evidence `voicerooms-…`.
///
/// The stub's `voicerooms` fixture (`batch-2026-11-14/voicerooms/stubhouse.patch`)
/// mirrors `people.py:401`: every satellite room (kitchen, study) for every
/// person, plus a room with a sample and no satellite. Jordan knows kitchen
/// (current) and porch (legacy) and not study, so `2/3` with one `not yet`
/// and the hint; Alex knows both rooms, so `2/2` and no hint.
/// `POST /api/_test/voice-rooms {"mode": "missing" | "empty"}` makes the route
/// a 404 (a server before 1.3.0) or an empty list (a house with no rooms);
/// `peopleLaunch` resets it to the rows.
extension StubHouseUITests {

    /// Spelt out rather than imported, like every string these files match on.
    enum VoiceRoomsUnderTest {
        static let label = "rooms that know this voice"
        static let jordanCount = "2/3"
        static let alexCount = "2/2"
        static let kitchen = "kitchen"
        static let porch = "porch"
        static let studyNotYet = "study \u{00B7} not yet"
        static let kitchenNotYet = "kitchen \u{00B7} not yet"
        static let jordanHint = "Jordan can say \u{201C}my name is Jordan\u{201D} and answer yes."
        static let hintStart = "Rooms recognize a voice best"
        /// Jordan's notes: drawn after the section, so a profile that has
        /// these has had its chance to draw the rooms too.
        static let notes = "prefers the kitchen satellite"
    }

    @MainActor
    func voiceRoomsMode(_ mode: String) {
        control("voice-rooms", "{\"mode\":\"\(mode)\"}", port: 6601, what: "the voice-rooms answer")
    }

    /// Open `name`'s profile and bring `element` into the built range.
    @MainActor
    func voiceRoomsOpen(_ app: XCUIApplication, _ name: String, until element: XCUIElement) -> Bool {
        guard peopleOpen(app, name) else { return false }
        if element.waitForExistence(timeout: 15) { return true }
        let found = peopleScroll(app, to: element)
        if !found {
            print("=== \(name)'s PROFILE NEVER DREW \(element) — tree follows ===")
            print(app.debugDescription)
        }
        return found
    }

    /// The section, its `known/total` count, and one chip per room, with
    /// `· not yet` on the room that has never heard them — and on no other.
    @MainActor
    func testVoiceRooms_SectionShowsTheCountAndChips() throws {
        let app = peopleLaunch()
        let label = app.staticTexts[VoiceRoomsUnderTest.label]
        XCTAssertTrue(
            voiceRoomsOpen(app, PeopleUnderTest.jordan, until: label),
            "Jordan's profile has no `\(VoiceRoomsUnderTest.label)` section"
        )
        let count = app.staticTexts[VoiceRoomsUnderTest.jordanCount]
        let counted = count.waitForExistence(timeout: 10)
        let study = app.staticTexts[VoiceRoomsUnderTest.studyNotYet]
        let marked = study.waitForExistence(timeout: 5)
        evidence(app, "voicerooms-jordan")
        if !(counted && marked) {
            print("=== THE ROOMS SECTION IS INCOMPLETE — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertTrue(counted, "the count does not read \(VoiceRoomsUnderTest.jordanCount)")
        XCTAssertTrue(marked, "the room that never heard Jordan is not marked not yet")
        XCTAssertTrue(app.staticTexts[VoiceRoomsUnderTest.kitchen].exists, "no kitchen chip")
        XCTAssertTrue(
            app.staticTexts[VoiceRoomsUnderTest.porch].exists,
            "no porch chip — a legacy sample still counts, and a room with a sample but no "
                + "satellite is still listed"
        )
        XCTAssertFalse(
            app.staticTexts[VoiceRoomsUnderTest.kitchenNotYet].exists,
            "a room that knows Jordan is marked not yet"
        )
    }

    /// The hint names the person, in their own words, when a room is not yet
    /// — and is not drawn for somebody every room knows.
    @MainActor
    func testVoiceRooms_HintNamesThePerson() throws {
        let app = peopleLaunch()
        let hint = containing(app, VoiceRoomsUnderTest.jordanHint)
        let shown = voiceRoomsOpen(app, PeopleUnderTest.jordan, until: hint)
        evidence(app, "voicerooms-hint-jordan")
        XCTAssertTrue(shown, "Jordan's profile does not say how to teach the study their voice")

        app.buttons[PeopleUnderTest.back].tap()
        let alexCount = app.staticTexts[VoiceRoomsUnderTest.alexCount]
        XCTAssertTrue(
            voiceRoomsOpen(app, PeopleUnderTest.alex, until: alexCount),
            "Alex's profile does not read \(VoiceRoomsUnderTest.alexCount)"
        )
        evidence(app, "voicerooms-no-hint-alex")
        let stray = containing(app, VoiceRoomsUnderTest.hintStart)
        if stray.exists {
            print("=== A HINT FOR SOMEBODY EVERY ROOM KNOWS — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertFalse(stray.exists, "Alex is known in every room and still gets the hint")
    }

    /// A server from before 1.3.0 (the route is a 404): no section, and no
    /// toast or error about it.
    @MainActor
    func testVoiceRooms_HiddenForAServerWithoutTheRoute() throws {
        let app = peopleLaunch()
        voiceRoomsMode("missing")
        defer { voiceRoomsMode("rows") }
        voiceRoomsAbsent(app, evidence: "voicerooms-hidden-404")
    }

    /// A house with no rooms (an empty list): no section.
    @MainActor
    func testVoiceRooms_HiddenForAHouseWithNoRooms() throws {
        let app = peopleLaunch()
        voiceRoomsMode("empty")
        defer { voiceRoomsMode("rows") }
        voiceRoomsAbsent(app, evidence: "voicerooms-hidden-empty")
    }

    /// Jordan's profile drew (their notes are there), and in the seconds the
    /// rooms' read had, the section never appeared and nothing complained.
    @MainActor
    func voiceRoomsAbsent(_ app: XCUIApplication, evidence name: String) {
        let notes = app.staticTexts[VoiceRoomsUnderTest.notes]
        XCTAssertTrue(
            voiceRoomsOpen(app, PeopleUnderTest.jordan, until: notes),
            "Jordan's profile did not draw, so absence would prove nothing"
        )
        let label = app.staticTexts[VoiceRoomsUnderTest.label]
        let appeared = label.waitForExistence(timeout: 6)
        evidence(app, name)
        if appeared {
            print("=== THE ROOMS SECTION DREW — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertFalse(appeared, "the rooms section is drawn with no rooms to show")
        XCTAssertFalse(
            containing(app, "voice-rooms").exists || containing(app, "Not Found").exists,
            "the rooms' read raised an error on screen"
        )
        XCTAssertTrue(notes.exists, "the profile is not on screen any more")
    }
}
