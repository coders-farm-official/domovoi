import Foundation

/// One message of the conversation, including the reply being written right
/// now — `LiveMessage` (`ChatScreen.kt:234`).
///
/// A value rather than Android's observable class: there is one writer (the
/// stream) and one reader (the view), and a `struct` makes
/// ``ChatTranscript/apply(_:)`` a pure function of (transcript, event) that a
/// test can exercise without a socket. Android's version is a class because
/// Compose needs per-field `mutableStateOf` to avoid redrawing the whole list
/// on every token; SwiftUI's diffing over an `Equatable` array does that job.
/// Deliberately **not** `Identifiable`: the list's identity is ``ordinal`` and
/// not ``id``, and a synthesised `Identifiable` on an optional `id` is exactly
/// the trap that comment is about.
public struct ChatLiveMessage: Equatable, Sendable {

    /// `"user"` or `"assistant"`.
    public let role: String

    public var content: String

    /// The server's row id, once there is one. Nil while a reply is being
    /// written, and for a person's turn, **which the server never sends a row
    /// back for** (`ChatScreen.kt:747`).
    public var id: Int64?

    /// ISO 8601: the server's spelling where there is a row, and this phone's
    /// clock where there is not one yet.
    public var createdAt: String?

    public var model: String?
    public var error: String?
    /// The server's code for ``error`` (`stats.error_code`, or the live
    /// stream's `error` event); nil for an error this phone made.
    public var errorCode: String?

    /// Still being written.
    public var pending: Bool

    public var stats: ChatStats?

    /// Which install sent it: ``ChatStrings/thisPhone`` for this one, the
    /// device's own name for another, its id when the house has no name for it
    /// (`ChatScreen.kt:523`).
    public var device: String?

    /// `"voice"` for a turn said into the chat mic. Carried, undrawn — the
    /// microphone is its own batch with its own permission.
    public var source: String?

    /// Who voice identification heard. Carried; it reaches a person only in
    /// the share text (``ChatRules/shareText(title:messages:)``).
    public var speakerName: String?

    /// Whether "try again" may replace this reply.
    public var retryable: Bool

    /// This row carries a picture, a document, a doc reply or a draft — all
    /// four **deliberately undrawn**; see ``ChatMessage/hasDocuments``. Kept so
    /// the batch that draws them has the fact, and so a reader of this file can
    /// see that the omission was a decision rather than an oversight.
    public var hasAttachments: Bool

    /// The list's identity, assigned once when the message joins the
    /// transcript and never reused.
    ///
    /// A reply being written has no server id, so `id` cannot be the identity:
    /// a list keyed on it would re-create (and re-animate) the pending row on
    /// every token, and two pending rows would collide on `nil`.
    public internal(set) var ordinal: Int

    public init(
        role: String,
        content: String = "",
        id: Int64? = nil,
        createdAt: String? = nil,
        model: String? = nil,
        error: String? = nil,
        errorCode: String? = nil,
        pending: Bool = false,
        stats: ChatStats? = nil,
        device: String? = nil,
        source: String? = nil,
        speakerName: String? = nil,
        retryable: Bool = false,
        hasAttachments: Bool = false,
        ordinal: Int = 0
    ) {
        self.role = role
        self.content = content
        self.id = id
        self.createdAt = createdAt
        self.model = model
        self.error = error
        self.errorCode = errorCode
        self.pending = pending
        self.stats = stats
        self.device = device
        self.source = source
        self.speakerName = speakerName
        self.retryable = retryable
        self.hasAttachments = hasAttachments
        self.ordinal = ordinal
    }

    /// The server's kept row for a reply that streamed into this one
    /// (`finishWith`, `ChatScreen.kt:221`).
    ///
    /// **The kept text replaces what streamed**, which is the point of the
    /// `done` event rather than a nicety: the row carries the real id, the
    /// model and `retryable`, and for a reply the server checked before keeping
    /// it (citations of passages it never gave are taken out) the row and not
    /// the stream is what the reply says. A live reply left as the stream's
    /// guess would also have no id, so nothing could be retried or shared.
    ///
    /// `content` is **not** replaced when the row carries an error: a reply
    /// that failed half-written has words on screen and an empty `content` in
    /// its row, and taking the words away would leave an error with nothing
    /// above it.
    public mutating func finish(with row: ChatMessage) {
        id = row.id > 0 ? row.id : nil
        createdAt = row.createdAt
        model = row.model ?? model
        stats = row.stats
        if let rowError = row.error { error = rowError }
        if let code = row.stats?.errorCode { errorCode = code }
        retryable = row.retryable
        hasAttachments = row.hasAttachments
        if row.error == nil { content = row.content }
    }

