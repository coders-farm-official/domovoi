import Foundation

/// Up next on the phone itself (P9; Android P8, `player/UpNext.kt`, 4fb53334):
/// the server keeps a session for this device that hands out songs like the
/// ones playing; the phone adds them to its own queue as `auto` items, after
/// every song somebody asked for. **The phone never picks a song.**
///
/// Contract: `docs/API_REFERENCE.md` §3.5f; behaviour and words:
/// `web/static/player.jsx` (playFromList, playList, the refill effect,
/// PlayerUpNext) and `design-notes/MOBILE-HANDOFF-UPNEXT.md`.
///
/// This file: the wire shapes, the pure queue rules and the words. The
/// session itself is ``PhoneUpNext``.

// MARK: - the wire (domovoi/up_next/service.py session_json / pick_json)

/// Why a song is in the phone's queue (§3.5e / §3.5f `origin`;
/// `PlayItem.kt` `QueueOrigin`). The raw value is the wire word.
public enum PlayerQueueOrigin: String, Equatable, Sendable, CaseIterable {
    /// Somebody asked for it: a tap, play next, add to queue.
    case explicit
    /// Part of a whole list played (or shuffled) on purpose.
    case wholeList = "list"
    /// One of Up next's picks.
    case auto
}

/// A `Session`'s `target`: this device, or a room (after a cast moved it).
public struct UpNextTarget: Equatable, Sendable, Decodable {
    public let kind: String
    public let deviceID: String?
    public let roomID: String?

    public init(kind: String = "device", deviceID: String? = nil, roomID: String? = nil) {
        self.kind = kind
        self.deviceID = deviceID
        self.roomID = roomID
    }

    private enum CodingKeys: String, CodingKey {
        case kind
        case deviceID = "device_id"
        case roomID = "room_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        kind = (try? values.decodeIfPresent(String.self, forKey: .kind)) ?? "device"
        deviceID = try? values.decodeIfPresent(String.self, forKey: .deviceID)
        roomID = try? values.decodeIfPresent(String.self, forKey: .roomID)
    }
}

/// Whose taste the mix follows (nil: the household).
public struct UpNextPerson: Equatable, Sendable, Decodable {
    public let id: Int64
    public let name: String?

    public init(id: Int64, name: String? = nil) {
        self.id = id
        self.name = name
    }

    private enum CodingKeys: String, CodingKey { case id, name }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? values.decodeIfPresent(Int64.self, forKey: .id)) ?? 0
        name = try? values.decodeIfPresent(String.self, forKey: .name)
    }
}

/// A `Pick` (`service.py` pick_json).
public struct UpNextPick: Equatable, Sendable, Decodable {
    public let seq: Int
    public let trackID: Int64
    public let title: String?
    public let artist: String?
    public let album: String?
    public let durationSec: Double?
    public let reasonCode: String?
    public let reasonLabel: String?
    public let status: String?

    public init(
        seq: Int,
        trackID: Int64,
        title: String? = nil,
        artist: String? = nil,
        album: String? = nil,
        durationSec: Double? = nil,
        reasonCode: String? = nil,
        reasonLabel: String? = nil,
        status: String? = nil
    ) {
        self.seq = seq
        self.trackID = trackID
        self.title = title
        self.artist = artist
        self.album = album
        self.durationSec = durationSec
        self.reasonCode = reasonCode
        self.reasonLabel = reasonLabel
        self.status = status
    }

    private enum CodingKeys: String, CodingKey {
        case seq, title, artist, album, status
        case trackID = "track_id"
        case durationSec = "duration_sec"
        case reasonCode = "reason_code"
        case reasonLabel = "reason_label"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        seq = try values.decode(Int.self, forKey: .seq)
        trackID = try values.decode(Int64.self, forKey: .trackID)
        title = try? values.decodeIfPresent(String.self, forKey: .title)
        artist = try? values.decodeIfPresent(String.self, forKey: .artist)
        album = try? values.decodeIfPresent(String.self, forKey: .album)
        durationSec = try? values.decodeIfPresent(Double.self, forKey: .durationSec)
        reasonCode = try? values.decodeIfPresent(String.self, forKey: .reasonCode)
        reasonLabel = try? values.decodeIfPresent(String.self, forKey: .reasonLabel)
        status = try? values.decodeIfPresent(String.self, forKey: .status)
    }
}

