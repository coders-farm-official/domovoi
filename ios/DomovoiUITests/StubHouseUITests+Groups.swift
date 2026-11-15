import XCTest

/// Groups (V060): rooms sharing one music queue, on the three screens the
/// iPhone already had — a room's card on Home (its chip and its share menu),
/// a room's queue sheet ("shared with … · leave") and Settings → Rooms (the
/// Groups card). The web is the spec (`home.jsx`, `music.jsx`,
/// `satellites.jsx` on main 9fea285d). Every case is named `testGroups_…` and
/// every piece of evidence `groups-…`.
///
/// The stub's `groups` fixture (`batch-2026-11-14/groups/stubhouse.patch`)
/// mirrors `web/backend/api/music_groups.py`. `POST /api/_test/groups`:
///
/// * `{"mode": "on"}` — the groups house: kitchen online playing `Harvest
///   Moon`, garage and office online and quiet, study offline; one named
///   group, `Upstairs` = garage + office; nothing shared.
/// * `{"mode": "older"}` — the same rooms, and `/api/music/groups` is a 404
///   (a server older than groups). `"missing"` (the default, and what
///   reset-fixtures puts back) is the base house with that 404, so no other
///   unit's case ever meets a group control.
/// * `{"mode": "refused"}` — the same rooms, and the list answers 403.
/// * `{"shared": "upstairs" | "pair" | "none"}` — kitchen plays to Upstairs
///   (kitchen, garage, office), or garage joined kitchen by hand.
/// * `{"fail": 400 | 409 | 403 | null}` — every write answers that, with the
///   server's own sentence.
///
/// `GET /api/_test/groups` reads back what the phone sent (`sent`).
///
/// No case here opens the connection sheet or taps "use a different server".
extension StubHouseUITests {

    /// Spelt out rather than imported, like every string these files match on.
    enum GroupsUnderTest {
        static func menu(_ room: String) -> String {
            "play \(room)\u{2019}s music in other rooms, or join another room"
        }
        static let playInUpstairs = "play in Upstairs too"
        static let playEverywhere = "play everywhere"
        static let playInGarage = "play in garage too"
        static let playInOffice = "play in office too"
        static let joinKitchen = "join kitchen"
        static let leaveTheGroup = "leave the group"
        static let kitchenChip = "Upstairs · with garage, office"
        static let garageChip = "Upstairs · with kitchen, office"
        static let pairChip = "with garage"
        static let sharedDone = "play in garage too · done"
        static let joinedDone = "join kitchen · done"
        static let refused409 = "share failed: nothing is playing in kitchen"
        static let garageLine = "Upstairs · shared with 2 other rooms (kitchen, office)"
        static let garageLeft = "garage left the group"
        static let harvestMoon = "Harvest Moon"
        static let queueOfGarage = "garage's queue"
        static let title = "Groups"
        /// A piece of the card's help, drawn nowhere else.
        static let helpPiece = "needs no group"
        static func pick(_ room: String) -> String { "rooms.groups.pick.\(room)" }
        static let newGroup = "new group"
        static let nameField = "group name"
        static let save = "save"
        static let made = "group Downstairs made"
        static let radioSentence = "A group's name can't use words like station, radio or playlist, "
            + "or saying it would play something else."
        static let editUpstairs = "edit Upstairs"
        static let deleteUpstairs = "delete Upstairs"
        static let savedTopFloor = "group Top floor saved"
        static let deletedUpstairs = "group Upstairs deleted"
        static let deleteTitle = "Delete the group Upstairs?"
        static let delete = "delete"
    }

    // MARK: - getting there

    /// Reset, set the groups house up, then launch onto Home and wait for the
    /// kitchen's row to carry its song.
    @MainActor
    func groupsHome(_ setup: String = "{\"mode\":\"on\"}", paired: Bool = true) -> XCUIApplication {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures")
        groupsSet(setup)
        let app = launch(paired: paired)
        if paired, !containing(app, "kitchen, playing").waitForExistence(timeout: 40) {
            print("=== HOME NEVER DREW THE PLAYING KITCHEN — tree follows ===")
            print(app.debugDescription)
        }
        return app
    }

    /// Reset, set the groups house up, then launch into Settings → Rooms and
    /// wait for the broadcast card, which is drawn once the roster lands.
    @MainActor
    func groupsSettings(_ setup: String = "{\"mode\":\"on\"}", paired: Bool = true) -> XCUIApplication {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures")
        groupsSet(setup)
        let app = launch(paired: paired, push: "Satellites", pushDelay: 3)
        if paired, !containing(app, RoomsStringsUnderTest.broadcastHeading).waitForExistence(timeout: 40) {
            print("=== SETTINGS → ROOMS NEVER DREW ITS ROSTER — tree follows ===")
            print(app.debugDescription)
        }
        return app
    }

