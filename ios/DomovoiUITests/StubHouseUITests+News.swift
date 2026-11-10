import XCTest

/// News, driven against the stub house.
///
/// Thirteen cases, **bunched on purpose**: a launch costs about thirty
/// seconds, so each case below carries every assertion that can share one
/// app, and the write cases are grouped by the card they touch rather than
/// one case per button. Thirteen launches for the whole screen.
///
/// Reaching it is `launch(push: "News", …)` — ``AuditStrip``'s seeded route,
/// which is the same push a tab tap makes — and **never** the connection
/// sheet, which starts a LAN sweep across Kamron's real network.
///
/// ### What identifies an element here
///
/// Every repeated control on this screen carries Android's own
/// `contentDescription` as its accessibility label, so a person with five
/// topics hears five buttons called `remove topic`. The *identifier* is what
/// says which row — `news-remove-topic-tech`, `news-favorite-301` — and that
/// is what these cases tap. `XCUIElementQuery`'s subscript matches an
/// identifier as well as a label, so `app.buttons["news-poll"]` is the
/// button and nothing else: no `firstMatch` over a label the keyboard might
/// also be wearing.
///
/// ### The roster is another item's
///
/// `fixtures/people.py` claims `/api/people` and `fixtures/news.py` defers to
/// it (DECISIONS.md, "Two screens need /api/people"), so the four people on
/// screen are the People seed's: `Kamron`, `Chevy`, `Alex`, `alex `, in
/// `last_seen_at DESC` order. News seeds topics, stories and a briefing for
/// **1 (Kamron)** and **2 (Chevy)** only. Two consequences for the cases
/// below: the default selection is asserted as `Kamron's news` by name, and
/// `Alex` is a person with no news rather than a 404 — a change to that seed
/// is a change these cases will name.
extension StubHouseUITests {

    // MARK: - getting there

    /// Push News and wait for the one element only a person's detail can
    /// draw.
    ///
    /// The heading, not a story: the stories are the saved feed's read and
    /// the *heading* comes from the roster, so waiting on a story would make
    /// every case in this file also a test of the items read — and report a
    /// failed items read as "News never drew".
    /// - Parameter watchingTheProcess: poll `app.state` four times a second
    ///   while waiting for the heading and print the wall-clock instant it
    ///   first stops being `.runningForeground`, with the seconds since the
    ///   launch returned. **Off by default**, and passed only by
    ///   ``testNews_PassesApplesAccessibilityAudit()``: it is a diagnostic
    ///   for the one case that keeps losing its process, and a per-second
    ///   poll in all thirteen would cost the run for nothing.
    ///
    ///   The print is the whole point. The failure gives a time in the
    ///   xcodebuild log (`t = …`) and the Simulator's unified log gives wall
    ///   clock, and nothing yet lines the two up — so a clean `simlog.txt`
    ///   cannot be read as "no termination reason" rather than "the window
    ///   was wrong". It changes no assertion: the heading is still waited for
    ///   to the same 40 seconds, and a process that dies is still reported
    ///   by the wait, not by this.
    ///
    ///   **It did line them up, in train I004, and the answer cost this
    ///   diagnostic its own shape** — see the comment inside, under
    ///   `app.state` on a sleep. The wait no longer runs a *failed* predicate
    ///   once a second, because each of those costs two whole-tree walks (a
    ///   debug description and a triage snapshot) and the process died inside
    ///   the fourth walk. Note the offset the same log measured: `began` is
    ///   `launch()`'s return, which was Running Foreground **+ 2.2 s** there,
    ///   so a figure quoted against RF is not a figure in this loop's clock.
    @MainActor
    func openNews(
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        port: Int = 6601,
        delay: Double? = nil,
        watchingTheProcess: Bool = false
    ) -> (app: XCUIApplication, landed: Bool) {
        let app = launch(
            mode: mode,
            port: port,
            push: "News",
            pushDelay: delay ?? Self.pushDelay
        )
        let heading = newsElement(app, "news-person-heading")
        guard watchingTheProcess else {
            return (app, heading.waitForExistence(timeout: 40))
        }

        // **`app.state` on a sleep, and far fewer and cheaper tree queries.**
        //
        // This was `heading.waitForExistence(timeout: 1)` in a loop — up to
        // forty of them in the 40-second budget — and train I004's trace of
        // the Simulator's unified log says those queries were the exposure. A
        // *failed* one-second predicate is not cheap: XCTest captures the
        // element's debug description and then a **whole-tree accessibility
        // snapshot** for failure triage. I004's own kept log
        // (`overnight-trains/I004/ALL/testNews_PassesApplesAccessibilityAudit.log`)
        // shows two predicates producing four of those captures across
        // **3.5 seconds** — `Capturing element debug description` at t = 4.78
        // and t = 6.22, `Requesting snapshot of accessibility hierarchy` at
        // t = 4.97 and t = 8.25 — so two waits, not forty, were enough to
        // make four whole-tree passes, and the unified-log trace puts the
        // SIGSEGV 4 ms into the fourth, five elements deep, on the main
        // thread.
        //
        // **What of that is re-checkable, and what is not.** The four capture
        // timestamps and the `app.state` transition are in the kept log. The
        // signal itself, the `launchd (2, 11, 11)` line and the zero-hit
        // greps that rule out an ObjC exception, a Swift trap, SIGKILL,
        // jetsam, a watchdog and a crash report all come from the Simulator's
        // unified log, which is **not** among this case's kept attachments
        // (`att/…/` holds the hierarchy, two debug descriptions, a recording
        // and a snapshot). Attribution of the signal to a root cause is not
        // established either way — see this unit's REPORT.md. What is certain
        // from the kept log alone is that the queries are this file's, that
        // each failed one costs two whole-tree walks, and that the app was
        // gone before the first assertion ran.
        //
        // What replaces them keeps the diagnostic whole. `app.state` takes no
        // snapshot and is safe after the process has gone, so the death is
        // still timestamped to within a quarter-second — better than the
        // second this had. The tree is asked at most once a second, with a
        // plain `.exists`, and not at all for the first eight seconds.
        //
        // **The eight seconds are a saving, not a shield, and the arithmetic
        // says why.** `began` is when `launch()` returned, which is *not*
        // Running Foreground: I004's log reaches RF at about wall
        // 14:06:13.34 and prints `NEWS-WATCH launch returned` at 14:06:15.558,
        // so `began` was RF + 2.2 s in that run. Converted into this loop's
        // own clock, I004's death is `began` + 2.7 s (the trace's RF + 4.93 s)
        // and was first *seen* here at `began` + 4.73 s, while the deaths
        // recorded against RF in I001 (+10.0 s), I002 (+10.2 s) and R005
        // (+10.1 to 10.6 s) land near `began` + 8 s if their launches had a
        // similar offset — which is **inside** the window this loop queries,
        // not outside it. So no claim is made that the quiet window avoids
        // the deaths; it cannot, because the heading itself is not seen until
        // RF + 12.1 s on surviving processes (`began` + 9.9 s in I004's
        // offset) and a window wide enough to clear began + 10 s would be
        // guessing at the offset. The eight seconds buy what is measurable:
        // no survivor has ever found `news-person-heading` that early, so
        // every ask before it was a whole-tree walk that could only fail.
        //
        // A `.exists` rather than another `waitForExistence`: the same one
        // query, without the debug-description-and-hierarchy capture a failed
        // predicate adds. That is the whole of the reduction — about 32 asks
        // down to about 32 state reads plus at most 32 single walks, from a
        // worst case of 80 walks. The 40-second budget and the `landed`
        // contract are unchanged.
        let began = Date()
        let stamp = ISO8601DateFormatter()
        stamp.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        print("NEWS-WATCH launch returned at \(stamp.string(from: began))")
        let quietFor: TimeInterval = 8
        var lastAsked = Date.distantPast
        while Date().timeIntervalSince(began) < 40 {
            // **The state before the query, every time.** A query resolved
            // against a process that has gone is what raises `Failed to
            // resolve query: Application … is not running`, so asking the
            // tree first would throw on the very moment this is here to
            // timestamp.
            guard app.state == .runningForeground else {
                print(
                    "NEWS-WATCH app.state is \(app.state.rawValue) at "
                        + "\(stamp.string(from: Date())), "
                        + "+\(Date().timeIntervalSince(began))s — line this up "
                        + "against the Simulator's unified log"
                )
                // Returned, not asked again: a query against a process that
                // is gone is the `Failed to resolve query: Application … is
                // not running` this case has been dying on, and raising it
                // from a diagnostic would hide the thing the diagnostic is
                // for. The caller fails on `landed`, as it always has.
                return (app, false)
            }
            let elapsed = Date().timeIntervalSince(began)
            if elapsed >= quietFor, Date().timeIntervalSince(lastAsked) >= 1 {
                lastAsked = Date()
                if heading.exists {
                    print(
                        "NEWS-WATCH heading at \(stamp.string(from: Date())), "
                            + "+\(Date().timeIntervalSince(began))s"
                    )
                    return (app, true)
                }
            }
            usleep(250_000)
        }
        print("NEWS-WATCH no heading in 40s, and the app never left the foreground")
        return (app, false)
    }

    /// Any element with this identifier, whatever type it reports as.
    ///
    /// Across types on purpose: a story title is a `Link` when its url is a
    /// web link and a `Text` when it is not, and a case that asked
    /// `app.staticTexts[…]` would be testing which of the two SwiftUI built
    /// rather than what it says. ``testNews_AStoryWithANonWebLinkIsNotTappable``
    /// is the one case that asks about the type, and it asks for both.
    @MainActor
    func newsElement(_ app: XCUIApplication, _ identifier: String) -> XCUIElement {
        app.descendants(matching: .any).matching(identifier: identifier).firstMatch
    }

    /// Scroll the page until an element can actually be tapped.
    ///
    /// The page is a plain `VStack` in a `ScrollView` (``NewsView``), so
    /// every element **exists** from the first frame — which is the opposite
    /// of Chat's lazy list, and means `exists` says nothing about whether a
    /// tap would land. `isHittable` is the question. The swipe is on the app
    /// rather than on a scroll view, because the chip row is a `ScrollView`
    /// too and `scrollViews.firstMatch` is whichever of the two the tree
    /// holds first; a swipe from the middle of the window is inside the
    /// page.
    @MainActor
    @discardableResult
    func revealNews(_ app: XCUIApplication, _ element: XCUIElement, flicks: Int = 10) -> Bool {
        for _ in 0..<flicks {
            if element.exists, element.isHittable { return true }
            app.swipeUp(velocity: .slow)
        }
        if !(element.exists && element.isHittable) {
            print("=== NEWS: \(flicks) FLICKS AND STILL NOT HITTABLE — tree follows ===")
            print(app.debugDescription)
        }
        return element.exists && element.isHittable
    }

    /// Type into one of the screen's two fields.
    ///
    /// The tap comes first because a `TextField` needs focus, and the field
    /// is revealed first because a tap on something off screen lands on
    /// whatever is in that place instead.
    @MainActor
    func typeIntoNews(_ app: XCUIApplication, _ identifier: String, _ text: String) -> Bool {
        let field = app.textFields[identifier]
        guard revealNews(app, field) else { return false }
        field.tap()
        app.typeText(text)
        return true
    }

    /// Put the house back to its seed. Every case calls this first: the cases
    /// share one stub process for the whole run, and five of them write.
    @MainActor
    func resetNewsHouse(port: Int = 6601) {
        control("reset-fixtures", "{}", port: port, what: "the news seed")
    }

    // MARK: - 1. the screen draws what the server said

    /// Four reads land and each one's own words are on screen — and the other
    /// person's are not.
    ///
    /// Catches: the bare-array decode going to nothing (the wrapper trap that
    /// left four Home sections unaudited), the default selection picking
    /// nobody, `N feeds` read from the lazily-loaded feeds list instead of
    /// the topics read, and a saved feed that drew the wrong person's
    /// stories.
    @MainActor
    func testNews_DrawsAPersonsNewsFromTheServer() throws {
        resetNewsHouse()
        let (app, landed) = openNews()
        evidence(app, "news-01-draws")
        XCTAssertFalse(
            fellBackToLocalMedia(in: app),
            "the shell gave up on the server — this is not a test of News"
        )
        XCTAssertTrue(landed, "News never drew a person's heading")

        // The roster, as chips. Both seeded people, by identifier: the chip's
        // *label* is `Kamron's news`, which the heading also says.
        XCTAssertTrue(
            app.buttons["news-chip-Kamron"].exists,
            "no chip for Kamron — the roster read did not reach the chip row"
        )
        XCTAssertTrue(app.buttons["news-chip-Chevy"].exists, "no chip for Chevy")

        // The default selection is the roster's **first** row, which the
        // server sorts `last_seen_at DESC` — "most recently heard".
        XCTAssertEqual(
            newsElement(app, "news-person-heading").label,
            NewsStringsUnderTest.kamronsNews,
            "the first roster row was not the one picked"
        )

        // The briefing is Kamron's: `aurora` is in his and in nothing else in
        // the house.
        let briefing = newsElement(app, "news-briefing-body")
        XCTAssertTrue(
            briefing.label.contains("aurora"),
            "the briefing read did not land — the card says: \(briefing.label)"
        )

        // `2 feeds` comes from the topics read's own `feed_count`, and the
        // feeds list for `tech` has not been opened at all, so a count taken
        // from that list would read `0 feeds`.
        XCTAssertEqual(
            newsElement(app, "news-topic-count-tech").label,
            NewsStringsUnderTest.twoFeeds,
            "the topic row's count is not the server's"
        )
        XCTAssertEqual(
            newsElement(app, "news-topic-count-Formula 1").label,
            NewsStringsUnderTest.zeroFeeds,
            "the plural is not keyed on the count"
        )

        // A story of his, its summary stripped of the tags the feed sent, and
        // its meta line's last clause.
        let title = newsElement(app, "news-story-title-301")
        XCTAssertTrue(
            title.label.contains("trebuchet"),
            "the saved feed did not draw Kamron's story"
        )
        XCTAssertEqual(
            newsElement(app, "news-story-summary-301").label,
            NewsStringsUnderTest.strippedSummary,
            "the HTML tags were not taken out of the summary"
        )
        let meta = newsElement(app, "news-story-meta-301")
        XCTAssertTrue(
            meta.label.hasSuffix(NewsStringsUnderTest.unread),
            "the meta line does not end in whether it was read: \(meta.label)"
        )
        XCTAssertTrue(
            meta.label.hasPrefix("tech · Quarry Weekly"),
            "the meta line's topic and source are wrong: \(meta.label)"
        )

        // And **not** the other person's, which is the half that discriminates.
        // **`Marmalade`, with its capital.** `CONTAINS` is case-sensitive, and
        // the title the fixture ships is `Marmalade shortage reaches the
        // border`: asserted lower-case this was a sentence nothing on any
        // screen could match, so it passed here for the wrong reason and
        // failed in ``testNews_SwitchingPersonReplacesEveryCard`` (train
        // S002) where the same word had to be *present*.
        XCTAssertFalse(
            containing(app, "Marmalade").exists,
            "Chevy's story is on Kamron's screen"
        )
    }

