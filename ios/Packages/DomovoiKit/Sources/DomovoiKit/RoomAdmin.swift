import Foundation
import Observation

// ---------------------------------------------------------------------------
// A room as admin on this phone (1.5.0; design §1's fourth area): restart it,
// upgrade its software, change its settings. The port of Android's
// `ui/screens/satellites/SatAdmin.kt`, rule for rule and word for word.
//
// All three are admin on the server (docs/API_REFERENCE.md §3.7: restart
// `require_admin_mutation`, upgrade and the settings save
// `require_admin_security`, Bearer-only), so they are offered only while this
// phone holds admin (``AdminSession``: signed in, or kept today) and never on
// a shared screen, and each goes through ``AdminSession/act(_:)`` (Face ID
// first when needed). Signed out, the phone shows what it always did, and the
// "on the dashboard" lines add "or sign in as admin on this phone".
//
// Left for the dashboard: the Advanced settings (a wrong audio device, LED or
// mic-gain value can leave the room without a mic or sound until someone
// fixes config.toml on the device), reset pairing, removing a room, the mic
// DSP presets, the room's voice, a video room's display, moving or renaming a
// device, diagnostics and logs, and support sessions.
// ---------------------------------------------------------------------------

/// Whether a room's admin controls are offered: admin held here (signed in
/// or kept today), and not a shared screen (`roomAdminHeld`).
public func roomAdminHeld(_ state: AdminSession.State, sharedScreen: Bool) -> Bool {
    if sharedScreen { return false }
    if case .signedOut = state { return false }
    return true
}

/// What the device declared (S0): a Pi sends no list, so everything
/// (`satCan`).
public func roomsCan(_ room: RoomsSatellite, _ ability: String) -> Bool {
    room.supports?.contains(ability) ?? true
}

/// The two maintenance buttons, as Android draws them (`MaintenanceSection`):
/// restart needs the room online and able to restart; upgrade also needs it
/// behind the server (or its version unknown) and is the filled button when
/// it is known to be behind. Neither while an upgrade is running.
public struct RoomMaintenanceButtons: Equatable, Sendable {
    public let restartEnabled: Bool
    public let upgradeEnabled: Bool
    public let upgradePrimary: Bool
    public let upgradeLabel: String
    public let notOffered: String?

    public init(room: RoomsSatellite, coreSha: String?, restarting: Bool, asking: Bool) {
        let canRestart = roomsCan(room, "restart")
        let canUpgrade = roomsCan(room, "code_sync")
        let upgrading = room.upgrade?.running == true
        let upToDate = coreSha != nil && room.version != nil && room.version == coreSha
        restartEnabled = !upgrading && !restarting && room.online && canRestart
        upgradeEnabled = !upgrading && !asking && room.online && !upToDate && canUpgrade
        upgradePrimary = roomsNeedsUpgrade(coreSha: coreSha, roomVersion: room.version) && canUpgrade
        upgradeLabel = upgrading || asking ? RoomAdminWords.upgrading : RoomAdminWords.upgrade
        notOffered = RoomAdminWords.notOffered(canRestart: canRestart, canUpgrade: canUpgrade)
    }
}

/// `RoomAdminWords`, word for word.
public enum RoomAdminWords {
    /// The maintenance line, signed out (not on a shared screen).
    public static let maintenanceOrPhone =
        "restarting and upgrading a room are done from the dashboard on a computer, or sign in as admin on this phone"

    /// The settings line, signed out (not on a shared screen).
    public static let configOrPhone =
        "what this room is set to. Changing any of it is done from the dashboard on a computer, "
        + "or sign in as admin on this phone."

    /// Above the editor, while admin is held.
    public static let configEdit =
        "Saving sends the changes to the room, which restarts to apply them: it drops offline for a few seconds."

    /// The Advanced settings stay read-only on the phone.
    public static let advancedOnDashboard =
        "These can take the room offline (a wrong audio device, LED or mic-gain value leaves it without a mic or "
        + "sound until its config is fixed on the device), so they are changed from the dashboard on a computer."

    public static let restart = "Restart room"
    public static let upgrade = "Upgrade room"
    public static let upgrading = "Upgrading…"
    public static let save = "Save and restart"
    public static let saving = "Saving…"
    public static let cancel = "cancel"
    public static let undo = "undo changes"
    public static let notSet = "(not set)"