    /// The row as the share text reads it (`LiveMessage.asRow`,
    /// `ChatScreen.kt:1298`).
    public var asRow: ChatMessage {
        ChatMessage(role: role, content: content, error: error, speakerName: speakerName)
    }
}

extension ChatMessage {

    /// Whether this row has any of the four undrawn attachments.
    var hasAttachments: Bool {
        !images.isEmpty || hasDocuments || hasDocReply || hasDraft
    }

    /// The row as a transcript entry (`loadMessages`, `ChatScreen.kt:508`).
    ///
    /// - Parameter thisDeviceID: this install's device id. A message sent from
    ///   **here** is attributed to ``ChatStrings/thisPhone`` rather than to
    ///   whatever the house named this phone, so a person reading one thread on
    ///   two devices can tell which words were theirs.
    func live(ordinal: Int, thisDeviceID: String?) -> ChatLiveMessage {
        let device: String?
        if let deviceID, let thisDeviceID, !thisDeviceID.isEmpty, deviceID == thisDeviceID {
            device = ChatStrings.thisPhone
        } else {
            device = deviceName ?? deviceID
        }
        return ChatLiveMessage(
            role: role,
            content: content,
            id: id > 0 ? id : nil,
            createdAt: createdAt,
            model: model,
            error: error,
            errorCode: stats?.errorCode,
            pending: false,
            stats: stats,
            device: device,
            source: source,
            speakerName: speakerName,
            retryable: retryable,
            hasAttachments: hasAttachments,
            ordinal: ordinal
        )
    }
}

/// What one event of the stream did, so a caller can react without reading the
/// transcript back and guessing.
///
/// The names the stream can carry are `delta`, `replace`, `error`, `done`,
/// `job`, `progress`, `status` and `lookup_permission`. **The last two are not
/// in the map** and are in the server (`chat.py:1431` and `:1862`). An event
/// this app does not read must not end a reply, so each is ignored **by name**,
/// with the feature it belongs to named beside it.
public enum ChatStreamStep: Equatable, Sendable {
    /// More words arrived.
    case delta(String)
    /// The server said why the reply stopped.
    case failed(detail: String, code: String?, model: String?)
    /// The persisted row arrived. `kept` is a retry that failed and left the
    /// old reply in place; `replaces` is the id a successful retry removed.
    case finished(kept: Bool, replaces: Int64?)
    /// A named event this app does not read, ignored deliberately.
    case ignored(String)
    /// An event this app **does** read and could not decode. The stream carries
    /// on: half a reply beats none, and a `done` that will not parse is a
    /// server this build does not understand rather than a reason to throw the
    /// words already on screen away.
    case unreadable(String)
}

/// How a stream ended, which is not the same question as what its last event
/// was.
public enum ChatStreamEnd: Equatable, Sendable {
    /// A `done` row landed and the reply is the server's.
    case finished
    /// A retry failed: the reply that was there is back, and this is the
    /// failure's own words for the caller to say.
    case keptOldReply(String?)
    /// The stream stopped without a `done`. The words it managed are kept,
    /// with its error if it gave one.
    case unfinished(String?)
    /// There was no reply being written.
    case nothingRunning
}

/// The conversation: the messages, the reply being written, and the reply a
/// retry may have to put back.
///
/// **Every decision about the stream is in here**, which is the point: part 2
/// draws this and has no `if` of its own to get wrong. Three are not obvious,
/// and each has a test:
///
///  1. **The live reply is replaced by the `done` row**, never left as the
///     stream's guess (``ChatLiveMessage/finish(with:)``).
///  2. **A retry that fails puts the old reply back.** Android gets there by
///     re-reading the whole thread afterwards (`ChatScreen.kt:880`); this keeps
///     the row it took out and restores it, which is the same end state without
///     the extra read — and without the moment in between where the screen
///     shows a reply the server does not have.
///  3. **A retry can end with no `done` at all.** The `already_retried` branch
///     (`chat.py:1656`) sends `error` and closes. The old reply is still the
///     thread's, so ``finishStream(atISO:)`` restores it; without that the
///     transcript would keep a pending reply that is never coming.
public struct ChatTranscript: Equatable, Sendable {

    public private(set) var messages: [ChatLiveMessage] = []

