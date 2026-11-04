import Foundation

/// One row of Chat's history list (`ChatRow`, `ChatPlace.kt:70`), **minus the
/// place**.
///
/// Android's row carries a `ChatPlaceKind` because its list is two places
/// merged; this app has one, the house, so the field would be the same value
/// on every row for ever. `ANDROID-CHAT-MAP.md` says so of the chip too, and
/// the rule it is under is the owner's: a mark that never varies is furniture,
/// not information.
///
/// `id` is a `String` where the server's is a number, as Android's is, because
/// it is also the list's identity for a diff — and ``ChatRules/thread(_:)``
/// is the one way back to the number, so a caller cannot invent one.
public struct ChatRow: Equatable, Sendable, Identifiable {

    public let id: String
    public let title: String
    /// The last words, or a search's match. Nil for a thread with neither.
    public let snippet: String?
    /// Last activity, epoch milliseconds; 0 when unknown.
    public let updatedMs: Int64
    public let messages: Int
    /// In the archived view, not the main list.
    public let archived: Bool
    /// Pinned on **this phone** (``ChatRules/pinKey(id:server:)``).
    public let pinned: Bool
    /// A search hit: the words around the match. Nil when the title matched,
    /// or this is no search.
    public let match: String?
    /// A lookup in this chat went online (V037): the row's globe.
    public let usedInternet: Bool

    public init(
        id: String,
        title: String,
        snippet: String? = nil,
        updatedMs: Int64 = 0,
        messages: Int = 0,
        archived: Bool = false,
        pinned: Bool = false,
        match: String? = nil,
        usedInternet: Bool = false
    ) {
        self.id = id
        self.title = title
        self.snippet = snippet
        self.updatedMs = updatedMs
        self.messages = messages
        self.archived = archived
        self.pinned = pinned
        self.match = match
        self.usedInternet = usedInternet
    }

    /// The same row, pinned or not.
    public func pinned(_ value: Bool) -> ChatRow {
        ChatRow(
            id: id, title: title, snippet: snippet, updatedMs: updatedMs, messages: messages,
            archived: archived, pinned: value, match: match, usedInternet: usedInternet
        )
    }

    /// The server's own id, or nil for a row whose id is not a number — which
    /// nothing builds, and which must not become a request to
    /// `/threads/NaN`.
    public var threadID: Int64? { Int64(id) }

    /// What the row shows under the title: the search's match first, then the
    /// thread's last words, and **nothing at all** when either is blank.
    ///
    /// `ChatHome.kt:626`'s `(row.match ?: row.snippet)?.takeIf { it.isNotBlank() }`:
    /// a reply that failed leaves the server's snippet blank, and an empty
    /// line under the chip is worse than no line.
    public var subtitle: String? {
        guard let text = match ?? snippet,
              !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return text
    }
}

/// The history list's pure decisions, ported from `ChatPlace.kt`,
/// `ChatHome.kt` and `ServerChatPlace.kt` — all of them unit-tested on the JVM
/// over there, and all of them things a view cannot be asked whether it got
/// right.
public enum ChatRules {

    /// The longest search the server takes — `SEARCH_MAX_CHARS`
    /// (`ServerChatPlace.kt:36`), which is the server's own
    /// `_SEARCH_MAX_CHARS` (`chat.py:189`).
    public static let searchMaxChars = 200

    /// How many hits the server returns at most (`chat.py:190`'s
    /// `_SEARCH_LIMIT`). Not a cut this app makes — it is **why** a search
    /// that finds nothing more is not a search that found everything, and the
    /// reason is recorded here rather than left to a reader of the route.
    public static let searchLimit = 100

    /// How long typing pauses before a search runs — `SEARCH_DEBOUNCE_MS`
    /// (`ChatHome.kt:208`).
    public static let searchDebounceMs: Int64 = 250

    /// The pushes that mean "read the list again": `chat.changed`, which
    /// `rememberServerChatPlace` subscribes both its reads to
    /// (`ServerChatPlace.kt:65`).
    public static let listEvents: Set<String> = ["chat.changed"]

