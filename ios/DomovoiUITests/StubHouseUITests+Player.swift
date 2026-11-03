import XCTest

/// What a Simulator can say about the **player** on this branch.
///
/// The player is infrastructure: `PlayerModel` and the fifteen rule files
/// around it are new, and **not one of them is reachable from a screen yet**.
/// Podcasts, Audiobooks, Stations, PhoneMedia and MusicMore merge this branch
/// and build the surfaces that drive it; until they do there is no Player
/// screen to open, to audit, or to measure at an accessibility type size.
///
/// So this file is deliberately three cases, and
/// `design-notes/ios/overnight/player/MAP.md` §7.6 says why each one is the
/// only kind of answer a Simulator can add to the package tests:
///
/// 1. **The app still works with the player linked in.** A new file in
///    `DomovoiKit` that registers a `NotificationCenter` observer, configures
///    `AVAudioSession`, or publishes to `MPNowPlayingInfoCenter` at the wrong
///    moment breaks *video* — and no package test can see that, because
///    `swift test` builds for macOS, where the whole `#if os(iOS)` half of
///    this item does not exist. The video feed is the only thing in the app
///    today that drives `AVFoundation`, so it is the canary.
/// 2. **Nothing offers phone playback yet.** An assertion that the surface is
///    still absent, so the branch that adds it adds it on purpose.
/// 3. **The fixture really serves audio.** `fixtures/player.py` generates a
///    WAV and answers `Range:`. If it did not, every case the five merging
///    branches write against it would fail for a reason none of them owns.
///
/// What is **not** here: an accessibility audit and a Dynamic Type growth
/// measurement. Both need a screen. Writing them against Home or Videos would
/// re-measure what ``StubHouseUITests`` already measures and attribute it to
/// the player, which is worse than not writing them.
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
    /// session is configured on the **first play and never at launch**, and
    /// nothing on this branch calls `play`. This case is what makes that a
    /// measured fact rather than a sentence in a comment.
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

    // MARK: - 2. and it must not have grown a surface by accident

    /// Nothing in the app offers to play audio on the phone yet.
    ///
    /// Two halves. The phone bar is still the four tabs it was — `home`,
    /// `music`, `videos`, `chat` — and no tab of its own has appeared for the
    /// player. And no control on Home says it will play on this phone.
    ///
    /// The wordings below are the ones `PlayerStrings` holds and the five
    /// merging branches will draw. They are spelt out here rather than
    /// imported, like every other string this file matches on: a query that
    /// silently followed a renamed constant would stop testing what it claims
    /// to. When one of them starts to exist this case fails, and whoever
    /// added it moves the assertion into their own file — which is the point.
    @MainActor
    func testPlayer_NothingInTheAppOffersPhonePlaybackYet() throws {
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

        // A control that would play audio on this phone, by the words it
        // would carry. `PlayerStrings` has no sentence of its own for the
        // offer yet; these are what the five branches' menus will say.
        for offer in ["play on this phone", "play here"] {
            let found = app.descendants(matching: .button)
                .matching(NSPredicate(format: "label CONTAINS[c] %@", offer))
                .firstMatch
            XCTAssertFalse(
                found.exists,
                "something offers \"\(offer)\" and no screen on this branch "
                    + "drives PlayerModel — a control that cannot do what it "
                    + "says is the one thing this project will not ship"
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
