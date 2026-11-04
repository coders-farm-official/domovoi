import Compression
import XCTest
@testable import DomovoiKit

/// The spreadsheet parser, ported from `CsvSheet.kt`, `XlsxSheet.kt`,
/// `MiniXml.kt` and `SheetModel.kt` at owner answer 22.
///
/// Every case here is a question the server answers too, which is the point:
/// the phone's grid is a **fallback** for the same file
/// (``DocumentsSheetModel/source``), so a disagreement between the two would
/// show up as a sheet that looked different depending on which route
/// happened to work. Where a number's spelling is asserted, the expected
/// value is Python's `repr` — the server's `_cell_model` sends what Python
/// prints.
final class DocumentsSheetParserTests: XCTestCase {

    // MARK: - .csv

    /// The one the task names: quoted commas and an embedded newline.
    ///
    /// It would fail if the state machine ended a field on a comma inside
    /// quotes (three cells instead of two), ended a record on a newline
    /// inside quotes (four rows instead of two), or kept the doubled quote
    /// as two characters.
    func testACsvKeepsCommasAndNewlinesInsideQuotes() throws {
        let text = "name,note\r\n\"Smith, J\",\"line one\nline two\"\r\n\"a \"\"quoted\"\" word\",ok\r\n"
        let grid = try DocumentsCsvSheet.read(Array(text.utf8))
        XCTAssertEqual(3, grid.count)
        XCTAssertEqual(["name", "note"], grid[0].map { $0?.v })
        XCTAssertEqual(["Smith, J", "line one\nline two"], grid[1].map { $0?.v })
        XCTAssertEqual(["a \"quoted\" word", "ok"], grid[2].map { $0?.v })
        // Every cell is a value: `_read_sheet_grid` sends `{"v": cell, "f":
        // None}` for a `.csv` (`documents.py:993`).
        XCTAssertTrue(grid.allSatisfy { $0.allSatisfy { $0?.f == nil } })
    }

    /// A UTF-8 BOM is dropped, a blank line is an empty record, and a
    /// trailing empty field is kept.
    ///
    /// The BOM half is the one that bites: `utf-8-sig` on the server
    /// (`documents.py:988`) drops it, so a phone that kept it would show the
    /// first header as `\u{FEFF}name` and a sheet read twice would have two
    /// different first cells.
    func testACsvDropsTheBomAndKeepsBlankRecordsAndTrailingFields() throws {
        let text = "\u{FEFF}a,b,\n\nc,d,e"
        let grid = try DocumentsCsvSheet.read(Array(text.utf8))
        XCTAssertEqual(3, grid.count)
        XCTAssertEqual(["a", "b", ""], grid[0].map { $0?.v })
        XCTAssertEqual([], grid[1].map { $0?.v })
        XCTAssertEqual(["c", "d", "e"], grid[2].map { $0?.v })
    }

    /// A lone `\r` is a line end on its own, and `\r\n` is one line end and
    /// not two.
    func testACsvTakesAllThreeLineEndings() {
        XCTAssertEqual([["a"], ["b"], ["c"]], DocumentsCsvSheet.parse("a\rb\r\nc"))
    }

    /// A cell whose text starts with `=` stays text in a `.csv`, because that
    /// is what the server reads it as. It becomes a formula only on the way
    /// back out (``DocumentsPath/sheetBody(rows:deviceID:)``), which is the
    /// server's rule for the save.
    func testACsvFormulaTextIsAValueNotAFormula() throws {
        let grid = try DocumentsCsvSheet.read(Array("=SUM(A1:A2)".utf8))
        XCTAssertEqual("=SUM(A1:A2)", grid[0][0]?.v)
        XCTAssertNil(grid[0][0]?.f)
        // And the editor shows it either way, which is the whole of
        // `display`.
        XCTAssertEqual("=SUM(A1:A2)", grid[0][0]?.display)
    }

    /// The server's window, truncated and not errored
    /// (`_read_sheet_grid`'s docstring).
    func testACsvIsCutToTheServersWindow() throws {
        let wide = (0..<80).map { "c\($0)" }.joined(separator: ",")
        let text = (0..<1005).map { _ in wide }.joined(separator: "\n")
        let grid = try DocumentsCsvSheet.read(Array(text.utf8))
        XCTAssertEqual(DocumentsSheetLimits.maxRows, grid.count)
        XCTAssertEqual(DocumentsSheetLimits.maxCols, grid[0].count)
    }

