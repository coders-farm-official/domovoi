import Foundation

// ---------------------------------------------------------------------------
// Groups (V060): rooms sharing one music queue, in 1.3.0.
//
// The phone mirrors the web on the three screens it already has
// (design-notes GROUPS-MOBILE-BRIEF.md): a room's card on Home
// (`web/static/home.jsx` HomeGroupChoices / HomeGroupChipText), a room's
// queue (`web/static/music.jsx` the queue tab's "shared with" line) and
// Settings → Rooms (`web/static/satellites.jsx` MusicGroupsCard). No screen
// of its own.
//
// What the server says, and nothing worked out here:
//
//   GET    /api/music/groups            {groups: [{id, name, rooms}],
//                                        shared: [{id, name, host_room, rooms,
//                                                  group_id, everywhere}]}
//   POST   /api/music/groups            {name, rooms}     400: a sentence
//   PATCH  /api/music/groups/{id}       {name?, rooms?}
//   DELETE /api/music/groups/{id}
//   POST   /api/music/groups/shared     {host_room, rooms | group_id |
//                                        everywhere}       409: a sentence
//   POST   /api/music/groups/shared/join  {room_id, host_room}
//   POST   /api/music/groups/shared/leave {room_id}
//
// and `group` on a room's now-playing (`/api/satellites`) and on its queue
// (`/api/music/queue/{room}`): the shared queue it hears, host first, or
// null. A member room's song and queue are already the host's on the wire.
//
// Household only: reading is a household read and every change a household
// action, never a guest's. A read that 404s is a server older than groups,
// and the feature is simply not there.
// ---------------------------------------------------------------------------

// MARK: - the wire

/// The shared queue a room hears (`NowPlayingGroup`, `web/backend/schemas.py`
/// ~:275): its player is the host's, heard in every room of ``rooms`` (host
/// first). ``name`` is the named group's ("Upstairs"), "Everywhere", or nil
/// for rooms joined one by one. ``id`` is the shared queue's.
public struct NowPlayingGroup: Equatable, Sendable, Decodable {

    public let id: String
    public let name: String?
    public let hostRoom: String
    public let rooms: [String]
    public let groupID: Int64?
    public let everywhere: Bool

    public init(
        id: String = "",
        name: String? = nil,
        hostRoom: String = "",
        rooms: [String] = [],
        groupID: Int64? = nil,
        everywhere: Bool = false
    ) {
        self.id = id
        self.name = name
        self.hostRoom = hostRoom
        self.rooms = rooms
        self.groupID = groupID
        self.everywhere = everywhere
    }

    enum CodingKeys: String, CodingKey {
        case id, name, rooms, everywhere
        case hostRoom = "host_room"
        case groupID = "group_id"
    }

    /// Lenient for the reason every Home model is: one odd field must not
    /// throw a whole rooms read away. An `id` that came as a number is kept
    /// as its digits.
    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let text = try? c.decodeIfPresent(String.self, forKey: .id) {
            id = text
        } else if let number = try? c.decodeIfPresent(Int64.self, forKey: .id) {
            id = String(number)
        } else {
            id = ""
        }
        name = try? c.decodeIfPresent(String.self, forKey: .name)
        hostRoom = (try? c.decodeIfPresent(String.self, forKey: .hostRoom)) ?? ""
        rooms = (try? c.decodeIfPresent([String].self, forKey: .rooms)) ?? []
        groupID = try? c.decodeIfPresent(Int64.self, forKey: .groupID)
        everywhere = (try? c.decodeIfPresent(Bool.self, forKey: .everywhere)) ?? false
    }

    /// The rooms other than `room`, in the server's order (host first).
    public func others(than room: String) -> [String] {
        rooms.filter { $0 != room }
    }
}

/// A named group: a lasting set of rooms the household made ("Upstairs").
public struct MusicGroup: Equatable, Sendable, Decodable, Identifiable {

    public let id: Int64
    public let name: String
    public let rooms: [String]

