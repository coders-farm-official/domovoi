import Foundation

/// The wire models for Home's seven reads — the port of `HomeModels.kt`'s
/// `@Serializable` half (`HomeModels.kt:48-211`, read at `df658c2b`).
///
/// **Every field is optional or has a default**, which is the Kotlin's own
/// rule: "the backend evolves, and `DomovoiJson` ignores unknown keys"
/// (`HomeModels.kt:25`). `JSONDecoder` does ignore unknown keys, but it does
/// **not** tolerate a missing one, so every model here writes its
/// `init(from:)` by hand with `decodeIfPresent`. A model that let
/// `JSONDecoder` synthesise its initialiser would fail the whole read when
/// one field was absent, and Home's reads come from a server that may be
/// older than any of these fields.
///
/// ``HomeConfig`` is not here: it was part 1's, and it lives beside the
/// header it feeds in `HomeHeader.swift`.
///
/// ### What is deliberately not modelled
///
/// The rules over these models that belong to sections this batch does not
/// build — the timer view and the fire ledger, room ordering and grouping,
/// the song line, `weakWifi`, today's grouping, the status line's counts —
/// are not written here. They are listed in `design-notes/ios/I1D2-REPORT.md`
/// as what is left, rather than described in a comment as somebody else's.
/// What *is* here is every field those rules read, so that adding them needs
/// no change to the wire layer, plus the one predicate the health checks need
/// (``kioskDead(_:)``).

// MARK: - a JSON value whose shape the server does not promise

/// Any JSON value, for the two plugin fields whose type the backend does not
/// fix: `web_load_error` and `page_errors` (`HomeModels.kt:171-172`, both
/// `JsonElement?`).
///
/// The health check reads them through two predicates and nothing else
/// (`HealthChecks.kt:50-56`), so those are the only two members. Modelling
/// them as `String?` and `[String]?` would have been shorter and would have
/// thrown the whole plugins read away the first time the server sent a
/// number, an object or `false` — which the Kotlin's `truthy()` branches show
/// it does.
public enum LooseJSON: Equatable, Sendable, Decodable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([LooseJSON])
    case object([String: LooseJSON])

    public init(from decoder: any Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() {
            self = .null
        } else if let bool = try? value.decode(Bool.self) {
            self = .bool(bool)
        } else if let number = try? value.decode(Double.self) {
            self = .number(number)
        } else if let string = try? value.decode(String.self) {
            self = .string(string)
        } else if let array = try? value.decode([LooseJSON].self) {
            self = .array(array)
        } else if let object = try? value.decode([String: LooseJSON].self) {
            self = .object(object)
        } else {
            self = .null
        }
    }

    /// `JsonElement?.truthy()` (`HealthChecks.kt:50`), branch for branch.
    ///
    /// Kotlin reads a primitive's `content` as text, so the rule is: a string
    /// is truthy when it is not empty; any other primitive is truthy unless
    /// its text is `"false"` or `"0"`; an object or an array is truthy
    /// whatever is in it; null and absent are not.
    ///
    /// The `"0"` case is the one to be careful with: Kotlin's
    /// `JsonPrimitive(0).content` is `"0"`, and a `Double` printed as `"0.0"`
    /// in Swift would not match it. So the number case tests the value, not
    /// its spelling.
    public var truthy: Bool {
        switch self {
        case .null: return false
        case .bool(let bool): return bool
        case .number(let number): return number != 0
        case .string(let string): return !string.isEmpty
        case .array, .object: return true
        }
    }

    /// `JsonElement?.nonEmptyList()` (`HealthChecks.kt:56`): an array with
    /// something in it, and nothing else — an object with keys is **not** one.
    public var nonEmptyList: Bool {
        if case .array(let items) = self { return !items.isEmpty }
        return false
    }
}

// MARK: - GET /api/health