    // MARK: - .xlsx

    /// The second one the task names: shared strings and a formula cell.
    ///
    /// Five cell shapes at once, because each one is a different branch of
    /// `cellValue` and a port can get any of them alone:
    ///
    /// * `t="s"` — the text is in `sharedStrings.xml`, by index. A reader
    ///   that showed the index would put `0` in the header row.
    /// * a `<f>` child — the **formula**, with no value, as
    ///   `data_only=False` gives the server.
    /// * a plain number — Python's spelling of it.
    /// * `t="inlineStr"` — the text is in the cell.
    /// * `t="b"` — `True`/`False`, which is what Python prints for a bool.
    func testAnXlsxReadsSharedStringsAndAFormula() throws {
        let book = Self.workbook(sheet: """
        <worksheet><sheetData>
        <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
        <row r="2"><c r="A2"><v>1200</v></c><c r="B2"><f>SUM(A2:A2)</f></c></row>
        <row r="3"><c r="A3" t="inlineStr"><is><t>typed here</t></is></c><c r="B3" t="b"><v>1</v></c></row>
        </sheetData></worksheet>
        """, strings: ["quarter", "takings"])
        let grid = try DocumentsXlsxSheet.read(book)
        XCTAssertEqual(["quarter", "takings"], grid[0].map { $0?.v })
        XCTAssertEqual("1200", grid[1][0]?.v)
        XCTAssertEqual("=SUM(A2:A2)", grid[1][1]?.f)
        XCTAssertNil(grid[1][1]?.v, "a formula arrives with no cached value, as `data_only=False` gives")
        XCTAssertEqual("typed here", grid[2][0]?.v)
        XCTAssertEqual("True", grid[2][1]?.v)
    }

    /// A shared formula is translated to the cell it is in, not copied.
    ///
    /// `B2` holds `<f t="shared" si="0"/>` — an empty element. Copied, every
    /// row of a column would show the first row's formula; this is
    /// `XlsxSheet.translate` (`:292`) and openpyxl's `Translator` behind it.
    func testASharedFormulaIsTranslatedToItsOwnCell() throws {
        let book = Self.workbook(sheet: """
        <worksheet><sheetData>
        <row r="1"><c r="A1"><v>2</v></c><c r="B1"><f t="shared" ref="B1:B2" si="0">A1*2</f></c></row>
        <row r="2"><c r="A2"><v>3</v></c><c r="B2"><f t="shared" si="0"/></c></row>
        </sheetData></worksheet>
        """, strings: [])
        let grid = try DocumentsXlsxSheet.read(book)
        XCTAssertEqual("=A1*2", grid[0][1]?.f)
        XCTAssertEqual("=A2*2", grid[1][1]?.f, "the shared formula was copied, not translated")
    }

    /// An absolute reference does not move, and a function name that happens
    /// to end in letters-and-digits is not a reference.
    func testTranslateLeavesAbsoluteRefsAndFunctionNamesAlone() {
        XCTAssertEqual("=$A$1+B3", DocumentsXlsxSheet.translate("=$A$1+B2", dRow: 1, dColumn: 0))
        XCTAssertEqual("=LOG10(B3)", DocumentsXlsxSheet.translate("=LOG10(B2)", dRow: 1, dColumn: 0))
        XCTAssertEqual("=\"A1\"&B3", DocumentsXlsxSheet.translate("=\"A1\"&B2", dRow: 1, dColumn: 0))
        XCTAssertEqual("=A1", DocumentsXlsxSheet.translate("=A1", dRow: 0, dColumn: 0))
    }

    /// A date-formatted number prints as a Python `datetime` prints, not as
    /// its serial.
    ///
    /// `45000` with a custom `yyyy-mm-dd` format is 2023-03-15. A reader that
    /// missed the style would show `45000`, which is a wrong value and not a
    /// missing feature — the person would believe the sheet said 45000.
    func testADateFormattedNumberPrintsAsADate() throws {
        let book = Self.workbook(sheet: """
        <worksheet><sheetData>
        <row r="1"><c r="A1" s="1"><v>45000</v></c><c r="B1"><v>45000</v></c></row>
        </sheetData></worksheet>
        """, strings: [])
        let grid = try DocumentsXlsxSheet.read(book)
        XCTAssertEqual("2023-03-15 00:00:00", grid[0][0]?.v)
        XCTAssertEqual("45000", grid[0][1]?.v, "a cell with no date style is still a number")
    }

