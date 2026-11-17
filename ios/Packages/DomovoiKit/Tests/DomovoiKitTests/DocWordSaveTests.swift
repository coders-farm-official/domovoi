import Foundation
import UniformTypeIdentifiers
import XCTest
@testable import DomovoiKit

/// A row, built outside the test class: the fetch closures below are
/// `@Sendable` and a method on an `XCTestCase` is not something they may
/// capture.
private func docRow(_ rel: String, isDir: Bool = false) -> FileEntry {
    let name = rel.split(separator: "/").last.map(String.init) ?? rel
    return FileEntry(name: name, rel: rel, isDir: isDir, kind: FileKinds.kind(of: name, isDir: isDir))
}

/// 2026-11-17: saving a Domovoi document (a formatted `.html` in the
/// Documents library, the editor's own format) handed the person the raw web
/// page. It now saves the server's Word copy
/// (`GET /api/documents/export/doc/{rel_path:path}?fmt=docx`, `documents.py`
/// `export_doc`) as `<stem>.docx`; every other row saves as before.
/// Android's `DocWordDownloadTest`, case for case.
@MainActor
final class DocWordSaveTests: XCTestCase {

    private let exportPath = "/api/documents/export/doc/Letters/Notes%20for%20Gran/To%20Gran.html?fmt=docx"

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    // MARK: - which rows convert

    func testOnlyDocumentsHtmlConverts() {
        let docs = FileLibrary.documents
        XCTAssertTrue(FileSaveModel.savesAsWord(docRow("Letter.html"), from: docs))
        XCTAssertTrue(FileSaveModel.savesAsWord(docRow("Letters/Old.htm"), from: docs))
        XCTAssertTrue(FileSaveModel.savesAsWord(docRow("SHOUTY.HTML"), from: docs), "case-insensitive")
        XCTAssertTrue(FileSaveModel.savesAsWord(docRow("Mixed.HtM"), from: docs))
        // Other kinds in Documents keep their own file.
        for rel in ["notes.md", "list.txt", "scan.pdf", "letter.docx", "page.xhtml", "html", "a.html.bak"] {
            XCTAssertFalse(FileSaveModel.savesAsWord(docRow(rel), from: docs), rel)
        }
        // The same name in any other library is somebody's web page: as is.
        XCTAssertFalse(FileSaveModel.savesAsWord(docRow("Letter.html"), from: FileLibrary.other))
        XCTAssertFalse(FileSaveModel.savesAsWord(docRow("Letter.html"), from: "core:music"))
        XCTAssertFalse(FileSaveModel.savesAsWord(docRow("Letter.html"), from: "plugin:notes"))
        // A folder, even one named like a document, is the server's zip.
        XCTAssertFalse(FileSaveModel.savesAsWord(docRow("x.html", isDir: true), from: docs))
    }

    // MARK: - the address

    func testTheExportPathEncodesEachSegmentLikeTheServerRoute() throws {
        let rel = "Letters/Notes for Gran/To Gran.html"
        let path = DocumentsPath.wordExport(rel: rel)
        XCTAssertEqual(exportPath, path)
        // The server's {rel_path:path} decodes back to the row's rel exactly.
        let parts = try XCTUnwrap(URLComponents(string: "http://192.168.1.20:6369" + path))
        XCTAssertEqual("/api/documents/export/doc/" + rel, parts.path)
        XCTAssertEqual("fmt=docx", parts.percentEncodedQuery)

        // Characters that would end or split the path are escaped; "/" never is.
        let odd = "Q&A #2?/tea + cake.htm"
        let oddPath = DocumentsPath.wordExport(rel: odd)
        XCTAssertEqual("/api/documents/export/doc/Q%26A%20%232%3F/tea%20%2B%20cake.htm?fmt=docx", oddPath)
        let oddParts = try XCTUnwrap(URLComponents(string: "http://192.168.1.20:6369" + oddPath))
        XCTAssertEqual("/api/documents/export/doc/" + odd, oddParts.path)
        XCTAssertEqual("fmt=docx", oddParts.percentEncodedQuery)
    }

    // MARK: - the name and the type

