import Foundation

// "What does it say?" — what the server's scan reader answers.
//
// Every shape below is `domovoi/scan_reader/store.py` (`read_json`) and
// `says.py` (`summary`, `full`) on the branch that merged V065, through the
// web's `/api/scan/reads*` proxies (`web/backend/api/printers.py`). Every
// field is `decodeIfPresent` with a default, so a house a release ahead that
// adds a field, or one a release behind that lacks one, still decodes: a
// read that failed to decode would leave the sheet polling for ever.

/// One reading of a scan (a `ScanRead`, `store.py:read_json`).
///
/// Hashable because the Scan sheet pushes the reading screen with
/// `navigationDestination(item:)`.
public struct ScanRead: Equatable, Hashable, Sendable, Decodable, Identifiable {

    /// 32 hex characters, validated at both hops.
    public let id: String
    /// Documents-relative: `Scans/<name>.pdf`.
    public let path: String
    /// `queued` | `running` | `done` | `failed` | `cancelled` — the reader's
    /// spelling, two l's, where a scan **job** says `canceled`.
    public let state: String
    /// 0 until the first page is counted.
    public let pages: Int
    public let pagesDone: Int
    /// Pages the picture model read: "what the page might say".
    public let pagesGuessed: Int
    /// Pages nothing could read.
    public let pagesUnread: Int
    /// Paused while someone talks to Domovoi.
    public let waitingForVoice: Bool
    /// Done, and at least one page has words.
    public let readable: Bool
    /// `queued` or `running`. Sent by the server; derived from `state` only
    /// when an answer lacks it.
    public let reading: Bool
    /// The sentence to show for a failed read.
    public let error: String?
    /// `scan_missing` | `scan_no_reader` | `scan_read_interrupted` |
    /// `scan_read_failed`.
    public let errorCode: String?
    public let createdAt: String?
    public let finishedAt: String?

    public init(
        id: String = "",
        path: String = "",
        state: String = "queued",
        pages: Int = 0,
        pagesDone: Int = 0,
        pagesGuessed: Int = 0,
        pagesUnread: Int = 0,
        waitingForVoice: Bool = false,
        readable: Bool = false,
        reading: Bool? = nil,
        error: String? = nil,
        errorCode: String? = nil,
        createdAt: String? = nil,
        finishedAt: String? = nil
    ) {
        self.id = id
        self.path = path
        self.state = state
        self.pages = pages
        self.pagesDone = pagesDone
        self.pagesGuessed = pagesGuessed
        self.pagesUnread = pagesUnread
        self.waitingForVoice = waitingForVoice
        self.readable = readable
        self.reading = reading ?? Self.readingStates.contains(state)
        self.error = error
        self.errorCode = errorCode
        self.createdAt = createdAt
        self.finishedAt = finishedAt
    }

    private enum CodingKeys: String, CodingKey {
        case id, path, state, pages, readable, reading, error
        case pagesDone = "pages_done"
        case pagesGuessed = "pages_guessed"
        case pagesUnread = "pages_unread"
        case waitingForVoice = "waiting_for_voice"
        case errorCode = "error_code"
        case createdAt = "created_at"
        case finishedAt = "finished_at"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        path = try values.decodeIfPresent(String.self, forKey: .path) ?? ""
        let state = try values.decodeIfPresent(String.self, forKey: .state) ?? "queued"
        self.state = state
        pages = try values.decodeIfPresent(Int.self, forKey: .pages) ?? 0
        pagesDone = try values.decodeIfPresent(Int.self, forKey: .pagesDone) ?? 0
        pagesGuessed = try values.decodeIfPresent(Int.self, forKey: .pagesGuessed) ?? 0
        pagesUnread = try values.decodeIfPresent(Int.self, forKey: .pagesUnread) ?? 0
        waitingForVoice = try values.decodeIfPresent(Bool.self, forKey: .waitingForVoice) ?? false
        readable = try values.decodeIfPresent(Bool.self, forKey: .readable) ?? false
        reading = try values.decodeIfPresent(Bool.self, forKey: .reading)
            ?? Self.readingStates.contains(state)
        error = try values.decodeIfPresent(String.self, forKey: .error)
        errorCode = try values.decodeIfPresent(String.self, forKey: .errorCode)
        createdAt = try values.decodeIfPresent(String.self, forKey: .createdAt)
        finishedAt = try values.decodeIfPresent(String.self, forKey: .finishedAt)
    }

    /// `reading` = state is queued or running (`store.py:131`).
    public static let readingStates: Set<String> = ["queued", "running"]

    public var done: Bool { state == "done" }

    /// Ended without words to show: the sheet stops following it.
    public var ended: Bool { state == "failed" || state == "cancelled" }
}

/// `GET /api/scan/reads?path=` — the newest read of a scan found in
/// Documents → Scans, and whether this server can read scans at all.
public struct ScanReadLookup: Equatable, Sendable, Decodable {

