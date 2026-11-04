import XCTest

/// Audiobooks, the server half, against the stub house.
///
/// Every case is named `testAudiobooks_…` so a train can hand its result back
/// to this item, and every piece of evidence is named `audiobooks-…`.
///
/// ### What is here and what is a package test
///
/// The pure rules — `BookRules.parts`, `audioPath`'s encoding, `chapters`'
/// folder fallback, `sizeBytes`' refusal to guess, `BookPositionRules.resolve`'s
/// six rows, `BookContinueRules.pick` and `hm`, the store's cap, the push's
/// in-flight guard, the decoder against both answer shapes — are all in
/// `AudiobookModelsTests` and `AudiobooksModelTests`, which run with no
/// Simulator. What is here is what only a real app against a real house can
/// say: that the shapes decode, that each control does what its word says,
/// that the two empty states are not swapped, that the text grows, and that
/// nothing on the screen promises something this app has not got.
///
/// **One case `MAP.md` §11 planned is deliberately not here**: switching
/// houses inside a list read. The only way to change servers from the app is
/// the connection sheet, and NIGHT-RULES §1 forbids opening it on a
/// Simulator — it starts a LAN discovery sweep across Kamron's real home
/// network. That property is covered where it can be: the package tests
/// `testAStartInFlightWhenTheHouseChangesNeitherAsksNorPlays`,
/// `testAChapterTapInFlightWhenTheHouseChangesPlaysNothing` and
/// `testACoverInFlightWhenTheHouseChangesLandsNowhere` hold a request open,
/// switch the key **inside** the round trip and then let it go, which is the
/// shape [[read-after-await-wrong-key]] asks for. DECISIONS §17.
///
/// This comment used to cite `testABooksPlaceIsNeverReadFromAnotherHouse`,
/// which switches nothing mid-flight; the three above were written in the
/// fixer pass of 2026-11-02 together with the guards they were missing.
extension StubHouseUITests {

    // MARK: - getting there

    /// The library, pushed, with the seed put back first.
    ///
    /// **The reset is not optional.** Cases share one house for a whole run,
    /// in alphabetical order, and three of the cases below change the seed:
    /// the reindex adds a book, `audiobooks-grow` adds sixty, and a position
    /// save moves a place. Without this the count line a later case asserts
    /// would be whatever an earlier one left behind.
    ///
    /// **What it waits for.** `showing ` — ``Paging/countText(shown:total:noun:)``
    /// is the only thing in the app that writes that word and
    /// ``AudiobooksModel/countText`` is nil until the list has answered, so it
    /// cannot exist before a completed read.
    ///
    /// **`freshPlaces` resets the half the house does not own.** The phone's
    /// places are a file in its own Application Support
    /// (``BookPositionFile``) and `reset-fixtures` puts the *house* back, not
    /// the phone — so a place written by one run is still there for the next
    /// one, and reinstalling the app over in a Simulator keeps it.
    /// ``AudiobooksView/keptPlaces()`` has what that cost on train S010.
    @MainActor
    func openAudiobooks(
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        reset: Bool = true,
        grow: Int? = nil,
        freshPlaces: Bool = false
    ) -> (app: XCUIApplication, landed: Bool) {
        if reset {
            control("reset-fixtures", "{}", port: 6601, what: "the audiobook seed")
        }
        if let grow {
            control("audiobooks-grow", "{\"books\":\(grow)}", port: 6601, what: "a library bigger than one page")
        }
        let app = freshPlaces
            ? audiobooksLaunchWithNoPlacesKept(mode: mode)
            : launch(mode: mode, push: "Audiobooks", pushDelay: Self.pushDelay)
        let count = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label BEGINSWITH %@", "showing "))
            .firstMatch
        if count.waitForExistence(timeout: 40) { return (app, true) }
        // **At an accessibility size the count line is below the fold, and a
        // lazy stack has not built it.** The whole screen — the heading, the
        // subtitle, the search box, the chips, the count and the rows — is one
        // `LazyVStack`, which is ``FilesView``'s shape; at
        // `AccessibilityXXXL` the heading and the search box alone are about
        // 700 of the phone's 852 points, so the first thing below them is
        // unbuilt rather than off screen (train S004's tree: title, subtitle,
        // `reindex`, the field, and then nothing). It still has to exist, so
        // this scrolls to it rather than settling for less.
        return (app, audiobooksScroll(app, to: count))
    }

