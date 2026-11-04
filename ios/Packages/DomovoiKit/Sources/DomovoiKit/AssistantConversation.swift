import Foundation

/// Who said a message in an on-phone conversation (`Speaker`,
/// `ConversationStore.kt:15`).
///
/// The raw values are the Kotlin's own (`USER`, `DOMOVOI`), because the files
/// this decodes are meant to be readable by a person with a text editor and
/// because an export written by one app and read by the other is M5's
/// business, not a thing to make impossible now.
public enum AssistantSpeaker: String, Codable, Sendable {
    case user = "USER"
    case domovoi = "DOMOVOI"
}

/// One message of an on-phone conversation, as it is kept (`StoredMessage`,
/// `ConversationStore.kt:24-101`).
///
/// ### Every field is optional on the way in
///
/// The Kotlin defaults all thirty of its fields and decodes with
/// `ignoreUnknownKeys` and `coerceInputValues`, so a file written by an older
/// or a newer app still reads (`android/CONVENTIONS.md`). That is the whole
/// reason ``init(from:)`` is written out here: `Codable`'s synthesized decoder
/// throws on a missing key, and a conversation kept since M1 must not be lost
/// because M2 added `spoken`. An unknown speaker decodes as ``AssistantSpeaker/user``,
/// which is Kotlin's `coerceInputValues` on an enum with that default.
///
/// ### Eight fields, and the twenty-two that are not here
///
/// `from`, `text`, `at`, `error`, `stopped`, `firstTextMs`, `tokensPerS` and
/// `backend` are milestone 1's. The rest of `StoredMessage` is later
/// milestones' marks — `spoken`/`voiceMs`/`sttMs`/`stopMs` (M2),
/// `fromClock`/`fastPath`/`card`/`tools` (M3), `documents`/`docMode`/
/// `sources`/`notes`/`draft`/`lookupCard`/`lookupSources`/`jobId`/`photo`/
/// `vision`/`peakMb`/`headsUp` (M5, M6) — and they are left out rather than
/// decoded-and-ignored because nothing writes them yet and an unknown key is
/// already tolerated on the way in. A file written by a later milestone
/// therefore still opens here, minus its marks.
///
/// `tokensPerS` and `backend` are the two of the eight nothing fills:
/// Apple's session reports no token counts and there is no backend to pick
/// (`ANDROID-ASSISTANT-MAP.md` §8 — the timings line is left out rather than
/// invented). They decode so that a chat written by a later milestone, or by
/// the Android app's export, keeps them.
public struct AssistantMessage: Codable, Equatable, Sendable, Identifiable {

    public var from: AssistantSpeaker
    public var text: String
    /// When it was sent or finished, epoch ms.
    public var at: Int64
    /// Why the reply failed, in plain words; nil for a good one.
    public var error: String?
    /// The reply was stopped part-way by the person.
    public var stopped: Bool
    /// Send to the first words on screen, ms.
    public var firstTextMs: Int64?
    /// The model's own decode speed for this reply, tokens a second. Nothing
    /// in M1 fills it.
    public var tokensPerS: Double?
    /// Where the model ran. Nothing in M1 fills it.
    public var backend: String?

    /// A list's identity for this message, made at decode time and **not
    /// kept**: the Kotlin has no message id and adding one to the file would
    /// make a file this app wrote unreadable by the format it is a port of.
    /// It is stable for as long as the value lives, which is what a `ForEach`
    /// needs.
    public let id: UUID

    public var fromUser: Bool { from == .user }

    public init(
        from: AssistantSpeaker,
        text: String = "",
        at: Int64 = 0,
        error: String? = nil,
        stopped: Bool = false,
        firstTextMs: Int64? = nil,
        tokensPerS: Double? = nil,
        backend: String? = nil
    ) {
        self.from = from
        self.text = text
        self.at = at
        self.error = error
        self.stopped = stopped
        self.firstTextMs = firstTextMs
        self.tokensPerS = tokensPerS
        self.backend = backend
        self.id = UUID()
    }

