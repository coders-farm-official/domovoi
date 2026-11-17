import XCTest

/// "What does it say?" (the scan reader, 1.4.0), driven against the stub
/// house: on the Scan sheet after Done, and on a scan row under a printer's
/// recent jobs — the two places Android offers it (54d46869), and not on the
/// Documents list.
///
/// The fixture is `printing.py` with `batch-2026-11-17/scan-read/
/// stubhouse.patch` applied. `POST /api/_test/printing {"read": …}` decides
/// the next saved scan's reading (`none` by default: a server that can't read
/// scans), `{"read_advance": true}` finishes a held reading, and
/// `{"scan_log": "seeded"}` puts one read scan and one failed one in printer
/// 1's job log. The summary carries `quillfeather` and the full text
/// `marrowgate`, so each assertion has a word only that answer can produce.
///
/// Every case resets the fixtures first (inside ``printingOpenDocuments(_:blocked:)``
/// and ``printingOpenPanel(_:)``) and sets its read mode after: a reset puts
/// the mode back to `none`.
extension StubHouseUITests {

    // MARK: - getting there

    /// Set the printing fixture's scan-reader switch, and **check the answer
    /// came from the scan-reader fixture**.
    ///
    /// Train I064 failed three of these cases with the app right and the
    /// patched fixture right: a backup of the old module,
    /// `fixtures/printing.pre-scanread.py`, sat beside it, the house loads
    /// every `fixtures/*.py` not starting with `_` in name order, and the
    /// backup sorts first — so it claimed `/api/scan/` and
    /// `/api/_test/printing`, answered 200 to every switch it did not know,
    /// and served `read: null` and `scans: []`. ``control(_:_:port:what:)``
    /// only checks the 200, so the cases waited 30 s for a button no server
    /// would draw. The patched control echoes `read` and `scan_log`; an
    /// answer without them is the old module, and the case says so at once.
    @MainActor
    func scanReadArm(_ key: String, _ value: String, what: String) {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:6601/api/_test/printing")!)
        request.httpMethod = "POST"
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data("{\"\(key)\": \"\(value)\"}".utf8)
        let answered = expectation(description: "the stub set \(what)")
        let said = ScanReadStubAnswer()
        URLSession.shared.dataTask(with: request) { data, response, _ in
            said.code = (response as? HTTPURLResponse)?.statusCode ?? -1
            said.body = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 15)
        XCTAssertEqual(200, said.code, "the stub did not set \(what)")
        XCTAssertEqual(
            said.body?[key] as? String, value,
            "the printing control that answered is not the scan-reader fixture: a copy of the old "
                + "printing.py is loaded beside it (fixtures/*.py, name order — move backups out of "
                + "fixtures/ or name them _something.py), so \(what) was never set"
        )
    }

    /// Scan one page on the glass and press Done; true once the sheet says
    /// where the scan went.
    @MainActor
    func scanReadScanToDone(_ app: XCUIApplication) -> Bool {
        let open = app.buttons["scan"].firstMatch
        guard open.waitForExistence(timeout: 25) else {
            XCTFail("Documents offers no scan")
            return false
        }
        open.tap()
        let start = app.buttons["Scan"].firstMatch
        guard start.waitForExistence(timeout: 20) else {
            XCTFail("the scan sheet has no Scan button")
            return false
        }
        start.tap()
        let done = app.buttons["Done"].firstMatch
        guard done.waitForExistence(timeout: 30) else {
            print("=== NO DONE — tree follows ===")
            print(app.debugDescription)
            XCTFail("no Done on a scan with a page in hand")
            return false
        }
        done.tap()
        guard app.staticTexts["Saved to Documents as scan 2026-11-02.pdf"].waitForExistence(timeout: 30) else {
            print("=== THE SCAN WAS NOT SAVED — tree follows ===")
            print(app.debugDescription)
            XCTFail("the scan was not saved")
            return false
        }
        return true
    }

