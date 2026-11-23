import Foundation

// ---------------------------------------------------------------------------
// Friend Houses on the iPhone: "Message and share lists with friends’
// houses, house to house" (the server's domovoi/friend_houses/, proxied by the
// dashboard's web/backend/api/friend_houses.py).
//
// The port of Android's `friendhouses/FriendHousesApi.kt` (394473e2 on
// wf/android-friend-houses), decision for decision and word for word.
//
// What a household phone may do (owner decision 17): read the house inbox
// (one for the whole house), send a message, mark a conversation read, and
// see the friends and the shared lists. Everything about who the house
// trusts is an admin's, on the dashboard: the phone shows words for those,
// never a control (no misleading controls).
//
// The household token goes in the X-Device-Token header, as every request
// of ``DomovoiClient`` does: no cookie (the session has no cookie storage),
// no `?device_token=`. The only things a URL here ever carries are the other
// house's 64-hex house key and numbers (``FhPaths``): a message, a name or a
// title travels only in a body.
//
// Every body, name, title and last_error comes from ANOTHER house and is
// hostile text: the screens show it with `Text(verbatim:)` only
// (`ios/Domovoi/FriendHouses/FriendHousesPlainText.swift`), nothing here
// logs it, and nothing puts it in an error's words.
// ---------------------------------------------------------------------------

/// How the app describes the feature, word for word, wherever it does.
public let fhDescription = "Message and share lists with friends’ houses, house to house, with no company server in between"

/// The sentence a 403 shows, from any call: the household phone asked for
/// something only an admin may do.
public let fhAdminOnly = "Only an admin can do that, on the dashboard."

/// A message is 1 to 2,000 characters (the server's MAX_MESSAGE_CHARS,
/// counted as Python counts them: code points).
public let fhMessageMax = 2000

/// Optional fields read the way Kotlin's lenient decoder reads them: absent,
/// null or the wrong type is the default, never a failure of the whole row.
extension KeyedDecodingContainer {
    func fhOptional<T: Decodable>(_ type: T.Type, _ key: Key) -> T? {
        (try? decodeIfPresent(type, forKey: key)) ?? nil
    }

    /// A whole number that a server might write as `12` or `12.0`. A
    /// non-finite or absurd double is nil rather than a trap.
    func fhInt64(_ key: Key) -> Int64? {
        if let whole = fhOptional(Int64.self, key) { return whole }
        guard let real = fhOptional(Double.self, key), real.isFinite, abs(real) < 1e15 else { return nil }
        return Int64(real)
    }
}

/// The `GET /api/friend-houses` payload; only what the phone uses.
public struct FhStatus: Equatable, Sendable, Decodable {
    /// off | stopped | starting | on | unavailable | error.
    public let status: String
    /// A sentence when stopped or error, else nil.
    public let detail: String?
    public let houseName: String?
    /// Unread messages in the house inbox.
    public let unread: Int
    /// The week's trust changes — new friends and lists shared with one
    /// (server R6 FH-8) — or nil when the server sends none (one from before
    /// it). A row that is not one is dropped, not the whole list.
    public let changes: [FhChange]?

    public init(
        status: String = "",
        detail: String? = nil,
        houseName: String? = nil,
        unread: Int = 0,
        changes: [FhChange]? = nil
    ) {
        self.status = status
        self.detail = detail
        self.houseName = houseName
        self.unread = unread
        self.changes = changes
    }

    enum CodingKeys: String, CodingKey {
        case status, detail, unread, changes
        case houseName = "house_name"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = c.fhOptional(String.self, .status) ?? ""
        detail = c.fhOptional(String.self, .detail)
        houseName = c.fhOptional(String.self, .houseName)
        unread = c.fhOptional(Int.self, .unread) ?? 0
        changes = c.fhOptional([FhMaybe<FhChange>].self, .changes).map { $0.compactMap(\.value) }
    }
}