/// A `Session` (`service.py` session_json).
public struct UpNextSession: Equatable, Sendable, Decodable {
    public let id: String
    public let target: UpNextTarget?
    /// `list`, `library` or `continue`.
    public let mode: String?
    public let autoplay: Bool
    public let person: UpNextPerson?
    public let seed: RoomUpNextSeed?
    public let picks: [UpNextPick]
    public let batchNo: Int
    public let haveThroughSeq: Int
    public let ended: Bool

    public init(
        id: String,
        target: UpNextTarget? = nil,
        mode: String? = nil,
        autoplay: Bool = true,
        person: UpNextPerson? = nil,
        seed: RoomUpNextSeed? = nil,
        picks: [UpNextPick] = [],
        batchNo: Int = 0,
        haveThroughSeq: Int = 0,
        ended: Bool = false
    ) {
        self.id = id
        self.target = target
        self.mode = mode
        self.autoplay = autoplay
        self.person = person
        self.seed = seed
        self.picks = picks
        self.batchNo = batchNo
        self.haveThroughSeq = haveThroughSeq
        self.ended = ended
    }

    private enum CodingKeys: String, CodingKey {
        case id, target, mode, autoplay, person, seed, picks, ended
        case batchNo = "batch_no"
        case haveThroughSeq = "have_through_seq"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        target = try? values.decodeIfPresent(UpNextTarget.self, forKey: .target)
        mode = try? values.decodeIfPresent(String.self, forKey: .mode)
        autoplay = (try? values.decodeIfPresent(Bool.self, forKey: .autoplay)) ?? true
        person = try? values.decodeIfPresent(UpNextPerson.self, forKey: .person)
        seed = try? values.decodeIfPresent(RoomUpNextSeed.self, forKey: .seed)
        picks = (try? values.decodeIfPresent([UpNextPick].self, forKey: .picks)) ?? []
        batchNo = (try? values.decodeIfPresent(Int.self, forKey: .batchNo)) ?? 0
        haveThroughSeq = (try? values.decodeIfPresent(Int.self, forKey: .haveThroughSeq)) ?? 0
        ended = (try? values.decodeIfPresent(Bool.self, forKey: .ended)) ?? false
    }
}

/// `POST …/next`'s answer (`web/backend/api/up_next.py`).
public struct UpNextBatch: Equatable, Sendable, Decodable {
    public let batchNo: Int
    public let picks: [UpNextPick]
    public let ended: Bool
    public let removed: [Int]
    public let mode: String?

    public init(batchNo: Int = 0, picks: [UpNextPick] = [], ended: Bool = false, removed: [Int] = [], mode: String? = nil) {
        self.batchNo = batchNo
        self.picks = picks
        self.ended = ended
        self.removed = removed
        self.mode = mode
    }

    private enum CodingKeys: String, CodingKey {
        case picks, ended, removed, mode
        case batchNo = "batch_no"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        batchNo = (try? values.decodeIfPresent(Int.self, forKey: .batchNo)) ?? 0
        picks = (try? values.decodeIfPresent([UpNextPick].self, forKey: .picks)) ?? []
        ended = (try? values.decodeIfPresent(Bool.self, forKey: .ended)) ?? false
        removed = (try? values.decodeIfPresent([Int].self, forKey: .removed)) ?? []
        mode = try? values.decodeIfPresent(String.self, forKey: .mode)
    }
}

// MARK: - a start (§3.5f `start`)

/// What a start asks for: the song, the list it was picked in, and whether
/// the whole list plays. Kept on the state so a start that failed can be
/// tried again on the next song (Android keeps the `JsonObject`; this is the
/// same thing as a value).
public struct UpNextStart: Equatable, Sendable {
    public let trackID: Int64?
    public let context: UpNextContext?
    public let wholeList: Bool

    public init(trackID: Int64?, context: UpNextContext? = nil, wholeList: Bool = false) {
        self.trackID = trackID
        self.context = context
        self.wholeList = wholeList
    }

    /// The `start` object as sent.
    public var json: [String: Any] {
        var out: [String: Any] = [:]
        if let trackID { out["track_id"] = trackID }
        if let context { out["context"] = context.json }
        if wholeList { out["whole_list"] = true }
        return out
    }

