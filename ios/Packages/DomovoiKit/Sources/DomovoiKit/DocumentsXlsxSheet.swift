import Compression
import Foundation

/// An `.xlsx` read on the phone, without a spreadsheet library — the reading
/// half of `XlsxSheet.kt`.
///
/// What it answers with is what the server's openpyxl read gives the same
/// editor (`_read_sheet_grid`, `documents.py:996-1013`): the **first**
/// worksheet, a 1000 × 60 window, formulas as `=…` with shared formulas
/// translated to each cell and **no cached value** (`data_only=False`, and
/// `_cell_model` sends `f` with `v: None`), numbers as Python prints them,
/// booleans as `True`/`False`, date-formatted numbers as a Python `datetime`
/// prints, trailing empty cells and rows trimmed.
///
/// ### Why there is a zip reader in here
///
/// An `.xlsx` is a zip of XML parts. Android gets `ZipInputStream` from the
/// JVM; Foundation has no zip reader and `Compression` is the raw codec, so
/// the archive's central directory is read here (``parts(of:)``) and each
/// part inflated with `COMPRESSION_ZLIB`, which is Apple's name for raw
/// DEFLATE — the format a zip's method 8 entry holds. Apple's own framework
/// rather than a dependency, so there is no licence decision
/// (NIGHT-RULES §1).
///
/// The sizes come from the **central directory**, not from each local header,
/// because a file written with a data descriptor leaves the local header's
/// sizes as zero — the shape `openpyxl` and every other reader takes from the
/// directory too.
///
/// ### What is not ported
///
/// `patch` and everything under it (`:602-824`) — the writer, which rewrites
/// changed cells inside the worksheet XML and rezips — and `readForChat`
/// (`:527`). Both are explained in ``DocumentsSheetParser``: neither has
/// anything on this phone to call it.
enum DocumentsXlsxSheet {

    // MARK: - the zip

    private struct Part {
        let name: String
        let bytes: [UInt8]
    }

    private static func u16(_ bytes: [UInt8], _ at: Int) -> Int? {
        guard at >= 0, at + 2 <= bytes.count else { return nil }
        return Int(bytes[at]) | Int(bytes[at + 1]) << 8
    }

    private static func u32(_ bytes: [UInt8], _ at: Int) -> Int? {
        guard at >= 0, at + 4 <= bytes.count else { return nil }
        return Int(bytes[at]) | Int(bytes[at + 1]) << 8 | Int(bytes[at + 2]) << 16 | Int(bytes[at + 3]) << 24
    }

    /// Raw DEFLATE, to a size the directory already told us.
    ///
    /// The one-shot `compression_decode_buffer` rather than a stream: the
    /// uncompressed size is known before the call, so there is nothing to
    /// grow, and a part that does not inflate to exactly that size is a
    /// damaged archive rather than a short read.
    private static func inflate(_ input: [UInt8], to size: Int) -> [UInt8]? {
        if size == 0 { return [] }
        guard !input.isEmpty else { return nil }
        var out = [UInt8](repeating: 0, count: size)
        let written = input.withUnsafeBufferPointer { source in
            out.withUnsafeMutableBufferPointer { destination in
                compression_decode_buffer(
                    destination.baseAddress!, size,
                    source.baseAddress!, input.count,
                    nil, COMPRESSION_ZLIB
                )
            }
        }
        return written == size ? out : nil
    }