    // MARK: - 2. switching person

    /// Tapping another chip replaces every card — **and the first person's
    /// late answer is dropped**.
    ///
    /// The read-after-await defect this track has paid for most: a reply for
    /// person A landing under person B. The switch happens **inside** a
    /// parked `items` round trip, which is the only form of this test that
    /// works: `news-slow-items` holds Kamron's saved feed open for six
    /// seconds, the chip is tapped while it is in flight, and the assertion
    /// is taken after it has had time to come back.
    @MainActor
    func testNews_SwitchingPersonReplacesEveryCard() throws {
        resetNewsHouse()
        control(
            "news-slow-items",
            #"{"seconds": 6, "person": 1}"#,
            port: 6601,
            what: "Kamron's saved feed parked for six seconds"
        )
        let (app, landed) = openNews()
        XCTAssertTrue(landed, "News never drew a person's heading")

        let chevy = app.buttons["news-chip-Chevy"]
        XCTAssertTrue(chevy.waitForExistence(timeout: 20), "no chip for Chevy")
        chevy.tap()

        XCTAssertTrue(
            newsElement(app, "news-story-title-401").waitForExistence(timeout: 30),
            "Chevy's saved feed never drew"
        )
        // Long enough for Kamron's parked read to have answered — the whole
        // point. Asserting before it could land would pass against an app
        // with no key at all.
        usleep(9_000_000)
        evidence(app, "news-02-switched")

        XCTAssertEqual(
            newsElement(app, "news-person-heading").label,
            NewsStringsUnderTest.chevysNews,
            "the heading did not follow the chip"
        )
        XCTAssertTrue(
            containing(app, "Marmalade").exists,
            "Chevy's own story is not on screen"
        )
        XCTAssertFalse(
            containing(app, "trebuchet").exists,
            "Kamron's late saved-feed answer landed under Chevy — the reply "
                + "was published against the current selection rather than "
                + "the person it was asked for"
        )
        // Chevy has no briefing row at all, which is a successful read of two
        // nulls (`news.py:436-437`) and not a failure.
        XCTAssertEqual(
            newsElement(app, "news-briefing-body").label,
            NewsStringsUnderTest.noBriefing,
            "the no-briefing sentence is not what the card says"
        )
    }

    // MARK: - 3. adding a topic

    /// A free-form topic is posted, the field clears, the row appears with
    /// the right kind, and its feeds load the first time it is opened.
    @MainActor
    func testNews_AddsAFreeFormTopicAndShowsItsFeeds() throws {
        resetNewsHouse()
        let (app, landed) = openNews()
        XCTAssertTrue(landed, "News never drew a person's heading")

        XCTAssertTrue(
            typeIntoNews(app, "news-freeform", "Trebuchets"),
            "the free-form topic field could not be reached"
        )
        let add = app.buttons["news-add-topic"]
        XCTAssertTrue(revealNews(app, add), "the add-topic button is not tappable")
        add.tap()

        let word = newsElement(app, "news-topic-word-Trebuchets")
        XCTAssertTrue(
            word.waitForExistence(timeout: 25),
            "the topics list never showed the new topic — either the write "
                + "did not go out or nothing re-read afterwards"
        )
        evidence(app, "news-03-added")

        // The kind the app posted, read back off the row's pill: a write that
        // sent `category` would have the server refuse the word outright, and
        // one that sent nothing would be a 400.
        // Read off *this* row's pill by identifier. `containing(...)` matched
        // any label holding `freeform`, and the seeded `Formula 1` row's pill
        // already said it before the write — so that assertion passed
        // whatever kind the new row carried, and passed with no pill at all.
        XCTAssertEqual(
            newsElement(app, "news-topic-kind-Trebuchets").label,
            NewsStringsUnderTest.freeformKind,
            "the new row's pill does not say what kind of topic was posted"
        )
        // The field is cleared on success only (`NewsDetail.kt:225`).
        XCTAssertEqual(
            app.textFields["news-freeform"].value as? String ?? "",
            // The **visible** hint: an empty `TextField` reports its
            // placeholder as its value, and the placeholder is the short one.
            NewsStringsUnderTest.freeformPlaceholderShort,
            "the field kept what was typed after a successful add"
        )

        // **Put the keyboard away before going down the page.** The new row
        // is at the foot, behind the keyboard, and while the field holds
        // focus the page cannot be scrolled off it — train S002 flicked ten
        // times and the content offset did not move a pixel.
        // ``NewsView``'s `.scrollDismissesKeyboard(.immediately)` is the fix
        // in the app; this is the same thing done deliberately, and it is
        // safe here because the assertion above has just established that the
        // draft is empty, so the `done` key's `onSubmit` is a no-op
        // (`NewsPersonModel.addFreeform` returns on a blank topic).
        let done = app.keyboards.buttons["Done"]
        if done.exists, done.isHittable { done.tap() }

        // First open reads the feeds. Discovery attached one, so an open that
        // never read would say "discovery found none".
        let chevron = app.buttons["news-expand-Trebuchets"]
        XCTAssertTrue(revealNews(app, chevron), "the new row's chevron is not tappable")
        chevron.tap()
        XCTAssertTrue(
            containing(app, "Trebuchets digest").waitForExistence(timeout: 25),
            "expanding the row did not read its feeds"
        )
        evidence(app, "news-03-expanded")
    }

    // MARK: - 4. removing a topic

    /// The ✕ asks first, the question **names the topic**, cancelling keeps
    /// the row, and confirming removes it.
    @MainActor
    func testNews_RemovingATopicAsksFirst() throws {
        resetNewsHouse()
        let (app, landed) = openNews()
        XCTAssertTrue(landed, "News never drew a person's heading")

        let remove = app.buttons["news-remove-topic-Formula 1"]
        XCTAssertTrue(revealNews(app, remove), "no remove control on the Formula 1 row")
        remove.tap()

        let ask = newsElement(app, "news-remove-ask")
        XCTAssertTrue(ask.waitForExistence(timeout: 10), "the ✕ removed the topic with no question")
        evidence(app, "news-04-asked")
        // The body names the topic, so a dialog that confirmed the wrong row
        // is visible in the dialog itself.
        XCTAssertEqual(
            ask.label,
            NewsStringsUnderTest.removeFormulaOne,
            "the question does not name the topic it would remove"
        )

        app.buttons["news-remove-cancel"].tap()
        XCTAssertTrue(
            newsElement(app, "news-topic-word-Formula 1").waitForExistence(timeout: 10),
            "cancelling the dialog removed the topic anyway"
        )

        XCTAssertTrue(revealNews(app, remove), "the remove control went away after a cancel")
        remove.tap()
        XCTAssertTrue(
            app.buttons["news-remove-confirm"].waitForExistence(timeout: 10),
            "the dialog did not come back"
        )
        app.buttons["news-remove-confirm"].tap()

        XCTAssertTrue(
            newsElement(app, "news-topic-word-Formula 1").waitForNonExistence(timeout: 25),
            "confirming did not remove the topic"
        )
        // The other topic is untouched, which is how "it deleted that id" is
        // told from "it deleted something".
        XCTAssertTrue(
            newsElement(app, "news-topic-word-tech").exists,
            "removing Formula 1 took tech with it"
        )
        evidence(app, "news-04-removed")
    }

    // MARK: - 5. the category chips

    /// A chip is a toggle: on posts a category topic, on again deletes **that
    /// one**.
    @MainActor
    func testNews_ACategoryChipTogglesBothWays() throws {
        resetNewsHouse()
        let (app, landed) = openNews()
        XCTAssertTrue(landed, "News never drew a person's heading")

        let science = app.buttons["news-category-science"]
        XCTAssertTrue(revealNews(app, science), "no science chip on the card")
        science.tap()
        XCTAssertTrue(
            newsElement(app, "news-topic-word-science").waitForExistence(timeout: 25),
            "tapping an inactive chip did not add the topic"
        )
        evidence(app, "news-05-on")

        XCTAssertTrue(revealNews(app, science), "the science chip went away")
        science.tap()
        XCTAssertTrue(
            newsElement(app, "news-topic-word-science").waitForNonExistence(timeout: 25),
            "tapping an active chip did not remove its topic — the toggle "
                + "posted again instead of deleting"
        )
        // `tech` is the discriminator: a delete that took the wrong id, or a
        // write that cleared the list, shows up here and nowhere else.
        XCTAssertTrue(
            newsElement(app, "news-topic-word-tech").exists,
            "turning science off deleted tech"
        )
        evidence(app, "news-05-off")
    }

    // MARK: - 6. favouriting

    /// The star pins a story to the top of the list and changes what it says
    /// it will do.
    ///
    /// The order is the **server's** (`favorited DESC, published_at DESC`),
    /// so this is also a test that the saved feed is re-read after the write
    /// rather than re-sorted on the phone.
    @MainActor
    func testNews_FavoritingAStoryPinsItToTheTop() throws {
        resetNewsHouse()
        let (app, landed) = openNews()
        XCTAssertTrue(landed, "News never drew a person's heading")

        let trebuchet = newsElement(app, "news-story-title-301")
        let fencepost = newsElement(app, "news-story-title-302")
        XCTAssertTrue(trebuchet.waitForExistence(timeout: 25), "the saved feed never drew")
        XCTAssertTrue(fencepost.exists, "the second story never drew")
        // 302 is the newer of the two, so it starts above 301.
        XCTAssertLessThan(
            fencepost.frame.minY,
            trebuchet.frame.minY,
            "the saved feed is not in the server's order to begin with"
        )

        let star = app.buttons["news-favorite-301"]
        XCTAssertEqual(
            star.label,
            NewsStringsUnderTest.favorite,
            "the star does not say what it would do"
        )
        XCTAssertTrue(revealNews(app, star), "the story's star is not tappable")
        star.tap()

        // The list is re-read, so wait for the label to flip rather than for
        // a fixed time.
        let flipped = app.buttons["news-favorite-301"]
        var waited = 0
        while waited < 250, flipped.label != NewsStringsUnderTest.unfavorite {
            usleep(100_000)
            waited += 1
        }
        evidence(app, "news-06-favorited")
        XCTAssertEqual(
            flipped.label,
            NewsStringsUnderTest.unfavorite,
            "the star still offers to favourite a story it has favourited — "
                + "the write sent `favorited: true` unconditionally, or the "
                + "list never re-read"
        )
        XCTAssertLessThan(
            newsElement(app, "news-story-title-301").frame.minY,
            newsElement(app, "news-story-title-302").frame.minY,
            "the favourited story was not pinned above the newer one"
        )
    }

    // MARK: - 7. the feeds of one topic