/// One of the week's trust changes (`friending.recent_changes`):
/// `{kind, friend, list, at}`, kind `friend` or `shared_list`. Names and
/// titles are this house's own words for the other house, still shown as
/// plain text only.
public struct FhChange: Equatable, Sendable, Decodable {
    public let kind: String
    public let friend: String?
    public let list: String?
    /// ISO time, this house's clock.
    public let at: String?

    public init(kind: String, friend: String? = nil, list: String? = nil, at: String? = nil) {
        self.kind = kind
        self.friend = friend
        self.list = list
        self.at = at
    }

    enum CodingKeys: String, CodingKey { case kind, friend, list, at }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = c.fhOptional(String.self, .kind) ?? ""
        friend = c.fhOptional(String.self, .friend)
        list = c.fhOptional(String.self, .list)
        at = c.fhOptional(String.self, .at)
    }
}

/// A row that decodes or is nil, so one bad row costs only itself.
struct FhMaybe<T: Decodable>: Decodable {
    let value: T?

    init(from decoder: any Decoder) throws {
        value = try? T(from: decoder)
    }
}

public struct FhFriend: Equatable, Sendable, Decodable {
    public let id: String
    /// What THIS household calls them.
    public let name: String
    /// Their house key (64 hex): the key of their conversation.
    public let house: String
    /// inbox | sender | read, set by an admin.
    public let alert: String
    public let verified: Bool
    public let addedAt: String?
    /// Presence (hand-off, 2026-11-17): heard from in the last 15 minutes.
    /// Nil when the server sends no presence at all (one from before it),
    /// and then nothing is shown. It is about the other *house* running,
    /// never about who is home.
    public let online: Bool?
    /// When that house was last heard from (ISO), nil: not reached yet.
    public let lastSeenAt: String?

    public init(
        id: String = "",
        name: String = "",
        house: String = "",
        alert: String = "inbox",
        verified: Bool = false,
        addedAt: String? = nil,
        online: Bool? = nil,
        lastSeenAt: String? = nil
    ) {
        self.id = id
        self.name = name
        self.house = house
        self.alert = alert
        self.verified = verified
        self.addedAt = addedAt
        self.online = online
        self.lastSeenAt = lastSeenAt
    }

    enum CodingKeys: String, CodingKey {
        case id, name, house, alert, verified, online
        case addedAt = "added_at"
        case lastSeenAt = "last_seen_at"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.fhOptional(String.self, .id) ?? ""
        name = c.fhOptional(String.self, .name) ?? ""
        house = c.fhOptional(String.self, .house) ?? ""
        alert = c.fhOptional(String.self, .alert) ?? "inbox"
        verified = c.fhOptional(Bool.self, .verified) ?? false
        addedAt = c.fhOptional(String.self, .addedAt)
        online = c.fhOptional(Bool.self, .online)
        lastSeenAt = c.fhOptional(String.self, .lastSeenAt)
    }
}

struct FhFriends: Decodable {
    let friends: [FhFriend]

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        friends = c.fhOptional([FhFriend].self, .friends) ?? []
    }

    enum CodingKeys: String, CodingKey { case friends }
}

public struct FhConversation: Equatable, Sendable, Decodable {
    public let house: String
    public let name: String?
    /// Nil: no longer a friend (read only, no compose).
    public let friendId: String?
    public let unread: Int
    public let lastId: Int64
    public let notDelivered: Int

    public init(
        house: String = "",
        name: String? = nil,
        friendId: String? = nil,
        unread: Int = 0,
        lastId: Int64 = 0,
        notDelivered: Int = 0
    ) {
        self.house = house
        self.name = name
        self.friendId = friendId
        self.unread = unread
        self.lastId = lastId
        self.notDelivered = notDelivered
    }

