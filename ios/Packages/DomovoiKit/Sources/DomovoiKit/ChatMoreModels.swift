import Foundation

/// The parts of a chat row `ChatMessage` throws away, and the three card
/// shapes that ride on one — documents, drafts, long-document jobs, lookup
/// permission cards and heads-up cards.
///
/// Ported from `ChatDocsApi.kt:47` (`ServerDocument`), `:63` (`ServerSource`),
/// `:75` (`ServerDocReply`), `:88`/`:94` (the heads-up card), `:111`-`:139`
/// (the job), `ServerDrafts.kt:27` (`ServerDraftRef`) and
/// `assistant/lookup/LookupRules.kt:342` (`LookupCard`), read against
/// `web/backend/api/chat.py`, `chat_documents.py`, `chat_drafts.py` and
/// `chat_doc_jobs.py` on 2026-11-02.
///
/// ### Why these shapes exist twice
///
/// ``ChatMessage`` decodes `documents`, `doc` and `draft` as **presence
/// booleans** (`ChatModels.swift:253-255`), ``ChatStats`` keeps only
/// `error_code` and `window_from_id` (`:180`) so `stats.lookup_card` is lost,
/// and ``ChatMessageList`` drops the body's `jobs` array (`:385`). Even the
/// fields that *are* decoded — `images` and the four speaker ones — are
/// dropped on the way into the transcript by `ChatMessage.live(...)`
/// (`ChatTranscript.swift:152-175`). Every one of those files is one this
/// item may not edit (NIGHT-RULES §2), so ``ChatMoreDetail`` decodes the
/// **same** read a second time and keeps the extras keyed by message id.
/// `design-notes/ios/overnight/chatmore/DECISIONS.md` records the duplication
/// and the shared change that removes it.
///
/// ### Every field is optional on the way in
///
/// For the reason `ChatModels.swift:9-20` gives at length: Kotlin's
/// `@Serializable` gives each of these a default, so a server that omits one
/// is read by Android and would be refused by Swift's synthesised decoder. A
/// conversation that draws nothing because one server predates `elapsed_s` is
/// the whole screen lost to a field nothing reads.

// MARK: - documents

/// One note a document carries: scanned pages, formulas, a long document.
/// `DocQa.kt:9` (`DocNote`).
public struct ChatMoreDocNote: Equatable, Sendable, Decodable {
    public let code: String
    public let text: String

    public init(code: String = "", text: String = "") {
        self.code = code
        self.text = text
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        code = try values.decodeIfPresent(String.self, forKey: .code) ?? ""
        text = try values.decodeIfPresent(String.self, forKey: .text) ?? ""
    }

    enum CodingKeys: String, CodingKey { case code, text }
}

/// A document attached to a house chat: the upload's answer, and a user row's
/// `documents` (`ChatDocsApi.kt:47`; the server's `_meta`,
/// `chat_documents.py:158`).
public struct ChatMoreDocument: Equatable, Sendable, Decodable {
    public let token: String
    public let name: String
    public let kind: String
    public let pages: Int?
    public let words: Int
    public let parts: Int
    public let scannedPages: [Int]
    public let garbledPages: [Int]
    public let summary: String
    public let notes: [ChatMoreDocNote]
    public let size: Int64

    public init(
        token: String = "",
        name: String = "",
        kind: String = "",
        pages: Int? = nil,
        words: Int = 0,
        parts: Int = 0,
        scannedPages: [Int] = [],
        garbledPages: [Int] = [],
        summary: String = "",
        notes: [ChatMoreDocNote] = [],
        size: Int64 = 0
    ) {
        self.token = token
        self.name = name
        self.kind = kind
        self.pages = pages
        self.words = words
        self.parts = parts
        self.scannedPages = scannedPages
        self.garbledPages = garbledPages
        self.summary = summary
        self.notes = notes
        self.size = size
    }