    /// ``StubHouseUITests/launch(mode:paired:server:port:strip:push:pushDelay:)``
    /// for the audiobooks route, plus `-domovoi-audiobook-places-fresh`.
    ///
    /// A copy of the shared launcher's body rather than a parameter on it:
    /// `StubHouseUITests.swift` is shared with every other item tonight and
    /// this item may not edit it (NIGHT-RULES §2). The arguments are the ones
    /// `openAudiobooks` passes above and nothing else, so the only difference
    /// between a fresh-places launch and an ordinary one is the extra flag.
    /// If the shared launcher gains something every case needs, it belongs
    /// here too — which is the cost of the copy, written down.
    @MainActor
    func audiobooksLaunchWithNoPlacesKept(
        mode: String = StubHouseUITests.ordinaryTypeSize
    ) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = [
            "-domovoi-server", "127.0.0.1:6601",
            "-domovoi-token", Self.token,
            "-domovoi-audit-push", "Audiobooks",
            "-domovoi-audit-push-delay", "\(Self.pushDelay)",
            "-UIPreferredContentSizeCategoryName", mode,
            "-domovoi-audiobook-places-fresh",
        ]
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 30))
        return app
    }

    /// Open one book's page from the library and wait for its head.
    ///
    /// The row is a button labelled with the book's title
    /// (``AudiobooksBookRow``), and the page has landed when the back control
    /// exists — which only the page draws.
    @MainActor
    func openBook(_ app: XCUIApplication, _ title: String) -> Bool {
        let row = app.buttons[title].firstMatch
        guard audiobooksScroll(app, to: row) else { return false }
        row.tap()
        let back = app.buttons[StubAudiobooks.back].firstMatch
        let landed = back.waitForExistence(timeout: 25)
        if !landed {
            print("=== BOOK \(title) DREW NO PAGE — tree follows ===")
            print(app.debugDescription)
        }
        return landed
    }

    /// A book page's one action, **whichever word it is carrying**.
    ///
    /// `Text(if ((place ?: 0.0) > 5) "resume" else "play")` (`BookPage.kt:173`),
    /// so the word turns over the moment this phone holds a place past five
    /// seconds in the book. The place is the **phone's**: it lives in the
    /// app's own store, survives a relaunch, and `reset-fixtures` puts the
    /// *house* back and not the phone. Dune's server slot is adopted by the
    /// first case of a run that opens the question, so every case after that
    /// one finds `resume` where the first found `play`.
    ///
    /// Train S004 is what that cost: `testAudiobooks_TheResumeDialog…` and
    /// `testAudiobooks_TheServersPlaceWins…` both died on
    /// `No matches found for … '"play" IN identifiers'` while the tree held a
    /// `resume` button at the same point, and the audit case that runs before
    /// them in alphabetical order passed — it was the one that did the
    /// adopting. Asking for the control by one of its two words was testing
    /// the order the run happened to take.
    ///
    /// The word itself is still tested, where it can be said for certain:
    /// ``testAudiobooks_AFreshBookGoesStraightToPlay()`` opens a book with no
    /// place anywhere and requires `play` and the **absence** of `resume`.
    @MainActor
    func audiobooksStart(_ app: XCUIApplication) -> XCUIElement {
        app.buttons
            .matching(
                NSPredicate(
                    format: "label IN %@", [StubAudiobooks.play, StubAudiobooks.resume]
                )
            )
            .firstMatch
    }

    /// How many times the house has been asked for each book's cover.
    @MainActor
    func coverReads(port: Int = 6601) -> [String: Int] {
        var request = URLRequest(
            url: URL(string: "http://127.0.0.1:\(port)/api/_test/audiobooks-cover-reads")!
        )
        request.httpMethod = "POST"
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data("{}".utf8)
        let answered = expectation(description: "the stub counted its cover reads")
        var reads: [String: Int] = [:]
        URLSession.shared.dataTask(with: request) { data, _, _ in
            if let data,
               let top = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let found = top["reads"] as? [String: Int] {
                reads = found
            }
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 15)
        return reads
    }

    // MARK: - 1. the library

    /// The list draws the server's rows and the server's count.
    ///
    /// The count is the **server's** total and not the number of rows that
    /// loaded, which is the half a fixture's wrong wrapper hides: `_book_row`
    /// is nested under `items` with a `total` beside it when the request
    /// pages (`audiobooks.py:124-157`), and a decoder that read the bare-array
    /// shape would draw nine rows and no count at all.
    ///
    /// `Dune`'s meta line carries the length **and** the chapter count **and**
    /// a size, because those three come from three different places in the
    /// row — `duration_sec`, `chapters.count` and `BookRules.sizeBytes` — and
    /// one assertion over all three is what catches a row that decoded
    /// partially.
    @MainActor
    func testAudiobooks_DrawsTheLibraryWithTheServersCount() throws {
        let (app, landed) = openAudiobooks()
        evidence(app, "audiobooks-10-library")
        XCTAssertFalse(fellBackToLocalMedia(in: app), "the shell gave up on the server")
        XCTAssertTrue(landed, "the audiobooks list never answered — no count line drew")

        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.countAll].exists,
            "the count line is not the server's numbers"
        )
        let dune = app.buttons[StubAudiobooks.dune].firstMatch
        XCTAssertTrue(dune.exists, "no row for Dune")
        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.duneAuthor].exists,
            "the row drew no author, so `author` never decoded"
        )
        // The row carries its meta as the button's value
        // (``AudiobooksBookRow``), which is what VoiceOver reads and what a
        // query can compare exactly.
        let meta = dune.value as? String ?? ""
        XCTAssertTrue(meta.contains(StubAudiobooks.duneLength), "the meta has no length: \(meta)")
        XCTAssertTrue(meta.contains(StubAudiobooks.duneChapters), "the meta has no chapter count: \(meta)")
        XCTAssertTrue(meta.contains(" MB"), "the meta has no size: \(meta)")
    }

    /// Each sort chip asks the server for that sort, and the three answers are
    /// three different lists.
    ///
    /// The seed is built so the three orders have **three different first
    /// books**: `A Wizard of Earthsea` by title, `Grasshopper Lies Heavy` by
    /// author (`Aardvark, A.`) and `zzz-last of the breed` by `newest`. A
    /// chip that did nothing, or a `?sort=` that never reached the query,
    /// would leave the title order standing and fail on the author leg; a
    /// chip wired to the wrong value would fail on one of the other two.
    @MainActor
    func testAudiobooks_SortChipsReorderTheList() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")

        XCTAssertEqual(
            StubAudiobooks.firstByTitle, firstRowTitle(app),
            "the default sort is not the server's `title`"
        )

        app.buttons[StubAudiobooks.sortAuthor].firstMatch.tap()
        XCTAssertTrue(
            waitForFirstRow(app, StubAudiobooks.firstByAuthor),
            "the author sort did not put \(StubAudiobooks.firstByAuthor) first"
        )
        evidence(app, "audiobooks-11-sort-author")

        app.buttons[StubAudiobooks.sortNewest].firstMatch.tap()
        XCTAssertTrue(
            waitForFirstRow(app, StubAudiobooks.firstByNewest),
            "the newest sort did not put \(StubAudiobooks.firstByNewest) first"
        )
        evidence(app, "audiobooks-12-sort-newest")
    }

    /// Typing finds a book by its **narrator**, which is the leg of the
    /// server's filter nothing else on the screen shows.
    ///
    /// `Ganymede Quill` reads `Nightsong`, and the word `quill` appears in no
    /// other title, author or narrator in the seed
    /// (`fixtures/audiobooks.py`). So one row comes back and it is that one.
    ///
    /// **The absence is the discriminating half**: `A Wizard of Earthsea`
    /// sorts first on an unfiltered list, so a needle that was dropped on the
    /// way to `&q=` would leave it on screen and this case would pass on the
    /// presence assertion alone.
    @MainActor
    func testAudiobooks_SearchFindsABookByItsNarrator() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")

        let field = app.textFields[StubAudiobooks.searchPlaceholder].firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 20), "no search field")
        field.tap()
        app.typeText(StubAudiobooks.narratorNeedle)

        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.countOne].waitForExistence(timeout: 25),
            "the narrator search did not come back with exactly one book"
        )
        evidence(app, "audiobooks-13-narrator-search")
        XCTAssertTrue(
            app.buttons[StubAudiobooks.narratorBook].exists,
            "the one row is not the book the narrator reads"
        )
        XCTAssertFalse(
            app.buttons[StubAudiobooks.firstByTitle].exists,
            "the unfiltered list's first book is still on screen, so `q` never "
                + "reached the server"
        )
    }

    /// A new sort opens the list **at its first row**.
    ///
    /// Batch 18 on Android: the grid opened where the *old* list was scrolled
    /// to, the first book out of view, because a lazy grid follows the old
    /// first row's key into the new list (`AudiobooksScreen.kt:143-150`).
    ///
    /// ### What this case asserted until 2026-11-03, and why it caught nothing
    ///
    /// It waited for `zzz-last of the breed` to **exist** after the chip, and
    /// that row exists either way. A new list blanks the pager
    /// (`Pager.readFromTop(want: 0,…)`), so while the read is in flight the
    /// whole content is the heading, the field, the chips and a spinner —
    /// about 450 points against the window's 677 — and UIKit clamps the offset
    /// to zero before the answer lands. The new list therefore draws from the
    /// top with its first row built whether or not anything asked it to. The
    /// negative control of 2026-11-03 (train X007) took `wantsTop = true` out
    /// of `AudiobooksModel.listAnswered()` and this case passed in 39 seconds.
    ///
    /// ### What does discriminate, measured from the two trees
    ///
    /// Where the list **ends up**. ``AudiobooksModel/wantsTop`` makes the view
    /// `scrollTo(first, anchor: .top)`, which parks the first row under the
    /// navigation bar and takes the heading and `reindex` off the top of the
    /// window — a move of some 240 points that the clamp cannot imitate,
    /// because the clamp can only ever stop at zero:
    ///
    /// | run | `reindex` minY | first row minY | list minY |
    /// |---|---|---|---|
    /// | healthy (train S012) | **-84.3** | 137.3 | 93.3 |
    /// | no `wantsTop` (train X007) | **153.3** | 375.0 | 93.3 |
    ///
    /// So the two assertions are frames and not `exists`: the first row sits
    /// within ``StubAudiobooks/topSlack`` of the list's own top, and the
    /// heading has left the window. Every element named here exists in both
    /// trees, which is why the old assertion could not tell them apart.
    @MainActor
    func testAudiobooks_ANewSortOpensAtTheTop() throws {
        let (app, landed) = openAudiobooks(grow: 60)
        XCTAssertTrue(landed, "the grown audiobooks list never answered")

        let deep = app.buttons[StubAudiobooks.fillerDeep].firstMatch
        XCTAssertTrue(audiobooksScroll(app, to: deep), "the list never built \(StubAudiobooks.fillerDeep)")

        // **Back to the head, and far enough back that the heading is in the
        // window** — not merely until the chip is *built*. A lazy stack builds
        // a little beyond the viewport, so the pass that first makes the chip
        // exist can leave it, and the heading above it, off the top of the
        // window: train X007 tapped the chip at y 159 with the heading at
        // about 29, above the list's own 93.3. "The heading is gone
        // afterwards" says nothing unless it was there to begin with, so this
        // scrolls on the measurement rather than on existence.
        let heading = app.buttons[StubAudiobooks.reindex].firstMatch
        var backAtTheHead = false
        for _ in 0..<40 {
            if audiobooksInWindow(app, heading) {
                backAtTheHead = true
                break
            }
            audiobooksList(app).swipeDown(velocity: .slow)
        }
        XCTAssertTrue(backAtTheHead, "the head of the list never came back into its window")
        let chip = app.buttons[StubAudiobooks.sortNewest].firstMatch
        XCTAssertTrue(chip.exists, "the sort chips are not under the heading")
        chip.tap()

        XCTAssertTrue(
            waitForFirstRow(app, StubAudiobooks.firstByNewest),
            "the newest sort never drew \(StubAudiobooks.firstByNewest) first"
        )
        let first = app.buttons[StubAudiobooks.firstByNewest].firstMatch
        // The scroll is animated and runs *after* the page lands, so the gap
        // is polled rather than read once.
        let settled = audiobooksSettledAtTop(app, first)
        evidence(app, "audiobooks-14-new-sort-at-top")
        XCTAssertTrue(
            settled,
            "the new sort's first row is \(audiobooksTopGap(app, first)) points below the list's "
                + "top rather than within \(StubAudiobooks.topSlack) — the list opened where the "
                + "old one was scrolled to, which is Android's batch 18 bug"
        )
        XCTAssertFalse(
            audiobooksInWindow(app, heading),
            "the heading is still in the window, so nothing scrolled: the new "
                + "list opened at the offset the clamp left behind"
        )
    }

    /// Paging does **not** take you back to the top.
    ///
    /// The top request is once per *list*, not once per answer: `toppedFor`
    /// is what makes it once (``AudiobooksModel``'s `listAnswered`), and a
    /// `wantsTop` raised on every page would throw the reader back to book one
    /// each time they reached the foot of one — Android's batch 18 fix gone
    /// wrong in the other direction, which nothing else here would notice.
    ///
    /// `Grasshopper Lies Heavy` sorts after every filler, so it is on page
    /// **two** only: reaching it is a second request. By then the heading is
    /// some 1,300 points above the window; a yank back to the top would put it
    /// in the window again.
    @MainActor
    func testAudiobooks_PagingDoesNotYankTheListBackToTheTop() throws {
        let (app, landed) = openAudiobooks(grow: 60)
        XCTAssertTrue(landed, "the grown audiobooks list never answered")

        XCTAssertTrue(
            audiobooksScroll(app, to: app.buttons[StubAudiobooks.firstByAuthor].firstMatch),
            "page two's rows never drew"
        )
        evidence(app, "audiobooks-16-paged-without-a-yank")
        XCTAssertFalse(
            audiobooksInWindow(app, app.buttons[StubAudiobooks.reindex].firstMatch),
            "the heading is back in the window after a second page landed, so "
                + "paging scrolled the reader to the top of the list"
        )
    }

    /// The list loads as you scroll, and the count grows with it.
    ///
    /// Sixty-nine books against a page of fifty (``Paging/defaultPageSize``),
    /// so the first answer is `showing 50 of 69 books` and the foot of page
    /// one is **unbuilt** rather than off screen. `Grasshopper Lies Heavy`
    /// sorts after every filler, so it is on page **two** only: its presence
    /// is what cannot happen without a second request.
    @MainActor
    func testAudiobooks_TheListLoadsAsYouScroll() throws {
        let (app, landed) = openAudiobooks(grow: 60)
        XCTAssertTrue(landed, "the grown audiobooks list never answered")
        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.countFirstPage].exists,
            "the first answer is not one page of the grown library"
        )
        XCTAssertFalse(
            app.buttons[StubAudiobooks.firstByAuthor].exists,
            "a page-two book is on screen before anything scrolled, so the "
                + "pager asked for more than a page"
        )

        let footOfPageOne = app.buttons[StubAudiobooks.lastOfFirstPage].firstMatch
        XCTAssertTrue(
            audiobooksScroll(app, to: footOfPageOne),
            "the list never built the foot of page one"
        )
        XCTAssertTrue(
            audiobooksScroll(app, to: app.buttons[StubAudiobooks.firstByAuthor].firstMatch),
            "page two's rows never drew"
        )
        evidence(app, "audiobooks-15-second-page")
        // **Back up to the count line, which is at the head of the stack.**
        // It is drawn once, above the rows, so by the foot of page one it is
        // over a thousand points above the viewport and a lazy stack has
        // dropped it. Train S004 stood at the bottom waiting 25 seconds for a
        // line it could not have seen, with `Grasshopper Lies Heavy` — a
        // page-two book — already in the tree three rows below its own foot:
        // the app had paged correctly and the case was looking the wrong way.
        XCTAssertTrue(
            audiobooksScroll(
                app, to: app.staticTexts[StubAudiobooks.countGrown].firstMatch, up: true
            ),
            "the count line did not grow, so no second page was counted"
        )
    }

    /// A failed read draws the failure and a retry, **not** an empty library.
    ///
    /// "no audiobooks yet" beside a read that did not land is a lie about the
    /// house, and it is the easiest mistake to make: both states are "no
    /// rows". The fixture's failure is one-shot, so the retry has something
    /// to come back with.
    @MainActor
    func testAudiobooks_AFailedListOffersARetry() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the audiobook seed")
        control("audiobooks-fail", "{\"on\":true}", port: 6601, what: "a failing library read")
        let app = launch(push: "Audiobooks", pushDelay: Self.pushDelay)

        let retry = app.buttons[StubAudiobooks.retry].firstMatch
        XCTAssertTrue(retry.waitForExistence(timeout: 40), "a failed read drew no retry")
        evidence(app, "audiobooks-16-failed")
        XCTAssertFalse(
            app.staticTexts[StubAudiobooks.noneYet].exists,
            "a failed read told the person they have no audiobooks"
        )

        retry.tap()
        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.countAll].waitForExistence(timeout: 30),
            "the retry did not bring the library back"
        )
        XCTAssertTrue(app.buttons[StubAudiobooks.dune].exists, "no rows after the retry")
    }

    /// A search that matched nothing says so **and names the needle back**.
    ///
    /// Two empty states, and which one is drawn is the whole point: getting
    /// them the wrong way round tells somebody with a library that they have
    /// none. The needle in the sentence is the one the list was asked with,
    /// not whatever has been typed since.
    @MainActor
    func testAudiobooks_AnEmptySearchSaysSoWithTheWords() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")

        let field = app.textFields[StubAudiobooks.searchPlaceholder].firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 20), "no search field")
        field.tap()
        app.typeText(StubAudiobooks.missingNeedle)

        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.noMatch].waitForExistence(timeout: 25),
            "an empty search drew no `no books match`"
        )
        evidence(app, "audiobooks-17-empty-search")
        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.searchIsMissing].exists,
            "the empty line does not name the needle the list was asked with"
        )
        XCTAssertFalse(
            app.staticTexts[StubAudiobooks.noneYet].exists,
            "the two empty states are swapped — a filtered miss read as an "
                + "empty house"
        )
    }

    /// `reindex` posts, says what it indexed, and the list comes back bigger.
    ///
    /// Three things in one tap and all three are asserted, because each can
    /// fail alone: the POST (no toast at all), the `scanned` read (a toast
    /// with the wrong number) and the reload (the right toast over the old
    /// list). The fixture really adds the tenth book, so the last of the
    /// three cannot pass against an app that never re-read.
    @MainActor
    func testAudiobooks_ReindexSaysWhatItIndexed() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")

        app.buttons[StubAudiobooks.reindex].firstMatch.tap()
        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.indexedNine].waitForExistence(timeout: 30),
            "the reindex said nothing about what it indexed"
        )
        evidence(app, "audiobooks-18-reindexed")
        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.countAfterReindex].waitForExistence(timeout: 30),
            "the list was not re-read after the reindex"
        )
        XCTAssertTrue(
            audiobooksScroll(app, to: app.buttons[StubAudiobooks.reindexedBook].firstMatch),
            "the book the reindex found never drew"
        )
    }

    // MARK: - 2. one book's page

    /// A book's page draws its narrator, its chapters, and — once the phone
    /// and the server have compared places — where you are.
    ///
    /// **Why the place needs the dialog first.** The page reads the place the
    /// *phone* holds, which is Android's `produceState(null, bookId, …)`
    /// (`BookPage.kt:96-98`); the compare with the server's slot belongs to
    /// ``BookPositionSync/open(bookID:server:)``, which runs when a book
    /// starts. So the page of a book this phone has never played shows no
    /// place until something asks — exactly as Android's does. Tapping the
    /// start control runs that compare and raises the question; `not now`
    /// answers it without playing anything, and the adopted place is then the
    /// phone's.
    ///
    /// That makes this one case cover the whole chain: the detail read, the
    /// chapter list, `PositionRules.resolve`'s adopt branch through a real
    /// round trip, and `placeText`'s assembly.
    ///
    /// ### Which chapter row is asserted, and why it is the second
    ///
    /// Chapter **1** is a row whose value depends on the order the run took:
    /// it carries `— where you are now` the moment this phone holds Dune's
    /// place, which is from the first case of the run that opens the question
    /// onwards, and the phone's store is not what `reset-fixtures` puts back
    /// (``audiobooksStart(_:)``). Chapter **2** starts at 1:46:18, which is
    /// three quarters of an hour past anything this seed can put the place
    /// at, so it is never the current one and its value is its start time and
    /// nothing else — an exact equality that still fails if the row stopped
    /// saying where its chapter starts. Train S004 asserted chapter one's
    /// value exactly and failed on `0:00 — where you are now`, which is the
    /// app being right.
    ///
    /// The marker is then asserted **after** the compare, where it is certain
    /// rather than incidental: once the server's 1:01:12 has been adopted the
    /// place is inside chapter one, so that row and no other says so.
    @MainActor
    func testAudiobooks_ABookPageShowsItsChaptersAndPlace() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")
        XCTAssertTrue(openBook(app, StubAudiobooks.dune), "Dune's page never drew")

        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.duneNarrator].exists,
            "the page drew no `read by`, so `narrator` never decoded"
        )
        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.chaptersLabel].exists,
            "the page drew no chapters section"
        )
        let chapterOne = app.buttons[StubAudiobooks.duneChapterOne].firstMatch
        XCTAssertTrue(chapterOne.exists, "no row for Dune's first chapter")
        XCTAssertTrue(
            (chapterOne.value as? String ?? "").hasPrefix(StubAudiobooks.zeroTime),
            "Dune's first chapter row reads `\(chapterOne.value as? String ?? "")` "
                + "rather than starting with the time the chapter starts at"
        )
        let chapterTwo = app.buttons[StubAudiobooks.duneChapterTwo].firstMatch
        XCTAssertTrue(chapterTwo.exists, "no row for Dune's second chapter")
        XCTAssertEqual(
            StubAudiobooks.chapterTwoStart, chapterTwo.value as? String,
            "the chapter row does not say where the chapter starts"
        )
        evidence(app, "audiobooks-20-book-page")

        audiobooksStart(app).tap()
        let notNow = app.buttons[StubAudiobooks.notNow].firstMatch
        XCTAssertTrue(notNow.waitForExistence(timeout: 25), "the question never came up")
        notNow.tap()

        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.dunePlace].waitForExistence(timeout: 25),
            "the page shows no place after the phone and the server compared"
        )
        // The place is 1:01:12, which falls in chapter one and in no other
        // chapter, so exactly one row says where you are — in **words**, since
        // the shaded ground Android marks it with (`BookUi.kt:283`) is not
        // something a reader hears.
        XCTAssertEqual(
            StubAudiobooks.chapterOneHere, chapterOne.value as? String,
            "the chapter the adopted place falls in does not say so"
        )
        XCTAssertEqual(
            StubAudiobooks.chapterTwoStart, chapterTwo.value as? String,
            "a chapter the place is nowhere near is marked as where you are"
        )
        evidence(app, "audiobooks-21-book-place")
    }

    /// A folder book with no chapters of its own lists **its files**.
    ///
    /// `BookRules.chapters`' folder fallback (`BookModels.kt:97-101`): one
    /// chapter per file, titled by the file's own title. The Hobbit is seeded
    /// with three named files and `chapters: []`.
    ///
    /// The absence of `Chapter 1` is the discriminator: that is what Dune's
    /// chapters are called, so a page that drew the *row's* chapters for a
    /// book that has none would draw nothing here and a presence-only
    /// assertion could not tell the difference.
    @MainActor
    func testAudiobooks_AFolderBookWithoutChaptersListsItsFiles() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")
        XCTAssertTrue(openBook(app, StubAudiobooks.hobbit), "The Hobbit's page never drew")
        evidence(app, "audiobooks-22-folder-chapters")

        for file in StubAudiobooks.hobbitFiles {
            XCTAssertTrue(
                app.buttons[file].firstMatch.exists,
                "no chapter row for the file \(file)"
            )
        }
        XCTAssertFalse(
            app.buttons[StubAudiobooks.duneChapterOne].exists,
            "the page drew a numbered chapter for a book whose row has none"
        )
    }

    /// A book with no playable file draws, and offers **no** `play`.
    ///
    /// `Sabriel` is a folder book whose `files` name nothing, so
    /// `BookRules.parts` is empty and the audio route would answer **400**
    /// (`audiobooks.py:325-326`). A `play` there would be a control that
    /// cannot do what it says — the one thing this project will not ship.
    ///
    /// Its chapter rows are **plain text rather than buttons** for the same
    /// reason: a tap that silently did nothing is the same lie in a smaller
    /// control, and a chapter list is still worth reading.
    ///
    /// ### And `save to device` **is** drawn
    ///
    /// The two controls are decided by different questions, which is the half
    /// this case gained in the 2026-11-03 batch (D46/D47). `play` needs parts
    /// and an engine; the save needs neither — a folder book arrives as the
    /// zip the server builds (`audiobooks.py:353-418`), and keeping a file is
    /// worth doing on a phone that cannot play it. A case that asserted
    /// *nothing* was offered here would pass just as well over a screen that
    /// had lost the save, so both are named.
    ///
    /// What is **not** asserted: what `/api/audiobooks/{id}/download` answers
    /// for a folder book whose `files` name nothing. The control is honest
    /// about what it does — it fetches and hands over — and a failed fetch
    /// says so in a toast; whether this particular book zips is the server's
    /// question and `fixtures/audiobooks.py` does not claim an answer to it.
    @MainActor
    func testAudiobooks_ABookWithNoPlayableFilesDrawsWithoutAPlayControl() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")
        XCTAssertTrue(openBook(app, StubAudiobooks.sabriel), "Sabriel's page never drew")
        evidence(app, "audiobooks-23-nothing-playable")

        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.sabrielNarrator].exists,
            "the page did not draw for a book with nothing playable"
        )
        XCTAssertFalse(
            app.buttons[StubAudiobooks.play].exists,
            "a book with no playable file offered `play`, which would 400"
        )
        XCTAssertFalse(
            app.buttons[StubAudiobooks.resume].exists,
            "a book with no playable file offered `resume`"
        )
        XCTAssertTrue(
            containing(app, StubAudiobooks.sabrielPart).exists,
            "the chapter list is gone as well as the play control"
        )
        XCTAssertFalse(
            app.buttons[StubAudiobooks.sabrielPart].exists,
            "a chapter row is a button on a book nothing can play"
        )
        // The save, which answers a different question from the play — and
        // the answer is the same one here, for a different reason.
        //
        // `save to device` needs no audio engine; it does need the book to
        // **name a file**. Sabriel is a folder book whose `files` name
        // nothing, so `GET /api/audiobooks/11/download` walks a folder with no
        // audio in it and answers **404 "book folder has no audio files"**
        // (`web/backend/api/audiobooks.py:388-393`). A button that always
        // fails is the control [[no-misleading-controls]] forbids.
        //
        // **This case asserted the opposite until review 1 of this batch**, and
        // the fixture let it: `fixtures/player.py` answered 200 for every book
        // id, so the case passed over a control that could never work on the
        // real house. Both are fixed — the fixture 404s for this book now
        // (`BOOKS_WITH_NO_AUDIO`), and the control is gated on
        // ``AudiobookDetailModel/canSave``.
        //
        // Android draws its save ungated (`BookPage.kt:174-181`) and so starts
        // a download that fails; that divergence is a line in
        // `design-notes/ios/ANDROID-DEFECTS-FOUND-BY-IOS.md`.
        XCTAssertFalse(
            app.buttons[StubAudiobooks.saveControl].exists,
            "a book whose files name nothing offers `\(StubAudiobooks.saveControl)`, "
                + "and the route it would call answers 404 for exactly this book"
        )
        // And the discriminating half, so this is not an assertion that the
        // save control was simply lost: it **is** there on a book that has a
        // file, which is what `testAudiobooks_NoControlPromisesWhatThisAppCannotDo`
        // asserts on Dune's page in the same run.
        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.sabrielNarrator].exists,
            "the page itself is gone, so the absences above say nothing"
        )
    }

    /// A size the server does not know reads as an **em dash**, never `0 B`.
    ///
    /// `A Wizard of Earthsea` has no `size_bytes`, no `duration_sec`, and one
    /// file with no size. `BookRules.sizeBytes` refuses to guess a sum when
    /// any file is missing a figure (`BookModels.kt:106-110`), so the meta
    /// line is the dash and carries no size at all.
    ///
    /// `0 B` **anywhere on the screen** is the failure: that is what a sum
    /// over a file with no size produces, and it would read as a book of
    /// nothing.
    @MainActor
    func testAudiobooks_AnUnknownSizeReadsAsADash() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")

        let row = app.buttons[StubAudiobooks.unknownSizeBook].firstMatch
        XCTAssertTrue(audiobooksScroll(app, to: row), "no row for \(StubAudiobooks.unknownSizeBook)")
        evidence(app, "audiobooks-24-unknown-size")
        XCTAssertEqual(
            StubAudiobooks.dash, row.value as? String,
            "the meta of a book with no length and no size is not the em dash"
        )
        let zero = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS %@", StubAudiobooks.zeroBytes))
            .firstMatch
        XCTAssertFalse(
            zero.exists,
            "`0 B` is on screen, so a size was summed from a file that had none"
        )
    }

    // MARK: - 3. where you are, and where somebody else is

    /// The question offers the **room that got further**, and not the phone
    /// that did not.
    ///
    /// `ContinueRules` (`BookPositions.kt:227-257`): of the other places more
    /// than a minute ahead and naming a room or a device, the most recently
    /// used, then the furthest. The seed has two — the kitchen at 8013 s, ten
    /// minutes ago, and another phone at 3700 s, two days ago — against this
    /// phone's 3672 s.
    ///
    /// **The absent leg is the discriminator.** The other phone is 28 seconds
    /// ahead, inside `LEAD_SEC`'s minute, so it must not be offered: a `pick`
    /// that compared raw positions rather than the lead, or that sorted by
    /// distance rather than by recency first, would offer it.
    ///
    /// `2 h 13 m` with spaces is `ContinueRules.hm`, **not** `fmtBigDur`,
    /// which spells the same span `2h 13m`. Two wordings, two functions.
    @MainActor
    func testAudiobooks_TheResumeDialogOffersTheRoomThatGotFurther() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")
        XCTAssertTrue(openBook(app, StubAudiobooks.dune), "Dune's page never drew")

        audiobooksStart(app).tap()
        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.wereAtDune].waitForExistence(timeout: 25),
            "the question did not report the place this phone now holds"
        )
        evidence(app, "audiobooks-30-resume-dialog")

        XCTAssertTrue(
            app.buttons[StubAudiobooks.continueKitchen].exists,
            "the kitchen's place was not offered"
        )
        XCTAssertTrue(
            app.buttons[StubAudiobooks.resumeAtDune].exists,
            "there is no way to carry on where this phone was"
        )
        XCTAssertTrue(app.buttons[StubAudiobooks.startOver].exists, "there is no `start over`")
        let otherPhone = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS %@", StubAudiobooks.continueOtherPhone))
            .firstMatch
        XCTAssertFalse(
            otherPhone.exists,
            "a place 28 seconds ahead of this phone's was offered as somewhere "
                + "else to carry on from — `ContinueRules.LEAD_SEC` is not being applied"
        )
    }

    /// A book nobody has started goes **straight to play**, with no question.
    ///
    /// `StartPlan.asks` is false below five seconds with no offer
    /// (`BookPlay.kt:87`). `uncovered: Tehanu` has no server slot and no other
    /// places, so a dialog here would mean `asks` fires on a book nobody has
    /// touched — and every first play in the house would ask a pointless
    /// question.
    ///
    /// **Launched with no places kept, because this case is the one that
    /// writes one.** It taps the start control and then waits eight seconds
    /// for a question that must not come — and eight seconds of playback is a
    /// place, saved in the phone's own file, which `reset-fixtures` does not
    /// touch and a reinstall over the top keeps. Train S010 read
    /// `your place: 0:08 of 8:00:00 · 0%` and a `resume` button on
    /// `uncovered: Tehanu`: its own previous run's eight seconds. The app was
    /// right — a place past five seconds *is* `resume` (`BookPage.kt:173`) —
    /// and the case could only ever pass once per Simulator. So the book is
    /// fresh by construction here (``AudiobooksView/keptPlaces()``), and this
    /// run leaves nothing behind for the next one either.
    @MainActor
    func testAudiobooks_AFreshBookGoesStraightToPlay() throws {
        let (app, landed) = openAudiobooks(freshPlaces: true)
        XCTAssertTrue(landed, "the audiobooks list never answered")
        XCTAssertTrue(openBook(app, StubAudiobooks.tehanu), "Tehanu's page never drew")

        // **Waited for, not read the instant the page draws.** The back
        // control is the page's head and exists as soon as the push lands;
        // the start control is drawn from `canPlay`, which is false until
        // `AudiobookDetailModel.read` has the book (`AudiobookDetailView.swift:186`).
        // Train S007 asked 50 ms after the head appeared and reported "a
        // fresh book's button does not say `play`" against a page that had
        // not been told what the book was yet.
        let play = app.buttons[StubAudiobooks.play].firstMatch
        XCTAssertTrue(
            play.waitForExistence(timeout: 25),
            "a fresh book's button does not say `play`"
        )
        XCTAssertFalse(
            app.buttons[StubAudiobooks.resume].exists,
            "a book nobody has started says `resume`"
        )
        play.tap()
        // Given time to be wrong: a dialog that comes up late would otherwise
        // pass an assertion taken immediately.
        XCTAssertFalse(
            app.buttons[StubAudiobooks.startOver].waitForExistence(timeout: 8),
            "a book nobody has started asked where to start"
        )
        evidence(app, "audiobooks-31-fresh-book")
    }

    /// With nothing kept here and a place on the server, **the server's
    /// place stands**.
    ///
    /// `PositionRules.resolve`'s adopt branch (`BookPositions.kt:63-93`),
    /// through a real read of a real route: local nil, the server holding a
    /// row — which the route signals by sending an `updated_at` at all
    /// (`audiobooks.py:599-615`) — so the answer is the server's, adopted.
    ///
    /// The **absence** of `You haven't started this on this phone.` is what
    /// makes this a test of the adopt branch rather than of the dialog: that
    /// is the sentence a resolve which ignored the server's row would produce.
    @MainActor
    func testAudiobooks_TheServersPlaceWinsWhenOnlyItMoved() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")
        XCTAssertTrue(openBook(app, StubAudiobooks.dune), "Dune's page never drew")

        audiobooksStart(app).tap()
        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.wereAtDune].waitForExistence(timeout: 25),
            "the server's place was not adopted as this phone's"
        )
        evidence(app, "audiobooks-32-server-place-adopted")
        XCTAssertFalse(
            app.staticTexts[StubAudiobooks.notStartedHere].exists,
            "the question says nobody has started this book, with a place "
                + "sitting in the server's slot for this phone"
        )
    }

    // MARK: - 4. covers

    /// A book the server named no cover for draws its glyph and is **never
    /// asked about**; a book whose cover route answers 404 is asked **once**.
    ///
    /// §6's whole reason for ``AudiobookCoverStore`` existing beside
    /// ``VideoPosterStore``: that one maps every failure to `.failed`, "worth
    /// another try on the next appearance", and the cover route answers
    /// **404** for a book with no cover art. Every coverless book would
    /// re-fetch on every scroll pass, for ever.
    ///
    /// Counted at the house rather than guessed from the screen, because the
    /// cover is `accessibilityHidden` — a picture that is correct and a
    /// picture that is being re-fetched look identical in a tree. The two
    /// books are the two shapes: `uncovered: Tehanu` names no route (the view
    /// does not ask for a 404 it can predict), `zzz-last of the breed` names
    /// one that is gone (the only way to reach `.absent`).
    @MainActor
    func testAudiobooks_ACoverlessBookDrawsItsGlyph() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")

        let missing = app.buttons[StubAudiobooks.firstByNewest].firstMatch
        XCTAssertTrue(audiobooksScroll(app, to: missing), "no row for the book whose cover has gone")
        // Two passes over it, so a store that re-asked on every appearance
        // would have asked more than once by now.
        for _ in 0..<6 { audiobooksList(app).swipeDown(velocity: .slow) }
        XCTAssertTrue(audiobooksScroll(app, to: missing), "the row did not come back on the second pass")
        evidence(app, "audiobooks-40-covers")

        let reads = coverReads()
        XCTAssertEqual(
            1, reads[StubAudiobooks.missingCoverID] ?? 0,
            "the cover of a book whose route 404s was asked for "
                + "\(reads[StubAudiobooks.missingCoverID] ?? 0) times — a 404 "
                + "must settle as `.absent` and never be asked again"
        )
        XCTAssertNil(
            reads[StubAudiobooks.noCoverID],
            "a book whose row names no cover route was asked for one anyway"
        )
        XCTAssertEqual(
            1, reads[StubAudiobooks.coveredID] ?? 0,
            "the one book that has a cover was fetched "
                + "\(reads[StubAudiobooks.coveredID] ?? 0) times rather than once"
        )
    }

    // MARK: - 5. nothing promises what this app cannot do

    /// No control anywhere on this screen offers something this app has not
    /// got.
    ///
    /// Four wordings, each one a control Android draws and this port left
    /// out: the upload (`AudiobooksScreen.kt:197`), the phone **shelf**'s
    /// `remove from phone` (`BookPage.kt:175-212`), `remove from server
    /// (admin)` (`:226`) and a room to cast to — which neither app has,
    /// because a cast sends library track ids and a book has no library id
    /// (`CastPlan.kt:7-26`).
    ///
    /// It was six. The save and the `listening as` chip came off in the
    /// 2026-11-03 batch because they exist now, and
    /// ``StubAudiobooks/absentControls`` says which and why. This case gained
    /// the other half of that: the save control **is** on a book's page, and
    /// the chip is absent for the reason it is designed to be — no listener
    /// picked — rather than for want of a picker.
    ///
    /// Spelt out here rather than imported, like every other string this file
    /// matches on: a query that silently followed a renamed constant would
    /// stop testing what it claims to.
    @MainActor
    func testAudiobooks_NoControlPromisesWhatThisAppCannotDo() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")

        assertNothingOffers(app, where: "the library")

        // The chip is hidden, and **this is the design** (D11): a phone that
        // has never picked a listener shows nothing at all, which is
        // Android's `val id = listenerId ?: return`. A chip reading
        // "listening as nobody" is the thing this asserts against.
        //
        // **The precondition is that no listener is picked on this
        // install**, which is a phone that has never been to Settings →
        // Phones & browsers. It was asserted as
        // `XCTAssertTrue(StubAudiobooks.houseHasNoListener)` over a
        // `static let … = true`: a tautology that tested nothing and read as
        // coverage in a case whose job is to prove what is and is not drawn.
        // Both are gone. What keeps the precondition true is
        // `testPlayer_ThePersonPickerNamesTheListenerOnPodcasts`, which puts
        // the install back before it judges anything.
        XCTAssertFalse(
            containing(app, StubAudiobooks.listenerChip).exists,
            "the library drew a listening-as chip with no listener picked"
        )

        XCTAssertTrue(openBook(app, StubAudiobooks.dune), "Dune's page never drew")
        assertNothingOffers(app, where: "a book's page")

        // And the one that **is** there. Asserted in the same case as the
        // absences, so a merge that lost the save button fails here rather
        // than quietly shrinking what this file claims.
        XCTAssertTrue(
            containing(app, StubAudiobooks.saveControl).exists,
            "a book's page has no `save to device` — D47 built it and "
                + "AudiobookDetailView draws it beside `play`"
        )
        evidence(app, "audiobooks-50-no-false-controls")
    }

    @MainActor
    private func assertNothingOffers(_ app: XCUIApplication, where screen: String) {
        for offer in StubAudiobooks.absentControls {
            let found = app.descendants(matching: .any)
                .matching(NSPredicate(format: "label CONTAINS[c] %@", offer))
                .firstMatch
            XCTAssertFalse(
                found.exists,
                "\(screen) offers \"\(offer)\", and this app has no backing "
                    + "for it — a control that cannot do what it says is not drawn"
            )
        }
    }

    // MARK: - 6. Apple's audit, and the type size

    /// The library, by Apple's own audit.
    ///
    /// One case for the screen, classified by **signature** with
    /// ``floorAndRest(_:)`` and never against a pinned total: a finding Apple
    /// can attribute to an element is a defect to fix, and an anonymous
    /// finding of an unrecognised kind is an unknown rather than a floor.
    ///
    /// ### The two signatures this screen produces
    ///
    /// **`Dynamic Type font sizes …`, named.** Seventeen of them on train
    /// S004, against the row titles, the row authors, the row meta lines, the
    /// count line and the three sort chips. `testTheFilesListAudits` settled
    /// this class for the whole app: a named Dynamic Type finding is not a
    /// defect by virtue of being named, those leaves are `.subheadline` and
    /// `.caption` with nothing fixed about them, and **the measurement is the
    /// test** — ``testAudiobooks_TheLibraryTextGrowsAtAX5()`` compares these
    /// very leaves at the two sizes and fails on anything that is really
    /// stuck.
    ///
    /// **One `Contrast failed`, against the em dash.** `A Wizard of Earthsea`
    /// has no length and no size, so its whole meta line is one `—`, and the
    /// audit measured that leaf — 7.7 × 14.3 points — as failing. The colour
    /// is not what it is about: `Le Guin, Ursula K.` is drawn two points above
    /// it in the **same** ``Color/fgMuted`` on the **same** white canvas
    /// (7.44:1, the rows have no card behind them) and the same audit does not
    /// report it. One em-dash stroke in a box that is mostly background is a
    /// finding about ink coverage. So it is pinned to that label: if the
    /// token ever does regress, the author line goes with it and lands in
    /// `unexplained` below.
    ///
    /// ### What train I003 answered, and why nothing here changed for it
    ///
    /// Two findings, and both are the shell's new large navigation title:
    /// `Text clipped` naming `domovoi / audiobooks` at **7.3 x 40.7 points**
    /// (a two-line title squeezed to seven points of width, in the 200-point
    /// blank band the screenshot shows under `Back`), and one anonymous
    /// `Contrast failed` — a finding with no measurement, which is what
    /// Apple reports over a text element it cannot read a colour pair off.
    /// The same pair turned up on `testMusicMore_AuditThePlaylistsTab`, and
    /// the lone anonymous `Contrast failed` on its own on
    /// `testSystemSettings_AppLayoutPassesApplesAccessibilityAudit`, which is
    /// a panel this unit has never touched.
    ///
    /// The seventeen named Dynamic Type findings and the named em-dash
    /// contrast this case was written around did **not** appear: the title
    /// takes 96 points of the page and the audit saw five rows where I002
    /// saw nine. So the filters above are left exactly as they are — they
    /// describe the screen, not this run of it — and the two findings go
    /// with the shell's fix. If either survives it, it is new and this case
    /// will say so.
    @MainActor
    @available(iOS 17.0, *)
    func testAudiobooks_TheLibraryPassesApplesAudit() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")
        let found = try auditIssues(app, "audiobooks-library")
        evidence(app, "audiobooks-60-library-audit")
        let split = Self.floorAndRest(found)
        for line in split.rest { print("AUDIOBOOKS AUDIT REST :: \(line)") }

        let measured = split.rest.filter { $0.hasPrefix("Dynamic Type font sizes") }
        let emDash = split.rest.filter {
            $0.hasPrefix("Contrast failed")
                && $0.contains("label='\(StubAudiobooks.dash)'")
        }
        let unexplained = split.rest.filter {
            !measured.contains($0) && !emDash.contains($0)
        }
        XCTAssertEqual([], unexplained, "the audiobooks library has an audit finding of its own")
        XCTAssertEqual(
            0, split.floor,
            "an anonymous finding appeared on the library, where there were none"
        )
    }

    /// A book's page with the question **up**.
    ///
    /// The dialog is audited rather than dismissed first, and that is the
    /// whole point of auditing this state: a `Contrast failed` naming an
    /// element *behind* a dialog is a finding about the page under the scrim,
    /// which is how the connection panel's two live `forget` controls were
    /// found. A ``DialogCard`` carries `.isModal` so VoiceOver cannot reach
    /// the page's own start control while the question is open.
    ///
    /// ### What this case caught, and what it now allows
    ///
    /// Train S004 reported **six** findings that were not about type sizes:
    /// three `Contrast failed` naming `Frank Herbert`, the meta line and
    /// `play` — all three on the page, measured through the dialog's 35%
    /// scrim — and three `Contrast nearly passed` naming the dialog's own
    /// `resume at 1:01:12`, `start over` and `not now`. Both were true.
    /// ``AudiobooksView`` now hides the page behind the question
    /// (`.accessibilityHidden`, ``SettingsPlaceholder``'s fix word for word),
    /// because `.isModal` keeps VoiceOver *inside* the card without taking
    /// the dimmed page out of the tree; and the dialog's secondary answers
    /// carry the ordinary ink rather than ``Color/brandInk`` on a wash of
    /// itself, which is also what Android gives them (`BookUi.kt:186-189`).
    ///
    /// What is left allowed is the one class `testTheFilesListAudits` settled:
    /// a **named** `Dynamic Type font sizes …`, whose leaves
    /// ``testAudiobooks_TheLibraryTextGrowsAtAX5()`` measures.
    ///
    /// ### And the one class that hiding the page behind the question created
    ///
    /// Train S007 answered with **seven** anonymous
    /// `Potentially inaccessible text` — "this element appears to display
    /// text that should be represented using the accessibility API". They are
    /// the page itself: `audiobooks-61-book-page-audit.png` shows exactly
    /// seven of Dune's chapter rows legible below the card, and the tree
    /// taken with them shows the page's scroll view holding **no** elements
    /// at all. That is `.accessibilityHidden(true)` doing precisely what it
    /// was added to do, and the audit is reading the screenshot: text is
    /// drawn, no element covers it, so it reports one finding per run of
    /// words it can see.
    ///
    /// Both halves cannot be satisfied at once by this screen. The page
    /// behind a question is either in the tree — and then Apple measures its
    /// contrast through the 35% scrim and reports three `Contrast failed`,
    /// which S004 did and which were true — or it is out of the tree, and
    /// Apple reports it as text with no element. The first set names elements
    /// a person can still reach by VoiceOver while a question is up; the
    /// second names text nobody is being offered. The first is the defect.
    ///
    /// So this signature is a floor **in this state only**, and it is pinned
    /// the way ``knownScalingFalsePositive`` is: by its wording, with an
    /// element of `nil`, and never by a total. One of these that *names* an
    /// element is a real one and fails. The discriminator is the second audit
    /// below: the same page with the question **dismissed** must report none
    /// of them, which is what proves they come from the modal hide and not
    /// from a view of this screen's own that draws words without exposing
    /// them. DECISIONS §21.
    ///
    /// Everything else fails here.
    @MainActor
    @available(iOS 17.0, *)
    func testAudiobooks_ABookPagePassesApplesAudit() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")
        XCTAssertTrue(openBook(app, StubAudiobooks.dune), "Dune's page never drew")
        audiobooksStart(app).tap()
        XCTAssertTrue(
            app.buttons[StubAudiobooks.startOver].waitForExistence(timeout: 25),
            "the question never came up, so the modal state was never audited"
        )

        let found = try auditIssues(app, "audiobooks-book-page")
        evidence(app, "audiobooks-61-book-page-audit")
        let split = Self.floorAndRest(found)
        for line in split.rest { print("AUDIOBOOKS AUDIT REST :: \(line)") }

        let measured = split.rest.filter { $0.hasPrefix("Dynamic Type font sizes") }
        let hiddenPage = split.rest.filter(Self.audiobooksAnonymousInaccessibleText)
        let unexplained = split.rest.filter {
            !measured.contains($0) && !hiddenPage.contains($0)
        }
        XCTAssertEqual(
            [], unexplained,
            "a book's page with the question up has \(unexplained.count) "
                + "findings of its own:\n" + unexplained.joined(separator: "\n")
        )

        // **The discriminator.** The page hidden behind the question is the
        // only thing those findings may be. Answer `not now` and the page is
        // content again, in the tree, with no scrim over it — and the same
        // audit must report none of them. One here is a view of this
        // screen's own drawing words it never exposes, which is a real
        // defect and what this half is for.
        let notNow = app.buttons[StubAudiobooks.notNow].firstMatch
        XCTAssertTrue(notNow.waitForExistence(timeout: 25), "the question has no way out")
        notNow.tap()
        // The page back **in the tree** is the signal, and it is the same
        // fact this half turns on: `dunePlace` is drawn by the page, so it
        // cannot be found while the page is hidden behind the question.
        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.dunePlace].waitForExistence(timeout: 25),
            "`not now` left the question up, so the page was never audited uncovered"
        )
        XCTAssertFalse(app.buttons[StubAudiobooks.startOver].exists, "the question is still up")
        let uncovered = try auditIssues(app, "audiobooks-book-page-answered")
        evidence(app, "audiobooks-62-book-page-audit-answered")
        let afterSplit = Self.floorAndRest(uncovered)
        for line in afterSplit.rest { print("AUDIOBOOKS AUDIT REST (ANSWERED) :: \(line)") }
        XCTAssertEqual(
            [], afterSplit.rest.filter(Self.audiobooksAnonymousInaccessibleText),
            "the page draws text it does not expose even with no question over it"
        )
        // ### And the one class the uncovered half found the first time it
        // ### ever ran (train I004)
        //
        // I003 never got here — it failed earlier, on a `play`/`resume` it
        // could not find — so I004 is the first run of this second audit.
        // It came back with one finding that is not a type size: `Contrast
        // failed` naming `12:24:06` at `(309.33, 807.33, 59.67, 14.33)`.
        //
        // That is **Chapter 8's** duration, and the page's scroll view is
        // `{{0.0, 185.3}, {393.0, 584.7}}` — a foot at `770.0`. The whole
        // row (`{{16.0, 791.3}, {361.0, 44.0}}`) is built and clipped
        // entirely away: `audiobooks-62-book-page-audit-answered.png` shows
        // the list ending at Chapter 7 with the phone bar under it, and
        // Chapter 8 nowhere on the screen. Apple graded colours of pixels
        // the page never drew. The discriminator is in the same audit:
        // chapters 1–7's durations are the same `Text` in the same style
        // (``AudiobookChapterRow``) **inside** the viewport, and every one
        // of them reports a type-size finding and no contrast one.
        //
        // So a contrast finding whose element is wholly at or below the
        // page's own foot is taken out, by that measurement and not by a
        // belief, and nothing else is: one that **straddles** the foot has
        // pixels on screen and still fails here, which is narrower than the
        // rule `testStations_AuditFindsNothingNew` carries for the same
        // class. The foot is read off the app, never pinned as a number.
        let pageFoot = app.scrollViews.allElementsBoundByIndex
            .max(by: { $0.frame.height < $1.frame.height })?
            .frame.maxY
        var clippedAway: [String] = []
        let leftOver = afterSplit.rest.filter { line in
            if line.hasPrefix("Dynamic Type font sizes") { return false }
            if line.hasPrefix("Contrast"), let foot = pageFoot,
               let box = Self.frame(in: line), box.minY >= foot - 0.5 {
                clippedAway.append(line)
                return false
            }
            return true
        }
        for line in clippedAway {
            print("AUDIOBOOKS AUDIT (ANSWERED) below the page's foot \(pageFoot ?? -1): \(line)")
        }
        XCTAssertEqual(
            [], leftOver,
            "a book's page with nothing over it has an audit finding of its own "
                + "(page foot \(pageFoot.map { "\($0)" } ?? "not found")):\n"
                + afterSplit.rest.joined(separator: "\n")
        )
    }

    /// `Potentially inaccessible text` with no element — the shape the audit
    /// takes when it has read words off the screenshot and found no
    /// accessibility element covering them.
    ///
    /// Matched by wording **and** by `element: nil`, so one Apple can
    /// attribute still lands in `rest` and fails its caller, exactly as
    /// ``StubHouseUITests/floorAndRest(_:)`` treats the two Dynamic Type
    /// headlines.
    static func audiobooksAnonymousInaccessibleText(_ line: String) -> Bool {
        line.hasPrefix("Potentially inaccessible text") && line.hasSuffix("| element: nil")
    }

    /// Every named leaf grows at AX5, and the sort chips become a column.
    ///
    /// **Measured, not taken from a headline.** A named Dynamic Type finding
    /// has twice been a false positive on this track, so the test is a
    /// comparison of the same elements' heights at
    /// ``ordinaryTypeSize`` and at `UICTContentSizeCategoryAccessibilityXXXL`.
    /// A leaf that did not grow is a fixed `frame(height:)` or a
    /// `minimumScaleFactor`, both of which this app rejects.
    ///
    /// The chips are the other half: they are a row at the ordinary sizes and
    /// a `ViewThatFits` column at an accessibility one, which is Music's
    /// filter row's shape. A row that stayed a row at AX5 is three pills
    /// squeezed past the edge of the phone.
    @MainActor
    func testAudiobooks_TheLibraryTextGrowsAtAX5() throws {
        // **Each leaf is scrolled to, in the order the list draws them**, and
        // the whole pass starts at the top. Chat's AX5 case learnt this the
        // same way: one scroll position cannot hold every leaf at
        // `AccessibilityXXXL` — here the heading, the subtitle and the search
        // box alone are about 700 of 852 points, so the count line and the
        // first row are unbuilt while the heading is on screen and the
        // heading is gone by the time they are built. Measuring whatever
        // happened to be built is how a leaf that never grew gets skipped,
        // and `grown[label] == nil` below fails the case rather than passing
        // it quietly.
        func heights(_ app: XCUIApplication) -> [String: CGFloat] {
            var out: [String: CGFloat] = [:]
            let heading = app.staticTexts[StubAudiobooks.libraryTitle].firstMatch
            if !heading.exists { _ = audiobooksScroll(app, to: heading, up: true) }
            for label in StubAudiobooks.growingLeaves {
                let leaf = app.staticTexts[label].firstMatch
                if !leaf.exists { _ = audiobooksScroll(app, to: leaf, flicks: 12) }
                if leaf.exists { out[label] = leaf.frame.height }
            }
            let row = app.buttons[StubAudiobooks.dune].firstMatch
            if !row.exists { _ = audiobooksScroll(app, to: row, flicks: 12) }
            if row.exists { out[StubAudiobooks.dune] = row.frame.height }
            return out
        }

        /// The chips, scrolled back up to — they sit between the subtitle and
        /// the count line, so ``heights(_:)``'s downward pass has left them
        /// behind by the time it returns.
        func chips(_ app: XCUIApplication) -> (title: XCUIElement, newest: XCUIElement) {
            let title = app.buttons[StubAudiobooks.sortTitle].firstMatch
            if !title.exists { _ = audiobooksScroll(app, to: title, up: true) }
            return (title, app.buttons[StubAudiobooks.sortNewest].firstMatch)
        }

        let (ordinary, landedSmall) = openAudiobooks()
        XCTAssertTrue(landedSmall, "the audiobooks list never answered at the ordinary size")
        let small = heights(ordinary)
        let (titleChip, newestChip) = chips(ordinary)
        XCTAssertTrue(titleChip.exists && newestChip.exists, "the sort chips never drew")
        XCTAssertEqual(
            titleChip.frame.minY, newestChip.frame.minY, accuracy: 2,
            "the sort chips are not a row at the ordinary type size"
        )
        evidence(ordinary, "audiobooks-70-library-large")
        ordinary.terminate()

        let (big, landedBig) = openAudiobooks("UICTContentSizeCategoryAccessibilityXXXL", reset: false)
        XCTAssertTrue(landedBig, "the audiobooks list never answered at AX5")
        let grown = heights(big)
        evidence(big, "audiobooks-71-library-ax5")

        XCTAssertFalse(small.isEmpty, "nothing was measured at the ordinary type size")
        for (label, was) in small {
            guard let now = grown[label] else {
                XCTFail("`\(label)` is on screen at the ordinary size and gone at AX5")
                continue
            }
            XCTAssertGreaterThan(
                now, was,
                "`\(label)` is \(was) points at the ordinary size and \(now) at "
                    + "AX5 — it does not grow with the type size"
            )
        }

        let (bigTitle, bigNewest) = chips(big)
        XCTAssertTrue(bigTitle.exists && bigNewest.exists, "the sort chips never drew at AX5")
        XCTAssertGreaterThan(
            bigNewest.frame.minY, bigTitle.frame.minY + 8,
            "the three sort chips are still on one line at AX5, so they are "
                + "squeezed rather than stacked"
        )
    }

    // MARK: - small helpers

    /// The scroll view this screen's list lives in.
    ///
    /// **Not ``scrollUntilBuilt(_:_:flicks:)``, and not because of style.**
    /// That helper scrolls `app.collectionViews.firstMatch`, which its own
    /// doc explains: Chat's list is a `UICollectionView` because its rows
    /// need `swipeActions`. Audiobooks is a `ScrollView` over a `LazyVStack`,
    /// as Videos, Files and Music are, so there is no collection view on
    /// screen and that helper would report "NO LIST TO SCROLL" for every
    /// case below. `StubHouseUITests.swift` is a shared file this item may
    /// not edit (NIGHT-RULES §2), so the same gesture is spelt here against
    /// the right element. DECISIONS, "Needs a shared change".
    ///
    /// ### Why this is not `app.scrollViews.firstMatch`
    ///
    /// **At an accessibility type size the tab bar is itself a `ScrollView`.**
    /// That is what killed `testAudiobooks_TheLibraryTextGrowsAtAX5` on train
    /// S007: the tree at `AccessibilityXXXL` holds the library's scroll view
    /// at `{{0, 352.3}, {393, 349.7}}` **and** a second one at
    /// `{{0, 706}, {393, 146}}` carrying `home`, `music`, `videos`, `chat` —
    /// the phone bar, which scrolls horizontally once its four labels no
    /// longer fit. `firstMatch` took the bar, and the log is thirty
    /// `Swipe up ScrollView at {{0.0, 706.0}, {393.0, 146.0}}` against a bar
    /// that cannot scroll vertically while the library sat at 0%.
    ///
    /// The bar is told apart by what it carries rather than by its frame or
    /// its index: a scroll view holding both the `Home` and the `Chat` tab
    /// buttons is the bar. No audiobook row is labelled either word. At the
    /// ordinary sizes the bar is not a scroll view at all, so this returns
    /// exactly what `firstMatch` returned before.
    @MainActor
    func audiobooksList(_ app: XCUIApplication) -> XCUIElement {
        for candidate in app.scrollViews.allElementsBoundByIndex {
            guard candidate.exists else { continue }
            let isPhoneBar = candidate.buttons["Home"].exists && candidate.buttons["Chat"].exists
            if !isPhoneBar { return candidate }
        }
        return app.scrollViews.firstMatch
    }

    /// Scroll until `element` is **built**, and say whether it was.
    ///
    /// A lazy list's foot is unbuilt rather than off screen, and the gesture
    /// is a **slow flick** for the two reasons
    /// ``scrollUntilBuilt(_:_:flicks:)`` measured: a plain `swipeUp()` is
    /// worth about a viewport and steps over rows, and a press-and-drag held
    /// on a text field is taken by the field rather than by the scroll view.
    /// - Parameter up: scroll back towards the top instead of down. The count
    ///   line and the chips are at the **head** of this lazy stack, so a case
    ///   that has scrolled to the foot of a page cannot see them again
    ///   without this: `testAudiobooks_TheListLoadsAsYouScroll` waited
    ///   25 seconds at the bottom of train S004 for a line that was sitting
    ///   unbuilt 1,300 points above it, with page two's rows already drawn.
    @MainActor
    func audiobooksScroll(
        _ app: XCUIApplication,
        to element: XCUIElement,
        flicks: Int = 30,
        up: Bool = false
    ) -> Bool {
        let list = audiobooksList(app)
        guard list.waitForExistence(timeout: 20) else {
            print("=== NO AUDIOBOOKS LIST TO SCROLL — tree follows ===")
            print(app.debugDescription)
            return false
        }
        for _ in 0..<flicks {
            if element.exists { return true }
            if up {
                list.swipeDown(velocity: .slow)
            } else {
                list.swipeUp(velocity: .slow)
            }
        }
        if !element.exists {
            // The **tree**, not a sentence about the element: "it is not
            // there" is a claim about something absent, and the only useful
            // answer is what is.
            print("=== \(flicks) FLICKS AND STILL UNBUILT — tree follows ===")
            print(app.debugDescription)
        }
        return element.exists
    }

    // MARK: - where the list is, which `exists` cannot say

    /// How far below the list's own top an element's top sits, in points.
    ///
    /// Negative means it has been scrolled off the top of the window.
    /// `exists` stays true for a built row that is no longer visible, and
    /// true for a heading parked above the viewport, which is why every
    /// scroll-position assertion on this screen is a frame and not an
    /// existence. `.greatestFiniteMagnitude` for an element that is not built
    /// at all: as far away as the list can put it.
    @MainActor
    func audiobooksTopGap(_ app: XCUIApplication, _ element: XCUIElement) -> CGFloat {
        let list = audiobooksList(app)
        guard list.exists, element.exists else { return .greatestFiniteMagnitude }
        return element.frame.minY - list.frame.minY
    }

    /// Whether `element`'s top is inside the list's window at all.
    ///
    /// An unbuilt element is **not** in the window: a lazy stack drops what is
    /// far enough away, so for a heading 1,300 points above the viewport
    /// "scrolled away" and "unbuilt" are the same answer and both are what
    /// this asks about.
    @MainActor
    func audiobooksInWindow(_ app: XCUIApplication, _ element: XCUIElement) -> Bool {
        let gap = audiobooksTopGap(app, element)
        return gap >= 0 && gap < audiobooksList(app).frame.height
    }

    /// Poll until `row` is parked at the top of the list, and say whether it
    /// got there.
    ///
    /// Polled rather than read once: the scroll
    /// (``AudiobooksView``'s `onChange(of: books.wantsTop)`) runs **after** the
    /// page has landed and is animated, so the frame right after the first row
    /// appears is the frame before the move.
    @MainActor
    func audiobooksSettledAtTop(
        _ app: XCUIApplication,
        _ row: XCUIElement,
        seconds: TimeInterval = 10
    ) -> Bool {
        let deadline = Date().addingTimeInterval(seconds)
        repeat {
            if audiobooksTopGap(app, row) <= StubAudiobooks.topSlack { return true }
            usleep(300_000)
        } while Date() < deadline
        print("=== THE NEW LIST SAT \(audiobooksTopGap(app, row)) POINTS DOWN — tree follows ===")
        print(app.debugDescription)
        return false
    }

    /// The title of the list's first row, or "" when nothing drew.
    ///
    /// By **position**, not by `firstMatch` on a label: the question these
    /// cases ask is which book is first, and a query by name cannot answer
    /// it.
    @MainActor
    private func firstRowTitle(_ app: XCUIApplication) -> String {
        let rows = app.buttons.matching(
            NSPredicate(format: "label IN %@", StubAudiobooks.everySeededTitle)
        )
        var best: (y: CGFloat, label: String)?
        for index in 0..<rows.count {
            let row = rows.element(boundBy: index)
            guard row.exists else { continue }
            let y = row.frame.minY
            if best == nil || y < best!.y { best = (y, row.label) }
        }
        return best?.label ?? ""
    }

    @MainActor
    private func waitForFirstRow(_ app: XCUIApplication, _ title: String) -> Bool {
        let deadline = Date().addingTimeInterval(25)
        while Date() < deadline {
            if firstRowTitle(app) == title { return true }
            _ = app.buttons[title].firstMatch.waitForExistence(timeout: 1)
        }
        print("=== FIRST ROW IS \(firstRowTitle(app)), WANTED \(title) — tree follows ===")
        print(app.debugDescription)
        return false
    }
}