/// `GET /api/health` (`HomeModels.kt:62`).
///
/// `domovoiReachable == false` is the shell's `coreDown`, and `dbReachable ==
/// false` its `dbDown`; both are **false only when the server said so**,
/// which is why they are `Bool?` and compared to `false` rather than read as
/// booleans. A server that did not answer leaves the whole model nil, and the
/// checks read that as `not checked yet` — which is trap 1's rule, one layer
/// down.
public struct HomeHealth: Equatable, Sendable, Decodable {

    public let status: String?
    public let dbReachable: Bool?
    public let domovoiReachable: Bool?

    /// `ok` / `fallback` / `unavailable` / `stub` / `not_loaded`; nil when the
    /// core did not answer with one, or the server predates the field.
    public let stt: String?

    public init(
        status: String? = nil,
        dbReachable: Bool? = nil,
        domovoiReachable: Bool? = nil,
        stt: String? = nil
    ) {
        self.status = status
        self.dbReachable = dbReachable
        self.domovoiReachable = domovoiReachable
        self.stt = stt
    }

    enum CodingKeys: String, CodingKey {
        case status
        case dbReachable = "db_reachable"
        case domovoiReachable = "domovoi_reachable"
        case stt
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = try c.decodeIfPresent(String.self, forKey: .status)
        dbReachable = try c.decodeIfPresent(Bool.self, forKey: .dbReachable)
        domovoiReachable = try c.decodeIfPresent(Bool.self, forKey: .domovoiReachable)
        stt = try c.decodeIfPresent(String.self, forKey: .stt)
    }
}

// MARK: - GET /api/satellites

/// `HomeSong` (`HomeModels.kt:124`).
public struct HomeSong: Equatable, Sendable, Decodable {

    public let title: String?
    public let artist: String?
    public let file: String?
    public let durationSec: Double?

    public init(title: String? = nil, artist: String? = nil, file: String? = nil, durationSec: Double? = nil) {
        self.title = title
        self.artist = artist
        self.file = file
        self.durationSec = durationSec
    }

    enum CodingKeys: String, CodingKey {
        case title, artist, file
        case durationSec = "duration_sec"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = try c.decodeIfPresent(String.self, forKey: .title)
        artist = try c.decodeIfPresent(String.self, forKey: .artist)
        file = try c.decodeIfPresent(String.self, forKey: .file)
        durationSec = try c.decodeIfPresent(Double.self, forKey: .durationSec)
    }
}

/// `HomeNowPlaying` (`HomeModels.kt:132`).
public struct HomeNowPlaying: Equatable, Sendable, Decodable {

    /// `play` / `pause` / `stop`. A **stopped** room's player keeps the last
    /// song loaded and this read still names it — the 2026-10-11 bug where an
    /// idle room looked like it was playing at 0:00 (map §10.6). The rule that
    /// acts on it (`roomSong`) belongs to the rooms section and is not in this
    /// batch; the field is here so it can be written without touching this
    /// file.
    public let state: String?
    public let song: HomeSong?
    public let elapsedSec: Double?

    public init(state: String? = nil, song: HomeSong? = nil, elapsedSec: Double? = nil) {
        self.state = state
        self.song = song
        self.elapsedSec = elapsedSec
    }

    enum CodingKeys: String, CodingKey {
        case state, song
        case elapsedSec = "elapsed_sec"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = try c.decodeIfPresent(String.self, forKey: .state)
        song = try c.decodeIfPresent(HomeSong.self, forKey: .song)
        elapsedSec = try c.decodeIfPresent(Double.self, forKey: .elapsedSec)
    }
}

/// `HomeWifi` (`HomeModels.kt:139`).
///
/// `signalDbm` and `mode` arrived 2026-10-12 and are nil from an older
/// satellite; `mode == "ap"` means the radio is hosting the setup hotspot.
public struct HomeWifi: Equatable, Sendable, Decodable {

