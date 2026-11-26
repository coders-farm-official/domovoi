import Foundation
import Observation

// ---------------------------------------------------------------------------
// Health incidents as admin on this phone (1.5.0; design §1's third area):
// see the details, acknowledge a failure, clear tampering after review. The
// port of Android's `ui/screens/containers/IncidentAdmin.kt`, rule for rule.
//
// `GET /api/containers/incidents` is a device read; with the admin Bearer it
// also carries `expected`, `found`, `acknowledged_by`, `cleared_by` and
// `clear_note`, and every row carries `can_acknowledge` and `can_clear`
// (docs/API_REFERENCE.md §3.9a). Acknowledge is admin (Bearer); clear is the
// security tier, with an optional note of at most 500 characters, and
// clearing the last open tamper incident lifts the guard's freeze.
//
// The household list (``HealthPanelModel``'s guard read) is what Health
// draws; this is the admin half laid over it by id.
// ---------------------------------------------------------------------------

/// One `expected` or `found` row: a key and what it held.
public struct HealthDetailRow: Equatable, Sendable {
    public let key: String
    public let value: String

    public init(_ key: String, _ value: String) {
        self.key = key
        self.value = value
    }
}

/// One incident's admin half, as the Bearer read gives it.
public struct HealthIncidentAdminFields: Equatable, Sendable {
    public let id: Int64
    public var canAcknowledge = false
    public var canClear = false
    public var expected: [HealthDetailRow] = []
    public var found: [HealthDetailRow] = []
    public var acknowledgedBy: String?
    public var clearedBy: String?
    public var clearNote: String?

    public init(id: Int64) { self.id = id }
}

/// Where Health is for admin: nothing (signed out, or a shared screen), kept
/// today (one tap shows the details), or a live session.
public enum HealthIncidentAdminMode: Equatable, Sendable {
    case none
    case kept
    case live
}

public func healthIncidentAdminMode(_ state: AdminSession.State, sharedScreen: Bool) -> HealthIncidentAdminMode {
    if sharedScreen { return .none }
    switch state {
    case .signedIn: return .live
    case .kept: return .kept
    case .signedOut: return .none
    }
}

/// An `expected` or `found` object as rows (the dashboard's `CtrDetails`):
/// empty values left out, a list joined with commas ("—" when empty), a
/// nested object as its JSON. Keys in order.
public func healthDetailRows(_ value: Any?) -> [HealthDetailRow] {
    guard let object = value as? [String: Any] else { return [] }
    return object.keys.sorted().compactMap { key in
        let shown: String?
        switch object[key] {
        case nil, is NSNull:
            shown = nil
        case let list as [Any]:
            let joined = list.map { healthScalar($0) }.joined(separator: ", ")
            shown = joined.isEmpty ? "—" : joined
        case let nested as [String: Any]:
            shown = (try? JSONSerialization.data(withJSONObject: nested, options: [.sortedKeys]))
                .map { String(decoding: $0, as: UTF8.self) }
        case let other?:
            let text = healthScalar(other)
            shown = text.isEmpty ? nil : text
        }
        return shown.map { HealthDetailRow(key, $0) }
    }
}

private func healthScalar(_ value: Any) -> String {
    if let number = value as? NSNumber {
        if CFGetTypeID(number) == CFBooleanGetTypeID() { return number.boolValue ? "true" : "false" }
        return number.stringValue
    }
    if let text = value as? String { return text }
    return "\(value)"
}

/// The admin halves of a Bearer read, by incident id. Rows without an id are
/// skipped; a body that isn't one is an empty map.
public func healthAdminIncidents(_ data: Data) -> [Int64: HealthIncidentAdminFields] {
    guard let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
          let rows = object["incidents"] as? [[String: Any]] else { return [:] }
    var out: [Int64: HealthIncidentAdminFields] = [:]
    for row in rows {
        let id: Int64
        if let number = row["id"] as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() {
            id = number.int64Value
        } else if let text = row["id"] as? String, let parsed = Int64(text) {
            id = parsed
        } else {
            continue
        }
        var f = HealthIncidentAdminFields(id: id)
        f.canAcknowledge = row["can_acknowledge"] as? Bool == true
        f.canClear = row["can_clear"] as? Bool == true
        f.expected = healthDetailRows(row["expected"])
        f.found = healthDetailRows(row["found"])
        f.acknowledgedBy = healthNonBlank(row["acknowledged_by"])
        f.clearedBy = healthNonBlank(row["cleared_by"])
        f.clearNote = healthNonBlank(row["clear_note"])
        out[id] = f
    }
    return out
}

