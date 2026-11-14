import XCTest

/// Music → Genres, a playlist played on this phone by name, and the player
/// tab's Lyrics · Queue · Hide panel — the iOS port of Android 3cdd261b
/// (batch 2026-11-14, unit "music").
///
/// Every case is named `testMusicGenres_…` so a train can select them, and
/// every piece of evidence is named `musicgenres-…`. Every miss prints the
/// tree.
///
/// ### The house
///
/// The genres are the music unit's stub patch
/// (`design-notes/ios/batch-2026-11-14/music/stubhouse.patch`) over
/// `fixtures/musicmore.py`'s six tracks: **Brindlecore** (501, 502, 503),
/// **Mossgaze** (503, 504), **Saltgrass** (506), and 505 with no genre — so
/// "no genre · 1 track" is the last row. `POST /api/_test/genres-reading`
/// makes the answers say the tag read is still going, 4 of 6;
/// `reset-fixtures` puts it back.
///
/// Rooms are folded away (the default), so a song's title on screen is the
/// sheet's and not a room card's.
extension StubHouseUITests {

    // MARK: - getting there

    /// Open Music and move to the genres tab; true once a genre row drew.
    @MainActor
    func openGenresTab(_ mode: String = StubHouseUITests.ordinaryTypeSize)
        -> (app: XCUIApplication, landed: Bool) {
        let (app, landed) = openMusicMore(mode)
        guard landed else { return (app, false) }
        guard musicMoreTapTab(app, MusicGenresUnderTest.tab) else { return (app, false) }
        let row = genreRow(app, MusicGenresUnderTest.brindlecoreKey)
        if row.waitForExistence(timeout: 30) { return (app, true) }
        if musicMoreScroll(app, to: row) { return (app, true) }
        print("=== NO GENRE ROW — tree follows ===")
        print(app.debugDescription)
        return (app, false)
    }

    @MainActor
    func genreRow(_ app: XCUIApplication, _ key: String) -> XCUIElement {
        app.descendants(matching: .any)["music-genre-row-\(key)"].firstMatch
    }

    /// Open one genre's sheet by its row; true once the sheet's `close` is up.
    @MainActor
    func openGenreSheet(_ app: XCUIApplication, _ key: String) -> Bool {
        let row = genreRow(app, key)
        guard row.waitForExistence(timeout: 20) else {
            print("=== NO GENRE ROW '\(key)' — tree follows ===")
            print(app.debugDescription)
            return false
        }
        musicMoreScrollOnScreen(app, row)
        row.tap()
        guard app.buttons[MusicMoreUnderTest.close].waitForExistence(timeout: 20) else {
            print("=== THE GENRE SHEET NEVER CAME UP for '\(key)' — tree follows ===")
            print(app.debugDescription)
            return false
        }
        return true
    }

