import Foundation
import Observation

/// Friends and connections on the phone (1.5.0; design §1's Friend Houses
/// jobs): accept or decline a friend request, remove or block a friend,
/// unblock a house. Only while this phone holds admin (``AdminSession``): the
/// tab is not there otherwise (``fhTabsFor(_:sharedScreen:)``). Making or
/// adding friend files, this house's name, renaming, verifying and apps stay
/// on the dashboard (``FhAdminWords/onDashboard``).
///
/// The port of Android 3ac999df: `FhPaths`' admin half and
/// `FriendHousesAdminApi` (`FriendHousesApi.kt`), `fhTabsFor` and
/// `FhAdminWords` (`FriendHousesAdminZone.kt`). ``FriendHousesAdminModel`` is
/// that file's state, apart from the view. Every name and `last_error` came
/// from another house: the app shows them only through `FhPlainText`.

// MARK: - the routes

public extension FhPaths {

    static let requests = "\(status)/requests"
    static let blocked = "\(status)/blocked"

    /// A friend id or request id, checked; the refusal never repeats it.
    internal static func checkedId(_ value: String) throws(FhRefusal) -> String {
        guard isFriendId(value) else { throw FhRefusal.notAFriendId }
        return value
    }

    static func acceptRequest(_ id: String) throws(FhRefusal) -> String {
        let checked = try checkedId(id)
        return "\(requests)/\(checked)/accept"
    }

    static func declineRequest(_ id: String) throws(FhRefusal) -> String {
        let checked = try checkedId(id)
        return "\(requests)/\(checked)/decline"
    }

    /// `DELETE` removes the friend.
    static func friend(_ id: String) throws(FhRefusal) -> String {
        let checked = try checkedId(id)
        return "\(friends)/\(checked)"
    }

    static func blockFriend(_ id: String) throws(FhRefusal) -> String {
        let path = try friend(id)
        return "\(path)/block"
    }

    /// `DELETE` unblocks the house.
    static func blockedHouse(_ house: String) throws(FhRefusal) -> String {
        let checked = try checkedHouse(house)
        return "\(blocked)/\(checked)"
    }
}

// MARK: - what they read

/// A friend request (admin read): in `waiting` (for an admin here) or
/// `accepted` (still telling them); out `unsent` or `sent`. Names and
/// `last_error` came from another house.
public struct FhRequest: Equatable, Sendable, Decodable, Identifiable {
    public let id: String
    public let direction: String
    public let state: String
    public let theirName: String?
    public let name: String?
    public let code: String?
    public let trying: Bool?
    public let lastError: String?

    public init(
        id: String = "", direction: String = "in", state: String = "", theirName: String? = nil,
        name: String? = nil, code: String? = nil, trying: Bool? = nil, lastError: String? = nil
    ) {
        self.id = id
        self.direction = direction
        self.state = state
        self.theirName = theirName
        self.name = name
        self.code = code
        self.trying = trying
        self.lastError = lastError
    }

    enum CodingKeys: String, CodingKey {
        case id, direction, state, name, code, trying
        case theirName = "their_name"
        case lastError = "last_error"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.fhOptional(String.self, .id) ?? ""
        direction = c.fhOptional(String.self, .direction) ?? "in"
        state = c.fhOptional(String.self, .state) ?? ""
        theirName = c.fhOptional(String.self, .theirName)
        name = c.fhOptional(String.self, .name)
        code = c.fhOptional(String.self, .code)
        trying = c.fhOptional(Bool.self, .trying)
        lastError = c.fhOptional(String.self, .lastError)
    }

    /// Waiting here for an admin's yes or no.
    public var waitingHere: Bool { direction == "in" && state == "waiting" }
}

struct FhRequests: Decodable {
    let requests: [FhRequest]

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        requests = c.fhOptional([FhRequest].self, .requests) ?? []
    }

    enum CodingKeys: String, CodingKey { case requests }
}