    /// The confirm buttons (Android's `confirmLabel`s).
    public static let restartConfirmButton = "restart"
    public static let upgradeConfirmButton = "upgrade"
    public static let saveConfirmButton = "save"

    /// The dashboard's confirm, word for word (satellites.jsx restartSat).
    public static func restartConfirm(_ room: String) -> String {
        "Restart the \(room) room? It'll drop offline for a few seconds while the service bounces."
    }

    /// The dashboard's confirm, word for word (satellites.jsx upgradeSat).
    public static func upgradeConfirm(_ room: String, coreSha: String?) -> String {
        "Upgrade the \(room) room to \(coreSha ?? "the latest domovoi code")?\n\n"
            + "It pulls the new room software from the Domovoi server, backs up its current "
            + "tree, and restarts — dropping offline for a few seconds. If the new code won't "
            + "start, it rolls back automatically.\n\n"
            + "Some updates finish at the next boot: if the Health section of its Settings tab then says "
            + "\"reboot needed\", unplug it for a few seconds and plug it back in."
    }

    /// The dashboard's confirm, word for word (satellites.jsx RoomSettingsBody).
    public static func saveConfirm(_ room: String) -> String {
        "Save and restart the \(room) room? It'll drop offline for a few seconds while it applies the change."
    }

    public static func restarting(_ room: String) -> String { "restarting \(room)…" }
    public static func upgradingRoom(_ room: String) -> String { "upgrading \(room)…" }

    /// A device that restarts or updates itself (it didn't offer the ability).
    public static func notOffered(canRestart: Bool, canUpgrade: Bool) -> String? {
        switch (canRestart, canUpgrade) {
        case (true, true): return nil
        case (false, false): return "this device restarts and updates itself"
        case (false, true): return "this device restarts itself"
        case (true, false): return "this device updates itself"
        }
    }

    /// What a save did, from the server's answer.
    public static func saved(_ room: String, _ r: RoomsConfigSaveResult) -> String {
        if r.restarting && r.rejected.isEmpty { return "\(room) restarting to apply…" }
        if r.restarting { return "\(room) restarting to apply… (\(r.rejected.count) not saved)" }
        if let first = r.rejected.first { return "nothing saved: \(first.reason)" }
        return "nothing to save"
    }
}

// MARK: - the settings editor's rules (the dashboard's ConfigField)

/// One value in the editor, or one change sent: a switch's state, a typed
/// number, or text (a choice, a string field, or a number still being typed).
public enum RoomsConfigValue: Equatable, Sendable {
    case bool(Bool)
    case int(Int64)
    case double(Double)
    case text(String)

    var json: Any {
        switch self {
        case .bool(let b): return b
        case .int(let i): return i
        case .double(let d): return d
        case .text(let t): return t
        }
    }

    /// What a text field starts from.
    public var asText: String {
        switch self {
        case .bool(let b): return b ? "true" : "false"
        case .int(let i): return String(i)
        case .double(let d): return roomsNumberText(d)
        case .text(let t): return t
        }
    }
}

private let editableTypes: Set<String> = ["bool", "choice", "int", "float", "str"]

extension RoomsConfigField {
    /// A field the phone may change: not Advanced, a kind it can edit, and
    /// one the room's software has (`supported` false: upgrade it first).
    public var editableHere: Bool {
        section != "advanced" && supported != false && editableTypes.contains(type ?? "")
    }

    /// The reported value as the editor's starting text ("" for none).
    public var editText: String {
        switch value {
        case .none, .some(.null): return ""
        case .some(.bool(let b)): return b ? "true" : "false"
        case .some(.number(let d)): return roomsNumberText(d)
        case .some(.string(let s)): return s
        case .some(.array), .some(.object): return ""
        }
    }

    /// The reported value of a `bool` field.
    public var boolValue: Bool { value?.truthy ?? false }
}

/// A number as the dashboard prints it: a whole one without a decimal point.
func roomsNumberText(_ d: Double) -> String {
    if d.isFinite, d == d.rounded(), abs(d) < 1e15 { return String(Int64(d)) }
    return String(d)
}

