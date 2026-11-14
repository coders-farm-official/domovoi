import XCTest

/// The Podcasts screen, driven against the stub house.
///
/// Every case is `testPodcasts_…` and every piece of evidence is
/// `podcasts-…`, which is how a train hands these results back to this item
/// (NIGHT-RULES §2). The fixture is `design-notes/ios/stubhouse/fixtures/podcasts.py`
/// and its seed was written so that each assertion below has a word only one
/// row can produce.
///
/// **Deliberately few cases, each asserting several things.** A launch of
/// this app is about twenty seconds of a train's time, so the fixture is
/// seeded such that most of these need no mutation at all; the four that do
/// mutate call `control("reset-fixtures", …)` first, and so does every case
/// that asserts an exact total, because the cases share one house for a whole
/// run and run in name order.
///
/// ### What is **not** tested here, and why
///
/// **That a sound comes out.** The play control, the resume dialog and the
/// position read/write landed in the 2026-11-03 batch (D46/D49), and
/// `testPodcasts_AnEpisodeWithNoFileSaysWhatTheDownloadIsDoing` now asserts
/// the button is there on a row with a file and **not** on one without — the
/// case was written as an absence and is inverted rather than deleted. What a
/// Simulator still cannot add is whether `AVPlayer` reached `.readyToPlay`:
/// `testPlayer_TheStubHousePlaysTheFixturesAudio` owns the bytes and
/// `PodcastPlayTests` owns the resume arithmetic — twelve cases over a real
/// ``PlayerModel``, added in review 1 of this batch. This sentence named
/// `PodcastsModelTests` while that file had no case naming play or resume,
/// which is the kind of pointer that stops the next reader looking (M2).
///
/// **A `podcasts.changed` push reloading the grid.** The stub house can push
/// one hard-coded frame type and a fixture module cannot push its own
/// (`stubhouse.py:1549-1560`), so no case here can prove it. The wiring is
/// covered by a package test driving `Pager.received(_:)` with a `StateEvent`
/// of each type; the gap is written down so a green train is not read as
/// proof that the live reload works.
extension StubHouseUITests {

    // MARK: - getting there

    /// Podcasts is **not on the phone bar** (`defaultPhoneBar`,
    /// `Route.swift:118`), so the seeded push is the way in — exactly as
    /// `openVideos` does it. The count line is what says the shows read
    /// landed: it is drawn only once the pager has answered
    /// (``PodcastsModel/countText``), so a screen with a header and no count
    /// line is a read that did not come back.
    @MainActor
    func openPodcasts(
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        delay: Double? = nil
    ) -> (app: XCUIApplication, landed: Bool) {
        let app = launch(mode: mode, push: "Podcasts", pushDelay: delay ?? Self.pushDelay)
        let count = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label BEGINSWITH %@", "showing "))
            .firstMatch
        return (app, count.waitForExistence(timeout: 40))
    }