    enum CodingKeys: String, CodingKey {
        case token, name, kind, pages, words, parts, summary, notes, size
        case scannedPages = "scanned_pages"
        case garbledPages = "garbled_pages"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        token = try values.decodeIfPresent(String.self, forKey: .token) ?? ""
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? ""
        pages = try values.decodeIfPresent(Int.self, forKey: .pages)
        words = try values.decodeIfPresent(Int.self, forKey: .words) ?? 0
        parts = try values.decodeIfPresent(Int.self, forKey: .parts) ?? 0
        scannedPages = try values.decodeIfPresent([Int].self, forKey: .scannedPages) ?? []
        garbledPages = try values.decodeIfPresent([Int].self, forKey: .garbledPages) ?? []
        summary = try values.decodeIfPresent(String.self, forKey: .summary) ?? ""
        notes = try values.decodeIfPresent([ChatMoreDocNote].self, forKey: .notes) ?? []
        size = try values.decodeIfPresent(Int64.self, forKey: .size) ?? 0
    }

    /// What the marker calls it: the summary the server wrote, else the file
    /// name (`ChatScreen.kt:1410`).
    public var markerSummary: String {
        summary.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? name : summary
    }
}

/// One source under a document reply (`ChatDocsApi.kt:63`).
public struct ChatMoreSource: Equatable, Sendable, Decodable {
    public let token: String
    public let name: String
    public let label: String
    public let page: Int?
    public let text: String
    /// The sentence inside ``text`` the reply matched, to mark.
    public let match: String
    /// The reply quoted it, rather than merely reading it.
    public let cited: Bool

    public init(
        token: String = "",
        name: String = "",
        label: String = "",
        page: Int? = nil,
        text: String = "",
        match: String = "",
        cited: Bool = false
    ) {
        self.token = token
        self.name = name
        self.label = label
        self.page = page
        self.text = text
        self.match = match
        self.cited = cited
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        token = try values.decodeIfPresent(String.self, forKey: .token) ?? ""
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
        label = try values.decodeIfPresent(String.self, forKey: .label) ?? ""
        page = try values.decodeIfPresent(Int.self, forKey: .page)
        text = try values.decodeIfPresent(String.self, forKey: .text) ?? ""
        match = try values.decodeIfPresent(String.self, forKey: .match) ?? ""
        cited = try values.decodeIfPresent(Bool.self, forKey: .cited) ?? false
    }

    enum CodingKeys: String, CodingKey { case token, name, label, page, text, match, cited }

    /// Whether the page sheet can draw the page itself: a PDF with a page
    /// number (`ChatDocsApi.kt:315`).
    public var rendersPage: Bool {
        page != nil && name.lowercased().hasSuffix(".pdf")
    }
}

/// An assistant row's `doc`: how it answered about the chat's documents
/// (`ChatDocsApi.kt:75`; `DocPlan.doc_json`, `chat_documents.py:512`).
public struct ChatMoreDocReply: Equatable, Sendable, Decodable {
    public let mode: String
    public let found: Bool
    public let sources: [ChatMoreSource]
    public let notes: [ChatMoreDocNote]
    /// A summary a long-document job wrote: which job, and how far it read.
    public let job: ChatMoreJobRef?
    /// `mode == "heads_up"`: the card this reply is.
    public let headsUp: ChatMoreHeadsUp?

    public init(
        mode: String = "",
        found: Bool = true,
        sources: [ChatMoreSource] = [],
        notes: [ChatMoreDocNote] = [],
        job: ChatMoreJobRef? = nil,
        headsUp: ChatMoreHeadsUp? = nil
    ) {
        self.mode = mode
        self.found = found
        self.sources = sources
        self.notes = notes
        self.job = job
        self.headsUp = headsUp
    }

    enum CodingKeys: String, CodingKey {
        case mode, found, sources, notes, job
        case headsUp = "heads_up"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        mode = try values.decodeIfPresent(String.self, forKey: .mode) ?? ""
        found = try values.decodeIfPresent(Bool.self, forKey: .found) ?? true
        sources = try values.decodeIfPresent([ChatMoreSource].self, forKey: .sources) ?? []
        notes = try values.decodeIfPresent([ChatMoreDocNote].self, forKey: .notes) ?? []
        job = try values.decodeIfPresent(ChatMoreJobRef.self, forKey: .job)
        headsUp = try values.decodeIfPresent(ChatMoreHeadsUp.self, forKey: .headsUp)
    }
}

