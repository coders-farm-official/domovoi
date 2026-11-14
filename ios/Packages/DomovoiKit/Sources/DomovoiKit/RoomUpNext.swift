import Foundation

/// A room's Up next (P4), as a room's queue shows it.
///
/// `RoomUpNext.kt` (Android cb780e90 and bd274c2f). The server runs the
/// room's session itself — the core's `up_next_extender` refills the room's
/// queue — so the app only shows it and edits it. The web's shared bits
/// (components.jsx `upNextBasis`, `roomUpNextRows`, `roomUpNextLine`,
/// `roomSaveNote`) are the reference for the behaviour and the words.
///
/// Contract: `docs/API_REFERENCE.md` §3.5a (the queue routes) and §3.5f;
/// the shapes are `web/backend/api/music_queue.py`'s `RoomQueue.up_next` and
/// `domovoi/up_next/rooms.py`'s `queue_view`.

/// A room queue's `up_next` (nil without a session).
public struct RoomUpNext: Equatable, Sendable, Decodable {
    public let sessionID: String?
    /// `list`, `library` or `continue`.
    public let mode: String?
    public let autoplay: Bool?
    public let seed: RoomUpNextSeed?
    public let ended: Bool

    public init(
        sessionID: String? = nil,
        mode: String? = nil,
        autoplay: Bool? = nil,
        seed: RoomUpNextSeed? = nil,
        ended: Bool = false
    ) {
        self.sessionID = sessionID
        self.mode = mode
        self.autoplay = autoplay
        self.seed = seed
        self.ended = ended
    }

    private enum CodingKeys: String, CodingKey {
        case mode, autoplay, seed, ended
        case sessionID = "session_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        sessionID = try? values.decodeIfPresent(String.self, forKey: .sessionID)
        mode = try? values.decodeIfPresent(String.self, forKey: .mode)
        autoplay = try? values.decodeIfPresent(Bool.self, forKey: .autoplay)
        seed = try? values.decodeIfPresent(RoomUpNextSeed.self, forKey: .seed)
        ended = (try? values.decodeIfPresent(Bool.self, forKey: .ended)) ?? false
    }
}

/// What the room's mix is built from.
public struct RoomUpNextSeed: Equatable, Sendable, Decodable {
    public let trackID: Int64?
    public let title: String?
    public let artist: String?
    public let label: String?
    public let list: RoomUpNextList?

    public init(
        trackID: Int64? = nil,
        title: String? = nil,
        artist: String? = nil,
        label: String? = nil,
        list: RoomUpNextList? = nil
    ) {
        self.trackID = trackID
        self.title = title
        self.artist = artist
        self.label = label
        self.list = list
    }

    private enum CodingKeys: String, CodingKey {
        case title, artist, label, list
        case trackID = "track_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        trackID = try? values.decodeIfPresent(Int64.self, forKey: .trackID)
        title = try? values.decodeIfPresent(String.self, forKey: .title)
        artist = try? values.decodeIfPresent(String.self, forKey: .artist)
        label = try? values.decodeIfPresent(String.self, forKey: .label)
        list = try? values.decodeIfPresent(RoomUpNextList.self, forKey: .list)
    }
}

public struct RoomUpNextList: Equatable, Sendable, Decodable {
    public let kind: String?
    public let name: String?

    public init(kind: String? = nil, name: String? = nil) {
        self.kind = kind
        self.name = name
    }
}

/// `POST …/queue/{room}/save-playlist`'s 201.
public struct RoomSaveResult: Equatable, Sendable, Decodable {
    public let playlistID: Int64
    public let name: String?
    public let saved: Int
    public let skipped: Int
    public let duplicates: Int

    public init(playlistID: Int64 = 0, name: String? = nil, saved: Int = 0, skipped: Int = 0, duplicates: Int = 0) {
        self.playlistID = playlistID
        self.name = name
        self.saved = saved
        self.skipped = skipped
        self.duplicates = duplicates
    }

