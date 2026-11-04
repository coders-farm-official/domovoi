import Foundation

/// A very small XML tag scanner for the spreadsheet reader
/// (``DocumentsXlsxSheet``).
///
/// `MiniXml.kt`, with two deliberate differences, both of them stated here
/// because each one is a behaviour change and not a translation.
///
/// ### 1. It scans **bytes**, not characters
///
/// Android walks a `String` by UTF-16 index. Here the part is a `[UInt8]` of
/// its UTF-8 bytes and every offset is a byte offset. Nothing about XML
/// syntax is lost: `<`, `>`, `/`, `=`, `"`, `'`, `&`, `;` and XML's four
/// whitespace characters are all ASCII, and a UTF-8 lead or continuation byte
/// is never one of them, so a tag boundary found in bytes is the same
/// boundary found in characters. What it buys is the reason to do it: a
/// worksheet part of a few megabytes becomes a `[UInt8]` of a few megabytes,
/// where `[Character]` is sixteen bytes a character and `String.Index`
/// arithmetic over a multi-megabyte part is O(n) per step. Text is decoded
/// from the byte range at the moment it is wanted
/// (``textBetween(_:from:to:)``), which is once per cell.
///
/// One honest narrowing goes with it: whitespace inside a tag is XML's four
/// (space, tab, CR, LF) rather than `Char.isWhitespace()`'s Unicode set.
/// XML 1.0 allows exactly those four between a tag's parts, so a document
/// Android's looser test accepts and this one does not was never well-formed
/// — and it is refused rather than mis-scanned (see 2).
///
/// ### 2. A tag that will not read is a **refusal**
///
/// `MiniXml.tags` is a `Sequence` that `break`s when `readTag` returns null,
/// so a truncated part, an attribute whose quote never closes, or a comment
/// with no `-->` ends the scan silently — and `XlsxSheet.read` then returns
/// the rows it had got to, which is a grid that looks like a sheet with some
/// empty cells at the bottom. The next thing a phone does with that grid is
/// save it, and the save sends the whole grid, so the unparsed half gets
/// written back as blanks.
///
/// So every entry point here `throws`
/// ``DocumentsSheetTrouble/malformed(_:)`` instead, and the reader has no
/// path that produces a partial sheet. This is the half of the port the
/// task's third package test is about: *a malformed XML that must be refused
/// rather than half-parsed.*
///
/// Not a parser, in both ports: it reports each tag with its exact offsets,
/// which is what lets the writer (not ported — see
/// ``DocumentsSheetParser``) patch a few cells and copy every other byte
/// through. Comments, processing instructions, DOCTYPE and CDATA are skipped
/// as opaque spans. Attribute values come back unescaped.
enum DocumentsMiniXml {

    enum Kind: Equatable, Sendable {
        case open
        case close
        case empty
    }

    struct Tag: Sendable {

        /// Qualified name as written — `c`, `x:c`, `r:id`.
        let qname: String

        let kind: Kind

        /// Attributes by qualified name, unescaped, in document order. An
        /// array of pairs rather than a dictionary, as Android's
        /// `LinkedHashMap` is ordered too: ``attr(_:)`` needs to try an exact
        /// qualified name first and then any prefix, which a plain dictionary
        /// cannot answer.
        let attrs: [(name: String, value: String)]

        /// Byte offset of `<`.
        let start: Int

        /// Byte offset just past `>`.
        let end: Int

        /// Local part of the name (`c` for `x:c`).
        var name: String {
            guard let colon = qname.lastIndex(of: ":") else { return qname }
            return String(qname[qname.index(after: colon)...])
        }

        /// Attribute by exact qualified name, else by local name — the
        /// second half is how `r:id` answers a question about `id`.
        func attr(_ wanted: String) -> String? {
            for pair in attrs where pair.name == wanted { return pair.value }
            for pair in attrs where pair.name.contains(":") {
                if let colon = pair.name.lastIndex(of: ":"),
                   pair.name[pair.name.index(after: colon)...] == wanted {
                    return pair.value
                }
            }
            return nil
        }
    }

    // MARK: - scanning

    /// XML 1.0's whitespace, and only it. See the type's note.
    private static func isSpace(_ byte: UInt8) -> Bool {
        byte == 0x20 || byte == 0x09 || byte == 0x0A || byte == 0x0D
    }

    private static func matches(_ xml: [UInt8], at index: Int, _ literal: [UInt8]) -> Bool {
        guard index >= 0, index + literal.count <= xml.count else { return false }
        for offset in 0..<literal.count where xml[index + offset] != literal[offset] { return false }
        return true
    }

    private static func find(_ xml: [UInt8], _ literal: [UInt8], from: Int, to: Int) -> Int? {
        guard !literal.isEmpty, from >= 0 else { return nil }
        var index = from
        while index + literal.count <= to {
            if matches(xml, at: index, literal) { return index }
            index += 1
        }
        return nil
    }

    private static func findByte(_ xml: [UInt8], _ byte: UInt8, from: Int, to: Int) -> Int? {
        var index = max(from, 0)
        while index < to {
            if xml[index] == byte { return index }
            index += 1
        }
        return nil
    }

