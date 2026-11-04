import XCTest

/// The Stations screen, against the stub house.
///
/// Every case is `testStations_<What>` and every piece of evidence is
/// `stations-…`, which is how a train hands these results back to the branch
/// that wrote them (NIGHT-RULES §2).
///
/// ### Bunched on purpose
///
/// Kamron's standing ask for this night is that testing not pay the
/// build-and-launch cost once per assertion. XCTest gives every case its own
/// process, so "fewer launches" can only mean **fewer, fatter cases**: each
/// one below drives one launch and asserts everything that launch can be
/// asked. Where two of MAP §9's cases could share a launch without blurring
/// what a failure means, they were merged and the merge is named in the
/// case's comment.
///
/// ### The one thing to know before reading a failure here
///
/// **Stations is a plugin route**, gated on `capStations`
/// (`Route.requiredCapability`), and `Capabilities.has` resolves a capability
/// out of `plugins[].android_capabilities` (`Capabilities.swift:75-83`). The
/// house's own `/api/capabilities` answers a flat `{"capabilities": [...]}`
/// list iOS has no key for, so `fixtures/stations.py` **wraps that route**:
/// it asks the house for its own answer and adds the `plugins` array built
/// from every fixture module's `PLUGIN` dict. That is additive and steps
/// aside the moment `stubhouse.py` grows the key itself — DECISIONS, "Needs a
/// shared change". If every case here fails with `openStations` printing a
/// tree that shows **Home**, the wrapper is what to look at first: the route
/// was invisible, so the push landed nowhere.
///
/// ### What is not tested here, and where it is tested instead
///
/// **A server switch inside a round trip** — the top defect class on this
/// track. The only way to change the address from a UI case is the app's
/// connection sheet, which NIGHT-RULES §1 forbids on a Simulator: it starts a
/// LAN discovery sweep across Kamron's real home network. The case exists in
/// the package, where the round trip can be parked:
/// `StationsModelTests.testAPlayThatLandsAfterTheHouseChangedIsDropped`.
/// [[read-after-await-wrong-key]].
///
/// **Playing.** The route slot passes `StationsPhonePlayback` over the app's
/// one player since D46, so the rows play on this phone and one tap sends
/// exactly one `POST /play`. ``testStations_TappingAStationPlaysItOnThisPhone()``
/// asserts that against the house's own request log — it is the inverted
/// descendant of `testStations_NothingOffersToPlayAndTheScreenSaysWhy`, whose
/// own comment asked for exactly this rewrite.
extension StubHouseUITests {

    // MARK: - the house, by the words on its screen

    /// The stub's own seed (`fixtures/stations.py`), spelt out here rather
    /// than imported, as every other string in this file is: a query that
    /// silently followed a renamed constant would stop testing what it
    /// claims to.
    enum StubStations {

        /// 23 favourites: `Favourite Kestrel 01`…`22`, then `WJIM-FM`, which
        /// `favorited DESC, name ASC` sorts last — so it exists only once the
        /// **second** page has been asked for.
        static let firstFavourite = "Favourite Kestrel 01"
        static let secondPageRow = "Favourite Kestrel 22"

        /// The three now-playing branches, one row each.
        static let withTrack = "Favourite Kestrel 04"
        /// Row 04's `stream_url` in the fixture (`stations.py:130`). The
        /// detail sheet shows it as the stream line's accessibility *value*,
        /// never as its label — see ``StationsWords/streamURLLabel``.
        static let withTrackStream = "http://stream.example/kestrel04"
        static let trackText = "Sonora Pine"
        static let icyRefused = "Favourite Kestrel 05"
        static let neverProbed = "Favourite Kestrel 06"

        /// Played, never favourited — the Recent star has something to
        /// promote, and the favourites list must not hold it.
        static let playedOnly = "Played Once Gull"

        /// Its Recent line, from the fixture's `_played_ago`.
        static let playedAgo = "2m ago"

        /// The FM favourite: 97.5, no `stream_url`, so `resolve` is offered.
        static let fmRow = "WJIM-FM"

        /// An FM row that is **not** favourited, so the FM browse has
        /// something to answer that the favourites list does not hold — and
        /// 88.5, so the exact-frequency filter has something to exclude.
        static let unfavouritedFm = "KQED-FM"

        /// The catalogue. `01` is a fresh `id == 0` hit.
        static let freshHit = "Catalogue Ibis 01"

        /// Kestrel 04's four detections, one per branch of the source pill.
        static let localDetection = "Red Fire Serpent"
        static let icyDetection = "Quills of the Arrow"
        static let namelessArtist = "Thirteen Palisades"
        static let unknownSourceArtist = "Velvet Caiman"

        /// The band, from this module's `PLUGIN` dict.
        static let pluginName = "Radio Stations"
        static let pluginBy = "by Coders Farm"
        static let pluginBadge = "plugin"
    }

    /// The app's own words, spelt out for the same reason.
    enum StationsWords {
        static let title = "Stations"
        /// **With** Android's `· tap a station to play it` since D46: the
        /// rows play on this phone, so the one instruction on the page is
        /// true. The shorter sentence
        /// (``StationsStrings/subtitleWithoutPlaying(favorites:recent:)``) is
        /// a build with no audio engine, which no phone is — the device id
        /// the player is keyed to is minted locally on first launch, not
        /// handed out by a house (review 2 of the 2026-11-03 batch).
        static let subtitle = "23 favorited · 3 recent · tap a station to play it"
        static let subtitleAfterAStar = "24 favorited · 3 recent · tap a station to play it"
        static let subtitleAfterTwoStars = "25 favorited · 3 recent · tap a station to play it"
        static let subtitleAfterAForget = "22 favorited · 3 recent"
        static let playingTail = "tap a station to play it"
        /// `StationsStrings.playingNeedsAHouse`, **which no build contains
        /// any more**: review 2 of the 2026-11-03 batch deleted it, because
        /// it named a cause that does not exist (the device id is minted
        /// locally) for a state no phone reaches.
        ///
        /// Kept as a query on purpose, and it is the one assertion here that
        /// is deliberately vacuous today: the sentence coming back is how a
        /// build that re-grew the premise would announce itself, and these
        /// words are the only handle on that. It is spelt out rather than
        /// imported for the reason every string in this file is.
        static let noPlayback =
            "a station plays here once this phone has joined a domovoi — favoriting, sampling and detections all work"
        static let importIsAnAdminJob =
            "importing the FCC FM list is an admin job — do it on the dashboard"
        static let recent = "Recent"
        static let lastThree = "last 3"
        static let favorites = "Favorites"
        static let noFavorites = "no favorites yet"
        static let noFavoritesSub = "search and tap the star to start collecting"
        static let nothingPlayed = "nothing played yet"
        static let unreachable = "couldn't reach the server"
        static let retry = "retry"
        static let loadMore = "load more"
        static let fmScope = "local fm"
        static let queryHint = "search stations — name, callsign, network…"
        static let fmQueryHint = "name, call sign, city, or frequency (e.g. 97.5)"
        /// The first clause of the FM tip. Matched with ``startingWith`` —
        /// the whole sentence is past the 128-character query limit that
        /// throws (NIGHT-RULES §5).
        static let fmTipOpening = "Tip: leave the box blank"
        /// The search card's tag strip (`StationsStrings.tagsLabel`,
        /// `tagPresets`). Spelt out here for the same reason as everything
        /// else in this enum.
        static let tagsLabel = "tags:"
        static let tagPresets = ["indie", "jazz", "classical", "news", "electronic", "rock"]
        static let search = "search"
        /// The submit button's word in the FM scope (`StationSearch.kt:222`).
        static let browse = "browse"
        /// `fmHead` for the two FM rows: the call sign leads, then the
        /// frequency.
        static let wjimHead = "WJIM · 97.5 FM"
        static let lansing = "Lansing, MI"
        static let noStreamURL = "no stream URL — polling can't reach this station"
        /// What the line that *shows* a URL is called out loud
        /// (`StationsStrings.streamURLA11y`). Its visible text is the URL.
        static let streamURLLabel = "stream URL"
        static let resolve = "resolve"
        static let pasteURL = "paste url"
        static let foundSimulcast = "found simulcast for WJIM"
        static let yourFavorites = "your favorites"
        static let oneMatch = "1 match"
        static let noMatch = "no stations match"
        static let saved = "saved"
        static let favorite = "favorite"
        static let unfavorite = "unfavorite"
        static let openDetails = "details and detections"
        static let hideDetails = "hide details"
        static let noLiveMetadata = "no live metadata"
        static let listening = "listening…"
        static let recentDetections = "recent detections"
        static let unknownArtist = "unknown artist"
        static let localPill = "local"
        static let inLibrary = "in library"
        static let newPill = "new"
        static let interval = "interval"
        static let save = "save"
        static let cancel = "cancel"
        static let intervalRefused = "interval must be between 30 and 86400 s"
        static let intervalSaved = "Favourite Kestrel 04: sampling every 600s"
        static let metaAt600 = "sampling every 600s"
        static let metaAt180 = "sampling every 180s"
        static let forget = "forget"
        /// The toast a completed `forget` leaves (`StationsStrings.forgot`).
        static let forgotKestrel04 = "forgot Favourite Kestrel 04"
        static let forgetTitle = "forget Favourite Kestrel 04?"
        static let forgetBody = "Removes the station and stops sampling it."
    }