    @MainActor
    func groupsSet(_ body: String) {
        control("groups", body, port: 6601, what: "the groups house")
    }

    /// Bring `element` onto the window by swiping the page, and say whether
    /// it got there. Prints the tree when it did not.
    @MainActor
    func groupsReveal(_ app: XCUIApplication, _ element: XCUIElement, _ what: String) -> Bool {
        guard element.waitForExistence(timeout: 20) else {
            print("=== NO \(what) — tree follows ===")
            print(app.debugDescription)
            return false
        }
        var swipes = 0
        while !element.isHittable, swipes < 8 {
            app.swipeUp()
            swipes += 1
        }
        if !element.isHittable {
            print("=== \(what) NEVER CAME ONTO THE WINDOW — tree follows ===")
            print(app.debugDescription)
        }
        return element.isHittable
    }

    /// A menu item whose label starts with `label` (an item with a hint may
    /// read both lines).
    @MainActor
    func groupsMenuItem(_ app: XCUIApplication, _ label: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", label)).firstMatch
    }

    /// Open `room`'s share menu on Home.
    @MainActor
    func groupsOpenMenu(_ app: XCUIApplication, _ room: String) -> Bool {
        let button = app.buttons[GroupsUnderTest.menu(room)]
        guard groupsReveal(app, button, "\(room.uppercased())'S SHARE MENU") else { return false }
        button.tap()
        return true
    }

    /// `GET /api/_test/groups`, as text.
    @MainActor
    func groupsSent() -> String {
        roomsAsk("groups")
    }