    // MARK: - a thread as a row

    /// `ThreadRow.toChatRow()` (`ServerChatPlace.kt:18`).
    ///
    /// - Parameter nowMs: unused by the conversion itself and not taken: the
    ///   row keeps `updatedMs`, and the **wording** of an age is
    ///   ``relTime(_:nowMs:)``'s, which takes its own clock. Two places
    ///   deciding what "3d ago" means is how Files came to disagree with Home.
    public static func row(_ thread: ChatThread) -> ChatRow {
        ChatRow(
            id: String(thread.id),
            title: thread.title ?? ChatStrings.newChat,
            snippet: thread.lastSnippet ?? ChatStrings.noMessagesYet,
            updatedMs: isoMs(thread.updatedAt) ?? 0,
            messages: thread.messageCount,
            archived: thread.archived,
            match: thread.matchSnippet,
            usedInternet: thread.usedInternet
        )
    }

    /// Every thread of `list` as a row, **dropping the ones with no id**.
    ///
    /// A thread whose `id` did not decode is `0` (see
    /// ``ChatThread/init(from:)``), and a row for it would offer a rename, an
    /// archive and a delete that all address `/threads/0`. Android cannot
    /// reach this state — Kotlin's `id: Long = 0` default has the same hole,
    /// and nothing noticed — so dropping it is this port's decision, named
    /// here.
    public static func rows(of list: ChatThreadList) -> [ChatRow] {
        list.threads.filter { $0.id > 0 }.map(row)
    }

    // MARK: - the order

    /// The history list's order (`orderChats`, `ChatPlace.kt:99`): the chats
    /// pinned on this phone first, then the rest by last use, newest first.
    /// Archived chats are left out — they have their own view.
    ///
    /// Kotlin's `sortedWith` is a **stable** sort, so two rows with the same
    /// `updatedMs` keep the order they came in. Swift's `sorted(by:)` is not
    /// documented as stable, so the comparator falls back to the row's
    /// position: without that, two threads updated in the same second could
    /// swap places between two reads of the same list, and the list would
    /// shuffle under a finger for no reason.
    public static func orderChats(_ rows: [ChatRow]) -> [ChatRow] {
        stableSort(rows.filter { !$0.archived }) { left, right in
            if left.pinned != right.pinned { return left.pinned }
            return left.updatedMs > right.updatedMs
        }
    }

    /// The archived view: the archived chats, newest first
    /// (`archivedChats`, `ChatPlace.kt:103`). **Pins do not reorder it** —
    /// Android's `sortedByDescending { updatedMs }` has no pin clause, and an
    /// archived chat is not one somebody is keeping to hand.
    public static func archivedChats(_ rows: [ChatRow]) -> [ChatRow] {
        stableSort(rows.filter { $0.archived }) { $0.updatedMs > $1.updatedMs }
    }

    /// A search's hits, in the order Android puts them in
    /// (`ChatHome.kt:320`): **by date alone, and not through ``orderChats``**.
    ///
    /// That is the property to keep, and the reason it is a function with a
    /// test rather than a comment beside a sort. `GET /threads?q=` with no
    /// `archived` searches the archived threads **as well**
    /// (`chat.py:235`: the filter is applied only `if archived is not None or
    /// not query`), so running the hits through ``orderChats`` would silently
    /// drop every archived match — a search that quietly cannot find half of
    /// what it was asked about.
    public static func searchHits(_ rows: [ChatRow]) -> [ChatRow] {
        stableSort(rows) { $0.updatedMs > $1.updatedMs }
    }

    /// Kotlin's stable `sortedWith`, which Swift does not promise.
    private static func stableSort(
        _ rows: [ChatRow],
        by isBefore: (ChatRow, ChatRow) -> Bool
    ) -> [ChatRow] {
        rows.enumerated()
            .sorted { left, right in
                if isBefore(left.element, right.element) { return true }
                if isBefore(right.element, left.element) { return false }
                return left.offset < right.offset
            }
            .map(\.element)
    }

