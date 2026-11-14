import XCTest

/// Where the music and queues units meet (batch 2026-11-14, the integration
/// fix): the genre sheet's queue actions, a song row's Play next / Add to
/// queue, and "play in <room>" carrying this phone, its listener and the list
/// a song was opened from.
///
/// Every case is named `testIntegration_…` and every piece of evidence
/// `integration-…`. The house is the combined stub plus
/// `design-notes/ios/batch-2026-11-14/integration/stubhouse.patch`, which
/// writes down each `POST /api/music/play-track` body
/// (`GET /api/_test/integration-sent`); the room-queue writes are the queues
/// patch's (`GET /api/_test/queues-sent`).
///
/// No case here opens the connection sheet or taps "use a different server".
extension StubHouseUITests {

    // MARK: - what the stub was sent

    /// A stub control route's JSON, or nil when it did not answer.
    @MainActor
    func integrationRead(_ route: String, port: Int = 6601) -> Any? {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/_test/\(route)")!)
        request.httpMethod = "GET"
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        let answered = expectation(description: "the stub answered \(route)")
        var out: Any?
        URLSession.shared.dataTask(with: request) { data, _, _ in
            if let data { out = try? JSONSerialization.jsonObject(with: data) }
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 15)
        return out
    }

    /// The play-track bodies the phone sent, waiting up to `timeout` for one.
    @MainActor
    func integrationPlayTracks(timeout: Double = 15) -> [[String: Any]] {
        let end = Date().addingTimeInterval(timeout)
        repeat {
            let sent = (integrationRead("integration-sent") as? [String: Any])?["play_track"] as? [[String: Any]] ?? []
            if !sent.isEmpty { return sent }
            Thread.sleep(forTimeInterval: 0.5)
        } while Date() < end
        return []
    }

    /// The room-queue adds the phone sent to `room`.
    @MainActor
    func integrationQueueAdds(_ room: String, timeout: Double = 15) -> [[String: Any]] {
        let end = Date().addingTimeInterval(timeout)
        repeat {
            let sent = (integrationRead("queues-sent") as? [[String: Any]] ?? []).filter {
                ($0["room"] as? String) == room && ($0["action"] as? String) == "add"
            }
            let bodies = sent.compactMap { $0["body"] as? [String: Any] }
            if !bodies.isEmpty { return bodies }
            Thread.sleep(forTimeInterval: 0.5)
        } while Date() < end
        return []
    }

    /// Open the Mossgaze genre's sheet (Thrumming Ashkeep 503, Quillon
    /// Drift 504).
    @MainActor
    func integrationOpenMossgaze() -> (app: XCUIApplication, landed: Bool) {
        musicMoreReset()
        let (app, landed) = openGenresTab()
        guard landed, openGenreSheet(app, MusicGenresUnderTest.mossgazeKey) else { return (app, false) }
        guard app.buttons[IntegrationUnderTest.ashkeepRow].waitForExistence(timeout: 20) else {
            print("=== THE MOSSGAZE SHEET LISTS NO SONG — tree follows ===")
            print(app.debugDescription)
            return (app, false)
        }
        return (app, true)
    }

    /// Open a menu and tap one of its items; false (tree printed) on a miss.
    @MainActor
    func integrationMenu(_ app: XCUIApplication, _ menu: XCUIElement, item: String) -> Bool {
        guard menu.waitForExistence(timeout: 20) else {
            print("=== NO MENU — tree follows ===")
            print(app.debugDescription)
            return false
        }
        musicMoreScrollOnScreen(app, menu)
        menu.tap()
        let choice = app.buttons[item].firstMatch
        guard choice.waitForExistence(timeout: 10) else {
            print("=== THE MENU HAS NO '\(item)' — tree follows ===")
            print(app.debugDescription)
            return false
        }
        choice.tap()
        return true
    }

    // MARK: - the genre sheet's queue actions (A1)