    /// Trailing empty cells go per row and trailing empty rows go altogether
    /// — `:443-446`, and `_read_sheet_grid`'s own trim (`:1005`, `:1011`).
    ///
    /// It is what keeps a one-cell workbook from opening sixty columns wide,
    /// which the server's docstring records as a real defect.
    func testAnXlsxTrimsTrailingEmptiesSoTheGridIsNotSixtyWide() throws {
        let book = Self.workbook(sheet: """
        <worksheet><sheetData>
        <row r="1"><c r="A1"><v>1</v></c><c r="D1"></c><c r="BH1"></c></row>
        <row r="2"></row>
        </sheetData></worksheet>
        """, strings: [])
        let grid = try DocumentsXlsxSheet.read(book)
        XCTAssertEqual(1, grid.count, "an empty trailing row was kept")
        XCTAssertEqual(1, grid[0].count, "empty trailing cells were kept")
        // And the editor pads it to somewhere to type, exactly as a server
        // grid is padded.
        XCTAssertEqual(
            8, DocumentsSheetGrid(rows: grid).padded.count,
            "a one-cell sheet has nowhere to type"
        )
    }

    // MARK: - what must be refused rather than half-parsed

    /// The third one the task names: malformed XML.
    ///
    /// The worksheet is truncated after a perfectly good first row.
    /// `MiniXml.kt` would stop its tag scan and `XlsxSheet.read` would hand
    /// back that row — a grid that looks like a one-row sheet. On a phone the
    /// next thing anybody does with a grid is save it, and the save sends the
    /// **whole** grid, so the rows that did not parse would be written back
    /// as blanks. So it throws.
    func testATruncatedWorksheetIsRefusedAndNotHalfParsed() {
        let book = Self.workbook(sheet: """
        <worksheet><sheetData><row r="1"><c r="A1"><v>1</v></c></row>
        """, strings: [])
        XCTAssertThrowsError(try DocumentsXlsxSheet.read(book)) { error in
            guard case .malformed = error as? DocumentsSheetTrouble else {
                return XCTFail("a truncated worksheet answered \(error)")
            }
        }
    }

    /// An attribute whose quote never closes, which is where Android's
    /// `readTag` returns null.
    /// `<sheetData x="unclosed>` — there is no second quote anywhere after
    /// it, which is where Android's `readTag` returns null and its scan
    /// stops.
    func testAnUnclosedAttributeQuoteIsRefused() {
        let book = Self.workbook(
            sheet: "<worksheet><sheetData x=\"unclosed></sheetData></worksheet>",
            strings: []
        )
        XCTAssertThrowsError(try DocumentsXlsxSheet.read(book)) { error in
            guard case .malformed = error as? DocumentsSheetTrouble else {
                return XCTFail("an unclosed attribute quote answered \(error)")
            }
        }
    }

    /// A comment that never ends, which Android skips to the end of the
    /// document.
    func testACommentThatNeverEndsIsRefused() {
        XCTAssertThrowsError(try DocumentsMiniXml.nextTag(Array("<!-- oh".utf8)))
    }

    func testBytesThatAreNotAZipAreRefused() {
        XCTAssertThrowsError(try DocumentsXlsxSheet.read(Array("not a workbook at all".utf8))) { error in
            guard case .malformed = error as? DocumentsSheetTrouble else {
                return XCTFail("random bytes answered \(error)")
            }
        }
    }

    /// A zip with no `sheetData` at all — the part is there and is not a
    /// worksheet.
    func testAWorksheetWithNoSheetDataIsRefused() {
        let book = Self.workbook(sheet: "<worksheet><dimension ref=\"A1\"/></worksheet>", strings: [])
        XCTAssertThrowsError(try DocumentsXlsxSheet.read(book))
    }

    // MARK: - the entry point

