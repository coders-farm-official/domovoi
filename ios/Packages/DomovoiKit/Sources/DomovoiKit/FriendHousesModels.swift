import Foundation

// ---------------------------------------------------------------------------
// The Friend houses screen's words and logic, apart from SwiftUI so the
// package tests can pin them — Android's `ui/screens/friendhouses/
// FriendHousesModels.kt` (394473e2), word for word. The words are the
// dashboard's (web/static/friend_houses.jsx), at phone width.
//
// 1.4.1: the page is the dashboard's top to bottom — Messages, then Shared
// lists — and the dashboard's admin zone ("Friends and connections":
// requests, friends, friend files, this house, blocked houses) is not on the
// phone at all until phones can sign in as an admin. Accepting, declining
// and stopping a shared list are the household's now, and are controls.
// ---------------------------------------------------------------------------

public enum FhWords {
    public static let title = "Friend houses"

    /// Blocking joined the admin's list on 2026-11-17 (hand-off: blocking,
    /// unblocking and the blocked list answer 403 to a phone).
    ///
    /// **Not drawn in 1.4.1**: it belonged to the Friends tab, which the
    /// phones hide with the rest of the admin zone. Kept for the admin
    /// sign-in that brings that zone back.
    public static let friendsAdmin =
        "Only an admin can add, remove or block friends, compare the codes, or change what a new message does, on the dashboard."
    /// Starting a share is still an admin's (`POST /shared-lists`); accepting,
    /// declining and stopping are anyone's in the household (1.4.1).
    public static let listsAdmin =
        "Only an admin can share one of this house's lists with a friend house, on the dashboard."
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
    /// The dashboard's Messages card's own line: its "press Message on a
    /// friend" is for the admin zone's friends, which the phone hides.
    public static let noMessagesSub = "Messages with your friend houses. New ones arrive here."
    public static let noFriends = "no friends yet"
    public static let noFriendsSub = "An admin can add friend houses on the dashboard."
    public static let noShared = "no shared lists yet"
    public static let notVerified = "not verified"
    public static let verified = "verified"
    public static let loadEarlier = "Load earlier"
    public static let noLongerAFriend = "no longer a friend"
    public static let message = "message"
    public static let openInLists = "open in Lists"
    public static let allConversations = "all conversations"
    public static let send = "Send"
    public static let sending = "Sending…"
    public static let loading = "loading"

    /// The tabs, lowercase chrome.
    public static let tabMessages = "messages"
    public static let tabFriends = "friends"
    public static let tabShared = "shared lists"
    public static let tabConnections = "friends & connections"

    /// A list a friend shared with this house, not accepted yet (the
    /// dashboard's `FhListOffer`).
    public static func invited(_ friend: String, _ title: String) -> String {
        "\(friend) shared a list: \u{201C}\(title)\u{201D}"
    }

    // MARK: shared lists (the dashboard's `FhSharedListsCard`)

    public static let shareExplain =
        "Changes either house makes show up in both, usually within a minute. Each house keeps its own copy and its own name for it."
    public static let offeredHeading = "offered to this house"
    public static let sharedNowHeading = "shared now"
    public static let accept = "Accept"
    public static let decline = "Decline"
    public static let stopSharing = "Stop sharing"
    public static let cancel = "Cancel"
    public static let rename = "give it another name here"
    public static let callItHere = "Call it here"
    public static let declineTitle = "Decline this list?"
    public static let stopTitle = "Stop sharing?"
    public static let waitingForThem = "waiting for them to accept"
    public static let declined = "list declined"
    public static let stopped = "stopped sharing \u{2014} you both keep your own copy"

    /// "“Camping” won’t be added here. The Smiths isn’t told."
    public static func declineBody(_ title: String?, _ friend: String) -> String {
        let named = title.flatMap { fhBlank($0) ? nil : $0 } ?? "This list"
        return "\u{201C}\(named)\u{201D} won\u{2019}t be added here. \(friend) isn\u{2019}t told."
    }