    @MainActor
    func genresReading(_ reading: Bool, port: Int = 6601) {
        control("genres-reading", #"{"reading":\#(reading)}"#, port: port, what: "the genre read")
    }

    // MARK: - the list

    /// The genres in name order with their counts, and "no genre" last.
    @MainActor
    func testMusicGenres_TheTabListsGenresWithNoGenreLast() throws {
        musicMoreReset()
        let (app, landed) = openGenresTab()
        XCTAssertTrue(landed, "the genres tab never drew a row")

        let brindle = genreRow(app, MusicGenresUnderTest.brindlecoreKey)
        let salt = genreRow(app, MusicGenresUnderTest.saltgrassKey)
        let none = genreRow(app, MusicGenresUnderTest.noGenreKey)
        for (what, row) in [("Saltgrass", salt), ("no genre", none)] where !row.waitForExistence(timeout: 20) {
            XCTFail("no '\(what)' row — tree follows\n" + app.debugDescription)
        }
        evidence(app, "musicgenres-01-list")
        XCTAssertTrue(brindle.label.contains("3 tracks"), "Brindlecore's row reads '\(brindle.label)'")
        XCTAssertTrue(none.label.contains("no genre"), "the last row reads '\(none.label)'")
        XCTAssertTrue(none.label.contains("1 track"), "the no-genre row reads '\(none.label)'")
        XCTAssertLessThan(brindle.frame.minY, salt.frame.minY, "not in name order")
        XCTAssertLessThan(salt.frame.minY, none.frame.minY, "\"no genre\" is not the last row")
        XCTAssertTrue(
            containing(app, "3 genres").exists,
            "the count says something else — tree follows\n" + app.debugDescription
        )
    }

    /// A search narrows the list, and "no genre" is never a search result.
    @MainActor
    func testMusicGenres_ASearchNarrowsAndLeavesOutNoGenre() throws {
        musicMoreReset()
        let (app, landed) = openGenresTab()
        XCTAssertTrue(landed, "the genres tab never drew a row")
        let field = app.descendants(matching: .any)["music-genres-search"].firstMatch
        guard field.waitForExistence(timeout: 20) else {
            XCTFail("no genre search — tree follows\n" + app.debugDescription)
            return
        }
        field.tap()
        field.typeText("moss")
        XCTAssertTrue(
            waitUntilGone(genreRow(app, MusicGenresUnderTest.brindlecoreKey), timeout: 20),
            "Brindlecore still listed for 'moss' — tree follows\n" + app.debugDescription
        )
        evidence(app, "musicgenres-02-search")
        XCTAssertTrue(genreRow(app, MusicGenresUnderTest.mossgazeKey).exists, "Mossgaze is not listed for 'moss'")
        XCTAssertFalse(
            genreRow(app, MusicGenresUnderTest.noGenreKey).exists,
            "\"no genre\" was offered as a search result"
        )
    }

    /// While the server is still reading tags, the tab says how far it has
    /// got, and stops saying it once the read is done.
    @MainActor
    func testMusicGenres_WhileTagsAreReadTheTabSaysHowFar() throws {
        musicMoreReset()
        genresReading(true)
        defer { genresReading(false) }
        let (app, landed) = openGenresTab()
        XCTAssertTrue(landed, "the genres tab never drew a row")
        let line = app.descendants(matching: .any)["music-genres-reading"].firstMatch
        guard line.waitForExistence(timeout: 20) else {
            XCTFail("no reading line while the read is going — tree follows\n" + app.debugDescription)
            return
        }
        evidence(app, "musicgenres-03-reading")
        XCTAssertTrue(
            line.label.contains(MusicGenresUnderTest.readingFourOfSix),
            "the reading line says '\(line.label)'"
        )
        // The read finishes; the tab re-reads itself within ten seconds.
        genresReading(false)
        XCTAssertTrue(
            waitUntilGone(line, timeout: 30),
            "the reading line stayed after the read was done — tree follows\n" + app.debugDescription
        )
    }

    // MARK: - one genre

    /// A genre opens its songs by artist, with its count, and `play` sends
    /// the whole genre to a room and says so.
    @MainActor
    func testMusicGenres_AGenreOpensItsSongsAndPlaysInARoom() throws {
        musicMoreReset()
        let (app, landed) = openGenresTab()
        XCTAssertTrue(landed, "the genres tab never drew a row")
        XCTAssertTrue(openGenreSheet(app, MusicGenresUnderTest.mossgazeKey), "the Mossgaze sheet never opened")

        let ashkeep = containing(app, MusicMoreUnderTest.ashkeep)
        XCTAssertTrue(
            ashkeep.waitForExistence(timeout: 20),
            "the sheet lists no songs — tree follows\n" + app.debugDescription
        )
        XCTAssertTrue(containing(app, "2 tracks").exists, "the sheet does not say how many tracks")
        evidence(app, "musicgenres-04-sheet")

        let play = app.buttons[MusicMoreUnderTest.play]
        guard play.waitForExistence(timeout: 20) else {
            XCTFail("no room `play` in the genre sheet — tree follows\n" + app.debugDescription)
            return
        }
        play.tap()
        XCTAssertTrue(
            containing(app, MusicGenresUnderTest.playingMossgazeIn).waitForExistence(timeout: 10),
            "nothing said the genre is playing in a room — tree follows\n" + app.debugDescription
        )
        evidence(app, "musicgenres-05-played-in-room")
        XCTAssertTrue(
            waitUntilGone(app.buttons[MusicMoreUnderTest.close], timeout: 10),
            "the sheet stayed up after `play`, Android's drawer closes"
        )
    }

    /// "no genre" opens the songs whose tags name none, and says why.
    @MainActor
    func testMusicGenres_NoGenreOpensTheUntaggedSongs() throws {
        musicMoreReset()
        let (app, landed) = openGenresTab()
        XCTAssertTrue(landed, "the genres tab never drew a row")
        XCTAssertTrue(openGenreSheet(app, MusicGenresUnderTest.noGenreKey), "the no-genre sheet never opened")
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.sablewind).waitForExistence(timeout: 20),
            "the untagged song is not listed — tree follows\n" + app.debugDescription
        )
        XCTAssertTrue(
            containing(app, MusicGenresUnderTest.tagsNameNoGenre).exists,
            "the sheet does not say their tags name no genre"
        )
        evidence(app, "musicgenres-06-no-genre")
    }

    /// `play on this device` makes the genre's songs this phone's queue,
    /// names the genre, and the player tab holds them.
    @MainActor
    func testMusicGenres_AGenrePlaysOnThisPhoneByName() throws {
        musicMoreReset()
        let (app, landed) = openGenresTab()
        XCTAssertTrue(landed, "the genres tab never drew a row")
        XCTAssertTrue(openGenreSheet(app, MusicGenresUnderTest.brindlecoreKey), "the Brindlecore sheet never opened")
        let here = app.buttons[MusicMoreUnderTest.playHere]
        guard here.waitForExistence(timeout: 20) else {
            XCTFail("the genre sheet offers no `play on this device` — tree follows\n" + app.debugDescription)
            return
        }
        here.tap()
        XCTAssertTrue(
            containing(app, MusicGenresUnderTest.playingBrindlecoreHere).waitForExistence(timeout: 10),
            "nothing said the genre is playing on this device — tree follows\n" + app.debugDescription
        )
        evidence(app, "musicgenres-07-played-here")
        XCTAssertTrue(
            musicMoreTapTab(app, MusicMoreUnderTest.tabPlayer),
            "the player tab is not offered after a play on this device"
        )
        XCTAssertFalse(
            containing(app, MusicMoreUnderTest.nothingQueued).exists,
            "the player tab says nothing is queued after a genre was handed to it"
        )
    }

    // MARK: - a playlist on this phone (§5)

    /// The sentence names the playlist, not its first song.
    @MainActor
    func testMusicGenres_APlaylistPlayedHereIsNamedAsThePlaylist() throws {
        musicMoreReset()
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")
        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.longwater), "the sheet never opened")
        let here = app.buttons[MusicMoreUnderTest.playHere]
        guard here.waitForExistence(timeout: 20) else {
            XCTFail("the sheet offers no `play on this device` — tree follows\n" + app.debugDescription)
            return
        }
        here.tap()
        evidence(app, "musicgenres-08-playlist-here")
        XCTAssertTrue(
            sheetSaid(app, startsWith: MusicGenresUnderTest.playingLongwaterHere),
            "the sheet did not name the playlist — tree follows\n" + app.debugDescription
        )
    }

    // MARK: - the player's panel (§6)

    /// With a song on this phone (no lyrics followed locally on this app),
    /// the panel offers Queue · Hide, opens on the Queue, and Hide hides it.
    @MainActor
    func testMusicGenres_ThePlayerPanelHasQueueAndHide() throws {
        musicMoreReset()
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")
        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.longwater), "the sheet never opened")
        let here = app.buttons[MusicMoreUnderTest.playHere]
        guard here.waitForExistence(timeout: 20) else {
            XCTFail("the sheet offers no `play on this device` — tree follows\n" + app.debugDescription)
            return
        }
        here.tap()
        app.buttons[MusicMoreUnderTest.close].firstMatch.tap()
        XCTAssertTrue(musicMoreTapTab(app, MusicMoreUnderTest.tabPlayer), "no player tab after a play here")

        let queueTab = app.buttons[MusicGenresUnderTest.panelQueue]
        let hideTab = app.buttons[MusicGenresUnderTest.panelHide]
        guard queueTab.waitForExistence(timeout: 20), hideTab.exists else {
            XCTFail("the player tab has no Queue · Hide panel — tree follows\n" + app.debugDescription)
            return
        }
        evidence(app, "musicgenres-09-panel-queue")
        XCTAssertTrue(queueTab.isSelected, "the panel did not open on the Queue for a song with no lyrics")
        let clear = app.buttons[MusicGenresUnderTest.clear]
        XCTAssertTrue(clear.exists, "the queue panel draws no queue — tree follows\n" + app.debugDescription)

        musicMoreScrollOnScreen(app, hideTab)
        hideTab.tap()
        XCTAssertTrue(
            waitUntilGone(clear, timeout: 10),
            "Hide left the queue on screen — tree follows\n" + app.debugDescription
        )
        evidence(app, "musicgenres-10-panel-hidden")
        XCTAssertTrue(hideTab.isSelected, "Hide is not told as the selected tab")
        musicMoreScrollOnScreen(app, queueTab)
        queueTab.tap()
        XCTAssertTrue(clear.waitForExistence(timeout: 10), "the queue did not come back")
    }
}

enum MusicGenresUnderTest {
    static let tab = "genres"
    static let brindlecoreKey = "brindlecore"
    static let mossgazeKey = "mossgaze"
    static let saltgrassKey = "saltgrass"
    static let noGenreKey = ""
    static let readingFourOfSix = "reading genres from your files… 4 of 6"
    static let tagsNameNoGenre = "their tags name no genre"
    static let playingMossgazeIn = "playing Mossgaze in"
    static let playingBrindlecoreHere = "playing Brindlecore on this device"
    static let playingLongwaterHere = "playing Longwater Drive on this device"
    static let panelQueue = "music-player-panel-queue"
    static let panelHide = "music-player-panel-hide"
    static let clear = "clear"
}