    enum CodingKeys: String, CodingKey {
        case house, name, unread
        case friendId = "friend_id"
        case lastId = "last_id"
        case notDelivered = "not_delivered"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        house = c.fhOptional(String.self, .house) ?? ""
        name = c.fhOptional(String.self, .name)
        friendId = c.fhOptional(String.self, .friendId)
        unread = c.fhOptional(Int.self, .unread) ?? 0
        lastId = c.fhInt64(.lastId) ?? 0
        notDelivered = c.fhOptional(Int.self, .notDelivered) ?? 0
    }
}

public struct FhInbox: Equatable, Sendable, Decodable {
    public let conversations: [FhConversation]
    public let unread: Int

    public init(conversations: [FhConversation] = [], unread: Int = 0) {
        self.conversations = conversations
        self.unread = unread
    }

    enum CodingKeys: String, CodingKey { case conversations, unread }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        conversations = c.fhOptional([FhConversation].self, .conversations) ?? []
        unread = c.fhOptional(Int.self, .unread) ?? 0
    }

    /// Newest first (the server's order, made certain), and only those a
    /// URL may name (``FhPaths/isHouseKey(_:)``).
    public func newestFirst() -> [FhConversation] {
        conversations.filter { FhPaths.isHouseKey($0.house) }.sorted { $0.lastId > $1.lastId }
    }
}

public struct FhMessage: Equatable, Sendable, Decodable {
    public let id: Int64
    /// in | out.
    public let direction: String
    public let body: String?
    /// in: unread | read; out: queued | sent | failed.
    public let state: String
    /// Unix seconds, the SENDER's clock: display only.
    public let sentAt: Int64?
    /// This house's clock.
    public let createdAt: String?
    public let deliveredAt: String?
    public let lastError: String?

    public init(
        id: Int64 = 0,
        direction: String = "in",
        body: String? = nil,
        state: String = "",
        sentAt: Int64? = nil,
        createdAt: String? = nil,
        deliveredAt: String? = nil,
        lastError: String? = nil
    ) {
        self.id = id
        self.direction = direction
        self.body = body
        self.state = state
        self.sentAt = sentAt
        self.createdAt = createdAt
        self.deliveredAt = deliveredAt
        self.lastError = lastError
    }

    enum CodingKeys: String, CodingKey {
        case id, direction, body, state
        case sentAt = "sent_at"
        case createdAt = "created_at"
        case deliveredAt = "delivered_at"
        case lastError = "last_error"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.fhInt64(.id) ?? 0
        direction = c.fhOptional(String.self, .direction) ?? "in"
        body = c.fhOptional(String.self, .body)
        state = c.fhOptional(String.self, .state) ?? ""
        // Another house's number, stored unchecked by ours: a time outside
        // 0...9999-12-31 is no time at all (``fhEpochSec(_:)``).
        sentAt = fhEpochSec(c.fhInt64(.sentAt))
        createdAt = c.fhOptional(String.self, .createdAt)
        deliveredAt = c.fhOptional(String.self, .deliveredAt)
        lastError = c.fhOptional(String.self, .lastError)
    }

    public var incoming: Bool { direction != "out" }
}

struct FhMessages: Decodable {
    let messages: [FhMessage]

    enum CodingKeys: String, CodingKey { case messages }

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        messages = c.fhOptional([FhMessage].self, .messages) ?? []
    }
}

public struct FhSharedList: Equatable, Sendable, Decodable {
    public let id: String
    /// active | invited (an admin hasn't accepted yet).
    public let state: String
    /// us: our list, shared with them; them: their list, here.
    public let origin: String
    /// What the sharing house calls it.
    public let title: String?
    public let listId: Int64?
    public let listName: String?
    public let friend: String?
    public let friendId: String?
    public let createdAt: String?
    public let waitingToSend: Int

    public init(
        id: String = "",
        state: String = "",
        origin: String = "",
        title: String? = nil,
        listId: Int64? = nil,
        listName: String? = nil,
        friend: String? = nil,
        friendId: String? = nil,
        createdAt: String? = nil,
        waitingToSend: Int = 0
    ) {
        self.id = id
        self.state = state
        self.origin = origin
        self.title = title
        self.listId = listId
        self.listName = listName
        self.friend = friend
        self.friendId = friendId
        self.createdAt = createdAt
        self.waitingToSend = waitingToSend
    }