    // MARK: - getting there

    /// Reset the fixtures, choose what the reads answer, and push Stations.
    ///
    /// **Reset first, always.** The cases share one house for the whole run
    /// and half of them write to it, so a case that relied on the seed
    /// without resetting would pass or fail on which case ran before it.
    ///
    /// **What it waits for.** The subtitle, `23 favorited · 3 recent`, which
    /// cannot exist until `/badge` and `/recent` have both answered and been
    /// decoded — a marker only a *completed read* can produce, which is the
    /// lesson `houseName(in:)` carries. It is the second line of the screen,
    /// so it is built at every type size. The two broken-house launches pass
    /// their own marker.
    @MainActor
    func openStations(
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        empty: Bool = false,
        fail: Bool = false,
        waitingFor marker: String? = nil,
        delay: Double? = nil
    ) -> (app: XCUIApplication, landed: Bool) {
        control("reset-fixtures", "{}", port: 6601, what: "the stations fixture")
        if empty {
            control("stations-empty", "{\"empty\": true}", port: 6601, what: "an empty house")
        }
        if fail {
            control("stations-fail", "{\"fail\": true}", port: 6601, what: "a failing stations read")
        }
        let app = launch(mode: mode, push: "Stations", pushDelay: delay ?? Self.pushDelay)
        let wanted = marker ?? StationsWords.subtitle
        let landed = app.staticTexts[wanted].waitForExistence(timeout: 40)
        if !landed {
            // The tree, not a sentence about the element: when the route is
            // invisible this shows Home, which is the whole diagnosis.
            print("=== STATIONS DID NOT DRAW (waited for \(wanted)) — tree follows ===")
            print(app.debugDescription)
        }
        return (app, landed)
    }

    /// Flick the page until an element is built, and say whether it was.
    ///
    /// This screen is a `ScrollView` + `LazyVStack`, not a `List`, so there is
    /// no `collectionView` for ``scrollUntilBuilt(_:_:flicks:)`` to drive. A
    /// lazy list's foot is **unbuilt**, not off screen, so this is the only
    /// way page two is ever asked for.
    ///
    /// **`.slow`, never a bare `swipeUp()`.** `scrollUntilBuilt` measured the
    /// default velocity and rejected it (`StubHouseUITests.swift:2735-2746`:
    /// a flick at the default velocity is worth about a viewport, and eight
    /// of them scrolled *past* the row it was looking for), which is the same
    /// thing NIGHT-RULES §5 says. Momentum past row 19 is momentum past the
    /// `onAppear` that asks for page two, so the bare gesture fails this
    /// screen's paging case for a reason that is the gesture's. Twenty-four
    /// flicks because a slow one moves a fraction of a viewport and the
    /// favourites sit below the band, the header, the search card and Recent.
    @MainActor
    func scrollStationsUntil(_ app: XCUIApplication, _ element: XCUIElement, flicks: Int = 24) -> Bool {
        for _ in 0..<flicks {
            if element.exists { return true }
            app.swipeUp(velocity: .slow)
        }
        if !element.exists {
            print("=== \(flicks) FLICKS AND STILL UNBUILT — tree follows ===")
            print(app.debugDescription)
        }
        return element.exists
    }

    /// A row's star, picked by **label and value**.
    ///
    /// `favorite` is the same word on every unfavourited row, and
    /// `firstMatch` on it would be whichever row the tree happens to hold
    /// first — the trap `openChatRowMenu` was written for one screen earlier.
    /// The station's name is the control's accessibility **value**.
    @MainActor
    func stationsStar(_ app: XCUIApplication, _ name: String, favorited: Bool) -> XCUIElement {
        app.buttons
            .matching(NSPredicate(
                format: "label == %@ AND value == %@",
                favorited ? StationsWords.unfavorite : StationsWords.favorite,
                name
            ))
            .firstMatch
    }

    /// A favourite's chevron, picked the same way.
    @MainActor
    func stationsChevron(_ app: XCUIApplication, _ name: String) -> XCUIElement {
        app.buttons
            .matching(NSPredicate(
                format: "label == %@ AND value == %@",
                StationsWords.openDetails, name
            ))
            .firstMatch
    }

    /// Everything the stub house was asked to write, from the test process.
    ///
    /// Open, and a GET, so this does not reuse ``control(_:_:port:what:)``
    /// (which POSTs with a device token). The Simulator shares the Mac's
    /// loopback, which is how the app reaches the stub, so the test process
    /// can reach it too (``clearWatched`` says why).
    @MainActor
    func stationsSent(port: Int = 6601) -> [String: [Any]] {
        let request = URLRequest(
            url: URL(string: "http://127.0.0.1:\(port)/api/_test/stations-sent")!
        )
        let answered = expectation(description: "the stub listed what it was sent")
        var body = Data()
        URLSession.shared.dataTask(with: request) { data, _, _ in
            body = data ?? Data()
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 20)
        let object = try? JSONSerialization.jsonObject(with: body)
        return (object as? [String: [Any]]) ?? [:]
    }

    /// Every **row** on the screen that is about this station, as its star.
    ///
    /// Counting leaves whose label holds a name counts a row twice: a row's
    /// lines are combined into one element and the tree keeps the leaves
    /// under it as well (train S004). A row has exactly one
    /// ``StationsStarButton``, labelled `favorite` or `unfavorite` and valued
    /// with the station's name, so this is "how many rows are about it" and
    /// nothing else. Returned as strings so a failure message names what it
    /// found rather than a number.
    @MainActor
    func stationsRows(_ app: XCUIApplication, named name: String) -> [String] {
        // `value` is `Any?`, so it is read in Swift rather than asked for in
        // a predicate, where a type mismatch would quietly match nothing.
        let stars = app.buttons.matching(
            NSPredicate(
                format: "label == %@ OR label == %@",
                StationsWords.favorite, StationsWords.unfavorite
            )
        )
        return stars.allElementsBoundByIndex
            .filter { ($0.value as? String) == name }
            .map { "\($0.label) \($0.frame)" }
    }

    /// A control inside the detail sheet, told apart from **the keyboard's
    /// keys**.
    ///
    /// The interval box opens a `numberPad`, which covers the bottom of the
    /// sheet, and `firstMatch` takes an element behind a keyboard as happily
    /// as one in front of it — a key *is* hittable, so `isHittable` would not
    /// have saved it either. Bound by `keyboards.firstMatch.frame.minY`,
    /// which is the lesson `composerSend` carries.
    @MainActor
    func stationsSheetButton(_ app: XCUIApplication, _ label: String) -> XCUIElement? {
        let keyboard = app.keyboards.firstMatch
        let top = keyboard.exists ? keyboard.frame.minY : .greatestFiniteMagnitude
        let all = app.buttons.matching(NSPredicate(format: "label == %@", label))
        guard all.firstMatch.waitForExistence(timeout: 20) else {
            print("=== NO \(label) IN THE SHEET — tree follows ===")
            print(app.debugDescription)
            return nil
        }
        return all.allElementsBoundByIndex.first { $0.frame.height > 0 && $0.frame.maxY < top }
    }

    // MARK: - 1. the three cards

