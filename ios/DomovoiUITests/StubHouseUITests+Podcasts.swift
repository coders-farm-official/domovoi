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
/// **Playing an episode.** There is no play control on this screen: the audio
/// engine merged from `wf/ios-n-player` and nothing in the app owns one, so
/// the control, the resume dialog and the position read/write are left out
/// entirely (``PodcastsView``'s doc comment, and
/// `overnight/podcasts/DECISIONS.md`). What *is* tested is that the control is
/// absent — `testPodcasts_AnEpisodeWithNoFileSaysWhatTheDownloadIsDoing` —
/// because a branch that later draws one before there is an owner should fail
/// here rather than on Kamron's phone.
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

    /// A show the feed never titled is drawn by its feed URL.
    ///
    /// `title ?? feed_url ?? "podcast"` (``PodcastShow/displayTitle``). The
    /// seeded row has `title: null` and a feed URL that sorts onto page one,
    /// so the fallback is reachable without scrolling — and the word
    /// `podcast` must **not** be what the card says, which is what a port
    /// that dropped the middle link of the chain would draw.
    @MainActor
    func testPodcasts_AShowWithNoTitleFallsBackToItsFeed() throws {
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")
        evidence(app, "podcasts-12-untitled-show")

        XCTAssertTrue(
            app.buttons[StubPodcasts.untitledFeed].exists,
            "the show with no title is not drawn by its feed URL"
        )
        XCTAssertFalse(
            app.buttons[StubPodcasts.untitledWord].exists,
            "a show with a feed URL fell all the way to the word \"podcast\""
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
    /// doing, and offers no play control — because **nothing** on this screen
    /// does.
    ///
    /// Two halves, and the second is the one that matters tonight. Android
    /// draws a filled play button on every row and disables it unless
    /// `has_file` (`PodcastsEpisodes.kt:223`); the audio route answers **409
    /// "episode not downloaded yet"** for such a row (`podcasts.py:514`), so
    /// that button cannot do what it says. This port draws no play control at
    /// all — not for this row and not for the downloaded one either, since
    /// nothing in the app owns an audio engine — and says at the foot of the
    /// list where listening is done instead.
    ///
    /// So this case fails if a play button appears for an undownloaded
    /// episode (the no-misleading-controls failure) **and** if one appears at
    /// all before something owns a ``PlayerModel``.
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
        // The downloaded one must NOT carry a status word: the meta line
        // shows `download_status` only when there is no file
        // (``PodcastEpisode/metaLine``), and a row that showed `downloaded`
        // beside a file it already has would be noise on every row.
        XCTAssertFalse(
            podcastRow(app, StubPodcasts.newestEpisode, StubPodcasts.downloadedWord).exists,
            "an episode with its file still shows a download status"
        )
        XCTAssertFalse(
            app.buttons[StubPodcasts.play].exists,
            "something offers to play an episode and nothing in this app owns "
                + "a player — a control that cannot do what it says"
        )
        XCTAssertTrue(
            app.staticTexts[StubPodcasts.inTheDashboard].exists,
            "the list does not say where listening and saving are done"
        )
    }

    /// An episode whose feed did not say how long it is shows an em dash.
    ///
    /// `fmtDur(nil)` is `—`, and the reason it is not `0:00` is that a
    /// zero-length episode and an unknown length read the same on a row and
    /// mean opposite things. The seeded row is the only one with a null
    /// duration, and it **has** its file, so the dash cannot be confused with
    /// a pending row's.
    @MainActor
    func testPodcasts_AnEpisodeWithNoDurationShowsADash() throws {
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")
        XCTAssertTrue(openTheKitchenTable(app), "the show's episodes never drew")
        evidence(app, "podcasts-22-no-duration")

        XCTAssertTrue(
            podcastRow(app, StubPodcasts.noDurationEpisode, StubPodcasts.emDash).exists,
            "an episode with no duration did not draw the em dash"
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
        let added = app.buttons[StubPodcasts.newFeed].firstMatch
        XCTAssertTrue(added.waitForExistence(timeout: 25), "the new show is not on the grid")
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
        XCTAssertTrue(
            app.buttons[StubPodcasts.directoryHitFeed].waitForExistence(timeout: 25),
            "the hit's own feed is not the show that was added"
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
    }

    /// Apple's audit over the grid.
    ///
    /// Classified by **signature** with ``floorAndRest(_:)`` and never by a
    /// pinned total: the floor is the anonymous Dynamic Type finding every
    /// pushed screen in this app reports (`element: nil`, measured false by
    /// the case above), and `rest` must be empty — a finding Apple can
    /// attribute to an element is a defect to fix. That is how the modal bug
    /// on Settings was found, by two `Contrast failed` naming the views
    /// behind a dialog.
    ///
    /// What it is pointed at here: the mono `9/57 downloaded` line and the
    /// author line are both ``Color/fgMuted``, which was measured against the
    /// **white canvas** and fails on `secondarySystemBackground` — which is
    /// why ``PodcastShowCard`` is a stroked rectangle over the page rather
    /// than Android's filled card.
    @MainActor
    func testPodcasts_TheScreenPassesApplesAccessibilityAudit() throws {
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")
        let found = try auditIssues(app, "podcasts-grid")
        evidence(app, "podcasts-80-grid-audited")
        let (floor, rest) = Self.floorAndRest(found)
        print("PODCASTS AUDIT floor=\(floor) rest=\(rest.count)")
        XCTAssertTrue(
            rest.isEmpty,
            "the audit found something it can attribute to a view:\n"
                + rest.joined(separator: "\n")
        )
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

    // the shows
    static let kitchenTable = "The Kitchen Table"
    static let kitchenAuthor = "Marisa Whitlock"
    /// The card's `accessibilityValue`: the author, then the two counts in
    /// the server's order. A swap reads `57/9`.
    static let kitchenDetail = "Marisa Whitlock · 9/57 downloaded"
    /// The same after a poll gives the pending episode its audio.
    static let kitchenDetailAfterPoll = "Marisa Whitlock · 10/57 downloaded"
    /// A filler show, which the author needle must not match.
    static let fillerShow = "Tuesday Night Shift 01"
    /// The show the feed never titled, drawn by its feed URL.
    static let untitledFeed = "https://feeds.example.com/untitled-feed.xml"
    /// The last fallback of `title ?? feed_url ?? "podcast"`, which no seeded
    /// row should reach.
    static let untitledWord = "podcast"
    /// An author's name that appears in no title anywhere in the seed.
    static let authorNeedle = "Whitlock"
    static let noShowsMatch = "no shows match"

    // the episodes
    static let episodeCountLine = "showing 50 of 57 episodes"
    static let newestEpisode = "Brining a bird"
    static let newestMeta = "53:00 · 2 chapters"
    static let pendingEpisode = "Stock from a carcass"
    static let pendingMeta = "41:00 · pending"
    /// The word a downloaded row must **not** carry.
    static let downloadedWord = "downloaded"
    static let noDurationEpisode = "A quiet kitchen"
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
    /// The VoiceOver label a play control would carry, which nothing draws.
    static let play = "play"
    static let inTheDashboard = "playing or saving an episode is done in the dashboard"

    // polling
    static let polledToast = "Polled — 3 downloaded, 2 new"

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