    /// Scroll the screen's own scroll view until a lazy row is **built**.
    ///
    /// Not ``scrollUntilBuilt(_:_:flicks:)``, which scrolls
    /// `app.collectionViews.firstMatch`: that is a `List`, and both of this
    /// screen's lists are a `ScrollView` over a `LazyVStack`/`LazyVGrid`
    /// (``FilesView``'s shape, because neither needs swipe actions). There is
    /// no collection view to scroll, so that helper would report "no list to
    /// scroll" and nothing would move.
    ///
    /// The **tallest** scroll view, because a sheet over the grid leaves two
    /// in the tree and the one being read is the full-height one; and a slow
    /// flick, for the reason that helper documents at length — a plain
    /// `swipeUp()` steps over rows, and a held drag is taken by whatever text
    /// field it starts on.
    @MainActor
    func podcastsScrollUntilBuilt(
        _ app: XCUIApplication,
        _ element: XCUIElement,
        flicks: Int = 30
    ) -> Bool {
        let tallest = app.scrollViews.allElementsBoundByIndex
            .max(by: { $0.frame.height < $1.frame.height })
        guard let page = tallest else {
            print("=== NO SCROLL VIEW ON SCREEN — tree follows ===")
            print(app.debugDescription)
            return false
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

    /// One element whose label holds **both** words.
    ///
    /// An episode row is one accessibility element with its children combined
    /// (``PodcastEpisodeRow``), so its label is the title *and* the mono meta
    /// line joined by whatever VoiceOver joins them with. Asserting the
    /// joined string would be asserting UIKit's separator; asserting two
    /// `CONTAINS` on one element is asserting the row.
    @MainActor
    func podcastRow(_ app: XCUIApplication, _ title: String, _ meta: String) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(
                NSPredicate(
                    format: "label CONTAINS %@ AND label CONTAINS %@", title, meta
                )
            )
            .firstMatch
    }

    /// The shows field, then the needle, with the caret at the end.
    @MainActor
    func typeIntoPodcastSearch(_ app: XCUIApplication, _ needle: String) -> Bool {
        let field = app.textFields[StubPodcasts.searchShows].firstMatch
        guard field.waitForExistence(timeout: 20) else {
            print("=== NO SHOWS SEARCH FIELD — tree follows ===")
            print(app.debugDescription)
            return false
        }
        // At the end of whatever is there, rather than wherever a tap lands.
        field.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
        app.typeText(needle)
        return true
    }

    /// Open `The Kitchen Table` and wait for its episodes.
    @MainActor
    func openTheKitchenTable(_ app: XCUIApplication) -> Bool {
        let card = app.buttons[StubPodcasts.kitchenTable].firstMatch
        guard card.waitForExistence(timeout: 25) else {
            print("=== NO CARD FOR \(StubPodcasts.kitchenTable) — tree follows ===")
            print(app.debugDescription)
            return false
        }
        card.tap()
        let landed = podcastRow(app, StubPodcasts.newestEpisode, StubPodcasts.newestMeta)
            .waitForExistence(timeout: 25)
        if !landed {
            print("=== THE SHEET DREW NO EPISODES — tree follows ===")
            print(app.debugDescription)
        }
        return landed
    }

    /// The fixture back to its seed, which is also what protects the cases
    /// that run after a mutating one.
    @MainActor
    func resetPodcastHouse() {
        control("reset-fixtures", "{}", port: 6601, what: "the podcast fixture back to its seed")
    }

    /// The directory's failure mode, through the fixture's own control
    /// route. `control(_:_:port:what:)` posts to `/api/_test/<route>`, which
    /// is the house's prefix, so the module claims
    /// `/api/_test/podcasts-mode` the way `news.py` claims its four.
    @MainActor
    func setPodcastDiscovery(_ mode: String) {
        control(
            "podcasts-mode", "{\"discover_mode\": \"\(mode)\"}",
            port: 6601, what: "the directory to answer \(mode)"
        )
    }

    /// How many subscriptions the house answers with, through the same
    /// control route: `short` is three, which is a grid that fits on the
    /// phone with nothing to scroll.
    ///
    /// Only the audit uses it. Apple's audit **moves the page it is
    /// auditing** — on train S007 the grid's content offset went from 137.3
    /// to -326.3 while `performAccessibilityAudit` ran, and the pager loaded
    /// two more pages under it (5711 → 7520 points of content) — and the
    /// contrast readings it took along the way were not readings of this
    /// app's colours: one of eight identical `0/0 downloaded` lines failed
    /// and the other seven passed. A screen with nothing to scroll has one
    /// layout, so every reading is of the thing that is actually drawn.
    @MainActor
    func setPodcastShows(_ mode: String) {
        control(
            "podcasts-mode", "{\"shows_mode\": \"\(mode)\"}",
            port: 6601, what: "the house to answer \(mode) shows"
        )
    }

    /// The unsubscribe's failure mode, through the same control route.
    /// `gone` is the 404 a stale card gets (`podcasts.py:265-269`) — the one
    /// failure on this screen that lands while a sheet is still up.
    @MainActor
    func setPodcastUnsubscribe(_ mode: String) {
        control(
            "podcasts-mode", "{\"unsubscribe_mode\": \"\(mode)\"}",
            port: 6601, what: "the unsubscribe to answer \(mode)"
        )
    }

    // MARK: - the grid

    /// The grid, with the server's own numbers.
    ///
    /// The stub serves sixty-two subscriptions, so page one is fifty of
    /// sixty-two — a number the loaded rows alone cannot produce, which is
    /// what makes this a test of a paged read rather than of a bare array.
    /// `The Kitchen Table`'s card then says `9/57 downloaded` over its
    /// author, and both numbers come from counts the fixture takes per
    /// request rather than stores: a swap of `downloaded_count` and
    /// `episode_count` reads `57/9` and fails here.
    @MainActor
    func testPodcasts_DrawsTheShowsGridWithTheServersCounts() throws {
        resetPodcastHouse()
        let (app, landed) = openPodcasts()
        evidence(app, "podcasts-10-grid")
        XCTAssertFalse(fellBackToLocalMedia(in: app), "the shell gave up on the server")
        XCTAssertTrue(landed, "the shows list never answered — no count line drew")

        XCTAssertTrue(
            app.staticTexts[StubPodcasts.countLine].exists,
            "the count line is not the server's numbers"
        )
        // The subtitle, which is this port's own sentence: Android's says
        // "cast to a room" and neither app's Podcasts screen can
        // (IOS-ONLY-STRINGS.md).
        XCTAssertTrue(
            app.staticTexts[StubPodcasts.subtitle].exists,
            "the header subtitle is not the one this port ships"
        )
        let card = app.buttons[StubPodcasts.kitchenTable].firstMatch
        XCTAssertTrue(card.exists, "no card for the one show with a catalogue")
        XCTAssertEqual(
            StubPodcasts.kitchenDetail, card.value as? String,
            "the card's author and counts are not the server's"
        )
        // Both header actions are there, and `poll now` is not already
        // claiming to be in flight.
        XCTAssertTrue(app.buttons[StubPodcasts.pollNow].exists, "no poll now")
        XCTAssertTrue(app.buttons[StubPodcasts.subscribe].exists, "no subscribe")
    }

    /// Typing an **author's** name narrows the grid, which only the server
    /// can do.
    ///
    /// `Whitlock` is in no title anywhere in the seed — it is
    /// `The Kitchen Table`'s author. The route searches the title, the author
    /// *and* the feed URL (`podcasts.py:171-174`); a filter applied locally
    /// over the fifty loaded titles would match nothing at all, so a screen
    /// that stopped sending `?q=` fails this case on the row being absent
    /// rather than on a count.
    @MainActor
    func testPodcasts_SearchingByAuthorNarrowsTheGrid() throws {
        resetPodcastHouse()
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")
        XCTAssertTrue(typeIntoPodcastSearch(app, StubPodcasts.authorNeedle), "no search field")

        // The count line is what settles the 250 ms debounce **and** the
        // round trip: waiting on the row alone would pass while the grid
        // still held the unfiltered page.
        let narrowed = app.staticTexts[StubPodcasts.oneShowCountLine]
        let fell = narrowed.waitForExistence(timeout: 25)
        evidence(app, "podcasts-11-searched-by-author")
        XCTAssertTrue(fell, "the count line never fell to the server's one match")
        XCTAssertTrue(
            app.buttons[StubPodcasts.kitchenTable].exists,
            "the show whose author matched is not on the grid"
        )
        XCTAssertFalse(
            app.buttons[StubPodcasts.fillerShow].exists,
            "a show that does not match the needle is still on the grid"
        )
        XCTAssertFalse(
            app.staticTexts[StubPodcasts.noShowsMatch].exists,
            "a grid with one match drew the \"no shows match\" note"
        )
    }

    /// A show the feed never titled is **drawn** by its feed URL and
    /// **spoken** as the word.
    ///
    /// `title ?? feed_url ?? "podcast"` (``PodcastShow/displayTitle``). The
    /// seeded row has `title: null` and a feed URL that sorts onto page one,
    /// so the fallback is reachable without scrolling — and the word
    /// `podcast` must **not** be what the card draws, which is what a port
    /// that dropped the middle link of the chain would do.
    ///
    /// ### Why this asks the static text and not the button
    ///
    /// It asked `app.buttons[untitledFeed]` until train S004, where Apple's
    /// audit failed the grid with "The accessibilityLabel of this element is
    /// not human-readable" against that very card: a whole URL as a card's
    /// spoken name is a URL spelt out one slash at a time. The label is now
    /// ``PodcastShow/spokenTitle``, which stops at the word — so the drawn
    /// name is asked of the **text** and the spoken name of the **button**,
    /// and the case got longer rather than weaker. A port that fell straight
    /// to `podcast` still fails the first assert, which is what this case is
    /// named for.
    @MainActor
    func testPodcasts_AShowWithNoTitleFallsBackToItsFeed() throws {
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")
        evidence(app, "podcasts-12-untitled-show")

        XCTAssertTrue(
            app.staticTexts[StubPodcasts.untitledFeed].exists,
            "the show with no title is not drawn by its feed URL"
        )
        XCTAssertFalse(
            app.staticTexts[StubPodcasts.untitledWord].exists,
            "a show with a feed URL fell all the way to the word \"podcast\""
        )
        // And the spoken side of the same card: the word, never the URL.
        XCTAssertTrue(
            app.buttons[StubPodcasts.untitledWord].exists,
            "the untitled card is not spoken as the word \"podcast\""
        )
        XCTAssertFalse(
            app.buttons[StubPodcasts.untitledFeed].exists,
            "the untitled card still reads a whole URL out as its name"
        )
    }

    // MARK: - one show's episodes

    /// Opening a show lists its episodes, with the durations and the chapter
    /// counts the server sent.
    ///
    /// `53:00 · 2 chapters` is three facts in one line: `fmtDur` over
    /// `duration_sec` (3180 seconds, which is `53:00` and not `3180`),
    /// `chapters.count` from a JSONB array, and the order of the two. The
    /// episodes pager is keyed to the show that was tapped, so a pager built
    /// against the wrong id draws another show's catalogue — the seed gives
    /// every other show **none**, which is why that mistake shows up here as
    /// an empty sheet.
    @MainActor
    func testPodcasts_OpeningAShowListsItsEpisodes() throws {
        resetPodcastHouse()
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")
        XCTAssertTrue(openTheKitchenTable(app), "the show's episodes never drew")
        evidence(app, "podcasts-20-episodes")

        XCTAssertTrue(
            app.staticTexts[StubPodcasts.episodeCountLine].exists,
            "the episodes count line is not the server's numbers"
        )
        // The sheet names the show on its body as well as in its title bar,
        // and the author with it — a navigation title truncates where a
        // wrapping `Text` does not.
        XCTAssertTrue(
            app.staticTexts[StubPodcasts.kitchenAuthor].exists,
            "the sheet does not say whose show this is"
        )
        XCTAssertTrue(
            app.buttons[StubPodcasts.unsubscribe].exists,
            "the sheet has no unsubscribe"
        )
    }

    /// An episode the house has not downloaded says what the download is
    /// doing and offers **no play control**, while the one beside it with a
    /// file offers one.
    ///
    /// Two halves, and the second is still the one that matters. Android
    /// draws a filled play button on every row and **disables** it unless
    /// `has_file` (`PodcastsEpisodes.kt:223`); the audio route answers **409
    /// "episode not downloaded yet"** for such a row (`podcasts.py:514`), so
    /// that button cannot do what it says. This port draws no button at all
    /// on that row, which is the owner's rule one step further than Android
    /// took it.
    ///
    /// Until the 2026-11-03 batch the second half read "and not for the
    /// downloaded one either, since nothing in the app owns an audio engine".
    /// D49 gave it one, so that clause is replaced by its opposite rather
    /// than dropped: the downloaded row **must** have a play button, and the
    /// row with no file must not. A single assertion that no button exists
    /// anywhere would now pass only by regressing the feature.
    @MainActor
    func testPodcasts_AnEpisodeWithNoFileSaysWhatTheDownloadIsDoing() throws {
        resetPodcastHouse()
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")
        XCTAssertTrue(openTheKitchenTable(app), "the show's episodes never drew")
        evidence(app, "podcasts-21-pending-episode")

        XCTAssertTrue(
            podcastRow(app, StubPodcasts.pendingEpisode, StubPodcasts.pendingMeta).exists,
            "the undownloaded episode does not say what its download is doing"
        )
        // The downloaded one says so. This was the opposite assertion until
        // the 2026-11-14 batch: the line used to show `download_status` only
        // when there was no file. Android `bd84d970` moved to the web's
        // wording, where every row ends with its download state
        // (`PodFormat.download`), so a row with its file reads `downloaded`.
        XCTAssertTrue(
            podcastRow(app, StubPodcasts.newestEpisode, StubPodcasts.downloadedWord).exists,
            "an episode with its file does not say it is downloaded"
        )
        // The row with no file has no play button. Matched by the **name**,
        // because that is what the button carries
        // (`PlayerStrings.playNamed(_:)`): a bare `play` on fifty-seven rows
        // reads as "play, play, play" and `firstMatch` on it would be
        // whichever row the tree happens to hold first.
        XCTAssertFalse(
            app.buttons[StubPodcasts.playNamed(StubPodcasts.pendingEpisode)].exists,
            "an episode the house has not got offers to play — the audio route "
                + "would answer 409, so that control cannot do what it says"
        )
        // And the row that **does** have its file offers one. Asserted in the
        // same case, so a merge that lost the button fails here instead of
        // leaving an absence assertion that passes over a broken feature.
        XCTAssertTrue(
            app.buttons[StubPodcasts.playNamed(StubPodcasts.newestEpisode)].exists,
            "the downloaded episode has no play control — D46 put one on every "
                + "row the house has the file for"
        )
        // Saving is the same shape (D47): offered where there is a file.
        XCTAssertTrue(
            app.buttons[StubPodcasts.saveControl].exists,
            "no `save to device` on a show with a downloaded episode"
        )
        // The sentence that sent the reader to the dashboard is **gone**. It
        // was drawn under the show's name rather than at the foot, which is
        // what train S004 found the hard way (the foot of a lazy list of
        // fifty-seven rows is in no tree); now that the control it apologised
        // for is right there, the line would be a contradiction.
        XCTAssertFalse(
            app.staticTexts[StubPodcasts.inTheDashboard].exists,
            "the list still says listening is done in the dashboard, over a play button"
        )
        // And the chip is hidden, which is the design (D11): the stub house
        // has no listener picked, and Android shows nothing at all in that
        // state (`val id = listenerId ?: return`).
        XCTAssertFalse(
            containing(app, StubPodcasts.listenerChip).exists,
            "a listening-as chip is drawn with no listener picked"
        )
    }

    /// An episode whose feed did not say how long it is says **no length at
    /// all**.
    ///
    /// Until the 2026-11-14 batch this was `testPodcasts_AnEpisodeWithNoDurationShowsADash`:
    /// `fmtDur(nil)` drew `—`. Android `bd84d970` moved the line to the web's
    /// words, where an unknown length is left out (`PodFormat.length` is
    /// `""`) — so the dash is now as wrong as `0:00` ever was. The row is
    /// still told apart by what it does say: the stub seeds a place in it,
    /// and with no length to count down from that place reads `at 24 min`
    /// (`PodFormat.Progress.Resumed`).
    @MainActor
    func testPodcastDetails_AnEpisodeWithNoDurationLeavesTheLengthOut() throws {
        resetPodcastHouse()
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")
        XCTAssertTrue(openTheKitchenTable(app), "the show's episodes never drew")
        let row = podcastRow(app, StubPodcasts.noDurationEpisode, StubPodcasts.noDurationPlace)
        let built = row.exists || podcastsScrollUntilBuilt(app, row, flicks: 6)
        evidence(app, "podcasts-22-no-duration")

        XCTAssertTrue(built, "the episode with no duration did not say its place")
        XCTAssertFalse(
            podcastRow(app, StubPodcasts.noDurationEpisode, StubPodcasts.emDash).exists,
            "an unknown duration still draws the old em dash"
        )
        XCTAssertFalse(
            podcastRow(app, StubPodcasts.noDurationEpisode, StubPodcasts.zeroDuration).exists,
            "an unknown duration was drawn as a real zero-length episode"
        )
    }

    /// The back catalogue loads as you scroll.
    ///
    /// Episode fifty-seven is the oldest and the list is newest-first, so
    /// `Sourdough in a cold house` is on page two and **does not exist in the
    /// tree** until the lazy list is scrolled far enough to build it and the
    /// scroll trigger has asked for the next page
    /// (``PodcastsModel/shouldLoadMoreEpisodes(lastVisible:)``, which is the
    /// guarded question and not a bare "the last row appeared").
    @MainActor
    func testPodcasts_TheBackCatalogueLoadsAsYouScroll() throws {
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")
        XCTAssertTrue(openTheKitchenTable(app), "the show's episodes never drew")

        let oldest = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS %@", StubPodcasts.oldestEpisode))
            .firstMatch
        XCTAssertFalse(oldest.exists, "page two was already in the tree before any scrolling")
        let built = podcastsScrollUntilBuilt(app, oldest)
        evidence(app, "podcasts-23-back-catalogue")
        XCTAssertTrue(built, "the back catalogue never loaded — the next page was not asked for")
    }

    // MARK: - the writes

    /// `poll now` says what the house found, and the grid changes with it.
    ///
    /// The four counts in the fixture's answer all differ — `polled: 4`,
    /// `new: 2`, `downloaded: 3`, `evicted: 0` — so a toast that read them
    /// off the wrong keys cannot look plausible: it is `3 downloaded, 2 new`
    /// or it is wrong. And the pass gives the one `pending` episode its
    /// audio, so the card's `9/57` becomes `10/57` — which is the half that
    /// proves the list was reloaded after the write and not merely toasted
    /// over.
    ///
    /// The transient `polling…` label is **not** asserted: the stub answers
    /// in microseconds, so a case that waited for it would be waiting on a
    /// race it cannot win. The guard against a second poll is a package test
    /// (``PodcastsModel/poll()``'s `guard !polling`), which is where it can
    /// be asked without one.
    @MainActor
    func testPodcasts_PollNowReportsWhatItFound() throws {
        resetPodcastHouse()
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")

        app.buttons[StubPodcasts.pollNow].firstMatch.tap()
        let toast = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label == %@", StubPodcasts.polledToast))
            .firstMatch
        let said = toast.waitForExistence(timeout: 25)
        evidence(app, "podcasts-30-polled")
        XCTAssertTrue(said, "the poll said nothing, or said the wrong counts")

        let card = app.buttons[StubPodcasts.kitchenTable].firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 20), "the card went away")
        // The reload lands after the toast, so this is a wait and not a
        // read. A poll loop rather than an expectation: there is no event to
        // wait on — `value` is re-read from the app on every access, and the
        // thing being waited for is a changed attribute of an element that
        // already exists.
        var detail = card.value as? String
        for _ in 0..<40 where detail != StubPodcasts.kitchenDetailAfterPoll {
            Thread.sleep(forTimeInterval: 0.5)
            detail = card.value as? String
        }
        evidence(app, "podcasts-31-counts-moved")
        XCTAssertEqual(
            StubPodcasts.kitchenDetailAfterPoll, detail,
            "the grid was not re-read after the poll wrote episodes"
        )
    }

    /// Subscribing by RSS URL adds the show and closes the sheet.
    ///
    /// Four things at once, and each has failed somewhere on this track: the
    /// POST needs `X-Requested-With` or the house answers 403
    /// (`middleware.py:142-172`); the sheet closes **on success only**, which
    /// is why the toast is waited for before the grid is read; the list is
    /// reloaded after the write, so the count rises to sixty-three; and the
    /// new row has `title: null` — the house has not fetched the feed yet —
    /// so its card is drawn by its feed URL, which is the *second* reachable
    /// path through `title ?? feed_url ?? "podcast"`.
    ///
    /// The sheet's own `subscribe` button is told apart from the header's by
    /// `inSheet(_:_:)`, which takes the **hittable** one: the header is still
    /// in the tree behind a presented sheet and `firstMatch` would take it.
    @MainActor
    func testPodcasts_SubscribingByRssUrlAddsTheShow() throws {
        resetPodcastHouse()
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")

        app.buttons[StubPodcasts.subscribe].firstMatch.tap()
        let feedTab = app.buttons[StubPodcasts.byFeedURL].firstMatch
        XCTAssertTrue(feedTab.waitForExistence(timeout: 20), "the subscribe sheet never opened")
        feedTab.tap()

        let field = app.textFields[StubPodcasts.feedPlaceholder].firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 15), "the sheet has no feed field")
        field.tap()
        app.typeText(StubPodcasts.newFeed)
        evidence(app, "podcasts-40-feed-typed")

        guard let act = inSheet(app, StubPodcasts.subscribe) else {
            evidence(app, "podcasts-41-no-subscribe-in-sheet")
            return XCTFail("the sheet's subscribe button is not there, or not hittable")
        }
        act.tap()

        let toast = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label CONTAINS %@", StubPodcasts.subscribedToast))
            .firstMatch
        XCTAssertTrue(toast.waitForExistence(timeout: 25), "nothing was said about the subscribe")
        // **The text, not the button.** The new row has `title: null`, so its
        // card is *drawn* by its feed URL and *spoken* as the word
        // (``PodcastShow/spokenTitle``, the S004 audit fix that
        // `testPodcasts_AShowWithNoTitleFallsBackToItsFeed` pins). This asked
        // `app.buttons[newFeed]` and went on asking it after the label
        // changed, so on train S007 it failed against a grid that was holding
        // the new show perfectly well, one card from the top.
        let added = app.staticTexts[StubPodcasts.newFeed].firstMatch
        XCTAssertTrue(added.waitForExistence(timeout: 25), "the new show is not on the grid")
        // And it is a *card* and not loose text somewhere: the card speaks
        // the word, and before the subscribe there was exactly one card doing
        // that (the seeded untitled feed), so two is the new one.
        let untitledCards = app.descendants(matching: .button)
            .matching(NSPredicate(format: "label == %@", StubPodcasts.untitledWord))
        XCTAssertEqual(
            2, untitledCards.count,
            "the feed URL is on screen but not as a second untitled card"
        )
        XCTAssertTrue(
            app.staticTexts[StubPodcasts.countLineAfterSubscribe].waitForExistence(timeout: 20),
            "the count line did not take the new show in"
        )
        evidence(app, "podcasts-42-subscribed")
    }

    /// The directory search offers what it found, and `add` subscribes to
    /// that hit's own feed.
    ///
    /// `hearth` matches exactly one of the three seeded hits, so this fails
    /// if the needle is not sent (`Dough Rising` would be on screen too). The
    /// `add` button posts the hit's **own** `feed_url` — Android's shape, and
    /// the reason the person sees what they are adding before it is added —
    /// so the row that appears on the grid afterwards is the hit's feed and
    /// not the needle.
    ///
    /// The search is started with the Return key rather than the button
    /// beside it: both call the same thing, and three elements on this screen
    /// answer to the word `search` — the tab, the button, and the keyboard's
    /// own key once the field has focus.
    @MainActor
    func testPodcasts_SearchingTheDirectoryOffersWhatItFound() throws {
        resetPodcastHouse()
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")

        app.buttons[StubPodcasts.subscribe].firstMatch.tap()
        let field = app.textFields[StubPodcasts.namePlaceholder].firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 20), "the subscribe sheet never opened")
        field.tap()
        app.typeText(StubPodcasts.directoryNeedle + "\n")

        let hit = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label == %@", StubPodcasts.directoryHit))
            .firstMatch
        XCTAssertTrue(hit.waitForExistence(timeout: 25), "the directory answered nothing")
        evidence(app, "podcasts-50-directory")
        XCTAssertTrue(
            app.staticTexts[StubPodcasts.directoryHitAuthor].exists,
            "the hit does not say who makes it"
        )
        XCTAssertFalse(
            app.staticTexts[StubPodcasts.otherDirectoryHit].exists,
            "a hit the needle does not match is on screen — the search was not "
                + "sent to the server"
        )

        guard let add = inSheet(app, StubPodcasts.add) else {
            evidence(app, "podcasts-51-no-add")
            return XCTFail("the hit has no add button, or it is not hittable")
        }
        add.tap()
        let toast = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label CONTAINS %@", StubPodcasts.subscribedToast))
            .firstMatch
        XCTAssertTrue(toast.waitForExistence(timeout: 25), "nothing was said about the subscribe")
        // The static text, for the reason the RSS case above gives: a show
        // the house has not fetched yet has no title, so its card draws the
        // feed URL and speaks the word `podcast`. Asking the button for a URL
        // asks for the one thing ``PodcastShow/spokenTitle`` exists to
        // prevent.
        XCTAssertTrue(
            app.staticTexts[StubPodcasts.directoryHitFeed].waitForExistence(timeout: 25),
            "the hit's own feed is not the show that was added"
        )
        // Still discriminating: `Hearth & Hob` is the hit's *title* in the
        // directory, and the sheet it was on is gone, so a grid card drawn
        // from the needle or from the hit's title rather than from its feed
        // would leave that word on screen.
        XCTAssertFalse(
            app.staticTexts[StubPodcasts.directoryHit].exists,
            "the added show is drawn by the directory's title, which the "
                + "house did not send back"
        )
        evidence(app, "podcasts-52-added-from-directory")
    }

    /// A refusal from the directory is said **inside the sheet**, and the
    /// sheet stays up.
    ///
    /// With the house's internet off, `GET /discover` answers a 409 whose
    /// detail says so (`podcasts.py:397`). Android toasts that and leaves the
    /// dialog up; a toast on iOS is the shell's and is drawn *behind* a
    /// sheet, where nobody can read it — which is the same failure class as a
    /// control that cannot act. So the sentence is drawn in the sheet
    /// (``PodcastsModel/discoveryError``), and this case is what proves it is
    /// somewhere a person can see.
    @MainActor
    func testPodcasts_ADirectoryRefusalIsSaidInTheSheet() throws {
        resetPodcastHouse()
        setPodcastDiscovery("internet-off")
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")

        app.buttons[StubPodcasts.subscribe].firstMatch.tap()
        let field = app.textFields[StubPodcasts.namePlaceholder].firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 20), "the subscribe sheet never opened")
        field.tap()
        app.typeText(StubPodcasts.directoryNeedle + "\n")

        let refusal = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label CONTAINS %@", StubPodcasts.internetOff))
            .firstMatch
        let shown = refusal.waitForExistence(timeout: 25)
        evidence(app, "podcasts-53-directory-refused")
        XCTAssertTrue(shown, "the refusal was not said where the person is looking")
        // The sheet is still up, which is the other half: a failed search
        // must leave the field to correct.
        XCTAssertTrue(field.exists, "the sheet closed over a failure")
        // And the sentence is the server's own, not something this app made
        // up about being offline. The wording asserted absent is
        // ``failureText``'s own fallback for `.unreachable`/`.noServer` —
        // what the sheet *would* say if the 409's detail had been dropped on
        // the floor. (It was `request failed` here until review: that string
        // is `Pager.message(of:)`'s fallback for an empty error description
        // and the sheet cannot produce it, so the assertion could not fail.)
        XCTAssertFalse(
            app.descendants(matching: .staticText)
                .matching(NSPredicate(format: "label CONTAINS %@", StubPodcasts.genericDiscoveryFailure))
                .firstMatch
                .exists,
            "the server's reason was replaced with a generic offline failure"
        )
        resetPodcastHouse()
    }

    /// Unsubscribing asks first, and then takes the show and its count with
    /// it.
    ///
    /// The confirm names the show **and** says the downloads go, which is the
    /// whole reason it is a confirm. Cancelling leaves everything alone —
    /// including the sheet, because the show is still open. Confirming
    /// removes the card through ``Pager/remove(_:)`` rather than by a reload,
    /// which also brings the total down: `showing 50 of 61 shows`, not a
    /// count still claiming a show that is gone.
    @MainActor
    func testPodcasts_UnsubscribingAsksFirstAndThenRemovesTheShow() throws {
        resetPodcastHouse()
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")
        XCTAssertTrue(openTheKitchenTable(app), "the show's episodes never drew")

        app.buttons[StubPodcasts.unsubscribe].firstMatch.tap()
        // **Scoped to the alert**, not `firstMatch` over the app: the sheet's
        // own `unsubscribe` toolbar button is still in the tree behind the
        // confirm, and this screen's whole point is that the confirm is what
        // acts.
        let alert = app.alerts.firstMatch
        let body = alert.staticTexts[StubPodcasts.unsubscribeBody]
        XCTAssertTrue(body.waitForExistence(timeout: 20), "the destructive write asked nothing")
        evidence(app, "podcasts-60-unsubscribe-asked")

        alert.buttons[StubPodcasts.cancel].tap()
        XCTAssertTrue(
            podcastRow(app, StubPodcasts.newestEpisode, StubPodcasts.newestMeta)
                .waitForExistence(timeout: 20),
            "cancelling the confirm closed the show as well"
        )

        app.buttons[StubPodcasts.unsubscribe].firstMatch.tap()
        XCTAssertTrue(body.waitForExistence(timeout: 20), "the confirm did not come back")
        let confirm = alert.buttons[StubPodcasts.unsubscribe]
        guard confirm.waitForExistence(timeout: 10) else {
            evidence(app, "podcasts-61-no-confirm-button")
            return XCTFail("the confirm has no unsubscribe button")
        }
        confirm.tap()

        let toast = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label CONTAINS %@", StubPodcasts.unsubscribedToast))
            .firstMatch
        XCTAssertTrue(toast.waitForExistence(timeout: 25), "nothing was said about the unsubscribe")
        XCTAssertTrue(
            app.staticTexts[StubPodcasts.countLineAfterUnsubscribe].waitForExistence(timeout: 25),
            "the count line still claims the show that was removed"
        )
        XCTAssertFalse(
            app.buttons[StubPodcasts.kitchenTable].exists,
            "the card for the unsubscribed show is still on the grid"
        )
        evidence(app, "podcasts-62-unsubscribed")
        resetPodcastHouse()
    }

    /// A failed unsubscribe is said **in the sheet**, and the card stays.
    ///
    /// The sheet does not close over a failure, and the shell draws a toast
    /// *behind* a presented sheet (`AppShellView.swift:31-46`) — so this
    /// write's failure went somewhere nobody could read until review, which
    /// is the same failure class as a control that cannot act. The 404 is
    /// what a stale card gets (`podcasts.py:265-269`).
    ///
    /// The assertion that discriminates is the pair: the server's own
    /// sentence visible **while** the episodes list is still on screen. A
    /// toast would satisfy neither — it is drawn under the sheet.
    @MainActor
    func testPodcasts_AFailedUnsubscribeIsSaidInTheSheet() throws {
        resetPodcastHouse()
        setPodcastUnsubscribe("gone")
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")
        XCTAssertTrue(openTheKitchenTable(app), "the show's episodes never drew")

        app.buttons[StubPodcasts.unsubscribe].firstMatch.tap()
        let alert = app.alerts.firstMatch
        XCTAssertTrue(
            alert.staticTexts[StubPodcasts.unsubscribeBody].waitForExistence(timeout: 20),
            "the destructive write asked nothing"
        )
        alert.buttons[StubPodcasts.unsubscribe].tap()

        let said = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label BEGINSWITH %@", StubPodcasts.unsubscribeFailedPrefix))
            .firstMatch
        let shown = said.waitForExistence(timeout: 25)
        evidence(app, "podcasts-63-unsubscribe-failed")
        XCTAssertTrue(shown, "the failure was not said where the person is looking")
        // In the sheet, not behind it: the episodes list is still drawn.
        XCTAssertTrue(
            podcastRow(app, StubPodcasts.newestEpisode, StubPodcasts.newestMeta).exists,
            "the sheet closed over a failed write, so the sentence is not in it"
        )
        // And the write that did not happen took nothing off the grid.
        app.buttons[StubPodcasts.closeSheet].firstMatch.tap()
        XCTAssertTrue(
            app.staticTexts[StubPodcasts.countLine].waitForExistence(timeout: 25),
            "a write that failed changed the count line"
        )
        XCTAssertTrue(
            app.buttons[StubPodcasts.kitchenTable].firstMatch.exists,
            "a write that failed took the card off the grid"
        )
        resetPodcastHouse()
    }

    // MARK: - type size and the audit

    /// The card's text really grows with the type size.
    ///
    /// **Measured at both sizes rather than inferred from a headline.** Twice
    /// on this track a named Dynamic Type finding turned out to be a false
    /// positive and was only settled by comparing the element's own height at
    /// `large` and at AX5 ([[audit-reports-one-issue]]), so this case does
    /// the comparison itself. It is the one assertion that would catch the
    /// two things the audit demonstrably does not: a `minimumScaleFactor`
    /// somebody adds to make a card fit, and the fixed `frame(width:)` on the
    /// cover square that ``PodcastArtView`` takes from a `@ScaledMetric`
    /// instead.
    ///
    /// The card is also where the row/column swap happens
    /// (``PodcastShowCard/layout``), so at an accessibility size the growth
    /// is large rather than marginal — a 1.5x floor is well under what a
    /// working card does and well over what a clipped one could.
    @MainActor
    func testPodcasts_TheGridTextGrowsAtTheLargestTypeSize() throws {
        resetPodcastHouse()
        let ordinary = openPodcasts()
        XCTAssertTrue(ordinary.landed, "the shows list never answered at the ordinary size")
        let small = ordinary.app.buttons[StubPodcasts.kitchenTable].firstMatch
        XCTAssertTrue(small.waitForExistence(timeout: 25), "no card to measure")
        let smallHeight = small.frame.height
        // **The leaves the audit names**, not only the card around them.
        // Train S004 reported "User will not be able to change the font size
        // of this element" against a card's title, its author line and its
        // mono counts — named, which this file's rule calls a defect. The
        // card growing does not answer that: a card can grow while one line
        // inside it is pinned. So each flagged leaf is measured by its own
        // label, and the audit case below classifies that headline as false
        // only because these three numbers come back bigger.
        let smallLeaves = [
            StubPodcasts.kitchenTable,
            StubPodcasts.kitchenAuthor,
            StubPodcasts.kitchenDownloaded,
        ].map { label -> (String, CGFloat) in
            (label, ordinary.app.staticTexts[label].firstMatch.frame.height)
        }
        evidence(ordinary.app, "podcasts-70-card-ordinary")
        ordinary.app.terminate()

        let big = openPodcasts("UICTContentSizeCategoryAccessibilityXXXL")
        XCTAssertTrue(big.landed, "the shows list never answered at AX5")
        let large = big.app.buttons[StubPodcasts.kitchenTable].firstMatch
        // At AX5 the header alone takes much of the window, so the second
        // card may be unbuilt rather than merely off screen.
        if !large.exists { _ = podcastsScrollUntilBuilt(big.app, large, flicks: 12) }
        XCTAssertTrue(large.exists, "the card was never built at AX5")
        let largeHeight = large.frame.height
        evidence(big.app, "podcasts-71-card-ax5")

        print("PODCASTS CARD ordinary=\(small.frame.size) ax5=\(large.frame.size)")
        XCTAssertGreaterThan(
            largeHeight, smallHeight * 1.5,
            "the card grew from \(smallHeight) to \(largeHeight) — text that "
                + "does not grow is either scaled down or clipped"
        )
        // The three leaves, each against its own ordinary height. A leaf that
        // is unbuilt at AX5 measures zero and fails here rather than being
        // skipped, which is the failure mode a `guard` would have hidden.
        for (label, was) in smallLeaves {
            let now = big.app.staticTexts[label].firstMatch.frame.height
            print("PODCASTS LEAF \(label) ordinary=\(was) ax5=\(now)")
            XCTAssertGreaterThan(
                now, was * 1.5,
                "the leaf \(label) went from \(was) to \(now) — the audit's "
                    + "Dynamic Type finding against it is not a false positive"
            )
        }
    }

    /// Apple's audit over the grid.
    ///
    /// Classified by **signature** with ``floorAndRest(_:)`` and never by a
    /// pinned total: the floor is the anonymous Dynamic Type finding every
    /// pushed screen in this app reports (`element: nil`), and what is left
    /// must be empty - a finding Apple can attribute to an element is a
    /// defect to fix. That is how the modal bug on Settings was found, by two
    /// `Contrast failed` naming the views behind a dialog.
    ///
    /// What it is pointed at here: the mono `9/57 downloaded` line and the
    /// author line are both ``Color/fgMuted``, which was measured against the
    /// **white canvas** and fails on `secondarySystemBackground` - which is
    /// why ``PodcastShowCard`` is a stroked rectangle over the page rather
    /// than Android's filled card.
    ///
    /// ### What train S004 found, and which of it was real
    ///
    /// Eleven attributed findings. Four were defects and are fixed:
    ///
    ///  - `Contrast nearly passed` on `poll now` - a `.bordered` button draws
    ///    its label in the tint, the app's accent is ``Color/brand`` at
    ///    2.05:1, and the button named no tint of its own. Stations collected
    ///    six of the same finding on six chips in the same run. Now
    ///    ``Color/brandInk``, as every other bordered button in this app.
    ///  - `Text clipped` on a card's title - `.lineLimit(2)` over a
    ///    43-character feed URL in 186 points. The limit is gone.
    ///  - `Text clipped` on the shows field - `search shows...` in a control
    ///    that is one fixed row (``PodcastStrings/searchShowsPrompt`` carries
    ///    the measurement, and why the control cannot grow instead).
    ///  - `Label not human-readable` on the untitled show's card, whose
    ///    spoken name was a whole URL (``PodcastShow/spokenTitle``).
    ///
    /// ### What train S007 found, and why this case now shortens the grid
    ///
    /// Three more contrast findings, and they were two different things.
    ///
    ///  - `Contrast failed` on `subscribe` was **real**: the button took
    ///    `.tint(Color.brand)` and no `foregroundStyle`, so SwiftUI drew its
    ///    label in white on amber at 2.05:1. Every other prominent button in
    ///    this app pairs the two (`HomeAnnounceView.swift:158-163`); this one
    ///    said in a comment that it did, and did not. Fixed in the view.
    ///  - `Contrast nearly passed` on `poll now` is the same omission one
    ///    style along: a `.bordered` button draws a wash of its own tint
    ///    behind the label, and that wash lifts the background enough to drop
    ///    ``Color/brandInk`` from 5.4:1 on white to about 4.4:1 on it - which
    ///    is exactly the band Apple reports as "unless font size is larger".
    ///    S007 first answered it with a ``Color/brandFg`` label on that pill,
    ///    which is near-invisible in dark mode (about 1.2:1 on black). Since
    ///    unit mx-ink (2026-11-05) `poll now` is ``InkOutlineButtonStyle``,
    ///    Android's OutlinedButton (`PodcastsScreen.kt:133`): ``Color/brandInk``
    ///    on the page itself, a half-strength ink stroke and no fill, 5.66:1 on
    ///    white and 11.03:1 on black by the tokens.
    ///  - `Contrast failed` on one `0/0 downloaded` was **not** real, and the
    ///    geometry says why: `performAccessibilityAudit` scrolls the page it
    ///    audits. The content offset went 137.3 → -326.3 during that one run
    ///    and the pager loaded two more pages under it (5711 → 7520 points),
    ///    which is how one of eight identical lines failed while the other
    ///    seven passed.
    ///
    /// So the audit is pointed at a grid of three shows that fits on the
    /// phone (``setPodcastShows(_:)``). It still holds every distinct leaf -
    /// a title, an author, a counts line, a card with no title at all - and
    /// it holds still, which is the difference between reading this app's
    /// colours and reading a list mid-flight.
    ///
    /// The one remaining signature is classified rather than fixed, against a
    /// measurement this file makes itself. It is not excused by count, so a
    /// second signature - or this one moving to a leaf nobody measured -
    /// still fails this case.
    @MainActor
    func testPodcasts_TheScreenPassesApplesAccessibilityAudit() throws {
        // Three shows, so the whole screen fits and nothing scrolls
        // (``setPodcastShows(_:)`` carries the measurement from train S007).
        // The reset is what hands the next case back its sixty-two.
        resetPodcastHouse()
        setPodcastShows("short")
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")
        XCTAssertTrue(
            app.staticTexts[StubPodcasts.shortCountLine].waitForExistence(timeout: 20),
            "the house did not answer the short grid, so this audit would "
                + "run over a list that scrolls under it"
        )
        // Where the foot of the grid sits before the audit runs.
        // `Tuesday Night Shift 01` sorts last of the three, so its card is
        // the foot; a foot that is on screen and stays exactly where it was
        // is the whole claim that nothing scrolled under the audit.
        let foot = app.buttons[StubPodcasts.fillerShow].firstMatch
        XCTAssertTrue(foot.waitForExistence(timeout: 20), "the short grid's last card is not drawn")
        let page = app.scrollViews.allElementsBoundByIndex
            .max(by: { $0.frame.height < $1.frame.height })
        let before = foot.frame
        XCTAssertLessThan(
            before.maxY, page?.frame.maxY ?? 0,
            "the short grid runs past the bottom of the page, so the audit "
                + "can scroll it and its contrast readings are of whatever "
                + "was mid-flight"
        )

        // The shell's navigation bar, before. It used to fold its large
        // title away on a scroll, and the audit scrolls — see the assertion
        // below, where I004's numbers say it no longer does. Read as the
        // **frame** and not the height: the gap between its bottom edge and
        // the foot card is what the failure message subtracts, and two
        // heights read at two moments cannot be subtracted from one another.
        let barBefore = app.navigationBars.firstMatch.frame

        let found = try auditIssues(app, "podcasts-grid")
        evidence(app, "podcasts-80-grid-audited")
        let (floor, rest) = Self.floorAndRest(found)
        print("PODCASTS AUDIT floor=\(floor) rest=\(rest.count)")
        for line in rest { print("PODCASTS AUDIT REST :: \(line)") }

        // And the same card, after. The findings below are only worth
        // classifying if they were taken off a layout that held still, so
        // this is asserted rather than assumed - and it is what makes the
        // `Contrast failed`-at-the-viewport-edge exemption this case used to
        // carry unnecessary, which is why that exemption is gone rather than
        // kept as a safety net. An exemption with no case under it is a hole
        // for the next real finding to fall through.
        //
        // ### What moves it that is not a scroll (train I003)
        //
        // The foot came back 52 points higher and the grid had not moved a
        // row. The shell grew a large navigation title in the 2026-11-03
        // batch, Apple's audit scrolls, and the title folds away when
        // anything does — 96 points of bar to 44, with everything under it
        // rising by exactly the difference. That is the shell's own
        // behaviour and its own case
        // (`testShell_TheHeaderScrollsAwayOnAPushedScreen` asserts it), not
        // a list mid-flight, and a case that cannot tell the two apart is
        // one that fails for somebody else's feature.
        //
        // **The fold lands late, which is why this waits for it.** The I003
        // tree captured 30 ms before the foot was read still had the bar at
        // 96 and the card at 596; the read that failed the case got 544. Two
        // heights taken at two moments therefore cannot be subtracted from
        // one another — half the time one is from before the animation and
        // the other from after, and the case would be red under a message
        // that misdescribes it. What holds still through the fold is the
        // **gap between the bar's bottom edge and the foot**: the fold moves
        // both by the same 52 and the gap does not change, while a scroll
        // moves the card and not the bar. Both are read after the bar has
        // stopped moving (``podcastsSettledNavigationBar(_:)``).
        //
        // ### And train I004 measured the fold away again (2026-11-04)
        //
        // The gap was a prediction standing in for a measurement, and it let
        // the foot card move 52 points during the very audit that graded its
        // colours. I004 ran this case with the shell's stale-bar fix in
        // (`ShellChromeViews.swift:546-558`, `RESIDUAL-AUDIT.md` §1) and
        // printed:
        //
        //     PODCASTS AUDIT bar 43.999999999999986->43.999999999999986
        //       foot 544.0->544.0 gap 406.6666666666667->406.6666666666667
        //
        // The bar does not move on this screen any more and neither does the
        // card. So the claim goes back to the strict one it was before: the
        // foot card is in the **same place**, not merely the same distance
        // under a bar that may itself have moved. `barBefore`/`barAfter` are
        // still read and still printed, because they are what the failure
        // message needs to say *why* a future move happened — a fold moves
        // both, a scroll moves only the card. S007's run moved the content
        // offset 463 points under the audit, which fails this.
        let barAfter = Self.podcastsSettledNavigationBar(app)
        let after = foot.frame
        let gapBefore = before.minY - barBefore.maxY
        let gapAfter = after.minY - barAfter.maxY
        print(
            "PODCASTS AUDIT bar \(barBefore.height)->\(barAfter.height)"
                + " foot \(before.minY)->\(after.minY) gap \(gapBefore)->\(gapAfter)"
        )
        XCTAssertEqual(
            before, after,
            "the foot card was at \(before) before Apple's audit and \(after) "
                + "after it, so the page moved while the audit read its colours "
                + "and they are a list mid-flight (train S007). The bar went "
                + "\(barBefore.height) to \(barAfter.height) and the gap under it "
                + "\(gapBefore) to \(gapAfter): an equal gap with an unequal frame "
                + "is the large title folding away and taking the page with it "
                + "(train I003), which the shell's own case asserts; an unequal "
                + "gap is a scroll."
        )

        let unexplained = rest.filter { line in
            // **A Dynamic Type headline with an element attached.** This file
            // called that the signature of a real defect, and
            // ``testTheVideosGridAudits`` settled that it is not: on this
            // screen the three named leaves are the card's title, its author
            // and its counts, and the growth case above now measures all
            // three by label at `large` and at AX5. That measurement is the
            // test; this is only the classification that makes room for it,
            // and it collapses the moment those numbers stop growing.
            if Self.knownAnonymousHeadlines.contains(where: { line.hasPrefix($0) }) {
                return false
            }
            return true
        }
        XCTAssertEqual(
            [],
            unexplained,
            "the audit found something the Dynamic Type measurement above "
                + "does not explain:\n" + unexplained.joined(separator: "\n")
        )
    }

    /// The navigation bar's frame once it has stopped changing.
    ///
    /// The shell's large title folds away under a scroll, and the fold is
    /// **animated**: train I003's audit returned at t=22.08s, the tree taken
    /// at t=22.63s still had the bar at 96 points and the grid's foot at 596,
    /// and the next read — 30 ms later — had the foot at 544. Anything that
    /// compares a before and an after across that window is reading one side
    /// of an animation, which is how a case about scrolling came to be red
    /// about a fold.
    ///
    /// So: poll until two reads agree, up to three seconds, and return the
    /// last. Named for this unit rather than added to the shared file, which
    /// this unit does not own.
    @MainActor
    static func podcastsSettledNavigationBar(_ app: XCUIApplication) -> CGRect {
        var last = app.navigationBars.firstMatch.frame
        let settleBy = Date().addingTimeInterval(3)
        while Date() < settleBy {
            usleep(250_000)
            let now = app.navigationBars.firstMatch.frame
            if now == last { return now }
            last = now
        }
        print("PODCASTS AUDIT the navigation bar never settled; last read \(last)")
        return last
    }
}