    func testTheExtensionDecidesWhichReaderRuns() throws {
        let csv = try DocumentsSheetParser.grid(of: Data("a,b".utf8), named: "rows.csv")
        XCTAssertEqual(["a", "b"], csv.rows[0].map { $0?.v })
        XCTAssertEqual("rows.csv", csv.relPath)
        // The server's window travels with the grid, because that is the
        // window a save would be held to.
        XCTAssertEqual(DocumentsSheetLimits.maxRows, csv.maxRows)
        XCTAssertEqual(DocumentsSheetLimits.maxCols, csv.maxCols)
        // `hadRows` is true even for an empty file: a sheet `+ New` just made
        // must open as somewhere to type.
        XCTAssertTrue(try DocumentsSheetParser.grid(of: Data(), named: "new.csv").hadRows)
    }

    /// A folder with a dot in its name does not decide the format.
    func testTheLastPathComponentDecidesTheFormat() throws {
        let grid = try DocumentsSheetParser.grid(of: Data("a,b".utf8), named: "budget.v2/rows.csv")
        XCTAssertEqual(["a", "b"], grid.rows[0].map { $0?.v })
    }

    /// A `.xls` and an `.ods` are what the server answers **415** for, and
    /// the phone does not read them either — so the fallback has nothing to
    /// offer and says the same thing the server's refusal says.
    func testAnUnreadableSheetTypeIsNotASheet() {
        for name in ["old.xls", "old.ods", "notes.txt"] {
            XCTAssertThrowsError(try DocumentsSheetParser.grid(of: Data(), named: name)) { error in
                XCTAssertEqual(.notASheet, error as? DocumentsSheetTrouble, name)
            }
            XCTAssertEqual(
                DocumentsStrings.sheetUnsupported, DocumentsSheetTrouble.notASheet.message,
                "the refusal is the server's own sentence"
            )
        }
    }

    func testAFileOverTheByteCapIsRefusedBeforeItIsParsed() {
        let big = Data(count: DocumentsSheetLimits.maxFileBytes + 1)
        XCTAssertThrowsError(try DocumentsSheetParser.grid(of: big, named: "huge.csv")) { error in
            guard case .tooLarge = error as? DocumentsSheetTrouble else {
                return XCTFail("a file over the cap answered \(error)")
            }
        }
    }

    /// A small archive that declares a gigantic part is refused from the
    /// **directory**, without inflating any of it.
    func testAnArchiveThatClaimsToUnpackToGigabytesIsRefused() {
        let book = Self.zip([
            Self.Entry(name: "_rels/.rels", bytes: Array("<x/>".utf8), declaredSize: 900 * 1024 * 1024)
        ])
        XCTAssertThrowsError(try DocumentsXlsxSheet.read(book)) { error in
            guard case .tooLarge = error as? DocumentsSheetTrouble else {
                return XCTFail("a zip bomb answered \(error)")
            }
        }
    }

    // MARK: - numbers and addresses

    /// Python's `repr(float)`, which Swift's own `String(_:)` is **not**.
    ///
    /// `1e15` is the case that proves it: Swift prints `1e+15` and Python
    /// prints `1000000000000000.0`, and the cell is the string.
    func testNumbersAreSpeltAsPythonSpellsThem() {
        XCTAssertEqual("1000000000000000.0", DocumentsSheetParser.pythonFloat(1e15))
        XCTAssertEqual("1e+16", DocumentsSheetParser.pythonFloat(1e16))
        XCTAssertEqual("1e+100", DocumentsSheetParser.pythonFloat(1e100))
        XCTAssertEqual("0.0001", DocumentsSheetParser.pythonFloat(0.0001))
        XCTAssertEqual("1e-05", DocumentsSheetParser.pythonFloat(0.00001))
        XCTAssertEqual("1.0", DocumentsSheetParser.pythonFloat(1))
        XCTAssertEqual("20.0", DocumentsSheetParser.pythonFloat(20))
        XCTAssertEqual("1234.5", DocumentsSheetParser.pythonFloat(1234.5))
        XCTAssertEqual("0.30000000000000004", DocumentsSheetParser.pythonFloat(0.1 + 0.2))
        XCTAssertEqual("-0.0", DocumentsSheetParser.pythonFloat(-0.0))
        XCTAssertEqual("-12.5", DocumentsSheetParser.pythonFloat(-12.5))
        XCTAssertEqual("nan", DocumentsSheetParser.pythonFloat(.nan))
        XCTAssertEqual("inf", DocumentsSheetParser.pythonFloat(.infinity))
    }