    /// Where the reply being written is, if there is one.
    public private(set) var liveIndex: Int?

    /// The reply a retry took out, kept until the retry settles.
    private var stashed: ChatLiveMessage?

    /// Whether a `done` row replaced the live reply during this stream.
    private var replaced = false

    private var nextOrdinal = 0

    public init() {}

    public var isEmpty: Bool { messages.isEmpty }

    /// The reply being written, if there is one.
    public var live: ChatLiveMessage? {
        guard let liveIndex, messages.indices.contains(liveIndex) else { return nil }
        return messages[liveIndex]
    }

    /// Whether a stream is running.
    public var streaming: Bool { liveIndex != nil }

    /// Whether a retry is the stream that is running.
    public var retrying: Bool { stashed != nil }

    /// Where the window divider goes, if anywhere
    /// (``ChatRules/windowDividerIndex(ids:fromId:)``).
    public var windowDividerIndex: Int? {
        ChatRules.windowDividerIndex(
            ids: messages.map(\.id),
            fromId: ChatRules.windowFromId(messages)
        )
    }

    /// Whether "try again" is offered now (``ChatRules/canRetry(_:)``).
    public var canRetry: Bool { liveIndex == nil && ChatRules.canRetry(messages) }

    // MARK: - loading a thread

    /// Replace everything with the thread the server has (`loadMessages`,
    /// `ChatScreen.kt:508`).
    ///
    /// **Refused while a reply is being written**: a load mid-stream would take
    /// the reply away mid-sentence. Android's only mid-stream loader is its
    /// retry, which this type handles itself. Returns whether it loaded.
    @discardableResult
    public mutating func load(_ rows: [ChatMessage], thisDeviceID: String?) -> Bool {
        guard liveIndex == nil else { return false }
        stashed = nil
        replaced = false
        messages = rows.map { row in
            let message = row.live(ordinal: nextOrdinal, thisDeviceID: thisDeviceID)
            nextOrdinal += 1
            return message
        }
        return true
    }

    /// Forget everything — a different thread, or a different house.
    public mutating func clear() {
        messages = []
        liveIndex = nil
        stashed = nil
        replaced = false
    }

    // MARK: - sending

    /// The person's turn, then the pending reply under it (`send`,
    /// `ChatScreen.kt:749`).
    ///
    /// The user row is stamped **here** and not by the server: the server sends
    /// no row back for it, and the two clocks agree to the second on a LAN
    /// (`ChatScreen.kt:747`).
    public mutating func beginSend(content: String, atISO: String) {
        append(ChatLiveMessage(
            role: "user",
            content: content,
            createdAt: atISO,
            device: ChatStrings.thisPhone
        ))
        appendPendingReply()
    }

    /// Take the last reply out and put a pending one in its place (`retry`,
    /// `ChatScreen.kt:847`). False when ``canRetry`` says no, or a stream is
    /// already running.
    @discardableResult
    public mutating func beginRetry() -> Bool {
        guard canRetry else { return false }
        stashed = messages.removeLast()
        replaced = false
        appendPendingReply()
        return true
    }

    private mutating func appendPendingReply() {
        append(ChatLiveMessage(role: "assistant", pending: true))
        liveIndex = messages.count - 1
    }

    private mutating func append(_ message: ChatLiveMessage) {
        var copy = message
        copy.ordinal = nextOrdinal
        nextOrdinal += 1
        messages.append(copy)
    }

    // MARK: - the stream

