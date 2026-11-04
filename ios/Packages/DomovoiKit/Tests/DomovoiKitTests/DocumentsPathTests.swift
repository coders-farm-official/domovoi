import Foundation
import XCTest
@testable import DomovoiKit

/// The routes and the bodies, character by character.
///
/// `rel_path` is the **tail of the URL** on all three editor routes, so the
/// escaping is the one in ``FilePath/escPath(_:)`` and not the query one: a
/// `#` left raw ends the path and turns the rest of a filename into a
/// fragment, which is a save landing on a different file than the one on
/// screen.
final class DocumentsPathTests: XCTestCase {

    func testTheRoutesEscapeAPathTail() {
        XCTAssertEqual(
            "/api/documents/text/shopping%20list.txt",
            DocumentsPath.text(rel: "shopping list.txt")
        )
        XCTAssertEqual("/api/documents/doc/notes.md", DocumentsPath.doc(rel: "notes.md"))
        XCTAssertEqual(
            "/api/documents/sheet/a%20spreadsheet.xlsx",
            DocumentsPath.sheet(rel: "a spreadsheet.xlsx")
        )
    }

    /// A folder separator **survives** — it is a path tail — and everything
    /// that would end the path or mean something else does not.
    func testSeparatorsSurviveAndTheRestIsEscaped() {
        XCTAssertEqual(
            "/api/documents/text/Letters/to%20the%20bank%20%232.txt",
            DocumentsPath.text(rel: "Letters/to the bank #2.txt")
        )
        XCTAssertEqual(
            "/api/documents/doc/a%2Bb%20%25%20c.md",
            DocumentsPath.doc(rel: "a+b % c.md")
        )
        XCTAssertEqual(
            "/api/documents/sheet/q%3F1.csv",
            DocumentsPath.sheet(rel: "q?1.csv")
        )
        // A Windows house's names hold backslashes, which are not separators
        // in a URL and must not be read as one.
        XCTAssertEqual(
            "/api/documents/text/a%5Cb.txt",
            DocumentsPath.text(rel: "a\\b.txt")
        )
    }

    /// The text reader and the editor must ask for the SAME url — two copies
    /// of the escaping rule is how two screens come to disagree about a
    /// filename with a plus in it.
    func testTheEditorAndTheReaderAskForTheSameUrl() {
        XCTAssertEqual(
            FilePath.documentText(rel: "Letters/to the bank #2.txt"),
            DocumentsPath.text(rel: "Letters/to the bank #2.txt")
        )
    }

    // MARK: - the bodies

    private func json(_ data: Data) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    func testATextBodyCarriesTheTextVerbatim() throws {
        let text = "line one\r\n\"quoted\"\tand a tab\\ and a backslash"
        let body = try json(DocumentsPath.textBody(text: text, deviceID: "phone-1"))
        XCTAssertEqual(text, body["text"] as? String)
        XCTAssertEqual("phone-1", body["device_id"] as? String)
    }

    /// The field the server types `min_length=3`: a shorter id, or none, is
    /// **left out** rather than sent — a 422 over a field that only decides
    /// whether a per-device block applies would fail the save itself.
    func testAShortOrMissingDeviceIdIsLeftOut() throws {
        XCTAssertNil(try json(DocumentsPath.textBody(text: "x"))["device_id"])
        XCTAssertNil(try json(DocumentsPath.textBody(text: "x", deviceID: "ab"))["device_id"])
        let tooLong = try json(DocumentsPath.textBody(text: "x", deviceID: String(repeating: "a", count: 65)))
        XCTAssertNil(tooLong["device_id"])
        XCTAssertEqual("abc", try json(DocumentsPath.textBody(text: "x", deviceID: "abc"))["device_id"] as? String)
    }

    /// `{markdown: …}`, and **no `allow_empty`**: that flag is the dashboard
    /// editor saying an emptiness is real, which this app cannot know.
    func testADocBodyIsMarkdownAndNeverAllowsEmpty() throws {
        let body = try json(DocumentsPath.docBody(markdown: "# Title\n\n- one\n", deviceID: "phone-1"))
        XCTAssertEqual("# Title\n\n- one\n", body["markdown"] as? String)
        XCTAssertNil(body["allow_empty"])
        XCTAssertNil(body["html"])
    }

    /// The whole padded grid, `f` for a formula and `v` for everything else
    /// — including the empty string, which the server reads as a blank cell
    /// that keeps its formatting (`_grid_model`).
    func testASheetBodyCarriesTheWholeGridWithFormulasAsFormulas() throws {
        let body = DocumentsPath.sheetBody(rows: [["Item", "Cost"], ["kettle", "=SUM(B1:B2)"], ["", ""]])
        let object = try json(body)
        let rows = try XCTUnwrap(object["rows"] as? [[[String: Any]]])
        XCTAssertEqual(3, rows.count)
        XCTAssertEqual("Item", rows[0][0]["v"] as? String)
        XCTAssertNil(rows[0][0]["f"])
        XCTAssertEqual("=SUM(B1:B2)", rows[1][1]["f"] as? String)
        XCTAssertNil(rows[1][1]["v"])
        // The blank cells are sent, not dropped: the server diffs the grid it
        // is given, so a row left short would blank what it does not mention.
        XCTAssertEqual("", rows[2][0]["v"] as? String)
        XCTAssertEqual(2, rows[2].count)
    }

    /// `=` alone is not a formula anybody wrote, but it is what the server
    /// decides about, not this app: Android sends it as `f` too
    /// (`FileSource.kt:271` tests only `startsWith("=")`), so the port does.
    func testALoneEqualsSignGoesAsAFormulaAsItDoesOnAndroid() throws {
        let rows = try XCTUnwrap(try json(DocumentsPath.sheetBody(rows: [["="]]))["rows"] as? [[[String: Any]]])
        XCTAssertEqual("=", rows[0][0]["f"] as? String)
    }

    func testAnEmptyGridIsAnEmptyRowsArray() throws {
        let object = try json(DocumentsPath.sheetBody(rows: []))
        XCTAssertEqual(0, (object["rows"] as? [Any])?.count)
    }

    func testACreateBodyTrimsTheNameAndNamesTheKind() throws {
        let sheet = try json(DocumentsPath.createBody(name: "  Budget  ", kind: .spreadsheet, deviceID: "phone-1"))
        XCTAssertEqual("Budget", sheet["name"] as? String)
        XCTAssertEqual("sheet", sheet["kind"] as? String)
        XCTAssertEqual("phone-1", sheet["device_id"] as? String)

        let text = try json(DocumentsPath.createBody(name: "shopping.txt", kind: .text))
        XCTAssertEqual("text", text["kind"] as? String)
        XCTAssertEqual("shopping.txt", text["name"] as? String)
    }

    /// The two kinds this app offers, and the two `kind` values the server
    /// takes for them (`CreateRequest:616`).
    func testTheKindsAreTheServers() {
        XCTAssertEqual(["doc", "sheet", "text"], DocumentsNewKind.allCases.map(\.api))
    }
}