// MARK: - the heads-up card (documents plan D3)

public struct ChatMoreHeadsUpOption: Equatable, Sendable, Decodable {
    public let id: String
    public let label: String

    public init(id: String = "", label: String = "") {
        self.id = id
        self.label = label
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        label = try values.decodeIfPresent(String.self, forKey: .label) ?? ""
    }

    enum CodingKeys: String, CodingKey { case id, label }
}

/// A reply that is a heads-up card: the weakness in a sentence and the
/// options that work on the house (`ChatDocsApi.kt:94`;
/// `domovoi/doc_qa/headsup.py:398`).
public struct ChatMoreHeadsUp: Equatable, Sendable, Decodable {
    public let kind: String
    public let text: String
    public let options: [ChatMoreHeadsUpOption]
    /// The server's own wording for "never again", when it sent one;
    /// ``ChatMoreStrings/neverWarn`` otherwise (`ChatDocsApi.kt:101`).
    public let neverLabel: String?
    /// The option already picked, here or on another device.
    public let chosen: String?

    public init(
        kind: String = "",
        text: String = "",
        options: [ChatMoreHeadsUpOption] = [],
        neverLabel: String? = nil,
        chosen: String? = nil
    ) {
        self.kind = kind
        self.text = text
        self.options = options
        self.neverLabel = neverLabel
        self.chosen = chosen
    }

    enum CodingKeys: String, CodingKey {
        case kind, text, options, chosen
        case neverLabel = "never_label"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? ""
        text = try values.decodeIfPresent(String.self, forKey: .text) ?? ""
        options = try values.decodeIfPresent([ChatMoreHeadsUpOption].self, forKey: .options) ?? []
        neverLabel = try values.decodeIfPresent(String.self, forKey: .neverLabel)
        chosen = try values.decodeIfPresent(String.self, forKey: .chosen)
    }

    /// The label the "never again" button carries (`ChatDocsApi.kt:101`).
    public var neverText: String {
        let said = neverLabel?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return said.isEmpty ? ChatMoreStrings.neverWarn : said
    }

    /// The options this app can actually carry out.
    ///
    /// `choose_another` is dropped: it answers nothing and re-opens the
    /// picture picker (`ChatScreen.kt:790-800`), and pictures are not sent
    /// here — an option that cannot be carried out is not drawn
    /// (`[[no-misleading-controls]]`, DECISIONS.md).
    public var offerable: [ChatMoreHeadsUpOption] {
        options.filter { $0.id != ChatMoreStrings.chooseAnother }
    }

    /// What a card that has been answered says in place of its buttons: the
    /// pick's label, else the raw id (`ChatDocsUi.kt:402`).
    public var chosenText: String? {
        guard let chosen, !chosen.isEmpty else { return nil }
        return options.first { $0.id == chosen }?.label ?? chosen
    }
}

// MARK: - long-document jobs (documents plan D2)

/// A job on a reply's `doc`: which job wrote the summary, and how far it read
/// (`ChatDocsApi.kt:111`).
public struct ChatMoreJobRef: Equatable, Sendable, Decodable {
    public let id: String
    public let state: String
    public let parts: Int
    public let read: Int

    public init(id: String = "", state: String = "", parts: Int = 0, read: Int = 0) {
        self.id = id
        self.state = state
        self.parts = parts
        self.read = read
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        state = try values.decodeIfPresent(String.self, forKey: .state) ?? ""
        parts = try values.decodeIfPresent(Int.self, forKey: .parts) ?? 0
        read = try values.decodeIfPresent(Int.self, forKey: .read) ?? 0
    }

    enum CodingKeys: String, CodingKey { case id, state, parts, read }
}

/// A job's result: the reply's text and its `doc` (`ChatDocsApi.kt:116`).
public struct ChatMoreJobResult: Equatable, Sendable, Decodable {
    public let content: String
    public let doc: ChatMoreDocReply?