public struct FhBlockedHouse: Equatable, Sendable, Decodable {
    public let house: String
    public let name: String?
    public let blockedAt: String?

    public init(house: String = "", name: String? = nil, blockedAt: String? = nil) {
        self.house = house
        self.name = name
        self.blockedAt = blockedAt
    }

    enum CodingKeys: String, CodingKey {
        case house, name
        case blockedAt = "blocked_at"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        house = c.fhOptional(String.self, .house) ?? ""
        name = c.fhOptional(String.self, .name)
        blockedAt = c.fhOptional(String.self, .blockedAt)
    }
}

struct FhBlockedList: Decodable {
    let blocked: [FhBlockedHouse]

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        blocked = c.fhOptional([FhBlockedHouse].self, .blocked) ?? []
    }

    enum CodingKeys: String, CodingKey { case blocked }
}

// MARK: - the client

/// The admin routes the phone uses (accept or decline a request, remove,
/// block, unblock), each through the phone's admin sign-in: `headers` and
/// `onlyTo` come from ``AdminSession/call(_:)`` or ``AdminSession/act(_:)``,
/// never stored here.
public struct FriendHousesAdminApi: Sendable {

    let client: DomovoiClient

    public init(client: DomovoiClient) {
        self.client = client
    }

    public func requests(headers: [String: String], onlyTo: String) async throws -> [FhRequest] {
        let data = try await client.raw(method: "GET", path: FhPaths.requests, headers: headers, onlyTo: onlyTo)
        return try DomovoiClient.decode(data, as: FhRequests.self).requests
    }

    public func blocked(headers: [String: String], onlyTo: String) async throws -> [FhBlockedHouse] {
        let data = try await client.raw(method: "GET", path: FhPaths.blocked, headers: headers, onlyTo: onlyTo)
        return try DomovoiClient.decode(data, as: FhBlockedList.self).blocked
    }

    /// Accept, with the name this house calls them.
    public func accept(_ id: String, name: String, headers: [String: String], onlyTo: String) async throws {
        let path = try FhPaths.acceptRequest(id)
        let body = try JSONEncoder().encode(["name": name.trimmingCharacters(in: .whitespacesAndNewlines)])
        _ = try await client.raw(method: "POST", path: path, body: body, headers: headers, onlyTo: onlyTo)
    }

    public func decline(_ id: String, headers: [String: String], onlyTo: String) async throws {
        let path = try FhPaths.declineRequest(id)
        _ = try await client.raw(method: "POST", path: path, body: DomovoiClient.emptyObject, headers: headers, onlyTo: onlyTo)
    }

    public func remove(friendId: String, headers: [String: String], onlyTo: String) async throws {
        let path = try FhPaths.friend(friendId)
        _ = try await client.raw(method: "DELETE", path: path, headers: headers, onlyTo: onlyTo)
    }

    /// Block: answers whether the house is moving to a new address.
    public func block(
        friendId: String, keepMessages: Bool, newAddress: Bool, headers: [String: String], onlyTo: String
    ) async throws -> Bool {
        let path = try FhPaths.blockFriend(friendId)
        let body = try JSONEncoder().encode(["keep_messages": keepMessages, "new_address": newAddress])
        let data = try await client.raw(method: "POST", path: path, body: body, headers: headers, onlyTo: onlyTo)
        let reply = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        return reply?["new_address"] as? Bool == true
    }

    public func unblock(_ house: String, headers: [String: String], onlyTo: String) async throws {
        let path = try FhPaths.blockedHouse(house)
        _ = try await client.raw(method: "DELETE", path: path, headers: headers, onlyTo: onlyTo)
    }
}

// MARK: - the tabs and the words