    /// A feed is added, a failing one is re-checked, and one is removed — and
    /// the row's count follows the server.
    ///
    /// The three writes are one case because they share a launch and a card.
    /// Each has its own observable effect: the new row's title, the state
    /// beside the feed going from `not valid` to `valid`, and the count
    /// dropping back to `2 feeds`.
    @MainActor
    func testNews_AFeedIsAddedRecheckedAndRemoved() throws {
        resetNewsHouse()
        let (app, landed) = openNews()
        XCTAssertTrue(landed, "News never drew a person's heading")

        let chevron = app.buttons["news-expand-tech"]
        XCTAssertTrue(revealNews(app, chevron), "no chevron on the tech row")
        XCTAssertEqual(
            chevron.label,
            NewsStringsUnderTest.expand,
            "a collapsed row's chevron says the wrong thing"
        )
        chevron.tap()
        XCTAssertTrue(
            newsElement(app, "news-feed-title-101").waitForExistence(timeout: 25),
            "expanding tech did not read its feeds"
        )
        // The fallback title: feed 102 has no title and no source, so the url
        // is what it must show (`NewsDetail.kt:480`).
        XCTAssertEqual(
            newsElement(app, "news-feed-title-102").label,
            NewsStringsUnderTest.bustedFeedURL,
            "the title fallback did not fall all the way to the url"
        )
        evidence(app, "news-07-feeds")

        // ---- added
        XCTAssertTrue(
            typeIntoNews(app, "news-feed-url-11", "https://example.com/rss.xml"),
            "the add-feed field could not be reached"
        )
        let addFeed = app.buttons["news-add-feed-11"]
        XCTAssertTrue(revealNews(app, addFeed), "the add-feed button is not tappable")
        addFeed.tap()
        XCTAssertTrue(
            containing(app, NewsStringsUnderTest.feedAdded).waitForExistence(timeout: 20),
            "nothing said the feed was added"
        )
        XCTAssertTrue(
            newsElement(app, "news-topic-count-tech").waitForExistence(timeout: 20),
            "the topic row went away"
        )
        var count = newsElement(app, "news-topic-count-tech")
        var waited = 0
        while waited < 200, count.label != NewsStringsUnderTest.threeFeeds {
            usleep(100_000)
            waited += 1
            count = newsElement(app, "news-topic-count-tech")
        }
        XCTAssertEqual(
            count.label,
            NewsStringsUnderTest.threeFeeds,
            "the count did not follow the server after a feed was added"
        )

        // ---- re-checked
        let state102 = newsElement(app, "news-feed-state-102")
        XCTAssertEqual(
            state102.label,
            NewsStringsUnderTest.feedInvalid,
            "the invalid feed does not say so"
        )
        let recheck = app.buttons["news-validate-102"]
        XCTAssertTrue(revealNews(app, recheck), "no re-check control on the invalid feed")
        recheck.tap()
        waited = 0
        while waited < 200, newsElement(app, "news-feed-state-102").label != NewsStringsUnderTest.feedValid {
            usleep(100_000)
            waited += 1
        }
        evidence(app, "news-07-rechecked")
        XCTAssertEqual(
            newsElement(app, "news-feed-state-102").label,
            NewsStringsUnderTest.feedValid,
            "re-checking the feed did not reload the list it belongs to"
        )

        // ---- removed
        let removeFeed = app.buttons["news-remove-feed-102"]
        XCTAssertTrue(revealNews(app, removeFeed), "no remove control on the feed row")
        removeFeed.tap()
        XCTAssertTrue(
            newsElement(app, "news-feed-title-102").waitForNonExistence(timeout: 25),
            "removing the feed left its row on screen"
        )
        waited = 0
        while waited < 200, newsElement(app, "news-topic-count-tech").label != NewsStringsUnderTest.twoFeeds {
            usleep(100_000)
            waited += 1
        }
        XCTAssertEqual(
            newsElement(app, "news-topic-count-tech").label,
            NewsStringsUnderTest.twoFeeds,
            "the count did not drop after a feed was removed"
        )
        // The topic itself is still there: a remove that called the
        // topic-delete route would have taken the whole row.
        XCTAssertTrue(
            newsElement(app, "news-topic-word-tech").exists,
            "removing a feed removed its topic"
        )
        evidence(app, "news-07-removed")
    }

    // MARK: - 8. the link rule

    /// A story's title is tappable only when its url is a web link.
    ///
    /// `javascript:alert(1)` is **plain text** — a `Text`, not a `Link` with
    /// nothing behind it. The https story beside it is the control: a port
    /// with no scheme check at all would make both tappable, and one that
    /// refused everything would make neither.
    @MainActor
    func testNews_AStoryWithANonWebLinkIsNotTappable() throws {
        resetNewsHouse()
        let (app, landed) = openNews()
        XCTAssertTrue(landed, "News never drew a person's heading")
        XCTAssertTrue(
            newsElement(app, "news-story-title-302").waitForExistence(timeout: 25),
            "the saved feed never drew"
        )
        evidence(app, "news-08-links")

        // The `javascript:` one. Its identifier is on a `Text`, so it is a
        // static text and neither a link nor a button.
        XCTAssertTrue(
            app.staticTexts["news-story-title-302"].exists,
            "the fencepost story's title is not plain text"
        )
        XCTAssertFalse(
            app.links["news-story-title-302"].exists,
            "a `javascript:` url was drawn as a link"
        )
        XCTAssertFalse(
            app.buttons["news-story-title-302"].exists,
            "a `javascript:` url was drawn as a button"
        )

        // The `https:` one, which must be one or the other of those.
        let link = app.links["news-story-title-301"]
        let asButton = app.buttons["news-story-title-301"]
        print("NEWS link=\(link.exists) button=\(asButton.exists)")
        XCTAssertTrue(
            link.exists || asButton.exists,
            "the https story's title is not tappable — the scheme check "
                + "refused a url it should have accepted"
        )
        XCTAssertFalse(
            app.staticTexts["news-story-title-301"].exists,
            "the https story's title is plain text"
        )
    }

    // MARK: - 9. poll now

    /// `poll now` says what came back — and, when the house has no internet,
    /// says the server's own sentence rather than "failed".
    @MainActor
    func testNews_PollNowSaysWhatCameBack() throws {
        resetNewsHouse()
        let (app, landed) = openNews()
        XCTAssertTrue(landed, "News never drew a person's heading")

        let poll = app.buttons["news-poll"]
        XCTAssertTrue(revealNews(app, poll), "no poll control on the screen")
        XCTAssertEqual(poll.label, NewsStringsUnderTest.pollNow, "the poll button says the wrong thing")
        poll.tap()
        // `house: 1, topics: 2` — so the toast is the **sum**, and a toast
        // that said either number alone, or a fixed sentence, fails here.
        XCTAssertTrue(
            containing(app, NewsStringsUnderTest.polledThree).waitForExistence(timeout: 25),
            "the poll's toast did not say what came back"
        )
        evidence(app, "news-09-polled")

        control(
            "news-internet-off",
            #"{"off": true}"#,
            port: 6601,
            what: "the house's internet turned off"
        )
        XCTAssertTrue(revealNews(app, poll), "the poll control went away")
        poll.tap()
        XCTAssertTrue(
            containing(app, NewsStringsUnderTest.internetOff).waitForExistence(timeout: 25),
            "a 409 was reported without the server's own sentence in it — "
                + "the person who turned the internet off is the person able "
                + "to act on being told so"
        )
        evidence(app, "news-09-refused")
        // Put it back, because the stub outlives this case.
        resetNewsHouse()
    }

    // MARK: - 10. an empty roster

    /// A house with nobody enrolled says so, and draws no person.
    @MainActor
    func testNews_SaysWhenNobodyIsEnrolled() throws {
        resetNewsHouse()
        control(
            "news-no-people",
            #"{"empty": true}"#,
            port: 6601,
            what: "an empty roster"
        )
        let app = launch(push: "News", pushDelay: Self.pushDelay)
        let said = app.staticTexts[NewsStringsUnderTest.noPeople]
        let drew = said.waitForExistence(timeout: 40)
        evidence(app, "news-10-nobody")
        XCTAssertTrue(drew, "an empty roster drew nothing at all")
        XCTAssertTrue(
            app.staticTexts[NewsStringsUnderTest.noPeopleSub].exists,
            "nothing said where a speaker is enrolled"
        )
        XCTAssertFalse(
            newsElement(app, "news-person-heading").exists,
            "a house with nobody in it drew somebody's news"
        )
        resetNewsHouse()
    }

    // MARK: - 11. a roster that failed

    /// A roster read that failed says **why**, with a retry — and does not
    /// say that nobody is enrolled.
    ///
    /// ### Why a 503 and not the 401 the map asked for
    ///
    /// `/api/people` is `require_device_read`, so the refusal a stale phone
    /// really gets is a 401 — and a 401 never reaches this screen's failure
    /// branch: `DomovoiClient.raw` flips the client's pairing flag on any
    /// device-token refusal and the **shell** raises its own token-trouble
    /// screen over the whole app (measured at `StubHouseUITests.swift`'s
    /// `token-not-recognised` state, which is a different screen entirely).
    /// So the branch is driven with a 503, which is the same branch with a
    /// reason the shell does not own. DECISIONS.md, "A 401 on the roster is
    /// the shell's, not this screen's".
    @MainActor
    func testNews_SaysWhyWhenTheRosterIsRefused() throws {
        resetNewsHouse()
        control(
            "news-roster-status",
            #"{"status": 503}"#,
            port: 6601,
            what: "a roster read that fails"
        )
        let app = launch(push: "News", pushDelay: Self.pushDelay)
        let why = containing(app, NewsStringsUnderTest.rosterFailureDetail)
        let said = why.waitForExistence(timeout: 40)
        evidence(app, "news-11-refused")
        XCTAssertTrue(
            said,
            "the roster's failure is not on screen — the server's own sentence "
                + "is what a person can act on"
        )
        XCTAssertTrue(
            app.buttons["news-roster-retry"].exists,
            "a failed roster read offers no way to ask again"
        )
        XCTAssertFalse(
            app.staticTexts[NewsStringsUnderTest.noPeople].exists,
            "a failed roster read was drawn as a house with nobody in it"
        )
        resetNewsHouse()
    }

    // MARK: - 12. Apple's audit