/// What the stub house's podcasts half holds, by the words that appear on
/// screen.
///
/// Spelt out here rather than imported, like every other string these cases
/// match on: a query that silently followed a renamed constant would stop
/// testing what it claims to. These are the fixture's own
/// (`design-notes/ios/stubhouse/fixtures/podcasts.py`) and the app's words as
/// a person reads them, not references to `PodcastStrings`.
enum StubPodcasts {

    // the page
    /// Train S004 shipped `subscriptions + episodes, played on this phone`,
    /// which claimed the one thing the screen has no control for. Corrected
    /// in the app, not here: ``PodcastStrings/subtitle``.
    static let subtitle = "subscriptions + episodes, played on this phone"
    static let pollNow = "poll now"
    static let subscribe = "subscribe"
    static let searchShows = "search shows…"
    /// What the sheet says when a discovery failure has **no** server reason
    /// to show — ``failureText``'s `.unreachable`/`.noServer` wording. Seeded
    /// here only to be asserted absent when the server *did* give a reason.
    static let genericDiscoveryFailure = "Discovery failed (offline?)"

    /// Sixty-two subscriptions, fifty to a page.
    static let countLine = "showing 50 of 62 shows"
    /// After one subscribe.
    static let countLineAfterSubscribe = "showing 50 of 63 shows"
    /// After one unsubscribe. **49 of 61**, not 50 of 61: `Pager.remove`
    /// filters the row out of the loaded page *and* brings the total down,
    /// and nothing on this path re-reads page one (the stub house cannot
    /// push `podcasts.changed`, and `unsubscribe` deliberately does not
    /// reload). Both numbers falling by one is the discriminating fact — an
    /// implementation that reloaded instead of removing would read
    /// `showing 50 of 61 shows`, and one that forgot the total would read
    /// `showing 49 of 62 shows`.
    static let countLineAfterUnsubscribe = "showing 49 of 61 shows"
    static let oneShowCountLine = "showing 1 of 1 shows"