    public let rxMbits: Double?
    public let txMbits: Double?
    public let signalDbm: Double?
    public let mode: String?

    public init(
        rxMbits: Double? = nil,
        txMbits: Double? = nil,
        signalDbm: Double? = nil,
        mode: String? = nil
    ) {
        self.rxMbits = rxMbits
        self.txMbits = txMbits
        self.signalDbm = signalDbm
        self.mode = mode
    }

    enum CodingKeys: String, CodingKey {
        case rxMbits = "rx_mbits"
        case txMbits = "tx_mbits"
        case signalDbm = "signal_dbm"
        case mode
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        rxMbits = try c.decodeIfPresent(Double.self, forKey: .rxMbits)
        txMbits = try c.decodeIfPresent(Double.self, forKey: .txMbits)
        signalDbm = try c.decodeIfPresent(Double.self, forKey: .signalDbm)
        mode = try c.decodeIfPresent(String.self, forKey: .mode)
    }
}

/// `HomeDisplay` (`HomeModels.kt:149`).
public struct HomeDisplay: Equatable, Sendable, Decodable {

    public let kioskAlive: Bool?

    public init(kioskAlive: Bool? = nil) {
        self.kioskAlive = kioskAlive
    }

    enum CodingKeys: String, CodingKey {
        case kioskAlive = "kiosk_alive"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kioskAlive = try c.decodeIfPresent(Bool.self, forKey: .kioskAlive)
    }
}

/// One room of `GET /api/satellites` — all rooms in one call
/// (`HomeModels.kt:154`).
public struct HomeRoom: Equatable, Sendable, Decodable {

    public let roomID: String

    /// `online` | `offline` | `waiting` (set up, never connected).
    public let status: String?
    public let lastConnectedAt: String?
    public let nowPlaying: HomeNowPlaying?
    public let wifi: HomeWifi?
    public let inCallWith: String?
    public let satType: String
    public let roomLabel: String?
    public let display: HomeDisplay?

    public var online: Bool { status == "online" }

    public init(
        roomID: String = "",
        status: String? = nil,
        lastConnectedAt: String? = nil,
        nowPlaying: HomeNowPlaying? = nil,
        wifi: HomeWifi? = nil,
        inCallWith: String? = nil,
        satType: String = "voice",
        roomLabel: String? = nil,
        display: HomeDisplay? = nil
    ) {
        self.roomID = roomID
        self.status = status
        self.lastConnectedAt = lastConnectedAt
        self.nowPlaying = nowPlaying
        self.wifi = wifi
        self.inCallWith = inCallWith
        self.satType = satType
        self.roomLabel = roomLabel
        self.display = display
    }

    enum CodingKeys: String, CodingKey {
        case roomID = "room_id"
        case status
        case lastConnectedAt = "last_connected_at"
        case nowPlaying = "now_playing"
        case wifi
        case inCallWith = "in_call_with"
        case satType = "sat_type"
        case roomLabel = "room_label"
        case display
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        roomID = try c.decodeIfPresent(String.self, forKey: .roomID) ?? ""
        status = try c.decodeIfPresent(String.self, forKey: .status)
        lastConnectedAt = try c.decodeIfPresent(String.self, forKey: .lastConnectedAt)
        nowPlaying = try c.decodeIfPresent(HomeNowPlaying.self, forKey: .nowPlaying)
        wifi = try c.decodeIfPresent(HomeWifi.self, forKey: .wifi)
        inCallWith = try c.decodeIfPresent(String.self, forKey: .inCallWith)
        satType = try c.decodeIfPresent(String.self, forKey: .satType) ?? "voice"
        roomLabel = try c.decodeIfPresent(String.self, forKey: .roomLabel)
        display = try c.decodeIfPresent(HomeDisplay.self, forKey: .display)
    }
}

