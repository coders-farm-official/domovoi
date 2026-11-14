import XCTest

/// "This is me" and listening history on a person's page, and Settings'
/// "This phone belongs to" (the CLAIM unit, batch 2026-11-14; Android
/// a1e611b6). Against the stub house's `claim` fixture
/// (`design-notes/ios/batch-2026-11-14/claim/stubhouse.patch`): Jordan is
/// person 1, Chevy 2, Alex 3; Alex has 42 songs kept for a year.
///
/// Every case starts from `reset-fixtures`, which makes the phone nobody's
/// on the house's side; the app's own record follows at the launch's
/// registration, whose answer carries the owner. Nothing here opens the
/// connection sheet.
extension StubHouseUITests {

    /// The words matched on, spelt out (a query that followed a renamed
    /// constant would stop testing what it claims to).
    enum ThisIsMeUnderTest {
        static let thisPhone = "this phone"
        static let listeningHistory = "listening history"
        static let askJordan = "Is this Jordan’s phone?"
        static let isJordans = "This phone is Jordan’s. What plays here counts as Jordan’s listening"
        static let nowJordans = "this phone is Jordan’s now"
        static let nobodysNow = "this phone isn’t anyone’s now"
        static let thisIsMe = "this is me"
        static let notMe = "not me"
        static let takenChevy = "Chevy is already claimed by another phone or browser."
        static let openDashboard = "open the dashboard"
        static let alexLine = "42 songs played for Alex · kept a year"
        static let clear = "clear Alex’s listening history"
        static let onlyAlex = "Only Alex’s own phone or browser, or an admin, can clear it."
        static let card = "This phone belongs to"
        static let nobody = "nobody in particular"
    }

    @MainActor
    private func thisIsMeFind(_ app: XCUIApplication, _ element: XCUIElement, _ what: String) -> Bool {
        if element.waitForExistence(timeout: 20), peopleReveal(app, element) { return true }
        print("=== THIS IS ME: \(what) NOT FOUND — tree follows ===")
        print(app.debugDescription)
        return false
    }