    /// The whole grid under `shows_mode: "short"` — three shows, no second
    /// page, and nothing below the fold for Apple's audit to scroll to.
    static let shortCountLine = "showing 3 of 3 shows"

    // the shows
    static let kitchenTable = "The Kitchen Table"
    static let kitchenAuthor = "Marisa Whitlock"
    /// The card's `accessibilityValue`: the author, then the two counts in
    /// the server's order. A swap reads `57/9`.
    static let kitchenDetail = "Marisa Whitlock · 9/57 downloaded"
    /// The same after a poll gives the pending episode its audio.
    static let kitchenDetailAfterPoll = "Marisa Whitlock · 10/57 downloaded"
    /// The Kitchen Table card's mono counts line — the third of the three
    /// leaves the S004 audit named for Dynamic Type, measured at both sizes
    /// by ``testPodcasts_TheGridTextGrowsAtTheLargestTypeSize``. Nine of
    /// fifty-seven, which is the seed before any poll.
    static let kitchenDownloaded = "9/57 downloaded"
    /// A filler show, which the author needle must not match.
    static let fillerShow = "Tuesday Night Shift 01"
    /// The show the feed never titled, drawn by its feed URL.
    static let untitledFeed = "https://feeds.example.com/untitled-feed.xml"
    /// The last fallback of `title ?? feed_url ?? "podcast"`. No seeded row
    /// should **draw** it — and the untitled card should be the only thing
    /// that **speaks** it (``PodcastShow/spokenTitle``).
    static let untitledWord = "podcast"
    /// An author's name that appears in no title anywhere in the seed.
    static let authorNeedle = "Whitlock"
    static let noShowsMatch = "no shows match"

