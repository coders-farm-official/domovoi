import Foundation

/// The user manual's content: the **connected server's**
/// `/manual-content.json` — the same file the dashboard's `#manual` renders,
/// built by `scripts/manual/build_manual.py` from `scripts/manual/manual.md`
/// and shipped as `web/static/manual-content.json`.
///
/// A port of `android/.../ui/screens/manual/ManualContent.kt` (394 lines,
/// read on 2026-11-02 at `898b3af2`), whose model half this file is. The
/// rules are in ``ManualRules``, the markup in ``ManualMarkup``, the fetch
/// and the per-server copy in ``ManualStore``.
///
/// **The content is served, never bundled.** `ManualRepository.kt:31` reads
/// it from the server with `trackPairing = false`, and the route is the
/// static mount (`web/backend/main.py:757`, `RevalidatingStaticFiles(…,
/// html=True)`) — so it needs no device token, and a server older than the
/// file answers 404. That the mount is `html=True` is the reason
/// ``ManualDoc/parse(_:)`` guards on the schema: an unknown path there comes
/// back as the dashboard's `index.html` with a **200**, and a parser that
/// accepted it would draw half a manual.
///
/// Every field is decoded with `decodeIfPresent ?? default`, the way
/// ``Capabilities/init(from:)`` is, so a JSON `null` in a non-optional field
/// behaves as Kotlin's `coerceInputValues` makes it behave rather than
/// failing the whole document.

/// `MANUAL_PATH` (`ManualContent.kt:26`).
public let manualPath = "/manual-content.json"

/// `MANUAL_SCHEMA_PREFIX` (`ManualContent.kt:27`). The shipped file is
/// `domovoi-manual/2`.
public let manualSchemaPrefix = "domovoi-manual/"

/// One `labels` entry. The object is free-form in the file: most entries are
/// strings, four (`preview`, `admin`, `plugin`, `pending`) are
/// `{text, means}` and one (`net`) is `{no, degraded, yes}` — measured
/// against `web/static/manual-content.json`, not assumed.
///
/// Kotlin keeps `labels` as a raw `JsonObject` and reaches into it with
/// `jsonPrimitive`/`jsonObject` inside a `runCatching`
/// (`ManualContent.kt:45`). Swift has no such lenient value, so the two
/// shapes the file actually uses are cases and anything else is ``other`` —
/// which ``ManualDoc/label(_:_:fallback:)`` answers for exactly as Kotlin's
/// `runCatching` does: with the fallback.
public enum ManualLabelValue: Equatable, Sendable, Decodable {
    case text(String)
    case nested([String: String])
    /// Neither — a number, a list, an object of objects. Reads as absent.
    case other

    public init(from decoder: any Decoder) throws {
        let one = try decoder.singleValueContainer()
        if let text = try? one.decode(String.self) {
            self = .text(text)
        } else if let nested = try? one.decode([String: String].self) {
            self = .nested(nested)
        } else {
            self = .other
        }
    }
}

/// `ManualDoc` (`ManualContent.kt:30`).
public struct ManualDoc: Equatable, Sendable, Decodable {

    public let schema: String
    public let docBase: String
    public let labels: [String: ManualLabelValue]
    public let wakeWord: ManualWakeWord
    public let routes: [String: ManualRoute]

    /// handler → the server feature it needs on (`GET /api/capabilities`
    /// `features`) to be listed in "Everything it can do". The shipped file
    /// has two: `chat_mode → chat`, `double_check → ai`.
    public let handlerNeedsFeature: [String: String]

    public let centre: ManualCentre
    public let start: ManualStart
    public let topics: [ManualTopic]
    public let pieces: [ManualPiece]

