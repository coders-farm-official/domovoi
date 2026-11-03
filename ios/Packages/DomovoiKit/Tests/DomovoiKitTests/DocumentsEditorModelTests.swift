import Foundation
import XCTest
@testable import DomovoiKit

/// The server's refusal as this client's error — at file scope, not a method:
/// the stubs below are `@Sendable` and may not capture an `XCTestCase`.
private func refusing(_ status: Int, body: String) -> DomovoiError {
    let failure = ApiFailure(status: status, message: "\(status): \(body)", body: body)
    return status == 401 || status == 403 ? .refused(failure) : .httpError(failure)
}

/// The text and markdown editor's state machine, with a closure in place of a
/// server: every decision here is about what came back, which route it was
/// asked of, and what was in flight when it landed.
@MainActor
final class DocumentsEditorModelTests: XCTestCase {

    // MARK: - rig

    private func entry(_ name: String, rel: String? = nil) -> FileEntry {
        FileEntry(name: name, rel: rel ?? name, isDir: false, size: 64, mtime: 1_762_000_000, kind: "doc-text")
    }

    /// Every call the model made, as `"METHOD path"`, plus the bodies.
    private struct Calls: Sendable {
        let lines = Shared<[String]>([])
        let bodies = Shared<[Data]>([])
    }

    private func editor(
        _ file: FileEntry,
        kind: DocumentsEditorKind = .text,
        canWrite: Bool = true,
        deviceID: String? = "phone-1",
        calls: Calls,
        answer: @Sendable @escaping (String, String) async throws -> Data
    ) -> DocumentsEditorModel {
        DocumentsEditorModel(entry: file, kind: kind, canWrite: canWrite, deviceID: deviceID, send: { method, path, body in
            calls.lines.mutate { $0.append("\(method) \(path)") }
            if let body { calls.bodies.mutate { $0.append(body) } }
            return try await answer(method, path)
        })
    }

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

    private func json(_ data: Data) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    // MARK: - loading

