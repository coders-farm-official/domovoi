import Foundation
import Observation

// ---------------------------------------------------------------------------
// The Domovoi server, as admin on this phone (1.5.0; design §1's server
// area): install an update, restart. The port of Android's
// `ui/screens/settings/ServerUpdate.kt`, rule for rule and word for word,
// which is the dashboard's Settings → About & updates → Version card
// (web/static/settings.jsx `VersionSection`, and `restartDomovoiServer` in
// components.jsx) cut to those two jobs: check for updates (device tier),
// Update Domovoi (the pull: admin security; where the update unit can
// restart unattended, the pull then the restart, one press), Restart to
// apply changes and Restart Domovoi (admin security). Each mutation goes
// through ``AdminSession/act(_:)`` after the dashboard's own confirm.
// Offered only while this phone holds admin and never on a shared screen.
//
// Left for the dashboard: the update channel switch, a Windows or Mac
// install's host step (so Update Domovoi itself, where there is one),
// signed-update badges, the last update's warnings and the restart-by-hand
// command line.
// ---------------------------------------------------------------------------

/// `GET /api/config/version`, the parts the card reads.
public struct ServerVersion: Equatable, Sendable {

    public struct LastUpdate: Equatable, Sendable {
        public var status: String?
        public var mode: String?
        public var startedAt: String?
        public var error: String?
        public var fromSha: String?
        public var toSha: String?

        public init(status: String? = nil, mode: String? = nil, startedAt: String? = nil,
                    error: String? = nil, fromSha: String? = nil, toSha: String? = nil) {
            self.status = status
            self.mode = mode
            self.startedAt = startedAt
            self.error = error
            self.fromSha = fromSha
            self.toSha = toSha
        }
    }

    public var version: String?
    public var sha: String?
    public var runningSha: String?
    public var checkoutSha: String?
    public var startedAt: String?
    public var restartRequired = false
    public var codeRestartRequired: Bool?
    public var restartInProgress = false
    public var restartCapable = false
    public var restartMode: String?
    public var restartHint: String?
    public var badSha: String?
    public var lastUpdate: LastUpdate?
    public var pluginsPending: [String] = []
    /// A Windows or Mac install's host side with its own update step:
    /// "windows", "mac" or nil.
    public var hostSide: String?

    public init() {}

    /// `pendingRestart(v).code`.
    public var codePending: Bool { codeRestartRequired ?? restartRequired }
    public var updateUnit: Bool { restartMode == "update" }

    /// Nil for a body that isn't an object.
    public init?(data: Data) {
        guard let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return nil }
        func str(_ obj: [String: Any], _ k: String) -> String? { obj[k] as? String }
        func any(_ obj: [String: Any], _ k: String) -> String? {
            switch obj[k] {
            case let s as String: return s
            case let n as NSNumber where CFGetTypeID(n) != CFBooleanGetTypeID(): return n.stringValue
            default: return nil
            }
        }
        func flag(_ obj: [String: Any], _ k: String) -> Bool? {
            guard let n = obj[k] as? NSNumber, CFGetTypeID(n) == CFBooleanGetTypeID() else { return nil }
            return n.boolValue
        }
        version = str(o, "version")
        sha = str(o, "sha")
        runningSha = str(o, "running_sha")
        checkoutSha = str(o, "checkout_sha")
        startedAt = any(o, "started_at")
        restartRequired = flag(o, "restart_required") == true
        codeRestartRequired = flag(o, "code_restart_required")
        restartInProgress = flag(o, "restart_in_progress") == true
        restartCapable = flag(o, "restart_capable") == true
        restartMode = str(o, "restart_mode")
        restartHint = str(o, "restart_hint")
        badSha = str(o, "bad_sha")
        if let last = o["last_update"] as? [String: Any] {
            lastUpdate = LastUpdate(
                status: str(last, "status"), mode: str(last, "mode"), startedAt: any(last, "started_at"),
                error: str(last, "error"), fromSha: str(last, "from_sha"), toSha: str(last, "to_sha")
            )
        }
        pluginsPending = (o["plugins_pending_restart"] as? [Any] ?? []).compactMap { p in
            guard let p = p as? [String: Any] else { return nil }
            return "\(str(p, "slug") ?? "?") \(any(p, "from_version") ?? "?") → \(any(p, "to_version") ?? "?")"
        }
        if let side = o["windows_side"] as? [String: Any], flag(side, "present") == true {
            let kind = str(side, "kind") ?? "windows"
            // A Mac side without the updater is updated by hand: no host step.
            hostSide = (kind == "mac" && flag(side, "updates") != true) ? nil : kind
        }
    }
}