    public init(
        schema: String = "",
        docBase: String = "",
        labels: [String: ManualLabelValue] = [:],
        wakeWord: ManualWakeWord = ManualWakeWord(),
        routes: [String: ManualRoute] = [:],
        handlerNeedsFeature: [String: String] = [:],
        centre: ManualCentre = ManualCentre(),
        start: ManualStart = ManualStart(),
        topics: [ManualTopic] = [],
        pieces: [ManualPiece] = []
    ) {
        self.schema = schema
        self.docBase = docBase
        self.labels = labels
        self.wakeWord = wakeWord
        self.routes = routes
        self.handlerNeedsFeature = handlerNeedsFeature
        self.centre = centre
        self.start = start
        self.topics = topics
        self.pieces = pieces
    }

    enum CodingKeys: String, CodingKey {
        case schema, labels, routes, centre, start, topics, pieces
        case docBase = "doc_base"
        case wakeWord = "wake_word"
        case handlerNeedsFeature = "handler_needs_feature"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schema = try c.decodeIfPresent(String.self, forKey: .schema) ?? ""
        docBase = try c.decodeIfPresent(String.self, forKey: .docBase) ?? ""
        labels = try c.decodeIfPresent([String: ManualLabelValue].self, forKey: .labels) ?? [:]
        wakeWord = try c.decodeIfPresent(ManualWakeWord.self, forKey: .wakeWord) ?? ManualWakeWord()
        routes = try c.decodeIfPresent([String: ManualRoute].self, forKey: .routes) ?? [:]
        handlerNeedsFeature =
            try c.decodeIfPresent([String: String].self, forKey: .handlerNeedsFeature) ?? [:]
        centre = try c.decodeIfPresent(ManualCentre.self, forKey: .centre) ?? ManualCentre()
        start = try c.decodeIfPresent(ManualStart.self, forKey: .start) ?? ManualStart()
        topics = try c.decodeIfPresent([ManualTopic].self, forKey: .topics) ?? []
        pieces = try c.decodeIfPresent([ManualPiece].self, forKey: .pieces) ?? []
    }

    /// A chrome string from `labels` (`"search_placeholder"`), or a nested one
    /// (`"admin"`, `"text"`); `fallback` when the file has none
    /// (`ManualContent.kt:45`).
    public func label(_ key: String, _ sub: String? = nil, fallback: String = "") -> String {
        guard let value = labels[key] else { return fallback }
        switch (value, sub) {
        case (.text(let text), nil):
            return text
        case (.nested(let map), .some(let sub)):
            return map[sub] ?? fallback
        default:
            // A nested label asked for flat, or a flat one asked for a
            // sub-key: Kotlin's `runCatching` swallows both into the
            // fallback, and so does this.
            return fallback
        }
    }

    public func piece(_ id: String?) -> ManualPiece? { pieces.first { $0.id == id } }
    public func topic(_ id: String?) -> ManualTopic? { topics.first { $0.id == id } }

    /// The topics around the centre — the centre's own opens from it
    /// (`ManualContent.kt:56`). Ten topics in the shipped file, so a ring of
    /// nine.
    public var ringTopics: [ManualTopic] { topics.filter { $0.id != centre.opens } }

    /// `ManualDoc.parse` (`ManualContent.kt:58`): a document only when it
    /// names a schema this app knows **and** has topics.
    ///
    /// Both halves of the guard earn their keep. The schema prefix is what
    /// rejects the SPA's `index.html` that the static mount answers with for
    /// an unknown path; the non-empty `topics` is what rejects a document
    /// that decoded but has nothing to show.
    public static func parse(_ text: String) -> ManualDoc? {
        parse(Data(text.utf8))
    }

    public static func parse(_ data: Data) -> ManualDoc? {
        guard let doc = try? JSONDecoder().decode(ManualDoc.self, from: data) else { return nil }
        guard doc.schema.hasPrefix(manualSchemaPrefix), !doc.topics.isEmpty else { return nil }
        return doc
    }
}

/// `ManualWakeWord` (`ManualContent.kt:67`). The defaults are the Kotlin's,
/// so a document that omits the block still words the wake-word line.
public struct ManualWakeWord: Equatable, Sendable, Decodable {

