import XCTest

/// Music's second half: the playlists tab and its two sheets, the cover art,
/// the room-followed lyrics, and the tab that is deliberately not offered.
///
/// Every case is named `testMusicMore_…` so a train can hand its result back
/// to this item, and every piece of evidence is named `musicmore-…`.
///
/// ### Which track each case uses, and why it matters
///
/// `fixtures/musicmore.py` seeds six tracks whose words nothing else in the
/// house uses. **Track 501 is the one the parlour is playing**, so its title
/// is on screen twice — once on its library row and once on the room card —
/// and `containing(app, …).firstMatch` cannot say which. So no case here
/// opens 501's library row: the membership cases use **503**, the cover and
/// plain-lyrics cases **502**, the Favorites and create cases **505**, and
/// the timing-chip case **506**, each of which appears exactly once.
///
/// **Track 506 is synced and no room plays it** — the only combination that
/// reaches the no-room branch, where the timing chips would write nowhere and
/// are therefore not drawn.
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
        guard !tab.waitForExistence(timeout: 30) else { return (app, true) }
        // **The strip is below the room cards, and at an accessibility size
        // that is below the fold.** Train S007 failed here and the message
        // said "the playlists tab never drew a row", which reads as a screen
        // that did not answer; the tree beside it said otherwise. At
        // AX5 the three room cards above the strip are 1520 points tall
        // against a 400-point content area, so the strip is not off screen —
        // it is *unbuilt*, the lazy foot of a `LazyVStack` nobody has
        // scrolled to. Scrolling to it is what a person does, so the case
        // does it too, rather than the screen being called broken.
        //
        // Not an app defect: Android stacks `NowPlayingCards` above
        // `ScrollableTabRow` in the same order, and at the ordinary size the
        // strip is 726 points down a 770-point window, which is where it has
        // always been.
        return (app, musicMoreScroll(app, to: tab))
    }

    /// Open Music and move to the playlists tab.
    @MainActor
    func openPlaylistsTab(_ mode: String = StubHouseUITests.ordinaryTypeSize)
        -> (app: XCUIApplication, landed: Bool) {
        let (app, landed) = openMusicMore(mode)
        guard landed else { return (app, false) }
        startingWith(app, MusicMoreUnderTest.tabPlaylists).tap()
        let row = containing(app, MusicMoreUnderTest.longwater)
        if row.waitForExistence(timeout: 30) { return (app, true) }
        // The rows are under the strip, so at an accessibility size they are
        // unbuilt for the same reason the strip was — see `openMusicMore`.
        return (app, musicMoreScroll(app, to: row))
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

    /// What the open sheet says about **its own** write.
    ///
    /// Matched by `identifier`, which only `MusicMoreSheetStatus` carries, so
    /// nothing behind the sheet can satisfy it. That matters: the shell's
    /// toast is an overlay on the shell's content and a page sheet covers it,
    /// so these outcomes used to be drawn behind the sheet that was still up
    /// — and four of these cases passed anyway, because
    /// `app.descendants(matching: .any)` reaches an element behind a sheet
    /// (NIGHT-RULES §5). Asserted this way they fail if the message is
    /// invisible.
    @MainActor
    func sheetSaid(
        _ app: XCUIApplication,
        startsWith prefix: String,
        timeout: Double = 20
    ) -> Bool {
        app.staticTexts.matching(
            NSPredicate(
                format: "identifier == %@ AND label BEGINSWITH %@",
                MusicMoreUnderTest.sheetStatus, prefix
            )
        ).firstMatch.waitForExistence(timeout: timeout)
    }

    /// ``sheetSaid(_:startsWith:timeout:)`` for a phrase inside the sentence
    /// rather than at the front of it.
    @MainActor
    func sheetSaid(
        _ app: XCUIApplication,
        containing phrase: String,
        timeout: Double = 20
    ) -> Bool {
        app.staticTexts.matching(
            NSPredicate(
                format: "identifier == %@ AND label CONTAINS %@",
                MusicMoreUnderTest.sheetStatus, phrase
            )
        ).firstMatch.waitForExistence(timeout: timeout)
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

        XCTAssertTrue(
            sheetSaid(app, startsWith: MusicMoreUnderTest.alreadyInPrefix),
            "the sheet did not itself report the 409 as the track already being there"
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
        XCTAssertTrue(
            sheetSaid(app, startsWith: MusicMoreUnderTest.favoritedPrefix),
            "the sheet did not say the track was favourited — "
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
            sheetSaid(app, startsWith: MusicMoreUnderTest.createdPrefix),
            "the sheet did not report the name the create was given"
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

        let line = startingWith(app, MusicMoreUnderTest.activeSyncedLine)
        XCTAssertTrue(
            line.waitForExistence(timeout: 30),
            "the playing room's card carries no lyric line"
        )
        evidence(app, "musicmore-90-room-lyric-line")
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.plainFirstWords),
            "another track's plain text is on the room card"
        )
        // The line is the one the room's CLOCK is on, not the document's
        // first: the parlour is seeded past the last timed line, so a card
        // that simply drew `lines.first` would show this instead.
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.firstSyncedLine),
            "the card shows the document's first line rather than the room's position"
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
        // **Synchronised on the absence, not sampled after a sibling.**
        //
        // Waiting for `add to playlist` said nothing about the 503 having
        // landed: `HomeLink(addToPlaylist)` and the lyrics header are emitted
        // in the same body pass, and until the refusal arrives `result` is
        // nil, `isHidden` is false, and the panel legitimately draws its
        // header over a spinner. So the four `.exists` checks raced one round
        // trip and would fail on a loaded Simulator as though the app had
        // drawn a header over a refused read. The sheet opens with
        // `open: true`, so the header is `hide lyrics`, and its going away is
        // the refusal landing.
        XCTAssertTrue(
            waitUntilGone(app.buttons[MusicMoreUnderTest.hideLyrics], timeout: 25),
            "a refused read left the lyrics header up"
        )
        evidence(app, "musicmore-110-lyrics-refused")
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.showLyrics].exists,
            "a refused read drew a lyrics header"
        )
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.retry].exists,
            "a refused read offered a retry that would refuse every time"
        )
        // The rest of the sheet is there — only the panel is gone.
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.addToPlaylist).exists,
            "505's sheet did not draw at all, so the absences prove nothing"
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

    /// A track sheet for a song **no room is playing** shows the words and
    /// the source, and **no timing chips**.
    ///
    /// Track 506 is synced and no room plays it, which is the only
    /// combination that reaches the no-room branch. An offset is stored per
    /// room (`MusicLyricsNudgeStore.set` returns without writing for a blank
    /// one), so with no room behind the panel the three chips were three taps
    /// that changed nothing and did not even move the label beside them —
    /// the control the owner's rule forbids, and the panel's own doc comment
    /// claimed it was not drawn.
    ///
    /// The panel having drawn at all is asserted first, so a sheet that
    /// simply failed to show lyrics cannot pass this. The other half — that
    /// the chips **are** offered once there is a room — is
    /// `MusicRoomLyricsModel.maySetTiming` and is asserted in
    /// `MusicMoreModelTests`: reaching it through the UI would mean opening
    /// the library row of the very track the parlour is playing, whose title
    /// is on screen twice and which no case here opens (the header).
    @MainActor
    func testMusicMore_NoRoomNoTimingChips() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.harrowgate), "506's sheet never opened")
        XCTAssertTrue(
            startingWith(app, MusicMoreUnderTest.harrowgateLine).waitForExistence(timeout: 25),
            "506's synced document drew no lines, so there is nothing to assert about its chips"
        )
        evidence(app, "musicmore-115-no-room-no-timing")
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.lyricsEarlier].exists,
            "a panel with no room behind it offered a timing chip that writes nowhere"
        )
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.resetLyricsTiming].exists,
            "a panel with no room behind it offered `reset` for an offset it cannot store"
        )
        // And it is not the whole footer that is missing: the source label is
        // the panel's own and is drawn beside where the chips would be.
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.sidecarSource).exists,
            "the panel drew no footer at all, so the absent chips prove nothing"
        )
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
            startingWith(app, MusicMoreUnderTest.activeSyncedLine).waitForExistence(timeout: 30),
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
            sheetSaid(app, containing: MusicMoreUnderTest.playingInCellar),
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

    /// **The measurement the audit's Dynamic Type finding claims to be
    /// about** — the tab strip's own four words.
    ///
    /// Train S007's audit named all four tab buttons: "User will not be able
    /// to change the font size of this element", with the element attached.
    /// They are the first named Dynamic Type findings this item has seen, and
    /// this track has now had three: `favorites only`
    /// (``knownScalingFalsePositive``) and the five leaves
    /// ``testTheVideosGridTextGrowsAtAX5`` measures were both false. The
    /// shape is the same here — a `Text` with `.fixedSize(horizontal: false,
    /// vertical: true)` inside a `Button` that merges it, so the audit has no
    /// font to read on the element it can see — but a belief is not a
    /// measurement, so this is the measurement.
    ///
    /// **Why the assertion is "bigger", and not "half again bigger" like the
    /// playlist row's.** All four buttons measure exactly 44.0 at the
    /// ordinary size, and 44 is ``MusicTabBar``'s `minHeight` floor, not the
    /// text's height — a `.subheadline` leaf there is about 18 points. So a
    /// ratio would be a ratio against the floor: `.subheadline` at AX5 is
    /// around 49 points, which is the control growing by every point it has
    /// and still only 1.1x of 44. What discriminates is the floor itself —
    /// a strip whose font could not change would measure 44.0 at **both**
    /// sizes, because the floor is the only thing holding it up.
    @MainActor
    func testMusicMore_TheTabStripTextGrowsAtAX5() throws {
        func heights(_ mode: String) -> [String: CGFloat] {
            let (app, landed) = openMusicMore(mode)
            XCTAssertTrue(landed, "the tab strip never drew at \(mode)")
            var out: [String: CGFloat] = [:]
            for word in MusicMoreUnderTest.tabWords {
                let button = app.buttons
                    .matching(NSPredicate(format: "label BEGINSWITH %@", word))
                    .firstMatch
                if button.exists { out[word] = button.frame.height }
            }
            evidence(app, "musicmore-16A-tabstrip-\(mode == Self.ordinaryTypeSize ? "large" : "ax5")")
            app.terminate()
            return out
        }

        musicMoreReset()
        let ordinary = heights(Self.ordinaryTypeSize)
        let huge = heights("UICTContentSizeCategoryAccessibilityXXXL")
        for word in MusicMoreUnderTest.tabWords {
            // Printed whatever the verdict: a run where the numbers are
            // wanted is a run where the case already failed, and a log that
            // only prints on failure prints nothing the day the case is
            // right for the wrong reason.
            let large = ordinary[word].map { "\($0)" } ?? "not on screen"
            let ax5 = huge[word].map { "\($0)" } ?? "not on screen"
            print("SCALE [musicmore-tab] \(word) large=\(large) ax5=\(ax5)")
        }

        // Every one of the four, measured on both sides: a strip where one
        // word is pinned and three grow is still a defect, and an average
        // would hide it.
        for word in MusicMoreUnderTest.tabWords {
            guard let small = ordinary[word] else {
                XCTFail("the '\(word)' tab was not on screen at the ordinary size")
                continue
            }
            guard let big = huge[word] else {
                XCTFail("the '\(word)' tab was not on screen at AX5")
                continue
            }
            // Two points of margin, not none: the measurement comes back in
            // thirds of a point and a strict `>` on two equal 44.0s is a
            // comparison of floats.
            XCTAssertGreaterThan(
                big, small + 2,
                "the '\(word)' tab did not grow with the type size "
                    + "(\(small) -> \(big) points) — it is still on its 44-point "
                    + "floor at AX5, so the audit's Dynamic Type finding "
                    + "against it is real after all"
            )
        }
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
        let tabLeft = musicMoreUnexplained(onTab.rest, in: app)
        XCTAssertEqual(
            [], tabLeft,
            "the playlists tab has findings beyond the known floor of \(onTab.floor) "
                + "that neither a measurement nor the viewport edge explains: \(tabLeft)"
        )

        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.longwater), "the sheet never opened")
        let sheet = try auditIssues(app, "musicmore-playlist-sheet")
        let onSheet = Self.floorAndRest(sheet)
        evidence(app, "musicmore-171-audit-sheet")
        let sheetLeft = musicMoreUnexplained(onSheet.rest, in: app)
        XCTAssertEqual(
            [], sheetLeft,
            "the playlist sheet has findings beyond the known floor of \(onSheet.floor) "
                + "that neither a measurement nor the viewport edge explains: \(sheetLeft)"
        )
    }

    /// The named findings this item has an **answer** for, taken out — and
    /// everything else left in.
    ///
    /// Three exclusions, each with its reason, and none of them "the audit is
    /// usually wrong". The shape is `testTheVideosGridAudits`'s, which is
    /// where both of the first two were settled first.
    ///
    /// Anything else — a contrast failure on a card that is fully on screen,
    /// an icon-only control with no label, a finding naming an element
    /// *behind* the sheet (which is how a non-modal sheet reports) — is
    /// returned and fails the case.
    @MainActor
    func musicMoreUnexplained(_ rest: [String], in app: XCUIApplication) -> [String] {
        // The outer scroll view: the tallest on screen. The others are the
        // tab strip, lying on its side, and the phone bar.
        let page = app.scrollViews.allElementsBoundByIndex
            .max(by: { $0.frame.height < $1.frame.height })?.frame ?? .null

        return rest.filter { line in
            // 1. A Dynamic Type headline **with** an element. The four tab
            //    buttons arrive this way and
            //    ``testMusicMore_TheTabStripTextGrowsAtAX5`` measures those
            //    very four at both sizes, which is the test; this is only
            //    the classification that makes room for it. Nothing is
            //    excluded here on a belief — if the strip ever does stop
            //    scaling, the measurement fails and this line is why the
            //    audit stayed quiet, not what kept it quiet.
            if Self.knownAnonymousHeadlines.contains(where: { line.hasPrefix($0) }) {
                return false
            }
            // 2. A contrast finding against an element one of the scroll
            //    view's own edges is cutting. Half an element has no pixels
            //    to read, which is what the audit reports. S007's single
            //    contrast finding was `playing in parlour` at y
            //    184.3–198.7 against a content area starting at 197.3 — 1.3
            //    points of it visible — while `playing in cellar`, the same
            //    string in the same colour on the same card two rooms down
            //    and fully on screen, passed. One row further down the
            //    scroll and it is a different row's turn.
            if line.hasPrefix("Contrast failed"), let box = Self.frame(in: line),
               box.intersects(page), !page.contains(box) {
                return false
            }
            // 3. The phone-source segmented control, which is the PhoneMedia
            //    item's (`PhoneSourceSwitchView.swift:171`) and not this
            //    one's to edit — Apple reads its label `127.0.0.1:6601` as
            //    not human-readable, and it is right. Recorded in
            //    `overnight/musicmore/DECISIONS.md` under "Needs a shared
            //    change" rather than silently fixed in another item's file.
            //    Pinned to that one control by its identifier: a second
            //    unreadable label anywhere on Music still fails this case.
            if line.hasPrefix("Label not human-readable"),
               line.contains("id='\(MusicMoreUnderTest.phoneSourceServer)'") {
                return false
            }
            return true
        }
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

    /// The four words the strip offers with no engine registered, each one
    /// the **start** of a button's label: two of them carry a count
    /// (`library · 6`, `playlists · 3`) and the count moves with the
    /// fixtures, so a whole label would be a second thing to keep true.
    static let tabWords = ["library", "room queue", "playlists", "stats"]

    /// The phone-source switch's server segment, by identifier
    /// (`PhoneSourceSwitchView.swift:171`). Not this item's control; it is
    /// here because it is on Music's screen and so in Music's audit, and an
    /// identifier is the only stable handle on it — its label is the house's
    /// address.
    static let phoneSourceServer = "phone-source-server"

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
    /// The parlour's **permanently active** line.
    ///
    /// Not the first line. The room is seeded past the last timed line
    /// (26 s) at 30 s, because a room's position runs on at wall-clock rate:
    /// any seed inside the line list makes "which line is showing" a question
    /// about how long the Simulator took, and the active line would move on
    /// twice inside a 30 s wait.
    static let activeSyncedLine = "Down past the hollow tide"
    /// 501's FIRST line, which at the seeded position must **not** be the one
    /// the card shows — the half of the assertion that discriminates.
    static let firstSyncedLine = "Hold the greenlit awning"
    /// 506: synced lyrics, no room playing it.
    static let harrowgate = "Harrowgate Lantern"
    static let harrowgateLine = "Lanterns down the harrowgate"
    static let lyricsEarlier = "lyrics earlier"
    static let resetLyricsTiming = "reset lyrics timing"
    /// 501's and 506's `source_label`, which the panel's footer draws.
    static let sidecarSource = "lyric file beside the song"
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
    /// What a sheet says about its own write, **inside** the sheet
    /// (`MusicMoreSheetStatus.identifier`). Asserted by identifier rather
    /// than by a label query, because a label query reaches elements behind
    /// the sheet and that is exactly the bug this replaced.
    static let sheetStatus = "musicmore-sheet-status"

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