    public init(content: String = "", doc: ChatMoreDocReply? = nil) {
        self.content = content
        self.doc = doc
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        content = try values.decodeIfPresent(String.self, forKey: .content) ?? ""
        doc = try values.decodeIfPresent(ChatMoreDocReply.self, forKey: .doc)
    }

    enum CodingKeys: String, CodingKey { case content, doc }
}

public struct ChatMoreJobError: Equatable, Sendable, Decodable {
    public let detail: String
    public let code: String?

    public init(detail: String = "", code: String? = nil) {
        self.detail = detail
        self.code = code
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        detail = try values.decodeIfPresent(String.self, forKey: .detail) ?? ""
        code = try values.decodeIfPresent(String.self, forKey: .code)
    }

    enum CodingKeys: String, CodingKey { case detail, code }
}

/// A long-document job as the server shows it (`chat_doc_jobs.py:252`,
/// `Job.view()`; `ChatDocsApi.kt:123`).
///
/// ``question`` and ``elapsedSeconds`` are decoded although the Kotlin model
/// does not declare them: the server sends both (`chat_doc_jobs.py:266`,
/// `:268`), and a field the server sends and no phone reads is a field that
/// drifts. ``result`` and ``error`` are present **only when set** (`:277-281`).
public struct ChatMoreJob: Equatable, Sendable, Decodable, Identifiable {
    public let id: String
    /// `running | reducing | stopped | failed | done | interrupted`.
    public let state: String
    public let waitingForVoice: Bool
    /// Which part is being read now. **Never the progress bar's numerator** —
    /// see ``read``.
    public let part: Int
    public let parts: Int
    /// How many parts are read. This is the bar (`ChatDocsUi.kt:330`).
    public let read: Int
    public let etaSeconds: Double?
    public let estimateSeconds: Double?
    /// The server's own progress sentence, already worded
    /// ("reading part 3 of 8 · about 1 min left"); an interrupted job's is
    /// rewritten in the view (`chat_doc_jobs.py:271-276`).
    public let line: String
    /// A phone chat's job, which keeps nothing on the server. Never this
    /// app's: `POST /api/chat/doc-jobs` has no iOS subject (MAP §4).
    public let isPrivate: Bool
    public let threadID: Int64?
    public let covers: String
    public let question: String?
    public let elapsedSeconds: Double?
    public let result: ChatMoreJobResult?
    public let error: ChatMoreJobError?

    public init(
        id: String = "",
        state: String = "",
        waitingForVoice: Bool = false,
        part: Int = 0,
        parts: Int = 0,
        read: Int = 0,
        etaSeconds: Double? = nil,
        estimateSeconds: Double? = nil,
        line: String = "",
        isPrivate: Bool = false,
        threadID: Int64? = nil,
        covers: String = "",
        question: String? = nil,
        elapsedSeconds: Double? = nil,
        result: ChatMoreJobResult? = nil,
        error: ChatMoreJobError? = nil
    ) {
        self.id = id
        self.state = state
        self.waitingForVoice = waitingForVoice
        self.part = part
        self.parts = parts
        self.read = read
        self.etaSeconds = etaSeconds
        self.estimateSeconds = estimateSeconds
        self.line = line
        self.isPrivate = isPrivate
        self.threadID = threadID
        self.covers = covers
        self.question = question
        self.elapsedSeconds = elapsedSeconds
        self.result = result
        self.error = error
    }