    public let `default`: String
    /// slug → the phrase people say (`hey_jarvis` → `Hey Jarvis`).
    public let names: [String: String]
    public let quoted: String
    public let note: String
    public let noteItem: String
    public let noteJoin: String

    public init(
        default defaultWord: String = "hey_jarvis",
        names: [String: String] = ["hey_jarvis": "Hey Jarvis"],
        quoted: String = "\u{201C}{phrase}\u{201D}",
        note: String = "Some rooms answer to another word: {list}.",
        noteItem: String = "\u{201C}{phrase}\u{201D} in {rooms}",
        noteJoin: String = "; "
    ) {
        self.default = defaultWord
        self.names = names
        self.quoted = quoted
        self.note = note
        self.noteItem = noteItem
        self.noteJoin = noteJoin
    }

    enum CodingKeys: String, CodingKey {
        case `default`, names, quoted, note
        case noteItem = "note_item"
        case noteJoin = "note_join"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let fallback = ManualWakeWord()
        `default` = try c.decodeIfPresent(String.self, forKey: .default) ?? fallback.default
        names = try c.decodeIfPresent([String: String].self, forKey: .names) ?? fallback.names
        quoted = try c.decodeIfPresent(String.self, forKey: .quoted) ?? fallback.quoted
        note = try c.decodeIfPresent(String.self, forKey: .note) ?? fallback.note
        noteItem = try c.decodeIfPresent(String.self, forKey: .noteItem) ?? fallback.noteItem
        noteJoin = try c.decodeIfPresent(String.self, forKey: .noteJoin) ?? fallback.noteJoin
    }
}

/// `ManualRoute` (`ManualContent.kt:77`): `dashboard` is the dashboard's
/// hash, `app` what the app does with it.
public struct ManualRoute: Equatable, Sendable, Decodable {

    public let dashboard: String?
    public let app: ManualAppRoute

    public init(dashboard: String? = nil, app: ManualAppRoute = ManualAppRoute()) {
        self.dashboard = dashboard
        self.app = app
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        dashboard = try c.decodeIfPresent(String.self, forKey: .dashboard)
        app = try c.decodeIfPresent(ManualAppRoute.self, forKey: .app) ?? ManualAppRoute()
    }

    enum CodingKeys: String, CodingKey { case dashboard, app }
}

/// `ManualAppRoute` (`ManualContent.kt:82`). `screen` is a **``Route`` raw
/// name** (`"Home"`, `"Settings"`), `tab` a Settings section key,
/// `dashboard` "the app opens the dashboard at this route instead".
public struct ManualAppRoute: Equatable, Sendable, Decodable {

    public let screen: String?
    public let tab: String?
    public let dashboard: Bool

    public init(screen: String? = nil, tab: String? = nil, dashboard: Bool = false) {
        self.screen = screen
        self.tab = tab
        self.dashboard = dashboard
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        screen = try c.decodeIfPresent(String.self, forKey: .screen)
        tab = try c.decodeIfPresent(String.self, forKey: .tab)
        dashboard = try c.decodeIfPresent(Bool.self, forKey: .dashboard) ?? false
    }

    enum CodingKeys: String, CodingKey { case screen, tab, dashboard }
}

/// `ManualCentre` (`ManualContent.kt:85`): the map's middle node.
public struct ManualCentre: Equatable, Sendable, Decodable {

    public let id: String
    public let title: String
    public let subtitle: String
    /// The topic the centre opens — and the one topic left out of
    /// ``ManualDoc/ringTopics``.
    public let opens: String

    public init(
        id: String = "domovoi",
        title: String = "Domovoi",
        subtitle: String = "",
        opens: String = ""
    ) {
        self.id = id
        self.title = title
        self.subtitle = subtitle
        self.opens = opens
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let fallback = ManualCentre()
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? fallback.id
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? fallback.title
        subtitle = try c.decodeIfPresent(String.self, forKey: .subtitle) ?? fallback.subtitle
        opens = try c.decodeIfPresent(String.self, forKey: .opens) ?? fallback.opens
    }