    public static func stopBody(_ list: String, _ friend: String) -> String {
        "Stop sharing \(list) with \(friend)? You both keep your own copy."
    }

    /// The toast after an accept: the name the list has here.
    public static func accepted(_ name: String?) -> String {
        "added to Lists as \(name.flatMap { fhBlank($0) ? nil : $0 } ?? "a new list")"
    }

    /// A shared list's line: "Groceries — shared with The Smiths" (ours) or
    /// "Camping — from The Smiths" (theirs).
    public static func sharedLine(_ list: String, origin: String, friend: String) -> String {
        origin == "us" ? "\(list) \u{2014} shared with \(friend)" : "\(list) \u{2014} from \(friend)"
    }

    /// Their name for a list of theirs, when it isn't the name here.
    public static func theyCallIt(_ title: String) -> String { "they call it \u{201C}\(title)\u{201D}" }

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

    /// "This week" (server R6 FH-8), Android 3d99f404 word for word.
    public static let thisWeek = "This week"
    public static let notYou =
        "Not you? Only an admin can remove or block the house and change the admin password, on the dashboard."

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

    // MARK: quiet hours (1.4.1)

    public static let heldForQuietHours = "held for quiet hours"

    /// "held for quiet hours · read out at 7:00am".
    public static func heldThenReadOut(_ at: String) -> String { "\(heldForQuietHours) \u{00B7} read out at \(at)" }
}

/// The tabs, in the dashboard's order (1.4.1): Messages, then Shared lists.
/// The dashboard's admin zone is a third, "friends & connections", only while
/// this phone holds admin (1.5.0): which ones show is
/// ``fhTabsFor(_:sharedScreen:)``, never `allCases`.
public enum FhTab: String, CaseIterable, Sendable {
    case messages
    case shared

    /// Only while this phone holds admin (``FriendHousesAdminModel``).
    case connections

    public var label: String {
        switch self {
        case .messages: return FhWords.tabMessages
        case .shared: return FhWords.tabShared
        case .connections: return FhWords.tabConnections
        }
    }
}

// MARK: - quiet hours (1.4.1)

/// "07:00" (the house's clock, from the server) as the app writes a time
/// elsewhere — "7:00am" (``homeClock(ms:timeZone:)``'s format, the
/// dashboard's `fmtClock`). Nil for anything that isn't a time of day.
public func fhClock(_ hhmm: String?) -> String? {
    guard let hhmm else { return nil }
    let parts = hhmm.split(separator: ":", omittingEmptySubsequences: false)
    guard parts.count == 2, (1...2).contains(parts[0].count), parts[1].count == 2,
          parts.allSatisfy({ $0.allSatisfy { ("0"..."9").contains($0) } }),
          let hour = Int(parts[0]), let minute = Int(parts[1]), hour < 24, minute < 60
    else { return nil }
    let twelve = hour % 12 == 0 ? 12 : hour % 12
    return "\(twelve):\(String(format: "%02d", minute))\(hour < 12 ? "am" : "pm")"
}

/// Why a friend's messages aren't being said right now (the dashboard's
/// `fhQuietNote`): only for a choice that speaks in the rooms (`read`,
/// `sender`) while quiet hours are on. Nil otherwise, and for an `until`
/// that isn't a time.
///
/// **Ready, not drawn**: it belongs under a friend card's "when a message
/// comes from them" choice, and the phones hide the friends with the admin
/// zone in 1.4.1.
public func fhQuietNote(_ quiet: FhQuietHours?, alert: String?) -> String? {
    guard let quiet, alert == "read" || alert == "sender", let until = fhClock(quiet.until) else { return nil }
    let said = alert == "read" ? "read out" : "announced"
    if quiet.rooms == "some" {
        return quiet.readLater
            ? "Quiet hours in some rooms until \(until): new messages will be \(said) there at \(until)."
            : "Quiet hours in some rooms until \(until): new messages aren\u{2019}t \(said) there, and won\u{2019}t be later."
    }
    return quiet.readLater
        ? "Quiet hours until \(until): new messages will be \(said) at \(until)."
        : "Quiet hours until \(until): new messages wait in the inbox and won\u{2019}t be \(said) later."
}