    // MARK: - pins, which are this phone's

    /// How a pin is keyed: `server:<address>#<id>` (`pinKey`,
    /// `ChatPlace.kt:112`).
    ///
    /// **Pins live on this phone** (owner, 2026-10-20: one person's pins do
    /// not move the household's list), and the address is in the key so that
    /// two houses never share them. `server` must be the **normalised**
    /// spelling — ``ChatModel`` passes
    /// ``ServerCredentials/normalize(_:)``'s answer, as
    /// `ChatScreen.kt:151` does — or one house's pins would come back under
    /// `http://box:6369` and not under `box:6369`.
    ///
    /// The `server:` prefix is Android's and is kept although this app has one
    /// place: a phone that later grows the on-device engine must read the
    /// pins it already saved, and a key without the prefix would collide with
    /// Android's own `phone:<id>`.
    public static func pinKey(id: String, server: String) -> String {
        "server:\(server)#\(id)"
    }

    public static func pinKey(_ row: ChatRow, server: String) -> String {
        pinKey(id: row.id, server: server)
    }

    /// `rows` with ``ChatRow/pinned`` set from `pins` (`withPins`,
    /// `ChatPlace.kt:118`).
    public static func withPins(_ rows: [ChatRow], pins: Set<String>, server: String) -> [ChatRow] {
        rows.map { $0.pinned(pins.contains(pinKey($0, server: server))) }
    }

    // MARK: - the row menu

    /// What a row's menu offers, in order (`rowActions`, `ChatPlace.kt:122`).
    public static func rowActions(_ row: ChatRow) -> [String] {
        [
            ChatStrings.actionRename,
            row.pinned ? ChatStrings.actionUnpin : ChatStrings.actionPin,
            row.archived ? ChatStrings.actionUnarchive : ChatStrings.actionArchive,
            ChatStrings.actionShare,
            ChatStrings.actionDelete,
        ]
    }

    // MARK: - the window divider

    /// Where the window divider goes: the index of the message with `fromId`
    /// (`windowDividerIndex`, `ChatHome.kt:671`).
    ///
    /// Nil when there is none, **or when it is the first message** — nothing
    /// earlier fell out, so there is nothing to say. Nil too for an id that is
    /// not on screen at all, which is the case a client that has only part of
    /// a thread would otherwise draw a divider for at index 0.
    public static func windowDividerIndex(ids: [Int64?], fromId: Int64?) -> Int? {
        guard let fromId else { return nil }
        guard let at = ids.firstIndex(where: { $0 == fromId }), at > 0 else { return nil }
        return at
    }

    /// The `window_from_id` the divider is placed by: the **last** reply in
    /// the transcript that carries stats (`ChatScreen.kt:945`).
    ///
    /// The last one and not the first: the window moves as a thread grows, and
    /// an older reply's figure would put the divider where the model's view
    /// began several turns ago.
    public static func windowFromId(_ transcript: [ChatLiveMessage]) -> Int64? {
        transcript.last { $0.role != "user" && $0.stats != nil }?.stats?.windowFromId
    }

    // MARK: - the search route