    enum CodingKeys: String, CodingKey {
        case id, state, origin, title, friend
        case listId = "list_id"
        case listName = "list_name"
        case friendId = "friend_id"
        case createdAt = "created_at"
        case waitingToSend = "waiting_to_send"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.fhOptional(String.self, .id) ?? ""
        state = c.fhOptional(String.self, .state) ?? ""
        origin = c.fhOptional(String.self, .origin) ?? ""
        title = c.fhOptional(String.self, .title)
        listId = c.fhInt64(.listId)
        listName = c.fhOptional(String.self, .listName)
        friend = c.fhOptional(String.self, .friend)
        friendId = c.fhOptional(String.self, .friendId)
        createdAt = c.fhOptional(String.self, .createdAt)
        waitingToSend = c.fhOptional(Int.self, .waitingToSend) ?? 0
    }
}

struct FhSharedLists: Decodable {
    let sharedLists: [FhSharedList]

    enum CodingKeys: String, CodingKey { case sharedLists = "shared_lists" }

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sharedLists = c.fhOptional([FhSharedList].self, .sharedLists) ?? []
    }
}

public struct FhSent: Equatable, Sendable, Decodable {
    public let id: Int64
    public let state: String
    public let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, state
        case createdAt = "created_at"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.fhInt64(.id) ?? 0
        state = c.fhOptional(String.self, .state) ?? "queued"
        createdAt = c.fhOptional(String.self, .createdAt)
    }
}

struct FhMarked: Decodable {
    let marked: Int

    enum CodingKeys: String, CodingKey { case marked }

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        marked = c.fhOptional(Int.self, .marked) ?? 0
    }
}

/// Why the phone itself refused to make a call. Its words never repeat the
/// value: it is not one of ours.
public enum FhRefusal: Error, Equatable, Sendable, CustomStringConvertible {
    case notAHouseKey
    case notAFriendId

    public var description: String {
        switch self {
        case .notAHouseKey: return "not a house key"
        case .notAFriendId: return "not a friend id"
        }
    }
}

/// The routes, as paths. Only a house key that is 64 lowercase hex, and
/// numbers, ever go into one.
public enum FhPaths {
    public static let status = "/api/friend-houses"
    public static let friends = "/api/friend-houses/friends"
    public static let conversations = "/api/friend-houses/conversations"
    public static let messages = "/api/friend-houses/messages"
    public static let sharedLists = "/api/friend-houses/shared-lists"

    /// A conversation's page size (the dashboard's FH_PAGE).
    public static let page = 50

    /// True for a house key a URL may carry: exactly 64 lowercase hex.
    public static func isHouseKey(_ value: String?) -> Bool {
        guard let value else { return false }
        let bytes = Array(value.utf8)
        guard bytes.count == 64 else { return false }
        return bytes.allSatisfy { (0x30...0x39).contains($0) || (0x61...0x66).contains($0) }
    }

    /// True for a friend id the server could have made (its _ID rule:
    /// 1-64 of letters, digits, `_` and `-`).
    public static func isFriendId(_ value: String?) -> Bool {
        guard let value else { return false }
        let bytes = Array(value.utf8)
        guard (1...64).contains(bytes.count) else { return false }
        return bytes.allSatisfy {
            (0x30...0x39).contains($0) || (0x41...0x5A).contains($0) || (0x61...0x7A).contains($0)
                || $0 == 0x5F || $0 == 0x2D
        }
    }

    /// The key, checked; the refusal never repeats the value.
    static func checkedHouse(_ value: String) throws(FhRefusal) -> String {
        guard isHouseKey(value) else { throw FhRefusal.notAHouseKey }
        return value
    }