/// What an incoming message says about quiet hours (the dashboard's
/// `fhHeldText`): "held for quiet hours", and "· read out at 7:00am" once a
/// room said it afterwards. Nil for an outgoing message or one not held.
public func fhHeldText(_ m: FhMessage, zone: TimeZone = .current) -> String? {
    guard m.incoming, m.held == "quiet_hours" else { return nil }
    guard let sec = fhIsoSeconds(m.readOutAt) else { return FhWords.heldForQuietHours }
    return FhWords.heldThenReadOut(homeClock(ms: sec * 1000, timeZone: zone))
}

// MARK: - wire times, bounded (security review, Android 22b3b9c8)

/// The latest time these screens format: 9999-12-31T23:59:59Z. Every time
/// on the wire is checked against 0...this before it reaches arithmetic, a
/// `Date` or a formatter: `sent_at` is the OTHER house's number, and
/// `Int64.min` in a subtraction is a trap on every open of that
/// conversation.
public let fhMaxEpochSec: Int64 = 253_402_300_799

/// `sec` when it is a time these screens can show, else nil.
public func fhEpochSec(_ sec: Int64?) -> Int64? {
    guard let sec, sec >= 0, sec <= fhMaxEpochSec else { return nil }
    return sec
}

/// An ISO time from the server, as seconds, when it parses to a time these
/// screens can show; else nil. The `Double` is checked before it becomes an
/// integer, so no conversion can trap.
public func fhIsoSeconds(_ iso: String?) -> Int64? {
    guard let date = parseInstant(iso) else { return nil }
    let t = date.timeIntervalSince1970
    guard t.isFinite, t >= 0, t <= Double(fhMaxEpochSec) else { return nil }
    return fhEpochSec(Int64(t.rounded(.down)))
}

/// The ISO time itself, when ``fhIsoSeconds(_:)`` accepts it.
public func fhIso(_ iso: String?) -> String? {
    fhIsoSeconds(iso) == nil ? nil : iso
}

/// "4m ago" for a server time, or "—" for one that is missing, unreadable or
/// out of range: never a trap (``relTime(_:nowMs:)`` turns the time into
/// milliseconds, which a year in the billions would overflow).
public func fhRelTime(_ iso: String?, nowMs: Int64) -> String {
    guard let ok = fhIso(iso) else { return "—" }
    return relTime(ok, nowMs: nowMs)
}

/// `|a - b|`, saturating at `Int64.max` instead of trapping. The callers
/// bound both first; this is the second lock on the same door.
func fhDistance(_ a: Int64, _ b: Int64) -> Int64 {
    let (difference, overflow) = a.subtractingReportingOverflow(b)
    if overflow || difference == .min { return .max }
    return difference < 0 ? -difference : difference
}

/// A friend house's presence line: "Online"; else "Last seen <relative>"
/// (the app's ``relTime(_:nowMs:)`` words: "4m ago", "yesterday"…); else
/// "Not reached yet". Nil — nothing drawn — when the server sent no presence
/// (`online` absent: a server from before it).
public func fhPresence(_ friend: FhFriend, nowMs: Int64) -> String? {
    guard let online = friend.online else { return nil }
    if online { return FhWords.online }
    guard let seen = friend.lastSeenAt, !fhBlank(seen) else { return FhWords.notReachedYet }
    // A time that can't be shown says nothing rather than something wrong
    // (Android 22b3b9c8).
    guard fhIso(seen) != nil else { return nil }
    return FhWords.lastSeen(fhRelTime(seen, nowMs: nowMs))
}

/// At most this many of the week's changes are listed.
public let fhChangesShown = 5

