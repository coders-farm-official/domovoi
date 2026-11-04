import Foundation

/// The rows `/api/chat` sends, and the paths that ask for them.
///
/// Ported from `ChatScreen.kt:127` (`ThreadRow`), `:151` (`MessageRow`),
/// `ChatMessageActions.kt:41` (`ChatStats`) and `ChatErrors.kt:48`
/// (`ChatModelsInfo`), read against `web/backend/api/chat.py` on 2026-11-02.
///
/// ### Every field is optional on the way in
///
/// Each of these types writes its own ``Swift/Decodable/init(from:)`` with
/// `decodeIfPresent` throughout, which is **not** defensiveness: Kotlin's
/// `@Serializable` gives every field of every row above a default, so an older
/// server that omits one is read by Android and would be refused by Swift's
/// synthesised decoder. A thread list that throws because one server predates
/// `used_internet` is a Chat screen that shows nothing at all, and the
/// synthesised decoder is one `@Serializable` annotation away from being
/// exactly that. ``TrustBook/init(from:)`` is here for the same reason and says
/// so at length.
///
/// ### Dates are ISO strings here
///
/// `updated_at` and `created_at` are `datetime.isoformat()`
/// (`chat.py:277`, `:415`), not the epoch seconds Files' `mtime` carries, so
/// the formatter is ``relTime(_:nowMs:)`` and not ``relTimeMs(_:nowMs:)``.

// MARK: - the thread list

/// One thread of the household's chat: `GET /api/chat/threads`.
public struct ChatThread: Equatable, Sendable, Decodable {

    /// The server's own id. A number, and the only identity a thread has.
    public let id: Int64

    /// Nil until the title job has written one — ``ChatStrings/newChat`` is
    /// what the list calls that.
    public let title: String?

    /// ISO 8601, the server's own spelling.
    public let updatedAt: String?

    public let messageCount: Int

    /// The last 120 characters of the newest message (`chat.py:259`), or nil
    /// for a thread with none.
    public let lastSnippet: String?

    public let archived: Bool

    /// A person named it (V034): the model's title job never writes over it.
    /// Decoded because the rename write sets it; nothing draws it.
    public let titleByUser: Bool

    /// The words around the match, on a search's answer only.
    public let matchSnippet: String?

    /// "Allow in this chat" is on (V037). Decoded; the per-chat internet
    /// switch is deferred with lookups, so nothing draws it.
    public let internetAllowed: Bool

    /// A lookup in this chat went online (V037) — the list's globe, which is
    /// the one half of lookups this port does draw, because it is a fact about
    /// a thread rather than a control.
    public let usedInternet: Bool

    public init(
        id: Int64,
        title: String? = nil,
        updatedAt: String? = nil,
        messageCount: Int = 0,
        lastSnippet: String? = nil,
        archived: Bool = false,
        titleByUser: Bool = false,
        matchSnippet: String? = nil,
        internetAllowed: Bool = false,
        usedInternet: Bool = false
    ) {
        self.id = id
        self.title = title
        self.updatedAt = updatedAt
        self.messageCount = messageCount
        self.lastSnippet = lastSnippet
        self.archived = archived
        self.titleByUser = titleByUser
        self.matchSnippet = matchSnippet
        self.internetAllowed = internetAllowed
        self.usedInternet = usedInternet
    }

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case updatedAt = "updated_at"
        case messageCount = "message_count"
        case lastSnippet = "last_snippet"
        case archived
        case titleByUser = "title_by_user"
        case matchSnippet = "match_snippet"
        case internetAllowed = "internet_allowed"
        case usedInternet = "used_internet"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        // `id` keeps Kotlin's `= 0` default rather than being required: a row
        // without one is a row nothing can be done to, and the list drops it
        // (``ChatRules/rows(of:)``) rather than the whole answer failing.
        id = try values.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        title = try values.decodeIfPresent(String.self, forKey: .title)
        updatedAt = try values.decodeIfPresent(String.self, forKey: .updatedAt)
        messageCount = try values.decodeIfPresent(Int.self, forKey: .messageCount) ?? 0
        lastSnippet = try values.decodeIfPresent(String.self, forKey: .lastSnippet)
        archived = try values.decodeIfPresent(Bool.self, forKey: .archived) ?? false
        titleByUser = try values.decodeIfPresent(Bool.self, forKey: .titleByUser) ?? false
        matchSnippet = try values.decodeIfPresent(String.self, forKey: .matchSnippet)
        internetAllowed = try values.decodeIfPresent(Bool.self, forKey: .internetAllowed) ?? false
        usedInternet = try values.decodeIfPresent(Bool.self, forKey: .usedInternet) ?? false
    }
}

