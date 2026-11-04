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
        let big = Self.documentsIsBig(mode)
        let (app, landed) = openFiles(mode, blocked: blocked)
        // ### `landed` is false at AX5, and the folder is there anyway
        //
        // Train S008 failed ``testDocuments_TheGridGrowsAtAccessibilitySizes``
        // here with "the folder never answered". The tree it printed says
        // otherwise: Files had drawn, with its `•••`, its title and its
        // subtitle on screen. What was missing was the marker
        // ``openFiles(_:blocked:delay:)`` waits for at an accessibility size,
        // the `choose library` button — because Files' own content is a
        // `LazyVStack` in a `ScrollView`, the phone-source switch above it
        // takes 164 of the window's 400 points, and the header alone is 383
        // points tall at AX5. The picker is not off screen: it is **unbuilt**,
        // which is this screen's standing trap one level further up than the
        // rows it was written about.
        //
        // So the wait is allowed to time out and the picker is scrolled to,
        // exactly as a row is. A folder that really never answered still
        // fails, one flick-budget later.
        if !landed, big {
            // Its own failure, not `pickLibrary`'s: train S011 reported "the
            // library picker never opened" for a picker that was never
            // scrolled to, and the sentence sent two cases' diagnosis to the
            // menu instead of to the scroll.
            guard documentsScrollTo(app, app.buttons["choose library"].firstMatch, big: true) else {
                XCTFail("the library picker never built — the folder did not scroll")
                return nil
            }
        } else if !landed {
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
        guard documentsScrollTo(app, marker, big: big) else {
            XCTFail("Documents never listed")
            return nil
        }
        return app
    }

    /// Whether a launch mode is one of the accessibility type sizes.
    ///
    /// The same test ``openFiles(_:blocked:delay:)`` makes, written once
    /// here because three helpers in this file now need the answer.
    static func documentsIsBig(_ mode: String) -> Bool {
        mode.hasPrefix("UICTContentSizeCategoryAccessibility")
    }

    /// Scroll the folder until `element` is **built**.
    ///
    /// Files draws its rows in a `LazyVStack` inside a `ScrollView`, so a row
    /// below the fold does not exist rather than merely sitting off screen —
    /// and `shopping list.txt`, the row this item added, is the eleventh.
    /// Not ``scrollUntilBuilt(_:_:flicks:)``: that one scrolls
    /// `app.collectionViews.firstMatch`, which this screen has none of.
    ///
    /// ### The swipe is the folder's, not the window's
    ///
    /// `app.swipeUp()` starts in the middle of the window, and at an
    /// accessibility type size the middle of the window is the phone-source
    /// switch above the list — a swipe there scrolls nothing at all, which
    /// is half of why train S008's AX5 case never found anything. Swiping
    /// the `ScrollView` itself is the same gesture at `large`, where the
    /// list does occupy the middle, so both sizes take it.
    ///
    /// At AX5 the list scrolls inside a 178-point window past a 383-point
    /// header, so it gets the slow swipe (a fast one steps over rows) and a
    /// much longer budget.
    ///
    /// ### And it is not `scrollViews.firstMatch`
    ///
    /// Train S011 ran all forty flicks and the folder's scroll bar still read
    /// `value: 0%`. The log says where they went:
    ///
    /// ```
    /// t = 101.22s  Swipe up ScrollView (First Match)
    /// t = 101.72s  Swipe up ScrollView at {{0.0, 706.0}, {393.0, 146.0}}
    /// ```
    ///
    /// `{0, 706}` is the **phone bar** — home · music · videos · chat is a
    /// horizontal `ScrollView`, and `firstMatch` resolved to it, not to the
    /// folder at `{0, 523}`. Forty vertical swipes on a horizontal strip
    /// scroll nothing, so the picker stayed unbuilt and two cases failed one
    /// sentence later with "the library picker never opened".
    ///
    /// The folder is told apart by position rather than by index: the phone
    /// bar is pinned to the window's bottom, so the content scroll view is
    /// always the one with the smaller `minY`. Tree order does not decide it
    /// — the folder comes first in the printed tree and `firstMatch` took the
    /// bar anyway.
    @MainActor
    func documentsScrollTo(
        _ app: XCUIApplication,
        _ element: XCUIElement,
        flicks: Int = 12,
        big: Bool = false
    ) -> Bool {
        let budget = big ? 40 : flicks
        for _ in 0..<budget where !element.exists {
            // Re-resolved each flick: the bar and the folder are both built
            // from the first frame, but a query held across a swipe goes
            // stale on this screen often enough to be worth the lookup.
            guard let folder = documentsFolderScroll(app) else {
                app.swipeUp()
                continue
            }
            if big {
                folder.swipeUp(velocity: .slow)
            } else {
                folder.swipeUp()
            }
        }
        if !element.exists {
            print("=== \(budget) FLICKS AND STILL UNBUILT — tree follows ===")
            print(app.debugDescription)
        }
        return element.exists
    }

    /// The folder's own scroll view — the upper of the screen's two.
    ///
    /// Files' content scrolls in one `ScrollView` and the phone bar's
    /// home · music · videos · chat strip is another, pinned to the window's
    /// bottom; `scrollViews.firstMatch` took the bar (see
    /// ``documentsScrollTo(_:_:flicks:big:)``). Smallest `minY` wins, which
    /// stays right however many scroll views the screen grows, as long as the
    /// bar is the bottom-most thing in the window — and it is, by
    /// construction.
    ///
    /// ### The breadcrumb strip, and why width decides first (train S013)
    ///
    /// Smallest `minY` alone was still wrong. The breadcrumb — `library root
    /// › Documents` — is a **horizontal** `ScrollView` *inside* the folder,
    /// so once the folder has scrolled it sits **above** its own clipping
    /// parent: at AX5 the tree has the folder at `{0, 523.3}` and the
    /// breadcrumb at `{16, 286.3}`, and the smaller number is the one that
    /// cannot be touched. XCUITest said so exactly — *"visible frame is
    /// empty, which may happen if the element is offscreen"* — and threw,
    /// which is what ended `TheGridGrowsAtAccessibilitySizes`.
    ///
    /// So a candidate must first **span the window**: the folder and the
    /// phone bar are both full width, every inset strip inside the folder is
    /// not. Among those two, smallest `minY` still picks the folder, since
    /// the bar is pinned to the bottom. Position is not asked of an element
    /// that cannot be scrolled anyway.
    ///
    /// `nil` means the screen has no scroll view at all, which is a different
    /// thing from "it did not scroll" and the caller treats it differently.
    @MainActor
    func documentsFolderScroll(_ app: XCUIApplication) -> XCUIElement? {
        let window = app.windows.firstMatch.frame.width
        let all = app.scrollViews
        var highest: XCUIElement?
        var highestY = CGFloat.greatestFiniteMagnitude
        for index in 0..<all.count {
            let candidate = all.element(boundBy: index)
            guard candidate.exists else { continue }
            let box = candidate.frame
            // Full width to within a point: a strip inset in the folder's
            // 16-point gutter is 32 points narrower, and the two scroll views
            // that matter are edge to edge.
            guard window <= 0 || box.width >= window - 1 else { continue }
            if box.minY < highestY {
                highestY = box.minY
                highest = candidate
            }
        }
        return highest
    }

    /// Open one Documents row, scrolling to it first.
    ///
    /// `big` is passed on to ``documentsScrollTo(_:_:flicks:big:)`` rather
    /// than read from anywhere: an extension has no stored state, and a case
    /// that launched at AX5 is the only thing that knows it did.
    @MainActor
    func documentsTap(_ app: XCUIApplication, _ row: String, big: Bool = false) -> Bool {
        let button = app.buttons[row].firstMatch
        guard documentsScrollTo(app, button, big: big) else { return false }
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

    /// A formatted document opens in an editor on the phone, saves, and
    /// cannot be navigated off the house.
    ///
    /// This case **replaced** `testDocuments_AFormattedDocumentStaysReadOnly`,
    /// which asserted the opposite and was right until owner answer 21: a
    /// `.html` now opens the dashboard's own editor page in a `WKWebView`
    /// (``DocumentsWebEditorHost``), so the old case was a guard on a
    /// decision that has been taken the other way. The three things it is
    /// worth asserting are the three the port could get wrong:
    ///
    /// * **the page read the document** — `COUNCIL-BY-THE-DOC-ROUTE` is in
    ///   the `/doc` answer and nowhere else, and the page can only have it
    ///   if the token reached it through the bridge rather than through the
    ///   URL. A page that got no token shows `PAGE-COULD-NOT-READ` instead,
    ///   and ``docsHandoffMarkers`` names the other four ways the handoff can
    ///   be wrong;
    /// * **the page saved** — through `PUT /api/documents/doc`, with the
    ///   token, and the word it wrote is on screen when the file is opened
    ///   again;
    /// * **the origin lock holds** — the page carries a link to
    ///   `example.invalid`, the navigation policy cancels it, and the host
    ///   says so. A port that left `decidePolicyFor` out would leave the
    ///   house entirely, and the toast is the only part of that a case can
    ///   see.
    @MainActor
    func testDocuments_AFormattedDocumentOpensInTheEditor() throws {
        guard let app = documentsOpen() else { return }
        // A formatted document's row shows no extension
        // (``FileKinds/displayName(of:isDir:)``).
        XCTAssertTrue(documentsTap(app, "Letter to the council"), "the document's row never built")

        let said = containing(app, "COUNCIL-BY-THE-DOC-ROUTE")
        XCTAssertTrue(said.waitForExistence(timeout: 30),
                      "an .html did not open an editor showing the document's own words")
        evidence(app, "documents-18-formatted-editor")
        XCTAssertTrue(app.staticTexts[DocumentsPillWord.document].exists,
                      "no pill saying which editor this is")
        XCTAssertFalse(app.textFields["A1"].exists, "an .html opened the grid editor")
        for marker in Self.docsHandoffMarkers {
            XCTAssertFalse(containing(app, marker).exists,
                           "the page reports \(marker) — the token handoff is wrong")
        }

        // The save is the page's own control, not the host's: the host draws
        // no Save, because the page owns the document's dirty state.
        app.buttons["Save in page"].firstMatch.tap()
        XCTAssertTrue(containing(app, "page saved").waitForExistence(timeout: 25),
                      "the page's save never landed")

        // Off the house, and back again. Asserted after the save so a
        // cancelled navigation cannot be mistaken for a page that never
        // loaded.
        app.links["leave the house"].firstMatch.tap()
        let stayed = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label == %@", DocumentsWebWord.stayedHere))
            .firstMatch
        XCTAssertTrue(stayed.waitForExistence(timeout: 20),
                      "an off-origin link was followed, or the refusal was silent")
        evidence(app, "documents-18b-origin-refused")
        XCTAssertTrue(containing(app, "COUNCIL-BY-THE-DOC-ROUTE").exists,
                      "the page left the document after a link it was not allowed to follow")

        app.buttons["close"].firstMatch.tap()
        XCTAssertTrue(documentsTap(app, "Letter to the council"), "back to the list")
        XCTAssertTrue(containing(app, "SAVED-FROM-THE-PHONE").waitForExistence(timeout: 30),
                      "the saved words are not in the document the house hands back")
        // After the wait, not before it: this paragraph is drawn on the same
        // load as the document, so asking before the page has answered asks
        // about a page that is not there yet.
        XCTAssertTrue(containing(app, "BRIDGE-HAS-DOWNLOAD").exists,
                      "the page's Download menu would fall back to a browser's way of doing it")
        evidence(app, "documents-18c-formatted-reopened")
    }

    /// The **five** markers the stub editor page draws about its own handoff,
    /// each one naming a different way the port can be wrong. Every
    /// web-editor case asserts all five are absent.
    ///
    /// Four are about the token: it never arrived on the page request
    /// (`NO-TOKEN-ON-THE-PAGE-REQUEST`, which only the server can see), the
    /// bridge handed it over twice inside one document
    /// (`TOKEN-HANDED-TWICE`), the injected script never ran at all
    /// (`NO-BRIDGE-ON-THIS-PAGE`), or the token did not satisfy the read
    /// (`PAGE-COULD-NOT-READ`). The fifth is not about the token:
    /// `NO-DOWNLOAD-ON-THE-BRIDGE` is the branch `doc_editor.jsx` takes for
    /// its Download menu when the bridge has no `saveFile`/`print` — an
    /// `<a download>` click and `window.print()`, both of which a `WKWebView`
    /// ignores silently.
    ///
    /// `TOKEN-AFTER-RELOAD` is deliberately **not** in this list: it is only
    /// drawn on the page's second load, and
    /// ``testDocs_ASecondDocumentGetsNoToken`` is the one case that asks for
    /// one.
    static let docsHandoffMarkers = [
        "NO-TOKEN-ON-THE-PAGE-REQUEST",
        "TOKEN-HANDED-TWICE",
        "NO-BRIDGE-ON-THIS-PAGE",
        "PAGE-COULD-NOT-READ",
        "NO-DOWNLOAD-ON-THE-BRIDGE",
    ]

    /// The host's own words, spelled here for the reason
    /// ``DocumentsPillWord`` is: this target does not link `DomovoiKit`.
    enum DocumentsWebWord {
        static let stayedHere = "that link isn't on your home server, so it wasn't opened"
    }

    // MARK: - the audit, and the type size

    /// Apple's own audit over both editors, classified by signature.
    ///
    /// `rest` must be empty, with one carve-out this case **measures itself**
    /// rather than assumes.
    ///
    /// ### What train S008 found, and what is a defect in it
    ///
    /// Four attributable findings, all on the text editor:
    ///
    /// ```
    /// Contrast failed                           label='save'
    /// Dynamic Type … partially unsupported      label='close'
    /// Dynamic Type … partially unsupported      label='save'
    /// Dynamic Type … partially unsupported      label='text'
    /// ```
    ///
    /// The contrast one is **real and visible in the screenshot**: `save` was
    /// a `.disabled()` bar button, drawn in the system's light grey on a
    /// white bar. S013 failed it again after the colour was changed, and the
    /// screenshot measured the reason — the darkest pixel of the word is
    /// `#ABAAA7` (2.32:1), which is the `#58554F` token at half opacity,
    /// because `.plain` dims a disabled label whatever colour it is given.
    /// It is fixed in the app (``DocumentsEditorBarButtonStyle``), and
    /// nothing here excuses a contrast finding.
    ///
    /// The three Dynamic Type ones are the third time on this track a *named*
    /// Dynamic Type finding has appeared on elements with no fixed point size
    /// anywhere near them — ``knownScalingFalsePositive`` is the second, and
    /// the rule that came out of both is: measure the element before
    /// believing the headline. So this case measures them, at
    /// ``ordinaryTypeSize`` and at AX5, and excuses a Dynamic Type finding
    /// **only** for a label it has just watched grow. A label that does not
    /// grow fails with its two numbers printed, which is what a real defect
    /// looks like.
    @MainActor
    func testDocuments_EditorAudit() throws {
        guard let app = documentsOpen() else { return }
        XCTAssertTrue(documentsTap(app, "shopping list.txt"), "the text file's row never built")
        XCTAssertTrue(documentsBody(app).waitForExistence(timeout: 20), "the text editor never opened")
        evidence(app, "documents-19-audit-text")
        let text = Self.floorAndRest(try auditIssues(app, "documents-text-editor"))
        app.buttons["close"].firstMatch.tap()

        XCTAssertTrue(documentsTap(app, "a spreadsheet.xlsx"), "the spreadsheet's row never built")
        XCTAssertTrue(app.textFields["A1"].waitForExistence(timeout: 20), "the grid never drew")
        evidence(app, "documents-20-audit-sheet")
        let sheet = Self.floorAndRest(try auditIssues(app, "documents-sheet-editor"))
        // **Both audits first, then the assertions.** `continueAfterFailure`
        // is false for this suite, so the text editor's four findings ended
        // train S008's case before the sheet editor was audited at all — and
        // the two bars were identical, so the sheet carried the same contrast
        // defect unreported for a round.
        app.terminate()

        let grew = documentsScalingGrew()
        XCTAssertEqual(
            [], documentsUnexcused(text.rest, grew: grew),
            "the text editor has attributable audit findings"
        )
        XCTAssertEqual(
            [], documentsUnexcused(sheet.rest, grew: grew),
            "the sheet editor has attributable audit findings"
        )
    }

    /// The editor chrome's three leaves, measured at both type sizes; the
    /// ones that **grew** come back.
    ///
    /// Measured on `notes.md`, which ``documentsOpen(_:blocked:)`` already
    /// waits for and scrolls to, and not on `shopping list.txt`: at AX5 the
    /// folder scrolls inside a 178-point window past a 383-point header, so
    /// the eleventh row is a 2 400-point haul this measurement does not need
    /// to make. The pill that comes back says `markdown` there and `text` in
    /// the audited editor — **the same ``DocumentsEditorPill``, the same
    /// `.caption2`, the same place on the bar**, one word apart. Mapping the
    /// finding by view rather than by word is the only shortcut here, and a
    /// pill that could not scale would fail this measurement just the same.
    @MainActor
    func documentsScalingGrew() -> Set<String> {
        let leaves = ["close", "save", DocumentsPillWord.markdown]
        var ordinary: [String: CGFloat] = [:]
        var grew: Set<String> = []
        for mode in [Self.ordinaryTypeSize, "UICTContentSizeCategoryAccessibilityXXXL"] {
            let big = Self.documentsIsBig(mode)
            guard let app = documentsOpen(mode) else { return grew }
            guard documentsTap(app, "notes.md", big: big) else { return grew }
            guard documentsBody(app).waitForExistence(timeout: 25) else {
                print("=== NO EDITOR AT \(mode) — tree follows ===")
                print(app.debugDescription)
                return grew
            }
            for leaf in leaves {
                let found = documentsLeaf(app, leaf)
                let height = found.exists ? found.frame.height : 0
                if big {
                    let was = ordinary[leaf] ?? 0
                    print("SCALE \(leaf) \(was) -> \(height)")
                    if height > was, was > 0 { grew.insert(leaf) }
                } else {
                    ordinary[leaf] = height
                }
            }
            app.terminate()
        }
        return grew
    }

    /// One leaf of the editor's chrome by its word: `close` and `save` are
    /// buttons, the pill is a bare static text.
    @MainActor
    func documentsLeaf(_ app: XCUIApplication, _ label: String) -> XCUIElement {
        let button = app.buttons[label].firstMatch
        return button.exists ? button : app.staticTexts[label].firstMatch
    }

    /// The findings a measurement does **not** excuse.
    ///
    /// Only "Dynamic Type …" is ever excused, and only for a label in
    /// `grew`; the pill is mapped by view, so the audited editor's `text`
    /// rides on the measured `markdown`. Everything else — a contrast
    /// finding, a clipped text, a hit area, a Dynamic Type finding on a leaf
    /// nobody measured — comes straight back.
    func documentsUnexcused(_ found: [String], grew: Set<String>) -> [String] {
        let pillWords = [DocumentsPillWord.text, DocumentsPillWord.markdown,
                         DocumentsPillWord.sheet, DocumentsPillWord.document]
        let pillMeasured = grew.contains(DocumentsPillWord.markdown)
        return found.filter { line in
            guard line.hasPrefix("Dynamic Type font sizes") else { return true }
            if pillWords.contains(where: { line.contains("label='\($0)'") }) {
                return !pillMeasured
            }
            return !grew.contains { line.contains("label='\($0)'") }
        }
    }

    /// The three pill words, spelled here rather than imported: this target
    /// does not link `DomovoiKit`, and a test that read the app's own
    /// constant could not tell a renamed pill from a missing one.
    enum DocumentsPillWord {
        static let text = "text"
        static let markdown = "markdown"
        static let sheet = "sheet"
        /// ``DocumentsEditorKind/web``'s pill, which arrived with owner
        /// answer 21.
        static let document = "document"
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
            let big = Self.documentsIsBig(mode)
            guard let app = documentsOpen(mode) else { return 0 }
            guard documentsTap(app, "a spreadsheet.xlsx", big: big) else { return 0 }
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