    /// A song tapped inside a list (or on its own: no context, library
    /// mode). An `ids` list sends its first ``UpNextRules/idsCap`` songs
    /// (`PhoneUpNext.startFromListBody`).
    public static func fromList(trackID: Int64, context: UpNextContext?, listIDs: [Int64]) -> UpNextStart {
        guard let context else { return UpNextStart(trackID: trackID) }
        let ctx = context.kind == "ids"
            ? UpNextContext(kind: "ids", ref: context.ref, name: context.name, trackIDs: Array(listIDs.prefix(UpNextRules.idsCap)))
            : context
        return UpNextStart(trackID: trackID, context: ctx)
    }

    /// A whole list played on purpose (continue mode); `listIDs` in play
    /// order, never empty. An `ids` list sends its LAST ``UpNextRules/idsCap``
    /// songs (the web's playList), what a continue session seeds from
    /// (`PhoneUpNext.startWholeListBody`).
    public static func wholeList(context: UpNextContext?, listIDs: [Int64]) -> UpNextStart {
        let base = context ?? UpNextContext(kind: "ids", name: "this list")
        let ctx = base.kind == "ids"
            ? UpNextContext(kind: "ids", ref: base.ref, name: base.name, trackIDs: Array(listIDs.suffix(UpNextRules.idsCap)))
            : base
        return UpNextStart(trackID: listIDs.first, context: ctx, wholeList: true)
    }

    /// A queue of library songs added one by one (`PhoneUpNext.queueStartBody`).
    public static func queue(_ ids: [Int64]) -> UpNextStart {
        UpNextStart(
            trackID: ids.first,
            context: UpNextContext(kind: "ids", name: "this queue", trackIDs: Array(ids.suffix(UpNextRules.idsCap))),
            wholeList: true
        )
    }
}

/// Whose mix it is, as this phone says it: Android's `listenerFields`
/// (`RoomUpNext.kt:254`), from the RAW "listening as" pick for the server
/// (``PlayerListenerStore/pick(for:)``). A shared screen names nobody;
/// "household" picked on purpose (``ListenerPicks/household``) is
/// `as_household`; a picked person is `listener_person_id`; nothing picked
/// leaves it to the server (whose the phone is).
public enum UpNextListenerFields {
    public static func fields(pick: String?, shared: Bool) -> [String: Any] {
        if shared { return [:] }
        if pick == ListenerPicks.household { return ["as_household": true] }
        if let pick, let id = Int64(pick.trimmingCharacters(in: .whitespaces)) {
            return ["listener_person_id": id]
        }
        return [:]
    }
}

// MARK: - the session as the phone keeps it

/// The phone's session as the player keeps it; nil in ``PhoneUpNext/state``
/// means none. Every field an answer writes is keyed by ``server`` and
/// ``gen`` (the play it was for): an answer for another server or an older
/// play is dropped (`UpNext.kt` UpNextState).
public struct UpNextState: Equatable, Sendable {
    /// The server this session lives on (normalised: the address the start
    /// went to).
    public let server: String
    /// The play that started it (``PhoneUpNext/newPlay()``).
    public let gen: Int
    public var id: String?
    /// Where the session plays: this device, or (later: a shared queue,
    /// `design-notes/SHARED-QUEUES-PLAN.md`) a room. Read from the server.
    public var target: UpNextTarget?
    public var mode: String?
    public var autoplay: Bool
    public var person: UpNextPerson?
    public var seed: RoomUpNextSeed?
    /// The highest pick number the phone holds (`have_through_seq`).
    public var haveThrough: Int
    public var ended: Bool
    /// The start is on its way.
    public var loading: Bool
    /// The home server couldn't be reached (or failed): said, and tried
    /// again when the song changes.
    public var offline: Bool
    /// The server refused Up next for this play (a 4xx): nothing shown.
    public var refused: Bool
    /// The start, kept to try a failed one again.
    public var start: UpNextStart?
    /// The queue index the last try failed (or came back empty) on: not
    /// tried again until the song changes.
    public var failedAt: Int?