    /// Three audits of this screen: the page as it lands, the page with the
    /// remove dialog up, and the foot of the page — a third of which is below
    /// the fold when it lands. **In two app processes** — the page and its
    /// foot in one, the dialog in the other — because a process that has
    /// walked these passes does not reliably survive, and neither does a
    /// third launch (the body says what S002, S005, S011 and R004 each lost,
    /// and what R004's own recording and its sibling unit say about whose
    /// fault the last one was).
    ///
    /// **The first pass is the screen's, and it has to come first.** This
    /// case used to raise the dialog *before* its only audit, and
    /// ``NewsView`` applies `.accessibilityHidden(removalAsked != nil)` to
    /// the whole page — which does not de-prioritise the page, it deletes it
    /// from the hierarchy `performAccessibilityAudit` walks. The audit saw
    /// the dialog card and the shell's phone bar and nothing else: not one
    /// chip, topic row, category chip, feed row, story, star, chevron, poll
    /// button or briefing. It passed against a screen it never looked at, and
    /// would have passed against a sabotaged colour.
    ///
    /// The dialog pass is still worth running: it covers the dialog's own
    /// card, and a finding naming an element *behind* the confirmation would
    /// mean the page is no longer hidden — the gap that cost this app two
    /// `Contrast failed` on Settings.
    ///
    /// The foot pass is what makes ``newsOutsideTheViewport(_:_:)`` honest. The
    /// audit reports contrast against content the scroll view has clipped
    /// away, where there are no pixels to read; discounting those findings
    /// and stopping there would leave two thirds of this page audited by
    /// nobody. Scrolled to the foot, the same rows are on screen and are read
    /// properly.
    ///
    /// Everything the audit can attribute is a defect to fix; anonymous
    /// Dynamic Type findings are the measured platform floor
    /// (``floorAndRest(_:)``), and the floor is printed rather than pinned —
    /// a total would hide a new finding the moment one of the floor's went
    /// away.
    ///
    /// The one anonymous finding that is *not* floor and not a defect is a
    /// `Contrast failed` for a leaf an edge has halved, and it is allowed
    /// one-for-one against ``newsCutLeaves(in:)``'s count for that pass — a
    /// number this case measures from the app and prints, never a total it
    /// carries. R003: one cut leaf and one such finding on the page, three
    /// and three at the foot, none and none behind the dialog.
    @MainActor
    func testNews_PassesApplesAccessibilityAudit() throws {
        resetNewsHouse()

        // **Two app processes: the page and its foot in one, the dialog in
        // the other.**
        //
        // Train S011 lost this case in the third pass: the foot audit ran,
        // and partway through reading its findings' elements the app was
        // simply gone — `Failed to resolve query: Application … is not
        // running` at t = 48.6s, with no crash report behind it and the
        // run's own screenshot showing a perfectly ordinary page a second
        // earlier. Nothing in this screen's code was in the frame; what had
        // happened to that process is that it had just walked two full
        // `performAccessibilityAudit` passes over a 1111-point page.
        //
        // S002 and S005 saw the nearer end of the same thing. There, the two
        // passes finished and it was the *next* case that died, eight
        // seconds into its launch beside the leftover — which is the only
        // reason this case terminates its app at all. A third pass moved the
        // death inside the case.
        //
        // So each pass got its own launch, ran one audit, and was put down
        // before the next one started. Nothing is audited less: the page as
        // it lands, the confirmation, and the foot are all still walked.
        //
        // **Train I001 lost it again, and not to an audit.** `Lost connection
        // to the application (pid 71471)` at t = 13.47s, inside the *first*
        // `openNews` — before a tap, a swipe or a single
        // `performAccessibilityAudit`. The run's screen recording is what
        // settles what was in the frame: at t = 4, 6, 9, 12 and 13.4 seconds
        // the app is on **Home**, with an Alerts local notification ("Signs
        // of tampering", `AlertsStrings.swift:77`) banner over it until
        // t = 12.0. The seeded push is `runningForeground + pushDelay`,
        // which this launch put at t ≈ 15.4 — so the process died about two
        // seconds *before* News was ever built, and no line of this screen's
        // code had run. The twelve sibling cases in the same run reached
        // `news-person-heading` through this very helper, case 204 of 372 in
        // a two-hour Simulator.
        //
        // **And it is not "the crowd death of a two-hour Simulator", which
        // is what this comment used to say.** Corrected 2026-11-04 against
        // the I002 diagnosis, which measured three more runs:
        //
        // * **R005 was a 13-case, News-only run** and this was still the
        //   only case that died. In the 372-case trains I001 and I002 it was
        //   also the only one. What stays constant is *this case*, not where
        //   it sits in a run or how old the Simulator is — so a crowd is not
        //   what is killing it.
        // * **The hand-off from `testNews_Favoriting…` is not it either.**
        //   R005's process 1 was launched straight after Favoriting's and it
        //   survived, reaching the heading and running the audits. The
        //   process that died was the one after *this case's own*
        //   `app.terminate()`.
        // * **The clock is tight and the cause is not the audit.** Measured
        //   from Running Foreground to the lost connection: I001 10.0s,
        //   I002 10.2s, R005 process 2 10.1-10.6s — while every process that
        //   survives finds `news-person-heading` at RF + 12.1s. The deaths
        //   come 1.5 to 2 seconds *before* the seeded push, so no News code
        //   has run. R004's process 3 is the exception at RF + 12.6s, after
        //   News had drawn, so it is not one fixed clock. **I004 moved it
        //   again: RF + 4.93s**, seven seconds before the push, so "RF + 10"
        //   is not a constant and nothing should be read off it.
        // * **"External SIGKILL" is not established.** An uncaught ObjC
        //   exception kills the app at once and writes no crash report
        //   either — the `ordinaryTypeSize` note in `StubHouseUITests.swift`
        //   documents one this rig has already had. The video and the
        //   missing crash report fit that as well as a kill from outside,
        //   which means **the cause may be in the app**, on Home's path.
        //
        // **Train I004 read the Simulator's unified log, which is what the
        // `NEWS-WATCH` print was added for, and the signal is settled there.**
        // It is `SIGSEGV` — signal 11, `termination reported by launchd
        // (2, 11, 11)` — on the **main thread**, four milliseconds into the
        // fourth whole-tree XCTest accessibility query of the process, five
        // elements deep. Every competing hypothesis in the paragraphs above is
        // ruled out in that log by a zero-hit grep over all 31 million lines:
        // no `Terminating app due to uncaught exception` (that is SIGABRT), no
        // `Fatal error` or `EXC_BREAKPOINT` (a Swift trap), no SIGKILL, no
        // jetsam, no watchdog, no RunningBoard termination with a reason, and
        // `ReportCrash` never ran — which is why no crash report was ever
        // found.
        //
        // **That log is not in the kept evidence**, so none of the previous
        // paragraph can be re-checked from a train artefact: `att/` for this
        // case holds the hierarchy, two debug descriptions, a recording and a
        // snapshot, and nothing else. Capturing the unified log is
        // `design-notes/ios/stubhouse/stub_run.sh`'s, outside this unit's
        // files; a reader who needs the signal confirmed has to capture it
        // again. What the **kept** log does carry, and what the paragraphs
        // below rest on, is this: `NEWS-WATCH app.state is 1` at `began`
        // + 4.73 s, before any assertion of this case ran, and four whole-tree
        // captures from two failed one-second predicates (t = 4.78, 4.97,
        // 6.22, 8.25). The error this case reports (`app.screenshot()` inside
        // ``evidence(_:_:)``) is downstream of a process that was already
        // gone.
        //
        // What is **not** settled is why a pure-Swift app segfaults inside
        // Apple's own in-process query. `ios/Domovoi` holds exactly one unsafe
        // construct (`SystemSettings/SystemSettingsDiagnosticsReader.swift`'s
        // sysctl `withUnsafeBytes`, on no path this case touches): no
        // `Unmanaged`, no `unsafeBitCast`, no `assumingMemoryBound`, no
        // `unowned`. So there is no app frame to name and nothing here is
        // changed for one. Two things were done instead, and both are
        // reductions in exposure rather than fixes:
        //
        //  * **this file's own per-second failed predicate is gone** — see
        //    ``openNews(_:port:delay:watchingTheProcess:)``. Two of those
        //    predicates made four whole-tree passes across 3.5 seconds
        //    (t = 4.78 to 8.25), the fourth of which is the one the trace says
        //    died, and the loop was written to make up to forty of them;
        //  * **the shell's `needs attention` rows and room cards each build
        //    two nested elements with the same label**, which is about 30% of
        //    the tree that walk was traversing, and is a VoiceOver defect in
        //    its own right. `ios/Domovoi/Home/` is in no unit's `owns` list
        //    this batch, so it is routed in this unit's REPORT.md under
        //    `for_others` with the file and line numbers, not edited here.
        //    (Since then: the attention rows were relabelled on the Button in
        //    94283d81, and the I005 tree showed the room cards were one
        //    element already — the doubled half was the attention rows. Both
        //    are pinned by `testHome_EachRowIsOneElement`.)
        // Nothing here is changed for it, because there is nothing honest to
        // change yet: no assertion of this case has run when it dies. What
        // was added is a **measurement** — `watchingTheProcess: true` on the
        // first `openNews` below, which prints the wall-clock instant the
        // process stopped being `.runningForeground` so the death can be
        // lined up against the Simulator's unified log. Capturing that log is
        // `design-notes/ios/stubhouse/stub_run.sh`'s, outside this repo and
        // outside this unit's files; the predicate to use, and the
        // `Terminating app due to uncaught exception` clause that separates
        // an in-app exception from a RunningBoard or jetsam termination, are
        // in the I002 diagnosis and in this unit's REPORT.
        //
        // **R004 lost the third launch, and the run says it was not this
        // case's doing.** The page pass classified (`floor=0 rest=0 leaves an
        // edge cuts in half=1`) and the dialog pass classified (`floor=8
        // rest=0 the hidden page's text=7`); the third process came up,
        // reached `news-person-heading` at t = 67.92s, and was gone by
        // t = 68.5s — `Lost connection to the application (pid 89100)` on the
        // `news-story-meta-301` wait, with no crash report. The run's own
        // screen recording is what settles what was in the frame: frames at
        // t = 67.75, 68.0 and 68.25 are the News page drawn whole and still —
        // chip rail, briefing, category grid, phone bar — no dialog, no
        // banner, no alert, and at t = 68.5 the Simulator is on Springboard.
        // The page the audit was about to read was fine.
        //
        // And it is not this case's: **the same train lost
        // `testPhoneMedia_AProgressRingShowsWhileAnUploadRuns` to the
        // identical signature after a *single* launch** (`Lost connection …
        // (pid 91621)`, R004/SourceSwitch), on another screen in another
        // unit, plus `testTheFilesListTextGrowsAtAX5` beside it. Three cases,
        // two units, one train. No count of launches in this file explains a
        // one-launch case dying the same way.
        //
        // So this is not fixed here; the exposure is reduced. This was R004's
        // longest case by far at 89.7s — the two casualties beside it ran
        // 42.7s and 54.4s — and a third of that was the third launch and its
        // twelve-second push. The page and the foot now share one process:
        // the same three audits run, with the same classifiers and the same
        // per-pass measured counts, in two launches instead of three. Two
        // audits in one process is the shape S002 and S005 ran through; the
        // three S011 died on is not back, and the terminate that S002's
        // leftover bought is still here.
        var findings: [String] = []
        var mishaps: [String] = []
        var pins: [String] = []

        // **Nothing below here asserts until every app has been terminated.**
        // `setUp` sets `continueAfterFailure = false`, so the first failed
        // assertion leaves this case where it stands; a terminate on a path
        // an assertion can cut short is a terminate that does not happen.
        // Findings and mishaps are collected, and the case speaks at the end.

        // ### 1. The page, as it lands.
        do {
            // `watchingTheProcess` only here: this is the launch the process
            // keeps dying in, and the print is what lets the instant be
            // lined up against the Simulator's unified log (the I002 note
            // above).
            let (app, landed) = openNews(watchingTheProcess: true)
            defer { app.terminate() }
            if !landed { mishaps.append("News never drew a person's heading") }

            // **The page has to have settled before the first audit, or the
            // findings arrive with nothing on them.**
            //
            // Train S008's page pass reported six `Contrast failed` and
            // `issue.element` was `nil` on every one — not one `Find the …`
            // in the log, so the audit itself had nothing to attribute them
            // to. They are the same six S005 named: the five chip leaves the
            // app no longer puts in the tree, and the clipped
            // `news-topic-word-tech` ``newsOutsideTheViewport(_:_:)`` does.
            // Unattributed they are six unknowns, and every classifier in
            // this file reads the element.
            //
            // The difference between the two runs looked like three seconds:
            // S005's page pass ran at t = 18.5s and resolved every element,
            // S008's at t = 15.4s and resolved none. The dialog and foot
            // passes get that settling for free from their own taps and
            // swipes, and both of them did attribute findings in the same
            // run. So the page pass is given the same thing on purpose — the
            // foot of a 1111-point page existing, the category grid built,
            // and a beat after them.
            //
            // **The timing explanation is spent.** With the waits below, R002
            // audited this page at t = 20.4s and still attributed none of the
            // six. The waits stay — auditing a half-built page is its own
            // defect and the dialog and foot passes have no equivalent — but
            // what makes a finding anonymous here is not how late it runs.
            //
            // **R003 settled what the sixth was, and it is the chip the rail
            // cuts.** With the leaves gone this pass reported exactly one
            // `Contrast failed`, anonymous, and the run's own tree shows the
            // audit exactly one half-drawn leaf: `news-chip-alex ` at
            // x = 345.7–437.0 in a chip rail that ends at x = 377. S005 named
            // that very cut back when the avatar bubble was still an element
            // (`label='A' frame=(357.7, 225.3, 28, 28)`, the initial the
            // scroller halves); round 3 took the bubble out of the tree, so
            // the same cut now arrives with nothing to name. The run's
            // screenshot is what attributes it — the fourth chip is drawn as
            // a circle and no word. ``newsCutLeaves(in:)`` counts it, one
            // anonymous contrast finding is allowed per cut leaf, and a
            // second one still fails this case.
            _ = newsElement(app, "news-story-meta-301").waitForExistence(timeout: 25)
            _ = app.buttons["news-category-entertainment"].waitForExistence(timeout: 10)
            let settled = XCTestExpectation(description: "the page settled before the audit")
            _ = XCTWaiter.wait(for: [settled], timeout: 3)

            pins += newsChipRowIsOneElement(app)
            // **The picture is taken after the audit, not before the waits.**
            //
            // It used to run the moment `openNews` returned, about ten
            // seconds and two waits before the audit sampled anything, and
            // train I005 is what that cost: the page pass reported one
            // `Contrast failed` with no element and no leaf measured cut
            // (`floor=0 rest=1`, no `CUT ::` line — the four person chips end
            // at x = 293 inside a 377-point rail, so R004's explanation does
            // not apply), and the only picture beside it is of a different
            // moment (`t = 15.31 s`, against an audit that sampled between
            // `t ≈ 23.6 s` and `t = 28.3 s`). A finding that will not
            // attribute is read off the picture ([[audit-reports-one-issue]]),
            // so the picture has to be of the state that was audited. Same
            // one capture, moved.
            //
            // **And taken on the way out as well**, because a throwing audit
            // is this case's likeliest ending: `performAccessibilityAudit`
            // throws when the app goes away mid-query, which is exactly the
            // pass whose picture is wanted. ``evidence(_:_:)`` has its own
            // app-is-gone guard and does not throw, so the `catch` costs
            // nothing when there is nothing left to photograph.
            //
            // **It is a trade, not a strict improvement.** If the app dies in
            // the two waits above rather than inside the audit, `auditNews`
            // does not throw — it reports `UNMEASURED` — so no `catch` fires
            // and the capture below writes evidence's app-is-gone note where
            // the old position would have had a picture of the landing page.
            // A picture of the landing page is not a picture of the audited
            // state, so this is the right way round, but that is the case it
            // loses.
            //
            // The foot and dialog passes keep their capture *before* their
            // audit: measured in I005, `news-12-audited-foot` lands at
            // `t = 41.43 s` and that pass's first measurement at
            // `t = 41.61 s`, with nothing between them but the call — 0.2 s,
            // not 8–13 s and two waits. Nothing to move.
            do {
                findings += try auditNews(app, "news", "the page")
            } catch {
                evidence(app, "news-12-audited")
                throw error
            }
            evidence(app, "news-12-audited")

            // ### 1b. The foot of the page — walked by this same process.
            //
            // R004's lost third launch is why this is no longer a launch of
            // its own; the measurement that settles whose fault that was is
            // in the comment at the top of this case. Nothing about the pass
            // itself changes: the page audit above has run, nothing has been
            // tapped, and every number this pass stands on — the viewport
            // (``newsPageViewport(in:)``) and the leaves the edges cut
            // (``newsCutLeaves(in:)``) — is measured again inside
            // ``auditNews(_:_:_:behindADialog:allowingLeavesCutByAnEdge:)``
            // after the swipes below, so it reads this scroll position and
            // not the one the page pass read.
            // **The foot of the page, audited on screen.** The page pass
            // covers what the viewport holds, and this screen is 1111 points
            // of content in a 677-point scroll view: every topic row, every
            // feed row and every story was below the fold and *not* audited.
            // ``newsOutsideTheViewport(_:_:)`` discounts the contrast findings the
            // audit reports against that clipped-away content, so without
            // this second pass those elements would be discounted and never
            // looked at — which is the half of the first fix that would have
            // been asserting less. Scrolled down, they are ordinary visible
            // elements and the audit reads them properly.
            //
            // **The audit moves this page, so its verdicts on ten of this
            // screen's elements are measured, not believed.** R001 named
            // `Contrast failed` against all nine `news-category-…` chips and
            // the `saved feed` heading; R002 and I014 named none; I018 named
            // the same ten again. R002's clean run was credited to a ring
            // colour change, but I018 carries that ring, so the credit was
            // wrong. What I018 shows is the move: every frame Apple wrote sits
            // 40.3 points below the same element in the tree taken just before
            // the audit (the categories sentence 47.0 → 87.3, in I014 too;
            // the chips 92.7 → 133.0; `saved feed` 471.3 → 511.7). The
            // audit's Dynamic Type sweep re-laid the scrolled page out, as it
            // did Rooms and Home's grid. Read off I018's own foot picture
            // where each is drawn, the six whole chips are `#975800` on
            // `#F2F2F7` 5.07:1 and `saved feed` `#000000` on `#F1F1F2`
            // 18.60:1. The first row is under the navigation bar there; the
            // same process's landing picture reads `world` and `national`
            // 5.07:1 and the picked `tech` `#281600` on `#F2A618` 8.50:1. At
            // Apple's frames that picture holds other things (3.73:1 is the
            // free-form field's hint).
            //
            // So ``newsFootContrastMeasured(_:_:)`` retakes the picture once
            // the audit has returned and reads each named element's own ink
            // at its settled frame, first bringing one the navigation bar
            // covers wholly into view. 4.5:1 or better is excused and printed
            // with its reading; under it, or unmeasurable, fails with the
            // reason. `Contrast failed` only, these ten only, this pass only.
            //
            // The anonymous allowance is unchanged: scrolled to the foot the
            // first chip row is cut by the page's top edge, and
            // ``newsCutLeaves(in:)`` allows one anonymous `Contrast failed`
            // per leaf it measures cut (R003 and I014: three and three).
            for _ in 0..<12 { app.swipeUp(velocity: .slow) }
            evidence(app, "news-12-audited-foot")
            let foot = try auditNews(app, "news-foot", Self.newsFootPass)
            findings += newsFootContrastMeasured(app, foot)
        } catch {
            mishaps.append("the page and foot audits could not be run: \(error)")
        }

        // ### 2. The remove confirmation, which hides everything above it.
        do {
            let (app, landed) = openNews()
            defer { app.terminate() }
            if !landed {
                mishaps.append("News never drew a person's heading for the dialog pass")
            }
            let remove = app.buttons["news-remove-topic-tech"]
            if revealNews(app, remove) {
                remove.tap()
                if newsElement(app, "news-remove-ask").waitForExistence(timeout: 10) {
                    evidence(app, "news-12-audited-dialog")
                    findings += try auditNews(
                        app,
                        "news-dialog",
                        "the remove confirmation",
                        behindADialog: true,
                        // **This pass is allowed nothing anonymous.** The
                        // page is `.accessibilityHidden` behind the scrim, so
                        // it offers the audit no elements at all — which is
                        // why its findings are `Potentially inaccessible
                        // text` and not contrast — and R003's dialog tree has
                        // no leaf either edge cuts (measured: 0). An
                        // allowance here would buy nothing and hide the one
                        // thing this pass exists to catch.
                        allowingLeavesCutByAnEdge: false
                    )
                    // **The dialog has to still be there to be dismissed**,
                    // and in train I003 it was not. Two snapshots, five
                    // seconds apart, and nothing between them but the audit:
                    //
                    // * `news-12-audited-dialog` (t = 68.57): the dialog is
                    //   up — `news-remove-ask`, `news-remove-cancel`,
                    //   `news-remove-confirm` — and the page behind it is
                    //   `.accessibilityHidden`, its scroll content an empty
                    //   `{{0, -341.7}, {393, 1111.7}}` with no leaves. That
                    //   is the correct modal shape, so nothing was wrong yet.
                    // * The failed `tap()`'s own input dump (t = 73.07): no
                    //   `news-remove-cancel` anywhere, and the page back and
                    //   whole — `news-remove-topic-tech`, `news-expand-tech`,
                    //   both person chips, both story rows.
                    //
                    // So the dialog closed itself during its own audit and
                    // the detail screen behind it survived intact. Nothing in
                    // `NewsPersonModel` clears `confirmingRemoval` without
                    // also tearing `detail` down — `stop()` does both
                    // (`NewsPersonModel.swift:432-444`) and `cancelRemoval()`
                    // / `confirmRemoval(_:)` only run from the dialog's own
                    // buttons — so the presentation was dropped from above,
                    // the same instability that put `testPrinting_PanelAudit`
                    // and the About and App-layout audits back on the
                    // Settings index inside one case.
                    //
                    // An unguarded `tap()` reports that as "Failed to tap",
                    // which says nothing about what happened. This is **not**
                    // an allowance: the mishap list is asserted empty, so the
                    // case is still red — it is red with the reason in it,
                    // and with the tree beside it.
                    let cancel = app.buttons["news-remove-cancel"]
                    if cancel.exists {
                        cancel.tap()
                    } else {
                        evidence(app, "news-12-dialog-vanished")
                        mishaps.append(
                            "the remove confirmation went away during its own audit — "
                                + "news-remove-ask still on screen: "
                                + "\(newsElement(app, "news-remove-ask").exists), the page "
                                + "behind it still drawing its rows: "
                                + "\(app.buttons["news-remove-topic-tech"].exists)"
                        )
                    }
                } else {
                    mishaps.append("the remove confirmation never came up")
                }
            } else {
                mishaps.append("the remove control was not reachable")
            }
        } catch {
            mishaps.append("the dialog audit could not be run: \(error)")
        }

        XCTAssertEqual([], mishaps, "the audit never reached what it was meant to look at")
        XCTAssertEqual(
            [],
            pins,
            "a person chip is drawing leaves of its own again, which is what "
                + "Apple's audit read as five `Contrast failed`"
        )
        XCTAssertEqual(
            [],
            findings,
            "Apple's audit has findings beyond the measured anonymous floor, "
                + "the content the viewport has clipped away, the two meta "
                + "lines measured to scale, the page this app hides behind a "
                + "dialog, and one anonymous `Contrast failed` per leaf an "
                + "edge was measured cutting in half on that pass (the `CUT "
                + "::` lines in the log name them). Nothing else is "
                + "discounted: a `Contrast failed` naming a category chip, "
                + "`saved feed` or a person chip is this screen's to fix, "
                + "except a foot-pass chip or `saved feed` whose own ink, "
                + "measured where the page settled after the audit, clears "
                + "4.5:1 (the `NEWS FOOT` lines print each reading; a kept "
                + "one says why). An anonymous one beyond the cut count is an "
                + "unknown this case reports"
        )
    }

