import XCTest

/// Podcast episode details (batch 2026-11-14, Android `bd84d970`), driven
/// against the stub house with this unit's `stubhouse.patch` applied.
///
/// Every case is `testPodcastDetails_…` so a train can select the unit, and
/// every piece of evidence is `podcastdetails-…`. The patch wraps
/// `fixtures/podcasts.py` rather than reseeding it, so every case in
/// `StubHouseUITests+Podcasts.swift` still finds the house it was written
/// against; what the patch adds is `position_sec` on each episode row (three
/// seeded places, none on an episode another case plays), an HTML
/// description on `Brining a bird` and on the show, and one unreadable feed in
/// the poll's answer.
///
/// ### What is not tested here, and why
///
/// **The relative date's exact words** (`yesterday`, `5 days ago`). The stub
/// stamps `published_at` as "this many seconds ago" at the read, and the app
/// reads that against the Simulator's calendar day, so a case run just after
/// midnight sees a different word from one run at noon. The words are pinned
/// by `PodcastFormatTests` against a fixed clock and two zones; these cases
/// assert the parts of the line that do not move with the clock.
///
/// **The bar's width.** It is decoration under `15 min left`, hidden from
/// VoiceOver, so it has no element to read; its percentage is
/// `PodcastFormatTests.testProgressBarIsClampedTo2And98Percent`'s.
extension StubHouseUITests {