    /// The share menu is nowhere on Home, after the seconds it would need.
    @MainActor
    func groupsNoMenu(_ app: XCUIApplication, _ what: String) {
        let kitchen = app.buttons[GroupsUnderTest.menu("kitchen")]
        let appeared = kitchen.waitForExistence(timeout: 8)
            || app.buttons[GroupsUnderTest.menu("garage")].exists
        if appeared {
            print("=== A SHARE MENU \(what) — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertFalse(appeared, "a share menu is drawn \(what)")
    }

    /// The Groups card is nowhere in Settings → Rooms.
    @MainActor
    func groupsNoCard(_ app: XCUIApplication, _ what: String) {
        // The card would be under the broadcast card: travel there first.
        for _ in 0..<4 { app.swipeUp() }
        let appeared = containing(app, GroupsUnderTest.helpPiece).waitForExistence(timeout: 6)
            || app.buttons[GroupsUnderTest.newGroup].exists
        if appeared {
            print("=== A GROUPS CARD \(what) — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertFalse(appeared, "the Groups card is drawn \(what)")
    }

    // MARK: - Home: the chip

    /// Every room of a shared queue says so: the group's name and the rooms
    /// it plays with, the host first.
    @MainActor
    func testGroups_ARoomInAGroupShowsItsChipOnHome() throws {
        let app = groupsHome("{\"mode\":\"on\",\"shared\":\"upstairs\"}")
        let kitchen = containing(app, GroupsUnderTest.kitchenChip)
        let shown = kitchen.waitForExistence(timeout: 20)
        evidence(app, "groups-01-chip")
        if !shown {
            print("=== NO KITCHEN CHIP — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertTrue(shown, "the host's row does not name its group")
        XCTAssertTrue(
            containing(app, GroupsUnderTest.garageChip).exists,
            "a member's row does not name its group with the host first — tree follows\n" + app.debugDescription
        )
        // A member plays the host's song: the server says so, and the row
        // draws it.
        XCTAssertTrue(
            containing(app, "garage, playing, Harvest Moon").exists,
            "the member's row does not show the host's song — tree follows\n" + app.debugDescription
        )
    }

    // MARK: - Home: the share menu

    /// A playing room offers its named group, everywhere and each connected
    /// room — and no "leave", because it is in no group.
    @MainActor
    func testGroups_APlayingRoomsMenuOffersTheGroupEverywhereAndEachRoom() throws {
        let app = groupsHome()
        XCTAssertTrue(groupsOpenMenu(app, "kitchen"), "the kitchen has no share menu")
        let upstairs = groupsMenuItem(app, GroupsUnderTest.playInUpstairs)
        let shown = upstairs.waitForExistence(timeout: 10)
        evidence(app, "groups-02-menu")
        if !shown {
            print("=== THE MENU HAS NO GROUP — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertTrue(shown, "the menu does not offer the named group")
        XCTAssertTrue(groupsMenuItem(app, GroupsUnderTest.playEverywhere).exists, "no play everywhere")
        XCTAssertTrue(groupsMenuItem(app, GroupsUnderTest.playInGarage).exists, "no play in garage too")
        XCTAssertTrue(groupsMenuItem(app, GroupsUnderTest.playInOffice).exists, "no play in office too")
        XCTAssertFalse(groupsMenuItem(app, "play in study").exists, "an offline room is offered")
        XCTAssertFalse(groupsMenuItem(app, GroupsUnderTest.leaveTheGroup).exists, "leave is offered outside a group")
    }

    /// "play in garage too": the request the web sends, the web's words, and
    /// the chip that follows.
    @MainActor
    func testGroups_PlayInAnotherRoomTooSharesTheMusic() throws {
        let app = groupsHome()
        XCTAssertTrue(groupsOpenMenu(app, "kitchen"), "the kitchen has no share menu")
        let garage = groupsMenuItem(app, GroupsUnderTest.playInGarage)
        XCTAssertTrue(garage.waitForExistence(timeout: 10), "no play in garage too — tree follows\n" + app.debugDescription)
        garage.tap()
        XCTAssertTrue(roomsWaitFor(app, GroupsUnderTest.sharedDone), "the share said nothing")
        XCTAssertTrue(roomsWaitFor(app, GroupsUnderTest.pairChip), "no chip after the share")
        evidence(app, "groups-03-shared")
        let sent = groupsSent()
        XCTAssertTrue(sent.contains("/api/music/groups/shared"), "nothing was posted: \(sent)")
        XCTAssertTrue(sent.contains("\"host_room\": \"kitchen\""), "the host was not the kitchen: \(sent)")
        XCTAssertTrue(sent.contains("\"rooms\": [\"garage\"]"), "the garage was not asked for: \(sent)")
    }

    /// A quiet room is offered only the joins, and a join is said.
    @MainActor
    func testGroups_AQuietRoomIsOfferedOnlyTheJoins() throws {
        let app = groupsHome()
        XCTAssertTrue(groupsOpenMenu(app, "garage"), "the quiet garage has no share menu")
        let join = groupsMenuItem(app, GroupsUnderTest.joinKitchen)
        XCTAssertTrue(join.waitForExistence(timeout: 10), "no join kitchen — tree follows\n" + app.debugDescription)
        evidence(app, "groups-04-quiet-menu")
        XCTAssertFalse(groupsMenuItem(app, GroupsUnderTest.playEverywhere).exists, "a quiet room offers to share")
        XCTAssertFalse(groupsMenuItem(app, GroupsUnderTest.playInUpstairs).exists, "a quiet room offers its group")
        join.tap()
        XCTAssertTrue(roomsWaitFor(app, GroupsUnderTest.joinedDone), "the join said nothing")
    }

    /// The server's 409 sentence, as the web says it.
    @MainActor
    func testGroups_A409SaysTheServersSentence() throws {
        let app = groupsHome()
        groupsSet("{\"fail\":409}")
        defer { groupsSet("{\"fail\":null}") }
        XCTAssertTrue(groupsOpenMenu(app, "kitchen"), "the kitchen has no share menu")
        let everywhere = groupsMenuItem(app, GroupsUnderTest.playEverywhere)
        XCTAssertTrue(everywhere.waitForExistence(timeout: 10), "no play everywhere — tree follows\n" + app.debugDescription)
        everywhere.tap()
        XCTAssertTrue(roomsWaitFor(app, GroupsUnderTest.refused409), "the 409 was not said in the server's words")
        evidence(app, "groups-05-409")
    }

    /// A member is offered "leave the group".
    @MainActor
    func testGroups_AMemberIsOfferedLeave() throws {
        let app = groupsHome("{\"mode\":\"on\",\"shared\":\"upstairs\"}")
        XCTAssertTrue(groupsOpenMenu(app, "garage"), "the member has no share menu")
        let leave = groupsMenuItem(app, GroupsUnderTest.leaveTheGroup)
        XCTAssertTrue(leave.waitForExistence(timeout: 10), "a member is not offered leave — tree follows\n" + app.debugDescription)
        evidence(app, "groups-06-member-menu")
        leave.tap()
        XCTAssertTrue(roomsWaitFor(app, GroupsUnderTest.garageLeft), "leaving said nothing")
        XCTAssertTrue(roomsWaitGone(app, GroupsUnderTest.garageChip), "the garage still names the group")
    }

    // MARK: - the queue sheet

    /// A member's queue is the host's, says who shares it, and offers leave.
    @MainActor
    func testGroups_AMembersQueueSaysItIsSharedAndLeaves() throws {
        let app = groupsHome("{\"mode\":\"on\",\"shared\":\"upstairs\"}")
        let button = app.buttons[GroupsUnderTest.queueOfGarage]
        XCTAssertTrue(groupsReveal(app, button, "THE GARAGE'S QUEUE BUTTON"), "the member has no queue button")
        button.tap()
        let line = containing(app, GroupsUnderTest.garageLine)
        let shown = line.waitForExistence(timeout: 20)
        evidence(app, "groups-07-queue")
        if !shown {
            print("=== NO SHARED LINE ON THE GARAGE'S QUEUE — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertTrue(shown, "the member's queue does not say it is shared")
        XCTAssertTrue(
            containing(app, GroupsUnderTest.harvestMoon).exists,
            "the member's queue is not the host's — tree follows\n" + app.debugDescription
        )
        let leave = app.buttons[GroupsUnderTest.leaveTheGroup]
        XCTAssertTrue(leave.waitForExistence(timeout: 10), "no leave on the queue — tree follows\n" + app.debugDescription)
        leave.tap()
        XCTAssertTrue(roomsWaitFor(app, GroupsUnderTest.garageLeft), "leaving said nothing in the sheet")
        XCTAssertTrue(roomsWaitGone(app, GroupsUnderTest.garageLine), "the shared line stayed after leaving")
        XCTAssertFalse(app.buttons[GroupsUnderTest.leaveTheGroup].exists, "leave stayed on a room in no group")
    }

    /// A room in no group has no line and no leave.
    @MainActor
    func testGroups_ARoomOutsideAGroupHasNoLeave() throws {
        let app = groupsHome()
        let button = app.buttons["kitchen's queue"]
        XCTAssertTrue(groupsReveal(app, button, "THE KITCHEN'S QUEUE BUTTON"), "the kitchen has no queue button")
        button.tap()
        XCTAssertTrue(
            containing(app, GroupsUnderTest.harvestMoon).waitForExistence(timeout: 20),
            "the kitchen's queue never drew — tree follows\n" + app.debugDescription
        )
        evidence(app, "groups-08-queue-alone")
        XCTAssertFalse(containing(app, "shared with").exists, "a room in no group says it is shared")
        XCTAssertFalse(app.buttons[GroupsUnderTest.leaveTheGroup].exists, "leave is offered outside a group")
    }

    // MARK: - Settings → Rooms

    /// The card lists the named group; "new group" makes one.
    @MainActor
    func testGroups_SettingsMakesAGroup() throws {
        let app = groupsSettings()
        let new = app.buttons[GroupsUnderTest.newGroup]
        XCTAssertTrue(groupsReveal(app, new, "NEW GROUP"), "the Groups card has no new group")
        XCTAssertTrue(containing(app, "garage, office").exists, "the Upstairs row does not list its rooms")
        evidence(app, "groups-09-card")
        new.tap()
        let field = roomsField(app, GroupsUnderTest.nameField)
        XCTAssertTrue(groupsReveal(app, field, "THE NAME FIELD"), "no name field")
        field.tap()
        app.typeText("Downstairs")
        for room in ["kitchen", "garage"] {
            let pick = app.buttons.matching(identifier: GroupsUnderTest.pick(room)).firstMatch
            XCTAssertTrue(groupsReveal(app, pick, "THE \(room.uppercased()) PICK"), "no \(room) to pick")
            pick.tap()
        }
        let save = app.buttons[GroupsUnderTest.save]
        XCTAssertTrue(groupsReveal(app, save, "SAVE"), "no save")
        XCTAssertTrue(save.isEnabled, "save stays off with a name and two rooms — tree follows\n" + app.debugDescription)
        save.tap()
        XCTAssertTrue(roomsWaitFor(app, GroupsUnderTest.made), "making the group said nothing")
        // The rooms go to the server sorted, and it lists them as sent.
        XCTAssertTrue(roomsWaitFor(app, "garage, kitchen"), "the new group is not listed")
        evidence(app, "groups-10-made")
    }

    /// A name that sounds like a radio station: the server's 400 sentence,
    /// as it is, and the form stays open.
    @MainActor
    func testGroups_ARadioNameIsRefusedInTheServersWords() throws {
        let app = groupsSettings()
        let new = app.buttons[GroupsUnderTest.newGroup]
        XCTAssertTrue(groupsReveal(app, new, "NEW GROUP"), "the Groups card has no new group")
        new.tap()
        let field = roomsField(app, GroupsUnderTest.nameField)
        XCTAssertTrue(groupsReveal(app, field, "THE NAME FIELD"), "no name field")
        field.tap()
        app.typeText("Radio Room")
        for room in ["garage", "office"] {
            let pick = app.buttons.matching(identifier: GroupsUnderTest.pick(room)).firstMatch
            XCTAssertTrue(groupsReveal(app, pick, "THE \(room.uppercased()) PICK"), "no \(room) to pick")
            pick.tap()
        }
        let save = app.buttons[GroupsUnderTest.save]
        XCTAssertTrue(groupsReveal(app, save, "SAVE"), "no save")
        save.tap()
        XCTAssertTrue(roomsWaitFor(app, GroupsUnderTest.radioSentence), "the 400 was not said as the server said it")
        evidence(app, "groups-11-400")
        XCTAssertTrue(app.buttons[GroupsUnderTest.save].exists, "the form closed on a refused name")
    }

    /// Rename a group, then delete it after the question.
    @MainActor
    func testGroups_SettingsRenamesAndDeletesAGroup() throws {
        let app = groupsSettings()
        let edit = app.buttons[GroupsUnderTest.editUpstairs]
        XCTAssertTrue(groupsReveal(app, edit, "EDIT UPSTAIRS"), "Upstairs has no edit")
        edit.tap()
        let field = roomsField(app, GroupsUnderTest.nameField)
        XCTAssertTrue(groupsReveal(app, field, "THE NAME FIELD"), "no name field")
        // At the field's end, so the deletes take the whole old name.
        field.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
        let typed = (field.value as? String) ?? ""
        app.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: max(typed.count, 8)))
        app.typeText("Top floor")
        let save = app.buttons[GroupsUnderTest.save]
        XCTAssertTrue(groupsReveal(app, save, "SAVE"), "no save")
        save.tap()
        XCTAssertTrue(roomsWaitFor(app, GroupsUnderTest.savedTopFloor), "the rename said nothing")
        evidence(app, "groups-12-renamed")

        let delete = app.buttons["delete Top floor"]
        XCTAssertTrue(groupsReveal(app, delete, "DELETE TOP FLOOR"), "the renamed group has no delete")
        delete.tap()
        let alert = app.alerts["Delete the group Top floor?"].firstMatch
        XCTAssertTrue(alert.waitForExistence(timeout: 10), "deleting asked nothing — tree follows\n" + app.debugDescription)
        evidence(app, "groups-13-delete-asks")
        alert.buttons[GroupsUnderTest.delete].tap()
        XCTAssertTrue(roomsWaitFor(app, "group Top floor deleted"), "deleting said nothing")
        XCTAssertTrue(roomsWaitGone(app, "delete Top floor"), "the deleted group is still listed")
    }

    // MARK: - who is offered nothing

    /// The server says this phone is not a household device: no menu on
    /// Home, no card in Settings, and no error anywhere.
    @MainActor
    func testGroups_ARefusedPhoneIsOfferedNothing() throws {
        let app = groupsHome("{\"mode\":\"refused\"}")
        groupsNoMenu(app, "on a phone the server refused")
        evidence(app, "groups-14-refused-home")
        let settings = groupsSettings("{\"mode\":\"refused\"}")
        groupsNoCard(settings, "on a phone the server refused")
        evidence(settings, "groups-15-refused-settings")
    }

    /// A server older than groups (404): nothing, silently.
    @MainActor
    func testGroups_AnOlderServerShowsNothing() throws {
        let app = groupsHome("{\"mode\":\"older\"}")
        groupsNoMenu(app, "on a server older than groups")
        evidence(app, "groups-16-older-home")
        let settings = groupsSettings("{\"mode\":\"older\"}")
        groupsNoCard(settings, "on a server older than groups")
        evidence(settings, "groups-17-older-settings")
    }

    /// A guest (an unpaired phone) is offered no group control.
    @MainActor
    func testGroups_AGuestIsOfferedNothing() throws {
        let app = groupsSettings("{\"mode\":\"on\"}", paired: false)
        _ = waitPastTheAutoRaisedPairing(app, for: app.buttons[GroupsUnderTest.newGroup], timeout: 10)
        groupsNoCard(app, "to a guest")
        evidence(app, "groups-18-guest")
    }
}
