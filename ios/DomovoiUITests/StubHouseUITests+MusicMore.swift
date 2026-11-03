import XCTest

/// Music's second half: the playlists tab and its two sheets, the cover art,
/// the room-followed lyrics, and the tab that is deliberately not offered.
///
/// Every case is named `testMusicMore_…` so a train can hand its result back
/// to this item, and every piece of evidence is named `musicmore-…`.
///
/// ### Which track each case uses, and why it matters
///
/// `fixtures/musicmore.py` seeds five tracks whose words nothing else in the
/// house uses. **Track 501 is the one the parlour is playing**, so its title
/// is on screen twice — once on its library row and once on the room card —
/// and `containing(app, …).firstMatch` cannot say which. So no case here
/// opens 501's library row: the membership cases use **503**, the cover and
/// plain-lyrics cases **502**, and the Favorites and create cases **505**,
/// each of which appears exactly once.
///
/// ### One sheet at a time
///
/// Music has three sheets behind **one** `.sheet` modifier
/// (``MusicMoreSheet``), so `add to playlist` replaces the track sheet it was
/// reached from rather than opening over it, and one `close` comes back to
/// Music.
///
/// ### The two fixtures that exist to tell a kept guard from a dropped one
///
/// * **Track 502 has no cover**, so `GET …/502/cover` is a real 404 and the
///   placeholder is a measured answer rather than a screenshot of a loading
///   state.
/// * **The cellar's room-lyrics answer carries a document beside a null
///   `track_id`.** `MusicRoomLyricsModel.apply` keeps a doc only when the
///   track is not nil, and `isHidden` is true for a nil track; an app that
///   dropped either guard would print `musicmoreCellarLine` on the streaming
///   room's card. Its presence anywhere on screen is the failure.
extension StubHouseUITests {

    // MARK: - getting there

    /// Put the house back where every case below expects it.
    ///
    /// Cases share one house for the whole run, in alphabetical order, so a
    /// playlist another case created or a track it favourited is still there
    /// unless this is called first.
    @MainActor
    func musicMoreReset(port: Int = 6601) {
        control("reset-fixtures", "{}", port: port, what: "the musicmore seed")
    }

    /// Open Music and wait for it to have drawn.
    ///
    /// The crumb is the shell's; the tab bar is the screen's, and the second
    /// is what says the reads have answered rather than that the route was
    /// pushed.
    @MainActor
    func openMusicMore(_ mode: String = StubHouseUITests.ordinaryTypeSize)
        -> (app: XCUIApplication, landed: Bool) {
        let app = launch(mode: mode, push: "Music", pushDelay: Self.pushDelay)
        let crumb = app.staticTexts[ShellStringsUnderTest.musicCrumb]
        guard crumb.waitForExistence(timeout: 40) else { return (app, false) }
        let tab = startingWith(app, MusicMoreUnderTest.tabPlaylists)
        return (app, tab.waitForExistence(timeout: 30))
    }

    /// Open Music and move to the playlists tab.
    @MainActor
    func openPlaylistsTab(_ mode: String = StubHouseUITests.ordinaryTypeSize)
        -> (app: XCUIApplication, landed: Bool) {
        let (app, landed) = openMusicMore(mode)
        guard landed else { return (app, false) }
        startingWith(app, MusicMoreUnderTest.tabPlaylists).tap()
        let row = containing(app, MusicMoreUnderTest.longwater)
        return (app, row.waitForExistence(timeout: 30))
    }

    /// Open one playlist's sheet from the tab.
    @MainActor
    func openPlaylistSheet(_ app: XCUIApplication, _ name: String) -> Bool {
        let row = containing(app, name)
        guard row.waitForExistence(timeout: 20) else {
            // The tree, not a sentence about what is missing: "the row is not
            // there" is a claim about an absence and the only useful answer is
            // what *is*.
            print("=== NO PLAYLIST ROW for \(name) — tree follows ===")
            print(app.debugDescription)
            return false
        }
        row.tap()
        // The sheet's own eyebrow, not the name: the name is also on the row
        // behind the sheet, so waiting for it would not prove the sheet came
        // up at all.
        return app.buttons[MusicMoreUnderTest.close].waitForExistence(timeout: 20)
    }