    enum CodingKeys: String, CodingKey {
        case id, state, part, parts, read, line, covers, question, result, error
        case waitingForVoice = "waiting_for_voice"
        case etaSeconds = "eta_s"
        case estimateSeconds = "estimate_s"
        case isPrivate = "private"
        case threadID = "thread_id"
        case elapsedSeconds = "elapsed_s"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        state = try values.decodeIfPresent(String.self, forKey: .state) ?? ""
        waitingForVoice = try values.decodeIfPresent(Bool.self, forKey: .waitingForVoice) ?? false
        part = try values.decodeIfPresent(Int.self, forKey: .part) ?? 0
        parts = try values.decodeIfPresent(Int.self, forKey: .parts) ?? 0
        read = try values.decodeIfPresent(Int.self, forKey: .read) ?? 0
        etaSeconds = try values.decodeIfPresent(Double.self, forKey: .etaSeconds)
        estimateSeconds = try values.decodeIfPresent(Double.self, forKey: .estimateSeconds)
        line = try values.decodeIfPresent(String.self, forKey: .line) ?? ""
        isPrivate = try values.decodeIfPresent(Bool.self, forKey: .isPrivate) ?? false
        threadID = try values.decodeIfPresent(Int64.self, forKey: .threadID)
        covers = try values.decodeIfPresent(String.self, forKey: .covers) ?? ""
        question = try values.decodeIfPresent(String.self, forKey: .question)
        elapsedSeconds = try values.decodeIfPresent(Double.self, forKey: .elapsedSeconds)
        result = try values.decodeIfPresent(ChatMoreJobResult.self, forKey: .result)
        error = try values.decodeIfPresent(ChatMoreJobError.self, forKey: .error)
    }

    /// Still working (`ChatDocsApi.kt:140`).
    public var live: Bool { state == "running" || state == "reducing" }
}

// MARK: - drafts (documents plan D4)

/// A house chat reply's `draft`: the draft and the version this turn wrote,
/// or offered to print (`ServerDrafts.kt:27`; `chat_drafts.py:196`).
public struct ChatMoreDraftRef: Equatable, Sendable, Decodable {
    public let id: Int64
    public let version: Int
    public let kind: String
    public let title: String
    public let template: String
    public let words: Int
    /// `create` | `revise` | `print` — a print card draws Print, not save
    /// and share (`DraftUi.kt:226`).
    public let action: String
    public let change: String
    public let parts: Int?

    public init(
        id: Int64 = 0,
        version: Int = 1,
        kind: String = "letter",
        title: String = "",
        template: String = "letter-block",
        words: Int = 0,
        action: String = "create",
        change: String = "",
        parts: Int? = nil
    ) {
        self.id = id
        self.version = version
        self.kind = kind
        self.title = title
        self.template = template
        self.words = words
        self.action = action
        self.change = change
        self.parts = parts
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        version = try values.decodeIfPresent(Int.self, forKey: .version) ?? 1
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? "letter"
        title = try values.decodeIfPresent(String.self, forKey: .title) ?? ""
        template = try values.decodeIfPresent(String.self, forKey: .template) ?? "letter-block"
        words = try values.decodeIfPresent(Int.self, forKey: .words) ?? 0
        action = try values.decodeIfPresent(String.self, forKey: .action) ?? "create"
        change = try values.decodeIfPresent(String.self, forKey: .change) ?? ""
        parts = try values.decodeIfPresent(Int.self, forKey: .parts)
    }

    enum CodingKeys: String, CodingKey {
        case id, version, kind, title, template, words, action, change, parts
    }

    /// The format a save from the card writes (`ServerDrafts.kt:144`).
    public var cardFormat: String { kind == "letter" ? "pdf" : "docx" }
}

// MARK: - the lookup permission card (part 5, W1)

/// The card a mid-answer lookup asks with: the exact words, where they go,
/// and what was answered (`LookupRules.kt:342`; minted at `chat.py:1838`).
public struct ChatMoreLookupCard: Equatable, Sendable, Decodable {
    /// `search` or `page` — a page's query is a URL and is not quoted
    /// (`LookupUi.kt:112`).
    public let kind: String
    public let query: String
    /// Where the words go, in words ("Wikipedia"): the one sentence that says
    /// it (`chat.py:1800`).
    public let destination: String
    /// `pending | allowed_once | allowed_chat | declined | asked_again`.
    public let state: String

    public static let pending = "pending"
    public static let page = "page"

    public init(
        kind: String = "search",
        query: String = "",
        destination: String = "",
        state: String = ChatMoreLookupCard.pending
    ) {
        self.kind = kind
        self.query = query
        self.destination = destination
        self.state = state
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? "search"
        query = try values.decodeIfPresent(String.self, forKey: .query) ?? ""
        destination = try values.decodeIfPresent(String.self, forKey: .destination) ?? ""
        state = try values.decodeIfPresent(String.self, forKey: .state) ?? Self.pending
    }