    enum CodingKeys: String, CodingKey {
        case from, text, at, error, stopped, firstTextMs, tokensPerS, backend
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        from = (try? values.decodeIfPresent(AssistantSpeaker.self, forKey: .from)) ?? .user
        text = try values.decodeIfPresent(String.self, forKey: .text) ?? ""
        at = try values.decodeIfPresent(Int64.self, forKey: .at) ?? 0
        error = try values.decodeIfPresent(String.self, forKey: .error)
        stopped = try values.decodeIfPresent(Bool.self, forKey: .stopped) ?? false
        firstTextMs = try values.decodeIfPresent(Int64.self, forKey: .firstTextMs)
        tokensPerS = try values.decodeIfPresent(Double.self, forKey: .tokensPerS)
        backend = try values.decodeIfPresent(String.self, forKey: .backend)
        id = UUID()
    }

    /// Equality is the **kept** fields, never ``id``: two decodes of one file
    /// are the same message, and a test that compared the ids would compare
    /// two `UUID()` calls.
    public static func == (lhs: AssistantMessage, rhs: AssistantMessage) -> Bool {
        lhs.from == rhs.from && lhs.text == rhs.text && lhs.at == rhs.at
            && lhs.error == rhs.error && lhs.stopped == rhs.stopped
            && lhs.firstTextMs == rhs.firstTextMs && lhs.tokensPerS == rhs.tokensPerS
            && lhs.backend == rhs.backend
    }
}

/// One conversation kept on this phone (`StoredConversation`,
/// `ConversationStore.kt:104-160`), milestone 1's fields of it.
///
/// `documents`, `internetAllowed`, `usedInternet` and `job` are not here for
/// the reason the message's twenty-two are not: M5 owns them, nothing writes
/// them, and an unknown key already decodes. ``windowFrom`` **is** here, and
/// is the one field M1 could have left out and should not: it is where
/// ``ChatStrings/windowNote`` is drawn, and a context overflow that is not
/// written down comes back as a failure on the next launch.
public struct AssistantConversation: Codable, Equatable, Sendable, Identifiable {

    public var id: String
    public var created: Int64
    public var updated: Int64
    public var messages: [AssistantMessage]
    /// The chat's name once the model or the person gave it one; nil: the
    /// first words of the first message (``title``).
    public var name: String?
    /// The person named it (rename): the model's title never replaces it.
    public var titleByUser: Bool
    /// The model has written ``name`` (the title job does not run again).
    public var titled: Bool
    /// Model titles that failed or were not usable
    /// (``AssistantPersona/titleMaxTries``).
    public var titleTries: Int
    /// In the history list's archived view, not the main list.
    public var archived: Bool
    /// Where the model's view of this chat began on the last reply, as an
    /// index into ``messages``; nil when it saw it all.
    public var windowFrom: Int?

    /// `StoredConversation.NEW_TITLE` (`ConversationStore.kt:159`), which is
    /// also ``ChatStrings/newChat``. Named through `ChatStrings` so the two
    /// cannot drift.
    public static var newTitle: String { ChatStrings.newChat }

    public init(
        id: String,
        created: Int64 = 0,
        updated: Int64 = 0,
        messages: [AssistantMessage] = [],
        name: String? = nil,
        titleByUser: Bool = false,
        titled: Bool = false,
        titleTries: Int = 0,
        archived: Bool = false,
        windowFrom: Int? = nil
    ) {
        self.id = id
        self.created = created
        self.updated = updated
        self.messages = messages
        self.name = name
        self.titleByUser = titleByUser
        self.titled = titled
        self.titleTries = titleTries
        self.archived = archived
        self.windowFrom = windowFrom
    }

    enum CodingKeys: String, CodingKey {
        case id, created, updated, messages, name, titleByUser, titled, titleTries, archived, windowFrom
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        created = try values.decodeIfPresent(Int64.self, forKey: .created) ?? 0
        updated = try values.decodeIfPresent(Int64.self, forKey: .updated) ?? 0
        messages = try values.decodeIfPresent([AssistantMessage].self, forKey: .messages) ?? []
        name = try values.decodeIfPresent(String.self, forKey: .name)
        titleByUser = try values.decodeIfPresent(Bool.self, forKey: .titleByUser) ?? false
        titled = try values.decodeIfPresent(Bool.self, forKey: .titled) ?? false
        titleTries = try values.decodeIfPresent(Int.self, forKey: .titleTries) ?? 0
        archived = try values.decodeIfPresent(Bool.self, forKey: .archived) ?? false
        windowFrom = try values.decodeIfPresent(Int.self, forKey: .windowFrom)
    }