    /// An integer keeps all its digits, however many there are: `Int64` is
    /// not wide enough for every number a household sheet can hold, and a
    /// twenty-five-digit account number must not come back as something
    /// nearly right.
    func testAnIntegerIsNormalisedWithoutLosingDigits() {
        XCTAssertEqual("7", DocumentsSheetParser.pythonInteger("007"))
        XCTAssertEqual("5", DocumentsSheetParser.pythonInteger("+5"))
        XCTAssertEqual("-42", DocumentsSheetParser.pythonInteger("-42"))
        XCTAssertEqual("0", DocumentsSheetParser.pythonInteger("-0"))
        XCTAssertEqual(
            "1234567890123456789012345",
            DocumentsSheetParser.pythonInteger("1234567890123456789012345")
        )
        XCTAssertNil(DocumentsSheetParser.pythonInteger("12a"))
        // And `numberText` falls back to the raw text rather than losing it.
        XCTAssertEqual("12a", DocumentsXlsxSheet.numberText("12a"))
    }

    func testColumnNamesAndCellRefsAgree() {
        XCTAssertEqual("A", DocumentsSheetParser.columnName(0))
        XCTAssertEqual("AA", DocumentsSheetParser.columnName(26))
        XCTAssertEqual("AB", DocumentsSheetParser.columnName(27))
        XCTAssertEqual(0, DocumentsSheetParser.columnIndex("A"))
        XCTAssertEqual(27, DocumentsSheetParser.columnIndex("AB"))
        XCTAssertEqual(27, DocumentsSheetParser.columnIndex("ab"))
        for index in [0, 1, 25, 26, 51, 52, 700, 16_383] {
            XCTAssertEqual(
                index,
                DocumentsSheetParser.columnIndex(DocumentsSheetParser.columnName(index)),
                "\(index)"
            )
        }
        // And the model's own helper is the same table, not a second copy.
        XCTAssertEqual("B12", DocumentsSheetModel.address(row: 11, column: 1))
    }

    func testACellRefIsReadAndAnythingElseIsRefused() {
        XCTAssertEqual(11, DocumentsSheetParser.cellRef("B12")?.row)
        XCTAssertEqual(1, DocumentsSheetParser.cellRef("B12")?.column)
        XCTAssertEqual(0, DocumentsSheetParser.cellRef("$A$1")?.row)
        XCTAssertNil(DocumentsSheetParser.cellRef("B"))
        XCTAssertNil(DocumentsSheetParser.cellRef("12"))
        XCTAssertNil(DocumentsSheetParser.cellRef("ABCD1"))
        XCTAssertNil(DocumentsSheetParser.cellRef("A1:B2"))
        XCTAssertNil(DocumentsSheetParser.cellRef("Sheet1!A1"))
        // A row number past `Int` is nil rather than a crash: Android's
        // `toInt()` throws here, so a crafted `.xlsx` takes its reader down.
        XCTAssertNil(DocumentsSheetParser.cellRef("A99999999999999999999"))
    }

    func testTheEntitiesAndTheNumericReferences() {
        XCTAssertEqual("<a & b>", DocumentsMiniXml.unescape("&lt;a &amp; b&gt;"))
        XCTAssertEqual("A\u{00e9}", DocumentsMiniXml.unescape("&#65;&#xe9;"))
        // A lone surrogate is left as written rather than crashing the
        // reader, which is what `Character.toChars` would do on Android.
        XCTAssertEqual("&#xD800;", DocumentsMiniXml.unescape("&#xD800;"))
        XCTAssertEqual("&nbsp;", DocumentsMiniXml.unescape("&nbsp;"))
    }

    // MARK: - a workbook, built by hand