/// The tabs this phone shows: the household's two, and the admin one while
/// this phone holds admin (signed in, or kept today) and is not a shared
/// screen. `fhTabsFor`.
public func fhTabsFor(_ admin: AdminSession.State, sharedScreen: Bool) -> [FhTab] {
    if admin == .signedOut || sharedScreen { return [.messages, .shared] }
    return [.messages, .shared, .connections]
}

/// The admin tab's words (`FhAdminWords`), with the iPhone's Face ID where
/// Android says fingerprint or face.
public enum FhAdminWords {
    public static let sub = "Requests, friends and blocked houses, as admin on this phone."
    public static let onDashboard =
        "Making a friend file, adding a friend from their file, this house's name, renaming and checking codes are on the dashboard."
    public static let noRequests = "No requests waiting."
    public static let noFriends = "No friend houses yet."
    public static let unlock = "show with Face ID"

    public static let requestsTitle = "Requests"
    public static let friendsTitle = "Friends"
    public static let blockedTitle = "Blocked"
    public static let nameField = "what do you call them?"
    public static let accept = "accept"
    public static let decline = "decline"
    public static let remove = "remove"
    public static let block = "block"
    public static let unblock = "unblock"
    public static let cancel = "cancel"
    public static let friendsNow = "friends now"
    public static let declined = "request declined"
    public static let declineTitle = "Decline this request?"

    public static func incoming(_ r: FhRequest) -> String {
        "\(fhNonBlank(r.theirName) ?? "A house") (calls itself) wants to be friends"
    }

    public static func code(_ code: String) -> String {
        "Code \(code). Optional: call them and check they see the same code."
    }

    public static func outgoing(_ r: FhRequest) -> String {
        let who = fhNonBlank(r.name) ?? fhNonBlank(r.theirName) ?? "That house"
        return r.state == "unsent" ? "Asked \(who): not reached yet" : "Asked \(who): waiting for them to accept"
    }

    public static func accepted(_ r: FhRequest) -> String {
        "\(r.name ?? r.theirName ?? "A house"): accepted, telling them now"
    }

    public static func declineBody(_ r: FhRequest) -> String {
        "\(fhNonBlank(r.theirName) ?? "This house") won’t become a friend of this house."
    }

    public static let removeBody = "They won’t be able to reach this house any more. (Block also stops them asking again.)"

    public static func blockBody(_ name: String) -> String {
        "They’re removed and can’t ask to be friends again unless you unblock them."
    }

    public static let keepMessages = "Keep our messages with them"
    public static let newAddress =
        "Move this house to a new private address (recommended): the blocked house can’t even reach the door. "
        + "Your other friends are told the new address automatically; messages may take a few minutes to get through while it changes."

    public static func removeTitle(_ name: String) -> String { "Remove \(name)?" }
    public static func blockTitle(_ name: String) -> String { "Block \(name)?" }
    public static func unblockTitle(_ name: String) -> String { "Unblock \(name)?" }
    public static func removed(_ name: String) -> String { "\(name) removed" }
    public static func unblocked(_ name: String) -> String { "\(name) unblocked" }

    public static func blocked(_ name: String, moving: Bool) -> String {
        moving ? "\(name) is blocked · this house is moving to a new private address" : "\(name) is blocked"
    }

    public static func unblockBody(_ name: String) -> String {
        "Unblock \(name)? They aren’t friends again; they can ask with a new friend file."
    }

    /// A friend's or blocked house's name, or "this house".
    public static func houseName(_ name: String?) -> String { fhNonBlank(name) ?? "this house" }
}

/// `takeIf { it.isNotBlank() }`.
func fhNonBlank(_ text: String?) -> String? {
    guard let text, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
    return text
}

// MARK: - the tab's state

/// The admin tab's state: requests, friends and blocked houses, and one
/// change at a time. `FriendsAndConnectionsTab`'s logic, apart from the view.
///
/// Signed in, ``load(viaAct:)`` reads at once through
/// ``AdminSession/call(_:)``; kept only, the "show with Face ID" tap reads
/// through ``AdminSession/act(_:)``. Every change goes through `act`.
@MainActor
@Observable
public final class FriendHousesAdminModel {