    /// Scroll Music until `element` is built.
    ///
    /// **Not `scrollUntilBuilt`**: that one scrolls
    /// `app.collectionViews.firstMatch`, and Music's list is a `LazyVStack`
    /// inside a `ScrollView` (the owner's 2026-10-11 load-as-you-scroll
    /// decision, in SwiftUI's own container), so there is no collection view
    /// on this screen at all and it would report every row as unbuilt.
    ///
    /// `.slow`, for the reason the Chat pass wrote down: a plain `swipeUp()`
    /// steps over rows, and a lazy list's foot is unbuilt rather than merely
    /// off screen.
    @MainActor
    func musicMoreScroll(_ app: XCUIApplication, to element: XCUIElement, flicks: Int = 12) -> Bool {
        if element.exists { return true }
        let page = app.scrollViews.firstMatch
        guard page.waitForExistence(timeout: 20) else {
            print("=== NO SCROLL VIEW ON MUSIC — tree follows ===")
            print(app.debugDescription)
            return element.exists
        }
        for _ in 0..<flicks {
            if element.exists { return true }
            page.swipeUp(velocity: .slow)
        }
        if !element.exists {
            print("=== \(flicks) FLICKS AND STILL UNBUILT — tree follows ===")
            print(app.debugDescription)
        }
        return element.exists
    }

    /// Back to the top, so the tab bar is reachable again after a scroll.
    @MainActor
    func musicMoreScrollTop(_ app: XCUIApplication, flicks: Int = 12) {
        let page = app.scrollViews.firstMatch
        guard page.exists else { return }
        let bar = startingWith(app, MusicMoreUnderTest.tabPlaylists)
        for _ in 0..<flicks {
            if bar.exists, bar.isHittable { return }
            page.swipeDown(velocity: .slow)
        }
    }

    /// Open a library row's track sheet, by a title that appears once.
    @MainActor
    func openTrackSheet(_ app: XCUIApplication, _ title: String) -> Bool {
        let row = containing(app, title)
        guard musicMoreScroll(app, to: row) else { return false }
        row.tap()
        return app.buttons[MusicMoreUnderTest.close].waitForExistence(timeout: 20)
    }

    /// Close whatever sheet is up.
    @MainActor
    func closeSheet(_ app: XCUIApplication) {
        let close = app.buttons[MusicMoreUnderTest.close]
        if close.exists { close.tap() }
        _ = waitUntilGone(close, timeout: 15)
    }

    /// How many elements on screen carry a label containing `text`.
    ///
    /// For the half of an assertion that is an **absence**. `.exists` on a
    /// `firstMatch` answers the same question, and this one prints a number
    /// a failure message can carry.
    @MainActor
    func countContaining(_ app: XCUIApplication, _ text: String) -> Int {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS %@", text))
            .count
    }

    // MARK: - 1. the playlists tab