    /// Every person chip is **one** accessibility element.
    ///
    /// The five `Contrast failed` this screen carried for five trains were
    /// the chip row's own leaves: a `StaticText` per name wearing the pill's
    /// 113 x 44 frame, and the initial of the chip the scroller clips. The
    /// fix is in ``NewsPeopleChipRow``, and it is a shape rather than a
    /// colour, so this is what holds it: a chip with any text descendant is
    /// a chip whose children are back in the tree, and the audit below will
    /// report them again.
    ///
    /// It has already earned its place. Round 1's fix *read* as if it had
    /// worked — the chip gained an element with the right label and the right
    /// traits — and this pin is what said the leaves were still underneath it
    /// in R001 and R002, which no reading of the tree's top line would have.
    ///
    /// Asked of the whole roster rather than of one chip, because the leaf
    /// that cost the fifth finding belonged to the **last** chip — the one
    /// hanging off the scroller's right edge — and a check of the first
    /// would never have seen it.
    @MainActor
    func newsChipRowIsOneElement(_ app: XCUIApplication) -> [String] {
        // The People seed's roster, in `last_seen_at DESC` order
        // (`fixtures/people.py`; this screen defers to it).
        // An explicit result type: a multi-statement closure whose branches
        // are `String` and `nil` is one Swift will not always infer.
        return ["Kamron", "Chevy", "Alex", "alex "].compactMap { (name: String) -> String? in
            let chip = app.buttons["news-chip-\(name)"]
            guard chip.exists else { return "no chip for \(name)" }
            let leaves = chip.staticTexts.count
            return leaves == 0 ? nil : "the \(name) chip exposes \(leaves) text leaves"
        }
    }

    // **The chip row's five `Contrast failed` are fixed in the app, not
    // classified here.** `newsChipArtefact(_:)` used to wave them through on
    // the reasoning that the audit could not find the text in a composite
    // chip and that nothing a UI test did would change what it sampled. The
    // second half of that was wrong, and the run it was written from says so:
    // S005's own hierarchy has each chip Button holding two `StaticText`
    // children, the name of them carrying the **pill's** frame rather than
    // the text's, while the identically-framed Buttons beside them audited
    // clean. So the audit was reading leaves that should never have been in
    // the tree.
    //
    // **Round 1 reached for `accessibilityElement(children: .ignore)` and it
    // did nothing.** R001 and R002 ran with it and this pin reported all four
    // chips exposing two leaves in both: on a `Button` that modifier adds a
    // wrapper element and leaves the control and its leaves underneath it.
    // ``HomeAttentionView``'s rows are the same three-deep shape in the same
    // run, so it is the modifier and not this screen. What takes the leaves
    // out is a label that is a single `Text` — the bubble moved out of the
    // label and over the pill (``NewsPeopleChipRow/chip(_:stacked:)``), which
    // is the shape of every pill in this app that audits clean.
    //
    // Nothing is discounted in their place: a `Contrast failed` anywhere in
    // the chip row now fails this case, and ``newsChipRowIsOneElement``
    // pins the shape the fix depends on so a regression cannot quietly bring
    // the findings back as "the known five".
    //
    // **And the classification is not coming back, because its cause is
    // gone.** `newsChipArtefact(_:)` was deleted when the five findings were
    // fixed in the app (round 3, above). Kamron's answer 39 then took the
    // avatar bubble out of the chip altogether, which removes the last thing
    // in this row the auditor could sample and misattribute: a filled circle
    // inside a capsule was two overlapping fills under one 113 x 44
    // rectangle, and that sampling is what the whole four-train story was
    // about. A pill whose only content is one `Text` has nothing left to
    // classify. If a `Contrast failed` appears in this row again it is a real
    // one, and it fails this case — there is no exclusion left to widen.

    /// A `Contrast failed` against an element the page's own scroll view has
    /// **clipped away** — past its bottom edge or above its top one.
    ///
    /// Train S005 reported one: `id='news-topic-word-tech' label='tech'
    /// frame=(158.3, 790.7, 30.3, 18.0)`, against a page scroll view of
    /// `{{0, 93.3}, {393, 676.7}}` — bottom edge at 770. The word is twenty
    /// points *past* that edge, in the band the phone bar occupies, and the
    /// run's own screenshot shows the topic rows are not on screen at all:
    /// the last thing drawn is the free-form field at 731.7. The content is
    /// 1111.7 points tall in a 676.7-point viewport, so a third of this page
    /// is in the hierarchy with frames nothing draws into.
    ///
    /// An element with no pixels has no contrast to read, which is what the
    /// audit is reporting — the same shape ``testTheVideosGridAudits``
    /// classifies for a row the edge cuts in *half*, one step further out.
    /// `Color.primary` on `secondarySystemBackground` is over 15:1 where it
    /// is actually drawn, and ``testNews_PassesApplesAccessibilityAudit``
    /// scrolls to the foot and audits these very rows on screen rather than
    /// taking that on trust.
    ///
    /// ### The top edge is the same edge
    ///
    /// Train S008 reported the mirror image from the **foot** pass:
    /// `label='Categories give broad coverage; free-form covers the niche.'
    /// frame=(32, 87.3, 305.3, 33.7)`, against the same scroll view at
    /// `{{0, 93.3}, {393, 676.7}}`. The run's own tree puts that sentence at
    /// y = 47 with the content offset by -341.7, and the screenshot beside it
    /// shows what is actually drawn in that band: the navigation bar's
    /// `Back`, with the topics card's heading nowhere on screen. Scrolled to
    /// the foot, the top third of this page has frames above the viewport in
    /// exactly the way the bottom third had frames below it when the page
    /// landed.
    ///
    /// The page pass audits that sentence **on screen** — it is at y = 526 in
    /// the first tree, fully inside the viewport, and the audit reported
    /// nothing against it there. So the two passes between them read every
    /// element of this page where it has pixels, which is the whole reason
    /// this discount is honest.
    ///
    /// **Narrow on purpose**: contrast only, and only outside the edges
    /// ``newsPageViewport(in:)`` measured from the app at audit time. A
    /// finding against anything inside the viewport still fails.
    ///
    /// The viewport arrives as a rectangle rather than as an app to ask,
    /// because this runs while the audit's findings are being read and the
    /// app may no longer be there to ask — train S011 ended in
    /// `app.scrollViews` on this very line, with the case reporting the
    /// process rather than the screen. A classifier that only reads strings
    /// cannot fail that way, and a rectangle taken before the audit is the
    /// more honest one: it is where the scroll view was when the audit
    /// sampled these elements.
    static func newsOutsideTheViewport(_ viewport: CGRect, _ line: String) -> Bool {
        guard line.hasPrefix("Contrast failed"), let box = Self.frame(in: line) else {
            return false
        }
        guard viewport.height > 0 else { return false }
        return box.maxY > viewport.maxY || box.minY < viewport.minY
    }

    /// A `Contrast failed` the audit would not attribute — the shape, not the
    /// permission to ignore one. ``auditNews(_:_:_:behindADialog:allowingLeavesCutByAnEdge:)``
    /// allows these **only** up to the number of leaves ``newsCutLeaves(in:)``
    /// measured an edge cutting in half on that very pass.
    static func newsAnonymousContrast(_ line: String) -> Bool {
        line.hasPrefix("Contrast failed") && line.hasSuffix("| element: nil")
    }