    // the episodes
    static let episodeCountLine = "showing 50 of 57 episodes"
    static let newestEpisode = "Brining a bird"
    /// The meta line without its date (`yesterday` for this row): the date
    /// is the Simulator's calendar day against a timestamp taken 24 hours
    /// before the read, and the line's own case is ``StubPodcastDetails``'.
    /// Since Android `bd84d970` every row says its download state, so a
    /// downloaded row ends `downloaded` (`PodFormat.download`).
    static let newestMeta = "53 min · 2 chapters · downloaded"
    static let pendingEpisode = "Stock from a carcass"
    static let pendingMeta = "41 min · not downloaded"
    /// The word a downloaded row now **must** carry.
    static let downloadedWord = "downloaded"
    static let noDurationEpisode = "A quiet kitchen"
    /// What the no-duration row says instead of a length: its place, which
    /// the stub seeds (`stubhouse.patch`, 1440 s into an episode of no
    /// known length).
    static let noDurationPlace = "at 24 min"
    static let emDash = "—"
    static let zeroDuration = "0:00"
    /// Episode fifty-seven: page two, and only page two.
    static let oldestEpisode = "Sourdough in a cold house"
    static let unsubscribe = "unsubscribe"
    static let cancel = "cancel"
    /// `failed: ` — Android's own wording for this one write
    /// (`PodcastsEpisodes.kt:209`). A prefix, because the rest is the
    /// server's sentence and the whole thing is over 128 characters' worth of
    /// query risk (NIGHT-RULES §5).
    static let unsubscribeFailedPrefix = "failed: 404"
    /// The episodes sheet toolbar close (PodcastStrings.close).
    static let closeSheet = "close"
    static let unsubscribeBody =
        "Unsubscribe from \"The Kitchen Table\"? Downloaded episodes are removed."
    static let unsubscribedToast = "Unsubscribed"
    /// The VoiceOver label a play control carries: the **name**, not the bare
    /// word (``PlayerStrings/playNamed(_:)``).
    static func playNamed(_ episode: String) -> String { "play \(episode)" }