    /// The tab lists what the house has, Favorites first, and a row with a
    /// description shows **the description** rather than its count.
    ///
    /// The row's second line is a three-way `when` on Android
    /// (`PlaylistsTab.kt:114`) and the order is the whole of it: a
    /// description and a count are both true of the same row, and the one
    /// worth the line is the one a person wrote. `Longwater Drive` has both —
    /// a description and three tracks — so `3 tracks` appearing anywhere is
    /// the branch having collapsed to the count.
    @MainActor
    func testMusicMore_PlaylistsTabListsTheHousePlaylists() throws {
        musicMoreReset()
        let (app, landed) = openPlaylistsTab()
        evidence(app, "musicmore-10-playlists-tab")
        XCTAssertFalse(fellBackToLocalMedia(in: app), "the shell gave up on the server")
        XCTAssertTrue(landed, "the playlists tab never drew a row")

        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.favorites).exists,
            "Favorites is not on the tab — the server pins it on top and this does not re-sort"
        )
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.longwater).exists,
            "the named playlist is not on the tab"
        )
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.longwaterNote).exists,
            "the row with a description does not show it"
        )
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.threeTracks),
            "the row showed its count instead of the description it has"
        )
        // Favorites has no description, so its line IS its own sentence —
        // the second arm of the same `when`, and the one that proves the
        // first assertion is not passing by the count simply being absent.
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.derivedFromFavorites).exists,
            "the virtual playlist does not say where it comes from"
        )
    }

    // MARK: - 2. the playlist sheet

    /// The sheet lists the playlist's tracks **in the server's order**.
    ///
    /// `GET /api/playlists/{id}/tracks` answers a **bare array** in playback
    /// order (`playlists.py:122`), and the seed's order is deliberately not
    /// alphabetical: 501, 503, 502. A client that decoded a wrapper would
    /// show nothing, and one that re-sorted by title would put `Pellucid`
    /// first — so the assertion is on the positions and not just on
    /// presence.
    @MainActor
    func testMusicMore_PlaylistSheetShowsTracksInOrder() throws {
        musicMoreReset()
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")
        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.longwater), "the sheet never opened")
        evidence(app, "musicmore-20-playlist-sheet")

        let first = containing(app, MusicMoreUnderTest.veldrassen)
        let last = containing(app, MusicMoreUnderTest.pellucid)
        XCTAssertTrue(first.waitForExistence(timeout: 20), "the track read decoded to nothing")
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.ashkeep).exists,
            "the middle track is missing"
        )
        XCTAssertTrue(last.exists, "the last track is missing")
        XCTAssertLessThan(
            first.frame.minY, last.frame.minY,
            "the list is not in the server's order — 501 is first on the wire and "
                + "a title sort would put Pellucid there"
        )
    }

    // MARK: - 3. Favorites is not a playlist

    /// Favorites offers **no** edit, no delete and no reorder; the real
    /// playlist offers all three.
    ///
    /// Every one of those routes is refused for id 0 — `DELETE
    /// /api/playlists/0` (`playlists.py:359`), the order PATCH (`:318`) and
    /// the track POST, whose 400 names the library route to use instead
    /// (`:382`). A port that dropped the guard would ship a row of buttons
    /// that fail on every tap.
    ///
    /// **Both halves are asserted**, and that is what makes it discriminate:
    /// a sheet that drew none of the three for anybody would pass the first
    /// half alone.
    @MainActor
    func testMusicMore_FavoritesOffersNoEdit() throws {
        musicMoreReset()
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")

        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.favorites), "Favorites never opened")
        evidence(app, "musicmore-30-favorites-sheet")
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.edit].exists,
            "Favorites is offered an edit, and PATCH /api/playlists/0 is refused"
        )
        XCTAssertFalse(
            containing(app, MusicMoreUnderTest.deletePlaylist).exists,
            "Favorites is offered a delete, and DELETE /api/playlists/0 is refused"
        )
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.moveUp),
            "Favorites is offered a reorder, and its order PATCH is refused"
        )
        closeSheet(app)

        XCTAssertTrue(
            openPlaylistSheet(app, MusicMoreUnderTest.longwater),
            "the real playlist never opened"
        )
        evidence(app, "musicmore-31-real-playlist-sheet")
        XCTAssertTrue(
            app.buttons[MusicMoreUnderTest.edit].exists,
            "the real playlist is offered no edit either, so the first half proved nothing"
        )
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.deletePlaylist).exists,
            "the real playlist is offered no delete"
        )
        XCTAssertGreaterThan(
            countContaining(app, MusicMoreUnderTest.moveUp), 0,
            "the real playlist is offered no reorder"
        )
    }

    // MARK: - 4. add to playlist

    /// The sheet reads which playlists hold this track, and a tap moves the
    /// one that was tapped.
    ///
    /// Track 503 is in `Longwater Drive` and in nothing else, so one row must
    /// read `in this playlist` and the other its count — which is the
    /// memberships read having been decoded at all (another **bare array**,
    /// `music.py:341`).
    @MainActor
    func testMusicMore_AddToPlaylistTogglesMembership() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.ashkeep), "the track sheet never opened")
        app.buttons[MusicMoreUnderTest.addToPlaylist].tap()
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.createNewPlaylist).waitForExistence(timeout: 20),
            "the add-to-playlist sheet never opened"
        )
        evidence(app, "musicmore-40-add-sheet")

        let held = app.descendants(matching: .any).matching(
            NSPredicate(
                format: "label CONTAINS %@ AND label CONTAINS %@",
                MusicMoreUnderTest.longwater, MusicMoreUnderTest.inThisPlaylist
            )
        ).firstMatch
        XCTAssertTrue(held.exists, "the playlist that holds this track does not say so")

        let other = app.descendants(matching: .any).matching(
            NSPredicate(
                format: "label CONTAINS %@ AND label CONTAINS %@",
                MusicMoreUnderTest.quietHours, MusicMoreUnderTest.noTracks
            )
        ).firstMatch
        XCTAssertTrue(other.exists, "the empty playlist does not show its count")

        containing(app, MusicMoreUnderTest.quietHours).tap()
        let moved = app.descendants(matching: .any).matching(
            NSPredicate(
                format: "label CONTAINS %@ AND label CONTAINS %@",
                MusicMoreUnderTest.quietHours, MusicMoreUnderTest.inThisPlaylist
            )
        ).firstMatch
        XCTAssertTrue(
            moved.waitForExistence(timeout: 20),
            "the tapped playlist did not become one this track is in"
        )
        evidence(app, "musicmore-41-add-sheet-after")
        // And the other row is untouched: a toggle that moved both would
        // pass the assertion above on its own.
        XCTAssertTrue(held.exists, "the row that was not tapped changed too")
    }

    /// Adding a track that is **already** in a playlist says so, and does not
    /// say it failed.
    ///
    /// The server answers 409 "track N is already in this playlist"
    /// (`playlists.py:438`), which is the state the person asked for.
    /// Android's own bulk add counts those apart from real failures and says
    /// "added 3 tracks, 1 already in" (`MusicScreen.kt:255`); the single add
    /// from `AddToPlaylistSheet.kt:131` has no such branch and reads
    /// `failed: 409 Conflict: …`.
    ///
    /// The 409 is reached the way it happens in a house: the sheet is opened,
    /// **then** somebody else puts the track in, so this phone's memberships
    /// are a second out of date. `musicmore-prime` sends no push, which is
    /// what keeps them stale.
    @MainActor
    func testMusicMore_AddingATrackAlreadyInItSaysSo() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.sablewind), "the track sheet never opened")
        app.buttons[MusicMoreUnderTest.addToPlaylist].tap()
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.quietHours).waitForExistence(timeout: 20),
            "the add-to-playlist sheet never opened"
        )

        control(
            "musicmore-prime",
            "{\"playlist_id\":42,\"track_id\":505}",
            port: 6601,
            what: "track 505 into Quiet Hours behind the app's back"
        )
        containing(app, MusicMoreUnderTest.quietHours).tap()

        let said = startingWith(app, MusicMoreUnderTest.alreadyInPrefix)
        XCTAssertTrue(
            said.waitForExistence(timeout: 20),
            "a 409 was not reported as the track already being there"
        )
        evidence(app, "musicmore-50-already-in")
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.failedWord),
            "a 409 was reported as a failure"
        )
    }

    /// Favorites goes through the **library** route, not the playlist's.
    ///
    /// `POST /api/playlists/0/tracks` is a 400 whose own detail names
    /// `PATCH /api/music/library/{id} {favorited: true}` instead
    /// (`playlists.py:382`), so a port that forgot Favorites is not a
    /// playlist ships a row that fails every time it is tapped.
    ///
    /// Two things are asserted, because either alone could pass wrongly: the
    /// toast is the **favourite's** wording and not a playlist's, and the
    /// row itself becomes one this track is in — which only a 2xx produces.
    @MainActor
    func testMusicMore_FavoritesAddGoesThroughTheLibrary() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.sablewind), "the track sheet never opened")
        app.buttons[MusicMoreUnderTest.addToPlaylist].tap()
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.favorites).waitForExistence(timeout: 20),
            "the add-to-playlist sheet never opened"
        )

        containing(app, MusicMoreUnderTest.favorites).tap()
        let said = startingWith(app, MusicMoreUnderTest.favoritedPrefix)
        XCTAssertTrue(
            said.waitForExistence(timeout: 20),
            "tapping Favorites did not say the track was favourited — "
                + "the playlist route 400s for id 0"
        )
        evidence(app, "musicmore-60-favorited")
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.failedWord),
            "tapping Favorites reported a failure"
        )
        let moved = app.descendants(matching: .any).matching(
            NSPredicate(
                format: "label CONTAINS %@ AND label CONTAINS %@",
                MusicMoreUnderTest.favorites, MusicMoreUnderTest.inThisPlaylist
            )
        ).firstMatch
        XCTAssertTrue(
            moved.waitForExistence(timeout: 20),
            "the Favorites row did not become one this track is in, so nothing was written"
        )
    }

    /// Creating a playlist from the sheet names it, adds this track to it,
    /// and the tab then lists it.
    ///
    /// Two calls, one gesture (`AddToPlaylistSheet.kt:116`), and the new id
    /// comes **out of the 201's body** — nothing else names it
    /// (`playlists.py:179`). A name the seed does not contain, so the tab
    /// listing it cannot be a seed row read twice.
    @MainActor
    func testMusicMore_CreatePlaylistFromTheSheet() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.sablewind), "the track sheet never opened")
        app.buttons[MusicMoreUnderTest.addToPlaylist].tap()
        let field = app.textFields[MusicMoreUnderTest.playlistNameHint]
        XCTAssertTrue(field.waitForExistence(timeout: 20), "the create field is not on the sheet")

        field.tap()
        clearField(app)
        app.typeText(MusicMoreUnderTest.newPlaylist)
        let create = app.buttons[MusicMoreUnderTest.create]
        XCTAssertTrue(
            create.waitForExistence(timeout: 15),
            "`create` is not offered for a name that has been typed"
        )
        create.tap()

        XCTAssertTrue(
            startingWith(app, MusicMoreUnderTest.createdPrefix).waitForExistence(timeout: 20),
            "the create did not report the name it was given"
        )
        evidence(app, "musicmore-70-created")
        // **One** close, not two: the add sheet replaced the track sheet it
        // was reached from rather than being presented over it, because
        // three `.sheet` modifiers on one view is the hazard
        // `AppShellView.swift:54` wrote down (``MusicMoreSheet``).
        closeSheet(app)
        // The library row was reached by scrolling, so the bar is above the
        // fold now.
        musicMoreScrollTop(app)

        startingWith(app, MusicMoreUnderTest.tabPlaylists).tap()
        let row = containing(app, MusicMoreUnderTest.newPlaylist)
        XCTAssertTrue(
            row.waitForExistence(timeout: 25),
            "the created playlist is not on the tab, so the 201 was not read back"
        )
        evidence(app, "musicmore-71-created-on-tab")
    }

    // MARK: - 5. cover art

    /// A track with a cover says it has one; a track whose cover route 404s
    /// says it has none.
    ///
    /// The cover is fetched through `MusicCoverStore`, not `AsyncImage` —
    /// which would make its own request outside `DomovoiClient`, escape the
    /// cleartext and trust policies and draw nothing with no explanation. A
    /// store wired that way would leave **both** sheets looking the same,
    /// which is why both halves are asserted here.
    ///
    /// `GET …/502/cover` is a real 404, and `.absent` is the settled answer:
    /// `.loading` and `.failed` deliberately do **not** say "no cover art",
    /// because neither is an answer.
    @MainActor
    func testMusicMore_CoverArtDrawsForATrackThatHasOneAndNotForOneWithout() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.ashkeep), "the track sheet never opened")
        let has = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", MusicMoreUnderTest.coverArt))
            .firstMatch
        XCTAssertTrue(
            has.waitForExistence(timeout: 25),
            "the track with a cover does not say it has one"
        )
        evidence(app, "musicmore-80-cover-present")
        closeSheet(app)

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.pellucid), "the second sheet never opened")
        let none = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", MusicMoreUnderTest.noCoverArt))
            .firstMatch
        XCTAssertTrue(
            none.waitForExistence(timeout: 25),
            "the track whose cover route 404s does not say it has none"
        )
        evidence(app, "musicmore-81-cover-absent")
    }

    // MARK: - 6. lyrics

    /// The room card carries the line the room is **on**, out of the room's
    /// own document.
    ///
    /// The parlour is on track 501, whose document is `synced`; track 502's
    /// is `plain` and shares no word with it. So the synced line being on
    /// screen while the plain text is not is the document having been decoded
    /// **and** `state()` having taken the right branch for the right track.
    @MainActor
    func testMusicMore_LyricsFollowTheRoom() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")

        let line = startingWith(app, MusicMoreUnderTest.firstSyncedLine)
        XCTAssertTrue(
            line.waitForExistence(timeout: 30),
            "the playing room's card carries no lyric line"
        )
        evidence(app, "musicmore-90-room-lyric-line")
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.plainFirstWords),
            "another track's plain text is on the room card"
        )
    }

    /// Each lyrics state says the thing that is actually true of it.
    ///
    /// `Lyrics.kt:77`'s `when`, in its order, over three tracks: 502 is
    /// `plain` and its text is shown, 503 is `instrumental`, 504 is `none`
    /// with `checking` set. Collapsing that order is the defect `Lyrics.kt:83`
    /// has a comment for — a synced document whose lines turn out unusable
    /// still has its plain text, and "no lyrics" over a song whose words
    /// arrived is the quiet failure.
    @MainActor
    func testMusicMore_LyricsSayWhatThereIsForEachState() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.pellucid), "502's sheet never opened")
        XCTAssertTrue(
            startingWith(app, MusicMoreUnderTest.plainFirstWords).waitForExistence(timeout: 25),
            "a plain document's text is not shown"
        )
        evidence(app, "musicmore-100-lyrics-plain")
        closeSheet(app)

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.ashkeep), "503's sheet never opened")
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.instrumental).waitForExistence(timeout: 25),
            "an instrumental is not said to be one"
        )
        evidence(app, "musicmore-101-lyrics-instrumental")
        // And it is not the plain branch wearing another sentence: 502's
        // words must be gone with 502's sheet.
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.plainFirstWords),
            "the previous track's words are still on screen"
        )
        closeSheet(app)

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.quillon), "504's sheet never opened")
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.looking).waitForExistence(timeout: 25),
            "a document still being looked for does not say so"
        )
        evidence(app, "musicmore-102-lyrics-looking")
    }

    /// A house that refuses the lyrics read draws **nothing at all** — not
    /// even the section header.
    ///
    /// Track 505's read answers 503 with "lyrics are not set up on this
    /// server yet", which `MusicLyricsStore.isHiddenRefusal` reads as hidden.
    /// A header with a `retry` under it would refuse on every tap, which is
    /// the control the owner's rule forbids and the shape
    /// `LyricsPanel.kt:395` guards.
    ///
    /// The second half opens a track whose read **does** answer, so a sheet
    /// that simply never draws a lyrics header cannot pass this.
    @MainActor
    func testMusicMore_LyricsAreHiddenWhenTheHouseRefuses() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.sablewind), "505's sheet never opened")
        // Give the read time to come back and be shown, so this is not an
        // assertion about a frame that had not drawn yet.
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.addToPlaylist).waitForExistence(timeout: 25),
            "505's sheet did not finish drawing"
        )
        evidence(app, "musicmore-110-lyrics-refused")
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.showLyrics].exists,
            "a refused read drew a lyrics header"
        )
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.hideLyrics].exists,
            "a refused read drew a lyrics header"
        )
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.retry].exists,
            "a refused read offered a retry that would refuse every time"
        )
        closeSheet(app)

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.pellucid), "502's sheet never opened")
        let header = app.buttons[MusicMoreUnderTest.hideLyrics]
        XCTAssertTrue(
            header.waitForExistence(timeout: 25),
            "no sheet draws a lyrics header at all, so the first half proved nothing"
        )
        evidence(app, "musicmore-111-lyrics-header-present")
    }

    /// A room on a library track carries a lyric line; a room playing a
    /// stream carries none.
    ///
    /// The cellar's room-lyrics answer deliberately carries a **document
    /// beside a null `track_id`** — a contradiction no real house would
    /// write and the only shape that tells a kept guard from a dropped one.
    /// `MusicRoomLyricsModel.apply` keeps a doc only when the track is not
    /// nil, and `isHidden` is true for a nil track; drop either and
    /// `musicmoreCellarLine` goes on the streaming room's card.
    @MainActor
    func testMusicMore_RoomCardShowsALineForALibraryTrackAndNoneForAStream() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")

        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.stream).waitForExistence(timeout: 30),
            "the streaming room's card is not on screen, so there is nothing to assert about it"
        )
        XCTAssertTrue(
            startingWith(app, MusicMoreUnderTest.firstSyncedLine).waitForExistence(timeout: 30),
            "the library room's card carries no lyric line"
        )
        evidence(app, "musicmore-120-room-cards")
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.cellarLine),
            "the streaming room's card carries words for a track it has no id for"
        )
    }

    // MARK: - 7. playing a playlist

    /// `play` goes to the room the chips were showing as picked, not to
    /// whichever room the array happens to start with.
    ///
    /// The seed's first room is `parlour`; this picks `cellar`, so a model
    /// that sent `rooms.first` would name the wrong one
    /// (`PlaylistDrawer.kt:86`).
    @MainActor
    func testMusicMore_PlaylistPlaysInTheRoomTheSheetOffers() throws {
        musicMoreReset()
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")
        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.longwater), "the sheet never opened")

        let chip = app.buttons[MusicMoreUnderTest.cellarRoom]
        XCTAssertTrue(chip.waitForExistence(timeout: 20), "the sheet offers no room chips")
        chip.tap()
        app.buttons[MusicMoreUnderTest.play].tap()

        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.playingInCellar).waitForExistence(timeout: 20),
            "the play named a room other than the chip that was picked"
        )
        evidence(app, "musicmore-130-played-in-picked-room")
    }

    /// With no room online, `play` and `shuffle all` are **absent**, and the
    /// section label says why.
    ///
    /// `MusicPlaylistDetailModel.play(shuffle:rooms:)` refuses a room the
    /// server never reported, so a button drawn here would have nothing to
    /// send to (`PlaylistDrawer.kt:222`).
    @MainActor
    func testMusicMore_NoRoomsNoPlayButton() throws {
        musicMoreReset()
        // After the reset, because the reset puts the rooms back.
        control(
            "musicmore-rooms", "{\"rooms\":\"empty\"}",
            port: 6601, what: "a house with no room online"
        )
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")
        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.longwater), "the sheet never opened")
        evidence(app, "musicmore-140-no-rooms")

        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.noRoomsOnlineLabel).exists,
            "the section does not say the room list is empty"
        )
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.play].exists,
            "`play` is drawn with nowhere to send it"
        )
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.shuffleAll].exists,
            "`shuffle all` is drawn with nowhere to send it"
        )
        // The tracks are still listed: the sheet is not broken, only the two
        // controls are gone.
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.veldrassen).waitForExistence(timeout: 20),
            "the sheet stopped listing its tracks as well"
        )
        // Put it back for whatever case runs next in this shared house.
        control("musicmore-rooms", "{}", port: 6601, what: "the rooms back")
    }

    // MARK: - 8. the tab that is not offered

    /// The tab bar does not offer `player`, and does offer `playlists`.
    ///
    /// Nothing in this app conforms to `MusicMorePlayerEngine`, so
    /// `MusicTab.shown(hasPlayer:)` leaves that tab out entirely — not
    /// greyed, not empty. A bar built from `MusicTab.allCases` would offer
    /// it, and `MusicPlayerTabView` behind it would draw a transport row over
    /// nothing.
    ///
    /// **Delete this case when the engine lands.** It is an assertion about
    /// an absence that is deliberate today and will be wrong the day
    /// `use(_:)` is called with a real engine.
    @MainActor
    func testMusicMore_ThePlayerTabIsNotOfferedWithoutAnEngine() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        evidence(app, "musicmore-150-tab-bar")
        XCTAssertTrue(landed, "Music never drew")

        XCTAssertTrue(
            startingWith(app, MusicMoreUnderTest.tabPlaylists).exists,
            "the playlists tab is not offered, so this says nothing about the player tab"
        )
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.tabPlayer].exists,
            "the tab bar offers a player tab with no engine behind it"
        )
    }

    // MARK: - 9. Dynamic Type

    /// The playlists tab's own text grows with the type size.
    ///
    /// **Measured, not read off a headline.** A named Dynamic Type audit
    /// finding has twice been a false positive on this track, so this
    /// compares one row's height at `ordinaryTypeSize` against
    /// `AccessibilityXXXL`. A fixed `frame(width:)` on a glyph, a
    /// `minimumScaleFactor`, or a row that should become a column all show up
    /// as a height that did not move.
    ///
    /// The **cover** is expected not to grow and is not measured: it is a
    /// picture, not a glyph, and a 40-point thumbnail scaled 3.6x would push
    /// the title it belongs to off the row. `MusicCoverView`'s doc comment is
    /// where that is argued.
    @MainActor
    func testMusicMore_PlaylistsSurviveAnAccessibilityTextSize() throws {
        func rowHeight(_ mode: String, _ name: String) throws -> CGFloat {
            musicMoreReset()
            let (app, landed) = openPlaylistsTab(mode)
            XCTAssertTrue(landed, "the playlists tab never drew a row at \(mode)")
            let row = containing(app, MusicMoreUnderTest.longwaterNote)
            XCTAssertTrue(row.waitForExistence(timeout: 25), "no row to measure at \(mode)")
            evidence(app, name)
            let height = row.frame.height
            app.terminate()
            return height
        }

        let ordinary = try rowHeight(Self.ordinaryTypeSize, "musicmore-160-playlists-ordinary")
        let huge = try rowHeight(
            "UICTContentSizeCategoryAccessibilityXXXL",
            "musicmore-161-playlists-ax5"
        )

        XCTAssertGreaterThan(
            huge, ordinary * 1.5,
            "the playlist row's text did not grow with the type size "
                + "(\(ordinary) -> \(huge) points)"
        )
    }

    // MARK: - 10. the audit

    /// Apple's own accessibility audit over the playlists tab and the sheet
    /// it opens.
    ///
    /// Classified **by signature**, never by a pinned total: a floor that is
    /// a number goes stale the first time a row is added, and
    /// `floorAndRest(_:)` is where the known anonymous findings live. `rest`
    /// is what this case is about — a contrast failure on the new ink, an
    /// icon-only control with no `accessibilityLabel`, or a sheet that is not
    /// modal (which the audit reports as a finding naming an element
    /// *behind* the sheet).
    @MainActor
    func testMusicMore_AuditThePlaylistsTab() throws {
        guard #available(iOS 17.0, *) else {
            throw XCTSkip("performAccessibilityAudit needs iOS 17")
        }
        musicMoreReset()
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")

        let tab = try auditIssues(app, "musicmore-playlists-tab")
        let onTab = Self.floorAndRest(tab)
        evidence(app, "musicmore-170-audit-tab")
        XCTAssertEqual(
            [], onTab.rest,
            "the playlists tab has \(onTab.rest.count) findings beyond the known floor "
                + "of \(onTab.floor): \(onTab.rest)"
        )

        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.longwater), "the sheet never opened")
        let sheet = try auditIssues(app, "musicmore-playlist-sheet")
        let onSheet = Self.floorAndRest(sheet)
        evidence(app, "musicmore-171-audit-sheet")
        XCTAssertEqual(
            [], onSheet.rest,
            "the playlist sheet has \(onSheet.rest.count) findings beyond the known floor "
                + "of \(onSheet.floor): \(onSheet.rest)"
        )
    }
}