    /// Apply one event to the reply being written, and say what it did.
    ///
    /// Does nothing when there is no live reply: an event that arrives after a
    /// stream was given up on is not a reason to write into whichever message
    /// happens to be last.
    @discardableResult
    public mutating func apply(_ event: ServerEvent) -> ChatStreamStep {
        guard let index = liveIndex, messages.indices.contains(index) else {
            return .ignored(event.name)
        }
        switch event.name {
        case "delta":
            guard let payload: ChatDelta = event.payload() else { return .unreadable(event.name) }
            messages[index].content += payload.text
            return .delta(payload.text)

        case "error":
            guard let payload: ChatStreamError = event.payload(),
                  let detail = payload.detail else { return .unreadable(event.name) }
            messages[index].error = detail
            messages[index].errorCode = payload.code
            // Only when the server named one: a refusal that names no model
            // must not blank a model a `done` row already gave.
            if let model = payload.model { messages[index].model = model }
            return .failed(detail: detail, code: payload.code, model: payload.model)

        case "done":
            guard let row: ChatMessage = event.payload() else { return .unreadable(event.name) }
            if row.kept {
                // A retry that failed: the server kept the reply that was
                // already there, and this row IS that reply. Put it back rather
                // than dressing the live one up as it — restoring the stash
                // keeps the row's own ordinal, so the list does not animate one
                // row out and an identical one in.
                restoreStash(replacement: row)
                // The stream is settled: a `done` landed, even though what it
                // carried was the reply that was already there.
                replaced = true
                return .finished(kept: true, replaces: nil)
            }
            messages[index].finish(with: row)
            messages[index].pending = false
            liveIndex = nil
            stashed = nil
            replaced = true
            return .finished(kept: false, replaces: row.replaces)

        // Ignored by name, each with the feature it belongs to:
        //  `replace`   — a lookup answer that became the honest line; the
        //                `done` row carries the kept text (`ChatScreen.kt:386`).
        //  `job`,
        //  `progress`  — a long document read in parts (documents plan D2).
        //  `status`    — "drafting" / "checking" (`chat.py:1431`), the drafts
        //                and lookups work. **Not in the map.**
        //  `lookup_permission`
        //              — the card that asks to go online (`chat.py:1862`), the
        //                lookups work. **Not in the map.**
        case "replace", "job", "progress", "status", "lookup_permission":
            return .ignored(event.name)

        default:
            // `message` among them, which is what an event with no name is
            // called: the server names all of its, so an unnamed one is a
            // server this app does not understand rather than something to
            // guess at (``ServerEventReader/unnamed``).
            return .ignored(event.name)
        }
    }

    /// The stream ended.
    ///
    /// A reply the stream never finished stops pending and keeps whatever words
    /// it has — Android does the same (`ChatScreen.kt:779`), and a spinner that
    /// never stops is the one outcome a person cannot act on.
    ///
    /// A **retry** that ended without its `done` row gets the old reply back:
    /// the thread on the server is unchanged (`chat.py:1621`, "a retry that
    /// fails writes nothing"), so the screen must say what the server would.
    @discardableResult
    public mutating func finishStream(atISO: String) -> ChatStreamEnd {
        guard let index = liveIndex, messages.indices.contains(index) else {
            stashed = nil
            return replaced ? .finished : .nothingRunning
        }
        if stashed != nil && !replaced {
            let failure = messages[index].error
            restoreStash(replacement: nil)
            return .keptOldReply(failure)
        }
        if messages[index].createdAt == nil { messages[index].createdAt = atISO }
        messages[index].pending = false
        let failure = messages[index].error
        liveIndex = nil
        stashed = nil
        return .unfinished(failure)
    }

    /// The error of a send that never reached the server at all
    /// (`ChatScreen.kt:773`): it goes where the reply would have been.
    public mutating func failLive(_ text: String) {
        guard let index = liveIndex, messages.indices.contains(index) else { return }
        messages[index].error = text
    }

    /// Put the stashed reply back where the live one is.
    ///
    /// `replacement` is the server's own row for it when the server sent one (a
    /// `kept: true` done), so the restored reply is the server's rather than a
    /// remembered copy — a retry that failed while another device edited the
    /// thread must not put a stale row back.
    private mutating func restoreStash(replacement: ChatMessage?) {
        guard let index = liveIndex, messages.indices.contains(index),
              var old = stashed else { return }
        if let replacement {
            old.finish(with: replacement)
            old.pending = false
        }
        messages[index] = old
        liveIndex = nil
        stashed = nil
        replaced = false
    }
}

/// `event: delta` — `{"text": "…"}`.
struct ChatDelta: Decodable {
    let text: String

    init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        text = try values.decodeIfPresent(String.self, forKey: .text) ?? ""
    }

    enum CodingKeys: String, CodingKey { case text }
}

/// `event: error` — `{"detail": …, "code": …, "model": …}` (`chat.py:1639`).
///
/// `detail` is optional on the way in so that an error event without one reads
/// as ``ChatStreamStep/unreadable(_:)`` rather than as a reply that says
/// nothing went wrong.
struct ChatStreamError: Decodable {
    let detail: String?
    let code: String?
    let model: String?

    init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        detail = try values.decodeIfPresent(String.self, forKey: .detail)
        code = try values.decodeIfPresent(String.self, forKey: .code)
        model = try values.decodeIfPresent(String.self, forKey: .model)
    }

    enum CodingKeys: String, CodingKey { case detail, code, model }
}