    /// The leaves this screen is showing the audit in **halves**: the ones an
    /// edge runs through, measured from the app at audit time and returned as
    /// text so no live element escapes onto the path that reads the findings
    /// (the reason ``newsPageViewport(in:)`` is taken early — train S011).
    ///
    /// Two edges cut on this screen, and both are counted:
    ///
    /// * the **page** scroll view's top and bottom. Scrolled to the foot, its
    ///   top edge at y = 93.3 runs through the first row of category chips at
    ///   y = 92.7 — by six tenths of a point — and those three chips are then
    ///   drawn in the band the navigation bar occupies;
    /// * the **people chip row's** right edge. That scroller is 361 points
    ///   wide over 421 points of chips, so the last chip always hangs over
    ///   it; the row's own frame has to be inside the page viewport first,
    ///   which is what keeps it from being counted on a pass that has
    ///   scrolled the row away.
    ///
    /// ### Why a half-drawn leaf is a `Contrast failed`
    ///
    /// Half an element has no pixels an audit can read, and this track has
    /// measured the shape three times over. ``testTheVideosGridAudits``
    /// classifies it for a grid row the bottom edge halves;
    /// ``testSystemSettings_AppLayoutPassesApplesAccessibilityAudit`` for a
    /// row of chips the same edge halves — Apple named `Podcasts` in R001 and
    /// left the others with no element at all, which is the same split News
    /// gets. S012 saw both attributions of one leaf: Home's now-playing line,
    /// cut by the same edge, came back named in one run and `element: nil` in
    /// the next, from the same pixels.
    ///
    /// R003's own numbers, which is why this is a classifier and not a
    /// sentence: the page pass showed the audit **one** cut leaf
    /// (`news-chip-alex `, 345.7–437.0 against a rail ending at 377) and
    /// reported **one** anonymous `Contrast failed`; the foot pass showed it
    /// **three** (`news-category-world`, `-national`, `-tech`) and reported
    /// **three**; the dialog pass showed it **none** and reported **none**.
    /// The uncut siblings of those chips were measured on that run's own
    /// screenshot at 151,88,0 on 242,242,247 — **5.07:1** — so it is not the
    /// ink: `business`, `science`, `sports`, `health`, `politics` and
    /// `entertainment` are the identical control on the identical pass and
    /// audit clean.
    ///
    /// **Narrow on purpose, and it is a count rather than a licence.** One
    /// anonymous `Contrast failed` more than there are cut leaves still fails
    /// this case, as does any other anonymous headline, and a named finding
    /// is still placed by its own frame.
    ///
    /// ### Why this is one tree read rather than a query per element
    ///
    /// It was `app.scrollViews/.buttons/.staticTexts.allElementsBoundByIndex`
    /// and then `.frame` on each, and **every element resolved that way is a
    /// whole-tree pass inside the app process**.
    ///
    /// What this helper and its neighbour asked for, counted in I005's slice
    /// of this case: each pass that measures leaves resolved 30
    /// `Find the StaticText (Element at index N)`, 27 `Find the Button
    /// (Element at index N)` and 4 `Find the ScrollView (Element at index N)`
    /// — 61 elements one at a time, in the block immediately before that
    /// pass's `CUT ::` group (`t = 21.0–22.9 s` on the page pass,
    /// `t = 41.6–43.8 s` on the foot) — and the dialog pass, which allows no
    /// cut and so reads the viewport only, resolved two more at
    /// `t = 70.52–70.54 s`: **124 of the 235 `Find the …` lines in the whole
    /// case slice**.
    ///
    /// What it cost the process, measured out of the kept simlog for pid
    /// 83743 (this case's first process, the one I005 lost) by bucketing
    /// every line it wrote — continuation lines of a multi-line dump
    /// attributed to the entry that owns them — into deciseconds: 229
    /// `Capturing hierarchy with attributes` whole-tree dumps and
    /// **8 229 350 B** over its whole life. The two helper bursts,
    /// 19:49:01.4–03.3 (the page pass) and 19:49:22.0–24.2 (the foot), are
    /// **155 of those 229 captures and 3 433 264 B** — about two thirds of
    /// the whole-tree work this process was asked for and about two fifths of
    /// what it wrote, at about 22 kB a capture against the process's own
    /// 36 kB average. One `snapshot()` answers all of it in process, so the
    /// three passes cost three captures.
    ///
    /// **The bursts either side are not this pair's**, and an earlier
    /// revision of this comment quoted a window instead of the helpers:
    /// 19:49:01.1–01.3 (7 captures, 146 857 B) is
    /// ``newsChipRowIsOneElement(_:)``'s existence checks, and
    /// 19:49:03.4–04.0 (25 captures, 663 939 B) together with
    /// 19:49:29.5–29.9 (15 captures, 313 455 B — the second this process
    /// died) is Apple's own audit resolving its findings' frames. Nothing
    /// here touches either. Nor is the rest of the life quiet: 19:48:52–58,
    /// inside `openNews`'s waits, wrote **2 923 213 B, 36% of everything
    /// this process ever logged**, in seven seconds with two captures,
    /// because a first-match poll walks the tree as well. This pair is the
    /// largest thing this file can cut; it is not the largest thing in the
    /// log.
    ///
    /// (The 43 `Find the "…" Button` and 12 `Find the "…" StaticText` lines in
    /// the same slice are **not** these helpers'. The 12 are
    /// `news-story-meta-3xx`, from the `newsElement(…).waitForExistence`
    /// lines; the 43 are `news-chip-…` / `news-category-…` /
    /// `news-remove-topic-…`, from ``newsChipRowIsOneElement(_:)`` and the
    /// taps. An earlier revision of this comment cited those two counts here
    /// by mistake — they are named finds, and this helper resolved by index.)
    ///
    /// It is also the more honest measurement: the viewport and the leaves
    /// now come from **one instant**, where before the scroll view's frame
    /// and the last leaf's could be a second apart.
    ///
    /// The geometry is unchanged — same two edges, same strict crossings,
    /// same `type=… id='…' label='…' frame=(…)` line, which is what
    /// ``frame(in:)`` parses and what the `CUT ::` lines in a train's log
    /// are read as.
    @MainActor
    static func newsCutLeaves(in tree: XCUIElementSnapshot) -> [String] {
        let nodes = newsDescendants(tree)
        let scrollers = nodes.filter { $0.elementType == .scrollView }.map(\.frame)
        guard let viewport = scrollers.max(by: { $0.height < $1.height }),
            viewport.height > 0
        else {
            return []
        }

        let controls = nodes.filter { $0.elementType == .button }
        let leaves = nodes.filter { $0.elementType == .staticText } + controls
        var cut = leaves.filter { leaf in
            let box = leaf.frame
            guard box.height > 0 else { return false }
            return (box.minY < viewport.minY && box.maxY > viewport.minY)
                || (box.minY < viewport.maxY && box.maxY > viewport.maxY)
        }

        // The people chip row lying on its side — strictly shorter than the
        // page, and only while the page is actually showing it.
        if let rail = scrollers.min(by: { $0.height < $1.height }),
            rail.height > 0,
            rail.height < viewport.height,
            rail.minY >= viewport.minY,
            rail.maxY <= viewport.maxY
        {
            cut += controls.filter { leaf in
                let box = leaf.frame
                guard box.height > 0, box.intersects(rail) else { return false }
                return (box.minX < rail.minX && box.maxX > rail.minX)
                    || (box.minX < rail.maxX && box.maxX > rail.maxX)
            }
        }
        return cut.map { newsOneLine($0) }
    }

    /// The frame of the page's own scroll view — the taller of the two on
    /// this screen, the other being the people chip row lying on its side.
    ///
    /// `.zero` when there is none, which ``newsOutsideTheViewport(_:_:)``
    /// treats as "discount nothing": an unmeasured viewport must not excuse a
    /// finding.
    @MainActor
    static func newsPageViewport(in tree: XCUIElementSnapshot) -> CGRect {
        newsDescendants(tree)
            .filter { $0.elementType == .scrollView }
            .map(\.frame)
            .max(by: { $0.height < $1.height }) ?? .zero
    }

    /// The **rows** of the page that are on the window but wholly outside the
    /// page's viewport — under the phone bar, or under the navigation bar —
    /// one line per row, returned as text like ``newsCutLeaves(in:)``.
    ///
    /// ### Why, from train I006
    ///
    /// The page pass came back with one anonymous `Contrast failed` and no cut
    /// leaf to allow it, and it is the same finding R005 had **named**:
    /// `news-topic-word-tech`, `label='tech'`, `frame=(158.3, 790.7, 30.3,
    /// 18.0)`. That row lies wholly below the page scroll view's foot (the
    /// viewport is 93.3–770.0) and under the phone bar (774 on), and I006's
    /// screenshot puts all three of its leaves on the bar's fill between the
    /// `music` and `videos` captions. The checker measured a text element whose
    /// pixels are the bar. Named, ``newsOutsideTheViewport(_:_:)`` discounts
    /// it; anonymous, nothing did — R003, I003, I005 and I006 returned it as
    /// `element: nil` from the same tree, R005 named it, and R004 went green
    /// only because a wider chip gave it a cut leaf to hide behind
    /// (`i006-diag/chat-news-states.md`, entry 2). Every *visible* text run on
    /// that screenshot measured 5.05:1 or better.
    ///
    /// ### The page's own subtree, and one per row
    ///
    /// **Only the page scroll view's descendants.** The first version of this
    /// walked the whole app tree and, on I006's page-pass tree, returned 11:
    /// the four shell-header leaves (`domovoi / news`, `127.0.0.1:6601`,
    /// `unverified`, `live`) and the four tab captions are outside the page's
    /// viewport too, because they are not the page at all — they are chrome
    /// drawn where it belongs — and counting them handed every cut-allowed
    /// pass eight allowances for nothing (verifier, revision 1).
    ///
    /// **One per row, not one per leaf.** The topic row is three leaves
    /// (`news-topic-kind-tech`, `-word-`, `-count-`, at y = 789.3 / 790.7 /
    /// 792.5), and every train that saw it returned **one** finding. Leaves
    /// whose vertical extents overlap are one row here, so I006's page pass
    /// allows exactly the one finding every train has returned.
    ///
    /// **Still a count, not a licence.** Each hidden row allows one anonymous
    /// `Contrast failed` on the pass it was measured on, beside the cut
    /// leaves; one more than `cut + hidden` still fails, and a **named**
    /// finding inside the viewport still fails wherever it is. A leaf off the
    /// window altogether is not counted — the checker does not sample pixels
    /// that are not on the screen — and neither is a leaf an edge runs
    /// through, which ``newsCutLeaves(in:)`` already counts.
    ///
    /// `[]` when there is no page scroll view to measure against: an
    /// unmeasured viewport must not excuse a finding.
    @MainActor
    static func newsHiddenRows(in tree: XCUIElementSnapshot) -> [String] {
        let window = tree.frame
        // The page's own scroll view — the tallest, as ``newsPageViewport(in:)``
        // picks it — as a node, so its subtree and not the app's is walked.
        guard
            let page = newsDescendants(tree)
                .filter({ $0.elementType == .scrollView && $0.frame.height > 0 })
                .max(by: { $0.frame.height < $1.frame.height }),
            window.height > 0
        else { return [] }
        let viewport = page.frame
        let hidden = newsDescendants(page)
            .filter { node in
                let box = node.frame
                guard node.elementType == .staticText, box.height > 0 else { return false }
                let outside = box.maxY <= viewport.minY || box.minY >= viewport.maxY
                return outside && window.contains(box)
            }
            .sorted { $0.frame.minY < $1.frame.minY }
        // Group by vertical overlap: a leaf that starts above the current
        // row's foot is in that row.
        var rows: [String] = []
        var current: (top: CGFloat, foot: CGFloat, lines: [String])?
        for node in hidden {
            let box = node.frame
            if let row = current, box.minY < row.foot {
                current = (row.top, max(row.foot, box.maxY), row.lines + [newsOneLine(node)])
            } else {
                if let row = current {
                    rows.append("row \(row.top)–\(row.foot): " + row.lines.joined(separator: " | "))
                }
                current = (box.minY, box.maxY, [newsOneLine(node)])
            }
        }
        if let row = current {
            rows.append("row \(row.top)–\(row.foot): " + row.lines.joined(separator: " | "))
        }
        return rows
    }

    /// The whole tree at one instant, or `nil` with a line saying why.
    ///
    /// `nil` is the conservative answer in both places that read it: no
    /// viewport discounts nothing, and no cut leaves allow nothing. A tree
    /// that could not be read is almost always a process that has gone, and
    /// ``auditIssues(_:_:)`` is the one that says so properly — it reads
    /// `app.state`, which needs no snapshot and is safe after the app has
    /// died (train I002's phantom Voices findings).
    @MainActor
    private func newsTree(_ app: XCUIApplication) -> XCUIElementSnapshot? {
        do {
            return try app.snapshot()
        } catch {
            print("NEWS AUDIT :: the tree could not be read: \(error)")
            return nil
        }
    }

    /// Every descendant of `tree`, in the order a query resolves them.
    ///
    /// Pre-order and the root excluded, which is what
    /// `descendants(matching:)` returns; the children are pushed reversed so
    /// that popping walks them in tree order.
    @MainActor
    static func newsDescendants(_ tree: XCUIElementSnapshot) -> [XCUIElementSnapshot] {
        var out: [XCUIElementSnapshot] = []
        var stack = Array(tree.children.reversed())
        while let node = stack.popLast() {
            out.append(node)
            stack.append(contentsOf: node.children.reversed())
        }
        return out
    }

    /// One snapshot as one line, in ``oneLine(_:)``'s exact wording — the
    /// `CUT ::` lines in every train's log are read as this shape, and
    /// ``frame(in:)`` parses the rectangle out of it.
    @MainActor
    static func newsOneLine(_ node: XCUIElementSnapshot) -> String {
        let id = node.identifier
        return "type=\(node.elementType.rawValue)"
            + (id.isEmpty ? "" : " id='\(id)'")
            + " label='\(node.label)'"
            + " frame=\(node.frame)"
    }

