import Foundation

/// A draft as `GET /api/chat/drafts/{id}` gives it, and the decisions the
/// canvas makes about it.
///
/// Ported from `DraftUi.kt:97` (`DraftVersionView`), `:113` (`DraftView`),
/// `:116` (`DraftSaveResult`), `:150`-`:172` (`firstLinesOf`, `sourceLabel`,
/// `changesOnFor`) and `ServerDrafts.kt:52` (the decode itself), read against
/// `chat_drafts.py:456` on 2026-11-02.

// MARK: - the server's shapes

/// One step of a version's changes against the one before: `=` kept, `+`
/// added, `-` removed (`chat_drafts.py:450`).
public struct ChatMoreDraftChange: Equatable, Sendable {
    public let op: String
    public let text: String

    public init(op: String, text: String) {
        self.op = op
        self.text = text
    }

    public var added: Bool { op == "+" }
    public var removed: Bool { op == "-" }
}

/// One version of a draft (`chat_drafts.py:105`, `_version_row`, plus
/// `display` and `changes` from `_with_changes`, `:447`).
public struct ChatMoreDraftVersion: Equatable, Sendable, Decodable, Identifiable {
    public let version: Int
    /// The markdown itself.
    public let body: String
    /// The plain text the changes view diffs (`chat_drafts.py:450`).
    public let display: String
    /// Against the version before; **nil for v1**, which is what the changes
    /// switch is gated on.
    public let changes: [ChatMoreDraftChange]?
    /// `first` | `revision` | `edited` (`chat_drafts.py:110`).
    public let source: String
    public let instruction: String?
    /// A revision written from an older version than the one before it.
    public let basedOn: Int?
    /// Which install wrote it, and what the house calls that install.
    public let deviceID: String?
    public let deviceName: String?
    public let words: Int
    public let author: String?
    public let model: String?
    public let createdAt: String?
    /// The files saved from it, one line each (`ServerDrafts.kt:59`).
    public let saved: [ChatMoreDraftSaved]

    public var id: Int { version }

    public init(
        version: Int = 1,
        body: String = "",
        display: String = "",
        changes: [ChatMoreDraftChange]? = nil,
        source: String = "first",
        instruction: String? = nil,
        basedOn: Int? = nil,
        deviceID: String? = nil,
        deviceName: String? = nil,
        words: Int = 0,
        author: String? = nil,
        model: String? = nil,
        createdAt: String? = nil,
        saved: [ChatMoreDraftSaved] = []
    ) {
        self.version = version
        self.body = body
        self.display = display
        self.changes = changes
        self.source = source
        self.instruction = instruction
        self.basedOn = basedOn
        self.deviceID = deviceID
        self.deviceName = deviceName
        self.words = words
        self.author = author
        self.model = model
        self.createdAt = createdAt
        self.saved = saved
    }

    enum CodingKeys: String, CodingKey {
        case version, body, display, changes, source, instruction, words, author, model, saved
        case basedOn = "based_on"
        case deviceID = "device_id"
        case deviceName = "device_name"
        case createdAt = "created_at"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        version = try values.decodeIfPresent(Int.self, forKey: .version) ?? 1
        body = try values.decodeIfPresent(String.self, forKey: .body) ?? ""
        display = try values.decodeIfPresent(String.self, forKey: .display) ?? ""
        // `[[op, text]]`, a list of two-element lists. A step with anything
        // else in it is dropped rather than failing the version: the switch
        // off is a draft read normally, and a throw is a canvas that says
        // "couldn't read the draft" because one diff step was odd.
        if let raw = try values.decodeIfPresent([[String]].self, forKey: .changes) {
            changes = raw.compactMap { $0.count == 2 ? ChatMoreDraftChange(op: $0[0], text: $0[1]) : nil }
        } else {
            changes = nil
        }
        source = try values.decodeIfPresent(String.self, forKey: .source) ?? "first"
        instruction = try values.decodeIfPresent(String.self, forKey: .instruction)
        basedOn = try values.decodeIfPresent(Int.self, forKey: .basedOn)
        deviceID = try values.decodeIfPresent(String.self, forKey: .deviceID)
        deviceName = try values.decodeIfPresent(String.self, forKey: .deviceName)
        words = try values.decodeIfPresent(Int.self, forKey: .words) ?? 0
        author = try values.decodeIfPresent(String.self, forKey: .author)
        model = try values.decodeIfPresent(String.self, forKey: .model)
        createdAt = try values.decodeIfPresent(String.self, forKey: .createdAt)
        saved = try values.decodeIfPresent([ChatMoreDraftSaved].self, forKey: .saved) ?? []
    }
}