    private enum CodingKeys: String, CodingKey {
        case name, saved, skipped, duplicates
        case playlistID = "playlist_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        playlistID = (try? values.decodeIfPresent(Int64.self, forKey: .playlistID)) ?? 0
        name = try? values.decodeIfPresent(String.self, forKey: .name)
        saved = (try? values.decodeIfPresent(Int.self, forKey: .saved)) ?? 0
        skipped = (try? values.decodeIfPresent(Int.self, forKey: .skipped)) ?? 0
        duplicates = (try? values.decodeIfPresent(Int.self, forKey: .duplicates)) ?? 0
    }
}

// MARK: - what the room queue view shows

public extension QueueItem {
    /// An entry the room's Up next picked (`origin: auto`).
    var isPick: Bool { origin == "auto" }
}

public enum RoomUpNextRules {

    /// The head's "why": what the mix is built from (the web's `upNextBasis`).
    public static func basis(mode: String?, seed: RoomUpNextSeed?) -> String {
        guard let seed else { return "" }
        let list = seed.list?.name.flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0 }
        if mode == "list", let list { return "shuffled from \(list)" }
        if mode == "continue", let list { return "more like \(list)" }
        if let title = seed.title, !title.trimmingCharacters(in: .whitespaces).isEmpty {
            return "based on \(title)"
        }
        return ""
    }

    /// The head's "why" for a room's queue; "" without a session.
    public static func basis(_ queue: RoomQueue) -> String {
        guard let upNext = queue.upNext else { return "" }
        return basis(mode: upNext.mode, seed: upNext.seed)
    }

    /// The room's waiting picks: the indices of the auto entries after the
    /// one playing.
    public static func rows(_ items: [QueueItem]) -> [Int] {
        let current = items.firstIndex(where: \.playing) ?? -1
        return items.indices.filter { $0 > current && items[$0].isPick }
    }

    /// The one muted line a room's Up next may show (the web's
    /// `roomUpNextLine`); nil for none.
    public static func line(_ queue: RoomQueue, waiting: Int) -> String? {
        guard queue.sessionID != nil else { return nil }
        let upNext = queue.upNext
        if queue.autoplay != true {
            if upNext?.mode == "list", waiting > 0 {
                return "autoplay is off — stops after the last song of \(upNext?.seed?.list?.name ?? "this list")"
            }
            return "autoplay is off — the queue stops at the end"
        }
        return waiting > 0 ? nil : "similar songs join here when the queue is nearly over"
    }

    /// "less like this" may be offered on `items`' entry `index`: a pick
    /// still to come.
    public static func lessLikeOffered(_ controls: RoomQueueControls, items: [QueueItem], index: Int) -> Bool {
        guard items.indices.contains(index) else { return false }
        let item = items[index]
        return controls.lessLike && item.isPick && item.upNextSeq != nil && rows(items).contains(index)
    }

    /// `q`'s entries as the view shows them: without the dropped picks, never
    /// the one playing.
    public static func visible(_ queue: RoomQueue, dropped: DroppedPicks?, server: String, room: String?) -> [QueueItem] {
        guard let dropped, dropped.server == server, dropped.room == room,
              dropped.sessionID == queue.sessionID else { return queue.items }
        return queue.items.filter { item in
            item.playing || !item.isPick || !(item.upNextSeq.map(dropped.seqs.contains) ?? false)
        }
    }

    /// Where a move of the shown row to `to` lands in the room's own queue.
    ///
    /// `roomMoveTarget` sends `items[to].pos` so that rows hidden from the
    /// view (dropped picks) do not shift it. **Only when rows are hidden**,
    /// here: with nothing hidden the shown index *is* the position, and an
    /// answer without `pos` decodes it as 0 — Android's `pos` is `Int = 0`
    /// too, so its every move on such an answer goes to the top.
    public static func moveTarget(shown: [QueueItem], all: [QueueItem], to: Int) -> Int {
        guard shown.count != all.count, shown.indices.contains(to) else { return to }
        return shown[to].pos
    }
}

