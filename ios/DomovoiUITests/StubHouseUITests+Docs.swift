import XCTest

/// The two things owner answers 21 and 22 added to Documents: the
/// formatted-document editor (the dashboard's own page in a `WKWebView`) and
/// a spreadsheet the **phone** parses when the house cannot.
///
/// `StubHouseUITests+Documents.swift` keeps the cases that were already
/// there, including the one this work replaced
/// (``testDocuments_AFormattedDocumentOpensInTheEditor``); everything new is
/// here, and every case below shares that file's helpers —
/// ``documentsOpen(_:blocked:)``, ``documentsTap(_:_:big:)`` and the two
/// audit helpers — rather than a second copy of them.
///
/// The fixture is the same `design-notes/ios/stubhouse/fixtures/documents.py`,
/// and the words these cases look for are in it for the same reason the rest
/// of that seed is the way it is — one thing on the screen can produce each
/// one:
///
/// * `Letter to the council.html` says `COUNCIL-BY-THE-DOC-ROUTE`, which is
///   in the `/doc` answer and nowhere else;
/// * `Contract.docx` has **no** body in `/doc` at all, which is what the
///   real route does for a Word file (`documents.py:1990`) — so the page
///   showing anything is the page having survived a shape with a key
///   missing;
/// * `takings.csv` says `WHISTLES, tin of` inside quotes and carries a cell
///   two lines long. Both are **the parse**: a reader that split on commas
///   shows `WHISTLES` and `tin of"`;
/// * `greenhouse.xlsx` says `TOMATOES-BY-THE-PHONE` in a shared string, so
///   the grid proves the phone unzipped the file and read its string table.
///
/// ### What the page says about its own handoff
///
/// `window.DomovoiAppBridge` is the only way the page gets a token — never a
/// URL (``DocumentsPath/documentPage(rel:)`` says why at length). None of
/// that is visible from outside, so the stub page draws a marker for each way
/// it can be wrong: `NO-TOKEN-ON-THE-PAGE-REQUEST` (the host did not put the
/// token on the request it made), `TOKEN-HANDED-TWICE` (the bridge is not
/// one-shot, where Android's is a `getAndSet(null)`), `NO-BRIDGE-ON-THIS-PAGE`
/// (the user script never ran) and `PAGE-COULD-NOT-READ` (the token did not
/// satisfy the read). ``StubHouseUITests/docsHandoffMarkers`` is the list, and
/// every web-editor case asserts all four are absent.
extension StubHouseUITests {

    // MARK: - the formatted-document editor

    /// A Word file opens in the same editor, and the page survives a `/doc`
    /// answer with **no body in it**.
    ///
    /// `GET /doc` sends a `.docx` its details and nothing else, because the
    /// browser reads `/raw` and converts the file there — so this is the one
    /// shape of the three `_editor_format` answers that has no document in
    /// it. A host that only ever saw the `html` branch works fine until a
    /// Word file is opened, which is the second half of owner answer 21 and
    /// the reason the fixture grafts a `.docx` into a house that had none.
    ///
    /// The presence line is asserted **absent** here: nobody else is editing
    /// this file, and a notice that is always drawn is worth nothing.
    @MainActor
    func testDocs_AWordFileOpensInTheEditorToo() throws {
        guard let app = documentsOpen() else { return }
        XCTAssertTrue(documentsTap(app, "Contract.docx"), "the Word file's row never built")

        let said = containing(app, "a Word file")
        XCTAssertTrue(said.waitForExistence(timeout: 30),
                      "a .docx did not open an editor — the tree is:\n\(app.debugDescription)")
        evidence(app, "docs-01-word-editor")
        XCTAssertTrue(app.staticTexts[DocumentsPillWord.document].exists,
                      "no pill saying which editor this is")
        for marker in Self.docsHandoffMarkers {
            XCTAssertFalse(containing(app, marker).exists,
                           "the page reports \(marker) — the token handoff is wrong")
        }
        XCTAssertFalse(containing(app, "Also editing").exists,
                       "a file nobody else has open is reported as being edited elsewhere")

        app.buttons["Save in page"].firstMatch.tap()
        XCTAssertTrue(containing(app, "page saved").waitForExistence(timeout: 25),
                      "a Word file's save never landed")
        evidence(app, "docs-02-word-saved")
    }