/// `{threads: [...]}` — the shape both the list and the archived view and the
/// search all answer with (`chat.py:281`).
public struct ChatThreadList: Equatable, Sendable, Decodable {
    public let threads: [ChatThread]

    public init(threads: [ChatThread] = []) {
        self.threads = threads
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        threads = try values.decodeIfPresent([ChatThread].self, forKey: .threads) ?? []
    }

    enum CodingKeys: String, CodingKey { case threads }
}

// MARK: - a message

/// An upload a message carries. **Decoded and deliberately undrawn**: there is
/// no photo picker and no image bubble in this app, because the photo library
/// is the milestone Files' upload and Music's save-to-device also wait on
/// (`ANDROID-CHAT-MAP.md`, "what this port leaves out"). The token is kept so
/// that a message with a picture reads back as one when that batch arrives,
/// and so that nothing here silently drops a field the server sent.
public struct ChatImageRef: Equatable, Sendable, Decodable {
    public let token: String
    public let name: String

    public init(token: String = "", name: String = "") {
        self.token = token
        self.name = name
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        token = try values.decodeIfPresent(String.self, forKey: .token) ?? ""
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? ""
    }

    enum CodingKeys: String, CodingKey { case token, name }
}

/// A reply's figures, as the server keeps them in `chat_messages.stats`
/// (`ChatMessageActions.kt:41`).
///
/// **Two fields of twenty-three.** Android's `ChatStats` decodes the lot
/// because its details dialog draws them; this port has no details sheet, so
/// the rest would be twenty-one dead properties with a doc comment apiece.
/// ``errorCode`` words a failed reply (``chatErrorView(error:code:model:)``)
/// and ``windowFromId`` places the window divider. Unknown keys are ignored
/// rather than refused, so the batch that draws the figures adds properties
/// here and changes nothing else.
///
/// `model_role` was the third, with a doc comment saying it "is what the error
/// wording keys on when the model name is absent" — and nothing passed it to
/// ``chatErrorView(error:code:model:)``, here or in the Kotlin. It decoded for
/// one assertion in `ChatModelsTests` and no reader, so it is gone; the key
/// still arrives and is still ignored, like the other twenty-one.
public struct ChatStats: Equatable, Sendable, Decodable {

    /// Why a reply has an error, for this app's own wording
    /// (``ChatErrors/modelCodes``).
    public let errorCode: String?

    /// The oldest message the model was given (`chat.py:1574`). Everything
    /// before it is still shown, under ``ChatStrings/windowNote``.
    public let windowFromId: Int64?

    public init(errorCode: String? = nil, windowFromId: Int64? = nil) {
        self.errorCode = errorCode
        self.windowFromId = windowFromId
    }

    enum CodingKeys: String, CodingKey {
        case errorCode = "error_code"
        case windowFromId = "window_from_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        errorCode = try values.decodeIfPresent(String.self, forKey: .errorCode)
        windowFromId = try values.decodeIfPresent(Int64.self, forKey: .windowFromId)
    }
}

/// One message of a thread: `GET /api/chat/threads/{id}/messages`, and the
/// `done` event's payload.
public struct ChatMessage: Equatable, Sendable, Decodable {