    /// Any control whose label starts "What does it say?".
    @MainActor
    func scanReadAnyButton(_ app: XCUIApplication) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "What does it say?")).firstMatch
    }

    // MARK: - the Scan sheet

    /// A saved scan that has been read offers "What does it say?"; tapping it
    /// closes the Scan sheet and opens the reading — the summary in the
    /// server's words, its note, then the full text on request.
    @MainActor
    func testScanRead_SavedScanOffersWhatDoesItSayAndShowsTheSummaryThenTheText() throws {
        guard let app = printingOpenDocuments() else { return }
        scanReadArm("read", "done", what: "the next scan to be read")
        guard scanReadScanToDone(app) else { return }

        let button = app.buttons["What does it say?"].firstMatch
        XCTAssertTrue(button.waitForExistence(timeout: 30), "a read scan does not offer What does it say?")
        evidence(app, "scanread-01-button")
        button.tap()

        XCTAssertTrue(
            containing(app, "quillfeather").waitForExistence(timeout: 30),
            "the summary never landed"
        )
        XCTAssertTrue(
            waitUntilGone(app.staticTexts["Saved to Documents as scan 2026-11-02.pdf"]),
            "the Scan sheet stayed up under the reading"
        )
        XCTAssertTrue(
            startingWith(app, "Page 2 was hard to read").exists,
            "the server's note about a guessed page is not shown"
        )
        XCTAssertFalse(containing(app, "marrowgate").exists, "the full text was shown before it was asked for")
        evidence(app, "scanread-02-summary")

        let full = app.buttons["Show the full text"].firstMatch
        XCTAssertTrue(full.waitForExistence(timeout: 15), "no Show the full text on a read scan")
        full.tap()
        XCTAssertTrue(
            containing(app, "marrowgate").waitForExistence(timeout: 20),
            "the full text never landed"
        )
        XCTAssertTrue(waitUntilGone(full), "Show the full text is still offered with the text on screen")
        evidence(app, "scanread-03-full-text")
    }

    /// While the saved scan is being read the button carries the progress,
    /// and the sheet keeps asking about the scan: when the reading finishes
    /// the label turns plain without anyone touching the screen.
    @MainActor
    func testScanRead_TheButtonCarriesTheReadingsProgress() throws {
        guard let app = printingOpenDocuments() else { return }
        scanReadArm("read", "reading", what: "the next scan's reading to be held")
        guard scanReadScanToDone(app) else { return }

        XCTAssertTrue(
            app.buttons["What does it say? (reading page 1 of 2)"].firstMatch.waitForExistence(timeout: 30),
            "the button does not carry the reading's progress"
        )
        evidence(app, "scanread-04-reading")

        control("printing", "{\"read_advance\": true}", port: 6601, what: "the held reading to finish")
        XCTAssertTrue(
            app.buttons["What does it say?"].firstMatch.waitForExistence(timeout: 30),
            "the label never turned plain — the sheet stopped asking about the scan once it was saved"
        )
        evidence(app, "scanread-05-read")
    }

    /// No button for a scan this server can't read, nor for one whose reading
    /// failed: a control that opens on nothing is the one this app never
    /// draws.
    @MainActor
    func testScanRead_NoButtonWhenTheScanCantBeReadOrTheReadingFailed() throws {
        guard let app = printingOpenDocuments() else { return }
        // The seed: `read: null`. Armed explicitly, so the case also proves
        // the scan-reader fixture is the one answering.
        scanReadArm("read", "none", what: "the next scan to be unreadable")
        guard scanReadScanToDone(app) else { return }
        // Long enough for the sheet's poll to have landed had it wanted one.
        Thread.sleep(forTimeInterval: 3)
        evidence(app, "scanread-06-cant-read")
        XCTAssertFalse(scanReadAnyButton(app).exists, "a scan this server can't read offers What does it say?")
        app.terminate()

        guard let second = printingOpenDocuments() else { return }
        scanReadArm("read", "failed", what: "the next scan's reading to fail")
        guard scanReadScanToDone(second) else { return }
        Thread.sleep(forTimeInterval: 3)
        evidence(second, "scanread-07-failed")
        XCTAssertFalse(scanReadAnyButton(second).exists, "a scan whose reading failed offers What does it say?")
    }

    // MARK: - a printer's recent jobs

    /// The recent list shows the scans made on the printer; the one that was
    /// read offers "What does it say?" and the one whose reading failed does
    /// not. Tapping it opens the reading.
    @MainActor
    func testScanRead_RecentJobsOfferWhatDoesItSayOnAReadScan() throws {
        guard let app = printingOpenPanel() else { return }
        scanReadArm("scan_log", "seeded", what: "printer 1's scans in its job log")
        let open = app.buttons["recent jobs"].firstMatch
        XCTAssertTrue(printingScrollToHittable(app, open), "no recent-jobs link on a printer card")
        open.tap()

        let read = startingWith(app, "scan \u{00b7} done \u{00b7} 2 pages \u{00b7} Scan 2026-11-16 18.20.pdf")
        XCTAssertTrue(read.waitForExistence(timeout: 25), "the job log does not list the scans")
        XCTAssertTrue(
            startingWith(app, "scan \u{00b7} done \u{00b7} 2 pages \u{00b7} Scan 2026-11-15 08.05.pdf").exists,
            "the scan whose reading failed is not listed"
        )
        let button = app.buttons["What does it say?"].firstMatch
        XCTAssertTrue(button.waitForExistence(timeout: 15), "the read scan does not offer What does it say?")
        XCTAssertEqual(
            1,
            app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "What does it say?")).count,
            "the scan whose reading failed offers What does it say? too"
        )
        evidence(app, "scanread-08-recent-jobs")

        XCTAssertTrue(printingScrollToHittable(app, button), "What does it say? is unreachable")
        button.tap()
        XCTAssertTrue(
            containing(app, "quillfeather").waitForExistence(timeout: 30),
            "the reading opened from the job log never showed its summary"
        )
        evidence(app, "scanread-09-from-recent-jobs")
    }
}

/// The control's answer, filled on URLSession's queue and read after the
/// wait. `@unchecked Sendable` with the reason: written once before
/// `fulfill()` and read only after `wait(for:)` returns, which orders the two.
private final class ScanReadStubAnswer: @unchecked Sendable {
    var code = -1
    var body: [String: Any]?
}