/// `kioskDead` (`HomeModels.kt:504`): a video satellite that is online and
/// whose screen has stopped showing anything.
///
/// All three conditions, and the third is `== false` rather than `!= true`:
/// a room that reports no `kiosk_alive` at all is not a dead screen.
public func kioskDead(_ room: HomeRoom) -> Bool {
    room.online && room.satType == "video" && room.display?.kioskAlive == false
}

// MARK: - GET /api/timers

/// One row of `GET /api/timers`. A reminder is a timer with a message
/// (`HomeModels.kt:72`).
public struct HomeTimer: Equatable, Sendable, Decodable {

    public let id: Int64
    public let expiresAt: String?
    public let createdAt: String?
    public let label: String?
    public let message: String?
    public let roomID: String?
    public let isReminder: Bool

    public init(
        id: Int64 = 0,
        expiresAt: String? = nil,
        createdAt: String? = nil,
        label: String? = nil,
        message: String? = nil,
        roomID: String? = nil,
        isReminder: Bool = false
    ) {
        self.id = id
        self.expiresAt = expiresAt
        self.createdAt = createdAt
        self.label = label
        self.message = message
        self.roomID = roomID
        self.isReminder = isReminder
    }

    enum CodingKeys: String, CodingKey {
        case id
        case expiresAt = "expires_at"
        case createdAt = "created_at"
        case label, message
        case roomID = "room_id"
        case isReminder = "is_reminder"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        expiresAt = try c.decodeIfPresent(String.self, forKey: .expiresAt)
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt)
        label = try c.decodeIfPresent(String.self, forKey: .label)
        message = try c.decodeIfPresent(String.self, forKey: .message)
        roomID = try c.decodeIfPresent(String.self, forKey: .roomID)
        isReminder = try c.decodeIfPresent(Bool.self, forKey: .isReminder) ?? false
    }
}

/// Where a fire was announced and how it went (`HomeModels.kt:83`).
public struct HomeFireDelivery: Equatable, Sendable, Decodable {

    public let roomID: String?

    /// pending | sending | spoken | interrupted | failed | offline |
    /// busy_timeout | cancelled.
    public let outcome: String?

    public init(roomID: String? = nil, outcome: String? = nil) {
        self.roomID = roomID
        self.outcome = outcome
    }

    enum CodingKeys: String, CodingKey {
        case roomID = "room_id"
        case outcome
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        roomID = try c.decodeIfPresent(String.self, forKey: .roomID)
        outcome = try c.decodeIfPresent(String.self, forKey: .outcome)
    }
}

/// A timer or reminder that went off, from the server's fire ledger
/// (`HomeModels.kt:92`).
public struct HomeFire: Equatable, Sendable, Decodable {

    public let id: Int64
    public let timerID: Int64
    public let kind: String?
    public let isReminder: Bool
    public let label: String?
    public let message: String?
    public let masked: Bool
    public let roomID: String?
    public let createdAt: String?
    public let dueAt: String?
    public let firedAt: String?
    public let heardIn: [String]

    /// `heard in garage, kitchen · still announcing` — the server's words,
    /// never speech, which is why a shared screen may show it.
    public let summary: String?
    public let deliveries: [HomeFireDelivery]

    public init(
        id: Int64 = 0,
        timerID: Int64 = 0,
        kind: String? = nil,
        isReminder: Bool = false,
        label: String? = nil,
        message: String? = nil,
        masked: Bool = false,
        roomID: String? = nil,
        createdAt: String? = nil,
        dueAt: String? = nil,
        firedAt: String? = nil,
        heardIn: [String] = [],
        summary: String? = nil,
        deliveries: [HomeFireDelivery] = []
    ) {
        self.id = id
        self.timerID = timerID
        self.kind = kind
        self.isReminder = isReminder
        self.label = label
        self.message = message
        self.masked = masked
        self.roomID = roomID
        self.createdAt = createdAt
        self.dueAt = dueAt
        self.firedAt = firedAt
        self.heardIn = heardIn
        self.summary = summary
        self.deliveries = deliveries
    }