/// `POST /api/config/version/check`'s answer.
public struct UpdateCheck: Equatable, Sendable {
    public var upstream = false
    public var channel: String?
    public var behind: Int?
    public var ahead: Int?
    public var upstreamVersion: String?
    public var upstreamSha: String?
    public var latestRelease: String?
    public var error: String?

    public init() {}

    public init(data: Data) {
        guard let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return }
        switch o["upstream"] {
        case let n as NSNumber where CFGetTypeID(n) == CFBooleanGetTypeID(): upstream = n.boolValue
        case let s as String: upstream = !s.trimmingCharacters(in: .whitespaces).isEmpty
        default: upstream = false
        }
        channel = o["channel"] as? String
        behind = (o["behind"] as? NSNumber)?.intValue
        ahead = (o["ahead"] as? NSNumber)?.intValue
        upstreamVersion = o["upstream_version"] as? String
        upstreamSha = o["upstream_sha"] as? String
        latestRelease = o["latest_release"] as? String
        error = o["error"] as? String
    }
}

/// What the card offers, in the dashboard's order.
public enum ServerMode: Equatable, Sendable {
    case busy, restart, pull, held, check
}

public func serverMode(_ v: ServerVersion, check: UpdateCheck?, following: Bool) -> ServerMode {
    if following || v.restartInProgress { return .busy }
    if v.restartRequired { return .restart }
    if let c = check, c.upstream, (c.behind ?? 0) > 0 {
        return (v.badSha != nil && c.upstreamSha == v.badSha) ? .held : .pull
    }
    return .check
}

/// One press (pull, then the restart) where the update unit can run the
/// restart unattended; else two steps. Never with a host side here.
public func serverOnePress(_ v: ServerVersion) -> Bool {
    v.updateUnit && v.restartCapable && v.hostSide == nil
}

/// Whether the card is offered: admin held, not a shared screen.
public func serverAdminHeld(_ state: AdminSession.State, sharedScreen: Bool) -> Bool {
    if sharedScreen { return false }
    if case .signedOut = state { return false }
    return true
}

private func unitAppliedSha(_ last: ServerVersion.LastUpdate?) -> String? {
    guard let last else { return nil }
    let sha = last.status == "ok" ? last.toSha : last.fromSha
    guard let sha, sha.range(of: "^[0-9a-f]{7,64}$", options: .regularExpression) != nil else { return nil }
    return sha
}

/// `restartRunsFullUpdate`: why a plain restart pressed now would run the
/// update unit's full update, or nil.
public func serverRestartRunsFullUpdate(_ v: ServerVersion) -> String? {
    guard v.updateUnit else { return nil }
    var checkout = v.checkoutSha ?? ""
    if checkout.hasSuffix("-dirty") { checkout.removeLast(6) }
    let pulled = v.codePending
        ? "Pulled code is waiting to load (running \(v.runningSha ?? v.sha ?? "null"), checked out \(checkout))"
        : nil
    if let applied = unitAppliedSha(v.lastUpdate),
       checkout.range(of: "^[0-9a-f]{4,64}$", options: .regularExpression) != nil {
        if applied.hasPrefix(checkout) { return nil }
        return pulled ?? "The checkout (\(checkout)) isn’t the commit the update unit last applied (\(applied.prefix(7)))"
    }
    return pulled
}

/// `ServerUpdateWords`, word for word.
public enum ServerUpdateWords {
    public static let title = "Domovoi server"
    public static let sub = "The Domovoi release this server runs, and installing its updates, as admin on this phone."
    public static let buildLineOrPhone =
        "The server's build / version identifiers live on the dashboard under About & updates, or sign in as admin on this phone."

    public static let check = "Check for updates"
    public static let checking = "Checking…"
    public static let update = "Update Domovoi"
    public static let pulling = "Pulling…"
    public static let restartApply = "Restart to apply changes"
    public static let restart = "Restart Domovoi"
    public static let restarting = "Restarting…"
    public static let updating = "Updating…"
    public static let already = "already updating — this card follows it"
    public static let reading = "reading the server's version…"
    public static let updateButton = "update"
    public static let restartButton = "restart"
    public static let cancel = "cancel"

    static let updateText =
        "This backs up the database, updates dependencies and migrations if "
        + "they changed, and restarts domovoi-core and domovoi-web. If they "
        + "don’t come back healthy it rolls everything back. Voice is "
        + "unavailable meanwhile, usually for under a minute, longer when "
        + "dependencies change."

