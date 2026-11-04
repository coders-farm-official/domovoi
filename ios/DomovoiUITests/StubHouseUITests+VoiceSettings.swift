import XCTest

/// Settings → **Voices**, and the three signpost sections beside it (Wake
/// words, Greetings, Voice history).
///
/// The fixture is `design-notes/ios/stubhouse/fixtures/voicesettings.py`, and
/// every value in its seed was chosen so a case here can only pass if the
/// thing it names really works — the house speaks with **Linda**, which is
/// neither the server's default nor Android's; exactly **one** of the three
/// voices has a sample the server can speak; the "say it like" list holds two
/// words that appear nowhere else on the screen. The module's docstring says
/// which case each of those is for.
///
/// Reset first, always: the cases share one house for the whole run
/// (`control("reset-fixtures", …)`), and two of them arm a control that would
/// otherwise still be armed for the next.
extension StubHouseUITests {

    // MARK: - what the fixture holds, by the words that appear on screen

    /// The three voices' `model_ref`s, which are the ids the play buttons
    /// carry and the sample route is keyed by.
    fileprivate enum VoiceSettingsStub {
        static let norman = "en_US-norman-medium"
        static let kristin = "en_US-kristin-medium"
        static let ljspeech = "en_US-ljspeech-medium"

        /// The house's voice in the seed — **Linda**, deliberately not the
        /// default on either platform.
        static let houseVoice = "Linda (female)"
        /// The two names the house's voice is not, asserted absent from the
        /// house's-voice element.
        static let notTheHouseVoice = ["Kristin", "Norman"]

        /// An id outside `PIPER_PINS`, set by the control for the unlisted
        /// case. A real `settings.tts_piper_voice` can hold anything.
        static let unlisted = "en_GB-alba-low"

        /// The seeded "say it like" entries. `Caoimhe` is the discriminating
        /// one: Android's built-in list is `Domovoi, Domo, Nguyen`, so
        /// `Nguyen` alone is a word a built-in list would also contain.
        static let saidAs = [("Nguyen", "Nguyen → win"), ("Caoimhe", "Caoimhe → kee-va")]
        static let twoEntries = "(2 entries)"
        /// Android's built-in wording, which must never appear here.
        static let builtInList = "Domo, Nguyen"

        /// The fixture's fail detail, a sentence that exists nowhere else.
        static let failDetail = "the voice settings read is broken on purpose"
        static let readFailed = "Could not read the voice settings"

        /// What the panel is not allowed to draw — Android's download-half
        /// controls, each of which would set a value nothing reads or start
        /// work nothing can finish.
        static let forbiddenControls = ["download", "pause download", "delete", "allow mobile data"]
        /// The picker's first option (`PhoneVoiceChoice.SAME_AS_SERVER`).
        static let forbiddenPicker = "Same as the server"

        /// The paragraph whose growth the audit case measures, by its opening
        /// words so a later edit to the rest of it does not silently stop the
        /// measurement.
        static let notOnThisPhoneOpening = "This app does not speak."

        static let settingsTile = "Settings"
        static let voiceGroup = "Voice"
        static let privacyGroup = "Privacy"
        static let voicesSection = "Voices"
        static let wakeWordsSection = "Wake words"
        static let greetingsSection = "Greetings"
        static let voiceHistorySection = "Voice history"
        static let onTheDashboard = "On the dashboard"
    }

    // MARK: - getting there

    /// Home → Settings → a group → a section.
    ///
    /// The `Settings` tile rather than anything in the chrome: it is a real
    /// button with a stable label (``HomeEverythingView``), and Settings is on
    /// the everything grid precisely because it is not on the default phone
    /// bar. Arrival is by a marker only the **destination** can draw — never
    /// by the breadcrumb, which ``ShellTopBar`` renders from
    /// `model.navigation.route` the instant `path` changes, before the
    /// destination has laid out. That confound cost this file three runs of
    /// meaningless zeroes.
    @MainActor
    fileprivate func reachVoiceSettings(
        _ app: XCUIApplication,
        group: String,
        section: String,
        marker: XCUIElement
    ) -> Bool {
        let tile = app.buttons[VoiceSettingsStub.settingsTile]
        guard tile.waitForExistence(timeout: 30) else {
            print("=== NO SETTINGS TILE — tree follows ===")
            print(app.debugDescription)
            return false
        }
        tile.tap()
        let groupButton = app.buttons[group]
        guard groupButton.waitForExistence(timeout: 20) else {
            print("=== NO \(group) GROUP — tree follows ===")
            print(app.debugDescription)
            return false
        }
        groupButton.tap()
        let sectionButton = app.buttons[section]
        guard sectionButton.waitForExistence(timeout: 20) else {
            print("=== NO \(section) SECTION — tree follows ===")
            print(app.debugDescription)
            return false
        }
        sectionButton.tap()
        guard marker.waitForExistence(timeout: 25) else {
            // The tree, not a sentence: "the section did not draw" is a claim
            // about something that is not there, and what *is* there is the
            // only useful answer.
            print("=== \(section) DREW NO MARKER — tree follows ===")
            print(app.debugDescription)
            return false
        }
        return true
    }