/// What the stub house's audiobook half holds, and what the app says about
/// it — by the words that appear on screen.
///
/// Spelt out here rather than imported, as every other string this file
/// matches on is: a query that silently followed a renamed constant would
/// stop testing what it claims to. The seed's own values are
/// `design-notes/ios/stubhouse/fixtures/audiobooks.py`; the app's wordings are
/// `AudiobooksStrings`, ported from the Kotlin.
enum StubAudiobooks {

    // The nine seeded books.
    static let dune = "Dune"

    /// Dune's row id (`fixtures/audiobooks.py`), as it appears in a path and
    /// in the position probe. It is the one book with a **seeded place**
    /// (`positions`, `(7, None)`), which is why the listener case opens it:
    /// a book the house knows a place for is a book whose page reads one.
    static let duneID = "7"
    static let hobbit = "The Hobbit"
    static let piranesi = "Piranesi"
    static let sabriel = "Sabriel"
    static let unknownSizeBook = "A Wizard of Earthsea"
    static let tehanu = "uncovered: Tehanu"
    static let firstByTitle = "A Wizard of Earthsea"
    static let firstByAuthor = "Grasshopper Lies Heavy"
    static let firstByNewest = "zzz-last of the breed"
    static let narratorBook = "Nightsong"
    static let reindexedBook = "found by the reindex"

