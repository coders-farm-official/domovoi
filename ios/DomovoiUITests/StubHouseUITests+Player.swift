import XCTest

/// What a Simulator can say about the **player**.
///
/// The player is infrastructure — `PlayerModel` and the fifteen rule files
/// around it — and it has **no screen of its own**. D49 put one instance on
/// ``ShellModel`` and D46 put the controls that drive it on five screens, so
/// what each of those screens offers is asserted in that screen's own file:
/// `StubHouseUITests+Podcasts`, `+Audiobooks`, `+Stations`, `+PhoneMedia` and
/// `+MusicMore`. This file holds the three things that belong to **none** of
/// them, and `design-notes/ios/overnight/player/MAP.md` §7.6 says why each is
/// the only kind of answer a Simulator can add to the package tests:
///
/// 1. **The app still works with the player linked in.** A new file in
///    `DomovoiKit` that registers a `NotificationCenter` observer, configures
///    `AVAudioSession`, or publishes to `MPNowPlayingInfoCenter` at the wrong
///    moment breaks *video* — and no package test can see that, because
///    `swift test` builds for macOS, where the whole `#if os(iOS)` half of
///    this item does not exist.
/// 2. **The player is built on first use and never at launch**, and there is
///    **no screen of its own**. The second half of what was
///    `testPlayer_NothingInTheAppOffersPhonePlaybackYet`: a Player tab, or any
///    harness screen, is still a thing that must not exist (Kamron's answer
///    46: no harness screen, the real buttons are what prove the player), and
///    laziness is measurable from the outside because an audio session
///    activated at launch stops whatever the person was already listening to.
/// 3. **The fixture really serves audio.** `fixtures/player.py` generates a
///    WAV and answers `Range:`. If it did not, every case the five screens
///    write against it would fail for a reason none of them owns.
///
/// What is **not** here: an accessibility audit and a Dynamic Type growth
/// measurement. Both need a screen, and the player has none; the five screens
/// that drive it each audit and measure their own.
extension StubHouseUITests {

    // MARK: - 1. the player must not break what already plays

    /// The video feed still plays with the whole player linked into the app.
    ///
    /// The same two screenshots three seconds apart as
    /// ``testAVideoPlaysInTheFeed()``, for a different reason: not whether the
    /// feed works — that case owns the question — but whether it *still*
    /// works now that `PlayerAudioSession`, `MPNowPlayingPublisher` and
    /// `AVQueueAudioEngine` are in the binary beside it.
    ///
    /// The failure this is here to catch is real and it is invisible to every
    /// package test: `AVAudioSession` is **process-wide**. A category set at
    /// launch, a `.playAndRecord` activation that outlives the mic, or an
    /// `MPRemoteCommandCenter` handler bound to a player that is gone, and
    /// the feed's audio stops or its `AVPlayer` never reaches `.readyToPlay`
    /// — while `swift test` on macOS, where none of that code compiles, stays
    /// green.
    ///
    /// `PlayerModel.activateSession` is written so it cannot do this: the
    /// session is configured on the **first play and never at launch**. The
    /// player itself is not even built until something asks for it
    /// (``ShellModel/player``), and opening Videos asks for nothing. This case
    /// is what makes that a measured fact rather than a sentence in a comment
    /// — and it matters more now than when it was written, because five
    /// screens do call `play`.
    @MainActor
    func testPlayer_TheVideoFeedStillPlaysWithThePlayerLinkedIn() throws {
        let (app, landed) = openVideos()
        XCTAssertTrue(landed, "the videos list never answered")
        XCTAssertFalse(fellBackToLocalMedia(in: app), "the shell gave up on the server")

        app.buttons["a long one.mp4"].firstMatch.tap()
        let counter = app.staticTexts["1 / 50"]
        let opened = counter.waitForExistence(timeout: 20)
        evidence(app, "player-10-feed-opened")
        XCTAssertTrue(
            opened,
            "the feed did not open — if this is new, the player's audio "
                + "session or its Now Playing publisher is reaching "
                + "AVFoundation at launch instead of on the first play"
        )

        Thread.sleep(forTimeInterval: 3)
        evidence(app, "player-11-feed-three-seconds-later")
        XCTAssertTrue(counter.exists, "the feed moved off the page by itself")
        XCTAssertEqual(
            XCUIApplication.State.runningForeground, app.state,
            "the app went away while a video played — "
                + StubHouseUITests.stateName(app.state)
        )
    }

    // MARK: - 2. and it must not have grown a surface of its own