/// Which controls a room queue shows, from what the server said about the
/// caller.
public struct RoomQueueControls: Equatable, Sendable {
    /// Tap a row to play it now (the household, or a guest on a playing room).
    public let playNow: Bool
    /// "save as playlist" (the household's, never a guest's).
    public let save: Bool
    /// The room's autoplay switch.
    public let autoplay: Bool
    /// "less like this" on a waiting pick.
    public let lessLike: Bool
    /// "clear queue" (never a guest's).
    public let clear: Bool

    public init(_ queue: RoomQueue) {
        let household = !queue.guest
        let session = queue.sessionID != nil
        playNow = queue.editable
        save = household && !queue.items.isEmpty
        autoplay = household && queue.editable && session
        lessLike = household && queue.editable && session
        clear = household && queue.editable && !queue.items.isEmpty
    }
}

/// The picks a "less like this" took back, shown gone at once (the server
/// takes them out of the room within ~5 s). Kept for the server, room and
/// session it was answered for: another house's or another session's queue
/// never loses a row by the same numbers.
public struct DroppedPicks: Equatable, Sendable {
    public let server: String
    public let room: String
    public let sessionID: String
    public let seqs: Set<Int>

    public init(server: String, room: String, sessionID: String, seqs: Set<Int>) {
        self.server = server
        self.room = room
        self.sessionID = sessionID
        self.seqs = seqs
    }

    public func plus(_ more: [Int]) -> DroppedPicks {
        DroppedPicks(server: server, room: room, sessionID: sessionID, seqs: seqs.union(more))
    }
}

// MARK: - the words (the web's)

public enum RoomUpNextStrings {
    public static let head = "up next"
    public static let autoplay = "autoplay"
    public static let pick = "up next pick"

    /// The name of a room's autoplay switch.
    public static func autoplayLabel(_ room: String) -> String {
        "autoplay in \(room): keep playing similar songs when the queue ends"
    }

    public static func autoplayNote(_ room: String, on: Bool) -> String {
        on ? "autoplay on in \(room)" : "autoplay off in \(room) — the queue stops at the end"
    }

    /// The name of "less like this" on a pick.
    public static func lessLikeLabel(_ artist: String?) -> String {
        "less like this: no more \(artist ?? "of this artist") in this mix"
    }

    /// What "less like this" said: the artist, and how many more picks went
    /// with it.
    public static func lessLikeNote(_ artist: String?, removed: Int) -> String {
        let more = max(0, removed - 1)
        return "no more \(artist ?? "of that") in this mix" + (more > 0 ? " (\(more) more taken out)" : "")
    }

    /// What saving a room's queue said (the web's `roomSaveNote`).
    public static func saveNote(_ result: RoomSaveResult?, fallbackName: String) -> String {
        let saved = result?.saved ?? 0
        let left = result?.skipped ?? 0
        let dup = result?.duplicates ?? 0
        let name = result?.name.flatMap { $0.isEmpty ? nil : $0 } ?? fallbackName
        let out = "Saved as \"\(name)\" (\(saved) song\(saved == 1 ? "" : "s"))"
        var why: [String] = []
        if left > 0 { why.append("\(left) left out: not songs from the library") }
        if dup > 0 { why.append("\(dup) repeat\(dup == 1 ? "" : "s") saved once") }
        return why.isEmpty ? "\(out)." : "\(out); \(why.joined(separator: ", "))."
    }

    public static func playingNow(_ title: String, room: String) -> String { "playing \"\(title)\" in \(room)" }
}

// MARK: - starting a room's Up next: "play in <room>"

/// The list a song was picked in, as an Up next start names it (§3.5f
/// `context`).
public struct UpNextContext: Equatable, Sendable {
    public let kind: String
    public let ref: String?
    public let name: String?
    public let trackIDs: [Int64]?

    public init(kind: String, ref: String? = nil, name: String? = nil, trackIDs: [Int64]? = nil) {
        self.kind = kind
        self.ref = ref
        self.name = name
        self.trackIDs = trackIDs
    }