    public init(id: Int64, name: String, rooms: [String]) {
        self.id = id
        self.name = name
        self.rooms = rooms
    }

    enum CodingKeys: String, CodingKey { case id, name, rooms }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int64.self, forKey: .id)
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? ""
        rooms = (try? c.decodeIfPresent([String].self, forKey: .rooms)) ?? []
    }
}

/// `GET /api/music/groups`: the named groups and the shared queues playing
/// now.
public struct MusicGroupsList: Equatable, Sendable, Decodable {

    public let groups: [MusicGroup]
    public let shared: [NowPlayingGroup]

    public init(groups: [MusicGroup] = [], shared: [NowPlayingGroup] = []) {
        self.groups = groups
        self.shared = shared
    }

    enum CodingKeys: String, CodingKey { case groups, shared }

    /// A group that will not decode is skipped, not the whole list.
    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let groupRows: [LenientGroupRow<MusicGroup>] =
            (try? c.decodeIfPresent([LenientGroupRow<MusicGroup>].self, forKey: .groups)) ?? []
        let sharedRows: [LenientGroupRow<NowPlayingGroup>] =
            (try? c.decodeIfPresent([LenientGroupRow<NowPlayingGroup>].self, forKey: .shared)) ?? []
        groups = groupRows.compactMap { $0.value }
        shared = sharedRows.compactMap { $0.value }
    }
}

/// One row that may or may not decode.
struct LenientGroupRow<Row: Decodable>: Decodable {
    let value: Row?
    init(from decoder: any Decoder) throws {
        value = try? Row(from: decoder)
    }
}

// MARK: - whether this phone may see the controls

/// What the groups read came back as.
///
/// **A 404 is not an error**: it is a server older than groups, and the
/// feature is not there. A 401 or 403 is the server saying this phone is not
/// a household device. Neither is ever shown as a failure.
public enum MusicGroupsAnswer: Equatable, Sendable {
    case list(MusicGroupsList)
    case notHousehold
    case notOnThisServer
}

/// The groups read, with the server it was asked of
/// ([[read-after-await-wrong-key]]: another house's groups are never drawn).
public struct MusicGroupsRead: Equatable, Sendable {
    public let server: String
    public let answer: MusicGroupsAnswer

    public init(server: String, answer: MusicGroupsAnswer) {
        self.server = server
        self.answer = answer
    }
}

/// Whether the household's group controls are drawn.
public enum GroupsAccess: Equatable, Sendable {
    /// Not answered yet (or answered for another server): nothing is drawn.
    case unknown
    /// A household phone on a server with groups.
    case household
    /// A guest, a phone the server refused, or an older server.
    case hidden

    public var showsControls: Bool { self == .household }
}

/// The one rule: **household only.** An unpaired phone (a guest) is never
/// offered a group control and never asks; a phone the server refused
/// (401/403, on the read or on a write since) and a server that has no
/// groups (404) hide them silently.
public func groupsAccess(paired: Bool, read: MusicGroupsRead?, server: String, refused: Bool) -> GroupsAccess {
    guard paired, !refused else { return .hidden }
    guard let read, read.server == server else { return .unknown }
    switch read.answer {
    case .list: return .household
    case .notHousehold, .notOnThisServer: return .hidden
    }
}

/// The named groups the read holds for `server`, or nil.
public func groupsList(_ read: MusicGroupsRead?, server: String) -> MusicGroupsList? {
    guard let read, read.server == server, case .list(let list) = read.answer else { return nil }
    return list
}

/// Whether a failure is the server saying this phone is not the household's.
public func groupsRefused(_ error: DomovoiError) -> Bool {
    if case .refused = error { return true }
    return error.failure.map { $0.status == 401 || $0.status == 403 } ?? false
}

// MARK: - the calls

/// The `/api/music/groups` routes. Each is one request.
public struct MusicGroupsApi: Sendable {

    public static let path = "/api/music/groups"

    private let client: DomovoiClient