    public init(
        server: String,
        gen: Int,
        id: String? = nil,
        target: UpNextTarget? = nil,
        mode: String? = nil,
        autoplay: Bool = true,
        person: UpNextPerson? = nil,
        seed: RoomUpNextSeed? = nil,
        haveThrough: Int = 0,
        ended: Bool = false,
        loading: Bool = false,
        offline: Bool = false,
        refused: Bool = false,
        start: UpNextStart? = nil,
        failedAt: Int? = nil
    ) {
        self.server = server
        self.gen = gen
        self.id = id
        self.target = target
        self.mode = mode
        self.autoplay = autoplay
        self.person = person
        self.seed = seed
        self.haveThrough = haveThrough
        self.ended = ended
        self.loading = loading
        self.offline = offline
        self.refused = refused
        self.start = start
        self.failedAt = failedAt
    }

    public static func from(_ s: UpNextSession, server: String, gen: Int) -> UpNextState {
        UpNextState(
            server: server, gen: gen, id: s.id, target: s.target, mode: s.mode,
            autoplay: s.autoplay, person: s.person, seed: s.seed,
            haveThrough: s.haveThroughSeq, ended: s.ended
        )
    }
}

// MARK: - the queue rules (MOBILE-HANDOFF-UPNEXT.md "Queue rules"). Pure.

public enum UpNextRules {
    /// Ask for more when this few songs are left after the one playing.
    public static let refillAt = 5

    /// Most track ids an `ids` list sends.
    public static let idsCap = 500

    /// An Up next pick still to come: after the song playing.
    public static func waiting(_ origin: PlayerQueueOrigin, at i: Int, index: Int) -> Bool {
        origin == .auto && i > index
    }

    /// The queue's count (`queue · N`): what plays and what was asked for,
    /// not the picks still waiting.
    public static func count(_ origins: [PlayerQueueOrigin], index: Int) -> Int {
        explicitRows(origins, index: index).count
    }

    /// The rows the queue list draws (real indices): everything but the
    /// waiting picks, which have their own "up next" section.
    public static func explicitRows(_ origins: [PlayerQueueOrigin], index: Int) -> [Int] {
        origins.indices.filter { !waiting(origins[$0], at: $0, index: index) }
    }

    /// The waiting picks (real indices), in order.
    public static func pickRows(_ origins: [PlayerQueueOrigin], index: Int) -> [Int] {
        origins.indices.filter { waiting(origins[$0], at: $0, index: index) }
    }

    /// Where "add to queue" lands: after the songs somebody asked for,
    /// before the first pick still to come.
    public static func addAt(_ queue: [PlayerItem], index: Int) -> Int {
        var i = max(0, index + 1)
        while i < queue.count {
            if queue[i].queueOrigin == .auto { return i }
            i += 1
        }
        return queue.count
    }

    /// Whether the queue holds a library song (else no Up next at all).
    public static func hasLibrary(_ queue: [PlayerItem]) -> Bool {
        queue.contains { $0.isLibrarySong }
    }

    /// A pick as a queue item.
    public static func pickItem(_ p: UpNextPick, sessionID: String) -> PlayerItem {
        var item = PlayerItem.fromTrack(
            id: p.trackID,
            title: nonBlank(p.title) ?? "track",
            artist: nonBlank(p.artist),
            album: nonBlank(p.album),
            durationSec: p.durationSec
        )
        item.queueOrigin = .auto
        item.upNextSeq = p.seq
        item.reasonLabel = nonBlank(p.reasonLabel)
        item.sessionID = sessionID
        return item
    }

    static func nonBlank(_ s: String?) -> String? {
        guard let s, !s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return s
    }
}

// MARK: - the words (the web's PlayerUpNextHere; `UpNext.kt:159-190`)

public enum PhoneUpNextStrings {
    public static let head = RoomUpNextStrings.head
    public static let autoplay = RoomUpNextStrings.autoplay
    public static let pick = RoomUpNextStrings.pick
    /// VoiceOver's name for the phone's autoplay switch.
    public static let autoplayLabel = "autoplay: keep playing similar songs when the queue ends"
    public static let offline = "up next needs the home server"
    public static let finding = "finding songs like this one…"
    public static let joinLater = "similar songs join here when the queue is nearly over"
    public static let autoplayOff = "autoplay is off — the queue stops at the end"

    public static func autoplayOffList(_ list: String?) -> String {
        "autoplay is off — stops after the last song of \(list ?? "this list")"
    }

