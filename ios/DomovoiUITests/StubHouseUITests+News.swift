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
    @MainActor
    func openNews(
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        port: Int = 6601,
        delay: Double? = nil
    ) -> (app: XCUIApplication, landed: Bool) {
        let app = launch(
            mode: mode,
            port: port,
            push: "News",
            pushDelay: delay ?? Self.pushDelay
        )
        let heading = newsElement(app, "news-person-heading")
        return (app, heading.waitForExistence(timeout: 40))
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
        XCTAssertFalse(
            containing(app, "marmalade").exists,
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
            containing(app, "marmalade").exists,
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
        XCTAssertTrue(
            containing(app, NewsStringsUnderTest.freeformKind).exists,
            "the new row's pill does not say what kind of topic was posted"
        )
        // The field is cleared on success only (`NewsDetail.kt:225`).
        XCTAssertEqual(
            app.textFields["news-freeform"].value as? String ?? "",
            NewsStringsUnderTest.freeformPlaceholder,
            "the field kept what was typed after a successful add"
        )

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

    /// One audit of this screen, **with the remove dialog up**.
    ///
    /// The dialog is up because that is how the modal gap shows: a finding
    /// that names an element *behind* a confirmation means the page under it
    /// is still content, which cost this app two `Contrast failed` on
    /// Settings. Everything the audit can attribute is a defect to fix;
    /// anonymous Dynamic Type findings are the measured platform floor
    /// (``floorAndRest(_:)``), and the floor is printed rather than pinned —
    /// a total would hide a new finding the moment one of the floor's went
    /// away.
    @MainActor
    func testNews_PassesApplesAccessibilityAudit() throws {
        resetNewsHouse()
        let (app, landed) = openNews()
        XCTAssertTrue(landed, "News never drew a person's heading")

        let remove = app.buttons["news-remove-topic-tech"]
        if revealNews(app, remove) {
            remove.tap()
            _ = newsElement(app, "news-remove-ask").waitForExistence(timeout: 10)
        } else {
            print("NEWS AUDIT: the remove control was not reachable — auditing without the dialog")
        }
        evidence(app, "news-12-audited")

        let found = try auditIssues(app, "news")
        let split = Self.floorAndRest(found)
        print("NEWS AUDIT floor=\(split.floor) rest=\(split.rest.count)")
        for line in split.rest { print("NEWS AUDIT   \(line)") }
        XCTAssertTrue(
            split.rest.isEmpty,
            "Apple's audit has findings against News beyond the measured "
                + "anonymous floor: " + split.rest.joined(separator: " / ")
        )
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
            for leaf in leaves {
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

    /// ``NewsStrings/noPeople`` and its sub-line.
    static let noPeople = "no people enrolled yet"
    static let noPeopleSub = "enroll a speaker on the people page first"

    /// ``NewsStrings/feedAdded``.
    static let feedAdded = "feed added"

    /// ``NewsStrings/freeformPlaceholder`` — which is also what an empty
    /// `TextField` reports as its `value`. The ellipsis is U+2026.
    static let freeformPlaceholder = "add a free-form topic (e.g. Formula 1)…"

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