    /// The screen draws the band, the header, Recent and the favourites.
    ///
    /// What this would catch: the slot still drawing `ScreenPlaceholder`; a
    /// band built from a manifest nothing decoded; a `/badge` or `/recent`
    /// answer that never landed; the favourites list pointed at the wrong
    /// filter.
    ///
    /// **`Played Once Gull` is the discriminating row.** It is in Recent and
    /// is *not* favourited, so it must appear in exactly **one row** on the
    /// screen: a favourites list that forgot `favorited_only=true` — or a
    /// screen that drew the same rows twice under two headings — fails here
    /// and passes every assertion that only looked for a name.
    ///
    /// **Rows are counted by their star, not by leaves with the name on
    /// them.** Train S004 failed this on a screen that was right: a
    /// ``StationsRecentRow``'s two lines are one accessibility element
    /// (`children: .combine`), and the tree holds *both* the combined
    /// `Played Once Gull, 2m ago` **and** the `Played Once Gull` leaf under
    /// it — two matches for one row. Every row of either list carries exactly
    /// one ``StationsStarButton``, whose `value` is the station's name, so
    /// counting those counts rows.
    @MainActor
    func testStations_TheScreenDrawsItsThreeCards() throws {
        let (app, landed) = openStations()
        XCTAssertTrue(landed, "Stations never drew its subtitle")
        XCTAssertFalse(fellBackToLocalMedia(in: app), "the shell gave up on the server")
        evidence(app, "stations-10-three-cards")

        // The plugin band — the screen says whose screen it is.
        XCTAssertTrue(
            app.staticTexts[StubStations.pluginName].exists,
            "the plugin band drew no name: the manifest's `plugins` never decoded"
        )
        XCTAssertTrue(app.staticTexts[StubStations.pluginBy].exists, "the band lost its publisher")
        XCTAssertTrue(app.staticTexts[StubStations.pluginBadge].exists, "the band lost its badge")

        XCTAssertTrue(app.staticTexts[StationsWords.title].exists, "no title")
        XCTAssertTrue(app.staticTexts[StationsWords.recent].exists, "no Recent card")
        XCTAssertTrue(app.staticTexts[StationsWords.lastThree].exists, "Recent lost its count")
        XCTAssertTrue(app.staticTexts[StationsWords.favorites].exists, "no Favorites heading")

        XCTAssertTrue(
            containing(app, StubStations.firstFavourite).exists,
            "the favourites list drew nothing"
        )
        XCTAssertTrue(
            containing(app, StubStations.playedOnly).exists,
            "\(StubStations.playedOnly) is not on the screen at all, so Recent is not "
                + "drawing the station that was played and never starred"
        )
        let rows = stationsRows(app, named: StubStations.playedOnly)
        XCTAssertEqual(
            1, rows.count,
            "\(StubStations.playedOnly) is played-but-not-favourited and must be one row, "
                + "in Recent only — the favourites read is not filtered on "
                + "favorited_only. Rows: \(rows)"
        )
    }

    // MARK: - 2. the three now-playing sentences

    /// `now_playing`, `icy_supported == false` and `icy_supported == null` are
    /// three different sentences, and this is the case that stops them
    /// collapsing into one.
    ///
    /// **Nil is not false**: never probed is `listening…`, and a screen that
    /// treated the two the same would tell somebody their station has no
    /// metadata before anything had looked. Asserted per row, off the row's
    /// own combined label, so "the words are on the screen somewhere" cannot
    /// pass for "the words are on the right row".
    @MainActor
    func testStations_NowPlayingTellsItsThreeStatesApart() throws {
        let (app, landed) = openStations()
        XCTAssertTrue(landed, "Stations never drew its subtitle")

        let withTrack = containing(app, StubStations.withTrack)
        let refused = containing(app, StubStations.icyRefused)
        let probing = containing(app, StubStations.neverProbed)
        XCTAssertTrue(withTrack.exists && refused.exists && probing.exists,
                      "the first page is missing one of rows 04, 05, 06")
        evidence(app, "stations-11-now-playing")

        XCTAssertTrue(
            withTrack.label.contains(StubStations.trackText),
            "row 04 does not say what is playing: \(withTrack.label)"
        )
        XCTAssertFalse(
            withTrack.label.contains(StationsWords.noLiveMetadata),
            "row 04 has a track AND says there is no live metadata: \(withTrack.label)"
        )
        XCTAssertTrue(
            refused.label.contains(StationsWords.noLiveMetadata),
            "row 05 has icy_supported == false and does not say so: \(refused.label)"
        )
        XCTAssertTrue(
            probing.label.contains(StationsWords.listening),
            "row 06 was never probed and does not say listening: \(probing.label)"
        )
        XCTAssertFalse(
            probing.label.contains(StationsWords.noLiveMetadata),
            "row 06's null icy_supported was read as false: \(probing.label)"
        )
        // The meta line, on the same label: the sampling interval is what the
        // interval case later changes.
        XCTAssertTrue(
            withTrack.label.contains(StationsWords.metaAt180),
            "row 04 lost its meta line: \(withTrack.label)"
        )
    }

    // MARK: - 3. a tap plays it, here

    /// A station plays **on this phone**, and the screen says so where it
    /// used to apologise.
    ///
    /// This case was `testStations_NothingOffersToPlayAndTheScreenSaysWhy`,
    /// and it was the measured fact that this screen drew no play affordance
    /// anywhere. D46 landed `StationsPhonePlayback` over
    /// ``ShellModel/player``, so it is inverted rather than deleted — every
    /// assertion in it is the opposite of what it was, which is what the old
    /// comment ("whoever moves it there rewrites it") asked for.
    ///
    /// Three halves, and the third is still the one that matters: the house's
    /// own request log must now hold **exactly one `POST /play`** after one
    /// row has been tapped. That POST is what stamps a station into Recent,
    /// and the model sends it *before* it hands the row to the player — so
    /// one tap is one history entry and one stream, not two of either.
    @MainActor
    func testStations_TappingAStationPlaysItOnThisPhone() throws {
        let (app, landed) = openStations()
        XCTAssertTrue(landed, "Stations never drew its subtitle")

        // The sentence is deleted from the app (review 2), so this cannot
        // fail on today's build. It stays as the tripwire for the premise
        // coming back: a branch that re-grows "a station plays here once this
        // phone has joined a domovoi" fails here rather than shipping it.
        XCTAssertFalse(
            app.staticTexts[StationsWords.noPlayback].exists,
            "the screen says a station needs a house to play — a premise review 2 measured false"
        )
        // Android's subtitle ends `· tap a station to play it`, and now that
        // is true, the page says it. `landed` already matched the whole
        // sentence; this names the tail so a failure says which half went.
        XCTAssertTrue(
            containing(app, StationsWords.playingTail).exists,
            "the subtitle dropped the one instruction on the page"
        )

        // One row, not two. The old case tapped a favourite and a Recent row
        // to prove neither did anything; now that each one starts a stream,
        // two taps would be two streams and the second would stop the first
        // — and `/play` would have been sent twice, which is what the count
        // below is checking.
        containing(app, StubStations.firstFavourite).tap()
        evidence(app, "stations-12-a-tap-plays-here")

        let sent = stationsSent()
        XCTAssertEqual(
            1, (sent["play"] ?? []).count,
            "one tap on a playable row must be exactly one POST /play: \(sent)"
        )
        XCTAssertTrue(
            "\(sent["play"] ?? [])".contains(StubStations.firstFavourite),
            "the house was told to play something other than the row that was tapped: \(sent)"
        )
        XCTAssertEqual(
            XCUIApplication.State.runningForeground, app.state,
            "the app went away while a station started — "
                + StubHouseUITests.stateName(app.state)
        )
    }

    // MARK: - 4. the control that would 401

