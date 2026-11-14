import XCTest

/// Library tools on the phone (V058; Android c57e255a): a playlist made from
/// the music files is read-only and offers a copy, a 409 says the server's
/// words, Favorites can be trimmed, and a cover is asked about again once
/// the app comes back to the screen.
///
/// Every case is named `testEnrich_…` so a train can select them, and runs
/// against `design-notes/ios/batch-2026-11-14/enrich/stubhouse.patch`:
/// `POST /api/_test/enrich {imported, stale, cover_bump}` and
/// `GET /api/_test/enrich-sent`. The imported playlist is OFF until a case
/// turns it on, so no `testMusicMore_…` case sees a fourth playlist.
///
/// Every miss prints the tree.
extension StubHouseUITests {

    // MARK: - the rig

    /// Reset the house, and turn the imported playlist on (or not).
    @MainActor
    func enrichReset(imported: Bool, stale: Bool = false, port: Int = 6601) {
        control("reset-fixtures", "{}", port: port, what: "the seed")
        control(
            "enrich", "{\"imported\":\(imported),\"stale\":\(stale)}",
            port: port, what: "the imported playlist"
        )
    }

    /// What the stub recorded (`GET /api/_test/enrich-sent`).
    @MainActor
    func enrichSent(port: Int = 6601) -> [String: Any] {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/_test/enrich-sent")!)
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        let answered = expectation(description: "the stub said what it was sent")
        let box = EnrichBox()
        URLSession.shared.dataTask(with: request) { data, _, _ in
            box.data = data
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 15)
        guard let data = box.data,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return object
    }