/// What the stub house's music half holds, by the words that appear on
/// screen.
///
/// Spelt out here rather than imported from `DomovoiKit`, like every other
/// string this file matches on: a query that silently followed a renamed
/// constant would stop testing what it claims to. These are the stub's own
/// (`design-notes/ios/stubhouse/fixtures/musicmore.py`) and the app's
/// pinned wordings, written out.
enum MusicMoreUnderTest {

    // the tabs
    static let tabPlaylists = "playlists"
    static let tabPlayer = "player"

    // the playlists
    static let favorites = "Favorites"
    static let longwater = "Longwater Drive"
    static let longwaterNote = "songs for the Vantersk road"
    static let quietHours = "Quiet Hours"
    static let derivedFromFavorites = "derived from your favorites"
    /// Longwater Drive's count — the line it must **not** show, because it
    /// has a description.
    static let threeTracks = "3 tracks"
    static let noTracks = "0 tracks"
    static let inThisPlaylist = "in this playlist"
    static let newPlaylist = "Grindlewood Nights"

    // the tracks
    static let veldrassen = "Veldrassen Overture"
    static let pellucid = "Pellucid Marrow"
    static let ashkeep = "Thrumming Ashkeep"
    static let quillon = "Quillon Drift"
    static let sablewind = "Sablewind Figure"
    static let stream = "Vantersk Night Radio"