    static let everySeededTitle = [
        "A Wizard of Earthsea", "Dune", "Grasshopper Lies Heavy", "Nightsong",
        "Piranesi", "Sabriel", "The Hobbit", "uncovered: Tehanu",
        "zzz-last of the breed",
    ]

    /// Dune's row: `21:15:43 · 12 chapters · 584.4 MB`, asserted in three
    /// pieces because the three come from three different fields.
    static let duneAuthor = "Frank Herbert"
    static let duneLength = "21:15:43"
    static let duneChapters = "12 chapters"
    static let duneNarrator = "read by Simon Vance"
    static let duneChapterOne = "Chapter 1"
    static let duneChapterTwo = "Chapter 2"
    static let zeroTime = "0:00"
    /// Chapter two's start, which is also its whole value: it is 45 minutes
    /// past the furthest place this seed holds, so it is never the chapter
    /// you are in.
    static let chapterTwoStart = "1:46:18"
    /// ``AudiobooksStrings/chapterPlayingNow(_:)`` over chapter one's start,
    /// which is what the row carries once Dune's place has been adopted.
    static let chapterOneHere = "0:00 — where you are now"

    /// `A Wizard of Earthsea` has no length, no size and one file with no
    /// size, so its whole meta line is the em dash — U+2014, not a hyphen.
    static let dash = "—"
    static let zeroBytes = "0 B"

