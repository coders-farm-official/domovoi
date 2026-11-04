import Foundation

/// A `.csv` read on the phone, by the rules of the server's Python `csv`
/// module — `CsvSheet.kt`.
///
/// `csv.reader` with the excel dialect, which is what
/// `_read_sheet_grid` opens a `.csv` with (`documents.py:987-995`):
/// comma-separated, `"` quotes a field, `""` inside quotes is one quote, a
/// newline inside quotes stays in the field, a UTF-8 BOM is dropped and bad
/// bytes become U+FFFD. A blank line is an empty record, as Python's reader
/// has it.
///
/// Two things the server does that this matches exactly, because a grid that
/// disagreed with the one `/sheet` sends for the same file would make the
/// fallback visible as a bug:
///
/// * **cells are values, never formulas.** `{"v": cell, "f": None}` for every
///   one (`:993`), so a cell whose text starts with `=` is the text `=…`.
///   ``DocumentsSheetCell/display`` shows it either way, and
///   ``DocumentsPath/sheetBody(rows:deviceID:)`` is what decides it is a
///   formula on the way back — which is the server's rule for the save, not
///   for the read.
/// * **rows are not trimmed.** A `.csv` row is as wide as the file wrote it,
///   trailing empty fields included (the `.xlsx` path is the one that trims,
///   `:1005`). ``DocumentsSheetGrid/padded`` squares the grid afterwards for
///   both.
///
/// ### What is not ported
///
/// `write`, `line`, `quote` and `newlineOf` (`CsvSheet.kt:79-120`) — the
/// writer. See ``DocumentsSheetParser``: there is nothing on this phone, and
/// no route from it, that a written `.csv` could go to.
enum DocumentsCsvSheet {

    /// The file's text: UTF-8 with replacement, less a leading BOM.
    static func decode(_ bytes: [UInt8]) -> String {
        var text = String(decoding: bytes, as: UTF8.self)
        if text.hasPrefix("\u{FEFF}") { text.removeFirst() }
        return text
    }

    /// Python's reader state machine — `CsvSheet.St` (`:26`), state for
    /// state.
    private enum State {
        case startRecord
        case startField
        case inField
        case inQuoted
        case quoteInQuoted
    }

    /// Every record in `text`, unbounded.
    ///
    /// Walked over `unicodeScalars` with an explicit index rather than over
    /// `[Character]`: a `.csv` is read at up to
    /// ``DocumentsSheetLimits/maxFileBytes``, and `[Character]` is sixteen
    /// bytes per element. Scalars rather than UTF-16 units is the one
    /// difference from the Kotlin, and it changes nothing: the five
    /// characters the machine tests for are ASCII, and no scalar outside the
    /// BMP equals any of them.
    static func parse(_ text: String, delimiter: Unicode.Scalar = ",") -> [[String]] {
        let view = text.unicodeScalars
        var rows: [[String]] = []
        var row: [String] = []
        var field = ""
        var state = State.startRecord

        func saveField() {
            row.append(field)
            field = ""
        }
        func endRecord() {
            rows.append(row)
            row = []
            state = .startRecord
        }

        var at = view.startIndex
        while at < view.endIndex {
            let scalar = view[at]
            let newline = scalar == "\n" || scalar == "\r"
            // CR LF is one line end.
            let afterOne = view.index(after: at)
            let twoWide = scalar == "\r" && afterOne < view.endIndex && view[afterOne] == "\n"
            let wasQuoted = state == .inQuoted
            var reread = false
            switch state {
            case .startRecord:
                if newline {
                    endRecord()
                } else {
                    state = .startField
                    // Kotlin's `continue`: the same character is processed
                    // again, in the new state, without advancing.
                    reread = true
                }
            case .startField:
                if newline {
                    saveField()
                    endRecord()
                } else if scalar == "\"" {
                    state = .inQuoted
                } else if scalar == delimiter {
                    saveField()
                } else {
                    field.unicodeScalars.append(scalar)
                    state = .inField
                }
            case .inField:
                if newline {
                    saveField()
                    endRecord()
                } else if scalar == delimiter {
                    saveField()
                    state = .startField
                } else {
                    field.unicodeScalars.append(scalar)
                }
            case .inQuoted:
                if scalar == "\"" {
                    state = .quoteInQuoted
                } else {
                    field.unicodeScalars.append(scalar)
                }
            case .quoteInQuoted:
                if scalar == "\"" {
                    field.unicodeScalars.append("\"")
                    state = .inQuoted
                } else if scalar == delimiter {
                    saveField()
                    state = .startField
                } else if newline {
                    saveField()
                    endRecord()
                } else {
                    field.unicodeScalars.append(scalar)
                    state = .inField
                }
            }
            if reread { continue }
            at = (newline && !wasQuoted && twoWide) ? view.index(after: afterOne) : afterOne
        }
        if state != .startRecord {
            saveField()
            rows.append(row)
        }
        return rows
    }

    /// The editor's grid for `bytes`, bounded to the server's window —
    /// `CsvSheet.read` (`:76`).
    static func read(_ bytes: [UInt8]) throws -> [[DocumentsSheetCell?]] {
        parse(decode(bytes))
            .prefix(DocumentsSheetLimits.maxRows)
            .map { row -> [DocumentsSheetCell?] in
                row.prefix(DocumentsSheetLimits.maxCols).map { DocumentsSheetCell(v: $0) }
            }
    }
}
