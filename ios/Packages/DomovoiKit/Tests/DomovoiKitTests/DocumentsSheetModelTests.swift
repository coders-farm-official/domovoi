import Foundation
import XCTest
@testable import DomovoiKit

/// The server's refusal as this client's error — file scope, because the
/// stubs are `@Sendable` and may not capture an `XCTestCase`.
private func sheetRefusal(_ status: Int, body: String) -> DomovoiError {
    .httpError(ApiFailure(status: status, message: "\(status): \(body)", body: body))
}

/// Three rows, ragged as the server sends them, with one cell carrying
/// **both** a formula and a cached value — the case the display rule is
/// about. At file scope, not a static on the test: the stubs below are
/// `@Sendable` and may not read a `@MainActor` type's storage.
private let sheetGrid = """
{"rel_path":"a spreadsheet.xlsx",
 "rows":[[{"v":"Item","f":null},{"v":"Cost","f":null}],
         [{"v":"kettle","f":null},{"v":"19","f":null}],
         [null,{"v":"42","f":"=SUM(B1:B2)"}]],
 "max_rows":1000,"max_cols":60}
"""

/// The grid: the wrapper it arrives in, the padding, the display rule that
/// decides between a formula and its value, and the body a save sends.
@MainActor
final class DocumentsSheetModelTests: XCTestCase {

