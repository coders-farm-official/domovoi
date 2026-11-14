import XCTest

/// The Music mix and "why this song?" (Up next 1.3.0; Android da6dbe46):
/// Settings → Household → Music mix, a person's own music mix on their page,
/// the "why this song?" chip on the phone's picks and a room's picks, and
/// Autoplay following the household's default until it is flipped here.
///
/// Every case is named `testMix_…` so a train can select them, every piece of
/// evidence is `mix-…`, and every miss prints the tree.
///
/// ### The house
///
/// `design-notes/ios/batch-2026-11-14/mix/stubhouse.patch` (against
/// stubhouse.combined.py): the household's mix is discovery 2, wander 4,
/// autoplay on, 50 songs, similar artists on, popularity not reported; Alex
/// (3) has their own (5, 1); Jordan (1) uses the household's. `PUT
/// /api/music/mix` is a 401 for a phone; a person's PUT is taken only from
/// the device the `claim` fixture says is theirs, 403 otherwise.
/// `POST /api/_test/mix {autoplay}` sets the household's default;
/// `GET /api/_test/mix-sent` answers the PUTs. The phone's picks carry `why`
/// on seqs 1 and 3 (none on 2, a blank one on 4); parlour's "same artist"
/// pick carries one, its "from Longwater Drive" pick none.
///
/// Nothing here opens the connection sheet or taps "use a different server",
/// and "Open on the dashboard" is asserted, never tapped (it leaves the app).
extension StubHouseUITests {

    /// The words matched on, spelt out (a query that followed a renamed
    /// constant would stop testing what it claims to).
    enum MixUnderTest {
        static let household = "Household"
        static let section = "Music mix"
        static let introStart = "How Up next picks the songs that follow when the queue runs out"
        static let mixTitle = "The mix"
        static let mixSub = "People can set their own in Settings → People; everyone else gets these."
        static let adminThis = "An admin can change this."
        static let adminThese = "An admin can change these."
        static let discovery2 = "more songs you haven’t heard in a while"
        static let wander4 = "wanders more across your library"
        static let autoplayLabel = "Autoplay when a playlist ends"
        static let follows = "This phone follows this until you switch autoplay in its queue."
        static let fifty = "50 songs"
        static let internetTitle = "From the internet"
        static let notKnownYet = "not known yet"
        static let dashboard = "Open on the dashboard"
        static let yourMix = "Your mix"
        static let personHead = "music mix"
        static let useHouseholdJordan = "Use the household’s mix for Jordan"
        static let usedWhenJordan =
            "Used when Up next plays for Jordan: “listening as” them, their phone, or their voice in a room."
        static let discoverySlider = "Discovery or Favorites"
        static let alexOwn = "Alex has their own mix."
        static let alexDiscovery5 = "mostly what you play most"
        static let alexWander1 = "stays close to the song playing"
        static let alexNote = "An admin, or Alex’s own phone, can change this."
        static let notYours = "Only an admin, or this person's own phone, can change their mix."
        static let whyPrefix = "why this song?"
        static let roomWhy = "why this song? same artist"
        static let roomSentence = "Nidra Vane again, like the song you started with · not played in 3 weeks"
        static let roomPlain = "from Longwater Drive"
        static let phoneSentences = [
            "same artist as the song you started with · not played in 4 months",
            "on Hollow Tide with the song you started with · you play this often",
        ]
    }

    // MARK: - helpers

    /// What the house was sent on the Music mix routes (`puts`).
    @MainActor
    func mixPuts(port: Int = 6601) -> [[String: Any]] {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/_test/mix-sent")!)
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        let answered = expectation(description: "the stub said what the phone put")
        var puts: [[String: Any]] = []
        URLSession.shared.dataTask(with: request) { data, _, _ in
            if let data,
               let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let rows = object["puts"] as? [[String: Any]] {
                puts = rows
            }
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 15)
        return puts
    }

