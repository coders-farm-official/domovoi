import Foundation
import Observation

// ---------------------------------------------------------------------------
// People as admin on this phone (1.5.0; design §1's People jobs): rename
// someone, forget someone. The port of Android's
// `ui/screens/people/PeopleAdmin.kt`, rule for rule and word for word.
//
// Both are admin on the server (PATCH and DELETE /api/people/{id}), so they
// are offered only while this phone holds admin and never on a shared screen
// (where People is hidden anyway), each through ``AdminSession/act(_:)``.
// Forget asks first in the dashboard's words (people.jsx `ConfirmForget`),
// typing the name included. Signed out, the profile's line adds "or sign in
// as admin on this phone".
// ---------------------------------------------------------------------------

/// `PeopleAdminWords`, word for word.
public enum PeopleAdminWords {
    /// The profile's line, signed out on a household phone.
    public static let editOrPhone =
        "renaming and forgetting someone are done on the dashboard, or sign in as admin on this phone"

    public static let rename = "rename"
    public static let save = "save"
    public static let cancel = "cancel"
    public static let newName = "new name"
    public static let forget = "forget person"

    /// How many turns the profile reads (the dashboard's
    /// `PERSON_TURNS_FETCHED`): that many means "or more".
    public static let turnsFetched = 200

    public static func renamed(_ name: String) -> String { "renamed to \(name)" }
    public static func forgot(_ name: String) -> String { "forgot \(name) · cascade complete" }

    /// `ConfirmForget`'s title: the id too when somebody shares the name.
    public static func forgetTitle(_ p: Person) -> String {
        "Forget \(p.name)\(p.namesakes > 0 ? " (#\(p.id))" : "")?"
    }

    /// `ConfirmForget`'s body, line for line: what Domovoi will lose.
    public static func forgetBody(_ p: Person, sessionCount: Int, turnCount: Int) -> String {
        let samples = p.voiceProfileCount
        let turns = "\(turnCount)\(turnCount >= turnsFetched ? "+" : "") conversation turn\(turnCount == 1 ? "" : "s")"
        let sessions = sessionCount > 0 ? " in \(sessionCount) session\(sessionCount == 1 ? "" : "s")" : ""
        return "This is a cascade delete. Domovoi will lose:\n"
            + "• \(samples) voice profile\(samples == 1 ? "" : "s")\n"
            + "• what they said: \(turns)\(sessions), with Domovoi's answers\n"
            + "• their memories, favorites and preferences\n\n"
            + "Turns where someone else said their name stay: Domovoi goes by who was speaking."
    }

    public static func typeToConfirm(_ name: String) -> String { "Type \(name) to confirm." }
    public static func forgetButton(_ name: String) -> String { "Forget \(name)" }
}

/// Whether rename and forget are offered: admin held, not a shared screen.
public func peopleAdminHeld(_ state: AdminSession.State, sharedScreen: Bool) -> Bool {
    if sharedScreen { return false }
    if case .signedOut = state { return false }
    return true
}

/// The profile's line about renaming and forgetting: none with admin held
/// (the buttons are there), the old one on a shared screen.
public func peopleEditLine(_ state: AdminSession.State, sharedScreen: Bool) -> String? {
    if peopleAdminHeld(state, sharedScreen: sharedScreen) { return nil }
    return sharedScreen ? PeopleStrings.renamingIsOnTheDashboard : PeopleAdminWords.editOrPhone
}

/// The name a rename sends: trimmed, runs of whitespace as one space; nil
/// when there is nothing to send (blank, or the name they have).
public func peopleRenameTo(current: String, draft: String) -> String? {
    let name = draft.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    return name.isEmpty || name == current ? nil : name
}

/// Forget is pressed only once the name is typed (`ConfirmForget`'s `ok`).
public func peopleForgetConfirmed(typed: String, name: String) -> Bool {
    typed.trimmingCharacters(in: .whitespacesAndNewlines) == name
}

/// The two routes; `headers` and `onlyTo` come from ``AdminSession``.
public struct PeopleAdminApi: Sendable {

    let client: DomovoiClient

    public init(client: DomovoiClient) {
        self.client = client
    }

    public func rename(_ id: Int, name: String, headers: [String: String], onlyTo: String) async throws {
        let body = try JSONEncoder().encode(["name": name])
        _ = try await client.raw(method: "PATCH", path: "/api/people/\(id)", body: body, headers: headers, onlyTo: onlyTo)
    }

    public func forget(_ id: Int, headers: [String: String], onlyTo: String) async throws {
        _ = try await client.raw(method: "DELETE", path: "/api/people/\(id)", headers: headers, onlyTo: onlyTo)
    }
}

/// Rename and forget, one at a time: each answers its toast.
@MainActor
@Observable
public final class PeopleAdminModel {

    public private(set) var busy = false

    private let admin: AdminSession
    private let api: PeopleAdminApi

    public init(admin: AdminSession) {
        self.admin = admin
        api = PeopleAdminApi(client: admin.client)
    }

    /// The toast, and whether it went through.
    public func rename(_ p: Person, to name: String) async -> (ok: Bool, said: String) {
        busy = true
        defer { busy = false }
        let api = api
        let id = p.id
        do {
            try await admin.act { h, srv in try await api.rename(id, name: name, headers: h, onlyTo: srv) }
            return (true, PeopleAdminWords.renamed(name))
        } catch {
            return (false, Self.problem("rename", error))
        }
    }

    public func forget(_ p: Person) async -> (ok: Bool, said: String) {
        busy = true
        defer { busy = false }
        let api = api
        let id = p.id
        do {
            try await admin.act { h, srv in try await api.forget(id, headers: h, onlyTo: srv) }
            return (true, PeopleAdminWords.forgot(p.name))
        } catch {
            return (false, Self.problem("forget", error))
        }
    }

    /// The server's own sentence for a taken name (409), an unknown person
    /// or a bad name; else the usual.
    public static func problem(_ action: String, _ error: any Error) -> String {
        if let why = (error as? AdminSession.AdminUnavailable)?.reason { return why }
        if error is AdminSession.NotSignedIn { return adminOnly }
        if let e = error as? DomovoiError {
            if let f = e.failure, [404, 409, 422].contains(f.status), let detail = jsonDetail(of: f.body) {
                return detail
            }
            return failureText(action, e)
        }
        return "\(action) failed"
    }
}
