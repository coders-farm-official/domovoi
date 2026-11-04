import Foundation

/// A spreadsheet file the phone could not make a grid out of, and why.
///
/// `SheetModel.kt:28-31` has two of these — `SheetRefused` (a save the sheet
/// code will not do) and `SheetUnsupported` (the format, which is the
/// server's 415). The reader needs a third, and it is the one this port
/// exists to get right: **``malformed(_:)`` is a refusal, not a partial
/// answer.**
///
/// Android's reader stops its tag scan when a tag will not read
/// (`MiniXml.readTag` returns null and `tags` breaks), so a worksheet whose
/// XML is truncated or whose attribute quote never closes comes back as the
/// rows that happened to parse before the damage — a grid that looks like a
/// sheet with some empty cells. On a phone the next thing a person does with
/// that grid is save it, and the save sends the **whole** grid: the half that
/// did not parse is written back as blanks. So this port throws instead
/// (``DocumentsMiniXml`` is the strict half), which is the standing rule in
/// [[no-misleading-controls]]'s own territory — a grid that cannot say it is
/// incomplete must not be drawn at all.
public enum DocumentsSheetTrouble: Error, Equatable, Sendable {

    /// Not a file this parser opens — the extension is not `.csv` or
    /// `.xlsx`. The server answers **415** for the same files and
    /// ``DocumentsStrings/sheetUnsupported`` is the sentence.
    case notASheet

    /// The bytes are not the format the name claims: not a zip, no workbook
    /// part, a worksheet with no `sheetData`, XML that does not scan.
    case malformed(String)

    /// Past ``DocumentsSheetLimits``. Said with the limit in it, because the
    /// only useful thing a person can do about it is open the file
    /// elsewhere.
    case tooLarge(String)

    /// The sentence to draw. The server's own wording where there is one.
    public var message: String {
        switch self {
        case .notASheet: return DocumentsStrings.sheetUnsupported
        case .malformed(let why): return DocumentsStrings.notASheetFile(why)
        case .tooLarge(let why): return why
        }
    }
}

/// The bounds the phone reads a sheet within.
///
/// ``maxRows`` and ``maxCols`` are the server's own window
/// (`SheetModel.kt:15-16`, `documents.py`'s `_SHEET_MAX_ROWS` /
/// `_SHEET_MAX_COLS`) and are not negotiable: a grid wider than the window
/// is a grid `PUT /sheet` would answer 413 to, so reading one would be a
/// save that cannot work.
///
/// The two byte bounds are **halved from Android's** (64 MiB file, 200 MiB
/// inflated — `SHEET_MAX_BYTES`, `XlsxSheet.MAX_UNZIPPED`), and the reason is
/// not caution: this reader holds the archive, the inflated worksheet, the
/// shared strings and the finished grid in memory at the same time, and the
/// grid it can possibly produce is 1000 × 60 cells however big the file is.
/// A household sheet that does not fit in 32 MiB is one to open on the
/// dashboard, which is what the refusal says.
public enum DocumentsSheetLimits: Sendable {
    public static let maxRows = 1000
    public static let maxCols = 60
    /// The sheet FILE the phone will open at all.
    public static let maxFileBytes = 32 * 1024 * 1024
    /// What an `.xlsx` may inflate to, counted while inflating so a small
    /// archive that unpacks to gigabytes is refused rather than held.
    public static let maxUnzippedBytes = 64 * 1024 * 1024
}