    /// "Somebody else is editing" is drawn, and it is the page that draws it.
    ///
    /// This is the third of the three things the Android host does that had
    /// to be ported, and the only one that is **not** code in this app: the
    /// notice comes from the collab layer the page already carries
    /// (`web/static/collab_ui.jsx` over `GET /api/collab/doc`), so porting it
    /// means hosting the page and not blocking its same-origin requests. The
    /// fixture answers that route with one other editor for this file and
    /// none for any other, so the notice being on screen is the page's
    /// request having been allowed out — and
    /// ``testDocs_AWordFileOpensInTheEditorToo`` is the same assertion the
    /// other way round.
    ///
    /// It would fail if the navigation policy or the data store blocked the
    /// page's own `fetch`, which is the most likely way a stricter port
    /// breaks the editor without breaking the first load.
    @MainActor
    func testDocs_SomebodyElseEditingIsSaid() throws {
        guard let app = documentsOpen() else { return }
        XCTAssertTrue(documentsTap(app, "Letter to the council"), "the document's row never built")

        let notice = containing(app, "Also editing: Nadia on the kitchen laptop")
        XCTAssertTrue(notice.waitForExistence(timeout: 30),
                      "the page's own 'somebody else is editing' line never drew")
        evidence(app, "docs-03-also-editing")
    }

    // MARK: - the sheets the phone reads itself

    /// A `.csv` the house could not read is parsed **here**, and the grid says
    /// so instead of pretending it can be saved.
    ///
    /// Owner answer 22. The fixture answers `GET /sheet` for this file with a
    /// **500**, which is the case the fallback was written for; a 415 is a
    /// different thing entirely (the server saying the type is not
    /// round-trippable) and stops the editor where it is.
    ///
    /// The three cells are the three things a CSV parser gets wrong, and each
    /// one has a different wrong answer: a comma inside quotes (`WHISTLES`
    /// alone in `A2`), a doubled quote (`he said ""two"" again` with its
    /// pairs left in), and a field two lines long (`"two` in `A3` and
    /// `lines"` beginning a fourth row that should not exist).
    ///
    /// And **no Save**: there is no route a phone can PUT a workbook's bytes
    /// to, so a Save button here would 415 or, worse, write the parsed grid
    /// back through `/sheet` and lose everything the parser could not
    /// represent ([[no-misleading-controls]]).
    @MainActor
    func testDocs_ACsvTheHouseCannotReadIsParsedHere() throws {
        guard let app = documentsOpen() else { return }
        XCTAssertTrue(documentsTap(app, "takings.csv"), "the csv's row never built")

        let a1 = app.textFields["A1"]
        XCTAssertTrue(a1.waitForExistence(timeout: 30),
                      "the csv never became a grid — the tree is:\n\(app.debugDescription)")
        evidence(app, "docs-04-csv-grid")
        XCTAssertEqual("week", a1.value as? String, "A1 is not the first row's first cell")
        XCTAssertEqual("WHISTLES, tin of", app.textFields["A2"].value as? String,
                       "the quoted comma was split — A2 holds half a cell")
        XCTAssertEqual("12", app.textFields["B2"].value as? String,
                       "the cell after the quoted one is wrong, so the quote never closed")
        XCTAssertEqual(#"he said "two" again"#, app.textFields["C2"].value as? String,
                       "a doubled quote was not read as one quote")

        // `contains`, not equality: a newline inside a cell is a cell this
        // grid shows on one line, and what it does with the character is the
        // field's business. What matters is that **both** words are in one
        // cell — split on the newline, `lines` would start a fourth row.
        let twoLines = (app.textFields["A3"].value as? String) ?? ""
        XCTAssertTrue(twoLines.contains("two") && twoLines.contains("lines"),
                      "the cell with a newline in it was split across rows — A3 says \(twoLines)")

        XCTAssertTrue(containing(app, "read on this phone").exists,
                      "a grid the house did not send says nothing about where it came from")
        XCTAssertFalse(app.buttons["save"].exists,
                       "a sheet parsed on the phone offers a save there is no route for")
    }