    /// One conversation, oldest first: `limit` (1-200) messages, those
    /// before the id `before` when it is given.
    public static func conversation(_ house: String, before: Int64? = nil, limit: Int = page) throws(FhRefusal) -> String {
        let key = try checkedHouse(house)
        let n = min(max(limit, 1), 200)
        var earlier = ""
        if let before, before >= 1 { earlier = "&before=\(before)" }
        return "\(conversations)/\(key)?limit=\(n)\(earlier)"
    }

    public static func markRead(_ house: String) throws(FhRefusal) -> String {
        let key = try checkedHouse(house)
        return "\(conversations)/\(key)/read"
    }
}

/// What `GET /api/friend-houses` says about showing Friend Houses at all.
///
/// SHOWN only on a 2xx whose `status` is on, starting, stopped or error
/// (stopped and error: on, but not running now; the inbox still reads and a
/// send queues). HIDDEN on off or unavailable (whatever `unread` says: a
/// house that was on keeps its messages when turned off), on anything that
/// isn't a status at all, and on 401, 403 (a server before F5 refuses the
/// household token here), 404 (a server before Friend Houses) and 501
/// (before setup). UNKNOWN: no answer, a 429, or a 5xx the core may be
/// behind: the last verdict for that server stands.
public enum FhAvailability: Equatable, Sendable {
    case shown(FhStatus)
    case hidden
    case unknown

    public var status: FhStatus? {
        if case .shown(let status) = self { return status }
        return nil
    }
}

public enum FhGate {
    public static let shownStatuses: Set<String> = ["on", "starting", "stopped", "error"]
    public static let hidingCodes: Set<Int> = [401, 403, 404, 501]

    /// `code`: the HTTP status, nil when nothing answered.
    public static func decide(code: Int?, body: Data?) -> FhAvailability {
        guard let code else { return .unknown }
        if (200...299).contains(code) {
            guard let body,
                  let status = try? JSONDecoder().decode(FhStatus.self, from: body),
                  shownStatuses.contains(status.status)
            else { return .hidden }
            return .shown(status)
        }
        if hidingCodes.contains(code) { return .hidden }
        // "Wait": says nothing about whether it is on.
        if code == 429 { return .unknown }
        if (400...499).contains(code) { return .hidden }
        return .unknown
    }

    /// A failed status call: an HTTP answer is decided by its code; anything
    /// that is not an answer (unreachable, held, not sent) is unknown.
    public static func decide(error: DomovoiError) -> FhAvailability {
        guard let failure = error.failure else { return .unknown }
        return decide(code: failure.status, body: Data(failure.body.utf8))
    }

    /// The verdict that holds after `next`, given the last one `previous`
    /// for the same server (nil: none yet, or another server's).
    public static func settle(_ previous: FhAvailability?, _ next: FhAvailability) -> FhAvailability {
        if next == .unknown { return previous ?? .hidden }
        return next
    }
}

/// The status line a shown screen carries, or nil for none: the server's
/// own sentence when stopped or in error, and a word while starting.
public func fhStatusLine(_ status: FhStatus) -> String? {
    let detail = status.detail.flatMap { fhBlank($0) ? nil : $0 }
    switch status.status {
    case "stopped", "error":
        return detail ?? (status.status == "error"
            ? "Friend Houses isn't running right now."
            : "Friend Houses is stopped right now.")
    case "starting":
        return detail ?? "Friend Houses is starting."
    default:
        return nil
    }
}

func fhBlank(_ text: String) -> Bool {
    text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
}

/// The `detail` string of a JSON error body, or nil (none, or not a
/// sentence: FastAPI's validation list).
public func fhDetail(_ body: String?) -> String? {
    guard let detail = jsonDetail(of: body), !fhBlank(detail) else { return nil }
    return detail
}