/// A `.csv` or an `.xlsx` turned into the grid editor's own model, on the
/// phone.
///
/// Android's `CsvSheet.kt`, `XlsxSheet.kt`, `MiniXml.kt` and `SheetModel.kt`,
/// ported at owner answer 22 ("port the spreadsheet parser here, in
/// Documents").
///
/// ### It is beside the server's route, not instead of it
///
/// `GET /api/documents/sheet` stays what it is and stays first: it is the
/// route the server keeps "for the phone app's own grid editor"
/// (`documents.py:1314`), openpyxl reads the workbook there, and `PUT /sheet`
/// patches the file cell by changed cell so styles, other sheets and charts
/// survive a save. Nothing about that changes.
///
/// What this adds is the **second** way to a grid, for the file the server
/// could not answer for: an older core with no `/sheet` route, a 500 out of
/// openpyxl, a 200 that is not this route's answer. Before this, those were
/// `Couldn't load: …` and a Close button; now the phone fetches the file's
/// own bytes from `GET /api/documents/raw` (`documents.py:2166`, the same
/// daily read tier as `/sheet`) and reads them itself.
/// ``DocumentsSheetModel/source`` is what tells the two apart, and a grid
/// that came from here is **read-only**: a save would have to be a patch of
/// the workbook's bytes and there is no route that takes one
/// ([[no-misleading-controls]]).
///
/// ### What is not ported, and why
///
/// * **The writer.** `XlsxSheet.patch` (`:609-824`) rewrites the changed
///   cells inside the worksheet XML and rezips, `CsvSheet.write` (`:94`)
///   re-emits the file, and `cellWrite`/`sheetChanges` (`SheetModel.kt:45`,
///   `:80`) classify what was typed. All four exist on Android to save a
///   file on the **phone's own storage**, which this app has none of — and
///   there is no server route that takes a sheet's bytes from a phone
///   (`/api/documents/upload` is the milestone behind owner answers 43 and
///   47). Ported, they would be the seam-with-no-caller defect
///   [[reasoning-that-hides-a-bug]] records, with tests pinning code nothing
///   could reach. They come with the item that gives this app files of its
///   own.
/// * **`XlsxSheet.readForChat`** (`:527`) — Chat reading a whole workbook.
///   Chat is another unit's screen and takes no file on this app yet.
public enum DocumentsSheetParser {

    /// The refusal for a file this big **before a byte of it is asked for**,
    /// and `nil` when there is nothing to refuse.
    ///
    /// ``grid(of:named:)`` asks the same question of the bytes it is handed,
    /// which is one request too late: the phone-parsed fallback fetches
    /// `/api/documents/raw/{rel}` whole and buffers it in a `Data`, so a
    /// 500 MB workbook — exactly the kind openpyxl falls over on, which is
    /// what sends the read here — was pulled over the LAN in full and then
    /// refused (round 2's review, S4). On a phone that is a jetsam kill as
    /// likely as a sentence, under a bare spinner with nothing to cancel.
    ///
    /// ``FileEntry/size`` is already on the entry the model holds when it
    /// makes the request. `nil` is an unknown size, not a small one: the
    /// listing is the only thing that carries it, and a read that reached the
    /// model another way must still be allowed to try. The check on the bytes
    /// stays where it is for that, and for a server whose listing understates
    /// a file.
    public static func refusalBeforeFetching(size: Int64?) -> DocumentsSheetTrouble? {
        guard let size, size > Int64(DocumentsSheetLimits.maxFileBytes) else { return nil }
        return .tooLarge(
            DocumentsStrings.sheetTooBig(DocumentsSheetLimits.maxFileBytes / (1024 * 1024))
        )
    }

    /// The grid for a sheet file the phone holds the bytes of.
    ///
    /// `name` decides the format, by extension, exactly as
    /// ``DocumentsKinds/category(of:)`` does — not by sniffing the bytes: a
    /// `.csv` whose first two bytes happen to be `PK` is still a `.csv` to
    /// the server, and the two must agree about which file is which.
    ///
    /// The returned grid carries the server's window rather than whatever the
    /// file holds, because that is the window a save would be held to, and
    /// ``DocumentsSheetGrid/hadRows`` is true: a file that really has no rows
    /// (a `+ New` spreadsheet) is an empty grid to type into, which is a
    /// different thing from a body that never carried the key.
    public static func grid(of bytes: Data, named name: String) throws -> DocumentsSheetGrid {
        guard bytes.count <= DocumentsSheetLimits.maxFileBytes else {
            throw DocumentsSheetTrouble.tooLarge(
                DocumentsStrings.sheetTooBig(DocumentsSheetLimits.maxFileBytes / (1024 * 1024))
            )
        }
        let rows: [[DocumentsSheetCell?]]
        // The last component, not the whole `rel`: a folder with a dot in its
        // name (`budget.v2/takings`) would otherwise hand `FileKinds.ext` the
        // folder's suffix.
        let leaf = name.split(separator: "/").last.map(String.init) ?? name
        switch FileKinds.ext(of: leaf) {
        case "csv": rows = try DocumentsCsvSheet.read(Array(bytes))
        case "xlsx": rows = try DocumentsXlsxSheet.read(Array(bytes))
        default: throw DocumentsSheetTrouble.notASheet
        }
        return DocumentsSheetGrid(
            relPath: name,
            rows: rows,
            maxRows: DocumentsSheetLimits.maxRows,
            maxCols: DocumentsSheetLimits.maxCols
        )
    }

