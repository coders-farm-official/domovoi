import XCTest

/// Up next on the phone itself (P9; Android P8 4fb53334): the "up next"
/// section under the player tab's Queue panel, and what the phone sent the
/// house for it.
///
/// Every case is named `testPhoneUpNext_…` so a train can select them, every
/// piece of evidence is `phoneupnext-…`, and every miss prints the tree.
///
/// ### The house
///
/// `design-notes/ios/batch-2026-11-14/p9/stubhouse.patch`: the phone's
/// session routes over `fixtures/musicmore.py`'s six songs, three picks a
/// batch. `POST /api/_test/upnext-phone {fail_start}` makes the start answer
/// that status; `GET /api/_test/upnext-phone-sent` answers what the phone
/// sent. `reset-fixtures` puts it back.
///
/// The stub's songs are 8-second tones, so the queue moves while a case
/// looks at it: nothing here pins a count or a row's place, only that a pick
/// is drawn, what the head and the line say, and what reached the house.
extension StubHouseUITests {

    // MARK: - getting there

    /// Reset the house, play Longwater Drive on this phone, and open the
    /// player tab's Queue panel. True once the queue's `clear` is drawn.
    @MainActor
    func phoneUpNextPlayLongwater(failStart: Int? = nil) -> (app: XCUIApplication, landed: Bool) {
        musicMoreReset()
        if let failStart {
            control("upnext-phone", #"{"fail_start":\#(failStart)}"#, port: 6601, what: "the up next start's answer")
        }
        let (app, landed) = openPlaylistsTab()
        guard landed, openPlaylistSheet(app, MusicMoreUnderTest.longwater) else { return (app, false) }
        let here = app.buttons[MusicMoreUnderTest.playHere]
        guard here.waitForExistence(timeout: 20) else {
            print("=== NO `play on this device` — tree follows ===")
            print(app.debugDescription)
            return (app, false)
        }
        here.tap()
        app.buttons[MusicMoreUnderTest.close].firstMatch.tap()
        guard musicMoreTapTab(app, MusicMoreUnderTest.tabPlayer) else { return (app, false) }
        let queueTab = app.buttons[MusicGenresUnderTest.panelQueue]
        guard queueTab.waitForExistence(timeout: 20) else {
            print("=== NO QUEUE PANEL TAB — tree follows ===")
            print(app.debugDescription)
            return (app, false)
        }
        if !queueTab.isSelected {
            musicMoreScrollOnScreen(app, queueTab)
            queueTab.tap()
        }
        let clear = app.buttons[MusicGenresUnderTest.clear]
        guard clear.waitForExistence(timeout: 20) else {
            print("=== THE QUEUE PANEL DREW NO QUEUE — tree follows ===")
            print(app.debugDescription)
            return (app, false)
        }
        return (app, true)
    }

    @MainActor
    func phoneUpNextElement(_ app: XCUIApplication, _ id: String) -> XCUIElement {
        app.descendants(matching: .any)[id].firstMatch
    }

    /// The first waiting pick's row, whichever it is.
    @MainActor
    func phoneUpNextAnyPick(_ app: XCUIApplication) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier BEGINSWITH %@", PhoneUpNextUnderTest.pickPrefix))
            .firstMatch
    }

    /// What the phone sent the house's Up next routes, in order.
    @MainActor
    func phoneUpNextSent(port: Int = 6601) -> [[String: Any]] {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/_test/upnext-phone-sent")!)
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        let answered = expectation(description: "the stub said what the phone sent")
        var sent: [[String: Any]] = []
        URLSession.shared.dataTask(with: request) { data, _, _ in
            if let data,
               let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let rows = object["sent"] as? [[String: Any]] {
                sent = rows
            }
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 15)
        return sent
    }

    /// The bodies of what was sent with `method` to a path ending in `tail`.
    @MainActor
    func phoneUpNextBodies(_ method: String, endingIn tail: String, port: Int = 6601) -> [[String: Any]] {
        phoneUpNextSent(port: port)
            .filter { ($0["method"] as? String) == method && (($0["path"] as? String) ?? "").hasSuffix(tail) }
            .compactMap { $0["body"] as? [String: Any] }
    }

