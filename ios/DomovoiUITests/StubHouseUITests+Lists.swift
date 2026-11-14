import XCTest

/// Lists (Android 3a3b6422), against the stub house: reached from Home's
/// `tools` group, three items added quickly, one crossed off, a rename onto a
/// taken name refused in the server's words, a list made and deleted (after
/// the confirm), an item said to a room arriving by the `lists.changed` push,
/// and Apple's audit judged the way the nav grid's and the rooms roster's are.
///
/// The house's lists are the stub's (`design-notes/ios/batch-2026-11-12/
/// lists/stubhouse.patch`): To-do and Shopping, reset by every case through
/// `/api/_test/lists-reset` — the run shares one house and the cases write to
/// it. The phone keeps the list it last opened per server, so every case taps
/// the chip it means rather than trusting which one opens.
///
/// **The connection sheet is never opened**: every case launches with the
/// server saved. Every miss prints the tree.
extension StubHouseUITests {

    /// The words matched on, spelt out rather than imported (this suite's
    /// convention: a query that followed a renamed constant would stop
    /// testing what it claims to). `ListsStrings` and the stub's seed.
    enum ListsUnderTest {
        static let title = "Lists"
        static let toolsHeading = "tools"
        static let todo = "To-do"
        static let shopping = "Shopping"
        static let freshSub = "2 lists · 0 things to go"
        static let todoPlaceholder = "add to to-do"
        static let renameList = "rename list"
        static let deleteList = "delete list"
        static let newList = "new list"
        static let make = "make"
        static let save = "save"
        static let taken = "there's already a list called Shopping"
        static let camping = "Camping"
        static let campingTyped = "Camping list"
        static let deleteCampingTitle = "Delete Camping?"
        static let deleteEmptyBody = "The list goes for good. This can't be undone."
        static let deleteConfirm = "Delete list"
        static let deletedCamping = "deleted Camping"
        static let three = ["Milk", "Eggs", "Bread"]
        static let threeToGo = "3 things to go"
        static let afterCrossing = "2 things to go · 1 crossed off"
        static let crossedOffHeading = "crossed off · 1"
        static let voiced = "Oat milk"
        static let voicedWho = "Alex · kitchen"
        static func crossOff(_ text: String) -> String { "cross off \(text)" }
        static func putBack(_ text: String) -> String { "put \(text) back on the list" }
    }

    // MARK: - getting there

    /// Reset the house's lists, launch, and open Lists from Home's grid —
    /// the `Lists` tile under the `tools` heading. True once the screen's add
    /// box is up (only a loaded list draws it).
    @MainActor
    func openLists(_ mode: String = StubHouseUITests.ordinaryTypeSize) -> (app: XCUIApplication, landed: Bool) {
        control("lists-reset", "{}", port: 6601, what: "the lists seed")
        let app = launch(mode: mode)
        let tile = app.buttons[ListsUnderTest.title]
        guard tile.waitForExistence(timeout: 30) else {
            print("=== NO LISTS TILE ON HOME — tree follows ===")
            print(app.debugDescription)
            return (app, false)
        }
        guard navReach(app, tile) else { return (app, false) }
        tile.tap()
        let field = app.textFields["lists.add"]
        guard field.waitForExistence(timeout: 30) else {
            print("=== LISTS NEVER DREW ITS ADD BOX — tree follows ===")
            print(app.debugDescription)
            return (app, false)
        }
        return (app, true)
    }