    func testATextFileIsReadFromTheTextRoute() async throws {
        let calls = Calls()
        let model = editor(entry("shopping list.txt"), calls: calls) { _, _ in
            Data(#"{"rel_path":"shopping list.txt","text":"kettle\nbread","newline":"\n","size":12}"#.utf8)
        }
        model.read()
        await settleUntil({ model.load == .ready }, "the text")
        XCTAssertEqual(["GET /api/documents/text/shopping%20list.txt"], calls.lines.current)
        XCTAssertEqual("kettle\nbread", model.text)
        XCTAssertFalse(model.dirty)
        XCTAssertEqual("text", model.pill)
    }

    /// The defect the whole route choice is about: a `.md` is read from
    /// **`/doc`**, which is the route whose save the server guards with the
    /// blanking refusal — and which answers under `markdown`, not `text`.
    func testAMarkdownFileIsReadFromTheDocumentRoute() async throws {
        let calls = Calls()
        let model = editor(entry("notes.md"), kind: .markdown, calls: calls) { _, _ in
            Data(##"{"rel_path":"notes.md","name":"notes","ext":".md","format":"md","markdown":"# Notes"}"##.utf8)
        }
        model.read()
        await settleUntil({ model.load == .ready }, "the markdown")
        XCTAssertEqual(["GET /api/documents/doc/notes.md"], calls.lines.current)
        XCTAssertEqual("# Notes", model.text)
        XCTAssertEqual("markdown", model.pill)
    }

    /// `/doc` answers `{markdown}` and `/text` answers `{text}`: a model that
    /// read the other key would show an empty document and then be able to
    /// save the emptiness.
    func testTheWrongKeyIsNotTakenForAnEmptyDocument() async throws {
        let calls = Calls()
        let model = editor(entry("notes.md"), kind: .markdown, calls: calls) { _, _ in
            Data(#"{"text":"this is the TEXT route's wording"}"#.utf8)
        }
        model.read()
        await settleUntil({ model.load != .loading }, "the answer")
        XCTAssertEqual(.failed(DocumentsStrings.couldNotLoad("that isn't Domovoi")), model.load)
        XCTAssertEqual("", model.text)
    }

    func testA200ThatIsNotThisRouteIsNotAnEmptyFile() async throws {
        let calls = Calls()
        let model = editor(entry("a.txt"), calls: calls) { _, _ in Data("<html>a portal</html>".utf8) }
        model.read()
        await settleUntil({ model.load != .loading }, "the answer")
        guard case .failed = model.load else { return XCTFail("a portal's page read as a file") }
    }

    /// The 415 from `GET /text` is an **answer**: `{"editable": false,
    /// "reason": "too_large"|"binary"}` — the two sentences Android draws,
    /// with the file's own path in them.
    func testThe415ReasonsBecomeAndroidsTwoSentences() async throws {
        for (reason, tail) in [("too_large", "it's too large."), ("binary", "it looks binary.")] {
            let calls = Calls()
            let said = #"{"editable":false,"reason":"\#(reason)"}"#
            let model = editor(entry("backup.dat"), calls: calls) { _, _ in
                throw refusing(415, body: said)
            }
            model.read()
            await settleUntil({ model.load != .loading }, "the refusal")
            XCTAssertEqual(.notEditable("Can't preview backup.dat as text — \(tail)"), model.load)
            // Not a failure: there is nothing to retry and nothing broke.
            XCTAssertFalse(model.offersSave)
        }
    }

    /// A 415 whose body this app cannot read still means "not as text".
    func testA415WithNoReasonStillSaysNotAsText() async throws {
        let calls = Calls()
        let model = editor(entry("backup.dat"), calls: calls) { _, _ in throw refusing(415, body: "") }
        model.read()
        await settleUntil({ model.load != .loading }, "the refusal")
        XCTAssertEqual(.notEditable("Can't preview backup.dat as text — it looks binary."), model.load)
    }

    /// `GET /doc`'s 415 is a plain `detail` string ("isn't opened by the
    /// document editor"), shown as the server wrote it.
    func testADocRoute415ShowsTheServersOwnSentence() async throws {
        let calls = Calls()
        let model = editor(entry("page.html"), kind: .markdown, calls: calls) { _, _ in
            throw refusing(415, body: #"{"detail":".html isn't opened by the document editor"}"#)
        }
        model.read()
        await settleUntil({ model.load != .loading }, "the refusal")
        XCTAssertEqual(.notEditable(".html isn't opened by the document editor"), model.load)
    }

    /// 413: over `_DOC_MAX_BYTES` (32 MiB). Android has no sentence for this
    /// — its editor is the dashboard's page.
    func testAFileTooBigForTheDocumentEditorSaysSo() async throws {
        let calls = Calls()
        let model = editor(entry("book.md"), kind: .markdown, calls: calls) { _, _ in
            throw refusing(413, body: #"{"detail":"book.md is larger than the 32 MB the editor opens"}"#)
        }
        model.read()
        await settleUntil({ model.load != .loading }, "the refusal")
        XCTAssertEqual(.notEditable(DocumentsStrings.tooBigToEdit), model.load)
    }

    func testAnUnreachableServerIsAFailureAndNotARefusal() async throws {
        let calls = Calls()
        let model = editor(entry("a.txt"), calls: calls) { _, _ in throw DomovoiError.unreachable("no route to host") }
        model.read()
        await settleUntil({ model.load != .loading }, "the failure")
        XCTAssertEqual(.failed("Couldn't load: no route to host"), model.load)
    }

    // MARK: - dirty, and the save that waits for it

    func testSaveWaitsForAChangeAndSendsTheBufferOnce() async throws {
        let calls = Calls()
        let model = editor(entry("shopping list.txt"), calls: calls) { method, _ in
            method == "GET"
                ? Data(#"{"text":"kettle"}"#.utf8)
                : Data(#"{"rel_path":"shopping list.txt","size":13}"#.utf8)
        }
        model.read()
        await settleUntil({ model.load == .ready }, "the text")

        // Nothing typed: no change, so no save — and `save()` is a no-op
        // rather than a request the server would answer 200 to.
        XCTAssertFalse(model.canSave)
        model.save()
        XCTAssertEqual(1, calls.lines.current.count)

        model.text = "kettle\nbread"
        XCTAssertTrue(model.dirty)
        XCTAssertTrue(model.canSave)
        model.save()
        XCTAssertTrue(model.saving)
        await settleUntil({ !model.saving }, "the save")

        XCTAssertEqual(
            ["GET /api/documents/text/shopping%20list.txt", "PUT /api/documents/text/shopping%20list.txt"],
            calls.lines.current
        )
        let body = try json(XCTUnwrap(calls.bodies.current.first))
        XCTAssertEqual("kettle\nbread", body["text"] as? String)
        XCTAssertEqual("phone-1", body["device_id"] as? String)
        // Saved: not dirty any more, and the shell has one line to draw.
        XCTAssertFalse(model.dirty)
        XCTAssertEqual("saved", model.toast)
        XCTAssertNil(model.saveFailure)
    }

    func testAMarkdownSaveGoesToTheDocumentRouteAsMarkdown() async throws {
        let calls = Calls()
        let model = editor(entry("notes.md"), kind: .markdown, calls: calls) { method, _ in
            method == "GET" ? Data(##"{"markdown":"# Notes"}"##.utf8) : Data(#"{"rel_path":"notes.md"}"#.utf8)
        }
        model.read()
        await settleUntil({ model.load == .ready }, "the markdown")
        model.text = "# Notes\n\n- one"
        model.save()
        await settleUntil({ !model.saving }, "the save")
        XCTAssertEqual("PUT /api/documents/doc/notes.md", calls.lines.current.last)
        let body = try json(XCTUnwrap(calls.bodies.current.first))
        XCTAssertEqual("# Notes\n\n- one", body["markdown"] as? String)
        XCTAssertNil(body["text"])
    }

    /// The 409 the blanking guard raises, whose `detail` is an **object**:
    /// the message is shown, not `409: {…}`, and the editor stays dirty so
    /// the person can type something and save again.
    func testABlankSaveIsRefusedInTheServersWords() async throws {
        let calls = Calls()
        let detail = #"{"detail":{"reason":"empty_over_content","message":"Not saved: this would have replaced the document with an empty one."}}"#
        let model = editor(entry("notes.md"), kind: .markdown, calls: calls) { method, _ in
            if method == "GET" { return Data(##"{"markdown":"# Notes"}"##.utf8) }
            throw refusing(409, body: detail)
        }
        model.read()
        await settleUntil({ model.load == .ready }, "the markdown")
        model.text = ""
        model.save()
        await settleUntil({ !model.saving }, "the refusal")
        XCTAssertEqual(
            "Not saved: this would have replaced the document with an empty one.",
            model.saveFailure
        )
        XCTAssertEqual(
            "save failed: Not saved: this would have replaced the document with an empty one.",
            model.toast
        )
        // The edit is still in hand — a refused save must not look like one
        // that landed.
        XCTAssertTrue(model.dirty)
        XCTAssertTrue(model.canSave)
    }

    func testASaveRefusedWithAPlainDetailShowsIt() async throws {
        let calls = Calls()
        let model = editor(entry("a.txt"), calls: calls) { method, _ in
            if method == "GET" { return Data(#"{"text":"x"}"#.utf8) }
            throw refusing(403, body: #"{"detail":"this device is blocked from changing files"}"#)
        }
        model.read()
        await settleUntil({ model.load == .ready }, "the text")
        model.text = "y"
        model.save()
        await settleUntil({ !model.saving }, "the refusal")
        XCTAssertEqual("this device is blocked from changing files", model.saveFailure)
        XCTAssertTrue(model.dirty)
    }

    /// A phone an admin has blocked, or a read-only library: no Save is
    /// drawn, and `save()` cannot be reached from anywhere else either
    /// ([[no-misleading-controls]]).
    func testAPhoneThatMayNotWriteHasNoSaveAtAll() async throws {
        let calls = Calls()
        let model = editor(entry("a.txt"), canWrite: false, calls: calls) { _, _ in
            Data(#"{"text":"x"}"#.utf8)
        }
        model.read()
        await settleUntil({ model.load == .ready }, "the text")
        model.text = "typed anyway"
        XCTAssertFalse(model.offersSave)
        XCTAssertFalse(model.canSave)
        model.save()
        for _ in 0..<3000 { await Task.yield() }
        XCTAssertEqual(["GET /api/documents/text/a.txt"], calls.lines.current)
    }

    /// A phone with no device id to send still saves: the field is optional
    /// on these routes (unlike a move, which the server requires it for), so
    /// it is left out rather than the save refused.
    func testAPhoneWithNoDeviceIdStillSaves() async throws {
        let calls = Calls()
        let model = editor(entry("a.txt"), deviceID: nil, calls: calls) { method, _ in
            method == "GET" ? Data(#"{"text":"x"}"#.utf8) : Data(#"{"rel_path":"a.txt"}"#.utf8)
        }
        model.read()
        await settleUntil({ model.load == .ready }, "the text")
        model.text = "y"
        model.save()
        await settleUntil({ !model.saving }, "the save")
        XCTAssertEqual("saved", model.toast)
        XCTAssertNil(try json(XCTUnwrap(calls.bodies.current.first))["device_id"])
    }

    // MARK: - the file switching under an answer in flight

    /// [[read-after-await-wrong-key]], the top defect class on this track:
    /// the model is pointed at another file **inside** the first file's round
    /// trip, and the first file's text must not land under the second's name.
    ///
    /// The switch happens while the GET is suspended — not before it starts —
    /// which is the only arrangement that tests the guard rather than the
    /// ordering of two calls.
    func testTextForTheFileThatWasOpenDoesNotLandUnderTheNewOne() async throws {
        let calls = Calls()
        let letTheFirstAnswerLand = Shared<Bool>(false)
        let model = editor(entry("a.txt"), calls: calls) { _, path in
            if path.hasSuffix("a.txt") {
                while !letTheFirstAnswerLand.current { await Task.yield() }
                return Data(#"{"text":"A's shopping list"}"#.utf8)
            }
            return Data(#"{"text":"B's shopping list"}"#.utf8)
        }

        model.read()
        await settleUntil({ calls.lines.current.contains("GET /api/documents/text/a.txt") }, "a.txt's read")

        // Inside the round trip: a's GET is suspended, and the editor moves.
        model.open(entry("b.txt"), kind: .text)
        letTheFirstAnswerLand.set(true)

        await settleUntil({ model.load == .ready }, "b.txt's read")
        XCTAssertEqual("B's shopping list", model.text)
        XCTAssertEqual("b.txt", model.entry.rel)
        XCTAssertFalse(model.dirty)
        // And a's answer, which arrived second, changed nothing.
        for _ in 0..<3000 { await Task.yield() }
        XCTAssertEqual("B's shopping list", model.text)
    }

    /// The same guard on the way out: a save that lands after the editor
    /// moved on must not mark the NEW file saved — its buffer was never sent.
    func testASaveThatLandsAfterTheFileChangedMarksNothingSaved() async throws {
        let calls = Calls()
        let letThePutLand = Shared<Bool>(false)
        let model = editor(entry("a.txt"), calls: calls) { method, _ in
            if method == "PUT" {
                while !letThePutLand.current { await Task.yield() }
                return Data(#"{"rel_path":"a.txt"}"#.utf8)
            }
            return Data(#"{"text":"A"}"#.utf8)
        }
        model.read()
        await settleUntil({ model.load == .ready }, "a.txt")
        model.text = "A, edited"
        model.save()
        await settleUntil({ calls.lines.current.contains(where: { $0.hasPrefix("PUT") }) }, "the save")

        model.open(entry("b.txt"), kind: .text)
        letThePutLand.set(true)
        await settleUntil({ model.load == .ready && model.text == "A" }, "b.txt")

        // b.txt's own text, no "saved" toast from a's save, and nothing
        // claiming b was written.
        XCTAssertEqual("b.txt", model.entry.rel)
        XCTAssertNil(model.toast)
        XCTAssertFalse(model.saving)
    }
}