    /// An `.xlsx` is unzipped, its shared strings resolved and its formula
    /// kept — all on the phone.
    ///
    /// The three assertions are the three parts of
    /// ``DocumentsXlsxSheet``: `TOMATOES-BY-THE-PHONE` is in
    /// `sharedStrings.xml` by index, so a reader that showed the index puts
    /// `0` in `A1`; `7` is a plain number spelt the way Python spells it; and
    /// `B4` holds a formula whose cached value `16` the server never sends
    /// (`data_only=False`), so the formula is the only thing it can show.
    ///
    /// The fixture deflates the workbook (`zipfile.ZIP_DEFLATED`), which
    /// makes this the only case that runs the inflate path on a device rather
    /// than in a package test.
    @MainActor
    func testDocs_AnXlsxIsUnzippedOnThePhone() throws {
        guard let app = documentsOpen() else { return }
        XCTAssertTrue(documentsTap(app, "greenhouse.xlsx"), "the workbook's row never built")

        let a1 = app.textFields["A1"]
        XCTAssertTrue(a1.waitForExistence(timeout: 30),
                      "the workbook never became a grid — the tree is:\n\(app.debugDescription)")
        evidence(app, "docs-05-xlsx-grid")
        XCTAssertEqual("TOMATOES-BY-THE-PHONE", a1.value as? String,
                       "A1 is the shared string's index or nothing — the string table was not read")
        XCTAssertEqual("7", app.textFields["B2"].value as? String,
                       "a plain number came out wrong")
        XCTAssertEqual("=SUM(B2:B3)", app.textFields["B4"].value as? String,
                       "the total cell is not showing its formula")
        XCTAssertTrue(containing(app, "read on this phone").exists,
                      "a grid the house did not send says nothing about where it came from")
    }

    // MARK: - + New