/// One file saved from a version (`chat_drafts.py:600`, the `saved` entry).
public struct ChatMoreDraftSaved: Equatable, Sendable, Decodable {
    public let format: String
    public let relPath: String
    public let template: String?
    public let at: String?
    public let checkOK: Bool
    public let checkLine: String

    public init(
        format: String = "",
        relPath: String = "",
        template: String? = nil,
        at: String? = nil,
        checkOK: Bool = false,
        checkLine: String = ""
    ) {
        self.format = format
        self.relPath = relPath
        self.template = template
        self.at = at
        self.checkOK = checkOK
        self.checkLine = checkLine
    }

    private struct Check: Decodable {
        let ok: Bool
        let line: String

        enum CodingKeys: String, CodingKey { case ok, line }

        init(from decoder: any Decoder) throws {
            let values = try decoder.container(keyedBy: CodingKeys.self)
            ok = try values.decodeIfPresent(Bool.self, forKey: .ok) ?? false
            line = try values.decodeIfPresent(String.self, forKey: .line) ?? ""
        }
    }

    enum CodingKeys: String, CodingKey {
        case format, template, at, check
        case relPath = "rel_path"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        format = try values.decodeIfPresent(String.self, forKey: .format) ?? ""
        relPath = try values.decodeIfPresent(String.self, forKey: .relPath) ?? ""
        template = try values.decodeIfPresent(String.self, forKey: .template)
        at = try values.decodeIfPresent(String.self, forKey: .at)
        let check = try values.decodeIfPresent(Check.self, forKey: .check)
        checkOK = check?.ok ?? false
        checkLine = check?.line ?? ""
    }

    /// The line the canvas lists it as (`ServerDrafts.kt:62`).
    public var line: String {
        ChatMoreStrings.savedEntry(relPath: relPath, check: checkLine)
    }
}

/// A draft's own row (`chat_drafts.py:95`, `_draft_row`).
public struct ChatMoreDraft: Equatable, Sendable, Decodable {
    public let id: Int64
    public let threadID: Int64?
    public let kind: String
    public let template: String?
    public let title: String
    /// The kind's default format, which the server computes (`:95`).
    public let format: String
    public let kindLabel: String?

    public init(
        id: Int64 = 0,
        threadID: Int64? = nil,
        kind: String = "letter",
        template: String? = nil,
        title: String = "",
        format: String = "docx",
        kindLabel: String? = nil
    ) {
        self.id = id
        self.threadID = threadID
        self.kind = kind
        self.template = template
        self.title = title
        self.format = format
        self.kindLabel = kindLabel
    }

    enum CodingKeys: String, CodingKey {
        case id, kind, template, title, format
        case threadID = "thread_id"
        case kindLabel = "kind_label"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        threadID = try values.decodeIfPresent(Int64.self, forKey: .threadID)
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? "letter"
        template = try values.decodeIfPresent(String.self, forKey: .template)
        title = try values.decodeIfPresent(String.self, forKey: .title) ?? ""
        format = try values.decodeIfPresent(String.self, forKey: .format) ?? "docx"
        kindLabel = try values.decodeIfPresent(String.self, forKey: .kindLabel)
    }
}

/// The whole answer of `GET /api/chat/drafts/{id}` (`chat_drafts.py:468`).
public struct ChatMoreDraftView: Equatable, Sendable, Decodable {
    public let draft: ChatMoreDraft
    /// Oldest first, as the route orders them (`chat_drafts.py:132`).
    public let versions: [ChatMoreDraftVersion]
    public let formats: [String]

