import XCTest

/// The room queues (Android 21b5ccfb, 46dc0d35, cb780e90, bd274c2f,
/// 6d14aa91): search to add, Up next in a room, Play next / Add to queue,
/// and a room's queue one tap from the room.
///
/// Every case is named `testQueues_…` and every piece of evidence
/// `queues-…`. The house is the MusicMore seed plus this unit's stub patch
/// (`design-notes/ios/batch-2026-11-14/queues/stubhouse.patch`):
///
/// * **parlour** plays `Veldrassen Overture`, then `Pellucid Marrow`
///   somebody asked for, then two of the room's own picks by Nidra Vane —
///   `Thrumming Ashkeep` ("same artist") and `Quillon Drift` ("from
///   Longwater Drive") — under a library session, autoplay on.
/// * **attic** is idle with 130 songs, `Gloamwick 001`…`Gloamwick 130`.
/// * **kitchen** is Home's one online room, playing `Harvest Moon`.
///
/// No case here opens the connection sheet or taps "use a different server".
extension StubHouseUITests {

    /// Music, on the room queue tab, with parlour's queue drawn.
    @MainActor
    func openRoomQueueTab() -> (app: XCUIApplication, landed: Bool) {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        guard landed else { return (app, false) }
        guard musicMoreTapTab(app, QueuesUnderTest.tabQueue) else { return (app, false) }
        let head = app.staticTexts[QueuesUnderTest.upNext]
        guard musicMoreScroll(app, to: head) else {
            print("=== THE ROOM QUEUE TAB NEVER DREW PARLOUR'S UP NEXT — tree follows ===")
            print(app.debugDescription)
            return (app, false)
        }
        return (app, true)
    }

    /// A room card's queue button, and the sheet it opens.
    @MainActor
    func openRoomQueueSheet(_ room: String) -> (app: XCUIApplication, landed: Bool) {
        musicMoreReset()
        let (app, landed) = openMusicMore(roomsOpen: true)
        guard landed else { return (app, false) }
        let button = app.buttons[QueuesUnderTest.queueOf(room)]
        guard musicMoreReveal(app, button) else {
            print("=== NO '\(QueuesUnderTest.queueOf(room))' BUTTON ON MUSIC — tree follows ===")
            print(app.debugDescription)
            return (app, false)
        }
        button.tap()
        let title = app.navigationBars.staticTexts[QueuesUnderTest.queueOf(room)]
        guard title.waitForExistence(timeout: 20) else {
            print("=== THE QUEUE SHEET NEVER OPENED — tree follows ===")
            print(app.debugDescription)
            return (app, false)
        }
        return (app, true)
    }

    // MARK: - the room queue tab

    /// The room's own picks are marked under an "up next" head that says
    /// what the mix is built from and holds the room's autoplay switch; a
    /// pick carries why it was picked and a "less like this".
    @MainActor
    func testQueues_TheRoomQueueShowsUpNextAndItsPicks() throws {
        let (app, landed) = openRoomQueueTab()
        XCTAssertTrue(landed, "the room queue tab never drew parlour's queue")
        evidence(app, "queues-10-up-next")

        XCTAssertTrue(
            containing(app, QueuesUnderTest.basis).waitForExistence(timeout: 20),
            "the head does not say what the mix is built from — tree follows\n" + app.debugDescription
        )
        XCTAssertTrue(
            app.switches[QueuesUnderTest.autoplayLabel].exists,
            "the room's autoplay switch is missing — tree follows\n" + app.debugDescription
        )
        XCTAssertTrue(
            containing(app, QueuesUnderTest.sameArtist).exists,
            "a pick does not say why the room picked it — tree follows\n" + app.debugDescription
        )
        XCTAssertTrue(
            app.buttons[QueuesUnderTest.lessLikeNidra].exists,
            "a waiting pick has no `less like this` — tree follows\n" + app.debugDescription
        )
        XCTAssertTrue(
            app.buttons[QueuesUnderTest.saveAsPlaylist].exists,
            "the household's queue has no `save as playlist` — tree follows\n" + app.debugDescription
        )
    }

    /// "Less like this" takes the artist's picks out of the view at once.
    @MainActor
    func testQueues_LessLikeThisTakesTheArtistsPicksOut() throws {
        let (app, landed) = openRoomQueueTab()
        XCTAssertTrue(landed, "the room queue tab never drew parlour's queue")
        let less = app.buttons[QueuesUnderTest.lessLikeNidra].firstMatch
        XCTAssertTrue(less.waitForExistence(timeout: 20), "no `less like this` — tree follows\n" + app.debugDescription)
        musicMoreScrollOnScreen(app, less)
        less.tap()

        let pill = containing(app, QueuesUnderTest.sameArtist)
        XCTAssertTrue(
            waitUntilGone(pill, timeout: 20),
            "the pick is still drawn after `less like this` — tree follows\n" + app.debugDescription
        )
        XCTAssertFalse(app.buttons[QueuesUnderTest.lessLikeNidra].exists, "the artist's other pick is still offered — tree follows\n" + app.debugDescription)
        evidence(app, "queues-11-less-like-this")
    }