    /// There is no `import fcc fm` control, **and** the screen says where the
    /// import is done.
    ///
    /// Both halves, because a case that only asserted the absence would pass
    /// against a blank screen. `POST /fcc-import` sits on the radio module's
    /// admin default (`web.py:38-40`) and this app has no admin session, so
    /// the button would 401 from every phone every time
    /// ([[no-misleading-controls]]). The fixture answers that 401 honestly,
    /// and the request log is asked whether anything went looking for it.
    @MainActor
    func testStations_TheScreenHasNoFccImportControl() throws {
        let (app, landed) = openStations()
        XCTAssertTrue(landed, "Stations never drew its subtitle")
        evidence(app, "stations-13-no-fcc-control")

        let fcc = app.descendants(matching: .button)
            .matching(NSPredicate(format: "label CONTAINS[c] %@", "fcc"))
        XCTAssertEqual(
            0, fcc.count,
            "a control mentioning fcc is drawn, and every phone's tap on it is a 401"
        )
        XCTAssertTrue(
            app.staticTexts[StationsWords.importIsAnAdminJob].exists,
            "nothing says where the FCC import is done, so the absence reads as a bug"
        )
        XCTAssertEqual(
            0, (stationsSent()["fcc"] ?? []).count,
            "something asked the admin-only import route anyway"
        )
    }

    // MARK: - 5. the list loads as you scroll

    /// Page two is asked for when the foot of the list is reached, and not
    /// before.
    ///
    /// 23 favourites and a page of 20, so `Favourite Kestrel 22` and
    /// `WJIM-FM` exist **only** after the second page has landed — and a
    /// lazy list's foot is unbuilt, not off screen, so nothing but scrolling
    /// gets there. The first assertion is that it was *not* there to begin
    /// with: a screen that loaded everything at once would pass the second
    /// assertion on its own.
    @MainActor
    func testStations_TheFavouritesListLoadsAsYouScroll() throws {
        let (app, landed) = openStations()
        XCTAssertTrue(landed, "Stations never drew its subtitle")

        XCTAssertFalse(
            containing(app, StubStations.secondPageRow).exists,
            "row 22 is on screen before anything scrolled, so the list asked for "
                + "everything at once and the page size means nothing"
        )
        let reached = scrollStationsUntil(app, containing(app, StubStations.secondPageRow))
        evidence(app, "stations-14-second-page")
        XCTAssertTrue(reached, "the second page never loaded")
        XCTAssertTrue(
            containing(app, StubStations.fmRow).exists,
            "the last favourite (\(StubStations.fmRow)) is missing from page two"
        )
        // And the foot's own word goes away when there is nothing more: page
        // two answered 3 of a page of 20, so `canLoadMore` is false. It
        // cannot be asserted *present* from here — the foot of a lazy list is
        // unbuilt until it is scrolled to, and scrolling to it is what loads
        // the page that removes it.
        XCTAssertFalse(
            app.buttons[StationsWords.loadMore].exists,
            "the list still offers `load more` with all 23 favourites loaded, so the "
                + "short last page was read as a full one"
        )
    }

    // MARK: - 5b. the FM half of the screen, and the stream editor

    /// The other half of the search card, and the one field with three faces
    /// — one launch, because reaching the second is a scroll away from the
    /// first.
    ///
    /// Nothing else in this file touches the FM scope or the stream editor,
    /// so without this case a break in either passes every assertion: the
    /// scope switch, the FM placeholder, the tip, the `browse` word, the
    /// `WJIM · 97.5 FM` head, the market line, the exact-frequency path, the
    /// `no stations match` state, the empty stream face and `resolve` are all
    /// drawn only here.
    ///
    /// **What discriminates what.** `KQED-FM` is the FM row that is *not*
    /// favourited, so its presence is the browse reading
    /// `/stations?source=fm` rather than the favourites; `Catalogue Ibis` is
    /// in no FM row, so its absence is the scope not having gone to
    /// `/search`; and `KQED` going away when `97.5` is typed is the number
    /// being sent as `frequency_mhz=` and not as a `q` ILIKE, which `88.5`
    /// would survive.
    ///
    /// Return, not the button, submits the two typed browses: it runs the
    /// search *and* puts the keyboard away, and the chevron this case taps
    /// later sits where the keyboard was.
    @MainActor
    func testStations_TheFmScopeBrowsesTheImportAndOffersAResolve() throws {
        let (app, landed) = openStations()
        XCTAssertTrue(landed, "Stations never drew its subtitle")

        // --- the scope switch changes the box, the tip and the button.
        let fm = app.buttons[StationsWords.fmScope]
        XCTAssertTrue(fm.waitForExistence(timeout: 20), "there is no local-fm scope button")
        fm.tap()
        XCTAssertTrue(
            startingWith(app, StationsWords.fmTipOpening).waitForExistence(timeout: 20),
            "the FM tip never drew, so the scope did not switch"
        )
        XCTAssertTrue(
            app.textFields[StationsWords.fmQueryHint].waitForExistence(timeout: 20),
            "the query box kept the online placeholder after the scope switched"
        )
        let browse = app.buttons[StationsWords.browse]
        XCTAssertTrue(browse.waitForExistence(timeout: 20), "the submit button does not say browse")
        evidence(app, "stations-29-fm-scope")

        // --- browse with an empty box walks the whole import.
        browse.tap()
        XCTAssertTrue(
            containing(app, StationsWords.wjimHead).waitForExistence(timeout: 25),
            "an empty-box browse answered nothing: the FM scope is not reading "
                + "/stations?source=fm"
        )
        XCTAssertTrue(
            containing(app, StationsWords.lansing).exists,
            "the FM row drew no market, so its third line is missing"
        )
        XCTAssertTrue(
            containing(app, StubStations.unfavouritedFm).exists,
            "\(StubStations.unfavouritedFm) is missing: the browse answered the favourites "
                + "rather than every FM row"
        )
        XCTAssertFalse(
            containing(app, "Catalogue Ibis").exists,
            "an online catalogue hit is in the FM results, so the scope went to /search"
        )
        evidence(app, "stations-30-fm-browse")

        // --- a number is an EXACT frequency, not a `q`.
        let box = app.textFields[StationsWords.fmQueryHint]
        box.tap()
        app.typeText("97.5\n")
        XCTAssertTrue(
            containing(app, StationsWords.wjimHead).waitForExistence(timeout: 25),
            "97.5 found nothing, and \(StubStations.fmRow) is on 97.5"
        )
        XCTAssertFalse(
            containing(app, StubStations.unfavouritedFm).exists,
            "88.5 survived a 97.5 filter: the number was sent as q= rather than as "
                + "frequency_mhz="
        )
        evidence(app, "stations-31-fm-frequency")

        // --- and a frequency nothing is on is an empty state, not a blank.
        box.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
        clearField(app)
        app.typeText("88.3\n")
        XCTAssertTrue(
            app.staticTexts[StationsWords.noMatch].waitForExistence(timeout: 25),
            "a frequency nothing is on drew no empty state, so a browse that found "
                + "nothing is indistinguishable from one that failed"
        )
        evidence(app, "stations-32-fm-no-match")

        // --- the stream field's empty face, on the one favourite the FCC
        //     import left without a URL. It sorts last, so this is below the
        //     second page of the favourites list.
        let chevron = stationsChevron(app, StubStations.fmRow)
        XCTAssertTrue(
            scrollStationsUntil(app, chevron),
            "\(StubStations.fmRow) never built in the favourites, so the stream editor "
                + "was never reached"
        )
        chevron.tap()
        XCTAssertTrue(
            app.staticTexts[StationsWords.recentDetections].waitForExistence(timeout: 25),
            "the chevron did not open \(StubStations.fmRow)'s detail"
        )
        XCTAssertTrue(
            containing(app, StationsWords.noStreamURL).exists,
            "a station with no stream_url does not say so: the field's empty face never drew"
        )
        XCTAssertTrue(
            app.buttons[StationsWords.pasteURL].exists,
            "the empty face offers no paste url, so there is no way to type one in"
        )
        let resolve = app.buttons[StationsWords.resolve]
        XCTAssertTrue(
            resolve.exists,
            "an FM row with a call sign offers no resolve, which is the only automatic "
                + "way it ever gets a URL"
        )
        evidence(app, "stations-33-stream-empty")

        // --- resolve, and what the house was asked.
        resolve.tap()
        XCTAssertTrue(
            containing(app, StationsWords.foundSimulcast).waitForExistence(timeout: 30),
            "resolve said nothing: the simulcast answer landed nowhere"
        )
        evidence(app, "stations-34-resolved")
        XCTAssertEqual(
            1, (stationsSent()["resolve"] ?? []).count,
            "the resolve POST was not sent exactly once: \(stationsSent())"
        )
    }

    // MARK: - 6. the favourites answer before the directory does