    public let id: Int64
    /// `"user"` or `"assistant"`. Compared as a string, as Android does: a
    /// server that grows a third role must not make a thread undecodable.
    public let role: String
    public let content: String
    public let model: String?
    public let error: String?
    /// ISO 8601.
    public let createdAt: String?
    public let stats: ChatStats?
    public let deviceID: String?
    public let deviceName: String?
    /// `"typed"`, or `"voice"` for both rows of a turn said into the chat mic.
    /// Decoded; the mic is its own batch with its own permission, so nothing
    /// here draws a spoken turn differently.
    public let source: String?

    // The four speaker fields. Decoded, **undrawn**: voice identification
    // arrives with the chat mic, and a speaker marker on a thread nobody can
    // speak into would name a person for no reason.
    public let speakerPersonID: Int64?
    public let speakerName: String?
    public let speakerConfidence: Double?
    public let speakerConfident: Bool

    /// Whether "try again" may replace this reply (``canRetry(_:)``).
    public let retryable: Bool

    /// Pictures attached to a user row. **Decoded and undrawn** — see
    /// ``ChatImageRef``.
    public let images: [ChatImageRef]

    /// Whether this row carries `documents`, `doc` or `draft`.
    ///
    /// **The three deferred features, decoded as presence and nothing more.**
    /// Documents, drafts and letters are four server modules and about 3,000
    /// lines of Android; this app draws none of them, so a typed port of their
    /// shapes would be dead code that still had to be kept in step with the
    /// server. What is kept is the one bit a later batch and a reader both
    /// need: that the row has one. Nothing in this batch reads it either —
    /// it is here so that the omission is written where the code is, which is
    /// the other half of [[no-misleading-controls]].
    public let hasDocuments: Bool
    public let hasDocReply: Bool
    public let hasDraft: Bool

    /// Set on a retry's `done` row when the retry **failed** and the old reply
    /// was kept (`chat.py:1641`). The map does not mention it; the server
    /// sends it, and it is the difference between "here is your new reply" and
    /// "here is the one you had".
    public let kept: Bool

    /// On a successful retry's `done` row: the id of the reply this one
    /// replaced (`chat.py:1676`).
    public let replaces: Int64?

    public init(
        id: Int64 = 0,
        role: String = "",
        content: String = "",
        model: String? = nil,
        error: String? = nil,
        createdAt: String? = nil,
        stats: ChatStats? = nil,
        deviceID: String? = nil,
        deviceName: String? = nil,
        source: String? = nil,
        speakerPersonID: Int64? = nil,
        speakerName: String? = nil,
        speakerConfidence: Double? = nil,
        speakerConfident: Bool = false,
        retryable: Bool = false,
        images: [ChatImageRef] = [],
        hasDocuments: Bool = false,
        hasDocReply: Bool = false,
        hasDraft: Bool = false,
        kept: Bool = false,
        replaces: Int64? = nil
    ) {
        self.id = id
        self.role = role
        self.content = content
        self.model = model
        self.error = error
        self.createdAt = createdAt
        self.stats = stats
        self.deviceID = deviceID
        self.deviceName = deviceName
        self.source = source
        self.speakerPersonID = speakerPersonID
        self.speakerName = speakerName
        self.speakerConfidence = speakerConfidence
        self.speakerConfident = speakerConfident
        self.retryable = retryable
        self.images = images
        self.hasDocuments = hasDocuments
        self.hasDocReply = hasDocReply
        self.hasDraft = hasDraft
        self.kept = kept
        self.replaces = replaces
    }