    /// The next tag at or after `from`, or nil when there is none before
    /// `to`.
    ///
    /// Android's `tags(...).firstOrNull()`, which is how that sequence is
    /// used everywhere but two loops — the laziness matters, because the
    /// worksheet scan asks this question once per row and once per cell and
    /// building the whole tag list first would be quadratic.
    static func nextTag(_ xml: [UInt8], from: Int = 0, to: Int? = nil) throws -> Tag? {
        let limit = min(to ?? xml.count, xml.count)
        var index = max(from, 0)
        while index < limit {
            guard let lessThan = findByte(xml, 0x3C, from: index, to: limit) else { return nil }
            if matches(xml, at: lessThan, Array("<!--".utf8)) {
                guard let close = find(xml, Array("-->".utf8), from: lessThan + 4, to: limit) else {
                    throw DocumentsSheetTrouble.malformed("a comment that never ends")
                }
                index = close + 3
            } else if matches(xml, at: lessThan, Array("<![CDATA[".utf8)) {
                guard let close = find(xml, Array("]]>".utf8), from: lessThan + 9, to: limit) else {
                    throw DocumentsSheetTrouble.malformed("a CDATA section that never ends")
                }
                index = close + 3
            } else if matches(xml, at: lessThan, Array("<?".utf8)) {
                guard let close = find(xml, Array("?>".utf8), from: lessThan + 2, to: limit) else {
                    throw DocumentsSheetTrouble.malformed("a processing instruction that never ends")
                }
                index = close + 2
            } else if matches(xml, at: lessThan, Array("<!".utf8)) {
                guard let close = findByte(xml, 0x3E, from: lessThan + 2, to: limit) else {
                    throw DocumentsSheetTrouble.malformed("a declaration that never ends")
                }
                index = close + 1
            } else {
                return try readTag(xml, lessThan)
            }
        }
        return nil
    }

    /// The next tag at or after `from` that `predicate` accepts.
    static func firstTag(
        _ xml: [UInt8],
        from: Int = 0,
        to: Int? = nil,
        where predicate: (Tag) -> Bool
    ) throws -> Tag? {
        var index = max(from, 0)
        let limit = min(to ?? xml.count, xml.count)
        while let tag = try nextTag(xml, from: index, to: limit) {
            if predicate(tag) { return tag }
            // A zero-width step is impossible — `end` is always past `start`
            // — but the guard is cheap and a scanner that could not advance
            // would hang the editor rather than fail it.
            index = max(tag.end, index + 1)
        }
        return nil
    }

    /// One tag, starting at the `<` at `lessThan`.
    ///
    /// Android returns null for every shape below; each one is a
    /// ``DocumentsSheetTrouble/malformed(_:)`` here instead.
    private static func readTag(_ xml: [UInt8], _ lessThan: Int) throws -> Tag {
        var at = lessThan + 1
        let closing = at < xml.count && xml[at] == 0x2F
        if closing { at += 1 }
        let nameStart = at
        while at < xml.count, !isSpace(xml[at]), xml[at] != 0x3E, xml[at] != 0x2F { at += 1 }
        let qname = Self.string(xml, nameStart, at)
        var attrs: [(name: String, value: String)] = []
        while at < xml.count {
            while at < xml.count, isSpace(xml[at]) { at += 1 }
            guard at < xml.count else { break }
            if xml[at] == 0x3E {
                return Tag(
                    qname: qname,
                    kind: closing ? .close : .open,
                    attrs: attrs,
                    start: lessThan,
                    end: at + 1
                )
            }
            if xml[at] == 0x2F, at + 1 < xml.count, xml[at + 1] == 0x3E {
                return Tag(qname: qname, kind: .empty, attrs: attrs, start: lessThan, end: at + 2)
            }
            // The attribute's name.
            let nameAt = at
            while at < xml.count, xml[at] != 0x3D, !isSpace(xml[at]), xml[at] != 0x3E, xml[at] != 0x2F {
                at += 1
            }
            let aname = Self.string(xml, nameAt, at)
            while at < xml.count, isSpace(xml[at]) { at += 1 }
            if at < xml.count, xml[at] == 0x3D {
                at += 1
                while at < xml.count, isSpace(xml[at]) { at += 1 }
                guard at < xml.count else {
                    throw DocumentsSheetTrouble.malformed("an attribute with no value")
                }
                let quote = xml[at]
                if quote == 0x22 || quote == 0x27 {
                    guard let close = findByte(xml, quote, from: at + 1, to: xml.count) else {
                        throw DocumentsSheetTrouble.malformed("an attribute whose quote never closes")
                    }
                    attrs.append((aname, unescape(Self.string(xml, at + 1, close))))
                    at = close + 1
                } else {
                    let valueAt = at
                    while at < xml.count, !isSpace(xml[at]), xml[at] != 0x3E { at += 1 }
                    attrs.append((aname, unescape(Self.string(xml, valueAt, at))))
                }
            } else if aname.isEmpty {
                at += 1   // a stray character; step over it, as Android does
            } else {
                attrs.append((aname, ""))
            }
        }
        throw DocumentsSheetTrouble.malformed("a tag that never closes")
    }