/// What a failed call shows, as the dashboard shows it: a 403 is
/// ``fhAdminOnly``; a 400 or 404 is the server's `detail`, as written (a
/// sentence for a person); any other status is "<action> failed (<code>)";
/// a call that got no answer is the app's usual failure line. Never the
/// request's own text.
public func fhProblem(_ action: String, _ error: any Error) -> String {
    if let error = error as? DomovoiError {
        if let failure = error.failure {
            if failure.status == 403 { return fhAdminOnly }
            if failure.status == 400 || failure.status == 404, let detail = fhDetail(failure.body) {
                return detail
            }
            return "\(action) failed (\(failure.status))"
        }
        return failureText(action, error)
    }
    return "\(action) failed"
}

/// A message's length as the server counts it (code points).
public func fhLength(_ text: String) -> Int {
    text.unicodeScalars.count
}

/// Whether `text` may be sent: something besides spaces, at most
/// ``fhMessageMax`` characters.
public func fhSendable(_ text: String) -> Bool {
    !fhBlank(text) && fhLength(text) <= fhMessageMax
}

/// The household routes, over the app's one client.
///
/// Every call takes the server it is **for** (`server`, the client's
/// `onlyTo`): a message typed for one house's friend is never sent to the
/// house the phone moved to while it was being typed.
public struct FriendHousesApi: Sendable {

    let client: DomovoiClient

    public init(client: DomovoiClient) {
        self.client = client
    }

    /// The availability gate's read. Never raises the pairing screen
    /// (`trackPairing: false`) and never throws: a 401 or 403 HERE means
    /// "not available on this server" (a server before F5), not a bad token.
    public func status(server: String? = nil) async -> FhAvailability {
        do throws(DomovoiError) {
            let data = try await client.raw(method: "GET", path: FhPaths.status, trackPairing: false, onlyTo: server)
            return FhGate.decide(code: 200, body: data)
        } catch {
            return FhGate.decide(error: error)
        }
    }

    public func friends(server: String? = nil) async throws -> [FhFriend] {
        try await get(FhPaths.friends, server: server, as: FhFriends.self).friends
    }

    public func inbox(server: String? = nil) async throws -> FhInbox {
        try await get(FhPaths.conversations, server: server, as: FhInbox.self)
    }

    /// One page of a conversation, oldest first (``FhPaths/conversation(_:before:limit:)``).
    public func conversation(
        _ house: String,
        before: Int64? = nil,
        limit: Int = FhPaths.page,
        server: String? = nil
    ) async throws -> [FhMessage] {
        let path = try FhPaths.conversation(house, before: before, limit: limit)
        return try await get(path, server: server, as: FhMessages.self).messages
    }

    /// Mark that conversation read: how many were.
    public func markRead(_ house: String, server: String? = nil) async throws -> Int {
        let path = try FhPaths.markRead(house)
        let data = try await client.raw(method: "POST", path: path, body: DomovoiClient.emptyObject, onlyTo: server)
        return (try? DomovoiClient.decode(data, as: FhMarked.self))?.marked ?? 0
    }

    /// Queue a message to a friend. The text goes in the body only.
    public func send(friendId: String, text: String, server: String? = nil) async throws -> FhSent {
        guard FhPaths.isFriendId(friendId) else { throw FhRefusal.notAFriendId }
        let body = try JSONEncoder().encode(FhSendBody(friendId: friendId, text: text))
        let data = try await client.raw(method: "POST", path: FhPaths.messages, body: body, onlyTo: server)
        return try DomovoiClient.decode(data, as: FhSent.self)
    }

    public func sharedLists(server: String? = nil) async throws -> [FhSharedList] {
        try await get(FhPaths.sharedLists, server: server, as: FhSharedLists.self).sharedLists
    }

    private func get<T: Decodable>(_ path: String, server: String?, as type: T.Type) async throws -> T {
        let data = try await client.raw(method: "GET", path: path, onlyTo: server)
        return try DomovoiClient.decode(data, as: type)
    }
}

/// `{"friend_id", "text"}`, and nothing else.
struct FhSendBody: Encodable {
    let friendId: String
    let text: String

    enum CodingKeys: String, CodingKey {
        case friendId = "friend_id"
        case text
    }
}
