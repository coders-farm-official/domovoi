import Foundation
import XCTest
@testable import DomovoiKit

/// The server's refusal as this client's error — file scope, because the
/// stubs are `@Sendable` and may not capture an `XCTestCase`.
private func createRefusal(_ status: Int, body: String) -> DomovoiError {
    let failure = ApiFailure(status: status, message: "\(status): \(body)", body: body)
    return status == 401 || status == 403 ? .refused(failure) : .httpError(failure)
}

/// `+ New`: the two kinds, the body, the refusals that keep the dialog open,
/// and the row the new file opens as.
@MainActor
final class DocumentsNewTests: XCTestCase {

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the model",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    // MARK: - the kinds

    /// **Three kinds, Android's three** (owner answer 21). `Document` was
    /// left out while the formatted-document editor was unported — an `.html`
    /// with nowhere to open is a `+ New` that makes rubbish — and came back
    /// with ``DocumentsWebEditorHost``. Android's fourth, Drawing, is drawn on
    /// neither side: there is no drawing editor (`NewDocument.kt:17-21`).
    func testTheKindsAreTheOnesThisAppCanOpen() {
        XCTAssertEqual([.document, .spreadsheet, .text], DocumentsNewKind.allCases)
        XCTAssertEqual("Document", DocumentsNewKind.document.label)
        XCTAssertEqual("Formatted document · .html", DocumentsNewKind.document.hint)
        XCTAssertEqual("new document", DocumentsNewKind.document.prompt)
        XCTAssertEqual("Spreadsheet", DocumentsNewKind.spreadsheet.label)
        XCTAssertEqual("Workbook · .xlsx", DocumentsNewKind.spreadsheet.hint)
        XCTAssertEqual("new spreadsheet", DocumentsNewKind.spreadsheet.prompt)
        XCTAssertEqual("e.g. Budget", DocumentsNewKind.spreadsheet.placeholder)
        XCTAssertEqual("Text file", DocumentsNewKind.text.label)
        XCTAssertEqual("Plain text · name as typed", DocumentsNewKind.text.hint)
        XCTAssertEqual("new text file", DocumentsNewKind.text.prompt)
        XCTAssertEqual("e.g. shopping.txt", DocumentsNewKind.text.placeholder)
    }

    // MARK: - the refusals, in words

    /// `createFailureMessage` (`NewDocument.kt:64`): a 409's detail is
    /// `"<name> already exists"`, and the name is pulled back out so the
    /// sentence can name the file to rename.
    func testATakenNameIsNamed() {
        XCTAssertEqual(
            "There's already a file called \"shopping list.txt\". Pick another name.",
            DocumentsNewModel.failureMessage(status: 409, detail: "shopping list.txt already exists")
        )
    }

    func testA409WithNothingToNameStillAsksForAnotherName() {
        XCTAssertEqual(
            DocumentsStrings.someNameTaken,
            DocumentsNewModel.failureMessage(status: 409, detail: "already exists")
        )
        XCTAssertEqual(
            DocumentsStrings.someNameTaken,
            DocumentsNewModel.failureMessage(status: 409, detail: nil)
        )
        XCTAssertEqual(
            DocumentsStrings.someNameTaken,
            DocumentsNewModel.failureMessage(status: 409, detail: "that file is in the way")
        )
    }

    /// Any other refusal is the server's own sentence — a reserved name, a
    /// type the Documents view would not list, a read-only library.
    func testAnyOtherDetailIsShownAsTheServerWroteIt() {
        XCTAssertEqual(
            "shopping.exe: programs aren't documents",
            DocumentsNewModel.failureMessage(status: 415, detail: " shopping.exe: programs aren't documents ")
        )
    }

    func testARefusalWithNoDetailFallsBackToWhoRefused() {
        XCTAssertEqual(
            DocumentsStrings.notAllowedToCreate,
            DocumentsNewModel.failureMessage(status: 403, detail: nil)
        )
        XCTAssertEqual(
            DocumentsStrings.notAllowedToCreate,
            DocumentsNewModel.failureMessage(status: 401, detail: "")
        )
        XCTAssertEqual(
            DocumentsStrings.couldNotCreate,
            DocumentsNewModel.failureMessage(status: 500, detail: nil)
        )
        XCTAssertEqual(
            DocumentsStrings.couldNotCreate,
            DocumentsNewModel.failureMessage(status: nil, detail: nil)
        )
    }

    // MARK: - the row the new file opens as

    /// `asEntry` (`NewDocument.kt:56`): the name comes from **`rel_path`'s
    /// last component**, because the server answers `name: target.stem` — a
    /// row built from that would be `Budget`, an extensionless file the text
    /// editor would claim.
    func testTheCreatedRowIsNamedFromItsPathAndOpensTheRightEditor() throws {
        let row = DocumentsRow(relPath: "Budget.xlsx", name: "Budget", ext: ".xlsx", size: 4821, category: "sheet")
        let entry = try XCTUnwrap(DocumentsNewModel.entry(from: row))
        XCTAssertEqual("Budget.xlsx", entry.name)
        XCTAssertEqual("Budget.xlsx", entry.rel)
        XCTAssertEqual(4821, entry.size)
        XCTAssertFalse(entry.isDir)
        // Classified as the server would (`doc-office`), and so it opens in
        // the grid editor — "opens exactly as tapping its row would".
        XCTAssertEqual("doc-office", entry.kind)
        XCTAssertEqual(.sheet, DocumentsKinds.editor(for: entry, in: FileLibrary.documents))
    }