    /// Every entry of the archive, inflated, with the running total bounded
    /// by ``DocumentsSheetLimits/maxUnzippedBytes``.
    ///
    /// The bound is checked **before** each part is inflated, from the size
    /// the directory declares, so a small archive that claims to unpack to
    /// gigabytes is refused without allocating any of it. (Android counts
    /// while inflating because it streams; this knows the sizes up front,
    /// which is strictly earlier.)
    private static func parts(of data: [UInt8]) throws -> [Part] {
        let eocdSignature: [UInt8] = [0x50, 0x4B, 0x05, 0x06]
        // The comment field is at most 65535 bytes, so the record starts no
        // more than 65557 bytes from the end.
        var eocd: Int?
        var at = data.count - 22
        let floor = max(0, data.count - 65557 - 22)
        while at >= floor {
            if at >= 0, at + 4 <= data.count,
               data[at] == eocdSignature[0], data[at + 1] == eocdSignature[1],
               data[at + 2] == eocdSignature[2], data[at + 3] == eocdSignature[3] {
                eocd = at
                break
            }
            at -= 1
        }
        guard let eocd else {
            throw DocumentsSheetTrouble.malformed("not an xlsx file (not a zip)")
        }
        guard let count = u16(data, eocd + 10),
              let directoryAt = u32(data, eocd + 16) else {
            throw DocumentsSheetTrouble.malformed("the archive's directory is truncated")
        }
        guard count != 0xFFFF, directoryAt != 0xFFFF_FFFF else {
            // Zip64. A household workbook is never one, and a reader that
            // guessed at the 64-bit fields would be reading a format it had
            // not been written for.
            throw DocumentsSheetTrouble.malformed("a zip64 archive, which this phone does not read")
        }
        var out: [Part] = []
        var inflated = 0
        var cursor = directoryAt
        for _ in 0..<count {
            guard cursor + 46 <= data.count,
                  data[cursor] == 0x50, data[cursor + 1] == 0x4B,
                  data[cursor + 2] == 0x01, data[cursor + 3] == 0x02,
                  let method = u16(data, cursor + 10),
                  let compressed = u32(data, cursor + 20),
                  let uncompressed = u32(data, cursor + 24),
                  let nameLength = u16(data, cursor + 28),
                  let extraLength = u16(data, cursor + 30),
                  let commentLength = u16(data, cursor + 32),
                  let localAt = u32(data, cursor + 42) else {
                throw DocumentsSheetTrouble.malformed("the archive's directory is truncated")
            }
            guard cursor + 46 + nameLength + extraLength + commentLength <= data.count else {
                throw DocumentsSheetTrouble.malformed("the archive's directory is truncated")
            }
            let name = DocumentsMiniXml.string(data, cursor + 46, cursor + 46 + nameLength)
            inflated += uncompressed
            guard inflated <= DocumentsSheetLimits.maxUnzippedBytes else {
                throw DocumentsSheetTrouble.tooLarge(
                    DocumentsStrings.sheetUnpacksTooBig(
                        DocumentsSheetLimits.maxUnzippedBytes / (1024 * 1024)
                    )
                )
            }
            // The local header, for where the bytes actually start: its name
            // and extra fields may be a different length from the
            // directory's.
            guard localAt + 30 <= data.count,
                  data[localAt] == 0x50, data[localAt + 1] == 0x4B,
                  data[localAt + 2] == 0x03, data[localAt + 3] == 0x04,
                  let localName = u16(data, localAt + 26),
                  let localExtra = u16(data, localAt + 28) else {
                throw DocumentsSheetTrouble.malformed("an entry's header is not where the archive says")
            }
            let from = localAt + 30 + localName + localExtra
            guard from >= 0, from + compressed <= data.count else {
                throw DocumentsSheetTrouble.malformed("an entry runs past the end of the file")
            }
            let raw = Array(data[from..<(from + compressed)])
            switch method {
            case 0:
                guard compressed == uncompressed else {
                    throw DocumentsSheetTrouble.malformed("a stored entry with two different sizes")
                }
                out.append(Part(name: name, bytes: raw))
            case 8:
                guard let bytes = inflate(raw, to: uncompressed) else {
                    throw DocumentsSheetTrouble.malformed("an entry that does not decompress")
                }
                out.append(Part(name: name, bytes: bytes))
            default:
                throw DocumentsSheetTrouble.malformed("an entry compressed in a way this phone does not read")
            }
            cursor += 46 + nameLength + extraLength + commentLength
        }
        guard !out.isEmpty else {
            throw DocumentsSheetTrouble.malformed("not an xlsx file (empty or not a zip)")
        }
        return out
    }

    private static func part(_ parts: [Part], _ name: String) -> [UInt8]? {
        for candidate in parts where candidate.name == name { return candidate.bytes }
        return nil
    }

    // MARK: - the package

    private struct Relationship {
        let id: String
        let type: String
        let target: String
    }

    private static func relationships(_ xml: [UInt8]?) throws -> [Relationship] {
        guard let xml else { return [] }
        var out: [Relationship] = []
        var at = 0
        while let tag = try DocumentsMiniXml.nextTag(xml, from: at) {
            if tag.name == "Relationship", tag.kind != .close {
                out.append(Relationship(
                    id: tag.attr("Id") ?? "",
                    type: tag.attr("Type") ?? "",
                    target: tag.attr("Target") ?? ""
                ))
            }
            at = max(tag.end, at + 1)
        }
        return out
    }