    /// `GET /api/chat/threads?q=` for `query` (`threadSearchPath`,
    /// `ServerChatPlace.kt:32`): trimmed, cut to ``searchMaxChars``, and
    /// percent-encoded the way Android's `URLEncoder` plus its `+` → `%20`
    /// rewrite encodes it.
    ///
    /// ### The two things this had to match by hand
    ///
    /// **The escape set.** `URLEncoder.encode(…, "UTF-8")` is
    /// `application/x-www-form-urlencoded`: it leaves alphanumerics and
    /// `-`, `_`, `.`, `*` alone and escapes everything else, where
    /// Foundation's `.urlQueryAllowed` leaves `&`, `=`, `+`, `~`, `!`, `(`,
    /// `)`, `'` and more through. Using the platform's set would send
    /// `?q=a&b` as two parameters and search for `a`.
    ///
    /// **The cut.** Kotlin's `take(200)` counts UTF-16 code units and Swift's
    /// `prefix(200)` counts grapheme clusters, and the server's own
    /// `max_length=200` counts neither: Python counts **code points**. This
    /// cuts by code points, because exceeding the server's limit is a 422 and
    /// the server's count is the one that decides. For the ASCII a search box
    /// mostly holds, all three agree.
    public static func threadSearchPath(query: String) -> String {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let cut = String(String.UnicodeScalarView(trimmed.unicodeScalars.prefix(searchMaxChars)))
        let encoded = cut.addingPercentEncoding(withAllowedCharacters: formURLUnreserved) ?? cut
        return "\(ChatPath.base)/threads?q=\(encoded)"
    }

    /// What `URLEncoder` leaves alone, exactly.
    private static let formURLUnreserved: CharacterSet = {
        var set = CharacterSet(charactersIn: "a"..."z")
        set.insert(charactersIn: "A"..."Z")
        set.insert(charactersIn: "0"..."9")
        set.insert(charactersIn: "-_.*")
        return set
    }()

    // MARK: - sharing a chat as text

    /// A thread as plain text, for the share sheet (`serverChatText`,
    /// `ServerChatPlace.kt:39`).
    ///
    /// A person's turn is attributed to the speaker voice identification heard
    /// where there is one, and to ``ChatStrings/shareYou`` otherwise — the one
    /// place the four undrawn speaker fields reach a person, and it reaches
    /// them because a shared transcript that said "You" for somebody else's
    /// words would be wrong rather than merely plain.
    public static func shareText(title: String, messages: [ChatMessage]) -> String {
        var out = "\(title)\n\(ChatStrings.shareHeader)\n"
        for message in messages {
            let who = message.role == "user"
                ? (message.speakerName ?? ChatStrings.shareYou)
                : ChatStrings.shareDomovoi
            var words = message.content.trimmingCharacters(in: .whitespacesAndNewlines)
            if let error = message.error {
                if !words.isEmpty { words += "\n" }
                words += "[\(error)]"
            }
            out += "\n\(who): \(words)\n"
        }
        return out
    }

    // MARK: - try again

    /// "Try again" is offered on the last message when it is a **finished,
    /// retryable** reply to a question (`canRetry`, `ChatScreen.kt:1302`).
    ///
    /// All four clauses matter: a reply still streaming is not finished, a
    /// spoken turn's reply is not retryable (the core may have set a timer
    /// with it), a reply with nothing before it has no question to re-ask, and
    /// a thread ending in a person's own words has no reply to replace.
    public static func canRetry(_ transcript: [ChatLiveMessage]) -> Bool {
        guard transcript.count >= 2, let last = transcript.last else { return false }
        let before = transcript[transcript.count - 2]
        return last.role != "user" && !last.pending && before.role == "user" && last.retryable
    }

    // MARK: - the composer's send

    /// Whether the composer draws a send at all: **something has been typed**
    /// (D8, and Android's `else` at `ChatScreen.kt:1165`).
    ///
    /// ### Why this is its own rule and not `!micOffered(typed:)`
    ///
    /// The mic's gate is ``ChatMoreModel/micOffered(typed:)``, which asks two
    /// questions — is the box empty, **and** does this phone have a device id
    /// to record against — and only the first of them has anything to do with
    /// the send. Reading the mic's rule backwards for the send made a future
    /// gate on the mic silently a gate on the send too: a phone with no device
    /// id and a typed message would have lost its send button, which is the
    /// one control this screen cannot do without. Spelt out here, the two
    /// branches are two questions about two controls, and the trim is the same
    /// one ``ChatMoreVoiceRules/micOffered(typed:)`` makes so that a box
    /// holding one space is still empty.
    public static func sendOffered(typed: String) -> Bool {
        !typed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}