    /// The room's `add to queue` puts the whole genre at the END of the
    /// room's queue, and the sheet (still up) says so.
    @MainActor
    func testIntegration_TheGenreSheetAddsTheGenreToARoomsQueue() throws {
        let (app, landed) = integrationOpenMossgaze()
        XCTAssertTrue(landed, "the Mossgaze sheet never opened")
        let add = app.buttons[IntegrationUnderTest.addToQueue]
        guard add.waitForExistence(timeout: 20) else {
            XCTFail("the genre sheet has no room `add to queue` — tree follows\n" + app.debugDescription)
            return
        }
        musicMoreScrollOnScreen(app, add)
        add.tap()
        XCTAssertTrue(
            sheetSaid(app, startsWith: IntegrationUnderTest.addedMossgazeToParlour),
            "the sheet did not say the genre went to the room's queue — tree follows\n" + app.debugDescription
        )
        evidence(app, "integration-01-genre-to-room-queue")
        XCTAssertTrue(app.buttons[MusicMoreUnderTest.close].exists, "the sheet closed; Android's drawer stays")
        let body = integrationQueueAdds(IntegrationUnderTest.parlour).last
        XCTAssertEqual("end", body?["where"] as? String, "not added at the end: \(String(describing: body))")
        // The genre's ids in the SERVER's order, unshuffled — Android's
        // `queueInRoom` sends `roomTrackIds(ids, shuffle = false)`
        // (`GenresTab.kt:385`, `TrackSets.kt:31`). The server's order is
        // `ORDER BY LOWER(artist), added_at DESC, id DESC`
        // (`music_genres.py:186`); both Mossgaze songs are Nidra Vane's, so
        // the newer 504 comes first (I037 caught this case asking for 503
        // first, which is no order the server gives).
        XCTAssertEqual([504, 503], body?["track_ids"] as? [Int], "\(String(describing: body))")
    }

    /// `queue here` puts the songs loaded so far on this phone's queue.
    @MainActor
    func testIntegration_TheGenreSheetQueuesItsSongsHere() throws {
        let (app, landed) = integrationOpenMossgaze()
        XCTAssertTrue(landed, "the Mossgaze sheet never opened")
        let here = app.buttons[IntegrationUnderTest.queueHere]
        guard here.waitForExistence(timeout: 20) else {
            XCTFail("the genre sheet has no `queue here` — tree follows\n" + app.debugDescription)
            return
        }
        musicMoreScrollOnScreen(app, here)
        here.tap()
        XCTAssertTrue(
            sheetSaid(app, startsWith: IntegrationUnderTest.queuedMossgazeHere),
            "the sheet did not say the songs were queued here — tree follows\n" + app.debugDescription
        )
        evidence(app, "integration-02-genre-queued-here")
    }

    /// A genre song's menu plays it next, and the sheet says where.
    @MainActor
    func testIntegration_AGenreSongPlaysNextFromItsRow() throws {
        let (app, landed) = integrationOpenMossgaze()
        XCTAssertTrue(landed, "the Mossgaze sheet never opened")
        XCTAssertTrue(
            integrationMenu(app, app.buttons[IntegrationUnderTest.ashkeepQueueMenu], item: IntegrationUnderTest.playNext),
            "the song's Play next could not be reached"
        )
        XCTAssertTrue(
            sheetSaid(app, startsWith: IntegrationUnderTest.playingNextAshkeep),
            "the sheet did not say the song plays next — tree follows\n" + app.debugDescription
        )
        evidence(app, "integration-03-genre-song-next")
    }

    /// A song opened from a genre plays in a room with the genre as its
    /// list, this phone's id and the room's mix.
    @MainActor
    func testIntegration_AGenresSongPlaysInARoomWithTheGenreAsItsList() throws {
        let (app, landed) = integrationOpenMossgaze()
        XCTAssertTrue(landed, "the Mossgaze sheet never opened")
        let row = app.buttons[IntegrationUnderTest.ashkeepRow]
        musicMoreScrollOnScreen(app, row)
        row.tap()
        let play = app.buttons[IntegrationUnderTest.playInParlour]
        guard play.waitForExistence(timeout: 20) else {
            XCTFail("the track sheet has no `play in parlour` — tree follows\n" + app.debugDescription)
            return
        }
        musicMoreScrollOnScreen(app, play)
        play.tap()
        let body = integrationPlayTracks().last
        XCTAssertNotNil(body, "no play-track reached the house")
        XCTAssertEqual(503, body?["track_id"] as? Int, "\(String(describing: body))")
        XCTAssertFalse((body?["device_id"] as? String ?? "").isEmpty, "no device id: \(String(describing: body))")
        let context = body?["context"] as? [String: Any]
        XCTAssertEqual("genre", context?["kind"] as? String, "the genre was not sent as the list: \(String(describing: body))")
        XCTAssertEqual(MusicGenresUnderTest.mossgazeKey, context?["ref"] as? String)
        evidence(app, "integration-04-genre-song-in-room")
    }