    private func entry(_ name: String) -> FileEntry {
        FileEntry(name: name, rel: name, isDir: false, size: 4096, mtime: 1_762_000_000, kind: "doc-office")
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

    // MARK: - the wrapper and the grid

    func testTheGridDecodesWithItsWindow() throws {
        let read = try JSONDecoder().decode(DocumentsSheetGrid.self, from: Data(sheetGrid.utf8))
        XCTAssertEqual("a spreadsheet.xlsx", read.relPath)
        XCTAssertEqual(3, read.rows.count)
        XCTAssertEqual(1000, read.maxRows)
        XCTAssertEqual(60, read.maxCols)
        // A null cell is a blank cell, not a decode failure — the server
        // sends them inside a row (`rows[r][c]` is `Optional[SheetCell]`).
        XCTAssertNil(read.rows[2][0])
    }

    /// `display()` the right way round (`SheetModel.kt:35`): a cell with a
    /// formula shows **the formula**. Backwards, every formula in a
    /// household's sheet would become its own last value the next time
    /// anybody saved.
    func testAFormulaCellShowsTheFormulaAndNotItsValue() throws {
        let read = try JSONDecoder().decode(DocumentsSheetGrid.self, from: Data(sheetGrid.utf8))
        XCTAssertEqual("=SUM(B1:B2)", read.rows[2][1]?.display)
        XCTAssertEqual("kettle", read.rows[1][0]?.display)
        XCTAssertEqual("", DocumentsSheetCell().display)
    }

    /// `max(8, rows) × max(4, widest)` (`SheetEditor.kt:80-86`): the padding
    /// is what gives a one-cell `.csv` somewhere to type, and the rectangle
    /// is what lets a save send a grid the server's diff can read.
    func testTheGridIsPaddedToAtLeastEightByFour() throws {
        let read = try JSONDecoder().decode(DocumentsSheetGrid.self, from: Data(sheetGrid.utf8))
        let padded = read.padded
        XCTAssertEqual(8, padded.count)
        XCTAssertTrue(padded.allSatisfy { $0.count == 4 })
        XCTAssertEqual(["Item", "Cost", "", ""], padded[0])
        XCTAssertEqual(["", "=SUM(B1:B2)", "", ""], padded[2])
        XCTAssertEqual(["", "", "", ""], padded[7])
    }

    func testAWideSheetKeepsItsWidth() throws {
        let wide = DocumentsSheetGrid(rows: [(0..<9).map { DocumentsSheetCell(v: "c\($0)") }])
        XCTAssertEqual(9, wide.padded[0].count)
        XCTAssertEqual(8, wide.padded.count)
    }

    func testAnEmptyAnswerIsStillAGridToTypeIn() {
        let empty = DocumentsSheetGrid()
        XCTAssertEqual(8, empty.padded.count)
        XCTAssertEqual(4, empty.padded[0].count)
    }

    // MARK: - column names

    /// `colName` (`SheetModel.kt:128`), at the two places base-26-with-no-zero
    /// goes wrong.
    func testColumnNamesCarryPastZ() {
        XCTAssertEqual("A", DocumentsSheetModel.columnName(0))
        XCTAssertEqual("Z", DocumentsSheetModel.columnName(25))
        XCTAssertEqual("AA", DocumentsSheetModel.columnName(26))
        XCTAssertEqual("AB", DocumentsSheetModel.columnName(27))
        XCTAssertEqual("AZ", DocumentsSheetModel.columnName(51))
        XCTAssertEqual("BA", DocumentsSheetModel.columnName(52))
        XCTAssertEqual("BH", DocumentsSheetModel.columnName(59))
        XCTAssertEqual("", DocumentsSheetModel.columnName(-1))
    }

    func testACellsAddressIsItsColumnAndItsRowNumber() {
        XCTAssertEqual("A1", DocumentsSheetModel.address(row: 0, column: 0))
        XCTAssertEqual("B12", DocumentsSheetModel.address(row: 11, column: 1))
    }

    // MARK: - the model

    private struct Calls: Sendable {
        let lines = Shared<[String]>([])
        let bodies = Shared<[Data]>([])
    }

    private func sheet(
        _ name: String = "a spreadsheet.xlsx",
        canWrite: Bool = true,
        calls: Calls,
        answer: @Sendable @escaping (String, String) async throws -> Data
    ) -> DocumentsSheetModel {
        DocumentsSheetModel(entry: entry(name), canWrite: canWrite, deviceID: "phone-1", send: { method, path, body in
            calls.lines.mutate { $0.append("\(method) \(path)") }
            if let body { calls.bodies.mutate { $0.append(body) } }
            return try await answer(method, path)
        })
    }

    func testTheSheetIsReadAndOneEditMakesItDirty() async throws {
        let calls = Calls()
        let model = sheet(calls: calls) { _, _ in Data(sheetGrid.utf8) }
        model.read()
        await settleUntil({ model.load == .ready }, "the grid")

        XCTAssertEqual(["GET /api/documents/sheet/a%20spreadsheet.xlsx"], calls.lines.current)
        XCTAssertEqual("Item", model.cell(row: 0, column: 0))
        XCTAssertEqual("=SUM(B1:B2)", model.cell(row: 2, column: 1))
        XCTAssertEqual(4, model.columns)
        XCTAssertFalse(model.dirty)
        XCTAssertFalse(model.canSave)

        model.setCell(row: 1, column: 1, to: "21")
        XCTAssertTrue(model.dirty)
        XCTAssertTrue(model.canSave)
        // Typing the same text again is not an edit.
        let before = model.dirty
        model.setCell(row: 1, column: 1, to: "21")
        XCTAssertEqual(before, model.dirty)
    }

    /// A keystroke that arrives after a reload shrank the grid is a race, not
    /// a programming error: it is ignored, and nothing traps.
    func testACellOutsideTheGridIsIgnored() async throws {
        let calls = Calls()
        let model = sheet(calls: calls) { _, _ in Data(sheetGrid.utf8) }
        model.read()
        await settleUntil({ model.load == .ready }, "the grid")
        model.setCell(row: 99, column: 99, to: "x")
        XCTAssertFalse(model.dirty)
        XCTAssertEqual("", model.cell(row: 99, column: 99))
    }

    /// The **whole** padded grid goes back, not only the changed cell: the
    /// server diffs what it is given (`documents.py:1275`), so a body holding
    /// just the edit would blank everything left out of it.
    func testASaveSendsTheWholeGridBack() async throws {
        let calls = Calls()
        let model = sheet(calls: calls) { method, _ in
            method == "GET" ? Data(sheetGrid.utf8) : Data(#"{"rel_path":"a spreadsheet.xlsx","size":5000}"#.utf8)
        }
        model.read()
        await settleUntil({ model.load == .ready }, "the grid")
        model.setCell(row: 1, column: 1, to: "21")
        model.save()
        await settleUntil({ !model.saving }, "the save")

        XCTAssertEqual("PUT /api/documents/sheet/a%20spreadsheet.xlsx", calls.lines.current.last)
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: try XCTUnwrap(calls.bodies.current.first)) as? [String: Any]
        )
        let rows = try XCTUnwrap(object["rows"] as? [[[String: Any]]])
        XCTAssertEqual(8, rows.count)
        XCTAssertEqual(4, rows[0].count)
        XCTAssertEqual("Item", rows[0][0]["v"] as? String)
        XCTAssertEqual("21", rows[1][1]["v"] as? String)
        // The formula is still a formula on the way out.
        XCTAssertEqual("=SUM(B1:B2)", rows[2][1]["f"] as? String)
        XCTAssertEqual("phone-1", object["device_id"] as? String)

        XCTAssertFalse(model.dirty)
        XCTAssertEqual("saved", model.toast)
    }