    enum CodingKeys: String, CodingKey {
        case id
        case timerID = "timer_id"
        case kind
        case isReminder = "is_reminder"
        case label, message, masked
        case roomID = "room_id"
        case createdAt = "created_at"
        case dueAt = "due_at"
        case firedAt = "fired_at"
        case heardIn = "heard_in"
        case summary, deliveries
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        timerID = try c.decodeIfPresent(Int64.self, forKey: .timerID) ?? 0
        kind = try c.decodeIfPresent(String.self, forKey: .kind)
        isReminder = try c.decodeIfPresent(Bool.self, forKey: .isReminder) ?? false
        label = try c.decodeIfPresent(String.self, forKey: .label)
        message = try c.decodeIfPresent(String.self, forKey: .message)
        masked = try c.decodeIfPresent(Bool.self, forKey: .masked) ?? false
        roomID = try c.decodeIfPresent(String.self, forKey: .roomID)
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt)
        dueAt = try c.decodeIfPresent(String.self, forKey: .dueAt)
        firedAt = try c.decodeIfPresent(String.self, forKey: .firedAt)
        heardIn = try c.decodeIfPresent([String].self, forKey: .heardIn) ?? []
        summary = try c.decodeIfPresent(String.self, forKey: .summary)
        deliveries = try c.decodeIfPresent([HomeFireDelivery].self, forKey: .deliveries) ?? []
    }
}

/// `GET /api/timers` (`HomeModels.kt:116`).
///
/// **`fires` being nil is a fact about the server**, not an empty list: nil
/// means this server keeps no fire ledger (older, or V018 not applied) and
/// Home falls back to guessing from rows that vanish; `[]` means nothing went
/// off lately. The distinction decides which of two different rules draws the
/// done lines, so the optionality is load-bearing and must not be flattened
/// to `[]` by a later convenience.
public struct HomeTimerList: Equatable, Sendable, Decodable {

    /// The database clock — the one timers fire against
    /// (``serverOffsetMs(serverNow:receivedAtMs:)``).
    public let serverNow: String?
    public let timers: [HomeTimer]
    public let fires: [HomeFire]?

    public init(serverNow: String? = nil, timers: [HomeTimer] = [], fires: [HomeFire]? = nil) {
        self.serverNow = serverNow
        self.timers = timers
        self.fires = fires
    }

    enum CodingKeys: String, CodingKey {
        case serverNow = "server_now"
        case timers, fires
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        serverNow = try c.decodeIfPresent(String.self, forKey: .serverNow)
        timers = try c.decodeIfPresent([HomeTimer].self, forKey: .timers) ?? []
        fires = try c.decodeIfPresent([HomeFire].self, forKey: .fires)
    }
}

// MARK: - GET /api/plugins

/// One plugin of `GET /api/plugins` (`HomeModels.kt:167`). Open: errors show
/// for everyone.
public struct HomePlugin: Equatable, Sendable, Decodable {

    public let slug: String
    public let name: String?
    public let enabled: Bool?
    public let status: String?
    public let webLoadError: LooseJSON?
    public let pageErrors: LooseJSON?

    public init(
        slug: String = "",
        name: String? = nil,
        enabled: Bool? = nil,
        status: String? = nil,
        webLoadError: LooseJSON? = nil,
        pageErrors: LooseJSON? = nil
    ) {
        self.slug = slug
        self.name = name
        self.enabled = enabled
        self.status = status
        self.webLoadError = webLoadError
        self.pageErrors = pageErrors
    }

    enum CodingKeys: String, CodingKey {
        case slug, name, enabled, status
        case webLoadError = "web_load_error"
        case pageErrors = "page_errors"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        slug = try c.decodeIfPresent(String.self, forKey: .slug) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name)
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled)
        status = try c.decodeIfPresent(String.self, forKey: .status)
        webLoadError = try c.decodeIfPresent(LooseJSON.self, forKey: .webLoadError)
        pageErrors = try c.decodeIfPresent(LooseJSON.self, forKey: .pageErrors)
    }
}