    enum CodingKeys: String, CodingKey { case kind, query, destination, state }

    public var isPending: Bool { state == Self.pending }

    /// The same card with an answer on it, shown at once while the decision's
    /// own stream runs (`ChatScreen.kt:893`).
    public func answered(_ decision: String) -> ChatMoreLookupCard {
        ChatMoreLookupCard(
            kind: kind,
            query: query,
            destination: destination,
            state: ChatMoreLookupRules.state(for: decision)
        )
    }
}

// MARK: - what the household says about going online

/// `GET /api/chat/internet` (`chat.py:2031`).
///
/// `always_allow` is decoded **leniently**: the route sends a JSON boolean
/// (`chat.py:2037`) and Android reads it as the *string* `"true"`
/// (`ChatScreen.kt:1272`), which is a Kotlin bug that makes its chip never
/// say "internet always on". The faithful thing is the server's shape, so a
/// boolean or a string both read here and the chip is right.
public struct ChatMoreInternet: Equatable, Sendable, Decodable {
    /// The household's egress answer; `"never"` turns lookups off entirely.
    public let internet: String
    public let alwaysAllow: Bool
    public let lookups: Bool

    public init(internet: String = "", alwaysAllow: Bool = false, lookups: Bool = true) {
        self.internet = internet
        self.alwaysAllow = alwaysAllow
        self.lookups = lookups
    }

    enum CodingKeys: String, CodingKey {
        case internet, lookups
        case alwaysAllow = "always_allow"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        internet = try values.decodeIfPresent(String.self, forKey: .internet) ?? ""
        if let flag = try? values.decodeIfPresent(Bool.self, forKey: .alwaysAllow) {
            alwaysAllow = flag ?? false
        } else {
            alwaysAllow = (try values.decodeIfPresent(String.self, forKey: .alwaysAllow)) == "true"
        }
        lookups = try values.decodeIfPresent(Bool.self, forKey: .lookups) ?? true
    }
}

// MARK: - the per-row extras, and the read they come from

/// Everything a row carries that the transcript drops.
///
/// Keyed by message id in ``ChatMoreDetail``, so ``ChatTranscript`` and
/// ``ChatModel`` are untouched: a bubble asks `extras(for: id)` and draws what
/// came back.
public struct ChatMoreRowExtras: Equatable, Sendable {
    public let id: Int64
    public let role: String
    public let content: String
    public let model: String?
    public let createdAt: String?
    public let source: String?
    public let images: [ChatImageRef]
    public let documents: [ChatMoreDocument]
    public let doc: ChatMoreDocReply?
    public let draft: ChatMoreDraftRef?
    public let lookupCard: ChatMoreLookupCard?
    public let speakerPersonID: Int64?
    public let speakerName: String?
    public let speakerConfidence: Double?
    public let speakerConfident: Bool

    public init(
        id: Int64,
        role: String = "",
        content: String = "",
        model: String? = nil,
        createdAt: String? = nil,
        source: String? = nil,
        images: [ChatImageRef] = [],
        documents: [ChatMoreDocument] = [],
        doc: ChatMoreDocReply? = nil,
        draft: ChatMoreDraftRef? = nil,
        lookupCard: ChatMoreLookupCard? = nil,
        speakerPersonID: Int64? = nil,
        speakerName: String? = nil,
        speakerConfidence: Double? = nil,
        speakerConfident: Bool = false
    ) {
        self.id = id
        self.role = role
        self.content = content
        self.model = model
        self.createdAt = createdAt
        self.source = source
        self.images = images
        self.documents = documents
        self.doc = doc
        self.draft = draft
        self.lookupCard = lookupCard
        self.speakerPersonID = speakerPersonID
        self.speakerName = speakerName
        self.speakerConfidence = speakerConfidence
        self.speakerConfident = speakerConfident
    }

    /// The row **is** a card rather than words: a lookup permission card, or
    /// a heads-up card (`ChatScreen.kt:984`, `:1449`).
    public var isCard: Bool { lookupCard != nil || headsUp != nil }