    /// A byte range as text. Invalid UTF-8 becomes U+FFFD rather than nil:
    /// a damaged *name* is a tag that will not match anything, which the
    /// reader already handles, and refusing the whole file over one bad byte
    /// in one cell's text would be stricter than the server, which decodes
    /// the same part with Python's replacement behaviour.
    static func string(_ xml: [UInt8], _ from: Int, _ to: Int) -> String {
        guard from < to, from >= 0, to <= xml.count else { return "" }
        return String(decoding: xml[from..<to], as: UTF8.self)
    }

    // MARK: - entities

    /// Decode the five predefined entities and numeric character references —
    /// `MiniXml.unescape` (`:118`).
    static func unescape(_ text: String) -> String {
        guard text.contains("&") else { return text }
        var out = ""
        out.reserveCapacity(text.count)
        var index = text.startIndex
        while index < text.endIndex {
            let character = text[index]
            if character == "&",
               let semicolon = text[text.index(after: index)...].firstIndex(of: ";") {
                let entity = String(text[text.index(after: index)..<semicolon])
                if let replacement = Self.entity(entity) {
                    out += replacement
                    index = text.index(after: semicolon)
                    continue
                }
            }
            out.append(character)
            index = text.index(after: index)
        }
        return out
    }

    private static func entity(_ name: String) -> String? {
        switch name {
        case "lt": return "<"
        case "gt": return ">"
        case "amp": return "&"
        case "quot": return "\""
        case "apos": return "'"
        default: break
        }
        if name.hasPrefix("#x") || name.hasPrefix("#X") {
            return codePoint(Int(name.dropFirst(2), radix: 16))
        }
        if name.hasPrefix("#") {
            return codePoint(Int(name.dropFirst()))
        }
        return nil
    }

    /// The text for a numeric character reference, or nil when `value` is not
    /// a code point — unparsable, negative, past U+10FFFF, or a lone
    /// surrogate. The reference is then left as written.
    ///
    /// `Character.toChars` throws for those on Android and the comment there
    /// says a crafted `.xlsx` must not throw out of the reader;
    /// `Unicode.Scalar(_:)` is the failable initializer that answers the same
    /// question, and it refuses the surrogate range itself.
    private static func codePoint(_ value: Int?) -> String? {
        guard let value, value >= 0, value <= 0x10FFFF, let scalar = Unicode.Scalar(value) else {
            return nil
        }
        return String(Character(scalar))
    }

    // MARK: - elements

    /// The byte offset just past the closing tag of the element `open`
    /// starts, counting nested elements of the same name — `elementEnd`
    /// (`:185`).
    ///
    /// For an empty element, its own end. For one that is never closed,
    /// Android returns the end of the document; here that is a
    /// ``DocumentsSheetTrouble/malformed(_:)``, for the reason in the type's
    /// note — an unclosed `<sheetData>` is a truncated worksheet, and the
    /// rows before the truncation are not the file.
    static func elementEnd(_ xml: [UInt8], _ open: Tag) throws -> Int {
        if open.kind == .empty { return open.end }
        var depth = 0
        var index = open.end
        while let tag = try nextTag(xml, from: index) {
            if tag.qname == open.qname {
                switch tag.kind {
                case .open: depth += 1
                case .close:
                    if depth == 0 { return tag.end }
                    depth -= 1
                case .empty: break
                }
            }
            index = max(tag.end, index + 1)
        }
        throw DocumentsSheetTrouble.malformed("<\(open.qname)> is never closed")
    }

    /// Character data between two offsets with tags removed, CDATA kept and
    /// entities decoded — `textBetween` (`:201`).
    static func textBetween(_ xml: [UInt8], from: Int, to: Int) throws -> String {
        let limit = min(to, xml.count)
        var out = ""
        var index = max(from, 0)
        while index < limit {
            guard let lessThan = findByte(xml, 0x3C, from: index, to: limit) else {
                out += unescape(Self.string(xml, index, limit))
                break
            }
            out += unescape(Self.string(xml, index, lessThan))
            if matches(xml, at: lessThan, Array("<![CDATA[".utf8)) {
                guard let close = find(xml, Array("]]>".utf8), from: lessThan + 9, to: limit) else {
                    throw DocumentsSheetTrouble.malformed("a CDATA section that never ends")
                }
                out += Self.string(xml, lessThan + 9, min(close, limit))
                index = close + 3
            } else if matches(xml, at: lessThan, Array("<!--".utf8)) {
                guard let close = find(xml, Array("-->".utf8), from: lessThan + 4, to: limit) else {
                    throw DocumentsSheetTrouble.malformed("a comment that never ends")
                }
                index = close + 3
            } else {
                guard let greaterThan = findByte(xml, 0x3E, from: lessThan, to: limit) else {
                    throw DocumentsSheetTrouble.malformed("a tag that never closes")
                }
                index = greaterThan + 1
            }
        }
        return out
    }
}