    /// A Dynamic Type finding against a **story's meta line**, which this
    /// run's own measurement contradicts.
    ///
    /// Train S008's foot pass named two: `news-story-meta-302` and
    /// `news-story-meta-301`, "User will not be able to change the font size
    /// of this element". ``testNews_AtTheLargestTypeSize`` measures
    /// `news-story-meta-301` in the same run, at both sizes:
    ///
    ///     ordinary   13.33 points tall
    ///     AX5       240.00 points tall
    ///
    /// Eighteen times the height is not a font that cannot change — the line
    /// goes from one row to six wrapped ones because every glyph in it grew.
    /// The modifier the audit is reacting to is `.caption2.monospaced()`
    /// (`NewsSavedFeedView.swift:98`), a text style with a design applied;
    /// `news-topic-count-tech` beside it is `.caption.monospaced()` and was
    /// not named, which is the audit's heuristic about very small type and
    /// not a difference in how the two scale.
    ///
    /// This is the third named Dynamic Type false positive on this track
    /// (``knownScalingFalsePositive`` is the first), and it is pinned by its
    /// **identifier** rather than taken out of the count: a named Dynamic
    /// Type finding against any other element on this screen — a real one —
    /// still fails the case, and the measurement that justifies this one is a
    /// live assertion in the case next door rather than a sentence in a
    /// comment.
    static func newsScalingFalsePositive(_ line: String) -> Bool {
        line.hasPrefix("Dynamic Type font sizes are")
            && line.contains("id='news-story-meta-")
    }

    /// `Potentially inaccessible text` with no element, in the **dialog**
    /// pass: the page ``NewsView`` has deliberately taken out of the
    /// accessibility tree, still drawn under the scrim.
    ///
    /// Train S008 reported seven, all anonymous, and only from the pass with
    /// the confirmation up. The sentence Apple prints is "This element
    /// appears to display text that should be represented using the
    /// accessibility API", which is a description of what
    /// `.accessibilityHidden(removalAsked != nil)` does on purpose:
    /// ``DialogCard``'s scrim is `Color.black.opacity(0.35)` and the run's
    /// screenshot shows `saved feed`, both story titles, both summaries, both
    /// meta lines and the topics sentence perfectly legible behind it — text
    /// with pixels and no accessibility element, which is the finding.
    ///
    /// The alternative was ``SettingsPlaceholder``'s, and it is worse: with
    /// `.isModal` alone the page stays *in* the tree and Apple's audit
    /// reports contrast against text nobody can read, which is the gap that
    /// cost this app two `Contrast failed` on Settings. Every dialog in this
    /// app has the same shape, so the fix belongs to ``DialogCard`` rather
    /// than to News — DECISIONS.md, "Needs a shared change".
    ///
    /// **Narrow on purpose**: this headline only, no element only, and only
    /// in the pass that has a dialog up. The same finding on the page pass,
    /// or one that names an element, still fails.
    static func newsHiddenPageText(_ line: String) -> Bool {
        line.hasPrefix("Potentially inaccessible text") && line.hasSuffix("| element: nil")
    }

    /// One audit, classified by signature; the findings it cannot explain are
    /// **returned** rather than asserted.
    ///
    /// Returned because of what an assertion here does to the case that calls
    /// it: `continueAfterFailure = false` means the first failure ends the
    /// case where it stands, and the caller has an app to put down first.
    /// Train S005 lost the *next* case to exactly that.
    @MainActor
    private func auditNews(
        _ app: XCUIApplication,
        _ name: String,
        _ what: String,
        behindADialog: Bool = false,
        allowingLeavesCutByAnEdge cutAllowed: Bool = true
    ) throws -> [String] {
        // **Measured before the audit runs, while the app is certainly
        // there.** Reading it per finding put a live query to the app on the
        // path that reads the audit's results; see
        // ``newsOutsideTheViewport(_:_:)``. ``newsCutLeaves(in:)`` is taken on
        // the same line and for the same reason, and it comes back as text so
        // that printing it cannot put a query there either.
        //
        // **One read of the tree for both**, where this used to resolve every
        // scroll view, button and static text one element at a time: see
        // ``newsCutLeaves(in:)`` for what that cost in a train's log. A tree
        // that could not be read discounts nothing and allows nothing.
        let tree = newsTree(app)
        let viewport = tree.map { Self.newsPageViewport(in: $0) } ?? .zero
        let cut = cutAllowed ? (tree.map { Self.newsCutLeaves(in: $0) } ?? []) : []
        // The page rows on the window that the viewport hides wholly (train
        // I006's topic row under the phone bar): ``newsHiddenRows(in:)``, one
        // per row and from the page's own subtree. From the same snapshot, so
        // no extra tree read, and under the same gate — the dialog pass allows
        // nothing anonymous.
        let hidden = cutAllowed ? (tree.map { Self.newsHiddenRows(in: $0) } ?? []) : []
        // Printed every pass, allowed for or not: a case that goes red here
        // next train needs to know what the edges were cutting, and a pass
        // whose count has changed is the first thing to read.
        for leaf in cut { print("NEWS AUDIT [\(what)] CUT :: \(leaf)") }
        for row in hidden { print("NEWS AUDIT [\(what)] HIDDEN :: \(row)") }
        let split = Self.floorAndRest(try auditIssues(app, name))
        var counts: [String: Int] = [:]
        var rest: [String] = []
        var anonymousAllowed = cut.count + hidden.count
        for found in split.rest {
            // In this order, and each one narrow: a line no classifier claims
            // lands in `rest` and fails the caller.
            if Self.newsOutsideTheViewport(viewport, found) {
                counts["outside the viewport", default: 0] += 1
            } else if Self.newsScalingFalsePositive(found) {
                counts["measured scaling false positives", default: 0] += 1
            } else if behindADialog, Self.newsHiddenPageText(found) {
                counts["the hidden page's text", default: 0] += 1
            } else if Self.newsAnonymousContrast(found), anonymousAllowed > 0 {
                // One per cut leaf or hidden row and no more — the count is
                // this pass's own measurement, printed above, not a tolerance.
                anonymousAllowed -= 1
                counts["leaves an edge cuts in half or rows the viewport hides", default: 0] += 1
            } else {
                rest.append(found)
            }
        }
        let classified = counts
            .sorted { $0.key < $1.key }
            .map { "\($0.key)=\($0.value)" }
            .joined(separator: " ")
        print("NEWS AUDIT [\(what)] floor=\(split.floor) rest=\(rest.count) \(classified)")
        for found in split.rest { print("NEWS AUDIT   \(found)") }
        return rest.map { "[\(what)] \($0)" }
    }

    // MARK: - 12b. the foot pass's contrast, measured where the page settled

    /// The foot pass's name, spelled once: ``auditNews(_:_:_:behindADialog:allowingLeavesCutByAnEdge:)``
    /// prefixes every line it returns with it, and ``newsFootTarget(in:)``
    /// takes only lines that carry it, which is what keeps the allowance off
    /// the page and dialog passes.
    static let newsFootPass = "the foot of the page"

    /// How many flicks ``newsFootContrastMeasured(_:_:)`` may spend bringing
    /// an element the navigation bar covers wholly into view. The first chip
    /// row needs under 45 points; six slow flicks is far more than that.
    static let newsFootFlicks = 6

    /// The foot pass's named `Contrast failed` findings on a category chip or
    /// on `saved feed`, **measured** where the page settled after the audit,
    /// rather than believed (I018: the audit judged the page 40.3 points away
    /// from where the case left it; the comment at the foot pass has it).
    ///
    /// Every other line is returned untouched, so it fails the case. Each line
    /// it takes is judged by ``newsFootJudge(_:on:read:)`` on a picture and a
    /// tree retaken after the audit (``newsFootSettled(_:_:)``): excused and
    /// printed with its reading at 4.5:1 or better; returned, with the reason
    /// appended, when it reads under that or cannot be read. An element the
    /// navigation bar covers is brought wholly into view one flick at a time
    /// and read there; one that never gets there is returned, and the tree
    /// is printed.
    @MainActor
    func newsFootContrastMeasured(_ app: XCUIApplication, _ lines: [String]) -> [String] {
        var kept: [String] = []
        var pending: [String] = []
        for line in lines {
            if Self.newsFootTarget(in: line) == nil { kept.append(line) } else { pending.append(line) }
        }
        guard !pending.isEmpty else { return kept }
        print(
            "NEWS FOOT: \(pending.count) named `Contrast failed` on this screen's chips or "
                + "`saved feed` — measured where the page settled"
        )
        var flicks = 0
        var excused = 0
        while !pending.isEmpty {
            let moment = flicks == 0 ? "settled after the audit" : "after \(flicks) flick(s)"
            guard let settled = newsFootSettled(app, moment) else {
                for line in pending {
                    let why = "no steady picture and tree could be taken \(moment), so it was not measured"
                    print("NEWS FOOT NOT excused :: \(why) :: \(line)")
                    kept.append("\(line) :: NOT excused: \(why)")
                }
                break
            }
            let (page, picture) = settled
            var covered: [(line: String, frame: CGRect)] = []
            for line in pending {
                let verdict = Self.newsFootJudge(line, on: page) {
                    Self.roomsLeafInk(picture, window: page.window, frame: $0)
                }
                switch verdict {
                case .notOurs:
                    kept.append(line)
                case .excused(let said):
                    excused += 1
                    print("NEWS FOOT excused, measured \(moment) :: \(said) :: \(line)")
                case .kept(let why):
                    print("NEWS FOOT NOT excused, \(moment) :: \(why) :: \(line)")
                    kept.append("\(line) :: NOT excused (\(moment)): \(why)")
                case .covered(let frame):
                    covered.append((line: line, frame: frame))
                }
            }
            pending = covered.map { $0.line }
            guard let first = covered.first else { break }
            guard flicks < Self.newsFootFlicks else {
                for one in covered {
                    let why = "never wholly in view: at \(one.frame) \(moment), against the visible page \(page.visible)"
                    print("NEWS FOOT NOT excused :: \(why) :: \(one.line)")
                    kept.append("\(one.line) :: NOT excused: \(why)")
                }
                print("=== NEWS FOOT: COVERED ELEMENTS NEVER CAME WHOLLY INTO VIEW — tree follows ===")
                print(app.debugDescription)
                break
            }
            print(
                "NEWS FOOT: \(covered.count) covered \(moment) (first at \(first.frame), "
                    + "visible page \(page.visible)) — one flick"
            )
            if first.frame.midY < page.visible.midY {
                app.swipeDown(velocity: .slow)
            } else {
                app.swipeUp(velocity: .slow)
            }
            flicks += 1
        }
        print("NEWS FOOT measured: excused=\(excused) returned=\(kept.count) flicks=\(flicks)")
        return kept
    }

    /// The page's geometry and a picture of it, **after** the audit, taken as
    /// tree, screenshot, tree, and kept only when the two trees agree on
    /// every element ``newsFootPage(in:)`` reads: a page still moving is not
    /// a measurement. Three tries, a second apart; `nil` with a line saying
    /// why. The picture is attached, so every reading can be checked against
    /// what was measured.
    @MainActor
    func newsFootSettled(_ app: XCUIApplication, _ moment: String) -> (NewsFootPage, CGImage)? {
        for attempt in 1...3 {
            guard app.state == .runningForeground else {
                print("NEWS FOOT: the app is \(app.state.rawValue), not running — nothing measured \(moment)")
                return nil
            }
            let beat = XCTestExpectation(description: "the page settles before it is measured")
            _ = XCTWaiter.wait(for: [beat], timeout: 1)
            guard let before = newsTree(app) else { return nil }
            let shot = app.screenshot()
            guard let after = newsTree(app) else { return nil }
            let one = Self.newsFootPage(in: before)
            let two = Self.newsFootPage(in: after)
            guard one == two else {
                print("NEWS FOOT: the page moved while it was photographed \(moment) (try \(attempt) of 3)")
                continue
            }
            guard let picture = shot.image.cgImage else {
                print("NEWS FOOT: the screenshot \(moment) has no bitmap")
                return nil
            }
            let attachment = XCTAttachment(screenshot: shot)
            attachment.name = "news-12-foot-measured-" + moment.replacingOccurrences(of: " ", with: "-")
            attachment.lifetime = .keepAlways
            add(attachment)
            print(
                "NEWS FOOT page \(moment): window \(two.window), visible \(two.visible); "
                    + two.elements.map { "'\($0.label)' \($0.frame)" }.joined(separator: ", ")
            )
            return (two, picture)
        }
        return nil
    }

    /// What ``newsFootJudge(_:on:read:)`` needs from one tree: the window,
    /// the part of the page's scroll view the navigation bar does not cover,
    /// and every category chip and `saved feed` heading with its frame.
    @MainActor
    static func newsFootPage(in tree: XCUIElementSnapshot) -> NewsFootPage {
        let nodes = newsDescendants(tree)
        let viewport = newsPageViewport(in: tree)
        let bar = nodes.first { $0.elementType == .navigationBar }?.frame ?? .zero
        var visible = viewport
        // Scrolled to the foot, the bar (93.3–137.3 in I018) lies over the
        // scroll view's top (93.3): what is under it is not drawn.
        if !viewport.isEmpty, !bar.isEmpty, bar.minY <= viewport.minY + 0.5, bar.maxY > viewport.minY {
            visible = CGRect(
                x: viewport.minX,
                y: bar.maxY,
                width: viewport.width,
                height: max(0, viewport.maxY - bar.maxY)
            )
        }
        let elements = nodes
            .filter {
                ($0.elementType == .button
                    && $0.identifier.hasPrefix(NewsStringsUnderTest.categoryChipPrefix))
                    || ($0.elementType == .staticText && $0.label == NewsStringsUnderTest.savedFeed)
            }
            .map {
                NewsFootElement(
                    type: $0.elementType.rawValue,
                    id: $0.identifier,
                    label: $0.label,
                    frame: $0.frame
                )
            }
        return NewsFootPage(window: tree.frame, visible: visible, elements: elements)
    }

