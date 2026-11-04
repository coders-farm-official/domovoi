import Foundation

// ---------------------------------------------------------------------------
// The alerts' wire models — the three things the phone is told about and then
// has to turn into a notification: a timer or reminder that went off, a set of
// help steps the house sent to a person, and a container incident.
//
// Ported from `android/.../alerts/FireModels.kt:21-90`,
// `HelpDeliveries.kt:47-76` and `ContainerIncidents.kt:50-215` (read at
// `898b3af2`), against the server's own shapes in `web/backend/schemas.py`
// (`TimerFire` `:735`, `TimerFireList` `:787`, `Timer` `:706`),
// `web/backend/api/help.py:311` and `domovoi/container_guard/views.py:84`.
//
// **Every field is optional with a default**, which is the Kotlin's own
// judgement and not a convenience: "the backend evolves, and an older server
// sends none of this at all" (`FireModels.kt:15-16`). Several of these fields
// are non-optional on the server today (`due_at`, `fired_at`, `summary`), and
// a decoder that required them would fail a whole batch of fires because one
// row came from a route that grew a field later.
//
// A fire itself is **not** re-declared here: ``HomeFire`` already decodes one
// field for field, `masked` and `heard_in` included (`HomeModels.swift:422`).
// Two parsers for one shape is how two screens come to disagree about what the
// server said.
// ---------------------------------------------------------------------------

// MARK: - lenient readers, for the payloads Android parses by hand

/// The three readers Android's incident parser uses
/// (`ContainerIncidents.kt:112-127`), which is hand-written over `JsonObject`
/// rather than a `@Serializable` class: a blank string is nothing, and a
/// boolean may arrive as `true`, as `"true"`, or as `1`.
///
/// They are `alerts`-prefixed because they hang off Foundation's own container
/// type, which every other file in this package also decodes with.
extension KeyedDecodingContainer {

    /// A string, or nil when the key is absent, null, not a string, or blank
    /// — `JsonObject.str` (`ContainerIncidents.kt:116`).
    /// `try?` flattens the optional a `decodeIfPresent` returns, so one
    /// binding is the whole test: an absent key, a null and a value of the
    /// wrong type all arrive here as nil.
    func alertsString(_ key: Key) -> String? {
        guard let raw = try? decodeIfPresent(String.self, forKey: key) else { return nil }
        return raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : raw
    }

    /// A boolean, however the server spelt it — `JsonObject.bool` (`:118`).
    func alertsBool(_ key: Key) -> Bool? {
        if let direct = try? decodeIfPresent(Bool.self, forKey: key) { return direct }
        if let text = try? decodeIfPresent(String.self, forKey: key) {
            switch text.lowercased() {
            case "true", "1": return true
            case "false", "0": return false
            default: return nil
            }
        }
        if let number = try? decodeIfPresent(Int.self, forKey: key) {
            return number != 0
        }
        return nil
    }

    /// An integer, or one written as digits in a string — `JsonObject.long`
    /// (`:126`).
    func alertsInt64(_ key: Key) -> Int64? {
        if let direct = try? decodeIfPresent(Int64.self, forKey: key) { return direct }
        if let text = try? decodeIfPresent(String.self, forKey: key) { return Int64(text) }
        return nil
    }
}

// MARK: - timers and reminders

/// `GET /api/timers/fires` (`satellites.py:1223`) and the fires on
/// `GET /api/timers` — `TimerFireList` (`FireModels.kt:64`).
///
/// ``windowSec`` is the load-bearing field and the reason this is not just
/// `[HomeFire]`: it says **how far back the answer reaches**. `null` is the
/// whole ledger, which only a household credential gets; `600` is the open
/// view's last ten minutes, every reminder masked (rule F1,
/// `satellites.py:1130-1140`). An empty answer therefore means two different
/// things, and ``alertsHistoryBehind(newest:seen:windowed:)`` is where the
/// difference is spent.
public struct AlertsFireList: Equatable, Sendable, Decodable {

