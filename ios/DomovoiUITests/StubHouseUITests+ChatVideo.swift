import XCTest

/// A video in a home-server chat (1.4.0): the paperclip's gate, the phone's
/// own length check, the upload and a refusal, the send, the job's chip as it
/// is watched, stop, and what was kept.
///
/// Every case is named `testChatVideo_…` and every piece of evidence
/// `chatvideo-…`. The house's side is `fixtures/chatvideo.py`
/// (`design-notes/ios/batch-2026-11-17/chat-video/stubhouse.patch`).
///
/// ### Why every case that attaches launches with `-domovoi-chat-video-pick`
///
/// PHPicker and the document picker are out-of-process UI that XCUITest
/// cannot hold steady (``PhoneMediaFake``'s reason, the same rule). So with
/// that argument both of the paperclip's doors hand over a small spooled file
/// of the given name, measured as the given length (`ChatVideoFake`, DEBUG
/// only); the real pickers are a hand pass on a phone.
///
/// Every name carries `quokka` or `wombat`, words the rest of the stub house
/// never uses, so a case cannot pass on a sentence another thread shares.
extension StubHouseUITests {

    // MARK: - getting there

    /// Reset the chat house and this fixture, set the offer, and land on
    /// Chat's list.
    @MainActor
    func openChatVideo(
        offer: String? = "on",
        pick: String? = nil,
        seed: Bool = false,
        upload: String? = nil,
        steps: [String]? = nil,
        port: Int = 6601
    ) -> (app: XCUIApplication, landed: Bool) {
        resetChatHouse(port: port)
        setChatAI(on: true, port: port)
        setChatFail(nil, port: port)
        control("reset-fixtures", "{}", port: port, what: "every fixture back to its seed")
        var body = ["\"offer\":\(offer.map { "\"\($0)\"" } ?? "null")", "\"seed\":\(seed)"]
        if let upload { body.append("\"upload\":\(upload)") }
        if let steps { body.append("\"steps\":[\(steps.map { "\"\($0)\"" }.joined(separator: ","))]") }
        control("chatvideo", "{\(body.joined(separator: ","))}", port: port, what: "the chat video house")
        let app = launch(
            push: "Chat",
            pushDelay: Self.pushDelay,
            args: pick.map { ["-domovoi-chat-video-pick", $0] } ?? []
        )
        let row = app.buttons[StubChat.fridge].firstMatch
        if row.waitForExistence(timeout: 40) { return (app, true) }
        print("=== CHAT NEVER LISTED ITS THREADS — tree follows ===")
        print(app.debugDescription)
        return (app, false)
    }

    /// Open thread 11 and pick a clip through the paperclip's Files door.
    @MainActor
    func chatVideoAttach(_ app: XCUIApplication) -> Bool {
        guard openThread(app, StubChat.fridge, waitingFor: "What can I cook") else { return false }
        let clip = app.buttons[ChatVideoUnderTest.attach].firstMatch
        guard clip.waitForExistence(timeout: 20) else {
            print("=== NO PAPERCLIP — tree follows ===")
            print(app.debugDescription)
            return false
        }
        clip.tap()
        let door = app.buttons[ChatVideoUnderTest.fromFiles].firstMatch
        guard door.waitForExistence(timeout: 10) else {
            print("=== THE PAPERCLIP OPENED NO MENU — tree follows ===")
            print(app.debugDescription)
            return false
        }
        door.tap()
        return true
    }

    // MARK: - the gate

    /// No paperclip on a server that cannot watch a video — an older one with
    /// no `video` key, and one with no ffmpeg — and one on a server that can.
    /// The composer itself is there in all three, so the absence is the
    /// paperclip's and not a lost composer.
    @MainActor
    func testChatVideo_ThePaperclipIsThereOnlyWhenTheHouseCanWatchOne() throws {
        for (offer, drawn) in [(nil, false), ("no-ffmpeg", false), ("on", true)] as [(String?, Bool)] {
            let (app, landed) = openChatVideo(offer: offer)
            XCTAssertTrue(landed, "the thread list never answered (\(offer ?? "no key"))")
            XCTAssertTrue(openThread(app, StubChat.fridge, waitingFor: "What can I cook"))
            XCTAssertTrue(composerField(app).waitForExistence(timeout: 20), "no composer at all")
            let clip = app.buttons[ChatVideoUnderTest.attach].firstMatch
            if drawn {
                XCTAssertTrue(clip.waitForExistence(timeout: 20), "a house that can watch one offers no paperclip")
                clip.tap()
                XCTAssertTrue(
                    app.staticTexts[ChatVideoUnderTest.menuItem].waitForExistence(timeout: 10)
                        || app.buttons[ChatVideoUnderTest.menuItem].exists,
                    "the menu does not say Android's words"
                )
                XCTAssertTrue(app.buttons[ChatVideoUnderTest.fromPhotos].exists)
                XCTAssertTrue(app.buttons[ChatVideoUnderTest.fromFiles].exists)
                evidence(app, "chatvideo-menu")
            } else {
                XCTAssertFalse(
                    clip.waitForExistence(timeout: 3),
                    "a paperclip on a house that said no (\(offer ?? "no key")): a control that cannot do what it says"
                )
            }
            app.terminate()
        }
    }

    /// AI features off: the whole composer goes, and the paperclip with it.
    @MainActor
    func testChatVideo_NoPaperclipWithAiOff() throws {
        let (app, landed) = openChatVideo(offer: "ai-off")
        XCTAssertTrue(landed)
        XCTAssertTrue(openThread(app, StubChat.fridge, waitingFor: "What can I cook"))
        XCTAssertFalse(app.buttons[ChatVideoUnderTest.attach].waitForExistence(timeout: 3))
        evidence(app, "chatvideo-ai-off")
    }

    // MARK: - checked on the phone

    /// Over three minutes: the server's sentence, at once, and nothing on the
    /// composer — the fixture would have taken any upload, so a chip here
    /// would mean the phone sent it.
    @MainActor
    func testChatVideo_ATooLongClipIsRefusedOnThePhoneWithTheServersSentence() throws {
        let (app, landed) = openChatVideo(pick: "quokka-long.mp4@222")
        XCTAssertTrue(landed)
        XCTAssertTrue(chatVideoAttach(app))
        XCTAssertTrue(
            containing(app, ChatVideoUnderTest.tooLong).waitForExistence(timeout: 15),
            "the refusal was not said in the server's words"
        )
        evidence(app, "chatvideo-too-long")
        XCTAssertFalse(containing(app, "quokka-long.mp4").exists, "a refused clip was left on the composer")
    }

    /// The house refused the upload: its own sentence.
    @MainActor
    func testChatVideo_ARefusedUploadSaysTheServersSentence() throws {
        let (app, landed) = openChatVideo(
            pick: "quokka-short.mp4@20",
            upload: #"{"status":507,"code":"video_no_room","detail":"The home server is short of disk space for that video. Free some space and try again."}"#
        )
        XCTAssertTrue(landed)
        XCTAssertTrue(chatVideoAttach(app))
        XCTAssertTrue(
            containing(app, "The home server is short of disk space").waitForExistence(timeout: 20),
            "the 507's detail was not shown"
        )
        evidence(app, "chatvideo-507")
    }

    // MARK: - attach, send, watch

    /// The whole of it: the clip goes up and says how long watching takes,
    /// a blank message sends it, the reply says so at once, the chip under
    /// the person's row follows the job to "Watched", the summary arrives as
    /// a reply, and "what was kept" opens what was said and what the
    /// pictures might show.
    @MainActor
    func testChatVideo_AttachSendAndWatch() throws {
        let (app, landed) = openChatVideo(pick: "quokka-garden.mp4@151.3")
        XCTAssertTrue(landed)
        XCTAssertTrue(chatVideoAttach(app))

        // 64 KiB measured as 2:31: "1 MB" is the dashboard's floor.
        XCTAssertTrue(
            containing(app, "2:31 · 1 MB · watching takes about 6 minutes").waitForExistence(timeout: 30),
            "the staged chip never said what it was"
        )
        XCTAssertTrue(app.buttons["remove quokka-garden.mp4"].exists, "no way to take it off")
        evidence(app, "chatvideo-staged")

        guard let send = composerSend(app) else { return XCTFail("a ready clip and a blank box offered no send") }
        XCTAssertTrue(send.isEnabled)
        send.tap()
        XCTAssertTrue(
            startingWith(app, "I'm watching quokka-garden.mp4 (2:31) now.").waitForExistence(timeout: 25),
            "the reply did not say the video is being watched"
        )
        XCTAssertFalse(app.buttons["remove quokka-garden.mp4"].exists, "the clip stayed on the composer")

        // The chip, polled every 3 s: four steps to "Watched".
        let watched = app.staticTexts["Watched"].firstMatch
        XCTAssertTrue(watched.waitForExistence(timeout: 45), "the chip never reached Watched")
        XCTAssertTrue(
            startingWith(app, "I've watched quokka-garden.mp4.").waitForExistence(timeout: 20),
            "the thread was not read again when the job ended"
        )
        evidence(app, "chatvideo-watched")

        let kept = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", ChatVideoUnderTest.whatWasKept))
            .firstMatch
        XCTAssertTrue(kept.waitForExistence(timeout: 10), "no way to see what was kept")
        kept.tap()
        XCTAssertTrue(containing(app, "happy birthday dear quokka").waitForExistence(timeout: 15))
        XCTAssertTrue(containing(app, "might show: a cake with candles beside a quokka").exists,
                      "a frame not worded as a guess")
        XCTAssertTrue(containing(app, ChatVideoUnderTest.guesses).exists)
        XCTAssertTrue(containing(app, ChatVideoUnderTest.deleted).exists)
        evidence(app, "chatvideo-kept")
    }

    // MARK: - seeded jobs

    @MainActor
    func testChatVideo_StopARunningVideo() throws {
        let (app, landed) = openChatVideo(seed: true)
        XCTAssertTrue(landed)
        XCTAssertTrue(openThread(app, ChatVideoUnderTest.runningTitle, waitingFor: "I'm watching wombat-garden.mov"))
        let stop = app.buttons[ChatVideoUnderTest.stop].firstMatch
        XCTAssertTrue(stop.waitForExistence(timeout: 20), "a running video offers no stop")
        XCTAssertTrue(containing(app, "Watching your video").exists)
        evidence(app, "chatvideo-running")
        stop.tap()
        XCTAssertTrue(app.staticTexts["Stopped"].firstMatch.waitForExistence(timeout: 20), "it never stopped")
        XCTAssertFalse(app.buttons[ChatVideoUnderTest.stop].exists, "stop is still offered on a stopped video")
        evidence(app, "chatvideo-stopped")
    }

    @MainActor
    func testChatVideo_WhatWasKeptOnAWatchedVideo() throws {
        let (app, landed) = openChatVideo(seed: true)
        XCTAssertTrue(landed)
        XCTAssertTrue(openThread(app, ChatVideoUnderTest.watchedTitle, waitingFor: "I've watched quokka-party.mp4"))
        XCTAssertTrue(app.staticTexts["Watched"].firstMatch.waitForExistence(timeout: 20))
        XCTAssertFalse(app.buttons[ChatVideoUnderTest.stop].exists, "stop on a watched video")
        let kept = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", ChatVideoUnderTest.whatWasKept))
            .firstMatch
        XCTAssertTrue(kept.waitForExistence(timeout: 10))
        kept.tap()
        XCTAssertTrue(containing(app, ChatVideoUnderTest.whatWasSaid).waitForExistence(timeout: 15))
        XCTAssertTrue(containing(app, ChatVideoUnderTest.picturesMightShow).exists)
        XCTAssertTrue(containing(app, "might show: a cake with candles beside a quokka").exists)
        evidence(app, "chatvideo-kept-seeded")
    }
}

/// The words the cases match, spelt out (``ChatStringsUnderTest``'s rule):
/// `ChatVideoStrings` in DomovoiKit, which match Android's `ChatVideo.kt`.
enum ChatVideoUnderTest {
    static let attach = "attach"
    static let menuItem = "a video (up to 3 minutes)"
    static let fromPhotos = "from Photos"
    static let fromFiles = "from Files"
    static let tooLong = "Videos up to 3 minutes can be attached. This one is 3:42 long."
    static let stop = "stop"
    static let whatWasKept = "what was kept"
    static let whatWasSaid = "what was said"
    static let picturesMightShow = "what the pictures might show"
    static let guesses = "these are a small model's guesses, and can be wrong"
    static let deleted = "the video itself was deleted after it was watched"
    /// `fixtures/chatvideo.py`'s seeded threads.
    static let watchedTitle = "Quokka birthday clip"
    static let runningTitle = "Wombat garden clip"
}