    /// The player has **no screen and no tab**, and is not built at launch.
    ///
    /// This case was `testPlayer_NothingInTheAppOffersPhonePlaybackYet` and it
    /// had two halves. The first — no Player tab, no `Now playing` tab — is
    /// unchanged and is now the owner's decision rather than a gap: Kamron's
    /// answer 46 was *"no harness screen; the real play buttons are added in
    /// this same work, and that is what proves the player"*, so a screen of
    /// its own appearing is a regression and this is where it is caught.
    ///
    /// The second half asserted that nothing anywhere offered `play on this
    /// phone`. Five screens do now, and each asserts its own; what is
    /// asserted here instead is **Home**, which is the screen the app opens
    /// on: it must still offer nothing of the kind, because the player must
    /// not be built — let alone activate an audio session — because somebody
    /// launched the app. `PlayerAudioSession.swift:11-18` is the reason:
    /// a `.playback` session activated at launch stops whatever the person was
    /// already listening to.
    ///
    /// The wordings below are the ones `PlayerStrings` holds and the five
    /// screens draw. They are spelt out here rather than imported, like every
    /// other string this file matches on: a query that silently followed a
    /// renamed constant would stop testing what it claims to.
    @MainActor
    func testPlayer_TheAppOpensOnHomeWithNoPlayerScreenAndNoPlayer() throws {
        // The Chat tab is gated on the household's AI switch
        // (`chatAvailable(_:phoneCanChat:)` — `phoneCanChat` is `false` on
        // iOS, so the tab follows `features.ai` alone), and that switch is a
        // mutable global in the stub house that any `chat-ai` case leaves
        // where it last put it. `reset-fixtures` does NOT restore it; only
        // `chat-reset` does. Train S003 failed here with a three-tab bar —
        // home · music · videos — because the house was still AI-off from
        // whatever ran against port 6601 before. The app was right and the
        // case had no precondition, so set one rather than drop the tab from
        // the assertion: the bar being the four tabs it was is exactly what
        // this case is for. Before `launch()`, because the manifest is read
        // once at launch.
        resetChatHouse()
        let app = launch()
        XCTAssertTrue(houseName(in: app).waitForExistence(timeout: 20), "Home never drew")
        evidence(app, "player-20-home-has-no-player")
        XCTAssertFalse(fellBackToLocalMedia(in: app), "the shell gave up on the server")

        for tab in ["Home", "Music", "Videos", "Chat"] {
            XCTAssertTrue(app.buttons[tab].exists, "the phone bar lost its \(tab) tab")
        }
        // Only the two names the player itself would take. Home's
        // "everything" list draws a row per workspace route, so asserting
        // that `Podcasts` is absent anywhere in the app would fail on
        // something that has nothing to do with this branch.
        for absent in ["Player", "Now playing"] {
            XCTAssertFalse(
                app.buttons[absent].exists,
                "a \(absent) tab has appeared — the player's surface belongs "
                    + "to the branch that builds the screen"
            )
        }

        // **On Home**, and only on Home. The five screens that drive the
        // player each draw their own control and assert it in their own file;
        // Home drives nothing, so a play offer here means the player is being
        // built on launch — which is the audio session activated under
        // whatever the person was already listening to.
        //
        // `play on this device` is the wording MusicMore's playlist sheet
        // uses for its button, and `play <name>` is the row controls'. The
        // sheet's section label is neither: it is `this device`, the thing
        // rather than the verb, because a heading identical to the button
        // under it gives VoiceOver the same sentence twice
        // (``MusicMoreStrings/playHereLabel``). `play here` and `play on this
        // phone` are nobody's, and both are kept because a screen that grew
        // one would be a screen nothing in this batch decided.
        for offer in ["play on this phone", "play on this device", "play here"] {
            let found = app.descendants(matching: .button)
                .matching(NSPredicate(format: "label CONTAINS[c] %@", offer))
                .firstMatch
            XCTAssertFalse(
                found.exists,
                "Home offers \"\(offer)\" — the player must not be reachable "
                    + "from the screen the app opens on, because reading it is "
                    + "what builds it and the first play is what takes the "
                    + "audio session"
            )
        }

        // Still on Home, and still there: a launch that activated a session
        // and lost it would show up as the app going away.
        XCTAssertEqual(
            XCUIApplication.State.runningForeground, app.state,
            "the app did not survive a launch with the player linked in — "
                + StubHouseUITests.stateName(app.state)
        )
    }