    public static let pullConfirm =
        "Update Domovoi?\n\n"
        + "This puts the newest version on the Domovoi server but does not start it yet — "
        + "this panel will then offer the restart that does. Rooms can be "
        + "upgraded individually afterwards."
    public static let onePressConfirm =
        "Update Domovoi?\n\n"
        + "Domovoi pulls the latest code, then backs up, applies it and restarts (the rooms, phones and "
        + "this dashboard lose Domovoi for a minute or two; it rolls back if it doesn’t come back healthy)."
    static let plainConfirm =
        "Restart Domovoi now?\n\n"
        + "domovoi-core and domovoi-web restart, so changes to domovoi/.env "
        + "take effect. Voice and this dashboard are back in a few seconds, and "
        + "rooms reconnect on their own. Music playing in a room may stop; "
        + "ask for it again if it does."

    public static func restartConfirm(_ v: ServerVersion, plain: Bool) -> String {
        if plain, let full = serverRestartRunsFullUpdate(v) {
            return "Restart Domovoi now, as a full update?\n\n"
                + "\(full), so this restart runs the update unit’s full update, not a quick restart. \(updateText)"
        }
        if plain { return plainConfirm }
        if v.updateUnit { return "Apply the pulled code?\n\n\(updateText)" }
        return "Restart the Domovoi services to load the pulled code?\n\n"
            + "This bounces domovoi-core and domovoi-web. Voice is unavailable for "
            + "a few seconds and connected rooms reconnect on their own."
    }

    /// A confirm as an alert: its question as the title, the rest as the
    /// message.
    public static func split(_ text: String) -> (title: String, body: String) {
        guard let r = text.range(of: "\n\n") else { return (text, "") }
        return (String(text[..<r.lowerBound]), String(text[r.upperBound...]))
    }

    public static func codePending(restartCapable: Bool) -> String {
        "New code is on disk but this process is still running the old modules"
            + (restartCapable ? " — restart below to load it." : " — restart the Domovoi server for it to take effect.")
    }

    public static func pluginsPending(_ list: [String], restartCapable: Bool) -> String {
        "Plugin upgrade\(list.count == 1 ? "" : "s") waiting for a restart: \(list.joined(separator: ", "))"
            + (restartCapable ? " — restart below to finish." : " — restart the Domovoi server to finish.")
    }

    public static func held(_ badSha: String) -> String {
        "The latest commit (\(badSha.prefix(7))) failed to apply on this server "
            + "and was rolled back, so it isn’t offered again. The next commit upstream will be."
    }

    public static func byHand(_ v: ServerVersion) -> String {
        var why = v.restartHint ?? "This host can’t restart itself"
        while let last = why.last, last == "." || last.isWhitespace { why.removeLast() }
        return why + ". Restart the Domovoi services the way they were started."
    }

    public static func hostOnDashboard(_ kind: String) -> String {
        "This server has a \(kind == "mac" ? "Mac" : "Windows") part too, so Update Domovoi is on the dashboard, which updates both."
    }

    /// `versionCheckWords`.
    public static func checkWords(_ r: UpdateCheck?) -> String? {
        guard let r, r.upstream else { return nil }
        let behind = r.behind ?? 0
        if r.channel == "stable" {
            if behind > 0 { return "Domovoi \(r.upstreamVersion ?? "a newer release") is available" }
            if (r.ahead ?? 0) > 0 { return "newer than the latest release" }
            return "the latest release"
        }
        return behind > 0 ? "\(behind) commit\(behind == 1 ? "" : "s") behind" : "up to date"
    }

    /// The check's toast.
    public static func checked(_ r: UpdateCheck) -> String {
        if let error = r.error { return "update check failed: \(error)" }
        if !r.upstream {
            return r.channel == "stable" && r.latestRelease == nil
                ? "no release has been published on the stable channel yet"
                : "no upstream configured — can’t check"
        }
        return checkWords(r) ?? "up to date"
    }

    public static let resultLabels: [String: String] = [
        "ok": "applied", "running": "in progress", "rolled_back": "rolled back", "rollback_failed": "rollback failed",
        "failed": "failed", "aborted": "not applied", "refused": "not applied",
    ]

    public static func lastUpdate(_ status: String) -> String { "last update: \(resultLabels[status] ?? status)" }
}

/// How a restart this phone asked for is going.
public enum RestartProgress: Equatable, Sendable {
    case waiting
    case done(ok: Bool, said: String)
}

/// What was true at the press (restartDomovoiServer's waitForServer).
public struct RestartPress: Equatable, Sendable {
    public let mode: String
    public let plain: Bool
    public let previousRun: String?
    public let previousStart: String?