    /// Resolve a relationship target against the directory of the part that
    /// owns it — `resolve` (`:100`).
    private static func resolve(_ baseDir: String, _ target: String) -> String {
        if target.hasPrefix("/") { return String(target.dropFirst()) }
        var parts = baseDir.isEmpty ? [] : baseDir.split(separator: "/").map(String.init)
        for segment in target.split(separator: "/", omittingEmptySubsequences: false) {
            switch segment {
            case "", ".": continue
            case "..": if !parts.isEmpty { parts.removeLast() }
            default: parts.append(String(segment))
            }
        }
        return parts.joined(separator: "/")
    }

    private static func relsPath(of part: String) -> String {
        guard let slash = part.lastIndex(of: "/") else { return "_rels/\(part).rels" }
        let dir = String(part[part.startIndex..<slash])
        let file = String(part[part.index(after: slash)...])
        return "\(dir)/_rels/\(file).rels"
    }

    private static func directory(of part: String) -> String {
        guard let slash = part.lastIndex(of: "/") else { return "" }
        return String(part[part.startIndex..<slash])
    }

    private struct Book {
        let sheetPath: String
        let stylesPath: String?
        let sharedStringsPath: String?
        let date1904: Bool
    }

    private static func book(_ parts: [Part]) throws -> Book {
        let rootRels = try relationships(part(parts, "_rels/.rels"))
        let workbookPath = rootRels.first { $0.type.hasSuffix("/officeDocument") }
            .map { resolve("", $0.target) } ?? "xl/workbook.xml"
        guard let workbook = part(parts, workbookPath) else {
            throw DocumentsSheetTrouble.malformed("not an xlsx file (no workbook)")
        }
        let workbookDir = directory(of: workbookPath)
        let workbookRels = try relationships(part(parts, relsPath(of: workbookPath)))
        guard let firstSheet = try DocumentsMiniXml.firstTag(workbook, where: {
            $0.name == "sheet" && $0.kind != .close
        })?.attr("id") else {
            throw DocumentsSheetTrouble.malformed("the workbook has no sheets")
        }
        guard let sheetRel = workbookRels.first(where: { $0.id == firstSheet }) else {
            throw DocumentsSheetTrouble.malformed("the workbook's first sheet is missing")
        }
        let properties = try DocumentsMiniXml.firstTag(workbook, where: {
            $0.name == "workbookPr" && $0.kind != .close
        })
        let date1904 = properties?.attr("date1904").map { $0 == "1" || $0.lowercased() == "true" } ?? false
        return Book(
            sheetPath: resolve(workbookDir, sheetRel.target),
            stylesPath: workbookRels.first { $0.type.hasSuffix("/styles") }
                .map { resolve(workbookDir, $0.target) },
            sharedStringsPath: workbookRels.first { $0.type.hasSuffix("/sharedStrings") }
                .map { resolve(workbookDir, $0.target) },
            date1904: date1904
        )
    }

    /// Plain text of an `<si>` or `<is>` element: its `<t>` runs, phonetic
    /// runs (`<rPh>`) left out — `richText` (`:150`).
    private static func richText(_ xml: [UInt8], from: Int, to: Int) throws -> String {
        var out = ""
        var skipDepth = 0
        var at = from
        while let tag = try DocumentsMiniXml.nextTag(xml, from: at, to: to) {
            if tag.name == "rPh", tag.kind == .open {
                skipDepth += 1
            } else if tag.name == "rPh", tag.kind == .close {
                skipDepth -= 1
            } else if tag.name == "t", tag.kind == .open {
                let end = try DocumentsMiniXml.elementEnd(xml, tag)
                if skipDepth == 0, let close = lastLessThan(xml, before: end) {
                    out += try DocumentsMiniXml.textBetween(xml, from: tag.end, to: close)
                }
                at = max(end, at + 1)
                continue
            }
            at = max(tag.end, at + 1)
        }
        return out
    }

    /// `xml.lastIndexOf('<', end - 1)` — where the element's closing tag
    /// starts, which is where its text stops.
    private static func lastLessThan(_ xml: [UInt8], before end: Int) -> Int? {
        var at = min(end, xml.count) - 1
        while at >= 0 {
            if xml[at] == 0x3C { return at }
            at -= 1
        }
        return nil
    }