    /// What is typed peeks at the **favourites** after 220 ms, and does not
    /// touch the directory until the search is submitted
    /// (`StationSearch.kt:112`).
    ///
    /// Discriminating both ways: the favourite's own name appears without
    /// anything being submitted, and no catalogue row — `Catalogue Ibis` is
    /// in no favourite's name — is anywhere on the screen. A debounce that
    /// was dropped, or an instant search that went to `/search`, fails one of
    /// the two.
    @MainActor
    func testStations_SearchingFindsFavouritesBeforeTheDirectory() throws {
        let (app, landed) = openStations()
        XCTAssertTrue(landed, "Stations never drew its subtitle")

        let field = app.textFields[StationsWords.queryHint]
        XCTAssertTrue(field.waitForExistence(timeout: 20), "no query box")
        field.tap()
        app.typeText("Kestrel 04")

        XCTAssertTrue(
            app.staticTexts[StationsWords.yourFavorites].waitForExistence(timeout: 20),
            "the favourites peek never arrived — the 220 ms debounce or its read is gone"
        )
        evidence(app, "stations-15-instant-favourites")
        XCTAssertTrue(
            app.staticTexts[StationsWords.oneMatch].exists,
            "the peek found something other than the one favourite called Kestrel 04"
        )
        XCTAssertFalse(
            containing(app, "Catalogue Ibis").exists,
            "a catalogue row is on screen and nothing was submitted: the directory is "
                + "being asked per keystroke"
        )
    }

    // MARK: - 7. the star, which is the one thing that favourites

    /// Two stars, two mechanisms, one launch.
    ///
    /// A **Recent** row's star is always a PATCH — the row is in the table
    /// because `POST /play` put it there — and it moves the count from
    /// `/badge`. A **search** row's star on a fresh `id == 0` hit is a POST
    /// that saves and favourites, and **the id that POST answers has to be
    /// kept**: the second tap is a PATCH of that id, and the bug the plugin
    /// board opened as F-P013 is the star that never comes off because the id
    /// was dropped. Both are here because both are "what the star does", and
    /// one launch can ask both.
    @MainActor
    func testStations_TheStarIsWhatFavourites() throws {
        let (app, landed) = openStations()
        XCTAssertTrue(landed, "Stations never drew its subtitle")

        // --- the Recent row: a PATCH, and the count moves.
        let gullStar = stationsStar(app, StubStations.playedOnly, favorited: false)
        XCTAssertTrue(gullStar.waitForExistence(timeout: 20), "no star on the Recent row")
        gullStar.tap()
        XCTAssertTrue(
            app.staticTexts[StationsWords.subtitleAfterAStar].waitForExistence(timeout: 25),
            "the favourites count did not move after a star in Recent"
        )
        evidence(app, "stations-16-recent-starred")

        // --- the search row: a POST that keeps its id.
        let field = app.textFields[StationsWords.queryHint]
        field.tap()
        app.typeText(StubStations.freshHit)
        // Return runs the search: a single-line field, so `submitLabel(.search)`
        // is honest and `onSubmit` fires (the multi-line trap does not apply).
        app.typeText("\n")

        let hit = containing(app, StubStations.freshHit)
        XCTAssertTrue(hit.waitForExistence(timeout: 25), "the search found nothing")
        let hitStar = stationsStar(app, StubStations.freshHit, favorited: false)
        XCTAssertTrue(hitStar.waitForExistence(timeout: 20), "the search row has no star")
        hitStar.tap()
        XCTAssertTrue(
            app.staticTexts[StationsWords.saved].waitForExistence(timeout: 25),
            "starring a fresh hit did not mark it saved"
        )
        // 25, not 24: the count has to move for the POST, or the assertion
        // after the second tap could not tell 24 from a 24 that never changed.
        XCTAssertTrue(
            app.staticTexts[StationsWords.subtitleAfterTwoStars].waitForExistence(timeout: 25),
            "the count did not move when a fresh hit was saved"
        )
        evidence(app, "stations-17-hit-saved")

        // The second tap is the one F-P013 is about.
        let unstar = stationsStar(app, StubStations.freshHit, favorited: true)
        XCTAssertTrue(unstar.waitForExistence(timeout: 20), "the starred row's star did not flip")
        unstar.tap()
        XCTAssertTrue(
            app.staticTexts[StationsWords.subtitleAfterAStar].waitForExistence(timeout: 25),
            "the second tap did nothing: the id the POST answered was dropped, so the "
                + "star POSTed again instead of unfavouriting (plugin board F-P013)"
        )
        evidence(app, "stations-18-hit-unsaved")
        XCTAssertFalse(
            app.staticTexts[StationsWords.saved].exists,
            "the row still says saved after being unfavourited"
        )
    }

    // MARK: - 8. the detail sheet

    /// The chevron opens the detail, the feed draws what the fingerprinter
    /// matched, and the sampling interval saves — one launch, because they
    /// are one sheet.
    ///
    /// The feed is the discriminating part: four detections, and each is a
    /// different branch. `dejavu_local` becomes `local` and is `in library`;
    /// `icy` is `new`; a null source is `—`; and **`shazam` passes through
    /// verbatim**, because the source vocabulary is the plugin's and not this
    /// app's — a screen that mapped unknown sources to a placeholder would
    /// fail on that one alone.
    ///
    /// Then the interval: `600` saves and the row's own meta line says so
    /// (the PATCH is applied in place, three pages from where it was typed),
    /// and `10` is refused by the client against the server's own bounds so a
    /// typo is a sentence rather than a 422.
    @MainActor
    func testStations_TheDetailSheetShowsTheFeedAndSavesTheInterval() throws {
        let (app, landed) = openStations()
        XCTAssertTrue(landed, "Stations never drew its subtitle")

        let chevron = stationsChevron(app, StubStations.withTrack)
        XCTAssertTrue(chevron.waitForExistence(timeout: 20), "row 04 has no chevron")
        chevron.tap()
        XCTAssertTrue(
            app.staticTexts[StationsWords.recentDetections].waitForExistence(timeout: 25),
            "the chevron did not open the detail — if the row played instead, the two "
                + "taps have been collapsed into one"
        )
        evidence(app, "stations-19-detail-sheet")

        XCTAssertTrue(containing(app, StubStations.localDetection).exists, "no local detection")
        XCTAssertTrue(containing(app, StubStations.icyDetection).exists, "no icy detection")
        // The unknown-artist branch, off the **meta** line and not the title:
        // `Thirteen Palisades` is the title of the detection whose artist is
        // null, so a screen that drew `artist ?? ""` would pass an assertion
        // on the title alone. The title and the meta are one element — the
        // inner `VStack` of `StationsDetectionRow` combines its children — so
        // this reads both off one label.
        let nameless = containing(app, StubStations.namelessArtist)
        XCTAssertTrue(
            nameless.exists,
            "the detection with no artist is missing, so the unknown-artist branch never drew"
        )
        XCTAssertTrue(
            nameless.label.contains(StationsWords.unknownArtist),
            "the detection with no artist does not say unknown artist: \(nameless.label)"
        )
        // The fourth branch of the source pill: `fingerprint_source: null`
        // becomes `—` (`StationsRules.detectionSource`). Two `—` are expected
        // in this sheet and nothing else in it is empty — detection 4001 has
        // no title, detection 4002 no source, and row 104's own five fields
        // are all populated in the fixture — so a null source mapped to a
        // word of its own drops this to one.
        let dashes = app.staticTexts.matching(NSPredicate(format: "label == %@", "—"))
        XCTAssertGreaterThanOrEqual(
            dashes.count, 2,
            "a null fingerprint_source did not become the — pill: only "
                + "\(dashes.count) em-dash leaves in the sheet, and the missing title "
                + "accounts for one of them"
        )
        // ...and the `shazam` row is tied to its artist, not only to the pill
        // word: `Velvet Caiman` is the detection whose title is null, so this
        // is also the branch where the title falls back to `—`.
        XCTAssertTrue(
            containing(app, StubStations.unknownSourceArtist).exists,
            "the detection with an unknown source is missing its artist, so the "
                + "verbatim-source row was never drawn"
        )
        XCTAssertTrue(app.staticTexts[StationsWords.localPill].exists, "no local pill")
        XCTAssertTrue(app.staticTexts[StationsWords.inLibrary].exists, "no in-library pill")
        XCTAssertTrue(app.staticTexts[StationsWords.newPill].exists, "no new pill")
        XCTAssertTrue(
            app.staticTexts["shazam"].exists,
            "an unknown fingerprint source did not pass through verbatim"
        )
        XCTAssertTrue(
            app.staticTexts["icy"].exists,
            "a known-but-not-local source did not pass through verbatim"
        )

        // --- the stream line is a named value, not a bare URL
        //
        // Train S007's audit of this sheet reported "Label not human-readable"
        // against `http://stream.example/kestrel04`: the element's whole
        // label was the URL. The URL is still what is *drawn* — the value is
        // asserted to be it exactly, so a screen that stopped showing the
        // address and kept the word would fail here.
        let streamLine = app.staticTexts[StationsWords.streamURLLabel]
        XCTAssertTrue(
            streamLine.waitForExistence(timeout: 20),
            "the stream line is not named, so VoiceOver reads a host and a path "
                + "with no word for what they are"
        )
        XCTAssertEqual(
            streamLine.value as? String, StubStations.withTrackStream,
            "the stream line does not carry row 04's URL as its value"
        )

        // --- the interval
        let open = app.buttons[StationsWords.interval]
        XCTAssertTrue(open.waitForExistence(timeout: 20), "the sheet has no interval control")
        open.tap()
        let box = app.textFields[StationsWords.interval]
        XCTAssertTrue(box.waitForExistence(timeout: 20), "the interval box never opened")
        box.tap()
        clearField(app)
        app.typeText("600")
        try XCTUnwrap(stationsSheetButton(app, StationsWords.save), "no save above the keyboard").tap()
        XCTAssertTrue(
            containing(app, StationsWords.intervalSaved).waitForExistence(timeout: 25),
            "nothing was said about the interval that was saved"
        )
        evidence(app, "stations-20-interval-600")

        // Refused, and the row keeps what it had: a draft the client rejects
        // is never sent, and the box keeps the number to correct.
        app.buttons[StationsWords.interval].firstMatch.tap()
        let again = app.textFields[StationsWords.interval]
        XCTAssertTrue(again.waitForExistence(timeout: 20), "the interval box did not reopen")
        again.tap()
        clearField(app)
        app.typeText("10")
        try XCTUnwrap(stationsSheetButton(app, StationsWords.save), "no save above the keyboard").tap()
        XCTAssertTrue(
            containing(app, StationsWords.intervalRefused).waitForExistence(timeout: 25),
            "10 seconds was accepted, so the 30-86400 bound is only the server's"
        )
        evidence(app, "stations-21-interval-refused")

        app.buttons[StationsWords.hideDetails].firstMatch.tap()
        let row = containing(app, StubStations.withTrack)
        XCTAssertTrue(row.waitForExistence(timeout: 20), "row 04 is gone from the list")
        XCTAssertTrue(
            row.label.contains(StationsWords.metaAt600),
            "the row does not show the interval that was saved: \(row.label)"
        )
    }

