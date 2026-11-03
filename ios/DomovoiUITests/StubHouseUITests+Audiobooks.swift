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
/// network. That property is covered where it can be: the package test
/// `testABooksPlaceIsNeverReadFromAnotherHouse` switches the key **inside**
/// the round trip, which is the shape [[read-after-await-wrong-key]] asks
/// for. DECISIONS §8.
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
    @MainActor
    func openAudiobooks(
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        reset: Bool = true,
        grow: Int? = nil
    ) -> (app: XCUIApplication, landed: Bool) {
        if reset {
            control("reset-fixtures", "{}", port: 6601, what: "the audiobook seed")
        }
        if let grow {
            control("audiobooks-grow", "{\"books\":\(grow)}", port: 6601, what: "a library bigger than one page")
        }
        let app = launch(mode: mode, push: "Audiobooks", pushDelay: Self.pushDelay)
        let count = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label BEGINSWITH %@", "showing "))
            .firstMatch
        return (app, count.waitForExistence(timeout: 40))
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
    /// The list is grown past one page and scrolled into the fillers, then
    /// swiped back only until the chips are **built** — not to the top, which
    /// is the difference that makes this discriminate. A `newest` tapped from
    /// there must leave `zzz-last of the breed` on screen; with the old offset
    /// kept, the new list's first row would be unbuilt and `exists` false.
    ///
    /// The scroll-offset arithmetic itself is the package test's
    /// (`testANewListAsksTheViewToGoToTheTop`): a Simulator cannot park a lazy
    /// list at a chosen offset and keep a top-of-screen chip reachable at the
    /// same time.
    @MainActor
    func testAudiobooks_ANewSortOpensAtTheTop() throws {
        let (app, landed) = openAudiobooks(grow: 60)
        XCTAssertTrue(landed, "the grown audiobooks list never answered")

        let deep = app.buttons[StubAudiobooks.fillerDeep].firstMatch
        XCTAssertTrue(audiobooksScroll(app, to: deep), "the list never built \(StubAudiobooks.fillerDeep)")

        let chip = app.buttons[StubAudiobooks.sortNewest].firstMatch
        for _ in 0..<40 where !chip.exists {
            audiobooksList(app).swipeDown(velocity: .slow)
        }
        XCTAssertTrue(chip.exists, "the sort chips never came back into the list's window")
        chip.tap()

        let first = app.buttons[StubAudiobooks.firstByNewest].firstMatch
        let onScreen = first.waitForExistence(timeout: 25)
        evidence(app, "audiobooks-14-new-sort-at-top")
        XCTAssertTrue(
            onScreen,
            "the new sort's first book is not on screen — the list kept the "
                + "old scroll offset, which is Android's batch 18 bug"
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
            app.staticTexts[StubAudiobooks.countGrown].waitForExistence(timeout: 25),
            "the count line did not grow, so no second page was asked for"
        )
        evidence(app, "audiobooks-15-second-page")
        XCTAssertTrue(
            audiobooksScroll(app, to: app.buttons[StubAudiobooks.firstByAuthor].firstMatch),
            "page two's rows never drew"
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
    /// place until something asks — exactly as Android's does. Tapping `play`
    /// runs that compare and raises the question; `not now` answers it
    /// without playing anything, and the adopted place is then the phone's.
    ///
    /// That makes this one case cover the whole chain: the detail read, the
    /// chapter list, `PositionRules.resolve`'s adopt branch through a real
    /// round trip, and `placeText`'s assembly.
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
        XCTAssertEqual(
            StubAudiobooks.zeroTime, chapterOne.value as? String,
            "the chapter row does not say where the chapter starts"
        )
        evidence(app, "audiobooks-20-book-page")

        app.buttons[StubAudiobooks.play].firstMatch.tap()
        let notNow = app.buttons[StubAudiobooks.notNow].firstMatch
        XCTAssertTrue(notNow.waitForExistence(timeout: 25), "the question never came up")
        notNow.tap()

        XCTAssertTrue(
            app.staticTexts[StubAudiobooks.dunePlace].waitForExistence(timeout: 25),
            "the page shows no place after the phone and the server compared"
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

        app.buttons[StubAudiobooks.play].firstMatch.tap()
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
    @MainActor
    func testAudiobooks_AFreshBookGoesStraightToPlay() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")
        XCTAssertTrue(openBook(app, StubAudiobooks.tehanu), "Tehanu's page never drew")

        let play = app.buttons[StubAudiobooks.play].firstMatch
        XCTAssertTrue(play.exists, "a fresh book's button does not say `play`")
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

        app.buttons[StubAudiobooks.play].firstMatch.tap()
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
    /// Six wordings, each one a control Android draws and this port left out:
    /// the upload (`AudiobooksScreen.kt:197`), the save and its three
    /// siblings (`BookPage.kt:175-212`), `remove from server (admin)`
    /// (`:226`), the `listening as` chip (`:205`) and a room to cast to —
    /// which neither app has, because a cast sends library track ids and a
    /// book has no library id (`CastPlan.kt:7-26`).
    ///
    /// Spelt out here rather than imported, like every other string this file
    /// matches on: a query that silently followed a renamed constant would
    /// stop testing what it claims to. When one of these starts to exist —
    /// PhoneMedia's save, a listener picker — whoever adds it moves the
    /// assertion into their own file, which is the point.
    @MainActor
    func testAudiobooks_NoControlPromisesWhatThisAppCannotDo() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")

        assertNothingOffers(app, where: "the library")
        XCTAssertTrue(openBook(app, StubAudiobooks.dune), "Dune's page never drew")
        assertNothingOffers(app, where: "a book's page")
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
    @MainActor
    @available(iOS 17.0, *)
    func testAudiobooks_TheLibraryPassesApplesAudit() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")
        let found = try auditIssues(app, "audiobooks-library")
        evidence(app, "audiobooks-60-library-audit")
        let split = Self.floorAndRest(found)
        XCTAssertTrue(
            split.rest.isEmpty,
            "the library has \(split.rest.count) attributed findings:\n"
                + split.rest.joined(separator: "\n")
        )
    }

    /// A book's page with the question **up**.
    ///
    /// The dialog is audited rather than dismissed first, and that is the
    /// whole point of auditing this state: a `Contrast failed` naming an
    /// element *behind* a dialog means the dialog is not modal, which is how
    /// the connection panel's two live `forget` controls were found. A
    /// ``DialogCard`` carries `.isModal` so VoiceOver cannot reach the page's
    /// own `play` while the question is open.
    @MainActor
    @available(iOS 17.0, *)
    func testAudiobooks_ABookPagePassesApplesAudit() throws {
        let (app, landed) = openAudiobooks()
        XCTAssertTrue(landed, "the audiobooks list never answered")
        XCTAssertTrue(openBook(app, StubAudiobooks.dune), "Dune's page never drew")
        app.buttons[StubAudiobooks.play].firstMatch.tap()
        XCTAssertTrue(
            app.buttons[StubAudiobooks.startOver].waitForExistence(timeout: 25),
            "the question never came up, so the modal state was never audited"
        )

        let found = try auditIssues(app, "audiobooks-book-page")
        evidence(app, "audiobooks-61-book-page-audit")
        let split = Self.floorAndRest(found)
        XCTAssertTrue(
            split.rest.isEmpty,
            "a book's page with the question up has \(split.rest.count) "
                + "attributed findings:\n" + split.rest.joined(separator: "\n")
        )
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
        func heights(_ app: XCUIApplication) -> [String: CGFloat] {
            var out: [String: CGFloat] = [:]
            for label in StubAudiobooks.growingLeaves {
                let leaf = app.staticTexts[label].firstMatch
                if leaf.exists { out[label] = leaf.frame.height }
            }
            let row = app.buttons[StubAudiobooks.dune].firstMatch
            if row.exists { out[StubAudiobooks.dune] = row.frame.height }
            return out
        }

        let (ordinary, landedSmall) = openAudiobooks()
        XCTAssertTrue(landedSmall, "the audiobooks list never answered at the ordinary size")
        let small = heights(ordinary)
        let titleChip = ordinary.buttons[StubAudiobooks.sortTitle].firstMatch
        let newestChip = ordinary.buttons[StubAudiobooks.sortNewest].firstMatch
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

        let bigTitle = big.buttons[StubAudiobooks.sortTitle].firstMatch
        let bigNewest = big.buttons[StubAudiobooks.sortNewest].firstMatch
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
    @MainActor
    func audiobooksList(_ app: XCUIApplication) -> XCUIElement {
        app.scrollViews.firstMatch
    }

    /// Scroll until `element` is **built**, and say whether it was.
    ///
    /// A lazy list's foot is unbuilt rather than off screen, and the gesture
    /// is a **slow flick** for the two reasons
    /// ``scrollUntilBuilt(_:_:flicks:)`` measured: a plain `swipeUp()` is
    /// worth about a viewport and steps over rows, and a press-and-drag held
    /// on a text field is taken by the field rather than by the scroll view.
    @MainActor
    func audiobooksScroll(
        _ app: XCUIApplication,
        to element: XCUIElement,
        flicks: Int = 30
    ) -> Bool {
        let list = audiobooksList(app)
        guard list.waitForExistence(timeout: 20) else {
            print("=== NO AUDIOBOOKS LIST TO SCROLL — tree follows ===")
            print(app.debugDescription)
            return false
        }
        for _ in 0..<flicks {
            if element.exists { return true }
            list.swipeUp(velocity: .slow)
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
    static let zeroTime = "0:00"

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
    static let absentControls = [
        "add audiobooks to the server",
        "save to phone",
        "remove from phone",
        "remove from server",
        "listening as",
        "play in a room",
    ]

    /// The leaves whose heights the Dynamic Type case compares.
    static let growingLeaves = [
        "Audiobooks",
        "books with chapters and your place, kept on the Domovoi",
        "showing 9 of 9 books",
    ]
}