    static let sabrielNarrator = "read by Tim Curry"
    /// One of Sabriel's two synthesised chapters, from a file that names
    /// nothing.
    static let sabrielPart = "disc one"

    /// The Hobbit's three files, which become its three chapter rows because
    /// its row carries no chapters at all.
    static let hobbitFiles = ["An Unexpected Party", "Roast Mutton", "A Short Rest"]

    /// How far below the list's own top the first row of a new list may sit:
    /// the navigation bar's 44 points and a little. Measured — train S012's
    /// healthy tree has it at **44.0**, and train X007's `wantsTop`-less one
    /// at **281.7**. Eighty, not the measured 44: the slack is there for the
    /// bar's height changing under a Dynamic Type or a layout it has not been
    /// measured at, and 80 is still a third of the distance the broken state
    /// sat at.
    static let topSlack: CGFloat = 80

    /// The library grown past one page: nine seeded plus sixty fillers. The
    /// fillers sort between `Dune` and `Grasshopper Lies Heavy`, so page one
    /// ends at `filler 047`.
    static let fillerDeep = "filler 030"
    static let lastOfFirstPage = "filler 047"

    // The count line, which only a completed read can write.
    static let countAll = "showing 9 of 9 books"
    static let countOne = "showing 1 of 1 books"
    static let countAfterReindex = "showing 10 of 10 books"
    static let countFirstPage = "showing 50 of 69 books"
    static let countGrown = "showing 69 of 69 books"