    /// Wait for the house to have been sent something that `matches`.
    @MainActor
    func phoneUpNextWaitForSent(
        _ method: String,
        endingIn tail: String,
        timeout: Double = 20,
        matches: ([String: Any]) -> Bool
    ) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if phoneUpNextBodies(method, endingIn: tail).contains(where: matches) { return true }
            RunLoop.current.run(until: Date().addingTimeInterval(0.5))
        }
        print("=== THE HOUSE NEVER GOT \(method) …\(tail) — sent: \(phoneUpNextSent()) ===")
        return false
    }

    // MARK: - the section

    /// A playlist played here starts a whole-list session (continue mode),
    /// and the section under the queue says what the mix is built from and
    /// draws a pick with its switch.
    @MainActor
    func testPhoneUpNext_APlaylistPlayedHereShowsUpNextUnderTheQueue() throws {
        let (app, landed) = phoneUpNextPlayLongwater()
        XCTAssertTrue(landed, "the player tab's queue never drew")

        XCTAssertTrue(
            phoneUpNextWaitForSent("POST", endingIn: "/up-next/sessions") { body in
                let start = body["start"] as? [String: Any]
                let context = start?["context"] as? [String: Any]
                let target = body["target"] as? [String: Any]
                return start?["whole_list"] as? Bool == true
                    && context?["kind"] as? String == "playlist"
                    && context?["ref"] as? String == "41"
                    && target?["kind"] as? String == "device"
                    && body["autoplay"] as? Bool == true
            },
            "no whole-list start for the playlist reached the house"
        )

        let head = phoneUpNextElement(app, PhoneUpNextUnderTest.headID)
        guard head.waitForExistence(timeout: 20) else {
            XCTFail("no `up next` head under the queue — tree follows\n" + app.debugDescription)
            return
        }
        musicMoreScrollOnScreen(app, head)
        evidence(app, "phoneupnext-01-section")
        XCTAssertTrue(
            containing(app, PhoneUpNextUnderTest.basisLongwater).waitForExistence(timeout: 10),
            "the head does not say what the mix is built from — tree follows\n" + app.debugDescription
        )
        XCTAssertTrue(
            phoneUpNextAnyPick(app).waitForExistence(timeout: 30),
            "no pick drawn under the head — tree follows\n" + app.debugDescription
        )
        let toggle = app.switches[PhoneUpNextUnderTest.autoplayLabel]
        XCTAssertTrue(
            toggle.exists || phoneUpNextElement(app, PhoneUpNextUnderTest.autoplayID).exists,
            "no autoplay switch by its name — tree follows\n" + app.debugDescription
        )
    }

    /// Autoplay off takes a mix's waiting picks out, says the queue stops,
    /// and tells the house.
    @MainActor
    func testPhoneUpNext_AutoplayOffTakesTheMixsPicksOutAndSaysTheQueueStops() throws {
        let (app, landed) = phoneUpNextPlayLongwater()
        XCTAssertTrue(landed, "the player tab's queue never drew")
        let pick = phoneUpNextAnyPick(app)
        guard pick.waitForExistence(timeout: 30) else {
            XCTFail("no pick drawn to take out — tree follows\n" + app.debugDescription)
            return
        }
        let toggle = phoneUpNextElement(app, PhoneUpNextUnderTest.autoplayID)
        guard toggle.waitForExistence(timeout: 10) else {
            XCTFail("no autoplay switch — tree follows\n" + app.debugDescription)
            return
        }
        musicMoreScrollOnScreen(app, toggle)
        // The switch itself sits at the trailing end of the row.
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap()
        evidence(app, "phoneupnext-02-autoplay-off")
        XCTAssertTrue(
            containing(app, PhoneUpNextUnderTest.autoplayOff).waitForExistence(timeout: 15),
            "the line does not say the queue stops — tree follows\n" + app.debugDescription
        )
        XCTAssertTrue(
            waitUntilGone(phoneUpNextAnyPick(app), timeout: 15),
            "a mix's waiting picks stayed with autoplay off — tree follows\n" + app.debugDescription
        )
        XCTAssertTrue(
            phoneUpNextWaitForSent("PATCH", endingIn: "") { $0["autoplay"] as? Bool == false },
            "the house was not told autoplay is off"
        )
    }

    /// "less like this" on a pick goes to the house as feedback, and the
    /// pick leaves.
    @MainActor
    func testPhoneUpNext_LessLikeThisTellsTheHouseAndThePickLeaves() throws {
        let (app, landed) = phoneUpNextPlayLongwater()
        XCTAssertTrue(landed, "the player tab's queue never drew")
        let less = startingWith(app, PhoneUpNextUnderTest.lessLikePrefix)
        guard less.waitForExistence(timeout: 30) else {
            XCTFail("no `less like this` on a pick — tree follows\n" + app.debugDescription)
            return
        }
        musicMoreScrollOnScreen(app, less)
        less.tap()
        evidence(app, "phoneupnext-03-less-like-this")
        XCTAssertTrue(
            phoneUpNextWaitForSent("POST", endingIn: "/feedback") { $0["kind"] as? String == "less_like_this" },
            "no less_like_this reached the house"
        )
        XCTAssertTrue(
            startingWith(app, PhoneUpNextUnderTest.lessLikeNotePrefix).waitForExistence(timeout: 15)
                || !phoneUpNextAnyPick(app).exists,
            "nothing said the artist left the mix — tree follows\n" + app.debugDescription
        )
    }

    /// A start the home server failed (5xx) is said, under the head.
    @MainActor
    func testPhoneUpNext_AFailedStartSaysUpNextNeedsTheHomeServer() throws {
        let (app, landed) = phoneUpNextPlayLongwater(failStart: 503)
        XCTAssertTrue(landed, "the player tab's queue never drew")
        let line = containing(app, PhoneUpNextUnderTest.offline)
        guard line.waitForExistence(timeout: 20) else {
            XCTFail("a failed start did not say up next needs the home server — tree follows\n" + app.debugDescription)
            return
        }
        musicMoreScrollOnScreen(app, line)
        evidence(app, "phoneupnext-04-offline")
    }

    /// A start the server refused (4xx) shows no Up next at all: no control
    /// that can't do what it says.
    @MainActor
    func testPhoneUpNext_ARefusedStartShowsNoUpNext() throws {
        let (app, landed) = phoneUpNextPlayLongwater(failStart: 422)
        XCTAssertTrue(landed, "the player tab's queue never drew")
        XCTAssertTrue(
            phoneUpNextWaitForSent("POST", endingIn: "/up-next/sessions") { _ in true },
            "the start never reached the house"
        )
        // Long enough for the refusal to land and the section to go.
        RunLoop.current.run(until: Date().addingTimeInterval(3))
        evidence(app, "phoneupnext-05-refused")
        XCTAssertTrue(
            waitUntilGone(phoneUpNextElement(app, PhoneUpNextUnderTest.headID), timeout: 10),
            "a refused start still shows Up next — tree follows\n" + app.debugDescription
        )
        XCTAssertFalse(containing(app, PhoneUpNextUnderTest.offline).exists, "a refusal is not an outage: say nothing")
    }

    /// A genre's song played here starts a session that goes on with the
    /// rest of that genre (list mode, the genre by its key).
    @MainActor
    func testPhoneUpNext_AGenresSongPlayedHereGoesOnWithTheGenre() throws {
        musicMoreReset()
        let (app, landed) = openGenresTab()
        XCTAssertTrue(landed, "the genres tab never drew")
        XCTAssertTrue(openGenreSheet(app, MusicGenresUnderTest.brindlecoreKey), "the genre sheet never opened")
        let play = app.buttons[PhoneUpNextUnderTest.playVeldrassenHere]
        guard play.waitForExistence(timeout: 20) else {
            XCTFail("the genre's song has no `play on this device` — tree follows\n" + app.debugDescription)
            return
        }
        musicMoreScrollOnScreen(app, play)
        play.tap()
        evidence(app, "phoneupnext-06-genre-song")
        XCTAssertTrue(
            phoneUpNextWaitForSent("POST", endingIn: "/up-next/sessions") { body in
                let start = body["start"] as? [String: Any]
                let context = start?["context"] as? [String: Any]
                return (start?["track_id"] as? NSNumber)?.intValue == 501
                    && start?["whole_list"] == nil
                    && context?["kind"] as? String == "genre"
                    && context?["ref"] as? String == MusicGenresUnderTest.brindlecoreKey
            },
            "the song's start did not carry its genre"
        )
    }
}

enum PhoneUpNextUnderTest {
    static let headID = "player-upnext-head"
    static let autoplayID = "player-upnext-autoplay"
    static let pickPrefix = "player-upnext-pick-"
    static let autoplayLabel = "autoplay: keep playing similar songs when the queue ends"
    static let basisLongwater = "more like Longwater Drive"
    static let autoplayOff = "autoplay is off — the queue stops at the end"
    static let offline = "up next needs the home server"
    static let lessLikePrefix = "less like this: no more"
    /// The toast, `no more <artist> in this mix` — never the button's own
    /// label, which starts `less like this:`.
    static let lessLikeNotePrefix = "no more "
    static let playVeldrassenHere = "play on this device Veldrassen Overture"
}