    /// The search box adds a song to the room's queue, and says so.
    @MainActor
    func testQueues_SearchAddsASongToTheRoomsQueue() throws {
        let (app, landed) = openRoomQueueTab()
        XCTAssertTrue(landed, "the room queue tab never drew parlour's queue")
        let field = app.textFields[QueuesUnderTest.searchLabel]
        XCTAssertTrue(musicMoreReveal(app, field), "no search box over the room's queue — tree follows\n" + app.debugDescription)
        field.tap()
        field.typeText(QueuesUnderTest.sablewindTerm)

        let add = app.buttons[QueuesUnderTest.addSablewind]
        XCTAssertTrue(
            add.waitForExistence(timeout: 20),
            "the search found nothing to add — tree follows\n" + app.debugDescription
        )
        add.tap()
        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(
                format: "identifier == %@ AND label BEGINSWITH %@",
                QueuesUnderTest.addStatusID, QueuesUnderTest.addedInParlour
            )).firstMatch.waitForExistence(timeout: 20),
            "the box did not say where the song went — tree follows\n" + app.debugDescription
        )
        evidence(app, "queues-12-search-added")
    }

    // MARK: - one tap from the room

    /// A room card's queue button opens that room's queue in a sheet, with
    /// the same rows and the search box.
    @MainActor
    func testQueues_ARoomCardOpensItsQueueInASheet() throws {
        let (app, landed) = openRoomQueueSheet(QueuesUnderTest.parlour)
        XCTAssertTrue(landed, "parlour's queue sheet never opened")
        // "added by Priya's phone" is on a queue row and nowhere else: the
        // song's title is also a library row behind the sheet.
        XCTAssertTrue(
            containing(app, QueuesUnderTest.addedByPriya).waitForExistence(timeout: 20),
            "the sheet does not list the room's queue — tree follows\n" + app.debugDescription
        )
        XCTAssertTrue(app.textFields[QueuesUnderTest.searchLabel].exists, "the sheet has no search box — tree follows\n" + app.debugDescription)
        evidence(app, "queues-20-sheet")
        app.buttons[MusicMoreUnderTest.close].tap()
    }

    /// The keyboard does not cover the sheet's search box (46dc0d35).
    @MainActor
    func testQueues_TheKeyboardDoesNotCoverTheSheetsSearchBox() throws {
        let (app, landed) = openRoomQueueSheet(QueuesUnderTest.parlour)
        XCTAssertTrue(landed, "parlour's queue sheet never opened")
        let field = app.textFields[QueuesUnderTest.searchLabel]
        XCTAssertTrue(field.waitForExistence(timeout: 20), "no search box — tree follows\n" + app.debugDescription)
        field.tap()
        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.waitForExistence(timeout: 10), "the keyboard never came up — tree follows\n" + app.debugDescription)
        field.typeText("Qu")
        evidence(app, "queues-21-keyboard")
        XCTAssertLessThanOrEqual(
            field.frame.maxY, keyboard.frame.minY + 0.5,
            "the keyboard covers the search box: field \(field.frame), keyboard \(keyboard.frame) — tree follows\n" + app.debugDescription
        )
    }

    /// A tap on a row plays it now — the room's own jump — and the sheet says
    /// so inside itself. The attic's songs are in no library row, so the row
    /// tapped can only be the sheet's.
    @MainActor
    func testQueues_ATapOnARowPlaysItNow() throws {
        let (app, landed) = openRoomQueueSheet(QueuesUnderTest.attic)
        XCTAssertTrue(landed, "attic's queue sheet never opened")
        let row = containing(app, QueuesUnderTest.gloamwickSecond)
        XCTAssertTrue(row.waitForExistence(timeout: 20), "no row to tap — tree follows\n" + app.debugDescription)
        row.tap()
        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(
                format: "identifier == %@ AND label BEGINSWITH %@",
                QueuesUnderTest.roomStatusID, QueuesUnderTest.playingGloamwick
            )).firstMatch.waitForExistence(timeout: 20),
            "the jump said nothing inside the sheet — tree follows\n" + app.debugDescription
        )
        evidence(app, "queues-22-play-now")
    }

    /// A long queue is drawn a hundred rows at a time, more as it scrolls.
    @MainActor
    func testQueues_ALongQueueLoadsMoreAsItScrolls() throws {
        let (app, landed) = openRoomQueueSheet(QueuesUnderTest.attic)
        XCTAssertTrue(landed, "attic's queue sheet never opened")
        XCTAssertTrue(
            containing(app, QueuesUnderTest.gloamwickFirst).waitForExistence(timeout: 20),
            "the attic's queue never drew — tree follows\n" + app.debugDescription
        )
        let last = containing(app, QueuesUnderTest.gloamwickLast)
        // The sheet's scroll view, not the Music page's behind it.
        let sheet = app.scrollViews.containing(
            NSPredicate(format: "label CONTAINS %@", QueuesUnderTest.gloamwickFirst)
        ).firstMatch
        for _ in 0..<60 where !last.exists {
            sheet.swipeUp(velocity: .fast)
        }
        XCTAssertTrue(last.exists, "the 130th song never drew — tree follows\n" + app.debugDescription)
        evidence(app, "queues-23-long-queue")
    }

    /// Home's room row carries the queue button for an online room.
    @MainActor
    func testQueues_AHomeRoomRowOpensItsQueue() throws {
        musicMoreReset()
        let app = launch()
        let button = app.buttons[QueuesUnderTest.queueOf(QueuesUnderTest.kitchen)]
        XCTAssertTrue(
            button.waitForExistence(timeout: 40),
            "the kitchen's row has no queue button — tree follows\n" + app.debugDescription
        )
        XCTAssertFalse(
            app.buttons[QueuesUnderTest.queueOf("study")].exists,
            "an offline room offers a queue — tree follows\n" + app.debugDescription
        )
        button.tap()
        XCTAssertTrue(
            containing(app, QueuesUnderTest.harvestMoon).waitForExistence(timeout: 20),
            "the kitchen's queue sheet never listed its queue — tree follows\n" + app.debugDescription
        )
        evidence(app, "queues-30-home")
    }

    // MARK: - Play next / Add to queue

    /// The track sheet's room buttons: play next in the picked room, said
    /// inside the sheet.
    @MainActor
    func testQueues_TheTrackSheetPlaysASongNextInARoom() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew — tree follows\n" + app.debugDescription)
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.sablewind), "the track sheet never opened")
        let next = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", QueuesUnderTest.playNextIn)).firstMatch
        XCTAssertTrue(next.waitForExistence(timeout: 20), "no `play next in <room>` — tree follows\n" + app.debugDescription)
        next.tap()
        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(
                format: "identifier == %@ AND label BEGINSWITH %@",
                QueuesUnderTest.trackStatusID, QueuesUnderTest.playingNextIn
            )).firstMatch.waitForExistence(timeout: 20),
            "the sheet did not say where the song went — tree follows\n" + app.debugDescription
        )
        XCTAssertTrue(app.buttons[QueuesUnderTest.addToQueue].exists, "this device's `add to queue` is missing — tree follows\n" + app.debugDescription)
        evidence(app, "queues-40-track-sheet")
        closeSheet(app)
    }
}