    public init(
        draft: ChatMoreDraft = ChatMoreDraft(),
        versions: [ChatMoreDraftVersion] = [],
        formats: [String] = []
    ) {
        self.draft = draft
        self.versions = versions
        self.formats = formats
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        draft = try values.decodeIfPresent(ChatMoreDraft.self, forKey: .draft) ?? ChatMoreDraft()
        versions = try values.decodeIfPresent([ChatMoreDraftVersion].self, forKey: .versions) ?? []
        formats = try values.decodeIfPresent([String].self, forKey: .formats) ?? []
    }

    enum CodingKeys: String, CodingKey { case draft, versions, formats }

    public var latest: ChatMoreDraftVersion? { versions.last }

    public func version(_ wanted: Int) -> ChatMoreDraftVersion? {
        versions.first { $0.version == wanted }
    }

    /// The format a save writes by default: the draft's own (the kind's
    /// default, `chat_drafts.py:95`), and the other one as the second button
    /// (`DraftUi.kt:353`).
    public var otherFormat: String { draft.format == "docx" ? "pdf" : "docx" }
}

/// What `POST /api/chat/drafts/{id}/save` answered (`chat_drafts.py:607`).
public struct ChatMoreDraftSaveResult: Equatable, Sendable, Decodable {
    public let relPath: String
    public let name: String
    public let format: String
    public let version: Int
    public let checkOK: Bool
    public let checkLine: String
    public let libraryID: String?

    public init(
        relPath: String = "",
        name: String = "",
        format: String = "",
        version: Int = 0,
        checkOK: Bool = false,
        checkLine: String = "",
        libraryID: String? = nil
    ) {
        self.relPath = relPath
        self.name = name
        self.format = format
        self.version = version
        self.checkOK = checkOK
        self.checkLine = checkLine
        self.libraryID = libraryID
    }

    private struct Check: Decodable {
        let ok: Bool
        let line: String

        enum CodingKeys: String, CodingKey { case ok, line }

        init(from decoder: any Decoder) throws {
            let values = try decoder.container(keyedBy: CodingKeys.self)
            ok = try values.decodeIfPresent(Bool.self, forKey: .ok) ?? false
            line = try values.decodeIfPresent(String.self, forKey: .line) ?? ""
        }
    }

    enum CodingKeys: String, CodingKey {
        case name, format, version, check
        case relPath = "rel_path"
        case libraryID = "library_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        relPath = try values.decodeIfPresent(String.self, forKey: .relPath) ?? ""
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
        format = try values.decodeIfPresent(String.self, forKey: .format) ?? ""
        version = try values.decodeIfPresent(Int.self, forKey: .version) ?? 0
        let check = try values.decodeIfPresent(Check.self, forKey: .check)
        checkOK = check?.ok ?? false
        checkLine = check?.line ?? ""
        libraryID = try values.decodeIfPresent(String.self, forKey: .libraryID)
    }

    /// Where it went, in the words the canvas shows (`ServerDrafts.kt:105`).
    public var whereText: String { ChatMoreStrings.savedHere(relPath) }
}

/// What `POST /api/chat/drafts/{id}/versions` answered: `{"version": {...}}`
/// (`chat_drafts.py:528`), with `unchanged` when the text was the same
/// (`:513`).
struct ChatMoreDraftEditResult: Decodable {
    let version: ChatMoreDraftVersion?
    let unchanged: Bool

    enum CodingKeys: String, CodingKey { case version, unchanged }

    init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        version = try values.decodeIfPresent(ChatMoreDraftVersion.self, forKey: .version)
        unchanged = try values.decodeIfPresent(Bool.self, forKey: .unchanged) ?? false
    }
}

// MARK: - the canvas's decisions

public enum ChatMoreDraftRules {

    /// Changes are on for a version newer than the newest one seen here, and
    /// **never for v1**, which has none (`DraftUi.kt:166`).
    public static func changesOn(version: Int, seen: Int) -> Bool {
        version > 1 && version > seen
    }

    /// Which version a canvas opens at: the one the card named when the draft
    /// still has it, else the newest (`DraftUi.kt:317`).
    ///
    /// A card for v2 of a draft now at v3 opens **v2**, not the latest: the
    /// reply the card sits under is about v2, and opening v3 would show words
    /// the reply never wrote.
    public static func opensAt(_ view: ChatMoreDraftView, asked: Int?) -> Int {
        if let asked, view.version(asked) != nil { return asked }
        return view.latest?.version ?? 1
    }