    public var headsUp: ChatMoreHeadsUp? {
        doc?.mode == "heads_up" ? doc?.headsUp : nil
    }

    /// A user row that is **only** a document draws no bubble
    /// (`ChatScreen.kt:1411`).
    public var documentOnly: Bool {
        role == "user" && !documents.isEmpty
            && content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}

/// One row of the parallel read. Decode-only: nothing writes this shape.
struct ChatMoreRow: Decodable {
    let extras: ChatMoreRowExtras

    /// `stats` for this read's purpose: the lookup card and nothing else.
    /// ``ChatStats`` keeps `error_code` and `window_from_id`, which the
    /// transcript already has.
    private struct Stats: Decodable {
        let lookupCard: ChatMoreLookupCard?

        enum CodingKeys: String, CodingKey { case lookupCard = "lookup_card" }

        init(from decoder: any Decoder) throws {
            let values = try decoder.container(keyedBy: CodingKeys.self)
            lookupCard = try values.decodeIfPresent(ChatMoreLookupCard.self, forKey: .lookupCard)
        }
    }

    enum CodingKeys: String, CodingKey {
        case id, role, content, model, source, images, documents, doc, draft, stats
        case createdAt = "created_at"
        case speakerPersonID = "speaker_person_id"
        case speakerName = "speaker_name"
        case speakerConfidence = "speaker_confidence"
        case speakerConfident = "speaker_confident"
    }

    init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        extras = ChatMoreRowExtras(
            id: try values.decodeIfPresent(Int64.self, forKey: .id) ?? 0,
            role: try values.decodeIfPresent(String.self, forKey: .role) ?? "",
            content: try values.decodeIfPresent(String.self, forKey: .content) ?? "",
            model: try values.decodeIfPresent(String.self, forKey: .model),
            createdAt: try values.decodeIfPresent(String.self, forKey: .createdAt),
            source: try values.decodeIfPresent(String.self, forKey: .source),
            images: try values.decodeIfPresent([ChatImageRef].self, forKey: .images) ?? [],
            documents: try values.decodeIfPresent([ChatMoreDocument].self, forKey: .documents) ?? [],
            doc: try values.decodeIfPresent(ChatMoreDocReply.self, forKey: .doc),
            draft: try values.decodeIfPresent(ChatMoreDraftRef.self, forKey: .draft),
            lookupCard: (try values.decodeIfPresent(Stats.self, forKey: .stats))?.lookupCard,
            speakerPersonID: try values.decodeIfPresent(Int64.self, forKey: .speakerPersonID),
            speakerName: try values.decodeIfPresent(String.self, forKey: .speakerName),
            speakerConfidence: try values.decodeIfPresent(Double.self, forKey: .speakerConfidence),
            speakerConfident: try values.decodeIfPresent(Bool.self, forKey: .speakerConfident) ?? false
        )
    }
}

/// `GET /api/chat/threads/{id}/messages` read for its extras and its `jobs`
/// (`chat.py:605`, the jobs at `:631`).
public struct ChatMoreDetail: Sendable, Decodable {

    /// The extras of every row that had any, keyed by message id.
    public let rows: [Int64: ChatMoreRowExtras]

    /// The thread's long-document jobs — the array ``ChatMessageList`` drops.
    public let jobs: [ChatMoreJob]

    public init(rows: [Int64: ChatMoreRowExtras] = [:], jobs: [ChatMoreJob] = []) {
        self.rows = rows
        self.jobs = jobs
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        let messages = try values.decodeIfPresent([ChatMoreRow].self, forKey: .messages) ?? []
        var byID: [Int64: ChatMoreRowExtras] = [:]
        for row in messages where row.extras.id > 0 {
            byID[row.extras.id] = row.extras
        }
        rows = byID
        jobs = try values.decodeIfPresent([ChatMoreJob].self, forKey: .jobs) ?? []
    }

    enum CodingKeys: String, CodingKey { case messages, jobs }

    public func extras(for id: Int64) -> ChatMoreRowExtras? { rows[id] }
}