    public init(mode: String, plain: Bool, previousRun: String?, previousStart: String?) {
        self.mode = mode
        self.plain = plain
        self.previousRun = previousRun
        self.previousStart = previousStart
    }

    public var what: String { plain ? "restart" : "update" }
    public var deadlineMs: Int64 { mode == "update" ? 15 * 60_000 : 90_000 }
    public var updating: Bool { mode == "update" && !plain }

    public func after(_ v: ServerVersion, wentAway: Bool) -> RestartProgress {
        let run = v.lastUpdate
        let newRun = run != nil && run?.startedAt != previousRun && run?.status != "running"
        if mode == "update", newRun, let run, run.status != "ok" {
            let label = run.status.map { ServerUpdateWords.resultLabels[$0] ?? $0 } ?? "null"
            return .done(ok: false, said: run.error ?? "\(what) \(label) — see journalctl -u domovoi-update")
        }
        let startedAgain: Bool
        if let previousStart, let now = v.startedAt { startedAgain = now != previousStart } else { startedAgain = wentAway }
        if !v.restartRequired && !v.restartInProgress && (mode == "update" ? newRun : startedAgain) {
            return .done(ok: true, said: "restarted — now running \(v.runningSha ?? v.sha ?? "new code")")
        }
        return .waiting
    }

    public func tooLong() -> String {
        mode == "update"
            ? "the \(what) is taking longer than expected — check journalctl -u domovoi-update"
            : "restart is taking longer than expected — check the service by hand"
    }
}

/// The routes. Mutations take `headers` and `onlyTo` from ``AdminSession``.
public struct ServerUpdateApi: Sendable {

    public static let version = "/api/config/version"

    let client: DomovoiClient

    public init(client: DomovoiClient) {
        self.client = client
    }

    public func read() async throws -> ServerVersion? {
        ServerVersion(data: try await client.raw(method: "GET", path: Self.version))
    }

    public func check() async throws -> UpdateCheck {
        UpdateCheck(data: try await client.raw(method: "POST", path: Self.version + "/check", body: DomovoiClient.emptyObject))
    }

    public func pull(headers: [String: String], onlyTo: String) async throws -> [String: Any]? {
        let data = try await client.raw(
            method: "POST", path: Self.version + "/pull", body: DomovoiClient.emptyObject, headers: headers, onlyTo: onlyTo
        )
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    public func restart(headers: [String: String], onlyTo: String) async throws -> [String: Any]? {
        let data = try await client.raw(
            method: "POST", path: Self.version + "/restart", body: DomovoiClient.emptyObject, headers: headers, onlyTo: onlyTo
        )
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }
}

/// A pull's or restart's answer, as the fields the card reads.
public struct ServerAnswer: Equatable, Sendable {
    public var ok = false
    public var inProgress = false
    public var pulled = false
    public var mode: String?
    public var error: String?
    public var notice: String?
    public var newSha: String?

    public init() {}

    init(_ o: [String: Any]?) {
        ok = o?["ok"] as? Bool == true
        inProgress = o?["in_progress"] as? Bool == true
        pulled = o?["pulled"] as? Bool == true
        mode = o?["mode"] as? String
        error = o?["error"] as? String
        notice = o?["notice"] as? String
        newSha = o?["new_sha"] as? String
    }
}

/// The card's state and its four actions. Every toast goes through ``say``.
@MainActor
@Observable
public final class ServerUpdateModel {

    public private(set) var version: ServerVersion?
    public private(set) var check: UpdateCheck?
    public private(set) var checking = false
    public private(set) var pulling = false
    /// A restart this card asked for and is following.
    public private(set) var following: RestartPress?
    public private(set) var problem: String?

    /// Where the toasts go (the shell's).
    @ObservationIgnored public var say: (String) -> Void = { _ in }

    private let admin: AdminSession
    private let api: ServerUpdateApi
    private let nowMs: @Sendable () -> Int64
    private let wait: @Sendable (Int64) async throws -> Void

    public init(
        admin: AdminSession,
        nowMs: @Sendable @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        wait: @Sendable @escaping (Int64) async throws -> Void = { try await Task.sleep(for: .milliseconds($0)) }
    ) {
        self.admin = admin
        api = ServerUpdateApi(client: admin.client)
        self.nowMs = nowMs
        self.wait = wait
    }

    public var mode: ServerMode? {
        version.map { serverMode($0, check: check, following: following != nil) }
    }

    public func load() async {
        if let v = try? await api.read() { version = v }
    }

    public func checkForUpdates() async {
        checking = true
        defer { checking = false }
        do {
            let r = try await api.check()
            check = r
            say(ServerUpdateWords.checked(r))
        } catch let e as DomovoiError {
            say(failureText("update check", e))
        } catch {
            say("update check failed")
        }
    }