    /// The house's-voice element, which is **its own element** and not the
    /// voices list.
    ///
    /// By identifier, not by label. Three rows name a voice and one of them
    /// names the same voice the house speaks with, so a query on the words
    /// would take whichever the tree happened to hand back first — the
    /// `firstMatch` trap this track has paid for twice.
    @MainActor
    fileprivate func houseVoiceElement(_ app: XCUIApplication) -> XCUIElement {
        app.descendants(matching: .any)["voicesettings-house-voice"]
    }

    /// Scroll the section's own scroll view until `element` is hittable.
    ///
    /// Not ``scrollUntilBuilt(_:_:flicks:)``: that one drives
    /// `app.collectionViews`, and a Settings section is a `ScrollView` over a
    /// plain `VStack` — every card is built, some of them are simply below
    /// the fold, so the question is hittability and not existence.
    @MainActor
    fileprivate func scrollVoiceSettingsTo(_ app: XCUIApplication, _ element: XCUIElement) -> Bool {
        if element.isHittable { return true }
        let page = app.scrollViews.firstMatch
        guard page.waitForExistence(timeout: 15) else {
            print("=== NO SCROLL VIEW IN THE SECTION — tree follows ===")
            print(app.debugDescription)
            return false
        }
        for _ in 0..<12 {
            if element.isHittable { return true }
            page.swipeUp(velocity: .slow)
        }
        if !element.isHittable {
            print("=== \(element) NEVER BECAME HITTABLE — tree follows ===")
            print(app.debugDescription)
        }
        return element.isHittable
    }

    /// Every voice id a sample has been asked for, read back out of the
    /// fixture.
    ///
    /// This is how a case proves the button was wired to the row it sits on
    /// **and that the app asked for nothing else** — the second half is the
    /// one a screenshot cannot answer.
    @MainActor
    fileprivate func voiceSettingsAsked(port: Int = 6601) -> [String] {
        var request = URLRequest(
            url: URL(string: "http://127.0.0.1:\(port)/api/_test/voicesettings-asked")!
        )
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        let answered = expectation(description: "the stub said which samples were asked for")
        var asked: [String] = []
        URLSession.shared.dataTask(with: request) { data, _, _ in
            if let data,
               let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let list = body["asked"] as? [String] {
                asked = list
            }
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 15)
        return asked
    }

    // MARK: - the voices section

    /// The house's voice is **named**, and it is the one the server said.
    ///
    /// This is the case that fails when the slot was never filled, when the
    /// read was never wired, and — the defect class that left four of Home's
    /// sections unaudited for a week — when the decoder reads the wrong key or
    /// the wrong wrapper, because a read that never decodes looks exactly like
    /// a house with nothing in it.
    ///
    /// Discriminating on purpose: the seed's `server_voice` is **Linda**,
    /// which is neither the server's default (Kristin, since 2026-10-12) nor
    /// Android's `ModelCatalog.defaultVoice`, so a port that substituted a
    /// default — as `PhoneVoiceChoice.effective` legitimately does, because
    /// Android's phone has to speak with *something* — draws "Kristin" and
    /// fails here.
    @MainActor
    func testVoiceSettings_VoicesSectionNamesTheHousesVoice() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the voices section")
        let app = launch()
        let house = houseVoiceElement(app)
        XCTAssertTrue(
            reachVoiceSettings(
                app,
                group: VoiceSettingsStub.voiceGroup,
                section: VoiceSettingsStub.voicesSection,
                marker: house
            ),
            "never reached Settings → Voices"
        )
        evidence(app, "voicesettings-10-voices")