    /// A text file named `notes.md` really is markdown: the server takes a
    /// `text` kind's name verbatim (`documents.py:772`), so this is the
    /// Android-faithful way to make one without a `Document` kind.
    func testATextKindNamedMdOpensTheMarkdownEditor() throws {
        let entry = try XCTUnwrap(DocumentsNewModel.entry(from: DocumentsRow(relPath: "notes.md", name: "notes", ext: ".md")))
        XCTAssertEqual(.markdown, DocumentsKinds.editor(for: entry, in: FileLibrary.documents))
    }

    func testARowWithNoPathIsNotARow() {
        XCTAssertNil(DocumentsNewModel.entry(from: DocumentsRow()))
    }

    // MARK: - the model

    private struct Calls: Sendable {
        let bodies = Shared<[Data]>([])
        let paths = Shared<[String]>([])
    }

    private func maker(
        calls: Calls,
        answer: @Sendable @escaping () async throws -> Data
    ) -> DocumentsNewModel {
        DocumentsNewModel(deviceID: "phone-1", send: { path, body in
            calls.paths.mutate { $0.append(path) }
            calls.bodies.mutate { $0.append(body) }
            return try await answer()
        })
    }

    func testPickingAKindAndNamingItMakesTheFileAndHandsBackItsRow() async throws {
        let calls = Calls()
        let model = maker(calls: calls) {
            Data(#"{"rel_path":"Budget.xlsx","name":"Budget","ext":".xlsx","size":4821,"category":"sheet"}"#.utf8)
        }
        XCTAssertFalse(model.canCreate)
        model.pick(.spreadsheet)
        XCTAssertEqual(.spreadsheet, model.kind)
        XCTAssertFalse(model.canCreate)

        model.name = "  Budget  "
        XCTAssertTrue(model.canCreate)
        model.create()
        XCTAssertTrue(model.creating)
        await settleUntil({ !model.creating }, "the create")

        XCTAssertEqual(["/api/documents/create"], calls.paths.current)
        let body = try XCTUnwrap(
            JSONSerialization.jsonObject(with: try XCTUnwrap(calls.bodies.current.first)) as? [String: Any]
        )
        XCTAssertEqual("Budget", body["name"] as? String)
        XCTAssertEqual("sheet", body["kind"] as? String)
        XCTAssertEqual("phone-1", body["device_id"] as? String)

        // The dialog is gone and there is a row to open.
        XCTAssertNil(model.kind)
        XCTAssertNil(model.failure)
        XCTAssertEqual("Budget.xlsx", model.created?.rel)
        model.opened()
        XCTAssertNil(model.created)
    }

    /// `dismissOnConfirm = false` (`FilesScreen.kt:857`): a refusal leaves
    /// the dialog up **with the name still in it**, so it can be fixed.
    func testATakenNameLeavesTheDialogOpenWithTheNameInIt() async throws {
        let calls = Calls()
        let model = maker(calls: calls) {
            throw createRefusal(409, body: #"{"detail":"shopping list.txt already exists"}"#)
        }
        model.pick(.text)
        model.name = "shopping list.txt"
        model.create()
        await settleUntil({ !model.creating }, "the refusal")

        XCTAssertEqual(
            "There's already a file called \"shopping list.txt\". Pick another name.",
            model.failure
        )
        XCTAssertEqual(.text, model.kind)
        XCTAssertEqual("shopping list.txt", model.name)
        XCTAssertNil(model.created)
        XCTAssertTrue(model.canCreate)
    }

    func testABlockedDevicesRefusalIsSaidAndNothingIsOpened() async throws {
        let calls = Calls()
        let model = maker(calls: calls) {
            throw createRefusal(403, body: #"{"detail":"this device is blocked from changing files"}"#)
        }
        model.pick(.text)
        model.name = "shopping.txt"
        model.create()
        await settleUntil({ !model.creating }, "the refusal")
        XCTAssertEqual("this device is blocked from changing files", model.failure)
        XCTAssertNil(model.created)
    }

    /// A 200 that is not a `DocumentRow` is not an opened file: the dialog
    /// says so rather than opening an editor on a path it does not have.
    func testA200WithNoPathDoesNotOpenAnything() async throws {
        let calls = Calls()
        let model = maker(calls: calls) { Data("<html>a portal</html>".utf8) }
        model.pick(.text)
        model.name = "shopping.txt"
        model.create()
        await settleUntil({ !model.creating }, "the answer")
        XCTAssertEqual(DocumentsStrings.couldNotCreate, model.failure)
        XCTAssertNil(model.created)
        XCTAssertEqual(.text, model.kind)
    }

    func testABlankNameIsNotSent() async throws {
        let calls = Calls()
        let model = maker(calls: calls) { Data(#"{"rel_path":"x.txt"}"#.utf8) }
        model.pick(.text)
        model.name = "   "
        XCTAssertFalse(model.canCreate)
        model.create()
        for _ in 0..<3000 { await Task.yield() }
        XCTAssertTrue(calls.paths.current.isEmpty)
    }

    func testCancellingForgetsTheNameAndTheRefusal() async throws {
        let calls = Calls()
        let model = maker(calls: calls) { throw createRefusal(409, body: #"{"detail":"x.txt already exists"}"#) }
        model.pick(.text)
        model.name = "x.txt"
        model.create()
        await settleUntil({ model.failure != nil }, "the refusal")
        model.cancel()
        XCTAssertNil(model.kind)
        XCTAssertNil(model.failure)
        XCTAssertEqual("", model.name)
    }

    /// The new file goes to the **top** of Documents wherever the browser is
    /// looking, which is said out loud before create is tapped.
    func testTheSubfolderNoteIsOnlyShownInASubfolder() {
        let model = DocumentsNewModel(send: { _, _ in Data() })
        XCTAssertEqual(DocumentsStrings.goesAtTheTop, model.note(inSubfolder: true))
        XCTAssertNil(model.note(inSubfolder: false))
    }
}