private func healthNonBlank(_ value: Any?) -> String? {
    guard let text = value as? String, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
    return text
}

/// What clearing answered: whether the freeze is still on, and the music
/// player image it kept as the expected one (else nil).
public struct HealthClearResult: Equatable, Sendable {
    public let frozen: Bool
    public let keptImage: String?

    public init(frozen: Bool, keptImage: String?) {
        self.frozen = frozen
        self.keptImage = keptImage
    }

    public init(data: Data) {
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        frozen = object?["frozen"] as? Bool == true
        keptImage = healthNonBlank(object?["kept_image"])
    }
}

/// The clear body: the note trimmed and held to 500 characters, blank as
/// `null`.
public func healthClearBody(note: String) -> Data {
    let trimmed = note.trimmingCharacters(in: .whitespacesAndNewlines)
    let value: Any = trimmed.isEmpty ? NSNull() : String(trimmed.prefix(HealthIncidentAdminWords.noteMax))
    return (try? JSONSerialization.data(withJSONObject: ["note": value])) ?? Data("{}".utf8)
}

/// Whether `incident` is one the phone offers to clear (open tampering): the
/// server's `can_clear`, checked against the household row so a stale admin
/// read never offers it on a resolved one.
public func healthOffersClear(_ incident: HealthIncident, _ f: HealthIncidentAdminFields?) -> Bool {
    f?.canClear == true && incident.isOpen && incident.isTamper
}

public func healthOffersAcknowledge(_ incident: HealthIncident, _ f: HealthIncidentAdminFields?) -> Bool {
    f?.canAcknowledge == true && incident.isOpen && !incident.isTamper && !incident.acknowledged
}

/// The admin half's words: the Kotlin's (`IncidentAdminWords`,
/// `HealthPanelStringsTests` reads them), with Face ID where Android says
/// fingerprint or face.
public enum HealthIncidentAdminWords {
    /// The Containers part's line while signed out (not on a shared screen).
    public static let dashboardOrPhone =
        "an admin acknowledges or clears these on the dashboard's Settings → Health, or sign in as admin on this phone"

    /// The no-details card's line while signed out (not on a shared screen).
    public static let detailsOrPhone = "an admin sees the details on the dashboard, or sign in as admin on this phone"

    /// Kept only: one tap asks for Face ID, then shows them.
    public static let unlock = "show details with Face ID"

    /// Signed in: what the incidents offer here.
    public static let live = "as admin on this phone: acknowledge a failure, or clear tampering after review"

    public static let expected = "expected"
    public static let found = "found"
    public static let acknowledge = "acknowledge"
    public static let acknowledgeHint = "hides the banner until something new happens; it still resolves by itself"
    public static let clear = "clear after review"
    public static let clearHint = "risky changes stay paused until every tampering incident is cleared"
    public static let clearTitle = "Clear this tampering incident?"
    public static let clearBody =
        "Clear this only once you know why it happened: you swapped the image yourself, or you removed what "
        + "shouldn't be there. If it is still there it stays accepted; anything different opens a new incident. "
        + "Clearing the last one resumes the changes it paused."
    public static let noteLabel = "what you checked (optional)"
    public static let noteMax = 500
    public static let clearConfirm = "clear"
    public static let cancel = "cancel"
    public static let acknowledged = "acknowledged"