        XCTAssertEqual(
            VoiceSettingsStub.houseVoice, house.label,
            "the house's voice is not the one the server reported"
        )
        for other in VoiceSettingsStub.notTheHouseVoice {
            XCTAssertFalse(
                house.label.contains(other),
                "the house's-voice element names \(other), which is not what the server said"
            )
        }
        // And the list is a different thing from the card: a panel that drew
        // only the house's voice would pass the assertions above.
        for id in [VoiceSettingsStub.norman, VoiceSettingsStub.kristin, VoiceSettingsStub.ljspeech] {
            XCTAssertTrue(
                app.descendants(matching: .any)["voicesettings-voice-\(id)"].exists,
                "the voices list is missing \(id)"
            )
        }
    }

    /// A voice this app does not list is **named, not swallowed**.
    ///
    /// `server_voice` is whatever `settings.tts_piper_voice` holds when the
    /// registry's default is not a shipped voice
    /// (`domovoi/voice_settings.py:143-152`), so this state is reachable and
    /// not theoretical. Printing "Kristin" for a house that speaks with
    /// something else would be a false reading rather than a fallback.
    @MainActor
    func testVoiceSettings_AnUnlistedServerVoiceIsNamedNotSwallowed() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the unlisted voice")
        control(
            "voicesettings-server-voice",
            "{\"voice\":\"\(VoiceSettingsStub.unlisted)\"}",
            port: 6601,
            what: "a house speaking with a voice outside the three"
        )
        let app = launch()
        let house = houseVoiceElement(app)
        XCTAssertTrue(
            reachVoiceSettings(
                app,
                group: VoiceSettingsStub.voiceGroup,
                section: VoiceSettingsStub.voicesSection,
                marker: house
            ),
            "never reached Settings → Voices"
        )
        evidence(app, "voicesettings-11-unlisted")

        XCTAssertTrue(
            house.label.contains(VoiceSettingsStub.unlisted),
            "the raw voice id is not on screen: \(house.label)"
        )
        XCTAssertFalse(
            house.label.contains("Kristin"),
            "an unlisted server voice was replaced with a default: \(house.label)"
        )
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures back after the unlisted voice")
    }

    /// A play button **only where the server says it can really speak that
    /// voice** — the no-misleading-controls rule, as a case.
    ///
    /// The fixture's samples map is one of each: `"voice"` for Norman,
    /// `"clip"` for Kristin, `null` for Linda. A `"clip"` arrives as Ogg Opus
    /// (`CLIP_MEDIA_TYPE`, `domovoi/voice_sample.py:22`), which
    /// `AVAudioPlayer` does not decode, so a button there would fetch bytes
    /// and play silence — the exact failure `PhoneVoice.kt:371` records
    /// Android shipping once, with all three voices sounding the same.
    ///
    /// So: **one** button, on Norman's row. A port that drew a button per row
    /// fails on the count; a port that drew it on the wrong row fails on the
    /// identifier.
    @MainActor
    func testVoiceSettings_PlayButtonOnlyWhereTheServerCanSpeakIt() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the play buttons")
        let app = launch()
        let play = app.buttons["voicesettings-play-\(VoiceSettingsStub.norman)"]
        XCTAssertTrue(
            reachVoiceSettings(
                app,
                group: VoiceSettingsStub.voiceGroup,
                section: VoiceSettingsStub.voicesSection,
                marker: play
            ),
            "never reached Settings → Voices with a play button on it"
        )
        evidence(app, "voicesettings-12-one-button")

        for id in [VoiceSettingsStub.kristin, VoiceSettingsStub.ljspeech] {
            XCTAssertFalse(
                app.buttons["voicesettings-play-\(id)"].exists,
                "there is a play button on \(id), which this server cannot speak"
            )
            XCTAssertTrue(
                app.descendants(matching: .any)["voicesettings-no-sample-\(id)"].exists,
                "\(id) has neither a button nor a line saying why not"
            )
        }
        // By label as well as by identifier, because the count is the half
        // that catches "a button per row" however it was identified.
        let byLabel = app.buttons.matching(NSPredicate(format: "label == %@", "play a sample"))
        XCTAssertEqual(1, byLabel.count, "there is not exactly one `play a sample` button")
    }

    /// The sample asks for **the voice its row names**, and for nothing else.
    ///
    /// Read back from the fixture, which records every id a sample was asked
    /// for. A button wired to the wrong row fails on the first assertion; a
    /// panel that prefetched every voice's sample — or asked for the Ogg clip
    /// it must never ask for — fails on the second.
    @MainActor
    func testVoiceSettings_SamplePlaysTheVoiceItNames() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the sample tap")
        let app = launch()
        let play = app.buttons["voicesettings-play-\(VoiceSettingsStub.norman)"]
        XCTAssertTrue(
            reachVoiceSettings(
                app,
                group: VoiceSettingsStub.voiceGroup,
                section: VoiceSettingsStub.voicesSection,
                marker: play
            ),
            "never reached Settings → Voices with a play button on it"
        )
        XCTAssertEqual(
            [], voiceSettingsAsked(),
            "a sample was asked for before anything was tapped"
        )
        XCTAssertTrue(scrollVoiceSettingsTo(app, play), "the play button never became hittable")
        play.tap()

        // Poll rather than sleep: the request is the app's, and how long it
        // takes to leave is not this case's subject.
        var asked: [String] = []
        let deadline = Date().addingTimeInterval(25)
        while Date() < deadline {
            asked = voiceSettingsAsked()
            if !asked.isEmpty { break }
            usleep(400_000)
        }
        evidence(app, "voicesettings-13-sample-asked")
        XCTAssertEqual(
            [VoiceSettingsStub.norman], asked,
            "the sample asked for the wrong voice, or for more than one"
        )
        // The WAV the fixture builds is a real one, so a failure line here is
        // the app's and not the stub's: either it asked for the wrong body or
        // it could not play a body it can play.
        let trouble = app.descendants(matching: .any)["voicesettings-sample-failed"]
        XCTAssertFalse(
            trouble.exists,
            "the sample the server rendered could not be played: "
                + (trouble.exists ? trouble.label : "")
        )
    }

    /// The "say it like" list on screen is **the server's**.
    ///
    /// Both seeded words, and the count line. `Caoimhe` is the discriminating
    /// one: Android's built-in list is `Domovoi, Domo, Nguyen`
    /// (`VoicePanel.kt:106`), so a panel that drew a built-in list could pass
    /// on `Nguyen` alone — which is why that wording is asserted absent too.
    @MainActor
    func testVoiceSettings_SayItLikeListIsTheServers() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the say-it-like list")
        let app = launch()
        let first = app.descendants(matching: .any)["voicesettings-said-as-Nguyen"]
        XCTAssertTrue(
            reachVoiceSettings(
                app,
                group: VoiceSettingsStub.voiceGroup,
                section: VoiceSettingsStub.voicesSection,
                marker: first
            ),
            "never reached Settings → Voices with a say-it-like list on it"
        )
        evidence(app, "voicesettings-14-say-it-like")

        for (word, row) in VoiceSettingsStub.saidAs {
            let entry = app.descendants(matching: .any)["voicesettings-said-as-\(word)"]
            XCTAssertTrue(entry.exists, "the list is missing \(word)")
            XCTAssertEqual(row, entry.label, "\(word)'s row does not say what the server says")
        }
        XCTAssertTrue(
            containing(app, VoiceSettingsStub.twoEntries).exists,
            "the count line does not say how many entries the server's list has"
        )
        XCTAssertFalse(
            containing(app, VoiceSettingsStub.builtInList).exists,
            "the panel drew a built-in list instead of the server's"
        )
    }

    /// The read failing is **said**, not drawn as an empty card.
    ///
    /// An empty card is the shape that let four of Home's sections go
    /// unaudited: a read that never decoded looked like a house with nothing
    /// in it. The fixture's detail is a sentence that exists nowhere else, so
    /// this cannot pass on a generic error wording.
    @MainActor
    func testVoiceSettings_TheReadFailingIsSaidNotHidden() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the failing read")
        control(
            "voicesettings-fail",
            "{\"how\":\"500\"}",
            port: 6601,
            what: "the voice settings read failing"
        )
        let app = launch()
        let failed = app.descendants(matching: .any)["voicesettings-read-failed"]
        XCTAssertTrue(
            reachVoiceSettings(
                app,
                group: VoiceSettingsStub.voiceGroup,
                section: VoiceSettingsStub.voicesSection,
                marker: failed
            ),
            "never reached Settings → Voices, or it said nothing about the failing read"
        )
        evidence(app, "voicesettings-15-read-failed")

        XCTAssertTrue(
            failed.label.contains(VoiceSettingsStub.readFailed),
            "the failure line does not say what failed: \(failed.label)"
        )
        XCTAssertTrue(
            failed.label.contains(VoiceSettingsStub.failDetail),
            "the failure line does not carry the server's own words: \(failed.label)"
        )
        XCTAssertFalse(
            houseVoiceElement(app).exists,
            "a failed read still drew a house's voice, which it cannot know"
        )
        // Disarmed here as well as reset by the next case: the control is
        // sticky on purpose (a one-shot failure would let a second read
        // quietly succeed), so leaving it armed would be leaving a trap.
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures back after the failing read")
    }

    /// **None of Android's download half is drawn.**
    ///
    /// The standing rule as a case, so a later "helpful" addition fails here
    /// rather than shipping: a picker that sets a value nothing reads, a
    /// download with nothing to download into, a delete with nothing
    /// installed, a mobile-data switch gating a download that cannot happen.
    ///
    /// Exact labels, not `CONTAINS`: ``VoiceSettingsStrings/notOnThisPhoneBody``
    /// legitimately contains the word "downloaded" while it explains why there
    /// is no download, and a substring check would fail on the explanation.
    @MainActor
    func testVoiceSettings_NoDownloadOrDeleteControl() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the absent controls")
        let app = launch()
        let house = houseVoiceElement(app)
        XCTAssertTrue(
            reachVoiceSettings(
                app,
                group: VoiceSettingsStub.voiceGroup,
                section: VoiceSettingsStub.voicesSection,
                marker: house
            ),
            "never reached Settings → Voices"
        )
        evidence(app, "voicesettings-16-no-download")

        for label in VoiceSettingsStub.forbiddenControls {
            let exact = app.descendants(matching: .any)
                .matching(NSPredicate(format: "label ==[c] %@", label))
            XCTAssertEqual(
                0, exact.count,
                "`\(label)` is on this screen, and nothing on this phone could do it"
            )
        }
        XCTAssertFalse(
            containing(app, VoiceSettingsStub.forbiddenPicker).exists,
            "the voice picker is on this screen, and a pick would set a value nothing reads"
        )
        // And the section says so rather than being silently thinner than
        // Android's.
        XCTAssertTrue(
            startingWith(app, VoiceSettingsStub.notOnThisPhoneOpening).exists,
            "the section does not say that this phone does not speak"
        )
    }

    // MARK: - the three signposts

    /// Wake words signposts the dashboard, **at its own section's address**.
    ///
    /// Catches the copy-paste that signposts the wrong section: the paragraph
    /// must name Wake words and the address must end in its page.
    @MainActor
    func testVoiceSettings_WakeWordsSignpostsTheDashboard() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the wake-words signpost")
        let app = launch()
        let open = app.buttons["voicesettings-open-dashboard"]
        XCTAssertTrue(
            reachVoiceSettings(
                app,
                group: VoiceSettingsStub.voiceGroup,
                section: VoiceSettingsStub.wakeWordsSection,
                marker: open
            ),
            "never reached Settings → Wake words with a dashboard link on it"
        )
        evidence(app, "voicesettings-20-wake-words")

        XCTAssertTrue(
            containing(app, VoiceSettingsStub.onTheDashboard).exists,
            "the signpost card is not there"
        )
        XCTAssertTrue(
            containing(app, "\(VoiceSettingsStub.wakeWordsSection) is changed on the Domovoi dashboard").exists,
            "the signpost does not name Wake words"
        )
        let address = app.descendants(matching: .any)["voicesettings-dashboard-address"]
        XCTAssertTrue(address.exists, "the signpost prints no address")
        XCTAssertTrue(
            address.label.hasSuffix("#settings/wake-words"),
            "the address is not Wake words' dashboard page: \(address.label)"
        )
    }

    /// Greetings signposts the dashboard, at its own section's address.
    ///
    /// The third of the item's four slots, and the one a review found
    /// untested: `VoiceSettingsDashboardTests` only exercises the URL builder
    /// against the section table, so a `greetings` slot left as
    /// `SettingsSectionNotBuilt()` — or wired with the wrong `section:`
    /// argument, the copy-paste the wake-words case was written to catch —
    /// would have shipped with nothing failing, the night's negative control
    /// included.
    @MainActor
    func testVoiceSettings_GreetingsSignpostsTheDashboard() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the greetings signpost")
        let app = launch()
        let open = app.buttons["voicesettings-open-dashboard"]
        XCTAssertTrue(
            reachVoiceSettings(
                app,
                group: VoiceSettingsStub.voiceGroup,
                section: VoiceSettingsStub.greetingsSection,
                marker: open
            ),
            "never reached Settings → Greetings with a dashboard link on it"
        )
        evidence(app, "voicesettings-22-greetings")

        XCTAssertTrue(
            containing(app, VoiceSettingsStub.onTheDashboard).exists,
            "the signpost card is not there"
        )
        XCTAssertTrue(
            containing(app, "\(VoiceSettingsStub.greetingsSection) is changed on the Domovoi dashboard").exists,
            "the signpost does not name Greetings"
        )
        let address = app.descendants(matching: .any)["voicesettings-dashboard-address"]
        XCTAssertTrue(address.exists, "the signpost prints no address")
        XCTAssertTrue(
            address.label.hasSuffix("#settings/greetings"),
            "the address is not Greetings' dashboard page: \(address.label)"
        )
    }

    /// Voice history's address is **`#settings/recordings`**.
    ///
    /// The one section whose key and whose dashboard page differ
    /// (`SettingsScreen.kt:140`, ported to `SettingsSections.swift`). A
    /// builder who reached for `section.key` passes every other section and
    /// sends this one to a page that does not exist — so both halves are
    /// asserted, the right page present and the key's page absent.
    @MainActor
    func testVoiceSettings_VoiceHistorySignpostsRecordings() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the voice-history signpost")
        let app = launch()
        let open = app.buttons["voicesettings-open-dashboard"]
        XCTAssertTrue(
            reachVoiceSettings(
                app,
                group: VoiceSettingsStub.privacyGroup,
                section: VoiceSettingsStub.voiceHistorySection,
                marker: open
            ),
            "never reached Settings → Voice history with a dashboard link on it"
        )
        evidence(app, "voicesettings-21-voice-history")

        XCTAssertTrue(
            containing(app, "\(VoiceSettingsStub.voiceHistorySection) is changed on the Domovoi dashboard").exists,
            "the signpost does not name Voice history"
        )
        let address = app.descendants(matching: .any)["voicesettings-dashboard-address"]
        XCTAssertTrue(address.exists, "the signpost prints no address")
        XCTAssertTrue(
            address.label.hasSuffix("#settings/recordings"),
            "Voice history does not point at the recordings page: \(address.label)"
        )
        XCTAssertFalse(
            address.label.contains("#settings/voice-history"),
            "Voice history points at its own key, which is not a dashboard page: \(address.label)"
        )
    }

    // MARK: - the audit

    /// The label an audit finding's element carries, out of the line
    /// ``StubHouseUITests/oneLine(_:)`` wrote.
    ///
    /// Searched **backwards** for the closing quote: this screen's labels
    /// contain apostrophes ("your Domovoi's voice"), so the first `'` after
    /// `label='` is inside the sentence and not the end of it. `frame=` is the
    /// last field on the line, which is what makes the backwards search exact.
    fileprivate static func voiceSettingsFindingLabel(in line: String) -> String? {
        guard let opened = line.range(of: "label='"),
              let closed = line.range(of: "' frame=", options: .backwards),
              opened.upperBound <= closed.lowerBound
        else { return nil }
        return String(line[opened.upperBound..<closed.lowerBound])
    }

    /// The scroll view the section is drawn in — the tallest one on screen.
    ///
    /// The other is the phone bar's, lying on its side (146 points tall
    /// against the section's 400-odd at AX5). Its `frame` is the rectangle
    /// the section is actually **painted** in, which is the rectangle a
    /// contrast reading has to come from.
    @MainActor
    fileprivate func voiceSettingsViewport(_ app: XCUIApplication) -> CGRect {
        app.scrollViews.allElementsBoundByIndex
            .max(by: { $0.frame.height < $1.frame.height })?
            .frame ?? .null
    }

    /// One audit of the Voices section, classified by **signature**, and the
    /// paragraph's growth **measured** at both type sizes.
    ///
    /// Two halves, for two reasons this track paid for:
    ///
    /// * `Self.floorAndRest` rather than a pinned total. A total moves with
    ///   every chrome change and tells nobody which finding moved; the floor
    ///   is the anonymous Dynamic Type headline the bars have always
    ///   reported, and anything Apple can attribute to an element is a defect.
    /// * the growth is **measured**, not read off a headline. A named Dynamic
    ///   Type finding has twice been a false positive on this track — the
    ///   eight flagged leaves grew 13.3 to 48.0 points — so the longest
    ///   paragraph on the screen is compared at `Self.ordinaryTypeSize` and at
    ///   `UICTContentSizeCategoryAccessibilityXXXL`, and it is the comparison
    ///   that decides.
    ///
    /// ### The two AX5 contrast findings, and why the colours are not what
    /// they are about
    ///
    /// Run S001 failed here on two `Contrast failed`, each naming an element:
    /// the `Settings` title at `(16, 155.7, 196, 67)` and the section's own
    /// explainer at `(16, 576.7, 351, 312.7)`. At AX5 the chrome above the
    /// section is 300 points tall, so the section is painted into a scroll
    /// view of `(0, 300.3, 393, 401.7)` — and **neither element is inside
    /// it**: the title is wholly above the viewport (it ends at 222.7, having
    /// scrolled off the top), and the explainer runs to 889.4, a third of it
    /// behind the phone bar. The screenshot beside that run shows it cut
    /// mid-word by the bar. Whatever the audit read at those coordinates, it
    /// was not those elements' pixels.
    ///
    /// That is a claim this case **measures** rather than argues. The `large`
    /// pass draws the same two elements, in the same colours, wholly inside
    /// its viewport — `Settings` at `(16, 153.4, 78.7, 26.3)`, the explainer
    /// at `(16, 263.8, 361, 33.7)` against a scroll view ending at 770 — and
    /// Apple reports **no** contrast finding there at all. Same tokens, same
    /// text, on screen: clean. So the `large` pass excuses nothing (its
    /// `rest` must be empty outright), and AX5 may only set aside a contrast
    /// finding whose element is both outside the viewport **and** one the
    /// `large` pass measured clean. A real colour defect fails at `large`,
    /// where every element fits; a new finding at AX5 against something that
    /// does fit still fails here. The same reading as
    /// ``StubHouseUITests/testTheVideosGridAudits``, which set aside a row the
    /// grid's bottom edge cut in half — widened because at AX5 an element can
    /// miss the viewport entirely rather than straddle it.
    ///
    /// ### The three findings of the first full-class run, and what the
    /// picture showed that the tree did not
    ///
    /// Run I001 failed the `large` half on three `Contrast failed`, each
    /// naming a shell-header element: `unverified` at `(299, 69.5, 52, 13.3)`,
    /// `  live` at `(351, 69, 26, 14.3)` and `127.0.0.1:6601` at
    /// `(203.3, 69, 79.7, 14.3)`. None of the three is this section's — they
    /// are `ShellChromeViews.swift`'s pill row, above the navigation bar at
    /// `y = 93.3` and so outside this screen's viewport entirely — and the
    /// floor was exactly the `knownPushedBarFloor` of 8, so the Voices
    /// section itself gave the audit nothing to say. **This case is not
    /// changed for them.**
    ///
    /// Their colours are not what they are about either, and the evidence is
    /// `127.0.0.1:6601`: it carries no `foregroundStyle` at all, so it is the
    /// primary label colour on a `.quaternary` capsule — near-black on light
    /// grey, which cannot fail a contrast reading. `  live` is `fgMuted` on
    /// the canvas it was measured against, which the project has passing.
    /// Three elements in three colours, all failing hard, all inside one
    /// 15-point band: that is one reading taken of something that is not
    /// them.
    ///
    /// The screenshot says what: a SpringBoard notification banner —
    /// `AlertsStrings.incidentTamperTitle`, "Signs of tampering" — covering
    /// the screen from `y ≈ 43` to `y ≈ 103`, over all three. It is in the
    /// evidence of every audit from `testAlerts_WhenAlertsAreOnALedgerFireIsDelivered`
    /// (10:18) onward — Home's at 11:42, About's at 12:57, this one's at
    /// 13:09 — and it reads `now` in each, so it is re-posted on launch, not
    /// one banner lingering. Notification authorisation is a fact about the
    /// install, so once that case grants it every later launch in the run
    /// redelivers the seeded incident of `fixtures/health.py` and every later
    /// audit reads the header through the banner.
    ///
    /// Dismissing it belongs in `StubHouseUITests.swift`'s own launch, not
    /// here: six cases across five units are reading through the same banner,
    /// and a private copy in this file would mend one of them. It is written
    /// up in `overnight/voicesettings/DECISIONS.md` under "Needs a shared
    /// change". What this case must keep doing meanwhile is report it, which
    /// is why nothing below is relaxed: a finding that names an element is a
    /// defect until a **measurement** says otherwise, and the measurement
    /// here says the defect is the banner rather than the ink.
    @MainActor
    func testVoiceSettings_Audit() throws {
        let ax5 = "UICTContentSizeCategoryAccessibilityXXXL"
        var heights: [String: CGFloat] = [:]
        // Every text the `large` pass drew **wholly inside** its viewport
        // while the audit found nothing to say about any of them. An AX5
        // finding may only be set aside against one of these.
        var cleanAtLarge: Set<String> = []

        for (name, size) in [("large", Self.ordinaryTypeSize), ("ax5", ax5)] {
            control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the \(name) audit")
            let app = launch(mode: size)
            let house = houseVoiceElement(app)
            XCTAssertTrue(
                reachVoiceSettings(
                    app,
                    group: VoiceSettingsStub.voiceGroup,
                    section: VoiceSettingsStub.voicesSection,
                    marker: house
                ),
                "never reached Settings → Voices at \(name)"
            )
            evidence(app, "voicesettings-30-audit-\(name)")

            let paragraph = startingWith(app, VoiceSettingsStub.notOnThisPhoneOpening)
            if paragraph.exists {
                heights[name] = paragraph.frame.height
                print("VOICESETTINGS [\(name)] paragraph \(paragraph.frame.height) points")
            } else {
                // Printed, not asserted here: the measurement's own assertion
                // is below, and a missing element there says more with both
                // sizes' numbers in hand.
                print("VOICESETTINGS [\(name)] no paragraph — tree follows")
                print(app.debugDescription)
            }

            let viewport = voiceSettingsViewport(app)
            XCTAssertFalse(
                viewport.isNull,
                "no scroll view at \(name) to take the painted rectangle from"
            )
            print("VOICESETTINGS [\(name)] viewport \(viewport)")

            let split = Self.floorAndRest(try auditIssues(app, "voicesettings-\(name)"))
            print("VOICESETTINGS [\(name)] floor \(split.floor), rest \(split.rest.count)")

            if name == "large" {
                // Nothing is excused at ordinary size: the whole section fits
                // the window here, so every finding is about the thing it
                // names. This is the pass that decides whether the colours
                // are right.
                XCTAssertTrue(
                    split.rest.isEmpty,
                    "the Voices section at \(name) has findings outside the measured floor: "
                        + split.rest.joined(separator: " / ")
                )
                // Recorded only after that assertion, so a screen with a real
                // contrast defect never supplies AX5 with an excuse for it.
                for text in app.staticTexts.allElementsBoundByIndex {
                    let label = text.label
                    guard !label.isEmpty, viewport.contains(text.frame) else { continue }
                    cleanAtLarge.insert(label)
                }
                print("VOICESETTINGS [large] \(cleanAtLarge.count) texts audited clean inside the viewport")
                XCTAssertFalse(
                    cleanAtLarge.isEmpty,
                    "the large pass measured no text inside its own viewport"
                )
            } else {
                var excused: [String] = []
                let unexplained = split.rest.filter { line in
                    guard line.hasPrefix("Contrast failed"),
                          let box = Self.frame(in: line),
                          // Outside the rectangle the section is painted in:
                          // there are no pixels of this element there to read.
                          !viewport.contains(box),
                          // And this very text was measured clean at ordinary
                          // size, where it did fit. Without this half, the
                          // viewport would excuse a genuinely bad colour the
                          // moment the element grew past the fold.
                          let label = Self.voiceSettingsFindingLabel(in: line),
                          cleanAtLarge.contains(label)
                    else { return true }
                    excused.append(line)
                    return false
                }
                for line in excused {
                    print("VOICESETTINGS [ax5] outside the viewport \(viewport), clean at large: \(line)")
                }
                XCTAssertTrue(
                    unexplained.isEmpty,
                    "the Voices section at \(name) has findings neither the floor nor the viewport explains: "
                        + unexplained.joined(separator: " / ")
                )
            }
            app.terminate()
        }

        guard let small = heights["large"], let big = heights["ax5"] else {
            return XCTFail(
                "the paragraph was not measurable at both sizes: \(heights)"
            )
        }
        // 1.5x is well under what the bars actually do (13.3 → 48.0 points is
        // 3.6x) and well over the noise of a layout that grew by a line. A
        // paragraph that did not grow at all is text with a fixed font, which
        // is the defect this half is for.
        XCTAssertGreaterThan(
            big, small * 1.5,
            "the paragraph did not grow with the type size: \(small) → \(big) points"
        )
    }
}