    public let serverNow: String?
    public let fires: [HomeFire]
    public let windowSec: Int?

    public init(serverNow: String? = nil, fires: [HomeFire] = [], windowSec: Int? = nil) {
        self.serverNow = serverNow
        self.fires = fires
        self.windowSec = windowSec
    }

    enum CodingKeys: String, CodingKey {
        case serverNow = "server_now"
        case fires
        case windowSec = "window_sec"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        serverNow = try c.decodeIfPresent(String.self, forKey: .serverNow)
        fires = try c.decodeIfPresent([HomeFire].self, forKey: .fires) ?? []
        windowSec = try c.decodeIfPresent(Int.self, forKey: .windowSec)
    }
}

/// One running timer, as the alarm mirror needs it — `AlertTimer`
/// (`FireModels.kt:72`).
///
/// **Not ``HomeTimer``, for one field**: `masked` (`schemas.py:721`), which
/// says the server withheld this reminder's words from this caller. Home's row
/// has no use for it and does not decode it; a notification that is about to
/// write a reminder's words into a banner does.
public struct AlertsTimer: Equatable, Sendable, Decodable {

    public let id: Int64
    public let expiresAt: String?
    public let createdAt: String?
    public let label: String?
    public let message: String?
    public let roomID: String?
    public let isReminder: Bool
    public let masked: Bool

    public init(
        id: Int64 = 0,
        expiresAt: String? = nil,
        createdAt: String? = nil,
        label: String? = nil,
        message: String? = nil,
        roomID: String? = nil,
        isReminder: Bool = false,
        masked: Bool = false
    ) {
        self.id = id
        self.expiresAt = expiresAt
        self.createdAt = createdAt
        self.label = label
        self.message = message
        self.roomID = roomID
        self.isReminder = isReminder
        self.masked = masked
    }

    enum CodingKeys: String, CodingKey {
        case id
        case expiresAt = "expires_at"
        case createdAt = "created_at"
        case label, message, masked
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
        masked = try c.decodeIfPresent(Bool.self, forKey: .masked) ?? false
    }
}

/// `GET /api/timers` — `AlertTimerList` (`FireModels.kt:86`). `fires` being
/// nil is a fact about the server (no V018), exactly as on ``HomeTimerList``.
public struct AlertsTimerList: Equatable, Sendable, Decodable {

    public let serverNow: String?
    public let timers: [AlertsTimer]
    public let fires: [HomeFire]?

    public init(serverNow: String? = nil, timers: [AlertsTimer] = [], fires: [HomeFire]? = nil) {
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
        timers = try c.decodeIfPresent([AlertsTimer].self, forKey: .timers) ?? []
        fires = try c.decodeIfPresent([HomeFire].self, forKey: .fires)
    }
}

// MARK: - help steps

/// One set of help steps the house sent to a person: `HelpDelivery`
/// (`HelpDeliveries.kt:47`), the row of `GET /api/help/deliveries`
/// (`help.py:311`, `domovoi/help_desk/delivery.py:226`).
///
/// A row with no `id`, no `person_id` or no `thread_id` is **not** one of
/// these — `parseDelivery` returns null (`:58-62`) — and a payload that
/// carries one is read as a payload with one row fewer, never as a failure.
/// ``AlertsHelpDeliveryList`` is where that skipping happens.
public struct AlertsHelpDelivery: Equatable, Sendable {

    public let id: Int64
    public let personID: Int64
    public let threadID: Int64

    /// The thread's title, `""` when the row has none — which
    /// ``alertsHelpTitle(_:)`` turns into `Help from Domovoi`.
    public let title: String
    public let createdAt: String?

    public init(id: Int64, personID: Int64, threadID: Int64, title: String = "", createdAt: String? = nil) {
        self.id = id
        self.personID = personID
        self.threadID = threadID
        self.title = title
        self.createdAt = createdAt
    }
}