    /// Poll the stub's record until `done` holds, or `seconds` pass.
    @MainActor
    func enrichWaitFor(_ seconds: Double = 20, _ done: ([String: Any]) -> Bool) -> [String: Any] {
        let deadline = Date().addingTimeInterval(seconds)
        var last = enrichSent()
        while !done(last), Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.5))
            last = enrichSent()
        }
        return last
    }

    @MainActor
    func enrichTree(_ app: XCUIApplication, _ why: String) {
        print("=== ENRICH: \(why) — tree follows ===")
        print(app.debugDescription)
    }

    /// Anything whose label is exactly `text`.
    @MainActor
    func enrichExactly(_ app: XCUIApplication, _ text: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label == %@", text)).firstMatch
    }

    /// Send the app away and bring it back: a real return, which renews the
    /// covers (`ShellModel.scenePhaseChanged`).
    @MainActor
    func enrichLeaveAndComeBack(_ app: XCUIApplication) {
        XCUIDevice.shared.press(.home)
        RunLoop.current.run(until: Date().addingTimeInterval(2))
        app.activate()
        _ = app.wait(for: .runningForeground, timeout: 20)
    }

    // MARK: - the playlist from the files

    /// The sheet says where the playlist comes from, offers the copy and the
    /// web's note, and draws none of the controls that 409: no edit, no
    /// arrows, no remove, no delete.
    @MainActor
    func testEnrich_APlaylistFromTheFilesIsReadOnlyAndOffersACopy() throws {
        enrichReset(imported: true)
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")
        guard openPlaylistSheet(app, EnrichUnderTest.imported) else {
            enrichTree(app, "the imported playlist's sheet never opened")
            return XCTFail("the imported playlist's sheet never opened")
        }
        let tracks = containing(app, EnrichUnderTest.harrowgate)
        if !tracks.waitForExistence(timeout: 20) { enrichTree(app, "no tracks drawn") }
        evidence(app, "enrich-10-imported-sheet")

        let title = containing(app, EnrichUnderTest.eyebrow)
        if !title.exists { enrichTree(app, "no eyebrow") }
        XCTAssertTrue(title.exists, "the sheet does not say it comes from the music files")
        let copy = app.buttons[EnrichUnderTest.copyButton]
        if !copy.exists { enrichTree(app, "no copy button") }
        XCTAssertTrue(copy.exists, "no \"Copy to a new playlist\"")
        XCTAssertTrue(containing(app, EnrichUnderTest.notePrefix).exists, "the web's note is missing")

        XCTAssertFalse(app.buttons[MusicMoreUnderTest.edit].exists, "an edit is offered, and it 409s")
        XCTAssertEqual(0, countContaining(app, MusicMoreUnderTest.deletePlaylist), "a delete is offered")
        XCTAssertEqual(0, countContaining(app, MusicMoreUnderTest.moveUp), "a reorder is offered")
        XCTAssertEqual(0, countContaining(app, EnrichUnderTest.remove), "a remove is offered")
        if countContaining(app, EnrichUnderTest.remove) > 0 { enrichTree(app, "remove drawn") }
    }

    /// The copy makes a household playlist: the sheet closes, the shell says
    /// `copied to “…”`, and the tab lists the copy.
    @MainActor
    func testEnrich_CopyMakesAHouseholdPlaylistAndClosesTheSheet() throws {
        enrichReset(imported: true)
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")
        guard openPlaylistSheet(app, EnrichUnderTest.imported) else {
            enrichTree(app, "the imported playlist's sheet never opened")
            return XCTFail("the imported playlist's sheet never opened")
        }
        let copy = app.buttons[EnrichUnderTest.copyButton]
        guard copy.waitForExistence(timeout: 20) else {
            enrichTree(app, "no copy button")
            return XCTFail("no \"Copy to a new playlist\"")
        }
        copy.tap()

        let said = containing(app, EnrichUnderTest.copiedToast)
        let toasted = said.waitForExistence(timeout: 20)
        evidence(app, "enrich-20-copied")
        if !toasted { enrichTree(app, "no copied toast") }
        XCTAssertTrue(toasted, "the shell did not say the copy was made")
        XCTAssertTrue(
            waitUntilGone(app.buttons[MusicMoreUnderTest.close], timeout: 15),
            "the sheet stayed open after the copy"
        )
        let sent = enrichSent()
        XCTAssertEqual(1, (sent["copies"] as? [Int])?.count, "copies sent: \(sent)")

        // The row, not the toast: the toast carries the name too.
        let row = app.descendants(matching: .any).matching(NSPredicate(
            format: "label CONTAINS %@ AND NOT (label BEGINSWITH %@)",
            EnrichUnderTest.copyName, "copied to"
        )).firstMatch
        if !row.waitForExistence(timeout: 20) { enrichTree(app, "the copy is not listed") }
        XCTAssertTrue(row.exists, "the tab was not re-read: the copy is not listed")
    }

    /// The add-to-playlist sheet leaves out the playlist the files own and
    /// keeps Favorites.
    @MainActor
    func testEnrich_TheAddSheetLeavesOutAPlaylistFromTheFiles() throws {
        enrichReset(imported: true)
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.sablewind), "the track sheet never opened")
        app.buttons[MusicMoreUnderTest.addToPlaylist].tap()
        let quiet = containing(app, MusicMoreUnderTest.quietHours)
        guard quiet.waitForExistence(timeout: 20) else {
            enrichTree(app, "the add sheet never listed")
            return XCTFail("the add-to-playlist sheet never opened")
        }
        evidence(app, "enrich-30-add-sheet")
        XCTAssertTrue(containing(app, MusicMoreUnderTest.favorites).exists, "Favorites was left out too")
        XCTAssertEqual(
            0, countContaining(app, EnrichUnderTest.imported),
            "the playlist from the music files is offered, and every add to it 409s"
        )
        if countContaining(app, EnrichUnderTest.imported) > 0 { enrichTree(app, "imported offered") }
    }

    /// A sheet opened from a stale list (the row said household) gets a 409
    /// on its first write: the server's words are said, and the sheet takes
    /// the server's row — the edit goes, the copy comes.
    @MainActor
    func testEnrich_AStaleFlagSaysTheServersWordsAndHidesTheControls() throws {
        enrichReset(imported: true, stale: true)
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")
        guard openPlaylistSheet(app, EnrichUnderTest.imported) else {
            enrichTree(app, "the stale playlist's sheet never opened")
            return XCTFail("the stale playlist's sheet never opened")
        }
        let remove = app.buttons[EnrichUnderTest.removeQuillon]
        guard remove.waitForExistence(timeout: 20) else {
            enrichTree(app, "the stale sheet drew no remove")
            return XCTFail("the stale row should still offer a remove")
        }
        // The server's list now says what the server does.
        control("enrich", "{\"stale\":false}", port: 6601, what: "the list's flag")
        remove.tap()

        let said = sheetSaid(app, startsWith: EnrichUnderTest.readOnlyPrefix)
        evidence(app, "enrich-40-stale-409")
        if !said { enrichTree(app, "no read-only sentence") }
        XCTAssertTrue(said, "the 409 was not said in the server's words")
        XCTAssertEqual(0, countContaining(app, "409"), "the status leaked into the sentence")
        let copy = app.buttons[EnrichUnderTest.copyButton]
        if !copy.waitForExistence(timeout: 20) { enrichTree(app, "the row was not re-read") }
        XCTAssertTrue(copy.exists, "the sheet did not take the server's row")
        XCTAssertFalse(app.buttons[MusicMoreUnderTest.edit].exists, "the edit is still offered")
        let refused = (enrichSent()["refused"] as? [[String: Any]]) ?? []
        XCTAssertEqual(1, refused.count, "refused writes: \(refused)")
    }

    // MARK: - Favorites

    /// Taking a song out of Favorites unfavourites it, as on Android.
    @MainActor
    func testEnrich_RemovingASongFromFavoritesUnfavoritesIt() throws {
        enrichReset(imported: false)
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")
        guard openPlaylistSheet(app, MusicMoreUnderTest.favorites) else {
            enrichTree(app, "Favorites never opened")
            return XCTFail("Favorites never opened")
        }
        let remove = app.buttons[EnrichUnderTest.removePellucid]
        guard remove.waitForExistence(timeout: 20) else {
            enrichTree(app, "no remove on Favorites")
            return XCTFail("Favorites offers no remove")
        }
        remove.tap()
        let sent = enrichWaitFor { (($0["unfavorited"] as? [Int]) ?? []).contains(502) }
        evidence(app, "enrich-50-unfavorited")
        XCTAssertEqual([502], sent["unfavorited"] as? [Int], "sent: \(sent)")
        XCTAssertFalse(sheetSaid(app, containing: MusicMoreUnderTest.failedWord, timeout: 3), "the remove failed")
    }

    // MARK: - covers

    /// A cover drawn before the app went away is asked about again with its
    /// ETag when it comes back; the new art is the one fetched.
    @MainActor
    func testEnrich_ACoverIsAskedAgainWithItsETagAfterTheAppReturns() throws {
        enrichReset(imported: false)
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.ashkeep), "the track sheet never opened")
        let has = enrichExactly(app, MusicMoreUnderTest.coverArt)
        guard has.waitForExistence(timeout: 25) else {
            enrichTree(app, "no cover drawn")
            return XCTFail("the track with a cover drew none")
        }
        control("enrich", "{\"cover_bump\":503}", port: 6601, what: "new art in 503's file")
        enrichLeaveAndComeBack(app)

        let sent = enrichWaitFor { record in
            let asks = (record["cover_asks"] as? [[String: Any]]) ?? []
            return asks.contains {
                ($0["track_id"] as? Int) == 503 && ($0["if_none_match"] as? String) == EnrichUnderTest.etag503
            }
        }
        evidence(app, "enrich-60-cover-revalidated")
        let asks = (sent["cover_asks"] as? [[String: Any]]) ?? []
        let conditional = asks.filter {
            ($0["track_id"] as? Int) == 503 && ($0["if_none_match"] as? String) == EnrichUnderTest.etag503
        }
        if conditional.isEmpty { enrichTree(app, "no conditional ask") }
        XCTAssertFalse(conditional.isEmpty, "503's cover was not asked about with its ETag: \(asks)")
        XCTAssertEqual(200, conditional.first?["status"] as? Int, "the new art was not fetched")
        XCTAssertTrue(has.exists, "the cover went away")
    }

    /// A song that had no cover and gains one is asked again once the app
    /// comes back: the remembered "no cover" is forgotten.
    @MainActor
    func testEnrich_ACoverASongGainedShowsAfterTheAppReturns() throws {
        enrichReset(imported: false)
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.pellucid), "the track sheet never opened")
        guard enrichExactly(app, MusicMoreUnderTest.noCoverArt).waitForExistence(timeout: 25) else {
            enrichTree(app, "no placeholder")
            return XCTFail("the coverless track did not say so")
        }
        control("enrich", "{\"cover_bump\":502}", port: 6601, what: "a cover written into 502")
        enrichLeaveAndComeBack(app)

        let gained = enrichExactly(app, MusicMoreUnderTest.coverArt).waitForExistence(timeout: 25)
        evidence(app, "enrich-70-cover-gained")
        if !gained { enrichTree(app, "the gained cover never drew") }
        XCTAssertTrue(gained, "the remembered 404 was never asked again")
    }
}

/// The URL session's completion runs off the main actor; a box to carry the
/// bytes back across `wait(for:)`.
private final class EnrichBox: @unchecked Sendable {
    var data: Data?
}

/// The words these cases match, spelt out rather than imported: the stub's
/// (`enrich/stubhouse.patch`) and the app's (`MusicPlaylistReadOnly`).
enum EnrichUnderTest {
    static let imported = "Hearthsong Mixtape"
    static let copyName = "Hearthsong Mixtape (copy)"
    static let eyebrow = "from your music files · #90"
    static let copyButton = "Copy to a new playlist"
    static let notePrefix = "This playlist comes from your music files (a playlist file"
    static let copiedToast = "copied to \u{201C}Hearthsong Mixtape (copy)\u{201D}"
    static let readOnlyPrefix = "This playlist comes from your music files, so it changes"
    static let remove = "remove from playlist"
    static let removeQuillon = "remove from playlist Quillon Drift"
    static let removePellucid = "remove from playlist Pellucid Marrow"
    static let harrowgate = "Harrowgate Lantern"
    static let etag503 = "\"musicmore-503\""
}