    private static func sharedStrings(_ xml: [UInt8]?) throws -> [String] {
        guard let xml else { return [] }
        var out: [String] = []
        var at = 0
        while let tag = try DocumentsMiniXml.firstTag(xml, from: at, where: {
            $0.name == "si" && $0.kind != .close
        }) {
            if tag.kind == .empty {
                out.append("")
                at = max(tag.end, at + 1)
            } else {
                let end = try DocumentsMiniXml.elementEnd(xml, tag)
                out.append(try richText(xml, from: tag.end, to: end))
                at = max(end, at + 1)
            }
        }
        return out
    }

    // MARK: - number formats (openpyxl's `is_date_format`)

    private static let builtinDateIDs: Set<Int> = [14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47]
    private static let builtinTimedeltaIDs: Set<Int> = [46]

    // `NSRegularExpression` is ICU and immutable once built, and matching on
    // one is documented thread-safe; `nonisolated(unsafe)` with that reason
    // is the house pattern (`ChatMoreDraftModels.swift:508`).
    nonisolated(unsafe) private static let stripPattern =
        try? NSRegularExpression(pattern: #"\[(?!hh?\]|mm?\]|ss?\])[^\]]*\]|"[^"]*"|\\.|_."#)
    nonisolated(unsafe) private static let dateCharsPattern =
        try? NSRegularExpression(pattern: "[dmhysDMHYS]")
    nonisolated(unsafe) private static let timedeltaPattern =
        try? NSRegularExpression(pattern: #"\[(hh?|mm?|ss?)\]"#, options: [.caseInsensitive])

    private static func firstSection(of code: String) -> String {
        code.split(separator: ";", omittingEmptySubsequences: false).first.map(String.init) ?? code
    }

    private static func contains(_ pattern: NSRegularExpression?, _ text: String) -> Bool {
        guard let pattern else { return false }
        return pattern.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil
    }

    /// `isDateFormat` (`:201`): the literal parts of the first section are
    /// stripped, and what is left is a date format if any date letter
    /// survives.
    static func isDateFormat(_ code: String) -> Bool {
        let first = firstSection(of: code)
        guard let stripPattern else { return contains(dateCharsPattern, first) }
        let stripped = stripPattern.stringByReplacingMatches(
            in: first, range: NSRange(first.startIndex..., in: first), withTemplate: ""
        )
        return contains(dateCharsPattern, stripped)
    }

    /// `isTimedeltaFormat` (`:206`): `[h]`, `[mm]`, `[ss]` — an elapsed time,
    /// which Python prints as a `timedelta` and not as a clock.
    static func isTimedeltaFormat(_ code: String) -> Bool {
        contains(timedeltaPattern, firstSection(of: code))
    }

    private struct Styles {
        let dates: Set<Int>
        let timedeltas: Set<Int>
    }

    private static func styles(_ xml: [UInt8]?) throws -> Styles {
        guard let xml else { return Styles(dates: [], timedeltas: []) }
        var custom: [Int: String] = [:]
        var formats: [Int] = []
        var inCellXfs = false
        var at = 0
        while let tag = try DocumentsMiniXml.nextTag(xml, from: at) {
            if tag.name == "numFmt", tag.kind != .close {
                if let id = tag.attr("numFmtId").flatMap({ Int($0) }) {
                    custom[id] = tag.attr("formatCode") ?? ""
                }
            } else if tag.name == "cellXfs" {
                inCellXfs = tag.kind == .open
            } else if inCellXfs, tag.name == "xf", tag.kind != .close {
                formats.append(tag.attr("numFmtId").flatMap { Int($0) } ?? 0)
            }
            at = max(tag.end, at + 1)
        }
        var dates: Set<Int> = []
        var timedeltas: Set<Int> = []
        for (index, id) in formats.enumerated() {
            if let code = custom[id] {
                if isDateFormat(code) { dates.insert(index) }
                if isTimedeltaFormat(code) { timedeltas.insert(index) }
            } else if builtinDateIDs.contains(id) {
                dates.insert(index)
                if builtinTimedeltaIDs.contains(id) { timedeltas.insert(index) }
            }
        }
        return Styles(dates: dates, timedeltas: timedeltas)
    }

    // MARK: - dates, as Python prints them

    private static func two(_ n: Int) -> String { n < 10 ? "0\(n)" : "\(n)" }

    private static func micros(_ value: Int) -> String {
        guard value != 0 else { return "" }
        var digits = "\(value)"
        while digits.count < 6 { digits = "0" + digits }
        return "." + digits
    }

    /// Java's `Math.round`: `floor(x + 0.5)`, which differs from Swift's
    /// `rounded()` (half away from zero) for a negative half — and an
    /// elapsed time really can be negative.
    private static func javaRound(_ value: Double) -> Double { (value + 0.5).rounded(.down) }

    /// Python's `str()` of what openpyxl's `from_excel` returns for `value` —
    /// `excelDateText` (`:245`).
    ///
    /// Three shapes, as that function has them: an elapsed time
    /// (`timedelta`), a time of day alone when the serial is inside the first
    /// day, and a date and time otherwise. The 1900 leap-year bug is the
    /// `value > 0 && value < 60` line, exactly as openpyxl carries it.
    ///
    /// Returns nil for a serial no date can be made of, where Android's
    /// `LocalDateTime.plusDays` would throw: a crafted `.xlsx` must not take
    /// the reader down, and the caller falls back to printing the number.
    static func excelDateText(_ value: Double, date1904: Bool, timedelta: Bool) -> String? {
        guard value.isFinite else { return nil }
        if timedelta {
            guard abs(value) < 1e12 else { return nil }
            var totalMs = Int(javaRound(value * 86_400_000.0))
            let negative = totalMs < 0
            if negative { totalMs = -totalMs }
            let days = totalMs / 86_400_000
            let rest = totalMs % 86_400_000
            let clock = "\(rest / 3_600_000):\(two(rest % 3_600_000 / 60_000)):"
                + two(rest % 60_000 / 1000) + micros(rest % 1000 * 1000)
            let signed = negative ? -days : days
            if days == 0 { return clock }
            return "\(signed) day\(abs(signed) == 1 ? "" : "s"), \(clock)"
        }
        let whole = value.rounded(.down)
        guard abs(whole) < 3_000_000 else { return nil }
        var day = Int(whole)
        var ms = Int(javaRound((value - whole) * 86_400_000.0))
        if ms >= 86_400_000 { day += 1; ms -= 86_400_000 }
        let clock = two(ms / 3_600_000) + ":" + two(ms % 3_600_000 / 60_000) + ":"
            + two(ms % 60_000 / 1000) + micros(ms % 1000 * 1000)
        if value >= 0, value < 1, day == 0 { return clock }
        // The two epochs openpyxl knows, as day numbers from 1970-01-01.
        let epoch = date1904 ? daysFromCivil(1904, 1, 1) : daysFromCivil(1899, 12, 30)
        if !date1904, value > 0, value < 60 { day += 1 }
        let (year, month, dayOfMonth) = civilFromDays(epoch + day)
        guard year >= 1, year <= 9999 else { return nil }
        var yearText = "\(year)"
        while yearText.count < 4 { yearText = "0" + yearText }
        return "\(yearText)-\(two(month))-\(two(dayOfMonth)) \(clock)"
    }

    /// Days from 1970-01-01 for a proleptic-Gregorian date, and back.
    ///
    /// Hinnant's `days_from_civil` / `civil_from_days`, which is the
    /// arithmetic `LocalDateTime` does on Android. Written out rather than
    /// taken from `Calendar`: a `Calendar` answer depends on the phone's time
    /// zone and calendar identifier, and a serial number in a workbook has
    /// neither — a sheet read in Tokyo must show the same date as the same
    /// sheet read in London.
    static func daysFromCivil(_ year: Int, _ month: Int, _ day: Int) -> Int {
        var y = year
        y -= month <= 2 ? 1 : 0
        let era = (y >= 0 ? y : y - 399) / 400
        let yearOfEra = y - era * 400
        let dayOfYear = (153 * (month + (month > 2 ? -3 : 9)) + 2) / 5 + day - 1
        let dayOfEra = yearOfEra * 365 + yearOfEra / 4 - yearOfEra / 100 + dayOfYear
        return era * 146_097 + dayOfEra - 719_468
    }

    static func civilFromDays(_ days: Int) -> (year: Int, month: Int, day: Int) {
        let z = days + 719_468
        let era = (z >= 0 ? z : z - 146_096) / 146_097
        let dayOfEra = z - era * 146_097
        let yearOfEra = (dayOfEra - dayOfEra / 1460 + dayOfEra / 36_524 - dayOfEra / 146_096) / 365
        let year = yearOfEra + era * 400
        let dayOfYear = dayOfEra - (365 * yearOfEra + yearOfEra / 4 - yearOfEra / 100)
        let monthPrime = (5 * dayOfYear + 2) / 153
        let day = dayOfYear - (153 * monthPrime + 2) / 5 + 1
        let month = monthPrime + (monthPrime < 10 ? 3 : -9)
        return (year + (month <= 2 ? 1 : 0), month, day)
    }

    // MARK: - numbers (openpyxl's `_cast_number`, then Python's `str`)

    static func numberText(_ raw: String) -> String {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.contains(".") || trimmed.contains("E") || trimmed.contains("e") {
            guard let value = Double(trimmed) else { return raw }
            return DocumentsSheetParser.pythonFloat(value)
        }
        return DocumentsSheetParser.pythonInteger(trimmed) ?? raw
    }

    // MARK: - shared formulas (openpyxl's `Translator`, simplified)

    nonisolated(unsafe) private static let tokenPattern = try? NSRegularExpression(
        pattern: #""(?:[^"]|"")*"|'(?:[^']|'')*'!?|(\$?)([A-Za-z]{1,3})(\$?)(\d+)(?![\w(])|(\$?)([A-Za-z]{1,3}):(\$?)([A-Za-z]{1,3})(?![\w(])|(\$?)(\d+):(\$?)(\d+)(?![\w(])"#
    )

    /// Shift the relative references of `formula` (with its `=`) by the given
    /// offsets — `translate` (`:292`).
    ///
    /// Written with explicit match walking rather than a replacement
    /// template, for the same reason the Kotlin uses a lambda: the decision
    /// needs the **character before** the match, to tell a cell reference
    /// from the tail of a function name or a defined name.
    static func translate(_ formula: String, dRow: Int, dColumn: Int) -> String {
        guard dRow != 0 || dColumn != 0, let tokenPattern else { return formula }
        let text = formula as NSString
        let whole = NSRange(location: 0, length: text.length)
        var out = ""
        var copied = 0
        for match in tokenPattern.matches(in: formula, range: whole) {
            let range = match.range
            out += text.substring(with: NSRange(location: copied, length: range.location - copied))
            copied = range.location + range.length
            out += shifted(match, in: text, dRow: dRow, dColumn: dColumn)
        }
        out += text.substring(from: copied)
        return out
    }

    private static func shifted(
        _ match: NSTextCheckingResult,
        in text: NSString,
        dRow: Int,
        dColumn: Int
    ) -> String {
        func group(_ index: Int) -> String {
            let range = match.range(at: index)
            return range.location == NSNotFound ? "" : text.substring(with: range)
        }
        let value = text.substring(with: match.range)
        // A name directly preceded by a letter, digit, `_` or `.` is part of a
        // longer identifier, not a reference.
        if match.range.location > 0 {
            let previous = text.substring(
                with: NSRange(location: match.range.location - 1, length: 1)
            )
            if let scalar = previous.unicodeScalars.first,
               CharacterSet.alphanumerics.contains(scalar) || previous == "_" || previous == "." {
                return value
            }
        }
        func column(_ absolute: String, _ letters: String) -> String {
            absolute == "$"
                ? letters
                : DocumentsSheetParser.columnName(
                    max(DocumentsSheetParser.columnIndex(letters) + dColumn, 0)
                )
        }
        if !group(2).isEmpty {
            let row = group(3) == "$"
                ? group(4)
                : "\(max((Int(group(4)) ?? 1) + dRow, 1))"
            return group(1) + column(group(1), group(2)) + group(3) + row
        }
        if !group(6).isEmpty {
            return group(5) + column(group(5), group(6)) + ":" + group(7) + column(group(7), group(8))
        }
        if !group(10).isEmpty {
            func shiftRow(_ absolute: String, _ digits: String) -> String {
                absolute == "$" ? digits : "\(max((Int(digits) ?? 1) + dRow, 1))"
            }
            return group(9) + shiftRow(group(9), group(10)) + ":"
                + group(11) + shiftRow(group(11), group(12))
        }
        // A quoted string or a sheet name.
        return value
    }

    // MARK: - the worksheet scan

    private struct CellSpan {
        let row: Int
        let column: Int
        let open: DocumentsMiniXml.Tag
        let end: Int
    }

    /// Walk every `<c>` of every `<row>` inside `<sheetData>` — `scan`
    /// (`:350`) and the `read` loop that consumes it (`:425-441`), **as one
    /// pass**, without the writer's `prefix` and `dataInnerEnd`.
    ///
    /// ### Why they are one function here
    ///
    /// Android's `scan` returns a list of spans and `read` then walks it.
    /// Each span carries a parsed tag — a name and its attributes — so the
    /// list is heavier than the grid it produces, and a worksheet is allowed
    /// to hold 16 384 columns: a thousand rows of those is sixteen million
    /// spans held at once to build a grid of sixty thousand cells. On a phone
    /// that is the memory the editor would be killed for.
    ///
    /// Scanning and filling in one pass holds exactly one tag at a time, so
    /// the memory is the grid, and the work is proportional to the part's
    /// bytes — which ``parts(of:)`` has already bounded. The cell cap below
    /// is then a backstop against a crafted part rather than a limit a real
    /// sheet can reach.
    ///
    /// Rows past the window stop the walk, as Android's `break` does: rows
    /// are in document order in every writer's output, and `_read_sheet_grid`
    /// bounds the same way (`max_row=_SHEET_MAX_ROWS`).
    private static func fill(
        _ xml: [UInt8],
        strings: [String],
        styles: Styles,
        date1904: Bool
    ) throws -> [[DocumentsSheetCell?]] {
        guard let dataOpen = try DocumentsMiniXml.firstTag(xml, where: {
            $0.name == "sheetData" && $0.kind != .close
        }) else {
            throw DocumentsSheetTrouble.malformed("the worksheet has no sheetData")
        }
        let dataEnd = try DocumentsMiniXml.elementEnd(xml, dataOpen)
        let innerEnd = dataOpen.kind == .empty
            ? dataOpen.end
            : (lastLessThan(xml, before: dataEnd) ?? dataOpen.end)
        // Shared-formula masters, by `si`: the formula and where it sits.
        var masters: [String: (formula: String, row: Int, column: Int)] = [:]
        var grid: [[DocumentsSheetCell?]] = []
        var seen = 0
        var at = dataOpen.end
        var lastRow = -1
        while at < innerEnd {
            guard let tag = try DocumentsMiniXml.nextTag(xml, from: at, to: innerEnd) else { break }
            guard tag.name == "row", tag.kind != .close else {
                at = max(tag.end, at + 1)
                continue
            }
            let row = tag.attr("r").flatMap { Int($0) }.map { $0 - 1 } ?? (lastRow + 1)
            lastRow = row
            if row >= DocumentsSheetLimits.maxRows { break }
            let rowEnd = try DocumentsMiniXml.elementEnd(xml, tag)
            if tag.kind == .open, row >= 0, let rowInnerEnd = lastLessThan(xml, before: rowEnd) {
                var cellAt = tag.end
                var lastColumn = -1
                while cellAt < rowInnerEnd {
                    guard let cell = try DocumentsMiniXml.nextTag(xml, from: cellAt, to: rowInnerEnd) else {
                        break
                    }
                    guard cell.name == "c", cell.kind != .close else {
                        cellAt = max(cell.end, cellAt + 1)
                        continue
                    }
                    let column = cell.attr("r").flatMap { DocumentsSheetParser.cellRef($0)?.column }
                        ?? (lastColumn + 1)
                    lastColumn = column
                    let cellEnd = try DocumentsMiniXml.elementEnd(xml, cell)
                    seen += 1
                    if seen > Self.maxCellsScanned {
                        throw DocumentsSheetTrouble.tooLarge(DocumentsStrings.sheetHasTooManyCells)
                    }
                    let span = CellSpan(row: row, column: column, open: cell, end: cellEnd)
                    // `cellValue` first and the column bound second, in that
                    // order and for a reason: a shared formula's MASTER can
                    // sit past the window's last column, and dropping the
                    // cell before reading it would leave every cell inside
                    // the window that shares its `si` with an untranslated
                    // `=`.
                    if let value = try cellValue(
                        xml, span, strings: strings, styles: styles,
                        date1904: date1904, masters: &masters
                    ), column >= 0, column < DocumentsSheetLimits.maxCols {
                        while grid.count <= row { grid.append([]) }
                        while grid[row].count <= column { grid[row].append(nil) }
                        grid[row][column] = value
                    }
                    cellAt = max(cellEnd, cellAt + 1)
                }
            }
            at = max(rowEnd, at + 1)
        }
        return grid
    }

    /// The backstop on a crafted worksheet: far more cells than the window
    /// can hold, and nothing a household sheet reaches — a thousand rows of
    /// the full 16 384 columns is sixteen million, and this stops at one
    /// twentieth of that having built a grid of at most sixty thousand.
    private static let maxCellsScanned = 800_000

    private static func child(
        _ xml: [UInt8],
        _ cell: CellSpan,
        _ name: String
    ) throws -> DocumentsMiniXml.Tag? {
        guard cell.open.kind != .empty else { return nil }
        return try DocumentsMiniXml.firstTag(xml, from: cell.open.end, to: cell.end, where: {
            $0.name == name && $0.kind != .close
        })
    }

    private static func innerText(_ xml: [UInt8], _ tag: DocumentsMiniXml.Tag) throws -> String? {
        guard tag.kind != .empty else { return nil }
        let end = try DocumentsMiniXml.elementEnd(xml, tag)
        guard let close = lastLessThan(xml, before: end) else { return nil }
        return try DocumentsMiniXml.textBetween(xml, from: tag.end, to: close)
    }

    // MARK: - read

    /// The first worksheet as the editor's grid, bounded to the server's
    /// window, trailing empty cells and rows trimmed — `read` (`:414`).
    static func read(_ bytes: [UInt8]) throws -> [[DocumentsSheetCell?]] {
        let archive = try parts(of: bytes)
        let book = try book(archive)
        guard let sheet = part(archive, book.sheetPath) else {
            throw DocumentsSheetTrouble.malformed("the first worksheet is missing")
        }
        let strings = try sharedStrings(book.sharedStringsPath.flatMap { part(archive, $0) })
        let style = try styles(book.stylesPath.flatMap { part(archive, $0) })
        var grid = try fill(sheet, strings: strings, styles: style, date1904: book.date1904)
        // Trim: trailing empty cells per row, then trailing empty rows.
        for index in grid.indices {
            while let last = grid[index].last, last == nil || (last?.v == nil && last?.f == nil) {
                grid[index].removeLast()
            }
        }
        while let last = grid.last, last.isEmpty { grid.removeLast() }
        return grid
    }

    private static func cellValue(
        _ xml: [UInt8],
        _ cell: CellSpan,
        strings: [String],
        styles: Styles,
        date1904: Bool,
        masters: inout [String: (formula: String, row: Int, column: Int)]
    ) throws -> DocumentsSheetCell? {
        let type = cell.open.attr("t") ?? "n"
        let style = cell.open.attr("s").flatMap { Int($0) } ?? 0
        if let f = try child(xml, cell, "f") {
            var formula = "=" + ((try innerText(xml, f)) ?? "")
            if f.attr("t") == "shared" {
                let si = f.attr("si") ?? ""
                if let master = masters[si] {
                    formula = translate(
                        master.formula,
                        dRow: cell.row - master.row,
                        dColumn: cell.column - master.column
                    )
                } else if formula != "=" {
                    masters[si] = (formula, cell.row, cell.column)
                }
            }
            return DocumentsSheetCell(f: formula)
        }
        if type == "inlineStr" {
            guard let inline = try child(xml, cell, "is") else { return nil }
            if inline.kind == .empty { return DocumentsSheetCell(v: "") }
            let end = try DocumentsMiniXml.elementEnd(xml, inline)
            return DocumentsSheetCell(v: try richText(xml, from: inline.end, to: end))
        }
        guard let raw = try child(xml, cell, "v").flatMap({ try innerText(xml, $0) }),
              !raw.isEmpty else { return nil }
        let text: String
        switch type {
        case "s":
            let index = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) ?? -1
            text = index >= 0 && index < strings.count ? strings[index] : ""
        case "b":
            text = (Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0) != 0 ? "True" : "False"
        case "str", "e":
            text = raw
        case "d":
            text = raw.replacingOccurrences(of: "T", with: " ")
        default:
            if styles.dates.contains(style),
               let value = Double(raw.trimmingCharacters(in: .whitespacesAndNewlines)),
               let said = excelDateText(
                   value, date1904: date1904, timedelta: styles.timedeltas.contains(style)
               ) {
                text = said
            } else {
                text = numberText(raw)
            }
        }
        return DocumentsSheetCell(v: text)
    }
}