    /// There is no `-player-harness` screen, and no other hidden way in.
    ///
    /// Kamron's answer 46 in as many words: *"No, lets wait until real
    /// buttons are added, they should be added here in the overall work we're
    /// doing here to publish a usable app"*. A harness screen is the thing
    /// this unit was asked **not** to build, so its absence is asserted the
    /// way every other decision in this file is — with the reason beside it
    /// — rather than left to a reader's memory of a conversation.
    ///
    /// It is checked by **launching with the argument**, not by reading the
    /// source: a flag the app ignores is the only honest proof that nothing
    /// reads it. The app must come up on Home exactly as it does without it.
    ///
    /// The app is built here rather than through ``launch(mode:paired:server:port:strip:push:pushDelay:)``
    /// because that helper takes no extra arguments and lives in a file this
    /// unit does not own. The three arguments below are the ones it would
    /// pass for a paired phone against the stub house, and nothing else.
    @MainActor
    func testPlayer_ThereIsNoHiddenHarnessScreen() throws {
        resetChatHouse()
        let app = XCUIApplication()
        app.launchArguments = [
            "-domovoi-server", "127.0.0.1:6601",
            "-domovoi-token", Self.token,
            "-UIPreferredContentSizeCategoryName", Self.ordinaryTypeSize,
            // The flag nothing may read.
            "-player-harness", "1",
        ]
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 30))
        XCTAssertTrue(
            houseName(in: app).waitForExistence(timeout: 20),
            "the app did not open on Home with -player-harness, which means "
                + "something read it — tree follows\n" + app.debugDescription
        )
        evidence(app, "player-21-no-harness-screen")
        for word in ["Player harness", "player harness", "Harness"] {
            XCTAssertFalse(
                containing(app, word).exists,
                "a harness screen answered \"\(word)\", and answer 46 said the "
                    + "real buttons are what prove the player"
            )
        }
    }

    // MARK: - 3. the fixture the next five branches build on

    /// The stub house serves real, seekable audio and a cover that can miss.
    ///
    /// Asked of the house directly rather than through the app, because on
    /// this branch nothing in the app asks: `fixtures/player.py` is the
    /// surface Podcasts, Audiobooks, Stations, PhoneMedia and MusicMore will
    /// all point `AVPlayer` at, and a fixture that answered JSON where those
    /// branches expect PCM would fail every one of their cases for a reason
    /// none of them owns.
    ///
    /// Four properties, and each one is something `AVPlayer` needs:
    ///
    /// * **A real WAV.** `AVPlayer` decodes the bytes; a plausible
    ///   `Content-Type` over an empty body reaches `.failed` and nothing else.
    /// * **`Accept-Ranges: bytes` and a working 206.** Without it there is no
    ///   seeking, and `PlayerModel.seek(to:)` — where the chapter jump, the
    ///   sleep timer's end-of-track seed and the saved-position resume all end
    ///   — cannot be exercised against the stub at all.
    /// * **Open, with no token.** Every audio and cover route on the real
    ///   server is open (`music.py:1271`, `music.py:1680`), which is the whole
    ///   reason the player hands `AVPlayer` a plain absolute URL instead of
    ///   repeating `VideoStream.swift`'s query-string token. A fixture that
    ///   demanded a header would make that design look fine here and break on
    ///   Kamron's house.
    /// * **A cover that 404s.** Track 9404 has none, which is the only way to
    ///   reach `PlayerArtwork.noteFailure` and the placeholder.
    @MainActor
    func testPlayer_TheStubHousePlaysTheFixturesAudio() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the player fixture back to its seed")

        let audio = try fetchOpen("/api/music/library/11/audio", range: nil)
        XCTAssertEqual(200, audio.code, "the library audio route is not open — \(audio.code)")
        XCTAssertEqual(
            "bytes", audio.headers["Accept-Ranges"],
            "the fixture does not advertise ranges, so nothing can seek in it"
        )
        XCTAssertTrue(
            (audio.headers["Content-Type"] ?? "").hasPrefix("audio/"),
            "the audio route answered \(audio.headers["Content-Type"] ?? "no type")"
        )
        // `RIFF....WAVE`: the four bytes at 0 and the four at 8. Checked
        // rather than the length alone, because a JSON error page is also
        // "some bytes" and would pass a size assertion.
        XCTAssertTrue(audio.body.count > 1024, "the audio body is \(audio.body.count) bytes")
        XCTAssertEqual("RIFF", Self.ascii(audio.body.prefix(4)))
        XCTAssertEqual("WAVE", Self.ascii(audio.body.dropFirst(8).prefix(4)))

        let slice = try fetchOpen("/api/music/library/11/audio", range: "bytes=0-1023")
        XCTAssertEqual(206, slice.code, "a Range request was answered \(slice.code), not 206")
        XCTAssertEqual(1024, slice.body.count, "the 206 is not the thousand bytes asked for")
        XCTAssertTrue(
            (slice.headers["Content-Range"] ?? "").hasPrefix("bytes 0-1023/"),
            "the 206 has no usable Content-Range — \(slice.headers["Content-Range"] ?? "none")"
        )

        let missing = try fetchOpen("/api/music/library/9404/cover", range: nil)
        XCTAssertEqual(
            404, missing.code,
            "the cover that is meant to be missing answered \(missing.code), so "
                + "the miss memo and the placeholder are both unreachable"
        )
    }

    // MARK: - 4. who the house's places are saved under

    /// The person picker in **Settings → Phones & browsers** records a
    /// listener, and the chip on Podcasts then names them.
    ///
    /// D11, and the one surface of it a package test cannot reach.
    /// `PhonesAndBrowsersTests` drive ``PlayerListenerStore`` and
    /// ``PlayerListenerModel`` directly and `PlayerServerPositionsTests` hold
    /// the `person_id` on the query and the body; what none of them can say
    /// is that the picker in Settings and the chip on a *different screen*
    /// are reading the same record, and that the person picked reaches the
    /// **bytes the phone sends** — which the stub house records and this case
    /// reads back (step 2b). They are two views over one install-wide
    /// store (`prefs.listenerPersonId`, `Prefs.kt:115`), and a second store
    /// built somewhere by accident would leave every package test passing
    /// while the chip stayed silent.
    ///
    /// ### Three launches, on purpose
    ///
    /// The record is in `UserDefaults` under ``PlayerListenerStore/key`` and
    /// **not** in the shell's blob, so it is one of the very few things that
    /// survives a relaunch even under `-domovoi-server` (which otherwise puts
    /// the shell on an `InMemoryShellStore`, `DomovoiApp.swift:95-115`). That
    /// is what makes the second launch worth making — a chip drawn from a
    /// value the first launch left behind is the whole claim — and it is also
    /// why the third launch exists: every other case in this suite asserts
    /// the chip is **absent**, which is true only of a phone that has never
    /// picked anybody. Leaving Chevy picked would redden Podcasts and
    /// Audiobooks from here to the end of the run, so this case puts the
    /// install back and **asserts that it did**.
    ///
    /// ### Nothing between the pick and the restore asserts
    ///
    /// `setUp` sets `continueAfterFailure = false`
    /// (`StubHouseUITests.swift:153`), so **one failed assertion ends the
    /// method where it stands**. Until review 1 of this batch the chip and
    /// the probe were asserted between the pick and the restore, so a failure
    /// in either left Chevy picked on the install — and XCTest runs selectors
    /// alphabetically, so every `testPodcasts_*` and `testAudiobooks_*` case
    /// that asserts the chip is **absent** runs after this one and would have
    /// failed for this one's reason: one failure becoming a page of them,
    /// pointing at the wrong unit.
    ///
    /// So the two findings are **gathered** into `chipDrew` and `asks`, the
    /// install is put back, and only then are they judged. Every helper used
    /// in between is assertion-free by construction — `openPodcasts`,
    /// `openAudiobooks(reset: false)`, `openBook` and `evidence` all return or
    /// attach rather than assert, and ``positionAsks()`` answers an empty list
    /// rather than failing. A teardown block would be the other way to do it;
    /// it is not used here because its closure is `@Sendable` and
    /// `XCTestCase` is not `Sendable`, so `self` could not be captured.
    ///
    /// ### Why Chevy
    ///
    /// `fixtures/people.py` seeds four — `Kamron`, `Chevy`, `Alex` and
    /// `alex ` — and Chevy is the only one of them that appears nowhere else
    /// in this app's chrome. `Kamron` is the house's own name in other
    /// fixtures and the two Alexes are a deliberate duplicate-name pair, so
    /// either would make a `label CONTAINS` query ambiguous.
    @MainActor
    func testPlayer_ThePersonPickerNamesTheListenerOnPodcasts() throws {
        // 1. Settings: pick a person.
        let settings = launch(push: "Settings")
        XCTAssertTrue(
            reachPhonesAndBrowsersFromSettings(settings),
            "never reached Settings → Phones & browsers, so the picker could "
                + "not be opened"
        )
        let picker = settings.buttons[PlayerListenerUnderTest.card]
        XCTAssertTrue(
            picker.waitForExistence(timeout: 20),
            "the panel draws no `\(PlayerListenerUnderTest.card)` picker — tree "
                + "follows\n" + settings.debugDescription
        )
        // The slot a phone that has never been asked is in, read off the
        // picker's **value** rather than its label: the label is the card's
        // name and never changes, so asserting it would pass over any pick.
        XCTAssertEqual(
            PlayerListenerUnderTest.thisDevice, picker.value as? String,
            "a fresh install does not read `\(PlayerListenerUnderTest.thisDevice)` — "
                + "either an earlier case left a listener picked, or the nil row "
                + "is not first (`listenerOptions`)"
        )
        picker.tap()
        let chevy = settings.buttons[PlayerListenerUnderTest.chevy]
        XCTAssertTrue(
            chevy.waitForExistence(timeout: 15),
            "the dropdown does not offer `\(PlayerListenerUnderTest.chevy)`, so "
                + "either /api/people did not answer or the card drew its "
                + "failure sentence — tree follows\n" + settings.debugDescription
        )
        chevy.tap()
        XCTAssertEqual(
            PlayerListenerUnderTest.chevy, picker.value as? String,
            "the picker still reads `\(picker.value as? String ?? "nothing")` after "
                + "a pick — the write did not land, or the card is not reading "
                + "the store back"
        )
        XCTAssertTrue(
            containing(settings, PlayerListenerUnderTest.pickedChevy)
                .waitForExistence(timeout: 15),
            "nothing said who this phone now saves under (`ConnectionPanel.kt:369-372`)"
        )
        evidence(settings, "player-30-listening-as-chevy")
        settings.terminate()

        // The install now holds a listener, and keeps holding one across
        // `-domovoi-server`. From here to `putTheListenerBack()` **nothing
        // asserts**: see "Nothing between the pick and the restore asserts".

        // 2. Podcasts, on a **fresh launch**: the chip names them.
        let (podcasts, landed) = openPodcasts()
        let chipDrew = landed
            && containing(podcasts, PlayerListenerUnderTest.chipChevy)
                .waitForExistence(timeout: 25)
        let podcastsTree = podcasts.debugDescription
        evidence(podcasts, "player-31-chip-names-chevy")
        podcasts.terminate()

        // 2b. The bytes. A chip is a label; this is the thing the feature is
        //     for — `person_id` on the request the phone actually sends.
        //     `AudiobookListenerTests` holds the query's shape and the
        //     remote's per-request read, but only the real app against the
        //     real route can say that the id the picker wrote is the id that
        //     leaves the phone. The stub house records every ask in order
        //     (`fixtures/audiobooks.py`, `position_asks`), and that probe had
        //     no reader at all until this review.
        //
        //     `reset: false` on purpose: a reset would empty the very list
        //     this step reads, and the seed is already in place from step 1's
        //     launches.
        let (books, booksLanded) = openAudiobooks(reset: false)
        let bookDrew = booksLanded && openBook(books, StubAudiobooks.dune)
        // **Opening the page is not what asks the house.** Train I003 failed
        // here with "the book's page read no position at all", and the page
        // was right: ``AudiobookDetailModel/refreshPlace()`` reads the
        // **phone's** place and nothing else, exactly as Android's
        // `produceState` does (`BookPage.kt:96-98`). The compare with the
        // server's slot is ``BookPositionSync/open(bookID:server:)``, which
        // runs when a book **starts** — so this case has to start one, and
        // `not now` answers the question that raises without playing
        // anything. `testAudiobooks_ABookPageShowsItsChaptersAndPlace` drives
        // the same two taps for the same reason.
        //
        // Nothing here asserts: step 3 has to put the install's listener
        // back first. A tap that found nothing leaves `reads` empty and the
        // verdict below says so.
        let start = audiobooksStart(books)
        if bookDrew, start.waitForExistence(timeout: 25) {
            start.tap()
            let notNow = books.buttons[StubAudiobooks.notNow].firstMatch
            if notNow.waitForExistence(timeout: 25) { notNow.tap() }
        }
        let reads = positionAsks()
            .filter { $0["method"] == "GET" && $0["book"] == StubAudiobooks.duneID }
        evidence(books, "player-32-person-id-on-the-wire")
        books.terminate()

        // 3. Put the install back **before judging anything**: every other
        //    case in this suite is written against a phone with no listener
        //    picked, and an assertion above this line would end the method
        //    with Chevy still on the install.
        putTheListenerBack()

        // 4. Now the verdicts.
        XCTAssertTrue(
            chipDrew,
            "Podcasts draws no `\(PlayerListenerUnderTest.chipChevy)` chip after a "
                + "listener was picked in Settings — the two surfaces are not "
                + "reading one store — tree follows\n" + podcastsTree
        )
        XCTAssertTrue(bookDrew, "Dune's page never drew, so no position was read")
        XCTAssertFalse(
            reads.isEmpty,
            "starting Dune read no position from the house at all, so this case "
                + "says nothing about the listener on the wire — either the page "
                + "drew no `play`/`resume` to tap (``AudiobooksModel/canPlay(_:)``) "
                + "or `BookPositionSync.open` never went up"
        )
        XCTAssertEqual(
            PlayerListenerUnderTest.chevyID, reads.last?["person_id"],
            "the position read went up under "
                + (reads.last?["person_id"] ?? "no person")
                + " with \(PlayerListenerUnderTest.chevy) picked — a place saved "
                + "under the unidentified listener is a different row from "
                + "theirs (`spoken_audio.py:145-147`), which is the whole of D11"
        )
    }

    /// Put the install's listener back to "me (this device)".
    ///
    /// Step 3 of the picker case, lifted out and called **before** any of
    /// that case's verdicts, so a failing verdict cannot end the method with
    /// a person still picked on the install.
    ///
    /// It asserts nothing about having *found* a listener — only that it
    /// **leaves** the install on the nil row, which is the state every other
    /// case in the suite is written against. That the choice survived a
    /// relaunch is step 2's claim, made by the chip, and is not re-made here.
    @MainActor
    func putTheListenerBack() {
        let back = launch(push: "Settings")
        XCTAssertTrue(
            reachPhonesAndBrowsersFromSettings(back),
            "the listener is still `\(PlayerListenerUnderTest.chevy)` and Settings "
                + "could not be reached to put it back — the Podcasts and "
                + "Audiobooks chip cases will fail after this one"
        )
        let again = back.buttons[PlayerListenerUnderTest.card]
        XCTAssertTrue(again.waitForExistence(timeout: 20), "the picker is gone")
        // Already back: the inline step 3 ran and this is its teardown. That
        // the choice survives a relaunch is asserted by the chip in step 2,
        // so nothing is lost by returning here — and insisting on finding
        // Chevy would make a passing case fail in its own teardown.
        guard again.value as? String != PlayerListenerUnderTest.thisDevice else {
            back.terminate()
            return
        }
        again.tap()
        let me = back.buttons[PlayerListenerUnderTest.thisDevice]
        XCTAssertTrue(
            me.waitForExistence(timeout: 15),
            "the dropdown has no `\(PlayerListenerUnderTest.thisDevice)` row to go "
                + "back to — tree follows\n" + back.debugDescription
        )
        me.tap()
        XCTAssertEqual(
            PlayerListenerUnderTest.thisDevice, again.value as? String,
            "the listener was left on a person, and every chip-absent case after "
                + "this one will fail for that reason and not their own"
        )
        back.terminate()
    }

    // MARK: - 5. the card is measured, not just drawn (review 1, N4)

    /// Apple's audit over **Settings → Phones & browsers**, with the
    /// "Listening as" card on it.
    ///
    /// Review 1's N4: the card shipped unmeasured. Settings' own audit cases
    /// live in a file this unit does not own, and the card is the one new
    /// element on that panel — so the measurement belongs here, where the
    /// panel is already reached (``reachPhonesAndBrowsersFromSettings(_:)``)
    /// for the picker case above.
    ///
    /// **A floor by signature, never by total.** The panel is not this unit's
    /// and `StubHouseUITests.swift:55-72` records what it already had: three
    /// `Text clipped`, five `Contrast failed` and a hit-area finding, all
    /// since fixed, plus the eight nil-element Dynamic Type findings of
    /// ``knownPushedBarFloor`` that are an artefact of a pushed bar and not a
    /// defect. Asserting a count here would make every unrelated Settings
    /// change this unit's failure. So this asserts the two things that are
    /// about the card: that no finding names it, and that the anonymous floor
    /// has not grown.
    ///
    /// The card is a `Menu` whose label carries ``ShellStrings/listeningAs(_:)``
    /// as its `accessibilityValue` — a tick on the chosen row rather than a
    /// highlight, because a menu row's selected state is not read out. The
    /// audit is taken with the dropdown **shut**: the open menu is
    /// SpringBoard's own presentation and a finding on it would not be this
    /// app's to fix.
    @MainActor
    @available(iOS 17.0, *)
    func testPlayer_TheListeningAsCardAudits() throws {
        let app = launch(push: "Settings")
        // **Reach the panel, let the house's banner come and go, and go back
        // in if it took the panel with it.**
        //
        // Train I003 audited the Settings *index*: the panel was reached at
        // t=7.3s, the card was found at t=8.35s, and the house's own alert
        // banner arrived at t=8.39s. `auditIssues` waited it out — correctly
        // — and audited at t=15.2s, by which time the panel had gone. A
        // banner takes the scene to `.inactive` and back,
        // `ShellModel.scenePhaseChanged` runs `refresh()` on the way back in,
        // and `SettingsScreen`'s `place` is a `@State` that was at `.index`
        // again. The audit then reported the index's eighteen anonymous
        // Dynamic Type findings — and three `Contrast failed` naming `Voice`
        // and `System` — as this card's.
        //
        // Waiting for the banner *before* navigating does not help: it is
        // posted about eight seconds into the launch, which is while the two
        // taps below are being made, and `waitForNoNotificationBanner`'s
        // `appearIn` would have to cover the whole of that. So the wait is
        // here instead, with the panel already drawn — the one place the
        // bounce can be seen — and the panel is re-entered when it has gone.
        // Two attempts, because the banner is posted once per launch at
        // `start()`: a second one means something else is going on and the
        // case should say so rather than loop.
        //
        // (The reset itself is Settings', not this unit's — it is written up
        // under "Needs a shared change" in DECISIONS.md. This is a case
        // working around another unit's defect, not a fix for it.)
        let picker = app.buttons[PlayerListenerUnderTest.card]
        for attempt in 1...2 {
            XCTAssertTrue(
                reachPhonesAndBrowsersFromSettings(app),
                "never reached Settings → Phones & browsers on attempt \(attempt), so "
                    + "the card could not be measured"
            )
            XCTAssertTrue(
                picker.waitForExistence(timeout: 20),
                "the panel draws no `\(PlayerListenerUnderTest.card)` card to audit "
                    + "— tree follows\n" + app.debugDescription
            )
            let banner = waitForNoNotificationBanner(
                "player-listening-as/attempt-\(attempt)",
                appearIn: 6
            )
            if picker.exists { break }
            XCTAssertNotNil(
                banner,
                "the `\(PlayerListenerUnderTest.card)` card went away on its own and no "
                    + "banner explains it — tree follows\n" + app.debugDescription
            )
            print(
                "LISTENING AS the banner at \(banner.map { "\($0)" } ?? "?") took Settings "
                    + "back to its index — going in again"
            )
        }

        let found = try auditIssues(app, "player-listening-as")
        evidence(app, "player-70-listening-as-audit")
        // **An audit only covers the state you launch.** The card has to
        // still be there when the audit finishes, or every finding below is
        // about whatever took its place — tree printed, because a failure
        // here is about where the app went.
        XCTAssertTrue(
            picker.exists,
            "the `\(PlayerListenerUnderTest.card)` card was gone by the time Apple's "
                + "audit finished, so these findings are another screen's — tree "
                + "follows\n" + app.debugDescription
        )
        let split = Self.floorAndRest(found)
        print("LISTENING AS AUDIT floor=\(split.floor) rest=\(split.rest.count)")
        for line in split.rest { print("LISTENING AS AUDIT REST :: \(line)") }

        // Named findings about the card itself. `label=` carries the card's
        // name and `value=` the sentence under it, so either naming it is
        // this unit's.
        let mine = split.rest.filter {
            $0.contains(PlayerListenerUnderTest.card)
                || $0.contains(PlayerListenerUnderTest.thisDevice)
                || $0.contains(PlayerListenerUnderTest.chevy)
        }
        XCTAssertEqual(
            [], mine,
            "the `\(PlayerListenerUnderTest.card)` card has an audit finding of "
                + "its own"
        )
        // **The anonymous floor, as an equality — and nobody has measured
        // this panel's yet.**
        //
        // This line was `split.floor <= knownPushedBarFloor` and train I003
        // failed it with 18 against 8. Neither number was a reading of
        // Phones & browsers: the 18 came off the Settings index (see the
        // bounce above) and the 8 is ``knownPushedBarFloor``, a figure
        // measured on other pushed screens. A `<=` is the wrong shape as
        // well, for the reason the shared helper gives: a floor that *grew*
        // is the thing worth seeing, and a bound hides exactly the direction
        // this unit's card can move it in.
        //
        // So it is an equality against the pushed-bar figure, and **the
        // first train to run this case with the panel actually on screen is
        // the measurement**. The `print` above is where to read it: if the
        // number it reports is all `element: nil` Dynamic Type lines, that is
        // the number to pin here. This is Rooms' arrangement for its detail
        // panel (`StubHouseUITests+Rooms.swift`), and it is a guard rather
        // than the nothing the fix for I003 first left behind — the project
        // rule asks for a floor by signature, not for no floor, and
        // `floorAndRest` is what makes this one by signature: only the two
        // anonymous Dynamic Type headlines are counted here and everything
        // Apple could attribute is asserted above.
        XCTAssertEqual(
            split.floor, Self.knownPushedBarFloor,
            "Phones & browsers reports \(split.floor) anonymous Dynamic Type findings "
                + "and the pushed-bar figure is \(Self.knownPushedBarFloor). This panel's "
                + "own floor has never been measured — train I003 graded the Settings "
                + "index instead — so if the \(split.floor) are all `element: nil` bar "
                + "findings, that is the number to pin here"
        )
    }

    /// Every position ask the stub house has been sent this run, in order.
    ///
    /// `GET /api/_test/audiobooks-position-asks` — the probe
    /// `fixtures/audiobooks.py` serves, which had no reader at all until
    /// review 1 of this batch. Each row is flattened to strings rather than
    /// decoded, so that a `person_id` the app sent as a **number** and one it
    /// sent as a **string** read differently here: `"2"` against `2` is
    /// exactly the confusion the route's `Optional[int]` turns into a 422,
    /// and a probe that normalised them would hide it.
    ///
    /// **It asserts nothing and throws nothing**: its one caller runs it
    /// before putting the install's listener back, where a failure would end
    /// the method with a person still picked. A house that did not answer
    /// gives an empty list, and the caller's own `XCTAssertFalse(reads
    /// .isEmpty)` — after the restore — is what reports it.
    @MainActor
    func positionAsks() -> [[String: String]] {
        guard let answer = try? fetchOpen(
            "/api/_test/audiobooks-position-asks", range: nil
        ), answer.code == 200 else { return [] }
        let object = try? JSONSerialization.jsonObject(with: answer.body)
        let rows = ((object as? [String: Any])?["asks"] as? [[String: Any]]) ?? []
        return rows.map { row in
            var flat: [String: String] = [:]
            for (key, value) in row where !(value is NSNull) {
                flat[key] = String(describing: value)
            }
            return flat
        }
    }

    /// Get to **Settings → Phones & browsers** on a **paired** phone.
    ///
    /// ``reachPhonesAndBrowsers(_:)`` is the unpaired way in and cannot serve
    /// here: an unpaired phone's `/api/people` is `require_device_read`
    /// (`people.py:87`, and the fixture serves the refusal), so the card
    /// would draw ``PlayerStrings/couldNotLoadPeople(_:)`` and there would be
    /// nobody to pick. This is the same two taps
    /// ``systemSettingsReach(_:group:section:)`` makes, spelt out here
    /// because `Phones & browsers` is not one of its `Section` cases and that
    /// file belongs to another unit.
    ///
    /// Through `-domovoi-audit-push Settings` and **not** Home's everything
    /// grid, for that file's reason: the Settings tile is thirteen tiles down
    /// a screen that scrolls, and a tap on an element XCUITest says exists
    /// but has not scrolled into view lands on whatever is at that
    /// coordinate.
    @MainActor
    func reachPhonesAndBrowsersFromSettings(_ app: XCUIApplication) -> Bool {
        let group = app.buttons[PlayerListenerUnderTest.roomsAndDevices]
        guard group.waitForExistence(timeout: 30) else {
            print("SETTINGS INDEX NOT REACHED — tree follows")
            print(app.debugDescription)
            return false
        }
        group.tap()
        let section = app.buttons[PlayerListenerUnderTest.phonesAndBrowsers]
        guard section.waitForExistence(timeout: 20) else {
            print("`Rooms & devices` HAS NO `Phones & browsers` — tree follows")
            print(app.debugDescription)
            return false
        }
        section.tap()
        guard app.staticTexts[ShellStringsUnderTest.householdToken]
            .waitForExistence(timeout: 20) else {
            print("PHONES & BROWSERS DREW NO TOKEN CARD — tree follows")
            print(app.debugDescription)
            return false
        }
        return true
    }

    /// A plain GET of the stub house, with **no device token**.
    ///
    /// ``control(_:_:port:what:)`` sends one and is a POST; these routes are
    /// open and the absence of the header is half of what is being asserted,
    /// so this does not reuse it.
    @MainActor
    func fetchOpen(
        _ path: String,
        range: String?,
        port: Int = 6601
    ) throws -> (code: Int, headers: [String: String], body: Data) {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)\(path)")!)
        if let range { request.setValue(range, forHTTPHeaderField: "Range") }
        let answered = expectation(description: "the stub answered \(path)")
        var code = -1
        var headers: [String: String] = [:]
        var body = Data()
        URLSession.shared.dataTask(with: request) { data, response, _ in
            if let http = response as? HTTPURLResponse {
                code = http.statusCode
                for (key, value) in http.allHeaderFields {
                    if let name = key as? String, let text = value as? String {
                        headers[name] = text
                    }
                }
            }
            body = data ?? Data()
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 20)
        return (code, headers, body)
    }

    /// Some bytes of a file header, as text.
    ///
    /// Lossy on purpose: a failed assertion that says what the bytes were not
    /// says more than one that says `nil`, which is what
    /// `String(data:encoding:)` answers for anything that is not valid UTF-8.
    static func ascii(_ bytes: some Sequence<UInt8>) -> String {
        String(decoding: Array(bytes), as: UTF8.self)
    }
}