    /// "acknowledged by …", "cleared by … · the note", or nil.
    public static func who(_ f: HealthIncidentAdminFields) -> String? {
        var parts: [String] = []
        if let by = f.acknowledgedBy { parts.append("acknowledged by \(by)") }
        if let by = f.clearedBy { parts.append("cleared by \(by)" + (f.clearNote.map { " · \($0)" } ?? "")) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// What clearing did, from the server's answer.
    public static func cleared(_ r: HealthClearResult) -> String {
        let head = r.frozen ? "cleared · \(clearHint)" : "cleared · paused changes resume"
        return r.keptImage != nil ? "\(head) · the music player image it found is now the one expected" : head
    }

    /// A refused acknowledge or clear, in plain words.
    public static func refusal(status: Int, detail: String?) -> String {
        switch status {
        case 409: return detail ?? "this incident changed; Health was read again"
        case 404: return "that incident is gone"
        case 501: return "set up an admin on the dashboard first"
        default: return detail ?? "that didn't work (HTTP \(status))"
        }
    }

    /// `NoDetails`' second line: signed out on a household phone, the
    /// dashboard or this phone's own admin sign-in; otherwise as before.
    public static func noDetailsNote(_ state: HealthPanelState, mode: HealthIncidentAdminMode) -> String {
        !state.shared && mode == .none ? detailsOrPhone : state.noDetailsNote
    }
}

/// The admin routes Health uses, each through the phone's admin sign-in:
/// `headers` and `onlyTo` come from ``AdminSession/call(_:)`` or
/// ``AdminSession/act(_:)`` per call.
public struct HealthIncidentAdminApi: Sendable {

    let client: DomovoiClient

    public init(client: DomovoiClient) {
        self.client = client
    }

    public func read(headers: [String: String], onlyTo: String) async throws -> [Int64: HealthIncidentAdminFields] {
        let data = try await client.raw(method: "GET", path: HealthGuardApi.incidentsPath, headers: headers, onlyTo: onlyTo)
        return healthAdminIncidents(data)
    }

    public func acknowledge(_ id: Int64, headers: [String: String], onlyTo: String) async throws {
        _ = try await client.raw(
            method: "POST", path: "\(HealthGuardApi.incidentsPath)/\(id)/acknowledge",
            body: DomovoiClient.emptyObject, headers: headers, onlyTo: onlyTo
        )
    }

    public func clear(_ id: Int64, note: String, headers: [String: String], onlyTo: String) async throws -> HealthClearResult {
        let data = try await client.raw(
            method: "POST", path: "\(HealthGuardApi.incidentsPath)/\(id)/clear",
            body: healthClearBody(note: note), headers: headers, onlyTo: onlyTo
        )
        return HealthClearResult(data: data)
    }
}

/// The admin half's state for Settings → Health: the fields by id, one
/// change at a time, and why the last one didn't work.
@MainActor
@Observable
public final class HealthIncidentAdminModel {

    /// Nil until a read with a session has landed.
    public private(set) var fields: [Int64: HealthIncidentAdminFields]?
    public private(set) var busy = false
    public private(set) var problem: String?

    private let admin: AdminSession
    private let api: HealthIncidentAdminApi

    public init(admin: AdminSession) {
        self.admin = admin
        api = HealthIncidentAdminApi(client: admin.client)
    }

    /// Signed in, through ``AdminSession/call(_:)``; kept only, the tap,
    /// through ``AdminSession/act(_:)``.
    public func load(viaAct: Bool) async {
        let api = api
        let read: @Sendable ([String: String], String) async throws -> [Int64: HealthIncidentAdminFields] = { h, srv in
            try await api.read(headers: h, onlyTo: srv)
        }
        do {
            fields = viaAct ? try await admin.act(read) : try await admin.call(read)
            problem = nil
        } catch is AdminSession.NotSignedIn {
            fields = nil
        } catch {
            problem = Self.problem(error)
        }
    }

    /// Admin ended (or the screen became a shared one): nothing of it stays.
    public func forget() {
        fields = nil
        problem = nil
    }

    public func acknowledge(_ incident: HealthIncident) async -> String? {
        let id = incident.id
        return await change { api, h, srv in
            try await api.acknowledge(id, headers: h, onlyTo: srv)
            return HealthIncidentAdminWords.acknowledged
        }
    }

    public func clear(_ incident: HealthIncident, note: String) async -> String? {
        let id = incident.id
        return await change { api, h, srv in
            HealthIncidentAdminWords.cleared(try await api.clear(id, note: note, headers: h, onlyTo: srv))
        }
    }

    /// One change, through ``AdminSession/act(_:)`` (Face ID first when
    /// needed), then a re-read: the toast it ends in, or nil (the problem
    /// line says why).
    private func change(
        _ work: @escaping @Sendable (HealthIncidentAdminApi, [String: String], String) async throws -> String
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
            problem = Self.problem(error)
            return nil
        }
    }

    static func problem(_ error: any Error) -> String {
        if let why = (error as? AdminSession.AdminUnavailable)?.reason { return why }
        if error is AdminSession.NotSignedIn { return adminOnly }
        if let failure = error as? DomovoiError {
            if let f = failure.failure {
                return HealthIncidentAdminWords.refusal(status: f.status, detail: jsonDetail(of: f.body))
            }
            return failure.message
        }
        return "that didn't work"
    }
}