/// One row, or nothing — the shape that lets a list skip a row it cannot use
/// without failing the whole decode. Decoding `[AlertsHelpDelivery]` directly
/// could not do that: one bad row would throw away the batch, and Android
/// keeps the rest.
struct AlertsMaybeHelpDelivery: Decodable {

    let row: AlertsHelpDelivery?

    enum CodingKeys: String, CodingKey {
        case id
        case personID = "person_id"
        case threadID = "thread_id"
        case title
        case createdAt = "created_at"
    }

    init(from decoder: any Decoder) throws {
        guard let c = try? decoder.container(keyedBy: CodingKeys.self),
              let id = c.alertsInt64(.id),
              let person = c.alertsInt64(.personID),
              let thread = c.alertsInt64(.threadID)
        else {
            row = nil
            return
        }
        row = AlertsHelpDelivery(
            id: id,
            personID: person,
            threadID: thread,
            title: c.alertsString(.title) ?? "",
            createdAt: c.alertsString(.createdAt)
        )
    }
}

/// `{"deliveries": [...]}` — the **wrapped** read (`help.py:320`). A server
/// without V035 answers `{"deliveries": []}` rather than a 404, so an empty
/// list here is an ordinary answer.
public struct AlertsHelpDeliveryList: Equatable, Sendable, Decodable {

    public let deliveries: [AlertsHelpDelivery]

    public init(deliveries: [AlertsHelpDelivery] = []) {
        self.deliveries = deliveries
    }

    enum CodingKeys: String, CodingKey {
        case deliveries
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let rows = try c.decodeIfPresent([AlertsMaybeHelpDelivery].self, forKey: .deliveries) ?? []
        deliveries = rows.compactMap(\.row)
    }
}

/// The `help_deliveries.changed` push, which is a **bare list** and not the
/// read's wrapper (`realtime.py:984-1004`). Android takes either in one
/// function (`parseDeliveries`, `HelpDeliveries.kt:66`); here the two shapes
/// are two types, because a `Decodable` is told which one it is by the
/// decoder and cannot try both.
public struct AlertsHelpDeliveryPush: Equatable, Sendable, Decodable {

    public let deliveries: [AlertsHelpDelivery]

    public init(deliveries: [AlertsHelpDelivery] = []) {
        self.deliveries = deliveries
    }

    public init(from decoder: any Decoder) throws {
        // A member that is not a usable row — a number, an object with no
        // `id` — decodes to nothing rather than throwing
        // (``AlertsMaybeHelpDelivery``), which is `mapNotNull` over a
        // `JsonArray` of mixed members.
        let rows = try [AlertsMaybeHelpDelivery](from: decoder)
        deliveries = rows.compactMap(\.row)
    }
}

// MARK: - container incidents

/// `class` = tamper: a different image, an unknown container under Domovoi's
/// names, risky settings. Only an admin clearing it closes one
/// (`ContainerIncidents.kt:45`).
public let alertsClassTamper = "tamper"

/// `class` = failure: missing, stopped, restarting, unhealthy, or Docker
/// itself down. Resolves by itself (`:46`).
public let alertsClassFailure = "failure"

/// One incident as the household tier sees it — `ContainerIncident`
/// (`ContainerIncidents.kt:50`), `incident_view` (`views.py:84`).
///
/// This holds the ten or so fields the **notification** plans need. The health
/// item parses the same payload for the banner and the Containers screen
/// (`overnight/health/MAP.md:338`); DECISIONS §5 is the record that the two
/// should become one parser after tonight's trains, and why they are not
/// tonight.
public struct AlertsIncident: Equatable, Sendable, Decodable {

    public let id: Int64

    /// `"failure"` or `"tamper"`; **anything else reads as a failure**, which
    /// is the Kotlin's own narrowing (`:134`).
    public let incidentClass: String