    /// `+ New > Document` makes a formatted document and opens it in the
    /// editor, and the row is in the folder afterwards.
    ///
    /// Before owner answer 21 `Document` was deliberately left out of the
    /// menu, because there was no editor to open what it made. It is first in
    /// the list now (`DocumentsNewKind`), and this case is the whole of why
    /// that is allowed: a kind whose file could not be opened again would be
    /// a `+ New` that made rubbish.
    ///
    /// It also covers the fixture's own half of it: `+ New`'s `.html` has to
    /// land where `/doc` reads it, and a created file that 404s on the way
    /// into the editor fails here rather than in a train's log.
    @MainActor
    func testDocs_NewDocumentIsMadeAndOpens() throws {
        guard let app = documentsOpen() else { return }
        app.buttons["new"].firstMatch.tap()
        // **Not `startingWith(app, "Document")`.** The breadcrumb and the
        // library button both say `Documents`, and `firstMatch` is tree
        // order rather than what is in front — an open menu does not put its
        // own rows first. The `NOT BEGINSWITH "Documents"` half is what makes
        // this the menu row, whether SwiftUI merges the row's two lines into
        // one label (`Document, Formatted document · .html`) or keeps the
        // first alone.
        let kind = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label BEGINSWITH %@ AND NOT label BEGINSWITH %@",
                                  "Document", "Documents"))
            .firstMatch
        XCTAssertTrue(kind.waitForExistence(timeout: 10), "the new-file menu has no Document")
        kind.tap()

        let field = app.textFields["new document"]
        XCTAssertTrue(field.waitForExistence(timeout: 10), "no name prompt came up")
        field.tap()
        app.typeText("Letter to the vet")
        evidence(app, "docs-06-new-prompt")
        app.buttons["create"].firstMatch.tap()

        // The page, not the grid and not the text editor: an empty document
        // has nothing to show, so the page's own "this document is empty" is
        // the marker. `Save in page` is the discriminating half — no native
        // editor in this app draws a control with that name.
        let save = app.buttons["Save in page"].firstMatch
        XCTAssertTrue(save.waitForExistence(timeout: 30),
                      "the new document did not open in the web editor — the tree is:\n\(app.debugDescription)")
        evidence(app, "docs-07-new-opened")
        // A formatted document's title has no extension
        // (``FileKinds/displayName(of:isDir:)``), which is also how the
        // dashboard lists it.
        XCTAssertTrue(app.staticTexts["Letter to the vet"].exists,
                      "the editor is not titled with the file that was just made")
        for marker in Self.docsHandoffMarkers {
            XCTAssertFalse(containing(app, marker).exists,
                           "the page reports \(marker) — the token handoff is wrong")
        }

        app.buttons["close"].firstMatch.tap()
        XCTAssertTrue(documentsScrollTo(app, app.buttons["Letter to the vet"]),
                      "the new document is not in the folder")
    }

    // MARK: - the audit, and the type size

    /// Apple's own audit over the web editor, classified by signature.
    ///
    /// The same measurement ``testDocuments_EditorAudit`` makes over the
    /// other two editors, and the same rule: `rest` must be empty, and a
    /// Dynamic Type finding is excused **only** for a label this run has
    /// watched grow ([[audit-reports-one-issue]] — measure the element before
    /// believing the headline).
    ///
    /// ### What this audit does and does not cover
    ///
    /// It covers the host: the pill, the bar's Close, the title, and the
    /// window the page is given. It also measures the **page**, and the
    /// fixture's page is written to be generous about type size, contrast and
    /// hit areas precisely so that a finding here is about this app — the
    /// real editor is a React bundle this house does not serve, and a finding
    /// about the stub's CSS would be a finding about nothing. If a train ever
    /// reports one that attributes to the page, the fixture is where it gets
    /// fixed, not the host.
    @MainActor
    func testDocs_TheWebEditorAudits() throws {
        guard let app = documentsOpen() else { return }
        XCTAssertTrue(documentsTap(app, "Letter to the council"), "the document's row never built")
        XCTAssertTrue(containing(app, "COUNCIL-BY-THE-DOC-ROUTE").waitForExistence(timeout: 30),
                      "the web editor never drew the document")
        evidence(app, "docs-08-audit-web")
        let web = Self.floorAndRest(try auditIssues(app, "documents-web-editor"))
        app.terminate()

        // Measured after the audit and in its own launches, as the other
        // audit case does: the pill and the bar are one `DocumentsEditorPill`
        // and one `DocumentsEditorBarButtonStyle` across all four editors, so
        // the measurement maps by view and the audited `document` pill rides
        // on the measured `markdown` one.
        let grew = documentsScalingGrew()
        XCTAssertEqual([], documentsUnexcused(web.rest, grew: grew),
                       "the web editor has attributable audit findings")
    }

    /// The web editor's chrome grows with the type size.
    ///
    /// Measured in **this** editor rather than inherited from
    /// ``documentsScalingGrew()``: that helper opens `notes.md`, and a case
    /// about this editor should fail if this editor is the one that stopped
    /// scaling.
    ///
    /// ### The page's own text is not in this measurement, and that is real
    ///
    /// A `WKWebView` scales with Dynamic Type only when the page asks it to
    /// (`font: -apple-system-body`), and the page here is the dashboard's,
    /// served to laptops as well, with its own CSS. So what grows is the
    /// host's chrome, which is what this app draws; the document's text
    /// follows the page's own sizing and the pinch-zoom the page allows.
    /// Android's host has exactly the same property. It is written down in
    /// `DECISIONS.md` as a limitation rather than measured here as a pass.
    @MainActor
    func testDocs_TheWebEditorGrowsAtAccessibilitySizes() throws {
        func height(_ mode: String, _ name: String) -> (pill: CGFloat, close: CGFloat) {
            let big = Self.documentsIsBig(mode)
            guard let app = documentsOpen(mode) else { return (0, 0) }
            guard documentsTap(app, "Letter to the council", big: big) else { return (0, 0) }
            let pill = app.staticTexts[DocumentsPillWord.document].firstMatch
            guard pill.waitForExistence(timeout: 30) else {
                print("=== NO WEB EDITOR AT \(mode) — tree follows ===")
                print(app.debugDescription)
                return (0, 0)
            }
            evidence(app, name)
            let found = (pill.frame.height, app.buttons["close"].firstMatch.frame.height)
            app.terminate()
            return found
        }

        let ordinary = height(Self.ordinaryTypeSize, "docs-09-chrome-large")
        let huge = height("UICTContentSizeCategoryAccessibilityXXXL", "docs-10-chrome-ax5")
        XCTAssertGreaterThan(ordinary.pill, 0, "the web editor never opened at the ordinary type size")
        XCTAssertGreaterThan(
            huge.pill, ordinary.pill,
            "the pill is \(huge.pill)pt at AX5 and \(ordinary.pill)pt at large — it does not grow"
        )
        XCTAssertGreaterThan(
            huge.close, ordinary.close,
            "Close is \(huge.close)pt at AX5 and \(ordinary.close)pt at large — it does not grow"
        )
    }
}