    /// `TransferStrings.saveToDevice`, spelt out like every other string this
    /// file matches on.
    static let saveControl = "save to device"

    /// ``PlayerStrings/listeningAsChip(_:)``'s opening. The stub house has no
    /// listener picked, so this must be absent everywhere these cases look.
    static let listenerChip = "listening as"

    /// `PodcastStrings.inTheDashboard`, which is **deleted** from the app
    /// (D46 put the play and the save on the row). Kept here because that is
    /// what a case now asserts: a merge that brought the sentence back beside
    /// a play button would be a screen contradicting itself.
    static let inTheDashboard = "playing or saving an episode is done in the dashboard"

    // polling
    /// The counts, then the one show whose feed the stub's poll could not
    /// read (`PollResult.failures`, `stubhouse.patch`), by name and in the
    /// server's own words (`PodFormat.pollText`).
    static let polledToast = "Polled — 3 downloaded, 2 new. "
        + "1 show couldn't be read: Tuesday Night Shift 05 — feed not found"

    // subscribing
    static let byFeedURL = "by rss url"
    static let namePlaceholder = "show name…"
    static let feedPlaceholder = "https://…/feed.xml"
    static let add = "add"
    static let subscribedToast = "Subscribed"
    /// A feed no seeded show has, which sorts onto page one.
    static let newFeed = "https://feeds.example.com/new-one.xml"
    /// The one directory hit `hearth` matches, its author, and its feed.
    static let directoryNeedle = "hearth"
    static let directoryHit = "Hearth & Hob"
    static let directoryHitAuthor = "Delia Marchetti"
    static let directoryHitFeed = "https://feeds.example.com/hearth-and-hob.xml"
    /// A hit the needle does not match.
    static let otherDirectoryHit = "Dough Rising"
    /// Words from the fixture's 409, which is the server's own refusal.
    static let internetOff = "turned off for this house"
}