    public init(client: DomovoiClient) {
        self.client = client
    }

    /// The list, as a ``MusicGroupsAnswer``: 404 and 401/403 are answers,
    /// not failures. **Untracked**: whether this phone may see groups says
    /// nothing about whether it is paired, and a guest's refusal must not
    /// raise the pairing screen.
    public func read() async throws(DomovoiError) -> MusicGroupsAnswer {
        do throws(DomovoiError) {
            return .list(try await client.getUntracked(Self.path, as: MusicGroupsList.self))
        } catch let trouble {
            if trouble.failure?.status == 404 { return .notOnThisServer }
            if groupsRefused(trouble) { return .notHousehold }
            throw trouble
        }
    }

    public func create(name: String, rooms: [String]) async throws(DomovoiError) {
        try await client.send(method: "POST", path: Self.path, body: Self.groupBody(name: name, rooms: rooms))
    }

    public func update(_ id: Int64, name: String, rooms: [String]) async throws(DomovoiError) {
        try await client.send(method: "PATCH", path: "\(Self.path)/\(id)", body: Self.groupBody(name: name, rooms: rooms))
    }

    public func delete(_ id: Int64) async throws(DomovoiError) {
        try await client.send(method: "DELETE", path: "\(Self.path)/\(id)")
    }

    /// One choice of a room's share menu: share, join or leave.
    public func perform(_ choice: GroupChoice) async throws(DomovoiError) {
        try await client.send(method: "POST", path: "\(Self.path)/\(choice.path)", body: choice.body)
    }

    /// `room` leaves its group and goes quiet; the others play on.
    public func leave(_ room: String) async throws(DomovoiError) {
        try await client.send(method: "POST", path: "\(Self.path)/shared/leave", body: Self.leaveBody(room: room))
    }

    /// `{"name":…,"rooms":[…]}`.
    public static func groupBody(name: String, rooms: [String]) -> Data {
        RoomPlayBodies.encode(["name": name, "rooms": rooms])
    }

    /// `{"room_id":…}`.
    public static func leaveBody(room: String) -> Data {
        RoomPlayBodies.encode(["room_id": room])
    }
}

// MARK: - a room's card

/// One choice of a room card's share menu (`HomeGroupChoices`,
/// `home.jsx` ~:601): other rooms hear this one, this one hears another, or
/// it leaves its group.
public struct GroupChoice: Equatable, Sendable, Identifiable {

    public enum Kind: Equatable, Sendable {
        case group(Int64)
        case everywhere
        case share(String)
        case join(String)
        case leave
    }

    public let room: String
    public let kind: Kind
    public let label: String
    public let hint: String?

    public init(room: String, kind: Kind, label: String, hint: String? = nil) {
        self.room = room
        self.kind = kind
        self.label = label
        self.hint = hint
    }

    /// The web's `key`.
    public var id: String {
        switch kind {
        case .group(let id): return "group:\(id)"
        case .everywhere: return "everywhere"
        case .share(let other): return "share:\(other)"
        case .join(let other): return "join:\(other)"
        case .leave: return "leave"
        }
    }

    /// Under `/api/music/groups/`.
    public var path: String {
        switch kind {
        case .group, .everywhere, .share: return "shared"
        case .join: return "shared/join"
        case .leave: return "shared/leave"
        }
    }

    /// The web's bodies, exactly.
    public var body: Data {
        switch kind {
        case .group(let id): return RoomPlayBodies.encode(["host_room": room, "group_id": id])
        case .everywhere: return RoomPlayBodies.encode(["host_room": room, "everywhere": true])
        case .share(let other): return RoomPlayBodies.encode(["host_room": room, "rooms": [other]])
        case .join(let host): return RoomPlayBodies.encode(["room_id": room, "host_room": host])
        case .leave: return RoomPlayBodies.encode(["room_id": room])
        }
    }