    // MARK: - 9. forgetting asks first

    /// `forget` is a DELETE, so it asks, and `cancel` really cancels.
    ///
    /// Both directions in one launch: the dialog's own words, then a cancel
    /// that leaves the station where it was, then a confirmation that removes
    /// it and moves the count. A destructive write with no dialog, and a
    /// dialog whose cancel deletes anyway, are the same class of bug and this
    /// is the only case that can see either.
    @MainActor
    func testStations_ForgettingAsksFirst() throws {
        let (app, landed) = openStations()
        XCTAssertTrue(landed, "Stations never drew its subtitle")

        stationsChevron(app, StubStations.withTrack).tap()
        let forget = app.buttons[StationsWords.forget]
        XCTAssertTrue(forget.waitForExistence(timeout: 25), "the sheet has no forget control")
        forget.tap()

        let ask = app.alerts.firstMatch
        XCTAssertTrue(ask.waitForExistence(timeout: 20), "forget deleted without asking")
        evidence(app, "stations-22-forget-asks")
        XCTAssertTrue(
            containing(app, StationsWords.forgetTitle).exists,
            "the dialog does not name the station it is about"
        )
        XCTAssertTrue(
            containing(app, StationsWords.forgetBody).exists,
            "the dialog does not say what forgetting does"
        )
        ask.buttons[StationsWords.cancel].tap()
        XCTAssertTrue(
            app.staticTexts[StationsWords.recentDetections].waitForExistence(timeout: 20),
            "cancel closed the sheet as well as the dialog"
        )
        XCTAssertEqual(
            0, (stationsSent()["delete"] ?? []).count,
            "cancel sent the DELETE anyway"
        )

        app.buttons[StationsWords.forget].firstMatch.tap()
        XCTAssertTrue(app.alerts.firstMatch.waitForExistence(timeout: 20), "the dialog did not reopen")
        app.alerts.firstMatch.buttons[StationsWords.forget].tap()
        XCTAssertTrue(
            app.staticTexts[StationsWords.subtitleAfterAForget].waitForExistence(timeout: 30),
            "the count did not drop after a station was forgotten"
        )
        evidence(app, "stations-23-forgotten")
        // **Its row, not its name.** Train S004 failed here against a screen
        // that had removed the row: the one element left holding the name was
        // the model's own toast, `forgot Favourite Kestrel 04`, and
        // `containing` matches a label that *holds* the words. So the row is
        // asked for as a row (``stationsRows(_:named:)``) and the toast is
        // asserted on purpose, which is the half of this that was never
        // checked.
        XCTAssertTrue(
            containing(app, StationsWords.forgotKestrel04).exists,
            "nothing was said about the station that was forgotten"
        )
        XCTAssertEqual(
            [], stationsRows(app, named: StubStations.withTrack),
            "the forgotten station is still a row on the list"
        )
    }

    // MARK: - 10. the empty house and the failed read

    /// Two broken houses, two launches, one case: an empty house says there
    /// is nothing, and a failed read says it **failed**.
    ///
    /// They are together because the bug they are both about is one bug —
    /// telling the two apart. "no favorites yet" over a read that fell over
    /// is the most plausible lie this screen could tell, and it is what
    /// ``StationsModel/favoritesAreEmpty`` exists to prevent.
    @MainActor
    func testStations_TheEmptyAndFailedStatesSayWhich() throws {
        // --- nothing in the house.
        let (empty, drew) = openStations(
            empty: true,
            waitingFor: StationsWords.noFavorites
        )
        XCTAssertTrue(drew, "an empty house drew no empty state")
        evidence(empty, "stations-24-empty-house")
        XCTAssertTrue(
            empty.staticTexts[StationsWords.noFavoritesSub].exists,
            "the empty favourites state lost its second line"
        )
        XCTAssertTrue(
            empty.staticTexts[StationsWords.nothingPlayed].exists,
            "an empty Recent strip says nothing"
        )
        XCTAssertFalse(
            empty.staticTexts[StationsWords.unreachable].exists,
            "an empty house is reported as a failure"
        )
        empty.terminate()

        // --- the read falls over.
        let (failed, said) = openStations(
            fail: true,
            waitingFor: StationsWords.unreachable
        )
        XCTAssertTrue(said, "a 500 from the favourites read drew no error state")
        evidence(failed, "stations-25-failed-read")
        XCTAssertTrue(
            failed.buttons[StationsWords.retry].exists,
            "the error state offers no retry"
        )
        XCTAssertFalse(
            failed.staticTexts[StationsWords.noFavorites].exists,
            "a failed read is reported as a house with no favourites — the one lie this "
                + "screen must not tell"
        )
    }

    // MARK: - 11. Dynamic Type