    func testSavedAsStemDotDocxWithWordsType() {
        let plan = FileSaveModel.plan(for: docRow("Letters/Notes for Gran/To Gran.html"), from: FileLibrary.documents)
        XCTAssertEqual(FileSaveModel.Plan(path: exportPath, name: "To Gran.docx"), plan)
        // Only the last extension goes, as the server's Path.stem does.
        XCTAssertEqual("v1.2 plan.docx", FileSaveModel.wordName(for: docRow("v1.2 plan.HTM")))
        // Hardened as every saved name is.
        XCTAssertEqual("notes_2026.docx", FileSaveModel.wordName(for: docRow("notes:2026.html")))
        XCTAssertEqual("document.docx", FileSaveModel.wordName(for: docRow("Letters/.html")))
        // The share sheet types the file by its extension: .docx is Word's.
        XCTAssertEqual(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            DocumentsPath.wordMIME
        )
        XCTAssertEqual(DocumentsPath.wordMIME, UTType(filenameExtension: "docx")?.preferredMIMEType)
    }

    func testASaveOfADocumentFetchesWordAndHandsBackTheDocx() async throws {
        let asked = Shared<[String]>([])
        let model = FileSaveModel(fetch: { path in
            asked.mutate { $0.append(path) }
            return Data("PK word bytes".utf8)
        })

        model.save(docRow("Letters/Notes for Gran/To Gran.html"), from: FileLibrary.documents)
        XCTAssertEqual(.fetching("To Gran.docx", nil), model.state)
        await settleUntil({
            if case .ready = model.state { return true }
            return false
        }, "the document")

        XCTAssertEqual([exportPath], asked.current)
        guard case .ready(let url, let name) = model.state else { return XCTFail("no file") }
        XCTAssertEqual("To Gran.docx", name)
        XCTAssertEqual("To Gran.docx", url.lastPathComponent)
        model.taken()
    }

    // MARK: - everything else

    func testEverythingElseSavesAsBefore() {
        XCTAssertEqual(
            FileSaveModel.Plan(path: "/api/files/download?library_id=core%3Adocuments&path=Notes%2Ftodo.md", name: "todo.md"),
            FileSaveModel.plan(for: docRow("Notes/todo.md"), from: FileLibrary.documents)
        )
        XCTAssertEqual(
            FileSaveModel.Plan(path: "/api/files/download?library_id=core%3Adocuments&path=scan.pdf", name: "scan.pdf"),
            FileSaveModel.plan(for: docRow("scan.pdf"), from: FileLibrary.documents)
        )
        XCTAssertEqual(
            FileSaveModel.Plan(path: "/api/files/download?library_id=core%3Aother&path=saved%2Fpage.html", name: "page.html"),
            FileSaveModel.plan(for: docRow("saved/page.html"), from: FileLibrary.other)
        )
        let folder = FileSaveModel.plan(for: docRow("Letters", isDir: true), from: FileLibrary.documents)
        XCTAssertEqual("/api/files/download?library_id=core%3Adocuments&path=Letters", folder.path)
    }

    // MARK: - a refused conversion

    /// The server's sentence is shown, and the raw page is **not** fetched in
    /// its place: one request, to the export route, and a failed save.
    func testAnExportErrorShowsTheServersDetailAndNothingElseIsFetched() async {
        let asked = Shared<[String]>([])
        let body = #"{"detail":"this document could not be converted"}"#
        let model = FileSaveModel(fetch: { path in
            asked.mutate { $0.append(path) }
            throw DomovoiError.httpError(ApiFailure(status: 422, message: "422: " + body, body: body))
        })

        model.save(docRow("Letters/Notes for Gran/To Gran.html"), from: FileLibrary.documents)
        await settleUntil({
            if case .failed = model.state { return true }
            return false
        }, "the refusal")

        XCTAssertEqual(.failed("couldn't save: this document could not be converted"), model.state)
        XCTAssertEqual([exportPath], asked.current, "no fallback to the raw .html")
    }

    /// No sentence in the answer: the usual failed-save text.
    func testAnExportErrorWithoutADetailSaysWhatItUsuallySays() {
        let page = "<html>bad gateway</html>"
        XCTAssertEqual(
            "couldn't save: 502: " + page,
            SaveToDeviceModel.failure(DomovoiError.serverTrouble(ApiFailure(status: 502, message: "502: " + page, body: page)))
        )
        XCTAssertEqual(
            "couldn't save: the house didn't answer",
            SaveToDeviceModel.failure(DomovoiError.unreachable("the house didn't answer"))
        )
    }
}