    /// Every room the choice touches is busy while it is out (the web's
    /// `[room, ...body.rooms]`).
    public var touched: [String] {
        if case .share(let other) = kind { return [room, other] }
        return [room]
    }

    public var isLeave: Bool { kind == .leave }

    /// What the phone says when it worked: "kitchen left the group", else
    /// "play in garage too · done".
    public var doneText: String {
        isLeave ? GroupStrings.leftTheGroup(room) : "\(label)\(HomeRoomStrings.middot)\(GroupStrings.done)"
    }

    /// The verb a failure is reported with.
    public var failVerb: String { isLeave ? GroupStrings.leaveVerb : GroupStrings.shareVerb }
}

/// What a room's card offers (`HomeGroupChoices`, `home.jsx` ~:601).
///
/// - A room that is **playing** (or paused): play in each named group that
///   has a connected room it does not reach yet, play everywhere, and play in
///   each connected room outside its group.
/// - Any room: join each connected room playing outside its group.
/// - A room **in a group**: leave the group.
///
/// So a quiet room gets only the joins, and "leave" is only ever a member's.
/// Offline rooms are never offered.
public func groupChoices(_ room: HomeRoom, rooms: [HomeRoom], groups: [MusicGroup]?) -> [GroupChoice] {
    let group = room.nowPlaying?.group
    let inMine = Set(group?.rooms ?? [room.roomID])
    let playingHere = roomSong(room) != nil
    let others = rooms.filter { $0.roomID != room.roomID && $0.online }
    let otherIDs = Set(others.map(\.roomID))
    var out: [GroupChoice] = []
    if playingHere {
        for named in groups ?? [] {
            let missing = named.rooms.filter { !inMine.contains($0) && otherIDs.contains($0) }
            if !missing.isEmpty {
                out.append(GroupChoice(room: room.roomID, kind: .group(named.id), label: GroupStrings.playIn(named.name)))
            }
        }
        if others.contains(where: { !inMine.contains($0.roomID) }) {
            out.append(GroupChoice(
                room: room.roomID, kind: .everywhere,
                label: GroupStrings.playEverywhere, hint: GroupStrings.playEverywhereHint
            ))
        }
        for other in others where !inMine.contains(other.roomID) {
            out.append(GroupChoice(room: room.roomID, kind: .share(other.roomID), label: GroupStrings.playIn(other.roomID)))
        }
    }
    for other in others where !inMine.contains(other.roomID) && roomSong(other) != nil {
        out.append(GroupChoice(
            room: room.roomID, kind: .join(other.roomID),
            label: GroupStrings.join(other.roomID), hint: GroupStrings.joinHint(other.roomID)
        ))
    }
    if group != nil {
        out.append(GroupChoice(room: room.roomID, kind: .leave, label: GroupStrings.leaveTheGroup, hint: GroupStrings.leaveHint))
    }
    return out
}

/// The chip on a room in a group (`HomeGroupChipText`): "Upstairs · with
/// garage, office", or "with garage" for rooms joined one by one. Only for a
/// connected room, as the web draws it.
public func groupChipText(_ room: HomeRoom) -> String? {
    guard room.online, let group = room.nowPlaying?.group else { return nil }
    let others = group.others(than: room.roomID)
    let withText: String = others.isEmpty ? "" : GroupStrings.withPrefix + others.joined(separator: ", ")
    if let name = group.name, !name.isEmpty {
        return withText.isEmpty ? name : name + HomeRoomStrings.middot + withText
    }
    return withText.isEmpty ? nil : withText
}

// MARK: - a room's queue

/// The line over a queue a room shares (`music.jsx` ~:1796): "Upstairs ·
/// shared with 2 other rooms (garage, office)".
public func groupQueueLine(_ group: NowPlayingGroup, room: String) -> String {
    let others = group.others(than: room)
    var name = ""
    if let given = group.name, !given.isEmpty { name = given + HomeRoomStrings.middot }
    let count = others.count
    return "\(name)shared with \(count) other room\(count == 1 ? "" : "s") (\(others.joined(separator: ", ")))"
}

