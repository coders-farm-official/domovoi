import XCTest

/// Files of any size, in both directions, against the real stub house.
///
/// D43 and D47 removed the app's two 64 MiB ceilings: an upload streams off
/// disk (`/api/files/upload-stream`, whose body IS the file) and a save
/// streams onto it (a `URLSessionDownloadTask` writing into the spool). The
/// package tests measure the models; these cases are the only thing that
/// drives a real socket, a real file on the Simulator's disk and the share
/// sheet the save ends in.
///
/// ### What the stub does for these cases, and why
///
/// * A row **declared** bigger than the ceiling that was gets
///   `BIG_SAVE_BYTES` — six mebibytes, a hundred-odd chunks, an honest
///   `Content-Length` — instead of the one-line body every other row gets
///   (`stubhouse.py`'s `download`). Six and not four thousand: the point is
///   a body that does not fit in one read and a percentage that is a real
///   number, not the Mac's loopback throughput.
/// * `/api/_test/save-pace` slows those chunks down. A cancel cannot be
///   tested against a save that finishes in a second — the stop button would
///   be gone before a tap reached it.
/// * `/api/files/upload-stream` is `fixtures/transfers.py`, which records
///   what the **socket** carried rather than what it kept, so a case can say
///   a two-hundred-mebibyte file really sent two hundred mebibytes
///   (`stubhouse.py`'s `_read_body` keeps a megabyte and drains the rest —
///   holding a test's upload in the stub's memory would make the stub the
///   thing that fell over in a test about the phone not falling over).
///
/// ### The share sheet
///
/// `UIActivityViewController` is the system's, presented inside this app's
/// process on a Simulator, and it has no label of the app's own to match. It
/// is found by its own view (`ActivityListView`) with a fallback to "any
/// sheet", and when neither is there the **tree** is printed rather than a
/// sentence about an element that is not — the standing rule on this track.
extension StubHouseUITests {

    // MARK: - the controls

    /// Forget what the streamed door has taken, and put the save back to full
    /// speed.
    ///
    /// `reset-fixtures` reaches `fixtures/transfers.py`, as it reaches every
    /// fixture; the pace is `stubhouse.py`'s own and is **not** a fixture, so
    /// it is cleared separately. Exactly `phoneReset`'s shape, and for the
    /// same reason: one case leaving the house slow would make every later
    /// case in the run time out somewhere else.
    @MainActor
    func transfersReset(port: Int = 6601) {
        control("reset-fixtures", "{}", port: port, what: "the transfers seed")
        control("save-pace", "{\"pause_ms\":0}", port: port, what: "the save pace, full speed")
    }

    /// Milliseconds between the chunks of a big save.
    @MainActor
    func savePace(_ ms: Int, port: Int = 6601) {
        control("save-pace", "{\"pause_ms\":\(ms)}", port: port, what: "the save pace")
    }

    /// Every streamed upload the house has taken, newest last.
    ///
    /// A GET, so it cannot go through ``control(_:_:port:what:)``, which
    /// POSTs. Decoded loosely on purpose: a case asks about one or two fields
    /// of one row and a typed model here would be a third place the fixture's
    /// shape is written down.
    @MainActor
    func transfersTaken(port: Int = 6601) -> [[String: Any]] {
        var request = URLRequest(
            url: URL(string: "http://127.0.0.1:\(port)/api/_test/transfers")!
        )
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        let answered = expectation(description: "the house said what it took")
        var rows: [[String: Any]] = []
        URLSession.shared.dataTask(with: request) { data, _, _ in
            if let data,
               let said = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let taken = said["taken"] as? [[String: Any]] {
                rows = taken
            }
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 20)
        return rows
    }

    /// Whether the system share sheet came up. See the note on this file.
    @MainActor
    func shareSheetAppeared(_ app: XCUIApplication, _ what: String) -> Bool {
        if app.otherElements["ActivityListView"].waitForExistence(timeout: 30) { return true }
        if app.sheets.firstMatch.exists { return true }
        // "No share sheet" is a claim about something that is not there, and
        // the only useful answer is what *is*.
        print("=== NO SHARE SHEET after \(what) — tree follows ===")
        print(app.debugDescription)
        return false
    }