    /// What the stub's episodes reads were asked with, from the patch's
    /// `GET /api/_test/podcast-details`. Nil when the stub did not answer,
    /// which the caller fails on with its own words.
    @MainActor
    func podcastDetailReads(port: Int = 6601) -> [[String: Any]]? {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/_test/podcast-details")!)
        request.httpMethod = "GET"
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        let answered = expectation(description: "the stub said what the episodes reads asked")
        var reads: [[String: Any]]?
        URLSession.shared.dataTask(with: request) { data, _, _ in
            if let data,
               let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                reads = object["reads"] as? [[String: Any]]
            }
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 15)
        return reads
    }

    /// An episode row, by its title and one piece of its meta line, built by
    /// scrolling the sheet if it is not built yet. Prints the tree on a miss.
    @MainActor
    func podcastDetailRow(_ app: XCUIApplication, _ title: String, _ meta: String) -> XCUIElement? {
        let row = podcastRow(app, title, meta)
        if row.exists || podcastsScrollUntilBuilt(app, row, flicks: 8) { return row }
        print("=== NO ROW \"\(title)\" SAYING \"\(meta)\" — tree follows ===")
        print(app.debugDescription)
        return nil
    }

    // MARK: - the episode row

    /// Each row says where the listener is, and the episodes read sent the
    /// slot that says it.
    ///
    /// Five rows, five different answers, and each word only one row can
    /// produce (`stubhouse.patch`'s places):
    /// * `Brining a bird` was never started — the unplayed dot, read as
    ///   `not played yet`;
    /// * `Tuesday service 01` is 600 s into 1501 — `15 min left`;
    /// * `Tuesday service 02` is 12 s from the end — `played`;
    /// * `A quiet kitchen` has a place and no length — `at 24 min`;
    /// * `Burnt ends and bad luck` tried and failed to download —
    ///   `download failed`, where the old line said `failed`.
    ///
    /// The places exist only because the read carried `device_id`: the real
    /// route answers `position_sec: null` on every row without it
    /// (`podcasts.py:352-356`), and the patch does the same. So a phone that
    /// stopped sending the slot would lose every place here, not just one —
    /// and the stub's record of the read is asserted as well, so the failure
    /// says which half broke.
    @MainActor
    func testPodcastDetails_EpisodeRowsSayWhereTheListenerIs() throws {
        resetPodcastHouse()
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")
        XCTAssertTrue(openTheKitchenTable(app), "the show's episodes never drew")
        evidence(app, "podcastdetails-10-rows")

        XCTAssertTrue(
            podcastRow(app, StubPodcastDetails.unplayedEpisode, StubPodcastDetails.notPlayedYet).exists,
            "the episode nobody has started carries no unplayed dot"
        )
        XCTAssertNotNil(
            podcastDetailRow(app, StubPodcastDetails.failedEpisode, StubPodcastDetails.downloadFailed),
            "the episode whose download failed does not say `download failed`"
        )
        XCTAssertNotNil(
            podcastDetailRow(app, StubPodcasts.noDurationEpisode, StubPodcasts.noDurationPlace),
            "the episode with a place and no length does not say `at 24 min`"
        )
        XCTAssertNotNil(
            podcastDetailRow(app, StubPodcastDetails.partEpisode, StubPodcastDetails.partLeft),
            "the half-heard episode does not say how much is left"
        )
        XCTAssertNotNil(
            podcastDetailRow(app, StubPodcastDetails.playedEpisode, StubPodcastDetails.played),
            "the finished episode does not say `played`"
        )
        // A started or finished row has no dot. `CONTAINS` on one element, so
        // it is that row's own label and not the sheet's.
        XCTAssertFalse(
            podcastRow(app, StubPodcastDetails.playedEpisode, StubPodcastDetails.notPlayedYet).exists,
            "a finished episode still carries the unplayed dot"
        )
        evidence(app, "podcastdetails-11-rows-scrolled")

        guard let reads = podcastDetailReads() else {
            return XCTFail("the stub did not answer /api/_test/podcast-details")
        }
        let sent = reads.compactMap { $0["device_id"] as? String }.filter { !$0.isEmpty }
        print("PODCASTDETAILS READS \(reads)")
        XCTAssertFalse(
            sent.isEmpty,
            "no episodes read carried a device_id, so the house could not say "
                + "where this listener is: \(reads)"
        )
    }

    // MARK: - descriptions

    /// The show's info line and both descriptions, as **plain text**.
    ///
    /// The stub's descriptions are HTML from a feed — `<p>`, `<b>`, `<i>`,
    /// an entity and a `<script>` — and the app must draw the words and none
    /// of the markup: `Salt, water, time & patience…` and nothing that says
    /// `<p>` or `&amp;`, nor the script's own words (`not drawn`). Then a tap
    /// on the two-line description opens it — the stub's runs to four or
    /// more lines on a phone — which is measured as the element growing,
    /// since a collapsed and an open `Text` carry the same label.
    @MainActor
    func testPodcastDetails_DescriptionsArePlainTextAndOpenOnATap() throws {
        resetPodcastHouse()
        let (app, landed) = openPodcasts()
        XCTAssertTrue(landed, "the shows list never answered")
        XCTAssertTrue(openTheKitchenTable(app), "the show's episodes never drew")

        // `57 episodes · updated 1h ago`: the server's count and its
        // `last_polled_at`, an hour before the read.
        let info = app.staticTexts[StubPodcastDetails.showInfo].firstMatch
        XCTAssertTrue(info.waitForExistence(timeout: 10), "the show has no info line")
        XCTAssertTrue(
            containing(app, StubPodcastDetails.showAbout).exists,
            "the show's description is not drawn as its plain sentence"
        )

        let about = containing(app, StubPodcastDetails.episodeAbout)
        guard about.waitForExistence(timeout: 10) else {
            print("=== NO EPISODE DESCRIPTION — tree follows ===")
            print(app.debugDescription)
            return XCTFail("`Brining a bird` has no description")
        }
        evidence(app, "podcastdetails-20-descriptions")
        for markup in StubPodcastDetails.markup {
            XCTAssertFalse(
                containing(app, markup).exists,
                "the feed's markup `\(markup)` reached the screen — a description "
                    + "is drawn as plain text, never as HTML"
            )
        }

        let closed = about.frame.height
        about.tap()
        var open = about.frame.height
        for _ in 0..<20 where open <= closed {
            Thread.sleep(forTimeInterval: 0.25)
            open = about.frame.height
        }
        evidence(app, "podcastdetails-21-description-open")
        print("PODCASTDETAILS DESCRIPTION closed=\(closed) open=\(open)")
        XCTAssertGreaterThan(
            open, closed,
            "a tap on the two-line description did not show the rest of it"
        )
    }
}

/// The stub's podcast-detail words, spelt out rather than imported — the
/// patch's (`design-notes/ios/batch-2026-11-14/podcasts/stubhouse.patch`)
/// and the app's (`PodcastStrings`), as every file here does.
enum StubPodcastDetails {
    static let unplayedEpisode = "Brining a bird"
    static let notPlayedYet = "not played yet"
    static let failedEpisode = "Burnt ends and bad luck"
    static let downloadFailed = "download failed"
    static let partEpisode = "Tuesday service 01"
    static let partLeft = "15 min left"
    static let playedEpisode = "Tuesday service 02"
    static let played = "played"
    static let showInfo = "57 episodes · updated 1h ago"
    static let showAbout = "Cooking, slowly, for people who are tired."
    /// The opening of `Brining a bird`'s description once its HTML is plain
    /// text: `<b>time</b> &amp;` reads `time &`.
    static let episodeAbout = "Salt, water, time & patience:"
    /// Every piece of the two descriptions' markup, none of which may be on
    /// screen.
    static let markup = ["not drawn", "<p>", "<b>", "<i>", "&amp;", "<script"]
}