// MARK: - what is said

/// A failure in the web's words: "share failed: nothing is playing in
/// kitchen" — the server's own sentence, from `detail` (``queueErrorText(_:)``,
/// the web's `apiErrorText`).
public func groupFailureText(_ verb: String, _ error: DomovoiError) -> String {
    switch error {
    case .refused, .rateLimited, .serverTrouble, .httpError:
        return "\(verb) failed: \(queueErrorText(error))"
    default:
        return failureText(verb, error)
    }
}

/// Saving a group: a 400 is a sentence about the name, shown **as it is**
/// ("A group's name can't use words like station, radio or playlist, …");
/// anything else is "save the group failed: …".
public func groupSaveFailureText(_ error: DomovoiError) -> String {
    if error.failure?.status == 400 { return queueErrorText(error) }
    return groupFailureText(GroupStrings.saveVerb, error)
}

/// The groups strings, the web's own words (lower case, as Home's are).
public enum GroupStrings {

    public static func playIn(_ place: String) -> String { "play in \(place) too" }
    public static let playEverywhere = "play everywhere"
    public static let playEverywhereHint = "every connected room hears it"
    public static func join(_ room: String) -> String { "join \(room)" }
    public static func joinHint(_ room: String) -> String { "hear what plays in \(room)" }
    public static let leaveTheGroup = "leave the group"
    public static let leaveHint = "this room stops, the others play on"
    public static let withPrefix = "with "
    public static let done = "done"
    public static func leftTheGroup(_ room: String) -> String { "\(room) left the group" }
    public static let shareVerb = "share"
    public static let leaveVerb = "leave the group"
    public static let saveVerb = "save the group"
    public static let deleteVerb = "delete the group"

    /// The share menu's button (`HomeGroupMenu`).
    public static func menuLabel(_ room: String) -> String {
        "play \(room)\u{2019}s music in other rooms, or join another room"
    }

    /// The queue's "leave" (`music.jsx` ~:1808).
    public static let leave = "leave"

    // Settings → Rooms, the Groups card (`satellites.jsx` ~:2483).
    public static let title = "Groups"
    public static let newGroup = "new group"
    public static let help = "Rooms in a group play the same music together: say \u{201C}play jazz upstairs\u{201D}, "
        + "or use a room\u{2019}s share button on Home. \u{201C}Everywhere\u{201D} needs no group. "
        + "Each room keeps its own volume. Speakers you can hear from the same spot may sound a little out of step."
    public static let noGroupsYet = "no groups yet"
    public static let needTwoRooms = "set up two rooms to make a group"
    public static let nameLabel = "name"
    public static let namePlaceholder = "upstairs"
    public static let nameFieldLabel = "group name"
    public static let roomsLabel = "rooms (two or more)"
    public static let cancel = "cancel"
    public static let save = "save"
    public static let delete = "delete"
    public static func editLabel(_ name: String) -> String { "edit \(name)" }
    public static func deleteLabel(_ name: String) -> String { "delete \(name)" }
    public static func deleteTitle(_ name: String) -> String { "Delete the group \(name)?" }
    public static let deleteBody = "Music playing in its rooms keeps playing."
    public static func made(_ name: String) -> String { "group \(name) made" }
    public static func saved(_ name: String) -> String { "group \(name) saved" }
    public static func deleted(_ name: String) -> String { "group \(name) deleted" }
}

/// A group form's rule (`MusicGroupForm`'s `ready`): a name after trimming,
/// and two or more rooms.
public func groupFormReady(name: String, rooms: Set<String>, busy: Bool) -> Bool {
    !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && rooms.count >= 2 && !busy
}

/// The rooms a group form offers: the house's, plus any the group already
/// holds that the house no longer reports, sorted (`satellites.jsx` ~:2548).
public func groupFormRooms(house: [String], group: MusicGroup?) -> [String] {
    Array(Set(house + (group?.rooms ?? []))).sorted()
}