/// One of the week's changes in the dashboard's words, with its time when
/// it can be shown: "The Smiths became a friend house · 2d ago",
/// "“Groceries” was shared with The Patels · 5h ago" (Android
/// `FhWords.change`). Nil for a kind this build doesn't know. A null or
/// blank name is "A house" / "A list" / "a friend house"; a missing time, or
/// one outside 0...9999-12-31, drops the " · <time>" part.
public func fhChangeLine(_ change: FhChange, nowMs: Int64) -> String? {
    func named(_ value: String?, _ fallback: String) -> String {
        guard let value, !fhBlank(value) else { return fallback }
        return value
    }
    let what: String
    switch change.kind {
    case "friend":
        what = "\(named(change.friend, "A house")) became a friend house"
    case "shared_list":
        what = "\u{201C}\(named(change.list, "A list"))\u{201D} was shared with "
            + named(change.friend, "a friend house")
    default:
        return nil
    }
    guard fhIso(change.at) != nil else { return what }
    return "\(what) \u{00B7} \(fhRelTime(change.at, nowMs: nowMs))"
}

/// The "This week" lines: newest first (a change with no time to show
/// after those with one, the server's order among equals), at most
/// ``fhChangesShown``; nil — no section — when there is nothing to say
/// (absent, not a list, empty, or only kinds this build doesn't know).
public func fhChangeLines(_ changes: [FhChange]?, nowMs: Int64) -> [String]? {
    guard let changes else { return nil }
    let ordered = changes.enumerated()
        .map { (index: $0.offset, at: fhIsoSeconds($0.element.at) ?? -1, change: $0.element) }
        .sorted { $0.at != $1.at ? $0.at > $1.at : $0.index < $1.index }
    let lines = ordered.compactMap { fhChangeLine($0.change, nowMs: nowMs) }.prefix(fhChangesShown)
    return lines.isEmpty ? nil : Array(lines)
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
/// five minutes: "sent <time> (their clock)"; else nil, and nil for either
/// time outside 0...``fhMaxEpochSec``.
public func fhTheirClock(_ m: FhMessage, zone: TimeZone = .current) -> String? {
    guard m.incoming, let hereSec = fhIsoSeconds(m.createdAt), let theirs = fhEpochSec(m.sentAt) else { return nil }
    if fhDistance(theirs, hereSec) <= fhClockSkewSec { return nil }
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

/// Whether this is a friend's list offered to this house, not accepted yet:
/// what Accept and Decline are for.
public func fhIsOffer(_ shared: FhSharedList) -> Bool {
    shared.origin == "them" && shared.state == "invited"
}

/// The Shared lists tab's two groups, as the dashboard draws them: the
/// offers to this house, then everything shared now (ours waiting for them
/// to accept included). Only lists a URL may name
/// (``FhPaths/isSharedListId(_:)``), in the server's order.
public func fhSharedSections(_ lists: [FhSharedList]) -> (offers: [FhSharedList], current: [FhSharedList]) {
    let named = lists.filter { FhPaths.isSharedListId($0.id) }
    return (named.filter(fhIsOffer), named.filter { !fhIsOffer($0) })
}

/// A shared list's name here: the list's own, else its title.
public func fhListName(_ shared: FhSharedList) -> String {
    for name in [shared.listName, shared.title] {
        if let name, !fhBlank(name) { return name }
    }
    return "this list"
}

/// "they call it “…”" when their title for a list of theirs differs from
/// the name here; else nil.
public func fhTheirTitle(_ shared: FhSharedList) -> String? {
    guard shared.origin == "them", let title = shared.title, !fhBlank(title), title != fhListName(shared) else { return nil }
    return FhWords.theyCallIt(title)
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

/// How many messages a conversation holds on screen from its own re-reads,
/// newest kept (security review: the thread is a plain `VStack`, and a
/// house that writes all day would otherwise grow it without end).
public let fhThreadKeep = 200

/// The most "Load earlier" may hold, newest kept.
public let fhThreadKeepEarlier = 1000

/// `thread` with at most `keep` messages, the newest; trimming says there
/// are earlier ones to load again.
public func fhCapped(_ thread: FhThread, keep: Int) -> FhThread {
    guard thread.messages.count > keep, keep > 0 else { return thread }
    return FhThread(messages: Array(thread.messages.suffix(keep)), more: true, loaded: thread.loaded)
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
