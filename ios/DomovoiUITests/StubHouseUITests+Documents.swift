import XCTest

/// The document editors and `+ New`, driven against the stub house.
///
/// The fixture is `design-notes/ios/stubhouse/fixtures/documents.py`, whose
/// seed is written so that every assertion below has a word only one thing
/// can produce:
///
/// * `shopping list.txt` says **anchovies**, which no other file in the house
///   says;
/// * `notes.md` is seeded **twice** — the house's `/api/documents/text` copy
///   says `oat milk`, this item's `/api/documents/doc` copy says
///   `via-the-doc-route`. A markdown editor on the wrong route shows the
///   first and not the second;
/// * the sheet's total cell carries both `{"v": "9191"}` and
///   `{"f": "=SUM(B2:B3)"}`, so "the formula, not its cached value" is a
///   question with two different answers on screen.
///
/// Every case resets the fixtures first: the cases share one house for the
/// whole run, and three of them write to it.
extension StubHouseUITests {

    // MARK: - getting there

    /// Reset the house, open Files and pick Documents.
    ///
    /// The reset is **before** the launch, not after it: the folder is read
    /// once when the screen appears, and a reset that landed afterwards would
    /// leave the app showing a listing the fixture had already replaced.
    @MainActor
    func documentsOpen(
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        blocked: Bool = false
    ) -> XCUIApplication? {
        control("reset-fixtures", "{}", port: 6601, what: "the documents fixture's seed")
        let (app, landed) = openFiles(mode, blocked: blocked)
        guard landed else {
            XCTFail("the folder never answered")
            return nil
        }
        guard pickLibrary(app, "Documents") else {
            XCTFail("the library picker never opened")
            return nil
        }
        // Waited for AND scrolled to: at an accessibility type size the
        // header, the picker, the crumbs and the filter fill the window, so
        // the first row is **unbuilt** rather than merely off screen — the
        // same thing ``openFiles(_:blocked:delay:)`` waits for a different
        // marker about.
        let marker = app.buttons["notes.md"]
        _ = marker.waitForExistence(timeout: 20)
        guard documentsScrollTo(app, marker) else {
            XCTFail("Documents never listed")
            return nil
        }
        return app
    }

    /// Scroll the folder until `element` is **built**.
    ///
    /// Files draws its rows in a `LazyVStack` inside a `ScrollView`, so a row
    /// below the fold does not exist rather than merely sitting off screen —
    /// and `shopping list.txt`, the row this item added, is the eleventh.
    /// Not ``scrollUntilBuilt(_:_:flicks:)``: that one scrolls
    /// `app.collectionViews.firstMatch`, which this screen has none of.
    @MainActor
    func documentsScrollTo(_ app: XCUIApplication, _ element: XCUIElement, flicks: Int = 12) -> Bool {
        for _ in 0..<flicks where !element.exists {
            app.swipeUp()
        }
        if !element.exists {
            print("=== \(flicks) FLICKS AND STILL UNBUILT — tree follows ===")
            print(app.debugDescription)
        }
        return element.exists
    }

    /// Open one Documents row, scrolling to it first.
    @MainActor
    func documentsTap(_ app: XCUIApplication, _ row: String) -> Bool {
        let button = app.buttons[row].firstMatch
        guard documentsScrollTo(app, button) else { return false }
        button.tap()
        return true
    }

    /// The editor's typing surface. A `UITextView`
    /// (``DocumentsTextBody``), so its **value** is the document's text —
    /// which is what makes "the right file's words are on screen" a question
    /// with one answer rather than a search of the whole tree.
    @MainActor
    func documentsBody(_ app: XCUIApplication) -> XCUIElement {
        app.textViews.firstMatch
    }

    @MainActor
    func documentsText(_ app: XCUIApplication) -> String {
        (documentsBody(app).value as? String) ?? ""
    }

