import Foundation
import Observation

// ---------------------------------------------------------------------------
// Printers as admin on this phone (1.5.0; design §1): rename a printer,
// remove one. The port of Android's `ui/screens/printing/PrinterAdmin.kt`,
// rule for rule and word for word.
//
// Both are admin on the server (POST /api/printers/{id}/rename and DELETE
// /api/printers/{id}), so they are offered only while this phone holds
// admin and never on a shared screen, through ``AdminSession/act(_:)``.
// Remove asks first in the dashboard's words (printers.jsx PrinterCard's
// `useDeleteConfirm`); rename, as there, does not. Trusting a printer's new
// certificate stays on the dashboard.
// ---------------------------------------------------------------------------

/// Whether rename and remove are offered: admin held, not a shared screen.
public func printersAdminHeld(_ state: AdminSession.State, sharedScreen: Bool) -> Bool {
    if sharedScreen { return false }
    if case .signedOut = state { return false }
    return true
}

/// `PrinterAdminWords`, word for word.
public enum PrinterAdminWords {
    /// The panel's line, signed out on a household phone.
    public static let adminLineOrPhone =
        "An admin can rename or remove printers on the web dashboard, or sign in as admin on this phone."

    public static let rename = "Rename"
    public static let save = "Save"
    public static let cancel = "Cancel"
    public static let remove = "Remove"
    public static let name = "printer name"

    public static func removeTitle(_ name: String) -> String { "Remove \(name)?" }
    public static func removeBody(canScan: Bool) -> String {
        "Domovoi stops printing\(canScan ? " and scanning" : "") with it. You can add it again later."
    }
    public static func removed(_ name: String) -> String { "removed \(name)" }
}

/// The panel's line under the printers: none with admin held.
public func printersAdminLine(_ state: AdminSession.State, sharedScreen: Bool) -> String? {
    if printersAdminHeld(state, sharedScreen: sharedScreen) { return nil }
    return sharedScreen ? PrintingStrings.adminOnlyLine : PrinterAdminWords.adminLineOrPhone
}

/// The name a rename sends: trimmed, at most 60 characters; nil when there
/// is nothing to send (blank, or the name it has).
public func printerRenameTo(current: String, draft: String) -> String? {
    let n = String(draft.trimmingCharacters(in: .whitespacesAndNewlines).prefix(60))
    return n.isEmpty || n == current ? nil : n
}

public struct PrinterAdminApi: Sendable {

    let client: DomovoiClient

    public init(client: DomovoiClient) {
        self.client = client
    }

    public func rename(_ id: Int, name: String, headers: [String: String], onlyTo: String) async throws {
        let body = try JSONEncoder().encode(["name": name])
        _ = try await client.raw(method: "POST", path: "/api/printers/\(id)/rename", body: body, headers: headers, onlyTo: onlyTo)
    }

    public func remove(_ id: Int, headers: [String: String], onlyTo: String) async throws {
        _ = try await client.raw(method: "DELETE", path: "/api/printers/\(id)", headers: headers, onlyTo: onlyTo)
    }
}

/// Rename and remove, one at a time; each answers its toast (nil: none).
@MainActor
@Observable
public final class PrinterAdminModel {

    public private(set) var busy = false

    private let admin: AdminSession
    private let api: PrinterAdminApi

    public init(admin: AdminSession) {
        self.admin = admin
        api = PrinterAdminApi(client: admin.client)
    }

    /// Renamed: (true, nil); refused: (false, why).
    public func rename(_ p: Printer, to name: String) async -> (ok: Bool, said: String?) {
        busy = true
        defer { busy = false }
        let api = api
        let id = p.id
        do {
            try await admin.act { h, srv in try await api.rename(id, name: name, headers: h, onlyTo: srv) }
            return (true, nil)
        } catch {
            return (false, Self.problem("rename", error))
        }
    }

    public func remove(_ p: Printer) async -> (ok: Bool, said: String?) {
        busy = true
        defer { busy = false }
        let api = api
        let id = p.id
        do {
            try await admin.act { h, srv in try await api.remove(id, headers: h, onlyTo: srv) }
            return (true, PrinterAdminWords.removed(p.name))
        } catch {
            return (false, Self.problem("remove", error))
        }
    }

    static func problem(_ action: String, _ error: any Error) -> String {
        if let why = (error as? AdminSession.AdminUnavailable)?.reason { return why }
        if error is AdminSession.NotSignedIn { return adminOnly }
        if let e = error as? DomovoiError { return printingFailureText(action, e) }
        return "\(action) failed"
    }
}