    enum CodingKeys: String, CodingKey {
        case id, role, content, model, error, stats, source, images, documents, doc, draft, kept, replaces
        case createdAt = "created_at"
        case deviceID = "device_id"
        case deviceName = "device_name"
        case speakerPersonID = "speaker_person_id"
        case speakerName = "speaker_name"
        case speakerConfidence = "speaker_confidence"
        case speakerConfident = "speaker_confident"
        case retryable
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        role = try values.decodeIfPresent(String.self, forKey: .role) ?? ""
        content = try values.decodeIfPresent(String.self, forKey: .content) ?? ""
        model = try values.decodeIfPresent(String.self, forKey: .model)
        error = try values.decodeIfPresent(String.self, forKey: .error)
        createdAt = try values.decodeIfPresent(String.self, forKey: .createdAt)
        stats = try values.decodeIfPresent(ChatStats.self, forKey: .stats)
        deviceID = try values.decodeIfPresent(String.self, forKey: .deviceID)
        deviceName = try values.decodeIfPresent(String.self, forKey: .deviceName)
        source = try values.decodeIfPresent(String.self, forKey: .source)
        speakerPersonID = try values.decodeIfPresent(Int64.self, forKey: .speakerPersonID)
        speakerName = try values.decodeIfPresent(String.self, forKey: .speakerName)
        speakerConfidence = try values.decodeIfPresent(Double.self, forKey: .speakerConfidence)
        speakerConfident = try values.decodeIfPresent(Bool.self, forKey: .speakerConfident) ?? false
        retryable = try values.decodeIfPresent(Bool.self, forKey: .retryable) ?? false
        images = try values.decodeIfPresent([ChatImageRef].self, forKey: .images) ?? []
        // The three undrawn ones, as presence. `decodeNil` is asked first so
        // that an explicit `null` — which is what the server sends for a row
        // without one — reads as absent rather than as a shape that would not
        // decode.
        hasDocuments = try Self.carries(ChatImageRef.self, values, .documents)
        hasDocReply = try Self.present(values, .doc)
        hasDraft = try Self.present(values, .draft)
        kept = try values.decodeIfPresent(Bool.self, forKey: .kept) ?? false
        replaces = try values.decodeIfPresent(Int64.self, forKey: .replaces)
    }

    /// Whether `key` holds a non-empty **array**, whatever its elements are.
    ///
    /// The element type is read only to count: `documents` is a list of
    /// `{token, …}` objects and ``ChatImageRef`` decodes any object with
    /// optional `token`/`name`, so it stands in as "one element" without this
    /// file claiming to know the documents plan's shape.
    private static func carries<T: Decodable>(
        _ element: T.Type,
        _ values: KeyedDecodingContainer<CodingKeys>,
        _ key: CodingKeys
    ) throws -> Bool {
        guard values.contains(key), try !values.decodeNil(forKey: key) else { return false }
        return try (values.decodeIfPresent([T].self, forKey: key) ?? []).isEmpty == false
    }

    /// Whether `key` is there and is not `null`.
    private static func present(
        _ values: KeyedDecodingContainer<CodingKeys>,
        _ key: CodingKeys
    ) throws -> Bool {
        guard values.contains(key) else { return false }
        return try !values.decodeNil(forKey: key)
    }
}

/// `{messages: [...], jobs: [...]}` (`chat.py:635`).
///
/// **`jobs` is not decoded.** Long-document jobs are deferred with the
/// documents work, and a type for them here would be a shape to keep in step
/// with a server module nothing in this app talks to. An unknown key is
/// ignored by `JSONDecoder`, so adding it later is additive.
public struct ChatMessageList: Equatable, Sendable, Decodable {
    public let messages: [ChatMessage]

    public init(messages: [ChatMessage] = []) {
        self.messages = messages
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        messages = try values.decodeIfPresent([ChatMessage].self, forKey: .messages) ?? []
    }

    enum CodingKeys: String, CodingKey { case messages }
}

// MARK: - what the house has installed

