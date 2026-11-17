import Foundation

// ---------------------------------------------------------------------------
// The Friend houses screen's words and logic, apart from SwiftUI so the
// package tests can pin them — Android's `ui/screens/friendhouses/
// FriendHousesModels.kt` (394473e2), word for word. The words are the
// dashboard's (web/static/friend_houses.jsx), at phone width. Everything an
// admin does there is a line of words here, never a control: the household
// token is refused it.
// ---------------------------------------------------------------------------

public enum FhWords {
    public static let title = "Friend houses"

    /// Blocking joined the admin's list on 2026-11-17 (hand-off: blocking,
    /// unblocking and the blocked list answer 403 to a phone).
    public static let friendsAdmin =
        "Only an admin can add, remove or block friends, compare the codes, or change what a new message does, on the dashboard."
    public static let listsAdmin =
        "Only an admin can share a list, accept one or stop sharing, on the dashboard."
    public static let conversationAdmin =
        "Only an admin can save a copy of these messages or delete them, on the dashboard."
    public static let formerFriend = "No longer a friend: messages can't be sent to this house."
    public static let phoneFollows =
        "This phone follows the same choice: a badge for the inbox, a notification saying who it's from, or one with the message."

    /// **The iPhone's own sentence, beside ``phoneFollows``.** An iPhone app
    /// gets no background time here (the app's only background mode is
    /// `audio`, and there is no push and nothing hosted), so the
    /// notifications come only while the app is open. Said, rather than left
    /// for a person to find out.
    public static let phoneOpenOnly = "On this iPhone, that happens while Domovoi is open."

    public static let noMessages = "no messages yet"
    public static let noMessagesSub = "press message on a friend to write one"
    public static let noFriends = "no friends yet"
    public static let noFriendsSub = "An admin can add friend houses on the dashboard."
    public static let noShared = "no shared lists"
    public static let notVerified = "not verified"
    public static let verified = "verified"
    public static let loadEarlier = "Load earlier"
    public static let noLongerAFriend = "no longer a friend"
    public static let message = "message"
    public static let openInLists = "open in lists"
    public static let allConversations = "all conversations"
    public static let send = "Send"
    public static let sending = "Sending…"
    public static let loading = "loading"

    /// The tabs, lowercase chrome.
    public static let tabMessages = "messages"
    public static let tabFriends = "friends"
    public static let tabShared = "shared lists"

    /// A list a friend shared that an admin hasn't accepted yet.
    public static func invited(_ friend: String, _ title: String) -> String {
        "\(friend) shared a list: \(title). An admin can accept it in the dashboard."
    }

    /// What a new message from this friend does, as words (the dashboard's
    /// three choices). The admin sets it; the phone only says it.
    public static func alert(_ level: String?) -> String {
        switch level {
        case "inbox": return "New messages: only in the inbox"
        case "sender": return "New messages: the rooms say who it's from"
        case "read": return "New messages: read aloud in the rooms"
        default: return "New messages: as set on the dashboard"
        }
    }

    /// Presence, the hand-off's words: the other house is running and
    /// reachable — never who is home.
    public static let online = "Online"
    public static let notReachedYet = "Not reached yet"
    public static func lastSeen(_ relative: String) -> String { "Last seen \(relative)" }

    public static func unread(_ n: Int) -> String { "\(n) unread" }
    public static func notDelivered(_ n: Int) -> String { "\(n) not delivered" }
    public static func waiting(_ n: Int) -> String { "\(n) \(n == 1 ? "change" : "changes") waiting to send" }

    public static func friendName(_ name: String?) -> String {
        guard let name, !fhBlank(name) else { return "a former friend" }
        return name
    }

    /// A conversation's name, or "this house".
    public static func conversationName(_ name: String?) -> String {
        guard let name, !fhBlank(name) else { return "this house" }
        return name
    }

    public static func placeholder(_ name: String) -> String { "Message \(name)…" }

    /// The counter under the composer: "n / 2000".
    public static func counter(_ length: Int) -> String { "\(length) / \(fhMessageMax)" }

    public static func ourList(_ friend: String) -> String { "Our list, shared with \(friend)" }
    public static func theirList(_ friend: String) -> String { "\(friend)'s list, shared with this house" }
}

/// The three tabs, in Android's order.
public enum FhTab: String, CaseIterable, Sendable {
    case messages
    case friends
    case shared

    public var label: String {
        switch self {
        case .messages: return FhWords.tabMessages
        case .friends: return FhWords.tabFriends
        case .shared: return FhWords.tabShared
        }
    }
}

/// A friend house's presence line: "Online"; else "Last seen <relative>"
/// (the app's ``relTime(_:nowMs:)`` words: "4m ago", "yesterday"…); else
/// "Not reached yet". Nil — nothing drawn — when the server sent no presence
/// (`online` absent: a server from before it).
public func fhPresence(_ friend: FhFriend, nowMs: Int64) -> String? {
    guard let online = friend.online else { return nil }
    if online { return FhWords.online }
    if let seen = friend.lastSeenAt, parseInstant(seen) != nil {
        return FhWords.lastSeen(relTime(seen, nowMs: nowMs))
    }
    return FhWords.notReachedYet
}