    /// The element a foot-pass line names, when it is one this allowance may
    /// measure: a **`Contrast failed`** line from **the foot pass**, naming a
    /// `news-category-…` Button or the `saved feed` StaticText (no
    /// identifier). `nil` for everything else — another headline, another
    /// pass, another element, an anonymous finding — so that line fails.
    static func newsFootTarget(in line: String) -> NewsFootElement? {
        guard line.hasPrefix("[\(newsFootPass)] Contrast failed:"),
              let element = line.range(of: "| element: type=")
        else { return nil }
        let digits = line[element.upperBound...].prefix { $0.isNumber }
        guard let type = UInt(digits), let label = roomsLabel(in: line) else { return nil }
        var id = ""
        if let open = line.range(of: " id='", range: element.upperBound..<line.endIndex),
           let close = line.range(of: "' label='", range: open.upperBound..<line.endIndex) {
            id = String(line[open.upperBound..<close.lowerBound])
        }
        let chip = NewsStringsUnderTest.categoryChipPrefix
        let isChip = type == XCUIElement.ElementType.button.rawValue
            && id.hasPrefix(chip) && id.count > chip.count
        let isSavedFeed = type == XCUIElement.ElementType.staticText.rawValue
            && id.isEmpty && label == NewsStringsUnderTest.savedFeed
        guard isChip || isSavedFeed else { return nil }
        return NewsFootElement(type: type, id: id, label: label, frame: frame(in: line) ?? .zero)
    }

    /// One foot-pass line against one settled page. **Pure**: the page is
    /// data and the ink comes from `read`, so the judgement needs no app.
    ///
    /// * not ``newsFootTarget(in:)``'s: `.notOurs` (the caller keeps it);
    /// * no visible page, or not exactly one element answering to the line's
    ///   type, identifier and label: `.kept`, with the reason;
    /// * the element not wholly inside the visible page: `.covered`, so the
    ///   caller can bring it into view and ask again;
    /// * nothing but paper in its frame: `.kept`;
    /// * its ink under ``roomsContrastFloor`` (4.5:1, held whatever the text
    ///   size): `.kept`, with the reading;
    /// * otherwise `.excused`, with the reading.
    @MainActor
    static func newsFootJudge(
        _ line: String,
        on page: NewsFootPage,
        read: (CGRect) -> (ink: String, paper: String, ratio: Double)?
    ) -> NewsFootVerdict {
        guard let target = newsFootTarget(in: line) else { return .notOurs }
        let name = target.id.isEmpty ? "'\(target.label)'" : target.id
        let visible = page.visible
        guard !visible.isEmpty else { return .kept("no visible page was measured, so \(name) was not read") }
        let twins = page.elements.filter {
            $0.type == target.type && $0.id == target.id && $0.label == target.label
        }
        guard twins.count == 1, let one = twins.first else {
            return .kept("\(twins.count) elements answer to \(name) in the settled tree, not one")
        }
        let at = one.frame
        let whole = at.height > 0
            && at.minX >= visible.minX - 0.5 && at.maxX <= visible.maxX + 0.5
            && at.minY >= visible.minY - 0.5 && at.maxY <= visible.maxY + 0.5
        guard whole else { return .covered(at) }
        guard let reading = read(at) else {
            return .kept("\(name) at \(at) holds only its paper — nothing to read")
        }
        let said = "\(name) drawn at \(at) (Apple's frame \(target.frame)) reads \(reading.ink) on "
            + "\(reading.paper) " + String(format: "%.2f", reading.ratio) + ":1"
        guard reading.ratio >= roomsContrastFloor else {
            return .kept("\(said), under 4.5:1 where it is drawn — the colour is the defect")
        }
        return .excused(said)
    }

    // MARK: - 13. the largest type size

    /// At AX5 the topic row is a column and the text is **measured** to have
    /// grown.
    ///
    /// A named Dynamic Type finding has twice been a false positive on this
    /// track, so this does not read the audit's sentence: it takes the height
    /// of the same two leaves at the ordinary size and at AX5 and compares
    /// them. A fixed `frame(width:)` on a glyph box or a `minimumScaleFactor`
    /// that crept in shows up as text that did not grow.
    @MainActor
    func testNews_AtTheLargestTypeSize() throws {
        resetNewsHouse()
        var heights: [String: [String: CGFloat]] = [:]
        let leaves = ["news-topic-word-tech", "news-story-meta-301", "news-person-heading"]
        // Measured but **not** held to the half-again bar below: a chip is a
        // tap target with a 44-point floor under it, so a chip that grew from
        // 44 to 60 has grown and would still read as "stuck" against a
        // multiplier. Train S002 named all nine of these — it was the
        // `LazyVGrid` they were in, which is gone — and the claim the finding
        // makes is about *this* element, so this is where it is checked.
        let chip = "news-category-entertainment"

        for (label, mode) in [
            ("large", Self.ordinaryTypeSize),
            ("AX5", "UICTContentSizeCategoryAccessibilityXXXL"),
        ] {
            let (app, landed) = openNews(mode)
            XCTAssertTrue(landed, "News never drew a person's heading at \(label)")
            // The story meta is below the fold at AX5; everything on this page
            // exists from the first frame (a plain `VStack`), so this is only
            // about getting a frame worth measuring.
            _ = newsElement(app, "news-story-meta-301").waitForExistence(timeout: 25)
            evidence(app, "news-13-\(label)")

            var measured: [String: CGFloat] = [:]
            for leaf in leaves + [chip] {
                let element = newsElement(app, leaf)
                if element.exists { measured[leaf] = element.frame.height }
                print("NEWS SCALE [\(label)] \(leaf) height=\(element.exists ? "\(element.frame.height)" : "missing")")
            }
            heights[label] = measured

            if label == "AX5" {
                // The topic row swaps to a column: the count sits **below**
                // the topic word rather than beside it. Five things on one
                // line at 48-point text is one word a line.
                let word = newsElement(app, "news-topic-word-tech")
                let count = newsElement(app, "news-topic-count-tech")
                XCTAssertTrue(word.exists && count.exists, "the topic row is not on screen at AX5")
                XCTAssertGreaterThan(
                    count.frame.minY,
                    word.frame.minY + 8,
                    "the topic row is still a row at AX5 — the count is beside "
                        + "the word, not under it"
                )
                // Nothing on this page is wider than the window, which is the
                // other half of "nothing is clipped".
                XCTAssertLessThanOrEqual(
                    word.frame.maxX,
                    app.frame.maxX + 1,
                    "the topic word runs off the side of the screen"
                )
            }
            app.terminate()
        }

        guard let small = heights["large"], let big = heights["AX5"] else {
            return XCTFail("a launch did not reach News")
        }
        var grew: [String] = []
        var stuck: [String] = []
        for leaf in leaves {
            guard let was = small[leaf], let now = big[leaf] else {
                print("NEWS SCALE [compare] \(leaf) MISSING at one size")
                continue
            }
            if now > was * 1.5 {
                grew.append("\(leaf) \(was)->\(now)")
            } else {
                stuck.append("\(leaf) \(was)->\(now)")
            }
        }
        print("NEWS SCALE GREW " + grew.joined(separator: " | "))
        print("NEWS SCALE STUCK " + stuck.joined(separator: " | "))
        XCTAssertTrue(
            stuck.isEmpty,
            "these leaves did not grow by half again between the ordinary "
                + "size and AX5: " + stuck.joined(separator: " | ")
        )
        XCTAssertEqual(grew.count, leaves.count, "a leaf was never measured at both sizes")

        // The chip, on its own terms: taller than the 44-point tap floor it
        // starts at, which is what "the user can change the font size of this
        // element" means for a control whose height has a minimum.
        guard let chipWas = small[chip], let chipNow = big[chip] else {
            return XCTFail("the category chip was not on screen at both sizes")
        }
        print("NEWS SCALE [chip] \(chip) \(chipWas)->\(chipNow)")
        XCTAssertGreaterThan(
            chipNow,
            max(chipWas, 44) + 8,
            "the category chip did not grow past its tap-target floor at AX5 "
                + "— the audit's Dynamic Type finding against the nine chips "
                + "would be a real one"
        )
    }
}

/// What this screen says, spelled out here rather than imported.
///
/// The convention of this file and the reason for it: a query that silently
/// followed a renamed constant would stop testing what it claims to. Every
/// string below is ``NewsStrings``' — and the three that are not Android's
/// own are marked, because a change to one of those is a change to a decision
/// rather than to a transcription.
enum NewsStringsUnderTest {

    /// ``NewsStrings/personsNews(_:)`` for the two seeded people.
    static let kamronsNews = "Kamron's news"
    static let chevysNews = "Chevy's news"

    /// ``NewsStrings/noBriefing``.
    static let noBriefing = "No briefing yet. Turn on auto-fetch (or poll now) to generate one."

    /// ``NewsStrings/feedCount(_:)`` at nought, two and three.
    static let zeroFeeds = "0 feeds"
    static let twoFeeds = "2 feeds"
    static let threeFeeds = "3 feeds"

    /// ``NewsStrings/expand``.
    static let expand = "expand"

    /// ``NewsStrings/favorite`` and ``NewsStrings/unfavorite``.
    static let favorite = "favorite"
    static let unfavorite = "unfavorite"

    /// ``NewsStrings/pollNow``, and ``NewsStrings/polled(_:)`` for the stub's
    /// `house: 1, topics: 2`. **The dash is an em dash**, U+2014, as the
    /// Kotlin's is.
    static let pollNow = "poll now"
    static let polledThree = "polled — 3 new items"

    /// ``NewsStrings/removeTopicBody(_:)`` for the seeded free-form topic.
    static let removeFormulaOne = "Remove \"Formula 1\" and its feeds?"

    /// ``NewsStrings/savedFeedLabel``, the saved feed card's heading.
    static let savedFeed = "saved feed"

    /// Every category chip's identifier starts with this
    /// (`NewsTopicsView.swift`: `news-category-<word>`).
    static let categoryChipPrefix = "news-category-"

    /// ``NewsStrings/noPeople`` and its sub-line.
    static let noPeople = "no people enrolled yet"
    static let noPeopleSub = "enroll a speaker under People in Settings first"

    /// ``NewsStrings/feedAdded``.
    static let feedAdded = "feed added"

    /// ``NewsStrings/freeformPlaceholder`` — Android's whole sentence, which
    /// is now the field's **accessibility label** rather than its visible
    /// hint. The ellipsis is U+2026.
    static let freeformPlaceholder = "add a free-form topic (e.g. Formula 1)…"

    /// ``NewsStrings/freeformPlaceholderShort`` — the visible hint, and so
    /// what an empty `TextField` reports as its `value`. **iOS-only**:
    /// Android's sentence did not fit the field and Apple's audit reported it
    /// clipped (train S005). IOS-ONLY-STRINGS.md §6.
    static let freeformPlaceholderShort = "free-form topic, e.g. Formula 1"

    /// The kind pill's word for a free-form topic (`news.py:154`).
    static let freeformKind = "freeform"

    /// **iOS-only** — the two words the feed's state dot carries, which
    /// Android has no counterpart for: over there the colour is the only
    /// thing that says a feed is failing. IOS-ONLY-STRINGS.md.
    static let feedValid = "valid"
    static let feedInvalid = "not valid"

    // ---- the stub's own words, not the app's ------------------------------

    /// `fixtures/news.py`: the summary of story 301 with its tags taken out.
    static let strippedSummary = "Bold claim"

    /// The last clause of a story that has not been read.
    static let unread = "· unread"

    /// Feed 102 has no title and no source, so its url is its title.
    static let bustedFeedURL = "https://example.invalid/busted.xml"

    /// The stub's 409 detail, which has to reach the toast whole.
    static let internetOff = "the internet is turned off for this house"

    /// The stub's detail for a roster read that failed.
    static let rosterFailureDetail = "the people table is not answering"
}

/// One element as ``StubHouseUITests/newsFootPage(in:)`` reads it from a
/// tree, or as ``StubHouseUITests/newsFootTarget(in:)`` reads it from an
/// audit line (`frame` is then Apple's).
struct NewsFootElement: Equatable {
    let type: UInt
    let id: String
    let label: String
    let frame: CGRect
}

/// The foot of News after the audit: the window (for the picture's scale),
/// the part of the page's scroll view the navigation bar leaves uncovered,
/// and the elements the foot allowance may measure.
struct NewsFootPage: Equatable {
    let window: CGRect
    let visible: CGRect
    let elements: [NewsFootElement]
}

/// ``StubHouseUITests/newsFootJudge(_:on:read:)``'s answer for one line.
enum NewsFootVerdict: Equatable {
    /// Not a line this allowance takes; it fails the case.
    case notOurs
    /// Measured at or over 4.5:1 where drawn; the reading.
    case excused(String)
    /// Measured under 4.5:1, or unmeasurable; the reason. It fails the case.
    case kept(String)
    /// Not wholly in the visible page at this frame; bring it into view.
    case covered(CGRect)
}