    /// Which version this is, in words (`DraftUi.kt:157`).
    ///
    /// "edited by you" only on the install that made the edit; anywhere else
    /// the device's name (`ServerDrafts.kt:73-75`).
    public static func sourceLabel(
        _ version: ChatMoreDraftVersion?,
        thisDeviceID: String?
    ) -> String {
        guard let version else { return "" }
        switch version.source {
        case "edited":
            guard let elsewhere = editedElsewhere(version, thisDeviceID: thisDeviceID) else {
                return ChatMoreStrings.edited
            }
            return ChatMoreStrings.editedOn(elsewhere)
        case "first":
            return ChatMoreStrings.firstDraft
        default:
            // A revision based on the version straight before it says nothing
            // about where it came from, as the Kotlin's `takeIf` does.
            let basedOn = version.basedOn.flatMap { $0 != version.version - 1 ? $0 : nil }
            return ChatMoreStrings.revisionLabel(instruction: version.instruction, basedOn: basedOn)
        }
    }

    /// The name of the device a hand edit was made on, or nil when it was
    /// made here (`ServerDrafts.kt:74`).
    public static func editedElsewhere(
        _ version: ChatMoreDraftVersion,
        thisDeviceID: String?
    ) -> String? {
        if let mine = thisDeviceID, !mine.isEmpty, version.deviceID == mine { return nil }
        let name = version.deviceName?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !name.isEmpty { return name }
        let id = version.deviceID?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return id.isEmpty ? ChatMoreStrings.anotherDevice : id
    }

    /// The changes switch is live only for a version that **has** changes and
    /// only while nothing is being edited (`DraftUi.kt:440`).
    public static func changesSwitchLive(_ version: ChatMoreDraftVersion?, editing: Bool) -> Bool {
        version?.changes != nil && !editing
    }

    /// The first lines a reader sees (`firstLinesOf`, `DraftUi.kt:150`; the
    /// server's own `first_lines`, `chat_drafts.py:166`).
    public static func firstLines(_ markdown: String, _ count: Int = 3) -> String {
        ChatMoreDraftBlocks.lines(of: markdown).prefix(count).joined(separator: "\n")
    }

    /// The name an exported file goes by (`ServerDrafts.kt:116`).
    ///
    /// The characters a file name cannot hold are replaced by a space and the
    /// result trimmed, exactly as the Kotlin's regex does — a draft called
    /// `Re: tenancy / 2026` must not become a path.
    public static func exportName(title: String, version: Int, format: String) -> String {
        var cleaned = ""
        var lastWasSpace = false
        for character in title {
            if "\\/:*?\"<>|".contains(character) {
                if !lastWasSpace { cleaned.append(" ") }
                lastWasSpace = true
            } else {
                cleaned.append(character)
                lastWasSpace = false
            }
        }
        let trimmed = cleaned.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = trimmed.isEmpty ? ChatMoreStrings.exportFallbackTitle : trimmed
        return "\(name) v\(version).\(format)"
    }
}

/// A draft's markdown as the lines a reader sees.
///
/// The line-producing half of `DraftBlocks.parse` (`DraftBlocks.kt:58`) and
/// `inline` (`:26`), which is all the card's preview and the canvas's plain
/// text need. **The block styles are not ported**: they exist so a template
/// can draw a title differently from a bullet, and the templates are the
/// server's — it renders the .docx and the PDF (`chat_drafts.py:531`). Porting
/// the styles here would be a renderer this app does not have.
public enum ChatMoreDraftBlocks {