/// `HomePlugins` (`HomeModels.kt:177`).
public struct HomePlugins: Equatable, Sendable, Decodable {

    public let plugins: [HomePlugin]

    public init(plugins: [HomePlugin] = []) {
        self.plugins = plugins
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        plugins = try c.decodeIfPresent([HomePlugin].self, forKey: .plugins) ?? []
    }

    enum CodingKeys: String, CodingKey {
        case plugins
    }
}

// MARK: - GET /api/acquisitions

/// A media request — only its kind and status; **never its text**
/// (`HomeModels.kt:180`).
public struct HomeAcquisition: Equatable, Sendable, Decodable {

    public let id: Int64
    public let kind: String?
    public let status: String?

    public init(id: Int64 = 0, kind: String? = nil, status: String? = nil) {
        self.id = id
        self.kind = kind
        self.status = status
    }

    enum CodingKeys: String, CodingKey {
        case id, kind, status
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        kind = try c.decodeIfPresent(String.self, forKey: .kind)
        status = try c.decodeIfPresent(String.self, forKey: .status)
    }
}

/// `HomeAcquisitions` (`HomeModels.kt:187`).
///
/// `coreReachable` defaults to **false**, as the Kotlin's does: a body that
/// does not say the core is reachable is not evidence that it is.
public struct HomeAcquisitions: Equatable, Sendable, Decodable {

    public let acquisitions: [HomeAcquisition]
    public let canFulfillQuery: Bool?
    public let canFulfillURL: Bool?
    public let coreReachable: Bool

    public init(
        acquisitions: [HomeAcquisition] = [],
        canFulfillQuery: Bool? = nil,
        canFulfillURL: Bool? = nil,
        coreReachable: Bool = false
    ) {
        self.acquisitions = acquisitions
        self.canFulfillQuery = canFulfillQuery
        self.canFulfillURL = canFulfillURL
        self.coreReachable = coreReachable
    }

    enum CodingKeys: String, CodingKey {
        case acquisitions
        case canFulfillQuery = "can_fulfill_query"
        case canFulfillURL = "can_fulfill_url"
        case coreReachable = "core_reachable"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        acquisitions = try c.decodeIfPresent([HomeAcquisition].self, forKey: .acquisitions) ?? []
        canFulfillQuery = try c.decodeIfPresent(Bool.self, forKey: .canFulfillQuery)
        canFulfillURL = try c.decodeIfPresent(Bool.self, forKey: .canFulfillURL)
        coreReachable = try c.decodeIfPresent(Bool.self, forKey: .coreReachable) ?? false
    }
}

// MARK: - GET /api/calendar/events

/// A calendar event as Home shows it (`HomeModels.kt:196`).
///
/// **No description field, on purpose**: Home never shows one, so it never
/// even decodes one. Keep it that way — the model is the privacy boundary.
public struct HomeEvent: Equatable, Sendable, Decodable {

    public let id: Int64
    public let title: String
    public let startsAt: String
    public let endsAt: String?
    public let location: String?

    public init(
        id: Int64 = 0,
        title: String = "",
        startsAt: String = "",
        endsAt: String? = nil,
        location: String? = nil
    ) {
        self.id = id
        self.title = title
        self.startsAt = startsAt
        self.endsAt = endsAt
        self.location = location
    }

    enum CodingKeys: String, CodingKey {
        case id, title
        case startsAt = "starts_at"
        case endsAt = "ends_at"
        case location
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        startsAt = try c.decodeIfPresent(String.self, forKey: .startsAt) ?? ""
        endsAt = try c.decodeIfPresent(String.self, forKey: .endsAt)
        location = try c.decodeIfPresent(String.self, forKey: .location)
    }
}