    // the rooms
    static let cellarRoom = "cellar"
    static let playingInCellar = "in cellar"

    // the lyrics
    static let firstSyncedLine = "Hold the greenlit awning"
    static let plainFirstWords = "Pellucid marrow, bleach the rafters"
    /// The whole phrase, not the first word: `instrumental break` is a gap
    /// line's own label elsewhere in the panel.
    static let instrumental = "no words to show"
    static let looking = "looking for lyrics"
    static let showLyrics = "show lyrics"
    static let hideLyrics = "hide lyrics"
    static let retry = "retry"
    /// The words the cellar's contradictory document carries. Their presence
    /// **anywhere** is the failure.
    static let cellarLine = "Cellar words nobody should see"

    // the covers
    static let coverArt = "cover art"
    static let noCoverArt = "no cover art"

    // the controls
    static let close = "close"
    static let edit = "edit"
    static let deletePlaylist = "delete playlist"
    static let moveUp = "move up"
    static let play = "play"
    static let shuffleAll = "shuffle all"
    static let noRoomsOnlineLabel = "play in room · no rooms online"
    static let addToPlaylist = "add to playlist"
    static let createNewPlaylist = "create new playlist"
    static let playlistNameHint = "playlist name…"
    static let create = "create"

    // the sentences a toast says, as prefixes
    //
    // A query string over 128 characters throws (train S001), and these are
    // matched with `startingWith`, so each is the leading phrase rather than
    // the whole sentence.
    static let alreadyInPrefix = "\"Sablewind Figure\" is already in"
    static let favoritedPrefix = "favorited \"Sablewind Figure\""
    static let createdPrefix = "created Grindlewood Nights"
    /// The word a 409 or a favourite must **not** produce.
    static let failedWord = "failed"
}