    // MARK: - cell addresses

    /// `0 → "A"`, `26 → "AA"`, `27 → "AB"` — `colName`
    /// (`SheetModel.kt:125`).
    ///
    /// It lives here rather than on ``DocumentsSheetModel`` (which is where
    /// this app had it, and which still answers
    /// ``DocumentsSheetModel/columnName(_:)`` by calling this) for one
    /// reason: that model is `@MainActor`, and the `.xlsx` reader needs
    /// column names off the main actor to translate a shared formula. Two
    /// copies of a column-name table is how a grid's header comes to disagree
    /// with its cells.
    public static func columnName(_ index: Int) -> String {
        guard index >= 0 else { return "" }
        var n = index + 1
        var out = ""
        while n > 0 {
            let remainder = (n - 1) % 26
            out.append(Character(UnicodeScalar(UInt8(65 + remainder))))
            n = (n - 1) / 26
        }
        return String(out.reversed())
    }

    /// `"A" → 0`, `"AB" → 27` — `colIndex` (`SheetModel.kt:118`).
    ///
    /// Letters only, and the caller has already checked that: the one thing
    /// that hands this a string is ``cellRef(_:)``'s match.
    public static func columnIndex(_ letters: String) -> Int {
        var n = 0
        for scalar in letters.unicodeScalars {
            // `uppercaseChar() - 'A' + 1` on ASCII, which is all the regex
            // behind it admits.
            let value = scalar.value
            let letter: UInt32
            if value >= 97, value <= 122 { letter = value - 97 } else { letter = value - 65 }
            n = n * 26 + Int(letter) + 1
        }
        return n - 1
    }

    /// `"B12" → (row: 11, column: 1)`, and nil for anything else — `parseRef`
    /// (`SheetModel.kt:139`), whose regex is `\$?([A-Za-z]{1,3})\$?(\d+)`
    /// anchored whole.
    ///
    /// Written by hand rather than as an `NSRegularExpression`: it is called
    /// once per cell of a worksheet — sixty thousand times for a full window
    /// — and a regex object per call was measurable on Android before the
    /// same change was made there.
    public static func cellRef(_ ref: String) -> (row: Int, column: Int)? {
        let scalars = Array(ref.trimmingCharacters(in: .whitespacesAndNewlines).unicodeScalars)
        var at = 0
        func isLetter(_ s: Unicode.Scalar) -> Bool {
            (s >= "A" && s <= "Z") || (s >= "a" && s <= "z")
        }
        func isDigit(_ s: Unicode.Scalar) -> Bool { s >= "0" && s <= "9" }
        // `\$?`
        if at < scalars.count, scalars[at] == "$" { at += 1 }
        // `([A-Za-z]{1,3})`
        var letters = ""
        while at < scalars.count, isLetter(scalars[at]), letters.count < 3 {
            letters.unicodeScalars.append(scalars[at])
            at += 1
        }
        guard !letters.isEmpty else { return nil }
        // `\$?`
        if at < scalars.count, scalars[at] == "$" { at += 1 }
        // `(\d+)`, and then the end of the string — `matchEntire`.
        var digits = ""
        while at < scalars.count, isDigit(scalars[at]) {
            digits.unicodeScalars.append(scalars[at])
            at += 1
        }
        guard at == scalars.count, let row = Int(digits) else { return nil }
        return (row: row - 1, column: columnIndex(letters))
    }

    // MARK: - numbers, as Python prints them