    /// Put the caret at the **end** of the document.
    ///
    /// A tap below and to the right of every line: `UITextView` puts the
    /// caret at the closest position to the touch, and past the last line
    /// that is the end. Tapping the middle of the view would land the caret
    /// inside a word, which is fine for appending a marker word and wrong for
    /// the two cases that delete backwards or mark up at the caret.
    @MainActor
    func documentsCaretToEnd(_ app: XCUIApplication) {
        documentsBody(app).coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.9)).tap()
    }

    // MARK: - the text editor

    /// A text file opens in the editor, takes a word, saves it, and still has
    /// it when it is opened again.
    ///
    /// It would fail if: the editor read the wrong route or the wrong file
    /// (`anchovies` is `shopping list.txt`'s alone); the save was never sent;
    /// the save went out with the wrong body key (the fixture answers 422 to
    /// a `PUT /text` with no `text`, and the toast would say so); or the
    /// reopen showed a cached copy rather than what the house now holds.
    @MainActor
    func testDocuments_TextFileOpensAndSaves() throws {
        guard let app = documentsOpen() else { return }
        XCTAssertTrue(documentsTap(app, "shopping list.txt"), "the text file's row never built")

        let body = documentsBody(app)
        XCTAssertTrue(body.waitForExistence(timeout: 20), "the text editor never opened")
        evidence(app, "documents-01-text-editor")
        XCTAssertTrue(
            documentsText(app).contains("anchovies"),
            "the editor is not showing shopping list.txt — it says: \(documentsText(app))"
        )
        XCTAssertTrue(app.staticTexts["text"].exists, "no pill saying which editor this is")

        documentsCaretToEnd(app)
        app.typeText("SARDINES")
        XCTAssertTrue(app.staticTexts["unsaved"].waitForExistence(timeout: 5),
                      "typing did not make the editor dirty")

        app.buttons["save"].firstMatch.tap()
        XCTAssertTrue(
            app.staticTexts["unsaved"].waitForNonExistence(timeout: 20),
            "the editor still says unsaved after a save that should have landed"
        )
        app.buttons["close"].firstMatch.tap()

        XCTAssertTrue(documentsTap(app, "shopping list.txt"), "back to the list")
        XCTAssertTrue(body.waitForExistence(timeout: 20), "the file did not open a second time")
        evidence(app, "documents-02-text-reopened")
        XCTAssertTrue(
            documentsText(app).contains("SARDINES"),
            "the saved word is not in the file the house hands back: \(documentsText(app))"
        )
    }

    /// Save is there but does nothing until there is something to save, and
    /// then it does. A Save that was always enabled, or never, fails this.
    @MainActor
    func testDocuments_SaveWaitsForAChange() throws {
        guard let app = documentsOpen() else { return }
        XCTAssertTrue(documentsTap(app, "shopping list.txt"), "the text file's row never built")
        XCTAssertTrue(documentsBody(app).waitForExistence(timeout: 20), "the editor never opened")

        let save = app.buttons["save"].firstMatch
        XCTAssertTrue(save.exists, "a writable library's editor offers no save at all")
        evidence(app, "documents-03-save-disabled")
        XCTAssertFalse(save.isEnabled, "save is enabled on a file nobody has touched")

        documentsCaretToEnd(app)
        app.typeText("x")
        XCTAssertTrue(save.isEnabled, "save stayed disabled after a keystroke")
    }

    /// Closing with edits in hand asks first, `cancel` comes back with them,
    /// and `discard` really does throw them away rather than saving.
    @MainActor
    func testDocuments_ClosingDirtyAsksFirst() throws {
        guard let app = documentsOpen() else { return }
        XCTAssertTrue(documentsTap(app, "shopping list.txt"), "the text file's row never built")
        XCTAssertTrue(documentsBody(app).waitForExistence(timeout: 20), "the editor never opened")

        documentsCaretToEnd(app)
        app.typeText("GHERKINS")
        app.buttons["close"].firstMatch.tap()

        let asked = app.staticTexts["unsaved changes"]
        XCTAssertTrue(asked.waitForExistence(timeout: 10), "closing a dirty editor did not ask")
        evidence(app, "documents-04-discard-dialog")
        XCTAssertTrue(
            startingWith(app, "You have unsaved changes").exists,
            "the dialog does not say what is about to be lost"
        )

        // Cancel keeps the editor, with the typing still in it.
        app.buttons["cancel"].firstMatch.tap()
        XCTAssertTrue(asked.waitForNonExistence(timeout: 10), "cancel did not close the dialog")
        XCTAssertTrue(
            documentsText(app).contains("GHERKINS"),
            "cancel threw the edit away: \(documentsText(app))"
        )

        // Discard closes, and nothing was saved.
        app.buttons["close"].firstMatch.tap()
        XCTAssertTrue(asked.waitForExistence(timeout: 10), "the second close did not ask")
        app.buttons["discard"].firstMatch.tap()

        XCTAssertTrue(documentsTap(app, "shopping list.txt"), "discard did not close the editor")
        XCTAssertTrue(documentsBody(app).waitForExistence(timeout: 20), "the file did not reopen")
        evidence(app, "documents-05-after-discard")
        XCTAssertFalse(
            documentsText(app).contains("GHERKINS"),
            "discard saved the edit it was asked to throw away"
        )
        XCTAssertTrue(documentsText(app).contains("anchovies"), "the file came back empty")
    }

    /// A `.md` is edited through `GET /api/documents/doc`, not through
    /// `/text`.
    ///
    /// The one assertion that matters is the second: the house's `/text` copy
    /// of `notes.md` says `oat milk`, and the fixture's `/doc` copy does not.
    /// An editor that took the text route would load, look right, and show
    /// the wrong file's words.
    @MainActor
    func testDocuments_MarkdownComesFromTheDocumentRoute() throws {
        guard let app = documentsOpen() else { return }
        app.buttons["notes.md"].firstMatch.tap()
        XCTAssertTrue(documentsBody(app).waitForExistence(timeout: 20), "the markdown editor never opened")
        evidence(app, "documents-06-markdown")

        let said = documentsText(app)
        XCTAssertTrue(said.contains("via-the-doc-route"), "not the document route's copy: \(said)")
        XCTAssertFalse(said.contains("oat milk"), "this is the TEXT route's copy of notes.md: \(said)")
        XCTAssertTrue(app.staticTexts["markdown"].exists, "the pill does not say which editor this is")
    }

    /// The formatting row marks up **at the caret** and leaves the caret
    /// between the marks, which is `applyMdTool`'s whole re-placement rule.
    ///
    /// Caret at the end, `bold`: the document must end in four asterisks.
    /// Then typing goes **between** them. A row that drew and did nothing
    /// fails the first assertion; a row that inserted at offset zero, or that
    /// left the caret after the marks, fails the second.
    @MainActor
    func testDocuments_MarkdownToolbarWrapsAtTheCaret() throws {
        guard let app = documentsOpen() else { return }
        app.buttons["notes.md"].firstMatch.tap()
        XCTAssertTrue(documentsBody(app).waitForExistence(timeout: 20), "the markdown editor never opened")

        documentsCaretToEnd(app)
        let bold = app.buttons["bold"].firstMatch
        XCTAssertTrue(bold.waitForExistence(timeout: 10), "a markdown file has no formatting row")
        bold.tap()
        evidence(app, "documents-07-markdown-toolbar")

        XCTAssertTrue(
            documentsText(app).hasSuffix("****"),
            "bold did not mark up at the caret: \(documentsText(app))"
        )
        XCTAssertTrue(app.staticTexts["unsaved"].exists, "marking up did not make the editor dirty")

        app.typeText("pears")
        XCTAssertTrue(
            documentsText(app).hasSuffix("**pears**"),
            "the caret was not left between the marks: \(documentsText(app))"
        )
    }

    /// Emptying a document and saving it is refused by the server, and the
    /// server's own sentence is what the person reads.
    ///
    /// An app that said `saved` on a 409 — or that showed `409: {...}` —
    /// fails this. So does one that cleared `unsaved` on a save that did not
    /// land.
    @MainActor
    func testDocuments_BlankMarkdownSaveIsRefusedAndSaid() throws {
        guard let app = documentsOpen() else { return }
        app.buttons["notes.md"].firstMatch.tap()
        XCTAssertTrue(documentsBody(app).waitForExistence(timeout: 20), "the markdown editor never opened")

        documentsCaretToEnd(app)
        clearField(app)
        clearField(app)
        clearField(app)
        XCTAssertEqual("", documentsText(app).trimmingCharacters(in: .whitespacesAndNewlines),
                       "the document was not emptied, so the 409 would not be reached")

        app.buttons["save"].firstMatch.tap()
        let refusal = startingWith(app, "Not saved: this would have replaced")
        XCTAssertTrue(refusal.waitForExistence(timeout: 20), "the server's refusal is nowhere on screen")
        evidence(app, "documents-08-blank-refused")
        XCTAssertTrue(app.staticTexts["unsaved"].exists, "a refused save was reported as a saved one")
    }

    // MARK: - the sheet editor

    /// A spreadsheet opens as a grid, takes a cell, saves the **whole** grid
    /// and still has both when it is opened again.
    ///
    /// The second reopened assertion is the one that catches a save sending
    /// only the changed cells: the server diffs the grid it is given, so a
    /// partial body blanks everything left out of it — `A1` would come back
    /// empty.
    @MainActor
    func testDocuments_SheetOpensAsAGridAndSaves() throws {
        guard let app = documentsOpen() else { return }
        XCTAssertTrue(documentsTap(app, "a spreadsheet.xlsx"), "the spreadsheet's row never built")

        let a1 = app.textFields["A1"]
        XCTAssertTrue(a1.waitForExistence(timeout: 20), "the grid never drew")
        evidence(app, "documents-09-sheet")
        XCTAssertEqual("quarter", a1.value as? String, "A1 is not the first row's first cell")
        XCTAssertTrue(app.staticTexts["sheet"].exists, "no pill saying which editor this is")

        // A5 is in the padded region — the grid is four rows of data shown as
        // eight, which is what gives a small sheet somewhere to type.
        let a5 = app.textFields["A5"]
        XCTAssertTrue(a5.exists, "the grid was not padded to eight rows")
        a5.tap()
        app.typeText("GHERKINS")
        XCTAssertTrue(app.staticTexts["unsaved"].waitForExistence(timeout: 5),
                      "typing in a cell did not make the sheet dirty")

        app.buttons["save"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["unsaved"].waitForNonExistence(timeout: 25),
                      "the sheet still says unsaved after a save that should have landed")
        app.buttons["close"].firstMatch.tap()

        XCTAssertTrue(documentsTap(app, "a spreadsheet.xlsx"), "back to the list")
        XCTAssertTrue(a1.waitForExistence(timeout: 20), "the sheet did not open a second time")
        evidence(app, "documents-10-sheet-reopened")
        XCTAssertEqual("GHERKINS", app.textFields["A5"].value as? String, "the typed cell was not saved")
        XCTAssertEqual("quarter", a1.value as? String,
                       "A1 was blanked — the save sent only the changed cells")
    }

    /// A cell holding both a formula and its cached value shows the
    /// **formula**, because that is what editing it has to change.
    ///
    /// `9191` is seeded nowhere else in the house, so its appearance anywhere
    /// on screen means `display` was ported backwards — and every formula in
    /// a household's sheet would become its own last value the next time
    /// anybody saved.
    @MainActor
    func testDocuments_AFormulaCellShowsTheFormula() throws {
        guard let app = documentsOpen() else { return }
        XCTAssertTrue(documentsTap(app, "a spreadsheet.xlsx"), "the spreadsheet's row never built")
        XCTAssertTrue(app.textFields["A1"].waitForExistence(timeout: 20), "the grid never drew")
        evidence(app, "documents-11-formula")

        XCTAssertEqual("=SUM(B2:B3)", app.textFields["B4"].value as? String,
                       "the total cell is not showing its formula")
        XCTAssertEqual(
            0,
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "value == %@", "9191")).count,
            "the formula's cached value is on screen, so display() is the wrong way round"
        )
    }

    // MARK: - + New

    /// `+ New` makes a spreadsheet and opens it exactly as tapping its row
    /// would, and the row is in the folder afterwards.
    @MainActor
    func testDocuments_NewSpreadsheetIsMadeAndOpens() throws {
        guard let app = documentsOpen() else { return }
        app.buttons["new"].firstMatch.tap()
        let kind = startingWith(app, "Spreadsheet")
        XCTAssertTrue(kind.waitForExistence(timeout: 10), "the new-file menu has no Spreadsheet")
        kind.tap()

        let field = app.textFields["new spreadsheet"]
        XCTAssertTrue(field.waitForExistence(timeout: 10), "no name prompt came up")
        field.tap()
        app.typeText("Budget")
        evidence(app, "documents-12-new-prompt")
        app.buttons["create"].firstMatch.tap()

        XCTAssertTrue(app.textFields["A1"].waitForExistence(timeout: 25),
                      "the new spreadsheet did not open in the grid editor")
        evidence(app, "documents-13-new-opened")
        XCTAssertTrue(app.staticTexts["Budget.xlsx"].exists,
                      "the editor is not titled with the file that was just made")
        app.buttons["close"].firstMatch.tap()

        XCTAssertTrue(documentsScrollTo(app, app.buttons["Budget.xlsx"]),
                      "the new file is not in the folder")
    }

    /// A name that is taken is said in the server's own words, with the
    /// prompt still up so it can be fixed.
    ///
    /// Android's `PromptDialog` is opened `dismissOnConfirm = false` for
    /// exactly this; a prompt that closed on the refusal would throw the
    /// typed name away.
    @MainActor
    func testDocuments_ATakenNameIsSaidAndTheDialogStays() throws {
        guard let app = documentsOpen() else { return }
        app.buttons["new"].firstMatch.tap()
        let kind = startingWith(app, "Text file")
        XCTAssertTrue(kind.waitForExistence(timeout: 10), "the new-file menu has no Text file")
        kind.tap()

        let field = app.textFields["new text file"]
        XCTAssertTrue(field.waitForExistence(timeout: 10), "no name prompt came up")
        field.tap()
        app.typeText("shopping list.txt")
        app.buttons["create"].firstMatch.tap()

        let said = startingWith(app, "There's already a file called")
        XCTAssertTrue(said.waitForExistence(timeout: 20), "a taken name was not reported")
        evidence(app, "documents-14-name-taken")
        XCTAssertTrue(app.buttons["create"].firstMatch.exists,
                      "the prompt closed on the refusal, so the typed name is gone")
    }

    /// `+ New` is in Documents and nowhere else. An over-broad gate would put
    /// it on Music, where `/api/documents/create` writes nothing.
    @MainActor
    func testDocuments_NewIsOnlyInDocuments() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the documents fixture's seed")
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")
        evidence(app, "documents-15-music-header")
        XCTAssertFalse(app.buttons["new"].exists, "Music offers a new-document button")

        XCTAssertTrue(pickLibrary(app, "Documents"), "the library picker never opened")
        XCTAssertTrue(app.buttons["new"].waitForExistence(timeout: 20),
                      "Documents does not offer a new-document button")
    }

    // MARK: - the two gates

    /// A device an admin has blocked gets neither `+ New` nor a Save — and
    /// the editor says why rather than drawing a button that would 403.
    @MainActor
    func testDocuments_ABlockedDeviceGetsNoNewAndNoSave() throws {
        guard let app = documentsOpen(blocked: true) else { return }
        evidence(app, "documents-16-blocked-list")
        XCTAssertFalse(app.buttons["new"].exists, "a blocked device is offered a new document")

        XCTAssertTrue(documentsTap(app, "shopping list.txt"), "the text file's row never built")
        XCTAssertTrue(documentsBody(app).waitForExistence(timeout: 20), "the editor never opened")
        evidence(app, "documents-17-blocked-editor")
        XCTAssertFalse(app.buttons["save"].exists, "a blocked device is offered a save that would 403")
        XCTAssertTrue(
            app.staticTexts["this phone can't change files here"].exists,
            "the save is gone and nothing says why"
        )
    }

    /// A formatted document keeps the read-only reader: the editor this port
    /// does not build is not claimed by one that would 415 on every save.
    @MainActor
    func testDocuments_AFormattedDocumentStaysReadOnly() throws {
        guard let app = documentsOpen() else { return }
        // A formatted document's row shows no extension
        // (``FileKinds/displayName(of:isDir:)``).
        XCTAssertTrue(documentsTap(app, "Letter to the council"), "the document's row never built")

        let readOnly = app.staticTexts["read-only — editing is on the dashboard"]
        XCTAssertTrue(readOnly.waitForExistence(timeout: 20), "an .html did not open the reader")
        evidence(app, "documents-18-formatted-read-only")
        XCTAssertFalse(app.buttons["save"].exists, "the reader is offering a save it cannot do")
        XCTAssertFalse(app.textFields["A1"].exists, "an .html opened the grid editor")
    }

    // MARK: - the audit, and the type size

    /// Apple's own audit over both editors, classified by signature.
    ///
    /// `rest` must be empty: an anonymous Dynamic Type headline is the
    /// measured floor (``floorAndRest(_:)``), anything Apple can attribute to
    /// an element is a defect.
    @MainActor
    func testDocuments_EditorAudit() throws {
        guard let app = documentsOpen() else { return }
        XCTAssertTrue(documentsTap(app, "shopping list.txt"), "the text file's row never built")
        XCTAssertTrue(documentsBody(app).waitForExistence(timeout: 20), "the text editor never opened")
        evidence(app, "documents-19-audit-text")
        let text = Self.floorAndRest(try auditIssues(app, "documents-text-editor"))
        XCTAssertEqual([], text.rest, "the text editor has attributable audit findings")
        app.buttons["close"].firstMatch.tap()

        XCTAssertTrue(documentsTap(app, "a spreadsheet.xlsx"), "the spreadsheet's row never built")
        XCTAssertTrue(app.textFields["A1"].waitForExistence(timeout: 20), "the grid never drew")
        evidence(app, "documents-20-audit-sheet")
        let sheet = Self.floorAndRest(try auditIssues(app, "documents-sheet-editor"))
        XCTAssertEqual([], sheet.rest, "the sheet editor has attributable audit findings")
    }

    /// A cell grows with the type size.
    ///
    /// **Measured, not read off a headline.** A named Dynamic Type finding
    /// has twice been a false positive on this track, so this compares one
    /// cell's height at `large` and at AX5. A `frame(height:)` or a
    /// `minimumScaleFactor` on a cell would hold the two equal.
    @MainActor
    func testDocuments_TheGridGrowsAtAccessibilitySizes() throws {
        func height(_ mode: String, _ name: String) -> CGFloat {
            guard let app = documentsOpen(mode) else { return 0 }
            guard documentsTap(app, "a spreadsheet.xlsx") else { return 0 }
            let a1 = app.textFields["A1"]
            guard a1.waitForExistence(timeout: 25) else {
                print("=== NO GRID AT \(mode) — tree follows ===")
                print(app.debugDescription)
                return 0
            }
            evidence(app, name)
            let found = a1.frame.height
            app.terminate()
            return found
        }

        let ordinary = height(Self.ordinaryTypeSize, "documents-21-cell-large")
        let huge = height("UICTContentSizeCategoryAccessibilityXXXL", "documents-22-cell-ax5")
        XCTAssertGreaterThan(ordinary, 0, "the grid never drew at the ordinary type size")
        XCTAssertGreaterThan(
            huge, ordinary,
            "a cell is \(huge)pt at AX5 and \(ordinary)pt at large — it does not grow with the type size"
        )
    }
}