    /// 415: a sheet type the server will not round-trip (`.xls`, `.ods`).
    /// It is an **answer** — the one control under it is Close.
    func testASheetTypeTheServerWillNotEditSaysSoAndOffersNoSave() async throws {
        let calls = Calls()
        let model = sheet("old.ods", calls: calls) { _, _ in
            throw sheetRefusal(415, body: #"{"detail":".ods isn't editable in the sheet editor (xlsx/csv only)"}"#)
        }
        model.read()
        await settleUntil({ model.load != .loading }, "the refusal")
        XCTAssertEqual(.unsupported, model.load)
        XCTAssertFalse(model.offersSave)
    }

    /// 413 — a row past the column cap — and 409, an `.xlsx` holding charts
    /// the server will not strip: both come with the server's own sentence,
    /// and both leave the edit in hand.
    func testASaveRefusedSaysWhyAndKeepsTheEdit() async throws {
        let calls = Calls()
        let model = sheet(calls: calls) { method, _ in
            if method == "GET" { return Data(sheetGrid.utf8) }
            throw sheetRefusal(413, body: #"{"detail":"more than 60 columns"}"#)
        }
        model.read()
        await settleUntil({ model.load == .ready }, "the grid")
        model.setCell(row: 0, column: 0, to: "Thing")
        model.save()
        await settleUntil({ !model.saving }, "the refusal")
        XCTAssertEqual("more than 60 columns", model.saveFailure)
        XCTAssertEqual("save failed: more than 60 columns", model.toast)
        XCTAssertTrue(model.dirty)
    }

    func testAPhoneThatMayNotWriteGetsNoSave() async throws {
        let calls = Calls()
        let model = sheet(canWrite: false, calls: calls) { _, _ in Data(sheetGrid.utf8) }
        model.read()
        await settleUntil({ model.load == .ready }, "the grid")
        model.setCell(row: 0, column: 0, to: "Thing")
        XCTAssertFalse(model.offersSave)
        XCTAssertFalse(model.canSave)
        model.save()
        for _ in 0..<3000 { await Task.yield() }
        XCTAssertEqual(1, calls.lines.current.count)
    }

    /// The window is **this server's**, not what the app remembers: an older
    /// or newer house may read a different one, and a save past it is a 413
    /// the person would otherwise not understand.
    func testTheWindowComesFromTheServer() async throws {
        let calls = Calls()
        let model = sheet(calls: calls) { _, _ in
            Data(#"{"rel_path":"a.csv","rows":[[{"v":"x"}]],"max_rows":200,"max_cols":12}"#.utf8)
        }
        model.read()
        await settleUntil({ model.load == .ready }, "the grid")
        XCTAssertEqual(200, model.maxRows)
        XCTAssertEqual(12, model.maxCols)
    }

    /// [[read-after-await-wrong-key]]: a save that lands after the grid was
    /// read again must not mark the newer grid saved — it was never sent.
    func testAGridReloadedInsideASaveIsNotMarkedSaved() async throws {
        let calls = Calls()
        let letThePutLand = Shared<Bool>(false)
        let answer = Shared<String>(sheetGrid)
        let model = sheet(calls: calls) { method, _ in
            if method == "PUT" {
                while !letThePutLand.current { await Task.yield() }
                return Data(#"{"rel_path":"a spreadsheet.xlsx"}"#.utf8)
            }
            return Data(answer.current.utf8)
        }
        model.read()
        await settleUntil({ model.load == .ready }, "the grid")
        model.setCell(row: 0, column: 0, to: "Thing")
        model.save()
        await settleUntil({ calls.lines.current.contains(where: { $0.hasPrefix("PUT") }) }, "the save")

        // The sheet is read again — somebody else's save landed — while this
        // phone's PUT is still in flight.
        answer.set(#"{"rel_path":"a spreadsheet.xlsx","rows":[[{"v":"Someone else's"}]],"max_rows":1000,"max_cols":60}"#)
        model.read()
        await settleUntil({ model.cell(row: 0, column: 0) == "Someone else's" }, "the reread")
        letThePutLand.set(true)
        for _ in 0..<3000 { await Task.yield() }

        // The reread's grid is what is on screen and what `dirty` compares
        // against — the save did not overwrite either with its own copy.
        XCTAssertEqual("Someone else's", model.cell(row: 0, column: 0))
        XCTAssertFalse(model.dirty)
    }
}