    /// The history list's title: the name it was given, or the first five
    /// words of the first thing asked, else "new chat"
    /// (`StoredConversation.title`, `ConversationStore.kt:137`).
    ///
    /// Android's middle link, `"About <doc>"`, is M5's: there are no documents
    /// to be about.
    public var title: String {
        if let name, !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return name }
        let asked = messages.first { $0.fromUser && !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        return ChatTitles.firstWords(asked?.text) ?? Self.newTitle
    }

    /// The first question and the first good answer after it, for the model's
    /// title; nil until there is one (`firstExchange`,
    /// `ConversationStore.kt:143`).
    ///
    /// "Good" is all three clauses of the Kotlin's: not the person's, not
    /// blank, no error and not stopped. A title written from a stopped reply
    /// would name the chat after half a sentence.
    public func firstExchange() -> (question: String, answer: String)? {
        guard let q = messages.firstIndex(where: {
            $0.fromUser && !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }) else { return nil }
        guard let a = messages.dropFirst(q + 1).first(where: {
            !$0.fromUser && !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                && $0.error == nil && !$0.stopped
        }) else { return nil }
        return (messages[q].text, a.text)
    }

    /// The title job has something to do for this chat (`needsTitle`,
    /// `ConversationStore.kt:152`).
    public var needsTitle: Bool {
        !titleByUser && !titled && titleTries < AssistantPersona.titleMaxTries && firstExchange() != nil
    }

    /// This chat with `other`'s name, archive mark and title state — the same
    /// chat changed meanwhile (renamed, or titled, while a reply ran)
    /// (`withMetaOf`, `ConversationStore.kt:156`).
    ///
    /// **This is the other half of [[read-after-await-wrong-key]].** The epoch
    /// guard in ``AssistantPhoneModel`` stops a reply landing in the wrong
    /// conversation; this stops a reply landing in the **right** conversation
    /// and overwriting a rename that happened while the model was writing.
    /// A different id is left alone, as the Kotlin does.
    public func withMetaOf(_ other: AssistantConversation) -> AssistantConversation {
        guard other.id == id else { return self }
        var out = self
        out.name = other.name
        out.titleByUser = other.titleByUser
        out.titled = other.titled
        out.titleTries = other.titleTries
        out.archived = other.archived
        return out
    }

    /// This conversation as the history list shows it.
    public var summary: AssistantConversationSummary {
        AssistantConversationSummary(
            id: id,
            title: title,
            updated: updated,
            messages: messages.count,
            archived: archived,
            titleByUser: titleByUser
        )
    }
}

/// A conversation as the history list shows it (`ConversationSummary`,
/// `ConversationStore.kt:163`), less `usedInternet`: there are no lookups to
/// have gone online (M5), so the globe would be false on every row for ever,
/// which is the furniture-not-information rule ``ChatRules/ChatRow`` already
/// cites.
public struct AssistantConversationSummary: Equatable, Sendable, Identifiable {
    public let id: String
    public let title: String
    public let updated: Int64
    public let messages: Int
    public let archived: Bool
    public let titleByUser: Bool

    public init(
        id: String,
        title: String,
        updated: Int64,
        messages: Int,
        archived: Bool = false,
        titleByUser: Bool = false
    ) {
        self.id = id
        self.title = title
        self.updated = updated
        self.messages = messages
        self.archived = archived
        self.titleByUser = titleByUser
    }

    /// This summary as a row of Chat's history list (`toChatRow`,
    /// `PhoneChatPlace.kt:17`).
    ///
    /// `snippet` is nil, as Android's is: the phone keeps no "last words"
    /// field, and computing one from the last message would be a second answer
    /// to a question the server's place answers from its own data.
    public func row(match: String? = nil) -> ChatRow {
        ChatRow(
            id: id,
            title: title,
            snippet: nil,
            updatedMs: updated,
            messages: messages,
            archived: archived,
            match: match
        )
    }
}

/// A conversation a search found: `snippet` is the words around the first
/// match in a message, nil when only the title matched (`ConversationHit`,
/// `ConversationStore.kt:176`).
public struct AssistantConversationHit: Equatable, Sendable {
    public let summary: AssistantConversationSummary
    public let snippet: String?

    public init(summary: AssistantConversationSummary, snippet: String?) {
        self.summary = summary
        self.snippet = snippet
    }
}