    // The app's own words.
    static let searchPlaceholder = "search books…"
    static let sortTitle = "title"
    static let sortAuthor = "author"
    static let sortNewest = "newest"
    static let reindex = "reindex"
    static let indexedNine = "Indexed 9 book(s)"
    static let noMatch = "no books match"
    static let noneYet = "no audiobooks yet"
    static let retry = "retry"
    static let play = "play"
    static let resume = "resume"
    static let startOver = "start over"
    static let notNow = "not now"
    static let chaptersLabel = "chapters"
    static let back = "back to audiobooks"
    static let notStartedHere = "You haven't started this on this phone."

    /// The needle that matches `Nightsong` through its narrator alone, and
    /// one that matches nothing at all.
    static let narratorNeedle = "quill"
    static let missingNeedle = "qqqq"
    static let searchIsMissing = "search = \"qqqq\""

    /// Dune's place: 3672 seconds of 76543, which is 4%.
    static let dunePlace = "your place: 1:01:12 of 21:15:43 · 4%"
    static let wereAtDune = "You were at 1:01:12 on this phone."
    static let resumeAtDune = "resume at 1:01:12"
    /// `ContinueRules.hm`, with spaces — **not** `fmtBigDur`'s `2h 13m`.
    static let continueKitchen = "continue from the kitchen (2 h 13 m)"
    /// The place that must **not** be offered: 28 seconds ahead of this
    /// phone's, inside `LEAD_SEC`'s minute.
    static let continueOtherPhone = "continue from phone"