    /// Nil: never read.
    public let read: ScanRead?
    /// Tesseract is installed, or the picture model is available.
    public let canRead: Bool

    public init(read: ScanRead? = nil, canRead: Bool = false) {
        self.read = read
        self.canRead = canRead
    }

    private enum CodingKeys: String, CodingKey {
        case read
        case canRead = "can_read"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        read = try values.decodeIfPresent(ScanRead.self, forKey: .read)
        canRead = try values.decodeIfPresent(Bool.self, forKey: .canRead) ?? false
    }
}

/// One notice under the summary (`{code, text}`): `scan_pages_guessed`,
/// `scan_pages_unread`, `letter_checked`. `text` is the server's sentence.
public struct ScanReadNote: Equatable, Sendable, Decodable, Identifiable {

    public let code: String
    public let text: String

    /// The code alone is not unique across a house a release ahead, so the
    /// words are part of the identity.
    public var id: String { code + "\u{1F}" + text }

    public init(code: String = "", text: String = "") {
        self.code = code
        self.text = text
    }

    private enum CodingKeys: String, CodingKey { case code, text }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        code = try values.decodeIfPresent(String.self, forKey: .code) ?? ""
        text = try values.decodeIfPresent(String.self, forKey: .text) ?? ""
    }
}

/// `GET /api/scan/reads/{id}/summary` — "What does it say?"
/// (`says.py:summary`): `{read, ai, content, said, notes, failed?}`.
public struct ScanReadSummary: Equatable, Sendable, Decodable {

    public let read: ScanRead?
    /// False with AI features off, when the model failed, or when no words
    /// were found.
    public let ai: Bool
    /// The letter reader's summary, as the chat model wrote it. Nil with AI
    /// features off or when the model failed.
    public let content: String?
    /// The spoken version — and, when `content` is nil, the start of the text.
    public let said: String
    public let notes: [ScanReadNote]
    /// The chat model was asked and failed; `said` says so.
    public let failed: Bool

    public init(
        read: ScanRead? = nil,
        ai: Bool = false,
        content: String? = nil,
        said: String = "",
        notes: [ScanReadNote] = [],
        failed: Bool = false
    ) {
        self.read = read
        self.ai = ai
        self.content = content
        self.said = said
        self.notes = notes
        self.failed = failed
    }

    private enum CodingKeys: String, CodingKey { case read, ai, content, said, notes, failed }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        read = try values.decodeIfPresent(ScanRead.self, forKey: .read)
        ai = try values.decodeIfPresent(Bool.self, forKey: .ai) ?? false
        content = try values.decodeIfPresent(String.self, forKey: .content)
        said = try values.decodeIfPresent(String.self, forKey: .said) ?? ""
        notes = try values.decodeIfPresent([ScanReadNote].self, forKey: .notes) ?? []
        failed = try values.decodeIfPresent(Bool.self, forKey: .failed) ?? false
    }
}

/// One page of the full text.
public struct ScanReadPage: Equatable, Sendable, Decodable, Identifiable {

    public let page: Int
    /// `ocr` | `vision` | `none`.
    public let method: String
    /// Nil for a page nothing read. A `Double`: OCR's mean confidence is a
    /// fraction, and `JSONDecoder` reads a whole number into a `Double` too.
    public let confidence: Double?
    /// As the server words it: a `vision` page already starts "(This page was
    /// hard to read. The picture model thinks it might say:)".
    public let text: String

    public var id: Int { page }

    public init(page: Int = 0, method: String = "none", confidence: Double? = nil, text: String = "") {
        self.page = page
        self.method = method
        self.confidence = confidence
        self.text = text
    }

    private enum CodingKeys: String, CodingKey { case page, method, confidence, text }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        page = try values.decodeIfPresent(Int.self, forKey: .page) ?? 0
        method = try values.decodeIfPresent(String.self, forKey: .method) ?? "none"
        confidence = try values.decodeIfPresent(Double.self, forKey: .confidence)
        text = try values.decodeIfPresent(String.self, forKey: .text) ?? ""
    }
}

/// `GET /api/scan/reads/{id}/text` — `{read, pages, text}`. `text` is the
/// whole thing as the server joins it ("Page 2" headings when there is more
/// than one page), and it is what the sheet shows.
public struct ScanReadFullText: Equatable, Sendable, Decodable {

    public let read: ScanRead?
    public let pages: [ScanReadPage]
    public let text: String

    public init(read: ScanRead? = nil, pages: [ScanReadPage] = [], text: String = "") {
        self.read = read
        self.pages = pages
        self.text = text
    }

    private enum CodingKeys: String, CodingKey { case read, pages, text }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        read = try values.decodeIfPresent(ScanRead.self, forKey: .read)
        pages = try values.decodeIfPresent([ScanReadPage].self, forKey: .pages) ?? []
        text = try values.decodeIfPresent(String.self, forKey: .text) ?? ""
    }
}