    /// Nobody's phone: the block asks, "this is me" claims it, the toast is
    /// the SERVER's answer, and the block then offers "not me", which
    /// releases it again.
    @MainActor
    func testThisIsMe_ClaimAndReleaseFromThePersonsPage() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's detail did not open")
        XCTAssertTrue(
            thisIsMeFind(app, containing(app, ThisIsMeUnderTest.askJordan), "the nobody's-phone question"),
            "the profile has no \"this phone\" block asking whether it is Jordan's"
        )
        let claim = startingWith(app, ThisIsMeUnderTest.thisIsMe)
        XCTAssertTrue(thisIsMeFind(app, claim, "the this-is-me button"), "no `this is me` button")
        claim.tap()
        let toast = containing(app, ThisIsMeUnderTest.nowJordans)
        let said = toast.waitForExistence(timeout: 15)
        evidence(app, "this-is-me-10-claimed")
        XCTAssertTrue(said, "the claim did not say the server's answer — tree follows\n" + app.debugDescription)
        XCTAssertTrue(
            thisIsMeFind(app, containing(app, ThisIsMeUnderTest.isJordans), "the claimed line"),
            "after the claim the block does not say the phone is Jordan's"
        )
        let release = app.buttons[ThisIsMeUnderTest.notMe]
        XCTAssertTrue(thisIsMeFind(app, release, "the not-me button"), "no `not me` on Jordan's own phone")
        release.tap()
        let released = containing(app, ThisIsMeUnderTest.nobodysNow).waitForExistence(timeout: 15)
        evidence(app, "this-is-me-11-released")
        XCTAssertTrue(released, "`not me` did not say the phone is nobody's — tree follows\n" + app.debugDescription)
    }

    /// A refused claim is shown in the server's words, never as the owner,
    /// with the dashboard offered (an admin's yes). The dashboard is not
    /// opened: it would leave the app.
    @MainActor
    func testThisIsMe_ARefusedClaimSaysTheServersSentence() throws {
        let app = peopleLaunch()
        control("claim", #"{"taken":[2]}"#, port: 6601, what: "Chevy claimed by another phone")
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.chevy), "Chevy's detail did not open")
        let claim = startingWith(app, ThisIsMeUnderTest.thisIsMe)
        XCTAssertTrue(thisIsMeFind(app, claim, "the this-is-me button"), "no `this is me` button")
        claim.tap()
        let refused = containing(app, ThisIsMeUnderTest.takenChevy).waitForExistence(timeout: 15)
        evidence(app, "this-is-me-20-refused")
        XCTAssertTrue(refused, "the refusal was swallowed — tree follows\n" + app.debugDescription)
        XCTAssertTrue(app.buttons[ThisIsMeUnderTest.openDashboard].exists, "the refusal offers no dashboard")
        XCTAssertFalse(
            containing(app, "This phone is Chevy’s").exists,
            "a refused claim is shown as the owner"
        )
    }

    /// Alex's listening history: the count, and "clear" from a phone that
    /// isn't Alex's answers with the dashboard's sentence, not a DELETE.
    @MainActor
    func testThisIsMe_ListeningHistoryCountAndClearFromSomeoneElsesPhone() throws {
        let app = peopleLaunch()
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.alex), "Alex's detail did not open")
        XCTAssertTrue(
            thisIsMeFind(app, containing(app, ThisIsMeUnderTest.alexLine), "Alex's count"),
            "the listening-history line does not count Alex's 42 songs"
        )
        let clear = app.buttons[ThisIsMeUnderTest.clear]
        XCTAssertTrue(thisIsMeFind(app, clear, "the clear button"), "no `clear` for a person with history")
        clear.tap()
        let said = containing(app, ThisIsMeUnderTest.onlyAlex).waitForExistence(timeout: 10)
        evidence(app, "this-is-me-30-clear-not-here")
        XCTAssertTrue(said, "clear from someone else's phone did not explain — tree follows\n" + app.debugDescription)
        XCTAssertTrue(app.buttons[ThisIsMeUnderTest.thisIsMe].exists, "the dialog does not offer `this is me`")
        app.buttons["cancel"].tap()
        XCTAssertTrue(containing(app, ThisIsMeUnderTest.alexLine).exists, "cancel deleted something")
    }

    /// Settings: the card shows the server's answer, and a pick is a claim
    /// whose toast is the server's answer.
    @MainActor
    func testThisIsMe_SettingsCardPicksThroughTheServer() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the house back to its seed")
        let app = launch(push: "Settings")
        XCTAssertTrue(reachPhonesAndBrowsersFromSettings(app), "never reached Phones & browsers")
        let card = app.buttons[ThisIsMeUnderTest.card]
        guard card.waitForExistence(timeout: 20) else {
            print("=== NO `This phone belongs to` CARD — tree follows ===")
            print(app.debugDescription)
            return XCTFail("the panel draws no `This phone belongs to` card on a paired phone")
        }
        XCTAssertEqual(ThisIsMeUnderTest.nobody, card.value as? String, "a phone nobody has claimed")
        card.tap()
        let jordan = app.buttons[PeopleUnderTest.jordan]
        guard jordan.waitForExistence(timeout: 15) else {
            print("=== THE CARD OFFERS NO JORDAN — tree follows ===")
            print(app.debugDescription)
            return XCTFail("the dropdown does not offer Jordan")
        }
        jordan.tap()
        let said = containing(app, ThisIsMeUnderTest.nowJordans).waitForExistence(timeout: 15)
        evidence(app, "this-is-me-40-settings-claimed")
        XCTAssertTrue(said, "the pick did not toast the server's answer — tree follows\n" + app.debugDescription)
        XCTAssertEqual(PeopleUnderTest.jordan, card.value as? String, "the card does not show the answer")
        // Put it back: the phone is nobody's for the next case.
        card.tap()
        let nobody = app.buttons[ThisIsMeUnderTest.nobody]
        if nobody.waitForExistence(timeout: 15) { nobody.tap() }
        XCTAssertTrue(
            containing(app, ThisIsMeUnderTest.nobodysNow).waitForExistence(timeout: 15),
            "releasing from Settings did not toast the server's answer"
        )
    }
}