    // MARK: - the library row (A2, A3)

    /// The library row's play-in-room menu sends this phone's id (the old
    /// path sent none, so the room's mix followed nobody).
    @MainActor
    func testIntegration_TheLibraryRowPlaysInARoomAsThisPhone() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew — tree follows\n" + app.debugDescription)
        let menu = app.buttons[IntegrationUnderTest.playInRoomMenu].firstMatch
        XCTAssertTrue(
            integrationMenu(app, menu, item: IntegrationUnderTest.playInParlour),
            "the library row's play-in-room menu could not be used"
        )
        let body = integrationPlayTracks().last
        XCTAssertNotNil(body, "no play-track reached the house")
        XCTAssertEqual(IntegrationUnderTest.parlour, body?["room_id"] as? String, "\(String(describing: body))")
        XCTAssertFalse(
            (body?["device_id"] as? String ?? "").isEmpty,
            "the library row's play sent no device id: \(String(describing: body))"
        )
        XCTAssertNil(body?["context"], "a library row named a list: \(String(describing: body))")
        evidence(app, "integration-05-library-in-room")
    }

    /// A library row's menu adds the song to the queue where the player
    /// plays (this phone, not casting), said in the shell's toast.
    @MainActor
    func testIntegration_ALibraryRowAddsItsSongToTheQueue() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew — tree follows\n" + app.debugDescription)
        let menu = app.buttons[IntegrationUnderTest.sablewindQueueMenu]
        XCTAssertTrue(musicMoreReveal(app, menu), "no queue menu on the library row — tree follows\n" + app.debugDescription)
        XCTAssertTrue(
            integrationMenu(app, menu, item: IntegrationUnderTest.addToQueue),
            "the library row's Add to queue could not be reached"
        )
        XCTAssertTrue(
            containing(app, IntegrationUnderTest.queuedSablewind).waitForExistence(timeout: 20),
            "nothing said the song was queued — tree follows\n" + app.debugDescription
        )
        evidence(app, "integration-06-library-queued")
    }

    // MARK: - the playlist sheet (A3)

    /// A playlist song's menu adds it to the queue, said in the sheet.
    @MainActor
    func testIntegration_APlaylistSongAddsToTheQueueFromItsRow() throws {
        musicMoreReset()
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")
        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.longwater), "the sheet never opened")
        let menu = app.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", IntegrationUnderTest.playlistQueueMenuPrefix)
        ).firstMatch
        XCTAssertTrue(
            integrationMenu(app, menu, item: IntegrationUnderTest.addToQueue),
            "the playlist row's Add to queue could not be reached"
        )
        XCTAssertTrue(
            sheetSaid(app, startsWith: IntegrationUnderTest.queuedPrefix),
            "the sheet did not say the song was queued — tree follows\n" + app.debugDescription
        )
        evidence(app, "integration-07-playlist-song-queued")
    }
}

/// The words these cases look for, each the app's own.
enum IntegrationUnderTest {
    static let parlour = "parlour"
    static let addToQueue = "add to queue"
    static let queueHere = "queue here"
    static let playNext = "play next"
    static let playInParlour = "play in parlour"
    static let playInRoomMenu = "play in room"
    static let addedMossgazeToParlour = "added 2 songs · Mossgaze to parlour's queue"
    static let queuedMossgazeHere = "queued 2 songs · Mossgaze"
    static let playingNextAshkeep = "playing next: \"Thrumming Ashkeep\""
    static let queuedSablewind = "queued \"Sablewind Figure\""
    static let queuedPrefix = "queued \""
    /// MusicGenreSongRow's identifiers.
    static let ashkeepRow = "music-genre-song-503"
    static let ashkeepQueueMenu = "music-genre-song-queue-503"
    static let sablewindQueueMenu = "queue Sablewind Figure"
    static let playlistQueueMenuPrefix = "music-playlist-song-queue-"
}