/// The words `testPlayer_ThePersonPickerNamesTheListenerOnPodcasts` matches
/// on, spelt out rather than imported like every other string in this file: a
/// query that silently followed a renamed constant would stop testing what it
/// claims to.
enum PlayerListenerUnderTest {

    /// ``PlayerStrings/listeningAsCard``, which is both the card's title and
    /// the picker's accessibility label.
    static let card = "Listening as"

    /// ``PlayerStrings/listenerThisDevice`` — the nil row, and the picker's
    /// value for a phone that has never been asked.
    static let thisDevice = "me (this device)"

    /// `fixtures/people.py`'s second person, chosen because the name appears
    /// nowhere else in the app's chrome.
    static let chevy = "Chevy"

    /// Their row id (`fixtures/people.py:104`), as the stub house records it
    /// off the query: a **string of the number**, because that is what
    /// `person_id=2` on a URL is. `"Chevy"` arriving here instead would be
    /// the app sending a name where the route types an int.
    static let chevyID = "2"

    /// ``ShellStrings/listeningAs(_:)`` for them.
    static let pickedChevy = "listening as Chevy"

    /// ``PlayerStrings/listeningAsChip(_:)`` for them, which is the same
    /// sentence — matched on a different screen, which is the point.
    static let chipChevy = "listening as Chevy"

    /// The Settings index's group and section
    /// (`SettingsSections.swift:88`).
    static let roomsAndDevices = "Rooms & devices"
    static let phonesAndBrowsers = "Phones & browsers"
}