/// Why an edited value can't be saved, or nil (`configEditProblem`). Only
/// numbers have rules; a number being typed is text until it is saved.
public func roomsConfigEditProblem(_ f: RoomsConfigField, _ value: RoomsConfigValue?) -> String? {
    guard f.type == "int" || f.type == "float", let value else { return nil }
    let wrong = f.type == "int" ? "expected a whole number" : "expected a number"
    guard let n = Double(value.asText.trimmingCharacters(in: .whitespacesAndNewlines)), n.isFinite else { return wrong }
    if f.type == "int" && n != n.rounded(.down) { return "expected a whole number" }
    if let min = f.min, n < min { return "must be at least \(roomsNumberText(min))" }
    if let max = f.max, n > max { return "must be at most \(roomsNumberText(max))" }
    return nil
}

/// A typed number as the value the dashboard would send: an int field's a
/// whole number, a float's a decimal; text that isn't one stays text (and
/// ``roomsConfigEditProblem(_:_:)`` says so). Blank is no value.
public func roomsTypedNumber(_ f: RoomsConfigField, _ text: String) -> RoomsConfigValue? {
    let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
    if t.isEmpty { return nil }
    guard let d = Double(t), d.isFinite else { return .text(t) }
    if f.type == "int", d == d.rounded(.down), abs(d) < 1e15 { return .int(Int64(d)) }
    return .double(d)
}

/// The edits that can't be saved yet, in the server's field order.
public func roomsConfigEditProblems(_ fields: [RoomsConfigField], _ edits: [String: RoomsConfigValue]) -> [String] {
    fields.compactMap { f in
        guard let v = edits[f.name] else { return nil }
        return roomsConfigEditProblem(f, v) == nil ? nil : f.name
    }
}

/// Only the changes, as the server takes them (a typed number as a number):
/// an edit equal to the reported value is no change. In the server's field
/// order.
public func roomsConfigChanges(
    _ fields: [RoomsConfigField], _ edits: [String: RoomsConfigValue]
) -> [(name: String, value: RoomsConfigValue)] {
    fields.compactMap { f in
        guard let typed = edits[f.name], f.editableHere else { return nil }
        let v: RoomsConfigValue
        if f.type == "int" || f.type == "float" {
            guard let n = roomsTypedNumber(f, typed.asText) else { return nil }
            v = n
        } else {
            v = typed
        }
        return roomsSameValue(f.value, v) ? nil : (f.name, v)
    }
}

private func roomsSameValue(_ reported: LooseJSON?, _ v: RoomsConfigValue) -> Bool {
    func number(_ text: String) -> Double? { Double(text.trimmingCharacters(in: .whitespacesAndNewlines)) }
    switch (reported, v) {
    case (.some(.bool(let a)), .bool(let b)): return a == b
    case (.some(.number(let a)), .int(let b)): return a == Double(b)
    case (.some(.number(let a)), .double(let b)): return a == b
    case (.some(.number(let a)), .text(let b)): return number(b) == a
    case (.some(.string(let a)), .text(let b)): return a == b || (number(a) != nil && number(a) == number(b))
    case (.some(.string(let a)), .int(let b)): return number(a) == Double(b)
    case (.some(.string(let a)), .double(let b)): return number(a) == b
    default: return false
    }
}

/// The PATCH body: `{"changes": {...}}`.
public func roomsConfigSaveBody(_ changes: [(name: String, value: RoomsConfigValue)]) -> Data {
    var map: [String: Any] = [:]
    for c in changes { map[c.name] = c.value.json }
    return (try? JSONSerialization.data(withJSONObject: ["changes": map], options: [.sortedKeys])) ?? Data("{}".utf8)
}

/// `PATCH /api/satellites/{room}/config`'s answer: `{sent, rejected, restarting}`.
public struct RoomsConfigSaveResult: Equatable, Sendable {
    public let sent: [String]
    /// What was refused and why, by name, in name order.
    public let rejected: [(name: String, reason: String)]
    public let restarting: Bool

    public init(sent: [String], rejected: [(name: String, reason: String)], restarting: Bool) {
        self.sent = sent
        self.rejected = rejected
        self.restarting = restarting
    }

    public init(data: Data?) {
        let o = data.flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] }
        sent = (o?["sent"] as? [Any])?.compactMap { $0 as? String } ?? []
        let r = o?["rejected"] as? [String: Any] ?? [:]
        rejected = r.keys.sorted().map { ($0, r[$0] as? String ?? "") }
        restarting = o?["restarting"] as? Bool == true
    }

    public static func == (a: Self, b: Self) -> Bool {
        a.sent == b.sent && a.restarting == b.restarting
            && a.rejected.map { $0.name } == b.rejected.map { $0.name }
            && a.rejected.map { $0.reason } == b.rejected.map { $0.reason }
    }
}