/// An outgoing message's state in plain words, with its tone.
public struct FhOutState: Equatable, Sendable {
    public let tone: HealthTone
    public let words: String

    public init(_ tone: HealthTone, _ words: String) {
        self.tone = tone
        self.words = words
    }
}

/// An outgoing message's state; nil for an incoming one.
public func fhOutState(_ m: FhMessage) -> FhOutState? {
    if m.incoming { return nil }
    let err = m.lastError.flatMap { fhBlank($0) ? nil : $0 }
    switch m.state {
    case "sent":
        return FhOutState(.ok, "Delivered")
    case "failed":
        return FhOutState(.err, err.map { "Not delivered: \($0)" } ?? "Not delivered")
    default:
        if let err { return FhOutState(.warn, "Still trying: \(err)") }
        return FhOutState(.idle, "Waiting to send")
    }
}

/// More than this apart, an incoming message also shows the sender's time.
public let fhClockSkewSec: Int64 = 5 * 60

/// The sender's own clock, when it disagrees with this house's by more than
/// five minutes: "sent <time> (their clock)"; else nil.
public func fhTheirClock(_ m: FhMessage, zone: TimeZone = .current) -> String? {
    guard m.incoming, let here = parseInstant(m.createdAt), let theirs = m.sentAt else { return nil }
    let hereSec = Int64(here.timeIntervalSince1970.rounded(.down))
    if abs(theirs - hereSec) <= fhClockSkewSec { return nil }
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = zone
    formatter.amSymbol = "AM"
    formatter.pmSymbol = "PM"
    formatter.dateFormat = "MMM d, h:mm a"
    return "sent \(formatter.string(from: Date(timeIntervalSince1970: Double(theirs)))) (their clock)"
}

/// A conversation as the screen opens it: from the inbox, else a friend
/// never written to yet. Nil when `house` is not a key a URL may carry.
public func fhOpenConversation(_ house: String?, inbox: [FhConversation], friends: [FhFriend]) -> FhConversation? {
    guard let house, FhPaths.isHouseKey(house) else { return nil }
    if let known = inbox.first(where: { $0.house == house }) { return known }
    guard let friend = friends.first(where: { $0.house == house }) else { return nil }
    return FhConversation(house: friend.house, name: friend.name, friendId: friend.id)
}

/// The shared lists the screen shows: active first, then the offers.
public func fhSharedOrder(_ lists: [FhSharedList]) -> [FhSharedList] {
    lists.filter { $0.state == "active" } + lists.filter { $0.state == "invited" }
}

/// What a read gives the screen: the data, or a sentence to show (a 403's
/// is ``fhAdminOnly``).
public enum FhRead<Value: Sendable & Equatable>: Equatable, Sendable {
    case ok(Value)
    case problem(String)

    public var value: Value? {
        if case .ok(let value) = self { return value }
        return nil
    }
}

/// One conversation as loaded so far: oldest first, by id.
public struct FhThread: Equatable, Sendable {
    public var messages: [FhMessage]
    /// Earlier messages to load ("Load earlier").
    public var more: Bool
    public var loaded: Bool

    public init(messages: [FhMessage] = [], more: Bool = false, loaded: Bool = false) {
        self.messages = messages
        self.more = more
        self.loaded = loaded
    }
}

/// `rows` merged into `current` by id (a re-read replaces what it holds),
/// oldest first: the newest is at the bottom.
public func fhMerge(_ current: [FhMessage], _ rows: [FhMessage]) -> [FhMessage] {
    var byId: [Int64: FhMessage] = [:]
    for m in current { byId[m.id] = m }
    for m in rows { byId[m.id] = m }
    return byId.values.sorted { $0.id < $1.id }
}

/// Loading a conversation: the latest page, earlier pages by `before`, and
/// marking it read. Held to the server it was opened on.
public struct FhThreadLoader: Sendable {
    let api: FriendHousesApi
    public let house: String
    public let server: String?

    public init(api: FriendHousesApi, house: String, server: String? = nil) {
        self.api = api
        self.house = house
        self.server = server
    }

    /// The newest page, merged in. `first`: the open, which also decides
    /// whether there are earlier ones. Returns the thread and whether an
    /// incoming message is still unread (mark it read).
    public func latest(_ thread: FhThread, first: Bool) async throws -> (thread: FhThread, unread: Bool) {
        let rows = try await api.conversation(house, limit: FhPaths.page, server: server)
        let merged = fhMerge(thread.messages, rows)
        let more = first ? rows.count >= FhPaths.page : thread.more
        let unread = rows.contains { $0.incoming && $0.state == "unread" }
        return (FhThread(messages: merged, more: more, loaded: true), unread)
    }

    /// The page before the oldest held.
    public func earlier(_ thread: FhThread) async throws -> FhThread {
        guard let oldest = thread.messages.map(\.id).min() else { return thread }
        let rows = try await api.conversation(house, before: oldest, limit: FhPaths.page, server: server)
        return FhThread(messages: fhMerge(thread.messages, rows), more: rows.count >= FhPaths.page, loaded: true)
    }

    public func markRead() async throws -> Int {
        try await api.markRead(house, server: server)
    }
}