    /// The lines of `markdown`, markdown's inline marks dropped, blank lines,
    /// rules and fences skipped.
    public static func lines(of markdown: String) -> [String] {
        var out: [String] = []
        let normalised = markdown
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        for raw in normalised.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(raw).replacingOccurrences(
                of: "\\s+$", with: "", options: .regularExpression
            )
            let stripped = line.trimmingCharacters(in: .whitespaces)
            if stripped.isEmpty { continue }
            if line.hasPrefix("```") || line.trimmingCharacters(in: .whitespaces).hasPrefix("```") { continue }
            if isRule(stripped) { continue }
            if let heading = capture(stripped, Self.heading, group: 2) {
                let text = inline(heading)
                if !text.isEmpty { out.append(text) }
                continue
            }
            if let bullet = capture(line, Self.bullet, group: 1) {
                let text = inline(bullet)
                if !text.isEmpty { out.append(text) }
                continue
            }
            if let bold = capture(stripped, Self.wholeBold, group: 1),
               !bold.contains("**"), !bold.contains("__") {
                let text = inline(bold)
                if !text.isEmpty { out.append(text) }
                continue
            }
            let text = inline(line)
            if !text.isEmpty { out.append(text) }
        }
        return out
    }

    /// `text` without markdown's inline marks, its spaces collapsed
    /// (`DraftBlocks.kt:26`).
    public static func inline(_ text: String) -> String {
        var s = replace(text, Self.link, "$1")
        s = replace(s, Self.code, "$1")
        // Twice, as the Kotlin does: `**a **b** c**` needs two passes.
        for _ in 0..<2 { s = replace(s, Self.bold, "$2") }
        s = replace(s, Self.italic, "$2")
        s = replace(s, Self.escape, "$1")
        s = replace(s, Self.spaces, " ")
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func isRule(_ line: String) -> Bool {
        matches(line, Self.rule)
    }

    // The same patterns as `DraftBlocks.kt:13-23`, as `NSRegularExpression`:
    // ICU takes the lookbehind in the italic pattern, which Swift's own
    // `Regex` engine does not promise to.
    // `nonisolated(unsafe)` with the reason the house rule asks for:
    // `NSRegularExpression` is documented immutable and thread-safe once
    // built, and these are built once and only matched against. The
    // alternative — compiling eleven patterns on every line of every draft —
    // is what a card's three-line preview would pay for a keyword.
    nonisolated(unsafe) private static let heading = regex("^(#{1,6})\\s+(.*?)\\s*#*\\s*$")
    nonisolated(unsafe) private static let bullet = regex("^\\s*(?:[-*+]|\u{2022})\\s+(.*)$")
    nonisolated(unsafe) private static let wholeBold = regex("^(?:\\*\\*|__)(.+?)(?:\\*\\*|__)$")
    nonisolated(unsafe) private static let rule = regex("^\\s*(?:-{3,}|\\*{3,}|_{3,})\\s*$")
    nonisolated(unsafe) private static let link = regex("\\[([^\\]]*)\\]\\([^)]*\\)")
    nonisolated(unsafe) private static let bold = regex("(\\*\\*|__)(.+?)\\1")
    nonisolated(unsafe) private static let italic =
        regex("(?<![A-Za-z0-9_*])([*_])(?![ \\t])(.+?)(?<![ \\t])\\1(?![A-Za-z0-9_*])")
    nonisolated(unsafe) private static let code = regex("`([^`]*)`")
    nonisolated(unsafe) private static let escape = regex("\\\\([\\\\`*_{}\\[\\]()#+\\-.!])")
    nonisolated(unsafe) private static let spaces = regex("[ \\t\u{00a0}]+")

    private static func regex(_ pattern: String) -> NSRegularExpression? {
        try? NSRegularExpression(pattern: pattern)
    }

    private static func matches(_ text: String, _ pattern: NSRegularExpression?) -> Bool {
        guard let pattern else { return false }
        let whole = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let first = pattern.firstMatch(in: text, range: whole) else { return false }
        return first.range == whole
    }

    /// The `group`th capture of a whole-string match, or nil.
    private static func capture(_ text: String, _ pattern: NSRegularExpression?, group: Int) -> String? {
        guard let pattern else { return nil }
        let whole = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let match = pattern.firstMatch(in: text, range: whole), match.range == whole,
              group < match.numberOfRanges,
              let range = Range(match.range(at: group), in: text) else { return nil }
        return String(text[range])
    }

    private static func replace(_ text: String, _ pattern: NSRegularExpression?, _ template: String) -> String {
        guard let pattern else { return text }
        return pattern.stringByReplacingMatches(
            in: text,
            range: NSRange(text.startIndex..<text.endIndex, in: text),
            withTemplate: template
        )
    }
}