    private func readNow() async -> ServerVersion? {
        try? await api.read()
    }

    private func alreadyUpdating() async -> Bool {
        if let now = await readNow(), now.restartInProgress {
            say(ServerUpdateWords.already)
            version = now
            return true
        }
        return false
    }

    /// Update Domovoi: the pull, then (one press) the restart.
    public func update(onePress: Bool) async {
        pulling = true
        problem = nil
        let action = onePress ? "update" : "pull"
        let answer: ServerAnswer
        do {
            if await alreadyUpdating() { pulling = false; return }
            let api = api
            let o = try await admin.act { h, srv in
                ServerAnswerBox(try await api.pull(headers: h, onlyTo: srv))
            }
            answer = o.answer
        } catch {
            pulling = false
            problem = Self.problem(action, error)
            say(problem ?? "")
            return
        }
        pulling = false
        let notice = answer.notice.map { "\($0). " } ?? ""
        if !answer.pulled {
            if let error = answer.error, error.range(of: "under way|already running|in progress", options: [.regularExpression, .caseInsensitive]) != nil {
                say(ServerUpdateWords.already)
                await load()
                return
            }
            say("\(notice)\(action) failed: \(answer.error ?? "unknown")")
            return
        }
        check = nil
        if onePress {
            await restart(plain: false)
        } else {
            say("\(notice)pulled\(answer.newSha.map { " — now \($0)" } ?? ""). Restart to apply.")
            await load()
        }
    }

    /// The restart (asked already), then the wait for the server.
    public func restart(plain: Bool) async {
        problem = nil
        guard let before = await readNow() ?? version else { return }
        let kind = before.updateUnit ? "update" : "restart"
        if before.restartInProgress {
            say("\(before.updateUnit ? "an update" : "a restart") is already under way — waiting for it to finish")
            await follow(RestartPress(mode: kind, plain: plain, previousRun: before.lastUpdate?.startedAt, previousStart: before.startedAt))
            return
        }
        let runsPlain = plain && serverRestartRunsFullUpdate(before) == nil
        let api = api
        do {
            let o = try await admin.act { h, srv in ServerAnswerBox(try await api.restart(headers: h, onlyTo: srv)) }
            let answer = o.answer
            if answer.ok {
                let mode = answer.mode ?? kind
                say(mode == "update" && !runsPlain ? "updating…" : "restarting…")
                await follow(RestartPress(mode: mode, plain: runsPlain, previousRun: before.lastUpdate?.startedAt, previousStart: before.startedAt))
            } else if answer.inProgress {
                await follow(RestartPress(mode: kind, plain: runsPlain, previousRun: before.lastUpdate?.startedAt, previousStart: before.startedAt))
            } else {
                say("restart failed: \(answer.error ?? "unknown")")
            }
        } catch let e as DomovoiError where Self.cutOff(e) {
            // Cut off mid-request: the bounce, most likely. Follow it.
            say(kind == "update" && !runsPlain ? "updating…" : "restarting…")
            await follow(RestartPress(mode: kind, plain: runsPlain, previousRun: before.lastUpdate?.startedAt, previousStart: before.startedAt))
        } catch {
            problem = Self.problem("restart", error)
            say(problem ?? "")
        }
    }

    /// Wait for the server to come back, or say what went wrong.
    func follow(_ press: RestartPress) async {
        following = press
        defer { following = nil }
        let until = nowMs() + press.deadlineMs
        var wentAway = false
        while nowMs() < until {
            do { try await wait(2_000) } catch { return }
            guard let v = try? await api.read() else {
                wentAway = true
                continue
            }
            if case .done(_, let said) = press.after(v, wentAway: wentAway) {
                version = v
                check = nil
                say(said)
                return
            }
        }
        say(press.tooLong())
        await load()
    }

    /// Only a connection-level failure (no answer) can mean the bounce cut
    /// the request off.
    static func cutOff(_ e: DomovoiError) -> Bool {
        if case .unreachable = e { return true }
        return false
    }

    static func problem(_ action: String, _ error: any Error) -> String {
        if let why = (error as? AdminSession.AdminUnavailable)?.reason { return why }
        if error is AdminSession.NotSignedIn { return adminOnly }
        if let e = error as? DomovoiError { return failureText(action, e) }
        return "\(action) failed"
    }
}

/// A pull's or restart's answer crossing out of ``AdminSession/act(_:)``.
struct ServerAnswerBox: Sendable {
    let answer: ServerAnswer
    init(_ o: [String: Any]?) { answer = ServerAnswer(o) }
}