    /// Every leaf Apple's audit names on this screen, and the screen's own
    /// headings, measured at both type sizes rather than trusted.
    ///
    /// A named Dynamic Type audit finding has three times been a false
    /// positive on this track, so this compares each leaf's height at `L`
    /// against its height at AX5. `minimumScaleFactor` is rejected everywhere
    /// in this app, and this is what would catch it: text that scales down to
    /// fit does not grow.
    ///
    /// **This case is the measurement ``testStations_AuditFindsNothingNew()``
    /// stands on.** Train S004's audit named eleven elements with "Dynamic
    /// Type font sizes are partially unsupported" — every one of them inside
    /// a card, and none of the identical leaves outside one. The audit case
    /// classifies those eleven out by signature; this case is why it is
    /// allowed to, and the day one of them stops growing is the day this
    /// fails rather than the day the screen quietly stops scaling.
    ///
    /// So the labels below are those eleven verbatim, plus the headings that
    /// were here before — and nothing is skipped: a leaf the AX5 pass cannot
    /// find is a failure, not a silence, because a skip is how a measurement
    /// stops measuring.
    ///
    /// The labels are in screen order and scrolled to in that order: at AX5
    /// the search card alone is taller than the phone, so Recent's rows are
    /// unbuilt until the list is flicked (NIGHT-RULES §5).
    @MainActor
    func testStations_TheScreenTextGrowsAtAX5() throws {
        // Text leaves. A ratio is meaningful for these: nothing holds them to
        // a minimum height.
        let above =
            [StubStations.pluginName, StationsWords.title, StationsWords.subtitle,
             // `noPlayback` was here. It is not drawn any more (D46: the
             // rows play), and a leaf list that named it would fail on an
             // element that is correctly absent.
             StationsWords.importIsAnAdminJob,
             StationsWords.tagsLabel]
        let below =
            [StationsWords.recent, StationsWords.lastThree,
             StubStations.playedOnly, StubStations.playedAgo,
             StationsWords.favorites]
        let leaves = above + below
        // **Screen order, because the flicks only go one way.** The chips sit
        // between `tags:` and the Recent card; measured after `Favorites` they
        // would have been scrolled off the top and reported missing.
        let ordered = above + StationsWords.tagPresets + below

        @MainActor
        func heights(_ app: XCUIApplication, _ labels: [String]) -> [String: CGFloat] {
            var out: [String: CGFloat] = [:]
            for label in labels {
                let found = anything(app, labelled: label)
                // Unbuilt is not absent: flick to it before believing it is
                // gone, which at AX5 is most of this list.
                if !found.exists { _ = scrollStationsUntil(app, found) }
                if found.exists { out[label] = found.frame.height }
            }
            return out
        }

        var measured: [String: [String: CGFloat]] = [:]
        for (name, mode) in [
            ("large", Self.ordinaryTypeSize),
            ("AX5", "UICTContentSizeCategoryAccessibilityXXXL"),
        ] {
            let (app, landed) = openStations(mode)
            XCTAssertTrue(landed, "Stations never drew at \(name)")
            usleep(1_000_000)
            evidence(app, "stations-26-scale-\(name)")
            let found = heights(app, ordered)
            measured[name] = found
            for (leaf, height) in found.sorted(by: { $0.key < $1.key }) {
                print("STATIONS SCALE [\(name)] \(leaf) height=\(height)")
            }
            app.terminate()
        }

        let small = measured["large"] ?? [:]
        let big = measured["AX5"] ?? [:]
        var stuck: [String] = []
        var missing: [String] = []

        for leaf in leaves {
            guard let height = small[leaf] else { missing.append("\(leaf) at L"); continue }
            guard let grown = big[leaf] else { missing.append("\(leaf) at AX5"); continue }
            if grown < height * 1.8 { stuck.append("\(leaf) \(height)->\(grown)") }
        }
        // The chips are the one control here with a 44-point hit floor
        // (rule §3), so a ratio says nothing: a chip whose word did not grow
        // at all would still measure exactly 44. What a grown word gives is
        // a chip taller than its floor, so that is what is asked for.
        for chip in StationsWords.tagPresets {
            guard let height = small[chip] else { missing.append("\(chip) at L"); continue }
            guard let grown = big[chip] else { missing.append("\(chip) at AX5"); continue }
            if grown <= max(height, 44) {
                stuck.append("chip \(chip) \(height)->\(grown), its 44-point floor")
            }
        }

        XCTAssertEqual(
            [], missing,
            "a leaf this screen is measured on was never found, so the comparison "
                + "below is of something smaller than it claims"
        )
        XCTAssertEqual([], stuck, "a leaf of this screen does not grow with the type size")
    }

    /// The sheet's bar word grows with the type size — the measurement the
    /// audit's fourth signature stands on.
    ///
    /// Train S007 audited the detail sheet for the first time and named
    /// `hide details` — the `ToolbarItem` button at `y 80`, inside the
    /// sheet's own navigation bar — with "Dynamic Type font sizes are
    /// partially unsupported" / "User will not be able to change the font
    /// size of this element". Nothing about the word distinguishes it: the
    /// screen's bar was audited in the same run and reported nothing, and
    /// `MusicTrackSheet` and `VideoFeedSheets` put their `close` in the bar
    /// in exactly this shape. The sheet's bar is simply the first bar either
    /// audit has looked inside.
    ///
    /// **"Partially" is the whole of the finding.** A navigation bar caps how
    /// far its buttons grow so that the bar cannot eat the screen, so a ratio
    /// says nothing here for the same reason it says nothing about a chip
    /// with a 44-point floor. What is measured is the detail's claim — that
    /// the size cannot be changed at all — and it is false if the word is
    /// taller at `XXXL`, the largest size a bar scales through, than at `L`.
    /// The accessibility size is taken too and **printed, not asserted**:
    /// that is where the cap lives, and a number pinned to it would be a
    /// pinned total under another name.
    @MainActor
    func testStations_TheHideDetailsWordGrows() throws {
        /// The bar button's height with the sheet open at one type size, or
        /// nil with the tree printed — a size that could not be measured is
        /// a failure below, never a silence (NIGHT-RULES §5).
        @MainActor
        func barWordHeight(_ mode: String, _ short: String) -> CGFloat? {
            let (app, landed) = openStations(mode)
            XCTAssertTrue(landed, "Stations never drew at \(short)")
            // At an accessibility size the search card alone is taller than
            // the phone, so row 04's chevron is unbuilt until the list is
            // flicked — unbuilt is not absent.
            let chevron = stationsChevron(app, StubStations.withTrack)
            if !chevron.exists { _ = scrollStationsUntil(app, chevron) }
            guard chevron.exists else { app.terminate(); return nil }
            chevron.tap()
            guard app.staticTexts[StationsWords.recentDetections]
                .waitForExistence(timeout: 25) else {
                print("=== THE SHEET DID NOT OPEN AT \(short) — tree follows ===")
                print(app.debugDescription)
                app.terminate()
                return nil
            }
            evidence(app, "stations-29-bar-\(short)")
            let word = app.buttons[StationsWords.hideDetails].firstMatch
            guard word.waitForExistence(timeout: 20) else {
                print("=== NO BAR WORD AT \(short) — tree follows ===")
                print(app.debugDescription)
                app.terminate()
                return nil
            }
            let height = word.frame.height
            print("STATIONS BAR [\(short)] hide details height=\(height)")
            app.terminate()
            return height
        }

        let ordinary = barWordHeight(Self.ordinaryTypeSize, "L")
        let largest = barWordHeight("UICTContentSizeCategoryXXXL", "XXXL")
        // The cap, for the record. Nothing is asserted about it.
        let capped = barWordHeight("UICTContentSizeCategoryAccessibilityXXXL", "AX5")
        print("STATIONS BAR cap at AX5: " + String(describing: capped))

        let small = try XCTUnwrap(ordinary, "the bar word was never measured at L")
        let grown = try XCTUnwrap(largest, "the bar word was never measured at XXXL")
        XCTAssertGreaterThan(
            grown, small,
            "hide details is no taller at XXXL than at L (\(small)), so the audit's "
                + "Dynamic Type finding against the sheet's bar is true and the bar "
                + "does not scale at any size"
        )
    }

    // MARK: - 12. the audit