    /// The "for" selector's word for nobody in particular.
    public static let household = "household"
    public static let forWord = "for"

    public static func forLabel(_ who: String) -> String { "whose taste this mix follows: \(who)" }

    public static func personFallback(_ id: Int64) -> String { "person \(id)" }

    public static func removeLabel(_ title: String) -> String { "remove \(title) from up next" }

    public static let playNow = "play now"

    public static func autoplayFailed(_ why: String) -> String { "Couldn't tell the home server: \(why)" }
    public static func listenerFailed(_ why: String) -> String { "Couldn't change whose mix this is: \(why)" }
    public static func lessLikeFailed(_ why: String) -> String { "Couldn't change the mix: \(why)" }
}

/// The one muted line under the phone's "up next" head; nil for none.
public func phoneUpNextLine(_ u: UpNextState?, autoplayOn: Bool, waiting: Int) -> String? {
    if u?.offline == true { return PhoneUpNextStrings.offline }
    if !autoplayOn {
        if u?.mode == "list", waiting > 0 {
            return PhoneUpNextStrings.autoplayOffList(UpNextRules.nonBlank(u?.seed?.list?.name))
        }
        return PhoneUpNextStrings.autoplayOff
    }
    if u?.loading == true { return PhoneUpNextStrings.finding }
    if waiting == 0 { return PhoneUpNextStrings.joinLater }
    return nil
}

/// Whether the phone's "up next" section shows at all: never for an unpaired
/// app, a queue with no library song (podcasts, books, radio, songs on the
/// phone), a play the server refused Up next for, or while casting (the
/// room's own Up next shows then).
public func phoneUpNextShown(paired: Bool, hasLibrary: Bool, state u: UpNextState?, casting: Bool) -> Bool {
    paired && !casting && hasLibrary && u?.refused != true
}

/// The "for" selector shows with two people or more, and a session.
public func phoneUpNextForShown(people: Int, state u: UpNextState?) -> Bool {
    people >= 2 && u?.id != nil
}

// MARK: - the Autoplay preference (one per device, default on)

/// Up next's Autoplay, this phone's (`Prefs.upNextAutoplay`; the web's
/// localStorage `domovoi-upnext-autoplay`): keep playing similar songs when
/// the queue ends. On until switched off. Sent with every start, every cast
/// and every room play (off only: on is the server's default there).
public final class UpNextAutoplayStore: @unchecked Sendable {

    public static let key = "domovoi.upnext.autoplay"

    /// The app's one store. A test makes its own over a memory place.
    ///
    /// **A seeded launch keeps nothing** (DEBUG only), the app's own rule for
    /// a stub-house run (`DomovoiApp.store(vault:)`): a UI case that switched
    /// Autoplay off must not hand that to the next case.
    public static let shared: UpNextAutoplayStore = {
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("-domovoi-server") {
            return UpNextAutoplayStore(place: SavedStateBox())
        }
        #endif
        return UpNextAutoplayStore()
    }()

    private let place: any SavedState
    private let lock = NSLock()
    private var value: Bool

    public init(place: (any SavedState)? = nil) {
        let place = place ?? UserDefaultsSavedState(key: UpNextAutoplayStore.key)
        self.place = place
        // Anything but a stored "0" is on: the default, and a blob nobody
        // can read.
        self.value = place.read().map { String(decoding: $0, as: UTF8.self) } != "0"
    }

    public var on: Bool {
        lock.lock()
        defer { lock.unlock() }
        return value
    }

    public func set(_ on: Bool) {
        lock.lock()
        let changed = value != on
        value = on
        lock.unlock()
        if changed { place.write(Data((on ? "1" : "0").utf8)) }
    }
}

/// `GET /api/people`'s rows, as the "for" selector reads them: a bare array
/// of `{id, name, …}`. Anything unreadable is nobody.
public enum UpNextPeople {
    public static func read(_ data: Data) -> [UpNextPerson] {
        guard let rows = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return [] }
        return rows.compactMap { row -> UpNextPerson? in
            guard let id = (row["id"] as? NSNumber)?.int64Value, id > 0 else { return nil }
            return UpNextPerson(id: id, name: row["name"] as? String)
        }
    }
}