    /// missing, stopped, restarting, unhealthy, oneshot_failed, daemon, image,
    /// unknown, config, outdated.
    public let kind: String?

    /// `"error"` (red) or `"warn"` (amber).
    public let severity: String?
    public let label: String?
    public let summary: String?
    public let openedAt: String?
    public let resolvedAt: String?
    public let acknowledged: Bool
    public let open: Bool

    public init(
        id: Int64,
        incidentClass: String = alertsClassFailure,
        kind: String? = nil,
        severity: String? = nil,
        label: String? = nil,
        summary: String? = nil,
        openedAt: String? = nil,
        resolvedAt: String? = nil,
        acknowledged: Bool = false,
        open: Bool = true
    ) {
        self.id = id
        self.incidentClass = incidentClass
        self.kind = kind
        self.severity = severity
        self.label = label
        self.summary = summary
        self.openedAt = openedAt
        self.resolvedAt = resolvedAt
        self.acknowledged = acknowledged
        self.open = open
    }

    public var tamper: Bool { incidentClass == alertsClassTamper }

    /// What the banner shows, and therefore the only thing that is ever
    /// notified: an open tamper incident (only clearing hides it), or an open
    /// failure nobody has acknowledged (`:78`).
    public var bannerWorthy: Bool { open && (tamper || !acknowledged) }

    enum CodingKeys: String, CodingKey {
        case id, kind, severity, label, summary
        case incidentClass = "class"
        case openedAt = "opened_at"
        case resolvedAt = "resolved_at"
        case acknowledged
        case acknowledgedAt = "acknowledged_at"
        case open
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let id = c.alertsInt64(.id) else {
            throw DecodingError.dataCorruptedError(
                forKey: .id, in: c, debugDescription: "an incident with no usable id"
            )
        }
        self.id = id
        incidentClass = c.alertsString(.incidentClass) == alertsClassTamper ? alertsClassTamper : alertsClassFailure
        kind = c.alertsString(.kind)
        severity = c.alertsString(.severity)
        label = c.alertsString(.label)
        summary = c.alertsString(.summary)
        openedAt = c.alertsString(.openedAt)
        let resolved = c.alertsString(.resolvedAt)
        resolvedAt = resolved
        // Both derivations are the Kotlin's (`:132`, `:148`): a row from a
        // server that does not send `open` is open while it has not resolved,
        // and one that does not send `acknowledged` is acknowledged if it
        // carries the time it was.
        let acknowledgedAt = c.alertsString(.acknowledgedAt)
        acknowledged = c.alertsBool(.acknowledged) ?? (acknowledgedAt != nil)
        open = c.alertsBool(.open) ?? (resolved == nil)
    }
}

/// One incident that may be unusable, so a payload can skip it — the shape
/// ``AlertsMaybeHelpDelivery`` has, for the same reason (`:129`).
struct AlertsMaybeIncident: Decodable {

    let row: AlertsIncident?

    init(from decoder: any Decoder) throws {
        row = try? AlertsIncident(from: decoder)
    }
}

/// `GET /api/containers/incidents` and the `containers.changed` push
/// (`containers.py:88`, `realtime.py:929`), as this item reads it: the
/// incidents, and nothing else.
///
/// **The banner and the freeze are deliberately not here.** They are the
/// health item's (`overnight/health/MAP.md:338`), the notification plans do
/// not consult them, and a second copy of a banner rule is a second place for
/// it to be wrong. A payload with no `incidents` array is not one of these —
/// the Kotlin's test, and the web's (`:197`).
public struct AlertsIncidentsPayload: Equatable, Sendable, Decodable {

    public let incidents: [AlertsIncident]

    public init(incidents: [AlertsIncident] = []) {
        self.incidents = incidents
    }

    enum CodingKeys: String, CodingKey {
        case incidents
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let rows = try c.decode([AlertsMaybeIncident].self, forKey: .incidents)
        incidents = rows.compactMap(\.row)
    }
}