// MARK: - the routes, each through the phone's admin sign-in

/// `headers` and `onlyTo` come from ``AdminSession/call(_:)`` or
/// ``AdminSession/act(_:)`` per call.
public struct RoomAdminApi: Sendable {

    let client: DomovoiClient

    public init(client: DomovoiClient) {
        self.client = client
    }

    public func restart(_ room: String, headers: [String: String], onlyTo: String) async throws {
        _ = try await client.raw(
            method: "POST", path: RoomsApi.room(room) + "/restart",
            body: DomovoiClient.emptyObject, headers: headers, onlyTo: onlyTo
        )
    }

    public func upgrade(_ room: String, headers: [String: String], onlyTo: String) async throws {
        _ = try await client.raw(
            method: "POST", path: RoomsApi.room(room) + "/upgrade",
            headers: headers, onlyTo: onlyTo
        )
    }

    public func saveConfig(
        _ room: String, _ changes: [(name: String, value: RoomsConfigValue)],
        headers: [String: String], onlyTo: String
    ) async throws -> RoomsConfigSaveResult {
        let data = try await client.raw(
            method: "PATCH", path: RoomsApi.config(room),
            body: roomsConfigSaveBody(changes), headers: headers, onlyTo: onlyTo
        )
        return RoomsConfigSaveResult(data: data)
    }
}

/// A room's admin actions for its overview and Settings tab: one at a time,
/// the toast each ends in, and why the last one didn't work.
@MainActor
@Observable
public final class RoomAdminModel {

    public private(set) var restarting = false
    public private(set) var asking = false
    public private(set) var saving = false

    /// Why the last restart or upgrade didn't work (under the buttons).
    public private(set) var problem: String?

    private let admin: AdminSession
    private let api: RoomAdminApi

    public init(admin: AdminSession) {
        self.admin = admin
        api = RoomAdminApi(client: admin.client)
    }

    /// The toast: `restarting kitchen…`, or why not.
    public func restart(_ room: String) async -> String {
        restarting = true
        problem = nil
        defer { restarting = false }
        let api = api
        do {
            try await admin.act { h, srv in try await api.restart(room, headers: h, onlyTo: srv) }
            return RoomAdminWords.restarting(room)
        } catch {
            let why = Self.problem("restart", error)
            problem = why
            return why
        }
    }

    /// The toast, and whether the room list should be read again (asked for,
    /// or a 409: one already under way).
    public func upgrade(_ room: String) async -> (said: String, reread: Bool) {
        asking = true
        problem = nil
        defer { asking = false }
        let api = api
        do {
            try await admin.act { h, srv in try await api.upgrade(room, headers: h, onlyTo: srv) }
            return (RoomAdminWords.upgradingRoom(room), true)
        } catch {
            let why = Self.problem("upgrade", error)
            problem = why
            return (why, (error as? DomovoiError)?.failure?.status == 409)
        }
    }

    /// The save's answer, or nil with the toast saying why.
    public func save(
        _ room: String, _ changes: [(name: String, value: RoomsConfigValue)]
    ) async -> (result: RoomsConfigSaveResult?, said: String) {
        saving = true
        defer { saving = false }
        let api = api
        let sending = changes.map { RoomsConfigChange(name: $0.name, value: $0.value) }
        do {
            let r = try await admin.act { h, srv in
                try await api.saveConfig(room, sending.map { (name: $0.name, value: $0.value) }, headers: h, onlyTo: srv)
            }
            return (r, RoomAdminWords.saved(room, r))
        } catch {
            return (nil, Self.problem("save", error))
        }
    }

    /// Admin ended (or the screen became a shared one).
    public func forget() { problem = nil }

    static func problem(_ action: String, _ error: any Error) -> String {
        if let why = (error as? AdminSession.AdminUnavailable)?.reason { return why }
        if error is AdminSession.NotSignedIn { return adminOnly }
        if let failure = error as? DomovoiError { return failureText(action, failure) }
        return "\(action) failed"
    }
}

/// A change as a `Sendable` pair, for crossing into ``AdminSession/act(_:)``.
private struct RoomsConfigChange: Sendable {
    let name: String
    let value: RoomsConfigValue
}