    enum CodingKeys: String, CodingKey { case id, title, subtitle, opens }
}

/// `ManualStart` (`ManualContent.kt:93`): the Start here page.
public struct ManualStart: Equatable, Sendable, Decodable {

    public let title: String
    public let blocks: [ManualBlock]
    public let checklist: [ManualCheck]

    public init(
        title: String = "Start here",
        blocks: [ManualBlock] = [],
        checklist: [ManualCheck] = []
    ) {
        self.title = title
        self.blocks = blocks
        self.checklist = checklist
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? "Start here"
        blocks = try c.decodeIfPresent([ManualBlock].self, forKey: .blocks) ?? []
        checklist = try c.decodeIfPresent([ManualCheck].self, forKey: .checklist) ?? []
    }

    enum CodingKeys: String, CodingKey { case title, blocks, checklist }
}

/// `ManualCheck` (`ManualContent.kt:100`): one line of Start here's checklist.
///
/// `platforms` says where the line belongs; `app_text` + `app_done_when` are
/// the app's own wording for a line the dashboard words differently — the
/// shipped file uses it for exactly one line (`app`), which is how pairing
/// this phone becomes a line the app can tick.
public struct ManualCheck: Equatable, Sendable, Decodable {

    public let id: String
    public let doneWhen: String
    public let platforms: [String]
    public let text: String
    public let appText: String?
    public let appDoneWhen: String?

    public init(
        id: String = "",
        doneWhen: String = "",
        platforms: [String] = [],
        text: String = "",
        appText: String? = nil,
        appDoneWhen: String? = nil
    ) {
        self.id = id
        self.doneWhen = doneWhen
        self.platforms = platforms
        self.text = text
        self.appText = appText
        self.appDoneWhen = appDoneWhen
    }

    enum CodingKeys: String, CodingKey {
        case id, platforms, text
        case doneWhen = "done_when"
        case appText = "app_text"
        case appDoneWhen = "app_done_when"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? ""
        doneWhen = try c.decodeIfPresent(String.self, forKey: .doneWhen) ?? ""
        platforms = try c.decodeIfPresent([String].self, forKey: .platforms) ?? []
        text = try c.decodeIfPresent(String.self, forKey: .text) ?? ""
        appText = try c.decodeIfPresent(String.self, forKey: .appText)
        appDoneWhen = try c.decodeIfPresent(String.self, forKey: .appDoneWhen)
    }
}

/// `ManualTopic` (`ManualContent.kt:110`).
public struct ManualTopic: Equatable, Sendable, Decodable {

    public let id: String
    public let title: String
    public let subtitle: String
    public let keywords: [String]
    /// Piece ids, in the order they ring the topic.
    public let pieces: [String]
    public let centre: Bool
    public let intro: String?

    public init(
        id: String = "",
        title: String = "",
        subtitle: String = "",
        keywords: [String] = [],
        pieces: [String] = [],
        centre: Bool = false,
        intro: String? = nil
    ) {
        self.id = id
        self.title = title
        self.subtitle = subtitle
        self.keywords = keywords
        self.pieces = pieces
        self.centre = centre
        self.intro = intro
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? ""
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        subtitle = try c.decodeIfPresent(String.self, forKey: .subtitle) ?? ""
        keywords = try c.decodeIfPresent([String].self, forKey: .keywords) ?? []
        pieces = try c.decodeIfPresent([String].self, forKey: .pieces) ?? []
        centre = try c.decodeIfPresent(Bool.self, forKey: .centre) ?? false
        intro = try c.decodeIfPresent(String.self, forKey: .intro)
    }

    enum CodingKeys: String, CodingKey {
        case id, title, subtitle, keywords, pieces, centre, intro
    }
}