    /// `repr(float)`: the shortest digits that round-trip, plain between
    /// `1e-4` and `1e16` and scientific outside it — `pyFloat`
    /// (`SheetModel.kt:100`).
    ///
    /// Android gets the digits from `BigDecimal(d.toString())`, which is the
    /// JVM's shortest round-trip repr. Swift's `String(describing:)` is also
    /// a shortest round-trip repr, but it is **not** formatted the same way:
    /// `String(1e15)` is `"1e+15"` where Python's `repr` is
    /// `"1000000000000000.0"`. So the digits are taken from Swift and the
    /// *shape* is rebuilt here, by the same exponent rule the Kotlin applies
    /// to `BigDecimal`'s scale. Reading this as "Swift already prints it
    /// right" is the trap: it prints the same number, differently, and a
    /// sheet's cell is the string.
    public static func pythonFloat(_ d: Double) -> String {
        if d.isNaN { return "nan" }
        if d.isInfinite { return d > 0 ? "inf" : "-inf" }
        if d == 0 { return d.sign == .minus ? "-0.0" : "0.0" }
        let sign = d < 0 ? "-" : ""
        guard let (digits, exponent) = significand(of: abs(d)) else { return sign + "0.0" }
        if exponent >= -4, exponent <= 15 {
            return sign + plain(digits, exponent)
        }
        let head = String(digits.first!)
        let tail = String(digits.dropFirst())
        let mantissa = tail.isEmpty ? head : head + "." + tail
        let magnitude = abs(exponent)
        let padded = magnitude < 10 ? "0\(magnitude)" : "\(magnitude)"
        return sign + mantissa + "e" + (exponent < 0 ? "-" : "+") + padded
    }

    /// The significant digits of a positive finite double, and the power of
    /// ten of the first of them: `1234.5` is `("12345", 3)`.
    private static func significand(of value: Double) -> (digits: String, exponent: Int)? {
        let text = String(value)
        var mantissa = text
        var tenth = 0
        if let marker = text.firstIndex(where: { $0 == "e" || $0 == "E" }) {
            mantissa = String(text[text.startIndex..<marker])
            tenth = Int(text[text.index(after: marker)...]) ?? 0
        }
        var raw = ""
        var integerDigits = 0
        var seenPoint = false
        for character in mantissa {
            if character == "." { seenPoint = true; continue }
            guard character.isNumber else { continue }
            raw.append(character)
            if !seenPoint { integerDigits += 1 }
        }
        let leadingZeros = raw.prefix(while: { $0 == "0" }).count
        guard leadingZeros < raw.count else { return nil }
        var digits = String(raw.dropFirst(leadingZeros))
        while digits.count > 1, digits.hasSuffix("0") { digits.removeLast() }
        if digits == "0" { return nil }
        return (digits, integerDigits + tenth - 1 - leadingZeros)
    }

    /// `digits` with a decimal point put after `exponent + 1` of them, always
    /// with a fractional part — Python's `repr` never prints a bare `1`.
    private static func plain(_ digits: String, _ exponent: Int) -> String {
        if exponent < 0 {
            return "0." + String(repeating: "0", count: -exponent - 1) + digits
        }
        let whole = exponent + 1
        if digits.count <= whole {
            return digits + String(repeating: "0", count: whole - digits.count) + ".0"
        }
        let head = String(digits.prefix(whole))
        let tail = String(digits.dropFirst(whole))
        return head + "." + tail
    }

    /// An integer as Python prints it: `"007"` is `7`, `"+5"` is `5`, and a
    /// run of three hundred digits keeps all three hundred.
    ///
    /// Android has `BigInteger` for this; Swift's standard library has no
    /// arbitrary-precision integer, and a sheet really can hold a number
    /// longer than `Int64`. Since the only thing wanted is Python's
    /// *spelling* of the digits that are already there, the digits are
    /// normalised as text instead of parsed: no precision to lose.
    /// `Int64(text)` would turn a twenty-five-digit account number into nil
    /// and then into the raw string, which is almost right and silently
    /// different.
    public static func pythonInteger(_ raw: String) -> String? {
        var digits = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        var sign = ""
        if digits.hasPrefix("-") { sign = "-"; digits.removeFirst() } else if digits.hasPrefix("+") {
            digits.removeFirst()
        }
        guard !digits.isEmpty, digits.allSatisfy({ $0.isASCII && $0.isNumber }) else { return nil }
        while digits.count > 1, digits.hasPrefix("0") { digits.removeFirst() }
        return digits == "0" ? "0" : sign + digits
    }
}