    /// One `.xlsx`, as bytes.
    ///
    /// The parts are the smallest set `book(_:)` needs: the root
    /// relationships, the workbook, the workbook's relationships, the
    /// worksheet, the shared strings and a `styles.xml` whose second `xf`
    /// carries a `yyyy-mm-dd` format, so `s="1"` is a date cell.
    ///
    /// Written here rather than checked in as a base64 blob: a blob cannot be
    /// read, so a case built on one cannot say what it is testing, and the
    /// zip writer is twenty lines.
    static func workbook(sheet: String, strings: [String]) -> [UInt8] {
        let shared = "<sst count=\"\(strings.count)\">"
            + strings.map { "<si><t>\($0)</t></si>" }.joined()
            + "</sst>"
        return zip([
            Entry(name: "_rels/.rels", bytes: Array("""
            <Relationships><Relationship Id="rId1" \
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" \
            Target="xl/workbook.xml"/></Relationships>
            """.utf8)),
            Entry(name: "xl/workbook.xml", bytes: Array("""
            <workbook><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>
            """.utf8)),
            Entry(name: "xl/_rels/workbook.xml.rels", bytes: Array("""
            <Relationships>\
            <Relationship Id="rId1" \
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" \
            Target="worksheets/sheet1.xml"/>\
            <Relationship Id="rId2" \
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" \
            Target="sharedStrings.xml"/>\
            <Relationship Id="rId3" \
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" \
            Target="styles.xml"/>\
            </Relationships>
            """.utf8)),
            // Deflated, because every real `.xlsx` part is: this is the one
            // entry that exercises `inflate`.
            Entry(name: "xl/worksheets/sheet1.xml", bytes: Array(sheet.utf8), deflate: true),
            Entry(name: "xl/sharedStrings.xml", bytes: Array(shared.utf8)),
            Entry(name: "xl/styles.xml", bytes: Array("""
            <styleSheet>\
            <numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts>\
            <cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="164"/></cellXfs>\
            </styleSheet>
            """.utf8)),
        ])
    }

    struct Entry {
        let name: String
        let bytes: [UInt8]
        var deflate = false
        /// What the directory **says** the part inflates to, when that has to
        /// be a lie (the zip-bomb case). Nil means the truth.
        var declaredSize: Int?
    }

    /// A zip, by hand.
    ///
    /// The CRC fields are left at zero: this reader does not check them (nor
    /// does Android's `ZipInputStream` for a stored entry read through to the
    /// end), and a CRC32 implementation in a test would be a second thing to
    /// get wrong. The sizes come from the central directory, which is where
    /// the reader takes them from.
    static func zip(_ entries: [Entry]) -> [UInt8] {
        var out: [UInt8] = []
        var directory: [UInt8] = []
        for entry in entries {
            let name = Array(entry.name.utf8)
            let payload = entry.deflate ? deflated(entry.bytes) : entry.bytes
            let method = entry.deflate ? 8 : 0
            let declared = entry.declaredSize ?? entry.bytes.count
            let localAt = out.count
            out += [0x50, 0x4B, 0x03, 0x04]
            out += u16(20) + u16(0) + u16(method) + u16(0) + u16(0)
            out += u32(0) + u32(payload.count) + u32(declared)
            out += u16(name.count) + u16(0)
            out += name
            out += payload
            directory += [0x50, 0x4B, 0x01, 0x02]
            directory += u16(20) + u16(20) + u16(0) + u16(method) + u16(0) + u16(0)
            directory += u32(0) + u32(payload.count) + u32(declared)
            directory += u16(name.count) + u16(0) + u16(0) + u16(0) + u16(0)
            directory += u32(0) + u32(localAt)
            directory += name
        }
        let directoryAt = out.count
        out += directory
        out += [0x50, 0x4B, 0x05, 0x06]
        out += u16(0) + u16(0) + u16(entries.count) + u16(entries.count)
        out += u32(directory.count) + u32(directoryAt) + u16(0)
        return out
    }

    static func u16(_ value: Int) -> [UInt8] {
        [UInt8(value & 0xFF), UInt8((value >> 8) & 0xFF)]
    }

    static func u32(_ value: Int) -> [UInt8] {
        [
            UInt8(value & 0xFF), UInt8((value >> 8) & 0xFF),
            UInt8((value >> 16) & 0xFF), UInt8((value >> 24) & 0xFF),
        ]
    }

    /// Raw DEFLATE, the format a zip's method 8 entry holds — `COMPRESSION_ZLIB`
    /// both ways, which is the pair the reader's own comment is about.
    static func deflated(_ input: [UInt8]) -> [UInt8] {
        // Deflate can expand incompressible input, so the buffer is roomy.
        var out = [UInt8](repeating: 0, count: input.count * 2 + 1024)
        let written = input.withUnsafeBufferPointer { source in
            out.withUnsafeMutableBufferPointer { destination in
                compression_encode_buffer(
                    destination.baseAddress!, destination.count,
                    source.baseAddress!, source.count,
                    nil, COMPRESSION_ZLIB
                )
            }
        }
        return Array(out.prefix(written))
    }
}