/// `ManualPiece` (`ManualContent.kt:121`): one page of the manual.
///
/// `plugin` is a plugin slug — the piece is shown only while that plugin is
/// installed and enabled (`notes.map`), which is ``ManualRules`` /
/// ``ManualModel/visiblePieces``. `alias_of` points at another piece, whose
/// page opens instead; no piece in the shipped file uses it today and the
/// field is still honoured.
public struct ManualPiece: Equatable, Sendable, Decodable {

    public let id: String
    public let topic: String
    public let title: String
    public let subtitle: String
    public let keywords: [String]
    public let preview: Bool
    public let plugin: String?
    public let live: String?
    public let aliasOf: String?
    public let blocks: [ManualBlock]
    public let more: ManualMore?

    public init(
        id: String = "",
        topic: String = "",
        title: String = "",
        subtitle: String = "",
        keywords: [String] = [],
        preview: Bool = false,
        plugin: String? = nil,
        live: String? = nil,
        aliasOf: String? = nil,
        blocks: [ManualBlock] = [],
        more: ManualMore? = nil
    ) {
        self.id = id
        self.topic = topic
        self.title = title
        self.subtitle = subtitle
        self.keywords = keywords
        self.preview = preview
        self.plugin = plugin
        self.live = live
        self.aliasOf = aliasOf
        self.blocks = blocks
        self.more = more
    }

    enum CodingKeys: String, CodingKey {
        case id, topic, title, subtitle, keywords, preview, plugin, live, blocks, more
        case aliasOf = "alias_of"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? ""
        topic = try c.decodeIfPresent(String.self, forKey: .topic) ?? ""
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        subtitle = try c.decodeIfPresent(String.self, forKey: .subtitle) ?? ""
        keywords = try c.decodeIfPresent([String].self, forKey: .keywords) ?? []
        preview = try c.decodeIfPresent(Bool.self, forKey: .preview) ?? false
        plugin = try c.decodeIfPresent(String.self, forKey: .plugin)
        live = try c.decodeIfPresent(String.self, forKey: .live)
        aliasOf = try c.decodeIfPresent(String.self, forKey: .aliasOf)
        blocks = try c.decodeIfPresent([ManualBlock].self, forKey: .blocks) ?? []
        more = try c.decodeIfPresent(ManualMore.self, forKey: .more)
    }
}

/// `ManualMore` (`ManualContent.kt:136`): the "More detail" link at the foot
/// of a piece. `target` is a repository path with an anchor, resolved by
/// ``manualDocURL(_:version:target:)``.
public struct ManualMore: Equatable, Sendable, Decodable {

    public let text: String
    public let target: String

    public init(text: String = "More detail", target: String = "") {
        self.text = text
        self.target = target
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        text = try c.decodeIfPresent(String.self, forKey: .text) ?? "More detail"
        target = try c.decodeIfPresent(String.self, forKey: .target) ?? ""
    }

    enum CodingKeys: String, CodingKey { case text, target }
}

/// `ManualBlock` (`ManualContent.kt:141`): one block of a page. Which fields
/// are set depends on ``type`` (the file's `notes.blocks`) — it is the union
/// of every type's fields on both platforms, so an unknown type decodes and
/// draws nothing rather than failing the document.
///
/// Measured across the shipped file: `p` 127, `label` 91, `list` 79, `say`
/// 26, `live` 5, `table` 2, `checklist` 1.
public struct ManualBlock: Equatable, Sendable, Decodable {

    public let type: String
    public let text: String
    /// `"app"` or `"dashboard"` — which reader shows this block
    /// (``shownInApp(_:)``).
    public let only: String?
    public let ordered: Bool
    public let items: [ManualItem]
    /// `say` | `how` | `wrong` | `also` | `ways` | `checklist` on a `label`.
    public let role: String?
    public let admin: Bool
    public let note: String?
    public let phrases: [ManualPhrase]
    public let header: [String]
    public let rows: [[String]]
    /// `wake-word` | `things-to-say` | `rooms` | `plugins` on a `live` block.
    public let slot: String?