    /// The signatures this screen's audit is allowed to report, each one
    /// measured somewhere else in this file or in ``StubHouseUITests``.
    ///
    /// **1. One anonymous `Contrast failed`.** The phone bar's.
    /// ``testTheLocalShellPassesTheAudit()`` is the control that placed it:
    /// the same ``ShellTopBar`` with *no* phone bar audits zero, Home audits
    /// one and the pairing screen three, and Stations is drawn in that same
    /// shell. It is allowed by **kind and by count**, so a second one, or one
    /// of any other kind, still fails.
    ///
    /// **2. Contrast on an element that straddles the foot of the scroll
    /// viewport.** On train S004 that was `Favourite Kestrel 01`, a plain
    /// primary-ink row title at `y 765.5` with the `ScrollView` ending at
    /// `770` — half of it rendered, half of it under the phone bar, and no
    /// pixels an audit can read. The picture is what attributed it; two tree
    /// diffs could not. The same class as four of the pairing screen's five
    /// (``knownPushedBarFloor``'s doc: "elements the bar was drawn over or
    /// that straddled a scroll edge"). Bounded by the viewport the app
    /// actually drew, never by a constant.
    ///
    /// **3. "Dynamic Type font sizes are partially unsupported" naming one
    /// of the eleven leaves ``testStations_TheScreenTextGrowsAtAX5()``
    /// measures.** Every one of them is inside a card — `HomeCard`'s heading
    /// and count, the Recent row's two lines, the search card's `tags:` and
    /// its six chips — and every identical leaf *outside* a card (the title,
    /// the subtitle, `importing the FCC FM list…`) is reported by nobody. A
    /// finding that names a leaf **not** on that list is a defect, because
    /// nothing has measured it.
    ///
    /// Anything else lands in `rest` and fails. That is the point of
    /// classifying by signature: the floor cannot swallow a finding it has
    /// not been told about, which is what a pinned total did for three
    /// sessions (``knownPushedBarFloor``).
    enum StationsAudit {

        /// The leaves of ``testStations_TheScreenTextGrowsAtAX5()``, which is
        /// where their growth is measured.
        static let measuredLeaves: Set<String> = Set(
            [StationsWords.recent, StationsWords.lastThree, StationsWords.tagsLabel,
             StubStations.playedOnly, StubStations.playedAgo]
                + StationsWords.tagPresets
        )

        /// The sheet's navigation-bar button, measured by
        /// ``testStations_TheHideDetailsWordGrows()``.
        static let measuredBarWords: Set<String> = [StationsWords.hideDetails]

        static let dynamicType = "Dynamic Type font sizes are"
        static let contrast = "Contrast"
        static let contrastFailed = "Contrast failed"
    }

    /// `label='…'` out of an ``auditIssues(_:_:)`` line, or nil when the
    /// audit named no element.
    func stationsAuditLabel(_ finding: String) -> String? {
        guard let open = finding.range(of: "label='") else { return nil }
        let rest = finding[open.upperBound...]
        guard let close = rest.range(of: "' frame=") else { return nil }
        return String(rest[..<close.lowerBound])
    }

    /// `frame=(x, y, w, h)` out of an ``auditIssues(_:_:)`` line as its top
    /// and bottom, or nil when there was no element to have a frame.
    ///
    /// Parsed rather than queried again: the audit reports the frame it
    /// measured at the moment it measured it, and an element asked for again
    /// after a screenshot and two attachments is not promised to be where it
    /// was.
    func stationsAuditBand(_ finding: String) -> (minY: CGFloat, maxY: CGFloat)? {
        guard let open = finding.range(of: "frame=(") else { return nil }
        let rest = finding[open.upperBound...]
        guard let close = rest.firstIndex(of: ")") else { return nil }
        let numbers = rest[..<close]
            .split(separator: ",")
            .compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }
        guard numbers.count == 4 else { return nil }
        return (CGFloat(numbers[1]), CGFloat(numbers[1] + numbers[3]))
    }

    /// Split one audit's findings into the measured signatures above and
    /// everything left, which the caller asserts is empty.
    ///
    /// `viewportFoot` is the bottom of the scroll view that was on screen
    /// when the audit ran. `.greatestFiniteMagnitude` when there was none —
    /// then nothing can straddle an edge and that branch allows nothing.
    @MainActor
    func stationsClassify(
        _ found: [String],
        viewportFoot: CGFloat,
        name: String
    ) -> [String] {
        var rest: [String] = []
        var anonymousContrast = 0
        for line in found {
            let label = stationsAuditLabel(line)

            if line.hasPrefix(StationsAudit.contrastFailed), label == nil {
                anonymousContrast += 1
                print("STATIONS AUDIT [\(name)] phone-bar contrast #\(anonymousContrast): \(line)")
                if anonymousContrast == 1 { continue }
                rest.append("a second anonymous contrast finding: \(line)")
                continue
            }
            if line.hasPrefix(StationsAudit.contrast), let band = stationsAuditBand(line),
               band.maxY > viewportFoot, band.minY < viewportFoot {
                print(
                    "STATIONS AUDIT [\(name)] straddles the viewport foot "
                        + "(\(band.minY)…\(band.maxY) over \(viewportFoot)): \(line)"
                )
                continue
            }
            if line.hasPrefix(StationsAudit.dynamicType), let label,
               StationsAudit.measuredLeaves.contains(label) {
                print("STATIONS AUDIT [\(name)] measured to scale, see the AX5 case: \(line)")
                continue
            }
            // A **button** of the sheet's navigation bar, which scales until
            // the bar caps it. Buttons only (`type=9`): a static text of this
            // screen that stopped scaling must still fail, and the bar holds
            // no static text of ours — its title is the screen's own name.
            if line.hasPrefix(StationsAudit.dynamicType), let label,
               StationsAudit.measuredBarWords.contains(label), line.contains("type=9") {
                print(
                    "STATIONS AUDIT [\(name)] a bar button, measured to grow in "
                        + "testStations_TheHideDetailsWordGrows: \(line)"
                )
                continue
            }
            rest.append(line)
        }
        return rest
    }

    /// Apple's own accessibility audit, over the screen **and** the open
    /// detail sheet, classified by signature.
    ///
    /// By signature with ``floorAndRest(_:)`` and ``StationsAudit``, never a
    /// pinned total: the floor is the anonymous "Dynamic Type font sizes are
    /// (partially) unsupported" finding every pushed screen in this app
    /// reports, with no element and a detail that
    /// ``testStations_TheScreenTextGrowsAtAX5()`` measures to be false, and
    /// the three signatures ``StationsAudit`` documents are the three this
    /// screen has had measured. Anything else the audit can say is a defect.
    ///
    /// The sheet is audited as well as the screen, and a finding that names a
    /// row **behind** the sheet is the signal that the sheet is not modal —
    /// which is how the two `Contrast failed` behind Settings' forget dialog
    /// were found. The sheet is its own scroll view, so the viewport foot is
    /// read again with it open rather than reused.
    @MainActor
    func testStations_AuditFindsNothingNew() throws {
        guard #available(iOS 17.0, *) else {
            throw XCTSkip("performAccessibilityAudit needs iOS 17")
        }
        // The foot of whatever scroll view is in front. `firstMatch` is the
        // screen's own before the sheet opens and the sheet's after, which is
        // why it is read twice.
        @MainActor
        func viewportFoot(_ app: XCUIApplication) -> CGFloat {
            let scroller = app.scrollViews.firstMatch
            return scroller.exists ? scroller.frame.maxY : .greatestFiniteMagnitude
        }

        let (app, landed) = openStations()
        XCTAssertTrue(landed, "Stations never drew its subtitle")

        let foot = viewportFoot(app)
        let screen = Self.floorAndRest(try auditIssues(app, "stations-screen"))
        evidence(app, "stations-27-audit-screen")
        print("STATIONS AUDIT [screen] floor=\(screen.floor) viewport foot=\(foot)")
        XCTAssertEqual(
            [], stationsClassify(screen.rest, viewportFoot: foot, name: "screen"),
            "the audit named something on the Stations screen that nothing has "
                + "measured (floor: \(screen.floor), viewport foot: \(foot))"
        )

        stationsChevron(app, StubStations.withTrack).tap()
        XCTAssertTrue(
            app.staticTexts[StationsWords.recentDetections].waitForExistence(timeout: 25),
            "the detail sheet never opened, so it was not audited"
        )
        let sheetFoot = viewportFoot(app)
        let sheet = Self.floorAndRest(try auditIssues(app, "stations-sheet"))
        evidence(app, "stations-28-audit-sheet")
        print("STATIONS AUDIT [sheet] floor=\(sheet.floor) viewport foot=\(sheetFoot)")
        XCTAssertEqual(
            [], stationsClassify(sheet.rest, viewportFoot: sheetFoot, name: "sheet"),
            "the audit named something with the detail sheet open that nothing has "
                + "measured (floor: \(sheet.floor)) — a finding naming a row behind the "
                + "sheet means the sheet is not modal"
        )
    }
}