    /// A list's chip.
    @MainActor
    func listsChip(_ app: XCUIApplication, _ name: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "identifier == %@ AND label == %@", "lists.chip", name)).firstMatch
    }

    /// Tap a list's chip and wait for its card's title.
    @MainActor
    func listsPick(_ app: XCUIApplication, _ name: String) -> Bool {
        let chip = listsChip(app, name)
        guard chip.waitForExistence(timeout: 20) else {
            print("=== NO `\(name)` CHIP — tree follows ===")
            print(app.debugDescription)
            return false
        }
        chip.tap()
        let title = app.staticTexts.matching(NSPredicate(format: "identifier == %@ AND label == %@", "lists.title", name))
            .firstMatch
        guard title.waitForExistence(timeout: 20) else {
            print("=== `\(name)` NEVER OPENED — tree follows ===")
            print(app.debugDescription)
            return false
        }
        return true
    }

    /// Type each of `items` into the add box and press return, tapping the
    /// box first each time so a keyboard that left cannot swallow the next.
    @MainActor
    func listsAdd(_ app: XCUIApplication, _ items: [String]) {
        let field = app.textFields["lists.add"]
        for text in items {
            field.tap()
            field.typeText(text + "\n")
        }
    }

    /// Wait for a staticText with exactly `label`; print the tree on a miss.
    @MainActor
    func listsSays(_ app: XCUIApplication, _ label: String, timeout: Double = 20) -> Bool {
        let said = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label == %@", label)).firstMatch
        if said.waitForExistence(timeout: timeout) { return true }
        print("=== NOTHING SAYS “\(label)” — tree follows ===")
        print(app.debugDescription)
        return false
    }

    /// Wait for a button with exactly `label`; print the tree on a miss.
    @MainActor
    func listsButton(_ app: XCUIApplication, _ label: String, timeout: Double = 20) -> XCUIElement? {
        let button = app.buttons[label]
        if button.waitForExistence(timeout: timeout) { return button }
        print("=== NO `\(label)` BUTTON — tree follows ===")
        print(app.debugDescription)
        return nil
    }

    // MARK: - the cases

    /// Home's grid has `Lists` in the `tools` run, after Calendar; tapping it
    /// opens the screen on the house's two seeded lists.
    @MainActor
    func testLists_OpensFromTools() throws {
        control("lists-reset", "{}", port: 6601, what: "the lists seed")
        let app = launch()
        let tile = app.buttons[ListsUnderTest.title]
        let tools = app.staticTexts[ListsUnderTest.toolsHeading]
        guard tile.waitForExistence(timeout: 30), tools.waitForExistence(timeout: 10) else {
            print("=== NO LISTS TILE OR NO TOOLS HEADING — tree follows ===")
            print(app.debugDescription)
            return XCTFail("Home's grid has no Lists tile under tools")
        }
        XCTAssertTrue(navReach(app, tile), "the Lists tile never came wholly into view")
        evidence(app, "lists-01-home-tile")
        let calendar = app.buttons["Calendar"]
        print("LISTS TILE tools=\(tools.frame) calendar=\(calendar.frame) lists=\(tile.frame)")
        XCTAssertLessThan(tools.frame.minY, tile.frame.minY, "the Lists tile is not under the tools heading")
        if calendar.exists {
            XCTAssertTrue(
                calendar.frame.minY < tile.frame.minY
                    || (abs(calendar.frame.minY - tile.frame.minY) < 1 && calendar.frame.minX < tile.frame.minX),
                "Lists is not after Calendar in the tools run"
            )
        }
        tile.tap()
        let field = app.textFields["lists.add"]
        let drew = field.waitForExistence(timeout: 30)
        evidence(app, "lists-01-opened")
        if !drew {
            print("=== LISTS NEVER DREW ITS ADD BOX — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertTrue(drew, "the Lists screen did not draw an open list")
        XCTAssertTrue(listsSays(app, ListsUnderTest.freshSub), "the header's sub-line is not the seed's")
        XCTAssertTrue(listsChip(app, ListsUnderTest.todo).exists, "no To-do chip")
        XCTAssertTrue(listsChip(app, ListsUnderTest.shopping).exists, "no Shopping chip")
        XCTAssertTrue(app.buttons[ListsUnderTest.newList].exists, "no `new list` chip")
    }

    /// Three items typed one after another, each sent with return: the box
    /// empties at once, so none is lost, and they land in the order typed.
    @MainActor
    func testLists_AddThreeItems() throws {
        let (app, landed) = openLists()
        XCTAssertTrue(landed, "Lists did not open")
        XCTAssertTrue(listsPick(app, ListsUnderTest.todo), "To-do did not open")
        listsAdd(app, ListsUnderTest.three)
        var tops: [CGFloat] = []
        for text in ListsUnderTest.three {
            guard let box = listsButton(app, ListsUnderTest.crossOff(text)) else {
                evidence(app, "lists-02-missing-\(text)")
                return XCTFail("“\(text)” never landed on the list")
            }
            tops.append(box.frame.minY)
        }
        evidence(app, "lists-02-three-added")
        print("LISTS ADDED tops=\(tops)")
        XCTAssertEqual(tops, tops.sorted(), "the three items are not in the order typed: \(tops)")
        XCTAssertTrue(listsSays(app, ListsUnderTest.threeToGo), "the card does not count three to go")
        // An empty field reads back as its placeholder.
        let left = app.textFields["lists.add"].value as? String ?? ""
        XCTAssertTrue(left.isEmpty || left == ListsUnderTest.todoPlaceholder, "the add box kept “\(left)” after the adds")
    }

    /// One crossed off: struck into the crossed-off section, with its box
    /// now offering to put it back.
    @MainActor
    func testLists_CrossOneOff() throws {
        let (app, landed) = openLists()
        XCTAssertTrue(landed, "Lists did not open")
        XCTAssertTrue(listsPick(app, ListsUnderTest.todo), "To-do did not open")
        listsAdd(app, ListsUnderTest.three)
        guard let milk = listsButton(app, ListsUnderTest.crossOff("Milk")),
              listsButton(app, ListsUnderTest.crossOff("Bread")) != nil
        else { return XCTFail("the items never landed") }
        milk.tap()
        let back = listsButton(app, ListsUnderTest.putBack("Milk"))
        evidence(app, "lists-03-crossed-off")
        XCTAssertNotNil(back, "Milk's box does not offer to put it back")
        XCTAssertTrue(listsSays(app, ListsUnderTest.crossedOffHeading), "no `crossed off · 1` section")
        XCTAssertTrue(listsSays(app, ListsUnderTest.afterCrossing), "the card's counts did not move")
        XCTAssertFalse(app.buttons[ListsUnderTest.crossOff("Milk")].exists, "Milk is still offered to cross off")
    }

    /// Renaming To-do onto "Groceries" — Shopping's key — is refused, and the
    /// server's own sentence is what the person reads; the field stays open.
    @MainActor
    func testLists_RenameOntoATakenNameSaysWhy() throws {
        let (app, landed) = openLists()
        XCTAssertTrue(landed, "Lists did not open")
        XCTAssertTrue(listsPick(app, ListsUnderTest.todo), "To-do did not open")
        guard let rename = listsButton(app, ListsUnderTest.renameList) else { return XCTFail("no rename") }
        rename.tap()
        let field = app.textFields["lists.rename"]
        guard field.waitForExistence(timeout: 15) else {
            print("=== NO RENAME FIELD — tree follows ===")
            print(app.debugDescription)
            return XCTFail("rename opened no field")
        }
        field.tap()
        clearField(app)
        app.typeText("Groceries")
        guard let save = listsButton(app, ListsUnderTest.save) else { return XCTFail("no save") }
        save.tap()
        let said = listsSays(app, ListsUnderTest.taken)
        evidence(app, "lists-04-name-taken")
        XCTAssertTrue(said, "the refusal was not said in the server's words")
        XCTAssertTrue(field.exists, "the rename field closed on a refused name")
        XCTAssertTrue(listsChip(app, ListsUnderTest.todo).exists, "To-do was renamed after all")
    }

    /// `new list` → "Camping list" → make: the server keeps "Camping" and the
    /// new list opens; its bin asks first, and only `Delete list` deletes.
    @MainActor
    func testLists_MakeAndDeleteAList() throws {
        let (app, landed) = openLists()
        XCTAssertTrue(landed, "Lists did not open")
        guard let new = listsButton(app, ListsUnderTest.newList) else { return XCTFail("no new list chip") }
        new.tap()
        let field = app.textFields["lists.newName"]
        guard field.waitForExistence(timeout: 15) else {
            print("=== NO NAME FIELD — tree follows ===")
            print(app.debugDescription)
            return XCTFail("`new list` opened no field")
        }
        field.tap()
        field.typeText(ListsUnderTest.campingTyped)
        guard let make = listsButton(app, ListsUnderTest.make) else { return XCTFail("no make") }
        make.tap()
        let opened = app.staticTexts.matching(
            NSPredicate(format: "identifier == %@ AND label == %@", "lists.title", ListsUnderTest.camping)
        ).firstMatch
        let made = opened.waitForExistence(timeout: 20)
        evidence(app, "lists-05-made")
        if !made {
            print("=== CAMPING NEVER OPENED — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertTrue(made, "the new list did not open as the server named it")
        XCTAssertTrue(listsChip(app, ListsUnderTest.camping).exists, "no Camping chip")

        guard let bin = listsButton(app, ListsUnderTest.deleteList) else { return XCTFail("no delete") }
        bin.tap()
        let alert = app.alerts.firstMatch
        let asked = alert.waitForExistence(timeout: 15)
        evidence(app, "lists-05-confirm")
        if !asked {
            print("=== NO CONFIRM — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertTrue(asked, "the delete was not confirmed first")
        XCTAssertTrue(alert.staticTexts[ListsUnderTest.deleteCampingTitle].exists, "the confirm does not name the list: \(alert.debugDescription)")
        XCTAssertTrue(alert.staticTexts[ListsUnderTest.deleteEmptyBody].exists, "the confirm's body is not Android's: \(alert.debugDescription)")
        XCTAssertTrue(listsChip(app, ListsUnderTest.camping).exists, "the list went before the confirm")
        alert.buttons[ListsUnderTest.deleteConfirm].tap()
        let said = listsSays(app, ListsUnderTest.deletedCamping)
        let gone = listsChip(app, ListsUnderTest.camping).waitForNonExistence(timeout: 20)
        evidence(app, "lists-05-deleted")
        XCTAssertTrue(said, "nothing was said about the delete")
        XCTAssertTrue(gone, "the Camping chip is still there")
    }

    /// An item said to the kitchen (the stub's voice add, then its
    /// `lists.changed` push on /ws/state) appears on the open list without a
    /// pull, with who said it and where.
    @MainActor
    func testLists_ALivePushShowsANewItem() throws {
        let (app, landed) = openLists()
        XCTAssertTrue(landed, "Lists did not open")
        XCTAssertTrue(listsPick(app, ListsUnderTest.shopping), "Shopping did not open")
        XCTAssertFalse(app.buttons[ListsUnderTest.crossOff(ListsUnderTest.voiced)].exists, "the item was there already")
        control(
            "lists-voice-add",
            #"{"list": "Shopping", "text": "Oat milk", "name": "Alex", "room": "kitchen"}"#,
            port: 6601,
            what: "a voice add to Shopping"
        )
        let arrived = listsButton(app, ListsUnderTest.crossOff(ListsUnderTest.voiced), timeout: 25) != nil
        evidence(app, "lists-06-pushed")
        XCTAssertTrue(arrived, "the pushed item never appeared")
        XCTAssertTrue(listsSays(app, ListsUnderTest.voicedWho, timeout: 5), "the who-line is not Android's")
    }

    /// Apple's audit on a list with an item said to a room, two typed, and
    /// one crossed off — classified by signature (``floorAndRest(_:)``), and
    /// judged as `testNav_TheGroupedGridAudits` is: when the audit moved the
    /// page, every pixel-sampled finding is read off the picture where its
    /// element was drawn (``roomsAssertOwnInk``, under 4.5:1 fails) and the
    /// audit is retaken where the page settled; a retaken finding against an
    /// element the page clips is measured whole (``navClippedMeasured``).
    @MainActor
    func testLists_Audits() throws {
        control("lists-reset", "{}", port: 6601, what: "the lists seed")
        control(
            "lists-voice-add",
            #"{"list": "To-do", "text": "Call the plumber", "name": "Priya", "room": "kitchen"}"#,
            port: 6601,
            what: "a voice add to To-do"
        )
        let app = launch()
        let tile = app.buttons[ListsUnderTest.title]
        guard tile.waitForExistence(timeout: 30), navReach(app, tile) else {
            print("=== NO LISTS TILE — tree follows ===")
            print(app.debugDescription)
            return XCTFail("no Lists tile to open")
        }
        tile.tap()
        XCTAssertTrue(listsPick(app, ListsUnderTest.todo), "To-do did not open")
        listsAdd(app, ["Milk", "Eggs"])
        guard let eggs = listsButton(app, ListsUnderTest.crossOff("Eggs")) else { return XCTFail("Eggs never landed") }
        eggs.tap()
        XCTAssertNotNil(listsButton(app, ListsUnderTest.putBack("Eggs")), "Eggs was not crossed off")
        // The keyboard down, so the page is what is audited: return on the
        // empty box lets it go.
        let field = app.textFields["lists.add"]
        field.tap()
        field.typeText("\n")
        if !app.keyboards.firstMatch.waitForNonExistence(timeout: 10) {
            print("=== THE KEYBOARD STAYED UP — tree follows ===")
            print(app.debugDescription)
        }
        evidence(app, "lists-07-audit")

        let before = roomsWindowShot(app)
        let picture = app.screenshot().image.cgImage
        let first = try auditIssues(app, "lists")
        let firstSplit = Self.floorAndRest(first)
        let settled = roomsWindowShot(app)
        let moves = Self.roomsPageMoves(from: before, to: settled)
        let other = firstSplit.rest.filter { !Self.roomsSamplesPixels($0) }
        var pixelLines = firstSplit.rest.filter(Self.roomsSamplesPixels)
        var judgedOn = before
        if moves.isEmpty {
            print("LISTS AUDIT: the audit left the page where it was — one pass is the measurement")
        } else {
            print(
                "LISTS AUDIT: the audit moved the page — "
                    + moves.prefix(6).map { "'\($0.label)' by \($0.dy)" }.joined(separator: ", ")
                    + " — so its \(pixelLines.count) pixel-sampled finding(s) are read where drawn and retaken"
            )
            roomsAssertOwnInk(pixelLines, on: before, picture: picture, window: app.frame, name: "lists")
            evidence(app, "lists-07-audit-settled")
            let retake = try auditIssues(app, "lists-settled")
            let movedAgain = Self.roomsPageMoves(from: settled, to: roomsWindowShot(app))
            XCTAssertTrue(
                movedAgain.isEmpty,
                "the retaken Lists audit moved the page as well — "
                    + movedAgain.prefix(6).map { "'\($0.label)' by \($0.dy)" }.joined(separator: ", ")
            )
            let retakeSplit = Self.floorAndRest(retake)
            for line in retakeSplit.rest where !Self.roomsSamplesPixels(line) {
                print("LISTS AUDIT retake (judged in the first pass) :: \(line)")
            }
            pixelLines = retakeSplit.rest.filter(Self.roomsSamplesPixels)
            judgedOn = settled
        }
        let clipped = navClippedMeasured(app, pixelLines, on: judgedOn)
        let rest = other + clipped.kept
        print(
            "LISTS AUDIT floor=\(firstSplit.floor) rest=\(firstSplit.rest.count) "
                + "moved=\(!moves.isEmpty) clipped-measured=\(clipped.allowed.count) ours=\(rest.count)"
        )
        XCTAssertTrue(
            rest.isEmpty,
            "Apple's audit has \(rest.count) attributed finding(s) on Lists: " + rest.joined(separator: " | ")
        )
    }
}