    /// Get rid of it again, whichever way this iOS offers.
    @MainActor
    func dismissShareSheet(_ app: XCUIApplication) {
        for label in ["Close", "Cancel", "close"] {
            let button = app.buttons[label].firstMatch
            if button.exists {
                button.tap()
                return
            }
        }
        // No button: the sheet is a card, and a swipe down is what a finger
        // would do.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.2))
            .press(
                forDuration: 0.1,
                thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.95))
            )
    }

    // MARK: - saving

    /// **The case D47 is about.** A four-gigabyte row is saved, with a
    /// percentage, and the file is handed to the share sheet.
    ///
    /// This replaces `testAFileTooBigToSaveSaysSoRatherThanOfferingOne`,
    /// which asserted the opposite against the same row: a line saying how
    /// big it was, and no control. The row is `film.mkv` in Documents, four
    /// gigabytes by its listing, and the one thing that has not changed is
    /// which row it is — so a reader comparing the two cases is comparing
    /// behaviour and not fixtures.
    ///
    /// The percentage is asserted **through its own words** rather than by
    /// reading a number out of a label: `TransferStrings.savingPercent` is
    /// `saving "film.mkv"… 42%`, so a `BEGINSWITH` on the sentence and a
    /// `CONTAINS "%"` together say a real fraction reached the control, which
    /// is what a chunked body with a declared length exists here to prove.
    @MainActor
    func testTransfers_AFilmFarOverTheOldCapIsSavedAndHandedToTheShareSheet() throws {
        transfersReset()
        // Paced, so the saving state is on screen long enough to be seen at
        // all: at full speed six mebibytes over loopback can be done before
        // the first query lands, and the case would then be asserting the
        // share sheet only.
        savePace(25)
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")
        XCTAssertTrue(pickLibrary(app, "Documents"), "the library picker never opened")

        let film = app.buttons["film.mkv"]
        XCTAssertTrue(film.waitForExistence(timeout: 20), "Documents never listed")
        XCTAssertFalse(
            app.staticTexts["too big to save from a phone (4.00 GB) — on the dashboard"].exists,
            "the four-gigabyte row still carries the refusal the cap used to draw"
        )
        film.tap()

        let saving = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label BEGINSWITH %@", "saving \"film.mkv\""))
            .firstMatch
        let started = saving.waitForExistence(timeout: 20)
        evidence(app, "56-files-saving-a-film")
        XCTAssertTrue(started, "a four-gigabyte row was tapped and nothing said it was saving")
        XCTAssertTrue(
            app.buttons["stop saving"].exists,
            "a multi-gigabyte save with no way out is a phone stuck on a mistake"
        )

        // The percentage, at whatever point it has reached: a determinate bar
        // is the feature, and a stub with no `Content-Length` would have left
        // it untested.
        let percent = app.descendants(matching: .staticText)
            .matching(NSPredicate(
                format: "label BEGINSWITH %@ AND label CONTAINS %@", "saving \"film.mkv\"", "%"
            ))
            .firstMatch
        let counted = percent.waitForExistence(timeout: 30)
        evidence(app, "56-files-saving-percent")
        XCTAssertTrue(counted, "the save never reported a percentage — the bar is indeterminate")

        XCTAssertTrue(shareSheetAppeared(app, "a four-gigabyte row's save"),
                      "the file was fetched and never offered")
        evidence(app, "56-files-saved-share-sheet")
        dismissShareSheet(app)
    }

    /// A save can be **stopped**, and stopping it says nothing in red.
    ///
    /// The person asked for it; a failure sentence about a thing somebody
    /// chose is noise, so the control goes back to offering the save. The
    /// share sheet must not arrive afterwards either — a transfer that
    /// finished in the gap between the tap and the transport noticing used to
    /// hand the file over anyway, which is the defect
    /// `SaveToDeviceModelTests` pinned and this case watches from outside.
    @MainActor
    func testTransfers_ASaveCanBeStopped() throws {
        transfersReset()
        // Slow enough that four seconds of save are left after the tap.
        savePace(60)
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")
        XCTAssertTrue(pickLibrary(app, "Documents"), "the library picker never opened")
        let film = app.buttons["film.mkv"]
        XCTAssertTrue(film.waitForExistence(timeout: 20), "Documents never listed")
        film.tap()

        let stop = app.buttons["stop saving"]
        XCTAssertTrue(stop.waitForExistence(timeout: 20), "the save offered no way out")
        evidence(app, "57-files-save-stoppable")
        stop.tap()

        let saving = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label BEGINSWITH %@", "saving \""))
            .firstMatch
        // Gone, and gone **quietly**: no "couldn't save: cancelled", which is
        // the app blaming somebody for a button they pressed.
        XCTAssertTrue(
            waitForAbsence(saving, timeout: 20),
            "the stopped save is still saying it is saving"
        )
        evidence(app, "57-files-save-stopped")
        XCTAssertFalse(
            app.descendants(matching: .staticText)
                .matching(NSPredicate(format: "label BEGINSWITH %@", "couldn't save"))
                .firstMatch.exists,
            "a save the person stopped was reported to them as a failure"
        )
        XCTAssertFalse(
            app.otherElements["ActivityListView"].exists,
            "a cancelled save still opened the share sheet"
        )
    }

    /// The Videos feed's own save, which is Android's `save to device` on the
    /// options sheet (`VideoFeed.kt:459`, handled at `VideosScreen.kt:216`).
    ///
    /// Android hands the URL to `DownloadManager` and the system does the
    /// rest; iOS has no public Downloads folder and no system downloader an
    /// app may hand a credentialled request to, so the app carries the file
    /// and the share sheet is the completion. The video is fetched from the
    /// same route the player uses with `download=1` on it, which is the
    /// server's own attachment form and what Android asks for too.
    @MainActor
    func testTransfers_AVideoIsSavedFromItsOptionsSheet() throws {
        transfersReset()
        savePace(20)
        let (app, landed) = openVideos()
        XCTAssertTrue(landed, "the videos list never answered")
        app.buttons["a long one.mp4"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["1 / 50"].waitForExistence(timeout: 20), "the feed did not open")

        // A long press on the **page**, by coordinate: the chrome is drawn
        // over the pager, so a press on a label never reaches the view
        // carrying the gesture. `testTheDetailsSheetSaysWhatThePlayerMeasured`
        // learnt this the hard way and the reasoning is written down there.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4))
            .press(forDuration: 1.0)
        let save = app.buttons["save to device"]
        let offered = save.waitForExistence(timeout: 10)
        evidence(app, "48-feed-options-with-save")
        XCTAssertTrue(offered, "the feed's options sheet does not offer a save to device")
        save.tap()

        // **The sheet stays open and becomes the progress.** Android closes
        // its sheet and lets the system notification carry the download; iOS
        // has no such notification, so a sheet that closed on the tap would
        // leave a film coming down with nothing on screen and no way to stop
        // it. The options row is `SaveToDeviceButton`, so the same stop that
        // Files draws is here.
        let saving = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label BEGINSWITH %@", "saving \""))
            .firstMatch
        let drawn = saving.waitForExistence(timeout: 20)
        evidence(app, "48-feed-saving")
        XCTAssertTrue(drawn, "the feed's save says nothing while it works")
        XCTAssertTrue(
            app.buttons["stop saving"].exists,
            "a film coming down from the feed has no way out"
        )

        XCTAssertTrue(shareSheetAppeared(app, "a video's save"),
                      "the video was fetched and never offered")
        evidence(app, "48-feed-video-saved")
        dismissShareSheet(app)

        // **And the options sheet does not come back.** The sheet was left
        // open under the share sheet on purpose (it is where the percentage
        // and the stop are drawn), so dismissing the share sheet used to
        // re-present it over the player: the binding took the file and
        // returned, the feed's own `sheet` was still `.options`, and the
        // getter ran again (REVIEW-1 finding 5). Android closes its sheet on
        // the tap (`VideoFeed.kt:459`).
        let options = app.buttons["save to device"]
        XCTAssertTrue(
            waitForAbsence(options, timeout: 15),
            "the options sheet re-presented itself as soon as the share sheet was dismissed"
        )
        evidence(app, "48-feed-after-sharing")
    }

    /// **The save from the Files viewer**, which is the third of the four
    /// call sites and had no case of its own.
    ///
    /// `logo.svg` is an image the phone cannot decode, so the viewer draws
    /// ``FileStrings/notAPicture`` and offers the one way out that does work
    /// (`FileViewers.swift`'s `notAPicture`). That branch is wired to the
    /// same ``SaveToDeviceModel`` the row tap uses, and nothing drove it
    /// until now: `testAPictureIsShownAndAnSvgSaysItCannotBe` asserts the
    /// control **exists**, and this one taps it.
    @MainActor
    func testTransfers_APictureThisPhoneCannotShowIsSavedFromTheViewer() throws {
        transfersReset()
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")
        XCTAssertTrue(pickLibrary(app, "Documents"), "the library picker never opened")

        let svg = app.buttons["logo.svg"]
        XCTAssertTrue(svg.waitForExistence(timeout: 20), "Documents never listed")
        svg.tap()
        XCTAssertTrue(
            app.staticTexts["not a picture this phone can show"].waitForExistence(timeout: 20),
            "the viewer never said it could not draw this one"
        )
        let save = app.buttons["save to device"]
        XCTAssertTrue(save.waitForExistence(timeout: 10), "the viewer offers no way out")
        save.tap()

        // The viewer closes and the share sheet is the completion — that
        // order matters, because two sheets cannot both be up and a save
        // stranded behind the viewer would be a file left in the container.
        XCTAssertTrue(shareSheetAppeared(app, "a save from the picture viewer"),
                      "the viewer's save fetched the file and never offered it")
        evidence(app, "56-files-viewer-saved")
        dismissShareSheet(app)
    }

    /// **The save from the document reader**, the fourth call site.
    ///
    /// `weird.txt` is a document the house refuses to decode — a 415 with
    /// `binary` as its reason, which is the real route's own answer
    /// (`documents.py:871`) — so the reader draws the refusal and the save
    /// beneath it (`FileViewers.swift`'s `refusal`). The same control, the
    /// same model, a different screen.
    @MainActor
    func testTransfers_ADocumentThisPhoneCannotShowIsSavedFromTheReader() throws {
        transfersReset()
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")
        XCTAssertTrue(pickLibrary(app, "Documents"), "the library picker never opened")

        let document = app.buttons["weird.txt"]
        XCTAssertTrue(document.waitForExistence(timeout: 20), "Documents never listed")
        document.tap()
        // The server's reason in the app's words
        // (``FileStrings/notShowable(_:)`` for `binary`).
        let refused = app.staticTexts["not a text file"]
        XCTAssertTrue(
            refused.waitForExistence(timeout: 20),
            "the reader never said why it could not show this one"
        )
        let save = app.buttons["save to device"]
        XCTAssertTrue(save.waitForExistence(timeout: 10), "the reader offers no way out")
        save.tap()

        XCTAssertTrue(shareSheetAppeared(app, "a save from the document reader"),
                      "the reader's save fetched the file and never offered it")
        evidence(app, "56-files-reader-saved")
        dismissShareSheet(app)
    }

    // MARK: - D66, measured

    /// **A long tile name really wraps at an accessibility size.**
    ///
    /// This is the measurement REVIEW-1 finding 7 asked for. D66 made the
    /// grid's caption `.lineLimit(dynamicTypeSize.isAccessibilitySize ? nil
    /// : 2)` (`VideosView.swift:277`), and the only thing that had been
    /// measured about it was that the caption grows with the type size —
    /// which a single line does too. So nothing said whether the wrap
    /// happened, and `testTheVideosGridAudits` was left to find out by
    /// failing.
    ///
    /// The claim that discriminates: **at an accessibility size the longer
    /// title is taller than the shorter one.** With a two-line ceiling both
    /// are capped at two lines and the heights are equal; with no ceiling the
    /// twenty-character name takes more lines than the fourteen-character one
    /// and is strictly taller. Put the `lineLimit(2)` back and this case
    /// fails.
    ///
    /// `AccessibilityM` rather than AX5: it is the **smallest** size at which
    /// `isAccessibilitySize` is true, so the rule under test is on while the
    /// grid still fits enough on a screen for both tiles to be reached. At
    /// AX5 the count line alone is 105 points tall and finding a particular
    /// second tile is forty flicks of luck
    /// (``testTheVideosGridTextGrowsAtAX5`` says so at length).
    @MainActor
    func testTransfers_ALongVideoNameWrapsRatherThanClippingAtAnAccessibilitySize() throws {
        let short = "a long one.mp4"
        let long = "01 she said \"no\".mp4"
        let (app, _) = openVideos("UICTContentSizeCategoryAccessibilityM")

        // Both captions, taken as they appear while the page is flicked: a
        // lazy grid's foot is unbuilt rather than off screen, and at an
        // accessibility size the second tile may be the far side of a tall
        // header.
        var seen: [String: CGFloat] = [:]
        for flick in 0...20 {
            for label in [short, long] where seen[label] == nil {
                let text = app.staticTexts[label].firstMatch
                if text.exists { seen[label] = text.frame.height }
            }
            if flick == 0 { evidence(app, "66-videos-grid-accessibility-size") }
            if seen.count == 2 { break }
            phoneSourceFlick(app)
        }
        for (label, height) in seen.sorted(by: { $0.key < $1.key }) {
            print("WRAP [AccessibilityM] \(label) height=\(height)")
        }
        guard let shortHeight = seen[short], let longHeight = seen[long] else {
            print("=== BOTH CAPTIONS NOT FOUND (\(seen)) — tree follows ===")
            print(app.debugDescription)
            return XCTFail("the two captions could not be measured, so this proved nothing")
        }
        XCTAssertGreaterThan(
            longHeight, shortHeight,
            "the two captions are the same height at an accessibility size, which is what a "
                + "two-line ceiling does: the longer name is being clipped, not wrapped"
        )
    }

    // MARK: - uploading

    /// **The case D43 is about.** Two hundred mebibytes leave this phone
    /// through the streamed door, and the house says it received them.
    ///
    /// `grendalf huge raw.wav` is 200 MiB in the seeded phone library, over
    /// the ceiling that was by a factor of three, and its file is made by
    /// setting a length rather than by writing anything
    /// (`PhoneMemorySource.sized`) — so this case sends a real two hundred
    /// mebibytes without a Simulator holding them or a disk storing them.
    ///
    /// Three claims, and the third is the one that cannot be made anywhere
    /// else:
    ///
    /// 1. The row **has** an upload control. It used to have none and a line
    ///    saying how big it was (`testPhoneMedia_AFileOverTheCapHasNoUploadControl`,
    ///    which this branch cannot change — see the report's "Needs a shared
    ///    change").
    /// 2. The control reports a **percentage** while it works, which is what
    ///    `PhoneUploadButtonState.busy(percent:)` carries now.
    /// 3. The house received the whole file, through
    ///    `/api/files/upload-stream`, with the library and the name in the
    ///    query rather than in a multipart envelope. The byte count is the
    ///    socket's, which is the only number in this project that can tell a
    ///    streamed upload from a truncated one.
    @MainActor
    func testTransfers_ABigPhoneFileGoesThroughTheStreamedDoor() throws {
        phoneReset()
        transfersReset()
        let app = phoneLaunch(push: "Files")
        XCTAssertTrue(
            phoneSwitchToDevice(app, waitingFor: StubPhoneMedia.phoneBigFileWord),
            "the device side of Files never listed the phone's own folder"
        )

        let row = containing(app, StubPhoneMedia.phoneBigFileWord)
        XCTAssertTrue(row.waitForExistence(timeout: 30), "the 200 MiB row is not listed")
        // Four, not three. The seeded folder's root holds one folder
        // (`Trips`) and four files, and every file offers an upload now
        // whatever its size; the 200 MiB one used to be the exception, which
        // is what made the old count three.
        XCTAssertEqual(
            phoneUploadControls(app).count, 4,
            "every file in the folder offers an upload now, the 200 MiB one included"
        )
        evidence(app, "phonemedia-big-upload-offered")

        // The big row's own control, found by **which row it sits on** and
        // not by its index in the query. An index would be arithmetic over
        // the folder's sort order — the kind of assertion that keeps passing
        // after somebody adds a row and then tests the wrong one. A row is
        // 48 points, so a control whose centre is within half of that of the
        // label's is on the same row and nothing else is.
        let controls = phoneUploadControls(app)
        let wanted = controls.allElementsBoundByIndex.first { control in
            abs(control.frame.midY - row.frame.midY) < 24
        }
        guard let wanted else {
            print("=== NO UPLOAD CONTROL ON THE BIG ROW — tree follows ===")
            print(app.debugDescription)
            return XCTFail("the 200 MiB row has no upload control beside it")
        }
        wanted.tap()

        // The ring says a number. `uploadingLabel(_:)` is `uploading 42%`
        // once a percentage has arrived and plain `uploading` until then, so
        // this is the assertion that the progress callback reaches the row.
        let counting = app.buttons
            .matching(NSPredicate(
                format: "label BEGINSWITH %@ AND label CONTAINS %@", "uploading", "%"
            ))
            .firstMatch
        let reported = counting.waitForExistence(timeout: 60)
        evidence(app, "phonemedia-big-upload-percent")
        XCTAssertTrue(reported, "a 200 MiB upload never reported a percentage")

        // **And a sighted person can see it.** The assertion above is the
        // button's accessibility label, and for a while that was the only
        // place the number existed — the row drew a spinner and no digits
        // anywhere (REVIEW-1 finding 3). The drawn number is its own element,
        // found by identifier so that this cannot match the label it is
        // beside.
        let drawn = app.staticTexts
            .matching(identifier: "transfers-upload-percent")
            .firstMatch
        XCTAssertTrue(
            drawn.waitForExistence(timeout: 60),
            "the percentage is in the accessibility label and nowhere on the screen"
        )
        XCTAssertTrue(
            drawn.label.hasSuffix("%"),
            "the drawn number is not a percentage: \(drawn.label)"
        )

        let done = startingWith(app, "uploaded \"grendalf huge raw.wav\" to Documents")
        let finished = done.waitForExistence(timeout: 180)
        evidence(app, "phonemedia-big-upload-done")
        XCTAssertTrue(finished, "the 200 MiB upload never finished")

        let taken = transfersTaken()
        XCTAssertEqual(1, taken.count, "the house took something other than one file")
        let first = taken.first ?? [:]
        XCTAssertEqual("grendalf huge raw.wav", first["rel_path"] as? String)
        XCTAssertEqual("core:documents", first["library_id"] as? String)
        XCTAssertEqual("", first["path"] as? String, "the library's root is an empty path, still sent")
        XCTAssertEqual(
            200 * 1024 * 1024, first["bytes"] as? Int,
            "the socket did not carry the whole file — the body was built, not streamed"
        )
    }

    // MARK: - the helper this file needs and the others did not

    /// Wait for an element to stop existing.
    ///
    /// `waitForExistence` has no opposite, and `!exists` read once is a race
    /// against a view that is on its way out. Polled rather than slept: a
    /// fixed sleep is either too short on a loaded Mac or wasted on an idle
    /// one.
    @MainActor
    func waitForAbsence(_ element: XCUIElement, timeout: TimeInterval) -> Bool {
        let until = Date().addingTimeInterval(timeout)
        while Date() < until {
            if !element.exists { return true }
            // A plain sleep: `XCTWaiter` on an expectation nothing fulfils
            // reports a timeout, and a quarter of a second of nothing is not
            // a thing a test should have to explain in its log.
            Thread.sleep(forTimeInterval: 0.25)
        }
        return !element.exists
    }
}
