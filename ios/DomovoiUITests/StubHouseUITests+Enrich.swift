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
    func enrichReset(imported: Bool, stale: Bool = false, legacy: Bool = false, port: Int = 6601) {
        control("reset-fixtures", "{}", port: port, what: "the seed")
        control(
            "enrich", "{\"imported\":\(imported),\"stale\":\(stale),\"legacy\":\(legacy)}",
            port: port, what: "the imported playlist"
        )
    }

    /// The cover asks the stub recorded for `trackID`.
    @MainActor
    func enrichCoverAsks(_ record: [String: Any], _ trackID: Int) -> [[String: Any]] {
        ((record["cover_asks"] as? [[String: Any]]) ?? []).filter { ($0["track_id"] as? Int) == trackID }
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
        // A server from before V059: no cover_v, so the ETag is the check.
        enrichReset(imported: false, legacy: true)
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
        // A server from before V059: no cover_v and no event.
        enrichReset(imported: false, legacy: true)
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.pellucid), "the track sheet never opened")
        guard enrichExactly(app, MusicMoreUnderTest.noCoverArt).waitForExistence(timeout: 25) else {
            enrichTree(app, "no placeholder")
            return XCTFail("the coverless track did not say so")
        }
        control("enrich", "{\"cover_bump\":502}", port: 6601, what: "a cover written into 502")
        enrichLeaveAndComeBack(app)

        // Not "cover art" anywhere: the other rows behind the sheet already
        // say that (I045). The coverless track's own "no cover art" going
        // away is the gained cover, and the stub saw it asked again.
        let gained = waitUntilGone(enrichExactly(app, MusicMoreUnderTest.noCoverArt), timeout: 25)
        evidence(app, "enrich-70-cover-gained")
        if !gained { enrichTree(app, "the gained cover never drew") }
        XCTAssertTrue(gained, "the remembered 404 was never asked again")
        let asks = enrichCoverAsks(enrichSent(), 502)
        XCTAssertTrue(asks.contains { ($0["status"] as? Int) == 200 }, "502 was never fetched again: \(asks)")
    }
}

extension StubHouseUITests {

    // MARK: - V059: cover versions, the live event, credits

    /// `library.cover.changed` redraws a cover on screen without leaving:
    /// the song gained art, the event names its new version, and the sheet
    /// asks for that version fresh.
    @MainActor
    func testEnrich_ACoverChangedEventRedrawsWithoutLeaving() throws {
        enrichReset(imported: false)
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.pellucid), "the track sheet never opened")
        guard enrichExactly(app, MusicMoreUnderTest.noCoverArt).waitForExistence(timeout: 25) else {
            enrichTree(app, "no placeholder")
            return XCTFail("the coverless track did not say so")
        }
        control("enrich", "{\"cover_bump\":502}", port: 6601, what: "a cover written into 502")

        // The stub's socket pushes every 3 s, so the ask is waited for — and
        // waited for on the STUB's record. I045 asserted "cover art" anywhere
        // on screen, which every other library row behind the sheet already
        // said, so the wait returned at once and the record was read before
        // the event had even gone out.
        let sent = enrichWaitFor(30) { record in
            self.enrichCoverAsks(record, 502).contains { ($0["v"] as? String) == "1" }
        }
        let asks = enrichCoverAsks(sent, 502)
        let fresh = asks.filter { ($0["v"] as? String) == "1" }
        if fresh.isEmpty { enrichTree(app, "version 1 never asked for") }
        XCTAssertFalse(fresh.isEmpty, "version 1 was never asked for: \(asks)")
        XCTAssertEqual("", fresh.first?["if_none_match"] as? String, "a new version is fetched fresh")
        // Every "no cover art" for 502 — the sheet's and its library row's —
        // is gone: both drew version 1. Nothing else on screen says it.
        let redrawn = waitUntilGone(enrichExactly(app, MusicMoreUnderTest.noCoverArt), timeout: 20)
        evidence(app, "enrich-80-cover-event")
        if !redrawn { enrichTree(app, "the event did not redraw the cover") }
        XCTAssertTrue(redrawn, "the cover-changed event did not redraw 502's cover")
    }

    /// A versioned cover already held is shown on return without asking:
    /// the version is the picture.
    @MainActor
    func testEnrich_AKnownCoverVersionIsNotAskedAgainOnReturn() throws {
        enrichReset(imported: false)
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.ashkeep), "the track sheet never opened")
        let has = enrichExactly(app, MusicMoreUnderTest.coverArt)
        guard has.waitForExistence(timeout: 25) else {
            enrichTree(app, "no cover drawn")
            return XCTFail("the track with a cover drew none")
        }
        let before = enrichCoverAsks(enrichSent(), 503).count
        enrichLeaveAndComeBack(app)
        RunLoop.current.run(until: Date().addingTimeInterval(5))
        evidence(app, "enrich-90-known-version")
        let after = enrichCoverAsks(enrichSent(), 503)
        XCTAssertEqual(before, after.count, "a known cover version was asked about again: \(after)")
        XCTAssertTrue(has.exists, "the cover went away")
    }

    /// Credited artists read "with B, C": after the artist on the library
    /// row, and on a line of their own in the track sheet.
    @MainActor
    func testEnrich_CreditsReadWithBAndC() throws {
        enrichReset(imported: false)
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        let row = containing(app, EnrichUnderTest.rowCredits)
        if !row.waitForExistence(timeout: 25) { enrichTree(app, "no credits on the row") }
        XCTAssertTrue(row.exists, "the library row does not read \"\(EnrichUnderTest.rowCredits)\"")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.harrowgate), "the track sheet never opened")
        let line = app.staticTexts.matching(NSPredicate(
            format: "identifier == %@ AND label == %@", "music-track-credits", EnrichUnderTest.credits
        )).firstMatch
        let shown = line.waitForExistence(timeout: 20)
        evidence(app, "enrich-95-credits")
        if !shown { enrichTree(app, "no credits line in the sheet") }
        XCTAssertTrue(shown, "the track sheet does not show \"\(EnrichUnderTest.credits)\"")
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
    /// 506 credits Marrow Choir (its artist), Tessaly Brook and Orbis Quill.
    static let credits = "with Tessaly Brook, Orbis Quill"
    static let rowCredits = "Marrow Choir · with Tessaly Brook, Orbis Quill"
}