    /// A genre's songs; nil for the "no genre" row, which has no key a mix
    /// can name.
    public static func genre(key: String, name: String) -> UpNextContext? {
        key.trimmingCharacters(in: .whitespaces).isEmpty ? nil : UpNextContext(kind: "genre", ref: key, name: name)
    }

    var json: [String: Any] {
        var out: [String: Any] = ["kind": kind]
        if let ref { out["ref"] = ref }
        if let name { out["name"] = name }
        if let trackIDs { out["track_ids"] = trackIDs }
        return out
    }
}

/// Whose mix a room's session follows, as this phone says it.
///
/// Android's `listenerFields` (`RoomUpNext.kt:254`): a shared screen names
/// nobody; a picked person is `listener_person_id`; nothing picked leaves it
/// to the server (whose the phone is). **No `as_household` here**: that is
/// Android's "household, on purpose" pick (`ListenerPicks.HOUSEHOLD`), and
/// this app's "listening as" (``PlayerListenerStore``) has no such row —
/// nil is "me (this device)", which is the server's default already.
public struct RoomListener: Equatable, Sendable {
    /// The stored "listening as" person, or nil.
    public let personID: String?
    /// This install is a shared screen (the kitchen tablet).
    public let shared: Bool

    public init(personID: String? = nil, shared: Bool = false) {
        self.personID = personID
        self.shared = shared
    }

    public static let unnamed = RoomListener()

    /// The fields this listener adds to a body. A person id that is not a
    /// number is left out — the server's `ge=1` int would 422 on it.
    var fields: [String: Any] {
        guard !shared, let personID, let id = Int64(personID.trimmingCharacters(in: .whitespaces)), id >= 1 else {
            return [:]
        }
        return ["listener_person_id": id]
    }
}

public enum RoomPlayBodies {

    /// `POST /api/music/play-track`'s body for "play in <room>": the song
    /// starts the room's Up next. With `context` the session is a `list` one,
    /// else `library`. Autoplay off is said; on is the server's default and
    /// left out (the web's `roomStartBody`).
    public static func playTrack(
        room: String,
        trackID: Int64,
        deviceID: String,
        context: UpNextContext?,
        listener: RoomListener,
        autoplay: Bool = true
    ) -> Data {
        var out: [String: Any] = ["room_id": room, "track_id": trackID, "device_id": deviceID]
        if let context { out["context"] = context.json }
        out.merge(listener.fields) { _, new in new }
        if !autoplay { out["autoplay"] = false }
        return encode(out)
    }

    /// `POST /api/music/play-playlist`'s body: the room loads the whole
    /// playlist as its queue and arms a `continue` Up next with it as the
    /// source. Autoplay is left at the server's default (on).
    public static func playPlaylist(
        room: String,
        playlistID: Int64,
        shuffle: Bool,
        deviceID: String,
        listener: RoomListener
    ) -> Data {
        var out: [String: Any] = [
            "room_id": room, "playlist_id": playlistID, "shuffle": shuffle, "device_id": deviceID,
        ]
        out.merge(listener.fields) { _, new in new }
        return encode(out)
    }

    static func encode(_ object: [String: Any]) -> Data {
        (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
    }
}

// MARK: - a playlist in a room

/// What a playlist played in a room answers (2026-11-12): `total` songs,
/// `queued` in the queue at the answer, `skipped` so far, `loading` while a
/// long playlist is still filling in, and the room's session. Every field
/// optional: an older server answers `{ok, room_id, queued}` and reads as
/// not loading.
public struct PlayPlaylistAnswer: Equatable, Sendable, Decodable {
    public let total: Int?
    public let queued: Int?
    public let skipped: Int?
    public let loading: Bool
    public let upNext: RoomUpNext?

    public init(total: Int? = nil, queued: Int? = nil, skipped: Int? = nil, loading: Bool = false, upNext: RoomUpNext? = nil) {
        self.total = total
        self.queued = queued
        self.skipped = skipped
        self.loading = loading
        self.upNext = upNext
    }