    /// Wait until the house has a PUT that `matches`; print what it has on a miss.
    @MainActor
    func mixWaitForPut(timeout: Double = 20, matches: ([String: Any]) -> Bool) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if mixPuts().contains(where: matches) { return true }
            RunLoop.current.run(until: Date().addingTimeInterval(0.5))
        }
        print("=== THE HOUSE NEVER GOT THE PUT — puts: \(mixPuts()) ===")
        return false
    }

    /// Reset the house, say whose this phone is on the house's side (nil:
    /// nobody's), and launch with `push`. The launch's registration carries
    /// the owner to the app.
    @MainActor
    func mixLaunch(owner: Int?, push: String) -> XCUIApplication {
        control("reset-fixtures", "{}", port: 6601, what: "the house back to its seed")
        if let owner {
            control("claim", #"{"owner":\#(owner)}"#, port: 6601, what: "this phone is person \(owner)'s")
        }
        return launch(push: push)
    }

    /// Settings → Household → Music mix, once its first card has drawn.
    @MainActor
    func mixReachSettings(_ app: XCUIApplication) -> Bool {
        let group = app.buttons[MixUnderTest.household]
        guard group.waitForExistence(timeout: 30) else {
            print("=== SETTINGS INDEX NOT REACHED — tree follows ===")
            print(app.debugDescription)
            return false
        }
        group.tap()
        let section = app.buttons[MixUnderTest.section]
        guard section.waitForExistence(timeout: 20), navReach(app, section) else {
            print("=== HOUSEHOLD HAS NO `Music mix` — tree follows ===")
            print(app.debugDescription)
            return false
        }
        section.tap()
        guard app.staticTexts[MixUnderTest.mixTitle].waitForExistence(timeout: 20) else {
            print("=== MUSIC MIX DREW NO `The mix` CARD — tree follows ===")
            print(app.debugDescription)
            return false
        }
        return true
    }

    /// A text on screen, brought wholly into the page; prints the tree on a miss.
    @MainActor
    func mixFind(_ app: XCUIApplication, _ element: XCUIElement, _ what: String, timeout: Double = 20) -> Bool {
        if element.waitForExistence(timeout: timeout), navReach(app, element) { return true }
        if navReach(app, element) { return true }
        print("=== MIX: \(what) NOT FOUND — tree follows ===")
        print(app.debugDescription)
        return false
    }

    /// Play Longwater Drive on this phone with the house's Autoplay default
    /// set first, and open the player tab's Queue panel.
    /// ``phoneUpNextPlayLongwater(failStart:)``'s walk, with the house's side
    /// set between the reset and the play.
    @MainActor
    func mixPlayLongwater(householdAutoplay: Bool?) -> (app: XCUIApplication, landed: Bool) {
        musicMoreReset()
        if let householdAutoplay {
            control("mix", #"{"autoplay":\#(householdAutoplay)}"#, port: 6601, what: "the household's autoplay default")
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
        guard app.buttons[MusicGenresUnderTest.clear].waitForExistence(timeout: 20) else {
            print("=== THE QUEUE PANEL DREW NO QUEUE — tree follows ===")
            print(app.debugDescription)
            return (app, false)
        }
        return (app, true)
    }

    // MARK: - Settings → Music mix

    /// The household's mix is said in words with who can change it — no
    /// slider, no switch — and the dashboard is offered. A phone that is
    /// nobody's gets no "Your mix".
    @MainActor
    func testMix_SettingsSaysTheHouseholdsMixInWords() throws {
        let app = mixLaunch(owner: nil, push: "Settings")
        XCTAssertTrue(mixReachSettings(app), "never reached Settings → Music mix")
        evidence(app, "mix-10-settings")
        XCTAssertTrue(containing(app, MixUnderTest.introStart).exists, "no intro — tree follows\n" + app.debugDescription)
        XCTAssertTrue(app.staticTexts[MixUnderTest.mixSub].exists, "the card's sub is not the web's")
        for (text, what) in [
            (MixUnderTest.adminThis, "who can change the mix"),
            (MixUnderTest.discovery2, "discovery 2 in words"),
            (MixUnderTest.wander4, "wander 4 in words"),
            (MixUnderTest.autoplayLabel, "the autoplay default"),
            (MixUnderTest.follows, "this phone's line"),
            (MixUnderTest.fifty, "no repeats within 50 songs"),
            (MixUnderTest.adminThese, "who can change the internet switches"),
            (MixUnderTest.notKnownYet, "popularity not reported"),
        ] {
            XCTAssertTrue(mixFind(app, containing(app, text), what), "\(what) is not said")
        }
        XCTAssertTrue(mixFind(app, app.buttons[MixUnderTest.dashboard], "the dashboard button"), "no way to the dashboard")
        evidence(app, "mix-11-settings-foot")
        XCTAssertEqual(0, app.sliders.count, "a slider the server would refuse — tree follows\n" + app.debugDescription)
        XCTAssertFalse(
            app.switches.matching(NSPredicate(format: "label BEGINSWITH %@", "Use the household")).firstMatch.exists,
            "a switch on a phone that is nobody's — tree follows\n" + app.debugDescription
        )
        XCTAssertFalse(app.staticTexts[MixUnderTest.yourMix].exists, "\"Your mix\" on a phone that is nobody's")
    }

    /// The phone that is Jordan's: "Your mix" with the switch; turning it
    /// off sends Jordan's own sliders (from what they use now) named by this
    /// phone, and the sliders and the sentence appear.
    @MainActor
    func testMix_YourMixOnThePhoneThatIsTheirs() throws {
        let app = mixLaunch(owner: 1, push: "Settings")
        XCTAssertTrue(mixReachSettings(app), "never reached Settings → Music mix")
        let toggle = app.switches[MixUnderTest.useHouseholdJordan]
        guard mixFind(app, toggle, "Jordan's switch", timeout: 30) else {
            return XCTFail("no `Use the household’s mix` on Jordan's own phone")
        }
        XCTAssertTrue(app.staticTexts[MixUnderTest.yourMix].exists, "no \"Your mix\" card")
        XCTAssertEqual("1", toggle.value as? String, "Jordan uses the household's mix")
        evidence(app, "mix-20-your-mix")
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
        XCTAssertTrue(
            mixWaitForPut { put in
                let body = put["body"] as? [String: Any]
                let override = body?["override"] as? [String: Any]
                return (put["path"] as? String) == "/api/people/1/music-mix"
                    && (put["status"] as? Int) == 200
                    && (put["device"] as? String)?.isEmpty == false
                    && override?["discovery_favorites"] as? Int == 2
                    && override?["close_wander"] as? Int == 4
            },
            "turning the switch off sent no override starting from what Jordan uses"
        )
        XCTAssertTrue(
            mixFind(app, app.sliders[MixUnderTest.discoverySlider], "the discovery slider"),
            "no sliders after turning the household's mix off"
        )
        XCTAssertTrue(mixFind(app, containing(app, MixUnderTest.usedWhenJordan), "the used-when sentence"),
                      "no \"Used when Up next plays for Jordan\"")
        evidence(app, "mix-21-own-sliders")
    }

    // MARK: - a person's page

    /// Someone else's page (the phone is nobody's): Alex's own mix in words,
    /// with who can change it, and no control.
    @MainActor
    func testMix_SomeoneElsesMixIsSaidInWords() throws {
        let app = mixLaunch(owner: nil, push: PeopleUnderTest.title)
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.alex), "Alex's detail did not open")
        let own = containing(app, MixUnderTest.alexOwn)
        guard own.waitForExistence(timeout: 30) || peopleReveal(app, own) else {
            print("=== NO `Alex has their own mix.` — tree follows ===")
            print(app.debugDescription)
            return XCTFail("Alex's page does not say their mix")
        }
        XCTAssertTrue(peopleReveal(app, own), "the mix never came into the page")
        evidence(app, "mix-30-someone-elses")
        XCTAssertTrue(app.staticTexts[MixUnderTest.personHead].exists, "no `music mix` head")
        XCTAssertTrue(containing(app, MixUnderTest.alexDiscovery5).exists, "discovery 5 not in words")
        XCTAssertTrue(containing(app, MixUnderTest.alexWander1).exists, "wander 1 not in words")
        XCTAssertTrue(containing(app, MixUnderTest.alexNote).exists, "no \"An admin, or Alex’s own phone\"")
        XCTAssertEqual(0, app.sliders.count, "a slider on someone else's mix — tree follows\n" + app.debugDescription)
        XCTAssertFalse(
            app.switches.matching(NSPredicate(format: "label BEGINSWITH %@", "Use the household")).firstMatch.exists,
            "a switch on someone else's mix"
        )
    }

    /// Jordan's own page on Jordan's phone, after the house gave the phone to
    /// someone else: the save is refused and the server's words are shown.
    @MainActor
    func testMix_ARefusedSaveIsSaidInTheServersWords() throws {
        let app = mixLaunch(owner: 1, push: PeopleUnderTest.title)
        XCTAssertTrue(peopleOpen(app, PeopleUnderTest.jordan), "Jordan's detail did not open")
        let toggle = app.switches[MixUnderTest.useHouseholdJordan]
        guard toggle.waitForExistence(timeout: 30), peopleReveal(app, toggle) else {
            print("=== NO SWITCH ON JORDAN'S OWN PAGE — tree follows ===")
            print(app.debugDescription)
            return XCTFail("Jordan's own phone offers no `Use the household’s mix`")
        }
        // An admin gives the phone to Chevy on the house's side; the app
        // has not heard.
        control("claim", #"{"owner":2}"#, port: 6601, what: "the phone is Chevy's now")
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
        let refused = containing(app, MixUnderTest.notYours)
        let said = refused.waitForExistence(timeout: 20)
        evidence(app, "mix-40-refused")
        XCTAssertTrue(said, "the 403 was not said in the server's words — tree follows\n" + app.debugDescription)
        XCTAssertTrue(
            mixWaitForPut { ($0["status"] as? Int) == 403 && ($0["path"] as? String) == "/api/people/1/music-mix" },
            "the house never refused a PUT"
        )
        XCTAssertFalse(app.sliders[MixUnderTest.discoverySlider].exists, "a refused save drew the sliders anyway")
    }

    // MARK: - "why this song?"

    /// A room's pick with a sentence: the chip is a button that shows the
    /// sentence under the row and hides it again, and plays nothing; the
    /// pick with none is a plain chip.
    @MainActor
    func testMix_ARoomPickSaysWhyThisSongWithoutPlayingIt() throws {
        let (app, landed) = openRoomQueueTab()
        XCTAssertTrue(landed, "the room queue tab never drew parlour's queue")
        let chip = app.buttons[MixUnderTest.roomWhy]
        guard chip.waitForExistence(timeout: 20), musicMoreReveal(app, chip) else {
            print("=== NO `why this song? same artist` BUTTON — tree follows ===")
            print(app.debugDescription)
            return XCTFail("the room's pick with a sentence has no why-this-song button")
        }
        XCTAssertFalse(app.staticTexts[MixUnderTest.roomSentence].exists, "the sentence shows before a tap")
        XCTAssertFalse(app.buttons["\(MixUnderTest.whyPrefix) \(MixUnderTest.roomPlain)"].exists,
                       "a pick with no sentence is a button")
        XCTAssertTrue(containing(app, MixUnderTest.roomPlain).exists, "the plain chip is gone")
        chip.tap()
        let line = app.staticTexts[MixUnderTest.roomSentence]
        let shown = line.waitForExistence(timeout: 10)
        evidence(app, "mix-50-room-why")
        XCTAssertTrue(shown, "the server's sentence did not show under the row — tree follows\n" + app.debugDescription)
        XCTAssertEqual("shown", chip.value as? String)
        chip.tap()
        XCTAssertTrue(waitUntilGone(line, timeout: 10), "a second tap did not hide the sentence")
        let played = (mixQueuesSent()).contains { ($0["action"] as? String) == "play" }
        XCTAssertFalse(played, "a tap on the chip played the song")
    }

    /// What the house's room queues were sent.
    @MainActor
    func mixQueuesSent(port: Int = 6601) -> [[String: Any]] {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/_test/queues-sent")!)
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        let answered = expectation(description: "the stub said what the queues were sent")
        var sent: [[String: Any]] = []
        URLSession.shared.dataTask(with: request) { data, _, _ in
            if let data, let rows = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] {
                sent = rows
            }
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 15)
        return sent
    }

    /// The phone's own pick with a sentence: the chip shows it, exactly as
    /// sent, under the row — and the row stays a pick still to come.
    @MainActor
    func testMix_APhonePickSaysWhyThisSong() throws {
        let (app, landed) = mixPlayLongwater(householdAutoplay: nil)
        XCTAssertTrue(landed, "the player tab's queue never drew")
        let chip = app.buttons
            .matching(NSPredicate(format: "label BEGINSWITH %@", MixUnderTest.whyPrefix))
            .firstMatch
        guard chip.waitForExistence(timeout: 40) else {
            print("=== NO WHY-THIS-SONG CHIP ON A PICK — tree follows ===")
            print(app.debugDescription)
            return XCTFail("no pick with a sentence drew a why-this-song button")
        }
        musicMoreScrollOnScreen(app, chip)
        chip.tap()
        let line = app.staticTexts
            .matching(NSPredicate(format: "label IN %@", MixUnderTest.phoneSentences))
            .firstMatch
        let shown = line.waitForExistence(timeout: 10)
        evidence(app, "mix-60-phone-why")
        XCTAssertTrue(shown, "neither of the server's sentences showed under the pick — tree follows\n" + app.debugDescription)
    }

    // MARK: - Autoplay follows the household's default until flipped here

    /// The house's default is off and this phone never flipped it: the start
    /// says nothing about autoplay, and the switch says off. A flip here goes
    /// to the session.
    @MainActor
    func testMix_AnUnflippedPhoneFollowsTheHouseholdsAutoplayDefault() throws {
        let (app, landed) = mixPlayLongwater(householdAutoplay: false)
        XCTAssertTrue(landed, "the player tab's queue never drew")
        XCTAssertTrue(
            phoneUpNextWaitForSent("POST", endingIn: "/up-next/sessions") { body in
                body["target"] != nil && body["autoplay"] == nil
            },
            "the start said autoplay although this phone never flipped it"
        )
        let toggle = phoneUpNextElement(app, PhoneUpNextUnderTest.autoplayID)
        guard toggle.waitForExistence(timeout: 20) else {
            XCTFail("no autoplay switch — tree follows\n" + app.debugDescription)
            return
        }
        musicMoreScrollOnScreen(app, toggle)
        evidence(app, "mix-70-household-off")
        XCTAssertEqual("0", toggle.value as? String, "the switch does not say the household's off")
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap()
        XCTAssertTrue(
            phoneUpNextWaitForSent("PATCH", endingIn: "") { $0["autoplay"] as? Bool == true },
            "the flip on did not reach the session"
        )
    }
}