    public init(
        type: String = "",
        text: String = "",
        only: String? = nil,
        ordered: Bool = false,
        items: [ManualItem] = [],
        role: String? = nil,
        admin: Bool = false,
        note: String? = nil,
        phrases: [ManualPhrase] = [],
        header: [String] = [],
        rows: [[String]] = [],
        slot: String? = nil
    ) {
        self.type = type
        self.text = text
        self.only = only
        self.ordered = ordered
        self.items = items
        self.role = role
        self.admin = admin
        self.note = note
        self.phrases = phrases
        self.header = header
        self.rows = rows
        self.slot = slot
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = try c.decodeIfPresent(String.self, forKey: .type) ?? ""
        text = try c.decodeIfPresent(String.self, forKey: .text) ?? ""
        only = try c.decodeIfPresent(String.self, forKey: .only)
        ordered = try c.decodeIfPresent(Bool.self, forKey: .ordered) ?? false
        items = try c.decodeIfPresent([ManualItem].self, forKey: .items) ?? []
        role = try c.decodeIfPresent(String.self, forKey: .role)
        admin = try c.decodeIfPresent(Bool.self, forKey: .admin) ?? false
        note = try c.decodeIfPresent(String.self, forKey: .note)
        phrases = try c.decodeIfPresent([ManualPhrase].self, forKey: .phrases) ?? []
        header = try c.decodeIfPresent([String].self, forKey: .header) ?? []
        rows = try c.decodeIfPresent([[String]].self, forKey: .rows) ?? []
        slot = try c.decodeIfPresent(String.self, forKey: .slot)
    }

    enum CodingKeys: String, CodingKey {
        case type, text, only, ordered, items, role, admin, note, phrases, header, rows, slot
    }
}

/// `ManualItem` (`ManualContent.kt:157`): one line of a `list` block.
public struct ManualItem: Equatable, Sendable, Decodable {

    public let text: String
    public let only: String?

    public init(text: String = "", only: String? = nil) {
        self.text = text
        self.only = only
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        text = try c.decodeIfPresent(String.self, forKey: .text) ?? ""
        only = try c.decodeIfPresent(String.self, forKey: .only)
    }

    enum CodingKeys: String, CodingKey { case text, only }
}

/// `ManualPhrase` (`ManualContent.kt:160`): one line of a `say` block.
///
/// `then` marks the **reply** to the phrase above it rather than a phrase of
/// its own; `pending` is "being built, this server may not answer yet";
/// `needs` is a `GET /api/capabilities` feature the phrase needs on
/// (``shownManualPhrases(_:features:)``).
public struct ManualPhrase: Equatable, Sendable, Decodable {

    public let text: String
    public let handler: String?
    public let path: String?
    public let note: String?
    public let then: Bool
    public let plugin: String?
    public let pending: String?
    public let needs: String?

    public init(
        text: String = "",
        handler: String? = nil,
        path: String? = nil,
        note: String? = nil,
        then: Bool = false,
        plugin: String? = nil,
        pending: String? = nil,
        needs: String? = nil
    ) {
        self.text = text
        self.handler = handler
        self.path = path
        self.note = note
        self.then = then
        self.plugin = plugin
        self.pending = pending
        self.needs = needs
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        text = try c.decodeIfPresent(String.self, forKey: .text) ?? ""
        handler = try c.decodeIfPresent(String.self, forKey: .handler)
        path = try c.decodeIfPresent(String.self, forKey: .path)
        note = try c.decodeIfPresent(String.self, forKey: .note)
        then = try c.decodeIfPresent(Bool.self, forKey: .then) ?? false
        plugin = try c.decodeIfPresent(String.self, forKey: .plugin)
        pending = try c.decodeIfPresent(String.self, forKey: .pending)
        needs = try c.decodeIfPresent(String.self, forKey: .needs)
    }

    enum CodingKeys: String, CodingKey {
        case text, handler, path, note, then, plugin, pending, needs
    }
}