/// The words these cases look for, each the app's own.
enum QueuesUnderTest {
    static let tabQueue = "room queue"
    static let parlour = "parlour"
    static let attic = "attic"
    static let kitchen = "kitchen"
    static func queueOf(_ room: String) -> String { "\(room)'s queue" }

    static let upNext = "up next"
    static let basis = "based on Veldrassen Overture"
    static let autoplayLabel = "autoplay in parlour: keep playing similar songs when the queue ends"
    static let sameArtist = "same artist"
    static let lessLikeNidra = "less like this: no more Nidra Vane in this mix"
    static let saveAsPlaylist = "save as playlist"

    static let searchLabel = "search the library to add to the queue"
    static let sablewindTerm = "Sablewind Fig"
    static let addSablewind = "add Sablewind Figure to the queue"
    static let addStatusID = "queue-add-status"
    static let addedInParlour = "Added in parlour: Sablewind Figure"

    static let addedByPriya = "added by Priya's phone"
    static let roomStatusID = "room-queue-status"
    static let playingGloamwick = "playing \"Gloamwick 002\" in attic"
    static let gloamwickFirst = "Gloamwick 001"
    static let gloamwickSecond = "Gloamwick 002"
    static let gloamwickLast = "Gloamwick 130"
    static let harvestMoon = "Harvest Moon"

    static let playNextIn = "play next in "
    static let playingNextIn = "playing next in "
    static let trackStatusID = "track-sheet-queue-status"
    static let addToQueue = "add to queue"
}