    /// The three books the cover cases count reads for, as the house keys
    /// them (JSON object keys are strings).
    static let coveredID = "7"
    static let noCoverID = "13"
    static let missingCoverID = "14"

    /// Controls Android draws and this port left out. Each one is a sentence
    /// this app has no backing for.
    ///
    /// **Two came off the list in the 2026-11-03 batch**, and both for the
    /// same reason: they exist now.
    ///
    /// * `save to phone` — D47 built the one-off save, and ``saveControl``
    ///   below is the words it really carries. It was never the right string
    ///   anyway: the button says `save to device`, so this entry would have
    ///   gone on passing beside a save button that was right there, which is
    ///   an absence assertion that had quietly stopped testing anything.
    /// * `listening as` — D11 built the chip. It is hidden when no listener
    ///   has been picked, which is the stub house's own state, so it is
    ///   checked by ``listenerChip`` with that precondition stated rather
    ///   than asserted absent for a reason that is no longer true.
    ///
    /// `remove from phone` stays: the phone's **shelf** (a managed library of
    /// saved books with a discard) is still the PhoneMedia item's, and one
    /// save to the share sheet is not it.
    static let absentControls = [
        "add audiobooks to the server",
        "remove from phone",
        "remove from server",
        "play in a room",
    ]

    /// What the save control really says (`TransferStrings.saveToDevice`),
    /// spelt out here like every other string this file matches on.
    static let saveControl = "save to device"

    /// The chip's prefix (`PlayerStrings.listeningAsChip(_:)`).
    static let listenerChip = "listening as"

    /// The screen's heading, which is the first thing the list draws and the
    /// mark the Dynamic Type case scrolls back up to before it measures.
    static let libraryTitle = "Audiobooks"

    /// The leaves whose heights the Dynamic Type case compares, **in the
    /// order the list draws them** — that is what lets the case scroll to
    /// each in turn.
    static let growingLeaves = [
        libraryTitle,
        "books with chapters and your place, kept on the Domovoi",
        "showing 9 of 9 books",
    ]
}