    private enum CodingKeys: String, CodingKey {
        case total, queued, skipped, loading
        case upNext = "up_next"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        total = try? values.decodeIfPresent(Int.self, forKey: .total)
        queued = try? values.decodeIfPresent(Int.self, forKey: .queued)
        skipped = try? values.decodeIfPresent(Int.self, forKey: .skipped)
        loading = (try? values.decodeIfPresent(Bool.self, forKey: .loading)) ?? false
        upNext = try? values.decodeIfPresent(RoomUpNext.self, forKey: .upNext)
    }

    /// The answer, read leniently: nil for anything that is not the object.
    public static func read(_ data: Data) -> PlayPlaylistAnswer? {
        guard let object = try? JSONSerialization.jsonObject(with: data), object is [String: Any] else { return nil }
        return try? JSONDecoder().decode(PlayPlaylistAnswer.self, from: data)
    }
}

/// Asks an open room queue view to read its room again: the rest of a long
/// playlist arrives after the play's answer (`RoomQueueNudges`).
@MainActor
public final class RoomQueueNudges {

    public static let shared = RoomQueueNudges()

    private var listeners: [Int: (String, String) -> Void] = [:]
    private var next = 0

    public init() {}

    public func nudge(server: String, room: String) {
        for one in listeners.values { one(server, room) }
    }

    @discardableResult
    public func listen(_ onNudge: @escaping (String, String) -> Void) -> Int {
        next += 1
        listeners[next] = onNudge
        return next
    }

    public func remove(_ id: Int) { listeners.removeValue(forKey: id) }

    public var count: Int { listeners.count }

    /// When an open room queue reads again after a playlist answered
    /// `loading`: a few times, then never.
    public static let refetchMs: [UInt64] = [2_000, 6_000, 15_000]

    /// After a playlist answered `loading: true` for `room` on `server`: the
    /// open room queue view reads again at each of ``refetchMs`` — a fixed
    /// few, never a loop — and not at all once the app talks to another
    /// server. Returns how many reads it asked for.
    public static func follow(
        _ answer: PlayPlaylistAnswer?,
        server: String,
        room: String,
        serverNow: @MainActor () -> String,
        sleep: (UInt64) async -> Void = { try? await Task.sleep(nanoseconds: $0 * 1_000_000) },
        nudge: @MainActor (String, String) -> Void = { RoomQueueNudges.shared.nudge(server: $0, room: $1) }
    ) async -> Int {
        guard answer?.loading == true else { return 0 }
        var asked = 0
        var waited: UInt64 = 0
        for at in refetchMs {
            await sleep(at - waited)
            waited = at
            if serverNow() != server { break }
            nudge(server, room)
            asked += 1
        }
        return asked
    }
}

/// Play a playlist in a room, the one place both callers go through
/// (`playPlaylistInRoom`, `RoomUpNext.kt:377`).
///
/// Sent to the server current at the tap, with that tap's listener; the
/// answer is read leniently, and while the room is still filling in a long
/// playlist an open room queue view of it reads again a few times
/// (``RoomQueueNudges/follow(_:server:room:serverNow:sleep:nudge:)``). Nothing
/// more is said than the web says: the toast is the caller's.
@MainActor
public enum RoomPlaylistPlay {
    public static func send(
        client: DomovoiClient,
        container: AppContainer,
        room: String,
        playlistID: Int64,
        shuffle: Bool,
        deviceID: String,
        listener: RoomListener
    ) async throws(DomovoiError) -> PlayPlaylistAnswer? {
        let server = container.store.serverURL
        let body = RoomPlayBodies.playPlaylist(
            room: room, playlistID: playlistID, shuffle: shuffle, deviceID: deviceID, listener: listener
        )
        let data = try await client.raw(method: "POST", path: "/api/music/play-playlist", body: body)
        let answer = PlayPlaylistAnswer.read(data)
        if answer?.loading == true {
            Task { @MainActor in
                _ = await RoomQueueNudges.follow(answer, server: server, room: room, serverNow: { container.store.serverURL })
            }
        }
        return answer
    }
}