    /// Nil until a read with a session has landed.
    public private(set) var requests: [FhRequest]?
    public private(set) var friends: [FhFriend] = []
    public private(set) var blocked: [FhBlockedHouse] = []
    public private(set) var problem: String?
    public private(set) var busy = false

    private let admin: AdminSession
    private let api: FriendHousesAdminApi
    private let household: FriendHousesApi

    public init(admin: AdminSession) {
        self.admin = admin
        api = FriendHousesAdminApi(client: admin.client)
        household = FriendHousesApi(client: admin.client)
    }

    public func load(viaAct: Bool) async {
        let api = api
        let read: @Sendable ([String: String], String) async throws -> ([FhRequest], [FhBlockedHouse]) = { h, srv in
            (try await api.requests(headers: h, onlyTo: srv), try await api.blocked(headers: h, onlyTo: srv))
        }
        do {
            let (r, b) = viaAct ? try await admin.act(read) : try await admin.call(read)
            requests = r
            blocked = b
            friends = try await household.friends()
            problem = nil
        } catch is AdminSession.NotSignedIn {
            requests = nil
        } catch {
            problem = Self.problem("load", error)
        }
    }

    /// One admin change, through ``AdminSession/act(_:)`` (Face ID first
    /// when needed): the toast it ends in, or nil when it didn't (the
    /// problem line says why). A change that went through reads again.
    public func change(
        _ action: String,
        _ work: @escaping @Sendable (FriendHousesAdminApi, [String: String], String) async throws -> String
    ) async -> String? {
        busy = true
        problem = nil
        defer { busy = false }
        let api = api
        do {
            let said = try await admin.act { h, srv in try await work(api, h, srv) }
            await load(viaAct: false)
            return said
        } catch {
            problem = Self.problem(action, error)
            return nil
        }
    }

    public func accept(_ r: FhRequest, name: String) async -> String? {
        let id = r.id
        return await change("accept") { api, h, srv in
            try await api.accept(id, name: name, headers: h, onlyTo: srv)
            return FhAdminWords.friendsNow
        }
    }

    public func decline(_ r: FhRequest) async -> String? {
        let id = r.id
        return await change("decline") { api, h, srv in
            try await api.decline(id, headers: h, onlyTo: srv)
            return FhAdminWords.declined
        }
    }

    public func remove(_ f: FhFriend) async -> String? {
        let id = f.id
        let name = FhAdminWords.houseName(f.name)
        return await change("remove") { api, h, srv in
            try await api.remove(friendId: id, headers: h, onlyTo: srv)
            return FhAdminWords.removed(name)
        }
    }

    public func block(_ f: FhFriend, keepMessages: Bool, newAddress: Bool) async -> String? {
        let id = f.id
        let name = FhAdminWords.houseName(f.name)
        return await change("block") { api, h, srv in
            let moving = try await api.block(
                friendId: id, keepMessages: keepMessages, newAddress: newAddress, headers: h, onlyTo: srv
            )
            return FhAdminWords.blocked(name, moving: moving)
        }
    }

    public func unblock(_ b: FhBlockedHouse) async -> String? {
        let house = b.house
        let name = FhAdminWords.houseName(b.name)
        return await change("unblock") { api, h, srv in
            try await api.unblock(house, headers: h, onlyTo: srv)
            return FhAdminWords.unblocked(name)
        }
    }

    /// Why it didn't work: a resume's reason, the admin-only sentence, or
    /// ``fhProblem(_:_:)``'s.
    static func problem(_ action: String, _ error: any Error) -> String {
        if let why = (error as? AdminSession.AdminUnavailable)?.reason { return why }
        if error is AdminSession.NotSignedIn { return fhAdminOnly }
        return fhProblem(action, error)
    }
}