/// `GET /api/chat/models` (`ChatErrors.kt:48`).
///
/// Read for one reason in this port: ``visionMissing(_:)``, so the composer
/// can say why a picture cannot be read **rather than offering a paperclip
/// that cannot work**. Nothing else is drawn from it, and the vision sentences
/// are the only place `vision_*` reaches a screen.
public struct ChatModelsInfo: Equatable, Sendable, Decodable {

    /// What Ollama has, as Ollama names it (`llama3.2:latest`).
    public let installed: [String]

    /// The house's configured chat model.
    public let defaultModel: String?

    /// The house's configured vision model.
    public let visionModel: String?

    /// False when a picture would be refused; **nil when the server could not
    /// ask Ollama**, or predates the field — which is not the same as false
    /// and must not be read as one.
    public let visionInstalled: Bool?

    public init(
        installed: [String] = [],
        defaultModel: String? = nil,
        visionModel: String? = nil,
        visionInstalled: Bool? = nil
    ) {
        self.installed = installed
        self.defaultModel = defaultModel
        self.visionModel = visionModel
        self.visionInstalled = visionInstalled
    }

    enum CodingKeys: String, CodingKey {
        case installed
        case defaultModel = "default_model"
        case visionModel = "vision_model"
        case visionInstalled = "vision_installed"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        installed = try values.decodeIfPresent([String].self, forKey: .installed) ?? []
        defaultModel = try values.decodeIfPresent(String.self, forKey: .defaultModel)
        visionModel = try values.decodeIfPresent(String.self, forKey: .visionModel)
        visionInstalled = try values.decodeIfPresent(Bool.self, forKey: .visionInstalled)
    }
}

// MARK: - the paths

/// Every route this screen asks for, in one place — the shape
/// ``FilePath`` and ``VideoPath`` have.
///
/// The routes this port does **not** ask for are listed in
/// `ANDROID-CHAT-MAP.md` with the feature each one waits on: `/lookup`,
/// `/lookups/{id}`, `/voice`, `/uploads/{token}`, `/heads-up`, and every route
/// in `chat_documents.py`, `chat_drafts.py`, `chat_letters.py` and
/// `chat_doc_jobs.py`.
public enum ChatPath {

    public static let base = "/api/chat"

    /// The list. `archived` picks the view: the route defaults to the
    /// not-archived ones (`chat.py:235`).
    public static func threads(archived: Bool = false) -> String {
        archived ? "\(base)/threads?archived=true" : "\(base)/threads"
    }

    /// A new thread. **Not asked for until the first message needs one** —
    /// see ``ChatModel/send(_:)``.
    public static let newThread = "\(base)/threads"

    public static func thread(_ id: Int64) -> String { "\(base)/threads/\(id)" }

    public static func messages(_ id: Int64) -> String { "\(base)/threads/\(id)/messages" }

    public static func retry(_ id: Int64) -> String { "\(base)/threads/\(id)/retry" }

    public static let models = "\(base)/models"

    /// `{"title": …}` or `{"archived": …}` for `PATCH /threads/{id}`.
    public static func patchBody(title: String) -> Data {
        json(["title": title])
    }

    public static func patchBody(archived: Bool) -> Data {
        guard let body = try? JSONSerialization.data(withJSONObject: ["archived": archived]) else {
            return Data(#"{"archived":false}"#.utf8)
        }
        return body
    }

    /// The send body. Android also puts `heads_up` and `images` in it
    /// (`ChatScreen.kt:334`); both belong to features this port does not have,
    /// and the server defaults them (`SendBody`, `chat.py:638`), so they are
    /// left out rather than sent empty.
    ///
    /// `device_id` **is** sent, because it is what the message's own row
    /// records about which install typed it, and a message with no device is a
    /// message the house cannot attribute.
    public static func sendBody(content: String, deviceID: String?) -> Data {
        var object: [String: Any] = ["content": content]
        if let deviceID, !deviceID.isEmpty { object["device_id"] = deviceID }
        return json(object)
    }

    private static func json(_ object: [String: Any]) -> Data {
        (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{}".utf8)
    }
}
