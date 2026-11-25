import Foundation
import Synchronization

/// Admin on this phone (1.5.0; `docs/SECURITY_PRIVACY.md`, "Admin on a phone:
/// the day's grant"). A port of `android/.../net/AdminSession.kt`, rule for
/// rule; where this differs, the difference is said where it is.
///
/// The phone signs in with the admin password through the dashboard's own
/// login (`POST /api/auth/login`) and holds the session's Bearer **in memory
/// only**: never written to disk, never logged, never in a URL. It is bound to
/// the server it was issued by; when the active server changes, it is not sent
/// (every admin call is `onlyTo` that server) and it is dropped.
///
/// The password is worth more than the household token, so it goes only to a
/// server that has just proved the identity this phone pinned for it (`prove`,
/// the identity gate's `.verified` verdict through ``IdentityAdmission``). The
/// session is bound to THAT identity, not to the address text: every admin
/// call asks the gate again, and a server that proves a different key (a
/// re-pin, another machine at the same address) never receives the Bearer,
/// which is forgotten. Room mode forgets it too, and no admin call goes out
/// while the phone is a room (security review 2026-11-22, L57).
///
/// **Forgotten at once, not only at the next call** (security review
/// 2026-11-23, L57): switching the active server or forgetting it
/// (``ShellStore/setServerURL(_:)``, ``ShellStore/forgetActiveServer()``), any
/// change to the identity pins (every store write that changes them) and
/// entering room mode (``HouseholdLock/setRoomModeActive(_:)``) each reach
/// ``AdminSessions``. Sign-out (L56) sends the Bearer only after the server
/// proves the identity the session is bound to, and only while that server is
/// still the active one and the phone is not a room; otherwise it just
/// forgets.
///
/// **Keep admin today** (``keepToday(label:)``, ``resume()``): after a
/// password sign-in the phone may take the day's grant (`AdminGrant.swift`)
/// and keep it behind Face ID or Touch ID, bound to this server and the
/// identity it proved. ``resume()`` opens it with the person's face or finger
/// and swaps it for a fresh session, only at that server after it proves that
/// identity again. The grant's 24 hours are the server's; the phone's copy of
/// the end is a hint, and a session the grant made is not used past it.
///
/// The session ends at the server: after an hour with no admin change (the
/// idle clock the login answer reports, which every admin write restarts), at
/// a grant's hard end, on sign-out, or when revoked. The phone learns that
/// from an admin refusal (``isAdminRefusal(status:body:)``) and drops the
/// Bearer; what the app shows from ``state`` is a hint, and the server is the
/// gate.
///
/// ### Concurrency
///
/// A `Sendable` class whose mutable state is one value under a `Mutex`, the
/// shape ``DomovoiClient`` and ``IdentityGate`` use: the hooks that forget it
/// (a store write, the room-mode seal) are synchronous and may run on any
/// thread, so an actor would leave a window between "the server switched" and
/// "the Bearer is gone". The lock is never held across an `await`, and every
/// injected closure and the change sink run outside it.
public final class AdminSession: Sendable {

    /// What the app shows. `idleUntilMs` is the phone's copy of the server's
    /// idle clock (nil when the server gave none); `keptUntilMs` is set while
    /// this phone keeps admin today. Epoch milliseconds.
    public enum State: Equatable, Sendable {
        case signedOut
        case signedIn(server: String, idleUntilMs: Int64?, keptUntilMs: Int64?)

        /// No session now, but admin is kept on this phone today for the
        /// active server: the Face ID or Touch ID check goes on (``resume()``).
        case kept(server: String, untilMs: Int64)
    }

    /// How a sign-in (or a resume) went.
    public enum SignIn: Equatable, Sendable {
        case ok
        case refused(String)
    }

    /// How keeping admin today went.
    public enum Keep: Equatable, Sendable {
        case ok(untilMs: Int64)
        case refused(String)
    }

    /// Thrown by ``call(_:)`` when there is no admin session for the active
    /// server (Android's `NotSignedIn`, an `IOException(ADMIN_ONLY)`).
    public struct NotSignedIn: Error, Equatable, Sendable {
        public init() {}
        public var message: String { adminOnly }
    }

    /// The live session. Never printed with its token: the default
    /// description of a struct prints every field.
    struct Live: Equatable, Sendable, CustomStringConvertible, CustomDebugStringConvertible {
        let server: String
        let token: String
        let idleMin: Int64?
        let fingerprint: String
        /// The day's grant, in memory while a session is live, so sign-out
        /// can revoke it without another Face ID check.
        var grant: String?
        /// A grant-made session's hard end (the grant's).
        var hardEndMs: Int64?
        var keptUntilMs: Int64?

        var description: String { "Live(\(server))" }
        var debugDescription: String { description }
    }

    private struct Inner {
        var live: Live?
        var state: State = .signedOut
        var onChange: (@Sendable (State) -> Void)?
    }

    private let inner = Mutex(Inner())

    let client: DomovoiClient
    private let prove: @Sendable (ServerURL) async throws -> String
    private let now: @Sendable () -> Int64
    private let grants: (any GrantShelf)?
    private let locker: (any GrantLocker)?
    private let deviceID: @Sendable () -> String

    /// The client's own room-mode seal: the one the household token obeys is
    /// the one admin obeys.
    var lock: HouseholdLock { client.lock }

    /// - Parameters:
    ///   - client: the app's client; the household token it adds is what the
    ///     grant endpoints need beside the Bearer.
    ///   - prove: proves the server at a base address and returns the
    ///     identity it proved (its key's fingerprint); throws, saying why,
    ///     when it can't. Asked before the password goes out and before every
    ///     admin call (``IdentityAdmission/fingerprintOf(_:)``).
    ///   - now: wall-clock epoch milliseconds (the grant's end is wall time).
    ///   - grants, locker: where the day's grant is kept and the biometric
    ///     lock on it. Without both, the phone signs in with the password
    ///     every time.
    ///   - deviceID: this phone's device id (``DeviceIdentity``), which the
    ///     grant is bound to.
    ///   - registry: what the forgetting hooks reach.
    public init(
        client: DomovoiClient,
        prove: @Sendable @escaping (ServerURL) async throws -> String,
        now: @Sendable @escaping () -> Int64 = AdminSession.wallClockMs,
        grants: (any GrantShelf)? = nil,
        locker: (any GrantLocker)? = nil,
        deviceID: @Sendable @escaping () -> String = { "" },
        registry: AdminSessions = .shared
    ) {
        self.client = client
        self.prove = prove
        self.now = now
        self.grants = grants
        self.locker = locker
        self.deviceID = deviceID
        registry.register(self)
        refresh()
    }

    public static let wallClockMs: @Sendable () -> Int64 = {
        Int64((Date().timeIntervalSince1970 * 1000).rounded())
    }

    // ---- what the app reads ------------------------------------------------

    public var state: State { inner.withLock { $0.state } }

    /// Call `sink` with each new ``state``, outside the lock. One sink, last
    /// one wins — the screen that draws it.
    public func onChange(_ sink: @Sendable @escaping (State) -> Void) {
        inner.withLock { $0.onChange = sink }
    }

    /// True while this phone holds an admin session for the active server.
    public var signedIn: Bool { liveForActive() != nil }

    /// The phone can keep admin today: Face ID or Touch ID is set up.
    public var canKeepToday: Bool { grants != nil && locker?.available == true }

    // ---- signing in --------------------------------------------------------

    /// Sign in with `password`. `label` is what the dashboard's session list
    /// shows ("phone: <device name>"). Never throws.
    public func signIn(password: String, label: String) async -> SignIn {
        if password.isEmpty { return .refused("type the admin password") }
        if lock.roomModeActive { return .refused(Self.roomMode) }
        let server = activeServer()
        guard let base = TokenScope.baseOf(server) else { return .refused("no server configured") }
        let fingerprint: String
        do {
            fingerprint = try await prove(base)
        } catch {
            return .refused(Self.proofRefusal(error))
        }
        let body = Self.json(["password": password, "label": String(label.prefix(Self.labelMax))])
        let data: Data
        do {
            data = try await client.raw(method: "POST", path: "/api/auth/login", body: body, onlyTo: server)
        } catch {
            return .refused(Self.signInRefusal(error, nonsense: "the server's sign-in made no sense"))
        }
        let reply = Self.object(data)
        guard let token = headerSafeToken(Self.string(reply, "token")) else {
            return .refused("the server's sign-in made no sense")
        }
        let idleMin = Self.long(reply, "idle_timeout_min")
        let idleSec = Self.long(reply, "idle_in_sec")
        // Not in the Kotlin: a switch or room mode that landed while the
        // login was in flight leaves nothing behind for the old server.
        if activeServer() != server || lock.roomModeActive {
            return .refused("the server changed while signing in; try again")
        }
        let at = now()
        let idleUntil = idleSec.map { at + $0 * 1000 } ?? idleMin.map { at + $0 * 60_000 }
        update { inner in
            inner.live = Live(server: server, token: token, idleMin: idleMin, fingerprint: fingerprint)
            inner.state = .signedIn(server: server, idleUntilMs: idleUntil, keptUntilMs: nil)
        }
        return .ok
    }

    // ---- keep admin today --------------------------------------------------

    /// Keep admin on this phone for the rest of the day: take the day's grant
    /// for this password session and keep it behind Face ID or Touch ID (the
    /// prompt shows). Needs a password sign-in from the last 5 minutes (the
    /// server's rule). A grant that couldn't be kept is revoked at once.
    public func keepToday(label: String) async -> Keep {
        guard let lk = locker, let shelf = grants, lk.available else { return .refused(Self.noBiometrics) }
        if liveForActive() == nil { return .refused(Self.signInFirst) }
        let dev = deviceID()
        guard (3...64).contains(dev.count) else { return .refused("this phone has no device id yet") }
        let body = Self.json(["device_id": dev, "label": String(label.prefix(Self.labelMax))])
        let issued: (live: Live, data: Data)
        do {
            issued = try await calling { [client] l in
                let data = try await client.raw(
                    method: "POST", path: "/api/auth/phone-grant", body: body,
                    headers: Self.bearer(l), onlyTo: l.server
                )
                return (l, data)
            }
        } catch {
            return .refused(Self.keepRefusal(error))
        }
        let l = issued.live
        let reply = Self.object(issued.data)
        guard let grant = Self.string(reply, "grant"), Self.isGrant(grant) else {
            return .refused("the server's answer made no sense")
        }
        let until = Self.zonedMs(Self.string(reply, "expires_at")) ?? (now() + Self.dayMs)
        let sealed: GrantSealed
        do {
            sealed = try await lk.seal(grant)
        } catch {
            await revoke(server: l.server, grant: grant)
            if case GrantLockerError.cancelled? = error as? GrantLockerError {
                return .refused("not kept: the Face ID or Touch ID check didn't finish")
            }
            return .refused((error as? GrantLockerError)?.message ?? Self.noBiometrics)
        }
        shelf.writeGrant(StoredGrant(
            server: l.server, fingerprint: l.fingerprint, deviceID: dev,
            expiresAtMs: until, sealed: sealed.bytes.base64EncodedString()
        ))
        update { inner in
            if inner.live == l {
                inner.live?.grant = grant
                inner.live?.keptUntilMs = until
            }
        }
        refresh()
        return .ok(untilMs: until)
    }

    /// Admin again from today's grant: Face ID or Touch ID opens it, and the
    /// server swaps it for a fresh session. Only to the server it came from,
    /// after that server proves the identity it proved then. Never throws.
    public func resume() async -> SignIn {
        if lock.roomModeActive { return .refused(Self.roomMode) }
        guard let g = storedForActive() else { return .refused("admin isn't kept on this phone today") }
        guard let lk = locker else { return .refused(Self.noBiometrics) }
        if now() >= g.expiresAtMs {
            dropGrant()
            return .refused(Self.dayOver)
        }
        if g.deviceID != deviceID() {
            dropGrant()
            return .refused("this phone's id changed; sign in with the password")
        }
        guard let base = TokenScope.baseOf(g.server) else { return .refused("no server configured") }
        let fingerprint: String
        do {
            fingerprint = try await prove(base)
        } catch {
            return .refused(Self.proofRefusal(error))
        }
        if fingerprint != g.fingerprint {
            dropGrant()
            return .refused(
                "this server proved a different identity than when admin was kept; sign in with the password"
            )
        }
        let unopened = "today's admin couldn't be opened; sign in with the password"
        guard let bytes = Data(base64Encoded: g.sealed) else {
            dropGrant()
            return .refused(unopened)
        }
        let grant: String
        do {
            grant = try await lk.open(GrantSealed(bytes: bytes))
        } catch {
            switch error as? GrantLockerError {
            case .keyGone(let why)?:
                dropGrant()
                return .refused(why.isEmpty ? Self.dayOver : why)
            case .cancelled?:
                return .refused("the Face ID or Touch ID check didn't finish")
            default:
                // A cancelled task is not a broken grant: nothing is dropped.
                if error is CancellationError { return .refused("the Face ID or Touch ID check didn't finish") }
                dropGrant()
                return .refused(unopened)
            }
        }
        guard Self.isGrant(grant) else {
            dropGrant()
            return .refused(unopened)
        }
        let data: Data
        do {
            data = try await client.raw(
                method: "POST", path: "/api/auth/phone-grant/exchange",
                body: Self.json(["grant": grant, "device_id": g.deviceID]),
                onlyTo: g.server
            )
        } catch {
            if case .refused(let f) = error, f.status == 401, !f.deviceTokenRequired {
                dropGrant()
                return .refused(
                    jsonDetail(of: f.body)?.contains("expired") == true
                        ? Self.dayOver
                        : "admin on this phone was ended (a revoke on the dashboard, or a password change); sign in with the password"
                )
            }
            return .refused(Self.signInRefusal(error, nonsense: "the server's answer made no sense"))
        }
        let reply = Self.object(data)
        guard let token = headerSafeToken(Self.string(reply, "token")) else {
            return .refused("the server's answer made no sense")
        }
        let hardEnd = Self.zonedMs(Self.string(reply, "grant_expires_at")) ?? g.expiresAtMs
        let idleMin = Self.long(reply, "idle_timeout_min")
        let idleSec = Self.long(reply, "idle_in_sec")
        if activeServer() != g.server || lock.roomModeActive {
            return .refused("the server changed while signing in; try again")
        }
        let at = now()
        let idleUntil = idleSec.map { at + $0 * 1000 } ?? idleMin.map { at + $0 * 60_000 }
        update { inner in
            inner.live = Live(
                server: g.server, token: token, idleMin: idleMin, fingerprint: fingerprint,
                grant: grant, hardEndMs: hardEnd, keptUntilMs: hardEnd
            )
            inner.state = .signedIn(
                server: g.server, idleUntilMs: idleUntil.map { min($0, hardEnd) } ?? hardEnd, keptUntilMs: hardEnd
            )
        }
        return .ok
    }

    // ---- admin calls -------------------------------------------------------

    /// Run one admin call on the active server with the session's headers
    /// (`Authorization: Bearer …`) and the server it must go to (pass it as
    /// `onlyTo`). A refusal that says the session is gone signs this phone
    /// out and is rethrown (its message is ``adminOnly``); a call that went
    /// through restarts the idle clock, as it did at the server.
    ///
    /// Throws ``NotSignedIn`` when there is no session for the active server
    /// or the server now proves a different identity, whatever `prove`
    /// throws when it cannot prove one, and whatever `block` throws.
    public func call<T>(
        _ block: @Sendable (_ headers: [String: String], _ server: String) async throws -> T
    ) async throws -> T {
        try await calling { l in try await block(Self.bearer(l), l.server) }
    }

    /// An admin action a screen asks for (design §1: "a tap after the hour
    /// but within the day asks for the biometric, then acts"). Android's
    /// `act` (`AdminSession.kt`, bd5c4b5e).
    ///
    /// With a live session, ``call(_:)``. With admin kept today and no
    /// session (the idle hour lapsed, the app restarted, or the server ended
    /// the session), Face ID first (``resume()``), then the call — and when
    /// that call says there is no session (``NotSignedIn``, or the server's
    /// own admin refusal: its idle hour), one more resume and one more call,
    /// never a loop. Without either, ``NotSignedIn``; a resume that didn't
    /// work throws ``AdminUnavailable`` with its reason.
    public func act<T>(
        _ block: @Sendable (_ headers: [String: String], _ server: String) async throws -> T
    ) async throws -> T {
        if liveForActive() == nil, storedForActive() != nil { try await resumeOrThrow() }
        do {
            return try await call(block)
        } catch {
            let sessionGone: Bool
            if error is NotSignedIn {
                sessionGone = true
            } else if let f = (error as? DomovoiError)?.failure, isAdminRefusal(status: f.status, body: f.body) {
                // The server ended this session (its idle hour); today's
                // grant may still be good for a new one.
                sessionGone = true
            } else {
                sessionGone = false
            }
            guard sessionGone, storedForActive() != nil else { throw error }
            try await resumeOrThrow()
            return try await call(block)
        }
    }

    /// ``act(_:)`` couldn't get a session: the reason, in plain words.
    /// Android's `AdminUnavailable`.
    public struct AdminUnavailable: Error, Equatable, Sendable {
        public let reason: String
        public init(_ reason: String) { self.reason = reason }
        public var message: String { reason }
    }

    private func resumeOrThrow() async throws {
        if case .refused(let why) = await resume() { throw AdminUnavailable(why) }
    }

    private func calling<T>(_ block: @Sendable (Live) async throws -> T) async throws -> T {
        guard let l = liveForActive() else {
            forget()
            throw NotSignedIn()
        }
        // The server proves itself again, and must be the one signed in to.
        guard let base = TokenScope.baseOf(l.server) else {
            forget()
            throw NotSignedIn()
        }
        let proved = try await prove(base)
        if proved != l.fingerprint || currentLive() != l {
            if currentLive() == l { forget() }
            throw NotSignedIn()
        }
        do {
            let result = try await block(l)
            let at = now()
            update { inner in
                guard let current = inner.live, current.server == l.server, current.token == l.token,
                      let idleMin = current.idleMin else { return }
                let idle = at + idleMin * 60_000
                inner.state = .signedIn(
                    server: current.server,
                    idleUntilMs: current.hardEndMs.map { min(idle, $0) } ?? idle,
                    keptUntilMs: current.keptUntilMs
                )
            }
            return result
        } catch {
            if let f = (error as? DomovoiError)?.failure, isAdminRefusal(status: f.status, body: f.body),
               currentLive() == l {
                forget()
            }
            throw error
        }
    }

    // ---- ending it ---------------------------------------------------------

    /// Sign out: forget the session and today's grant here at once, then end
    /// both at the server (best effort) under the same rules as every admin
    /// call (L56): only while the phone is not a room, only to the server
    /// that issued them while it is still the active one, and only after that
    /// server proves the identity the session is bound to. Otherwise they are
    /// just forgotten here (the Keychain item is deleted, so what was kept
    /// can't be opened again); the server ends them at their time.
    ///
    /// The server half runs in a task of its own that the caller awaits, so a
    /// screen that goes away mid-sign-out (its task cancelled) still ends the
    /// session at the server rather than cancelling the request.
    public func signOut() async {
        let l: Live? = update { inner in
            let was = inner.live
            inner.live = nil
            return was
        }
        dropGrant()
        guard let l else { return }
        if lock.roomModeActive { return }
        if l.server != activeServer() { return }
        guard let base = TokenScope.baseOf(l.server) else { return }
        let client = client
        let prove = prove
        let lock = lock
        await Task {
            let proved = try? await prove(base)
            guard proved == l.fingerprint, !lock.roomModeActive else { return }
            _ = try? await client.raw(
                method: "POST", path: "/api/auth/logout", headers: Self.bearer(l), onlyTo: l.server
            )
            if let grant = l.grant { await Self.revokeNow(client, server: l.server, grant: grant) }
        }.value
    }

    /// Drop the session here (the server already ended it, or the active
    /// server changed). Today's grant, if any, stays for ``resume()``.
    public func forget() {
        update { $0.live = nil }
        refresh()
    }

    /// Drop today's grant here: the stored copy and the Keychain item.
    public func dropGrant() {
        if grants?.readGrant() != nil { grants?.writeGrant(nil) }
        locker?.destroy()
        update { inner in
            inner.live?.grant = nil
            inner.live?.keptUntilMs = nil
        }
        refresh()
    }

    /// Recompute ``state`` for the active server (after a server switch).
    public func refresh() {
        let l = liveForActive()
        let stored = l == nil ? storedForActive() : nil
        let at = now()
        let roomMode = lock.roomModeActive
        update { inner in
            if let l {
                let idle: Int64?
                if case .signedIn(_, let until, _) = inner.state { idle = until } else { idle = nil }
                inner.state = .signedIn(server: l.server, idleUntilMs: idle, keptUntilMs: l.keptUntilMs)
            } else if let stored, at < stored.expiresAtMs, !roomMode {
                inner.state = .kept(server: stored.server, untilMs: stored.expiresAtMs)
            } else {
                inner.state = .signedOut
            }
        }
    }

    // ---- inside ------------------------------------------------------------

    private func activeServer() -> String { ServerCredentials.normalize(client.baseURL) }

    private func currentLive() -> Live? { inner.withLock { $0.live } }

    private func liveForActive() -> Live? {
        if lock.roomModeActive { return nil }
        guard let l = currentLive() else { return nil }
        if let end = l.hardEndMs, now() >= end { return nil }
        return l.server == activeServer() ? l : nil
    }

    private func storedForActive() -> StoredGrant? {
        guard let g = grants?.readGrant() else { return nil }
        return g.server == activeServer() ? g : nil
    }

    /// Change the state under the lock, then tell the sink outside it when
    /// what the app shows changed.
    @discardableResult
    private func update<R>(_ body: (inout Inner) -> R) -> R {
        let (result, changed, sink): (R, State?, (@Sendable (State) -> Void)?) = inner.withLock { inner in
            let before = inner.state
            let result = body(&inner)
            return (result, inner.state == before ? nil : inner.state, inner.onChange)
        }
        if let changed, let sink { sink(changed) }
        return result
    }

    /// A grant that couldn't be kept goes back at once (no credential needed
    /// but the grant; only to the server that just issued it). In a task of
    /// its own, so a cancelled caller still sends it.
    private func revoke(server: String, grant: String) async {
        let client = client
        await Task { await Self.revokeNow(client, server: server, grant: grant) }.value
    }

    private static func revokeNow(_ client: DomovoiClient, server: String, grant: String) async {
        _ = try? await client.raw(
            method: "POST", path: "/api/auth/phone-grant/revoke", body: json(["grant": grant]), onlyTo: server
        )
    }

    static func bearer(_ l: Live) -> [String: String] { ["Authorization": "Bearer \(l.token)"] }

    // ---- words ---------------------------------------------------------------

    /// The login's label limit (`LoginRequest.label`, max_length 120); the
    /// phone keeps it shorter (a grant's label is 1-60).
    public static let labelMax = 60

    /// Why there is no admin sign-in while the phone is a room.
    public static let roomMode = "this phone is a room; admin sign-in is off while it is"

    public static let noBiometrics = "keeping admin today needs Face ID or Touch ID set up on this phone"
    public static let dayOver = "today's admin on this phone has ended; sign in with the password"
    static let signInFirst = "sign in with the admin password first"

    static let dayMs: Int64 = 24 * 60 * 60 * 1000

    /// A refused sign-in in plain words. `signInRefusal` (`AdminSession.kt:458`),
    /// with the client's other failures (Android's catch clauses) folded in.
    static func signInRefusal(_ error: DomovoiError, nonsense: String) -> String {
        switch error {
        case .refused(let f), .rateLimited(let f, _), .serverTrouble(let f), .httpError(let f):
            let detail = jsonDetail(of: f.body).flatMap { $0.isEmpty ? nil : $0 }
            switch f.status {
            case 401: return "wrong admin password"
            case 409: return "this Domovoi has no admin yet; finish setting it up on the dashboard"
            case 429:
                if let detail { return detail }
                return tooManyTries(error)
            default: return detail ?? "sign-in failed (HTTP \(f.status))"
            }
        case .unreachable(let why) where why == DomovoiClient.serverChanged:
            return "the server changed while signing in; try again"
        case .unreachable, .noServer:
            return "couldn't reach the server"
        case .notDomovoi:
            return nonsense
        case .notPermitted(let why), .identityHeld(let why), .probeWiringCycle(let why):
            return why
        case .tokenThrottled:
            return error.message
        }
    }

    /// Why keeping admin today was refused (`AdminSession.kt:206-218`).
    static func keepRefusal(_ error: any Error) -> String {
        if error is NotSignedIn { return signInFirst }
        guard let failure = error as? DomovoiError else { return proofRefusal(error) }
        switch failure {
        case .refused(let f), .rateLimited(let f, _), .serverTrouble(let f), .httpError(let f):
            switch f.status {
            case 403:
                return "sign in with the password again first: keeping admin today needs a sign-in from the last 5 minutes"
            case 429:
                return tooManyTries(failure)
            default:
                let detail = jsonDetail(of: f.body).flatMap { $0.isEmpty ? nil : $0 }
                return detail ?? "the server didn't keep admin today (HTTP \(f.status))"
            }
        default:
            return signInRefusal(failure, nonsense: "the server's answer made no sense")
        }
    }

    /// Why the server's identity was not good enough: the admission's own
    /// words, or the gate's.
    static func proofRefusal(_ error: any Error) -> String {
        if let failure = error as? DomovoiError {
            let why = failure.message
            return why.isEmpty ? "the server did not prove it is your Domovoi" : why
        }
        return "the server did not prove it is your Domovoi"
    }

    private static func tooManyTries(_ error: DomovoiError) -> String {
        if case .rateLimited(_, let waitMs) = error, waitMs > 0 {
            return "too many tries; try again in \((waitMs + 999) / 1000) s"
        }
        return "too many tries; try again later"
    }

    // ---- the wire ------------------------------------------------------------

    static func isGrant(_ text: String) -> Bool {
        text.utf8.count == 64 && text.utf8.allSatisfy { (0x30...0x39).contains($0) || (0x61...0x66).contains($0) }
    }

    private static func json(_ fields: [String: String]) -> Data {
        (try? JSONEncoder().encode(fields)) ?? Data("{}".utf8)
    }

    private static func object(_ data: Data) -> [String: Any]? {
        (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    private static func string(_ object: [String: Any]?, _ key: String) -> String? {
        object?[key] as? String
    }

    /// A positive whole number, or nil: Kotlin's `longOrNull?.takeIf { it > 0 }`.
    private static func long(_ object: [String: Any]?, _ key: String) -> Int64? {
        guard let number = object?[key] as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let double = number.doubleValue
        guard double.rounded() == double, double > 0, double < 1e15 else { return nil }
        return number.int64Value
    }

    /// `parseInstantMs` (`AdminSession.kt:473`): an instant or an offset
    /// date-time, and nothing zoneless — the server's ends always carry one.
    static func zonedMs(_ iso: String?) -> Int64? {
        guard let iso else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        if let date = formatter.date(from: iso) { return WireNumber.epochMs(date) }
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: iso).map { WireNumber.epochMs($0) }
    }
}

/// Every ``AdminSession`` on this phone, so the things that end them all at
/// once (L57: a server switch, a pin change, entering room mode) reach each
/// without holding them alive. `AdminSessions` (`AdminSession.kt:494`).
///
/// A class with one ``shared`` registry rather than Android's `object`, so a
/// session can be given its own; the store hooks and the room-mode seal reach
/// ``shared``, which is the one every session the app makes is in.
public final class AdminSessions: Sendable {

    public static let shared = AdminSessions()

    private struct Entry {
        weak var session: AdminSession?
    }

    private let entries = Mutex<[Entry]>([])

    public init() {}

    func register(_ session: AdminSession) {
        entries.withLock { list in
            list.removeAll { $0.session == nil }
            list.append(Entry(session: session))
        }
    }

    /// The sessions alive now, taken under the lock and acted on outside it:
    /// forgetting re-enters the store (the active server) and the lock.
    private func alive() -> [AdminSession] {
        entries.withLock { list in
            list.removeAll { $0.session == nil }
            return list.compactMap(\.session)
        }
    }

    /// Forget every session. `dropGrants` also drops today's kept grant: a
    /// pin change (the identity it was bound to may be gone) and room mode
    /// (the household side is sealed; admin on the phone starts over after).
    /// A server switch keeps it, for its own server.
    public func forgetAll(dropGrants: Bool = false) {
        for session in alive() {
            session.forget()
            if dropGrants { session.dropGrant() }
        }
    }

    /// Room mode started on `lock`: every session that obeys that seal is
    /// forgotten, and its grant dropped. Called by
    /// ``HouseholdLock/setRoomModeActive(_:)``; a session on another lock (a
    /// test's) is not this seal's business.
    func roomModeEntered(_ lock: HouseholdLock) {
        for session in alive() where session.lock === lock {
            session.forget()
            session.dropGrant()
        }
    }

    /// The active server changed: each session's ``AdminSession/state`` is
    /// recomputed for it.
    public func refreshAll() {
        for session in alive() { session.refresh() }
    }

    /// What a store had before a write: the two things an admin session is
    /// bound to besides its token.
    public struct StoreMark: Equatable, Sendable {
        let server: String
        let pins: [String: ServerIdentity.Pin]

        public init(server: String, pins: [String: ServerIdentity.Pin]) {
            self.server = ServerCredentials.normalize(server)
            self.pins = pins
        }
    }

    /// The store's half of L57, called by every ``ShellStore`` write that
    /// can move either, after the write and **before** the store's own sinks
    /// (so nothing the shell does in answer to a switch can find the old
    /// Bearer): any change to the pins forgets every session and drops the
    /// kept grant (`Prefs.setPins`); a server switch, or the active server
    /// forgotten, forgets every session and keeps the grant for its own
    /// server (`Prefs.setServerUrl`, `Prefs.forgetActiveServer`).
    public func storeWrote(before: StoreMark, after: StoreMark) {
        if before.pins != after.pins {
            forgetAll(dropGrants: true)
        } else if before.server != after.server {
            forgetAll()
        }
    }
}

/// The admission the admin password asks of the identity gate: the server
/// proved, on this network and just now, the identity this phone pinned for
/// it. A legacy server (no identity to prove) and every other verdict refuse.
/// `IdentityAdmission` (`android/.../audiobooks/ServerRemove.kt:208`); the
/// iPhone has no "remove from server" yet, so this is its first user.
public enum IdentityAdmission {

    public static func verified(_ verdict: IdentityVerdict, _ base: ServerURL) throws(DomovoiError) {
        switch verdict {
        case .verified:
            return
        case .legacy:
            throw .identityHeld(
                "this server can't prove it is your Domovoi (it has no identity key), so the admin password "
                    + "isn't sent to it from the phone; use the dashboard"
            )
        case .mismatch:
            throw .identityHeld("\(base.host):\(base.port) is not the Domovoi this phone paired with; the password was not sent")
        case .unproven, .unavailable:
            throw .identityHeld("the server hasn't proved it is your Domovoi yet; try again in a moment")
        }
    }

    /// ``verified(_:_:)``, and the identity the server proved: what an admin
    /// session is bound to, so a server that later proves a different key
    /// never receives that session's Bearer.
    public static func fingerprint(_ verdict: IdentityVerdict, _ base: ServerURL) throws(DomovoiError) -> String {
        try verified(verdict, base)
        guard case .verified(let fingerprint, _) = verdict else {
            throw .identityHeld("the server hasn't proved it is your Domovoi yet; try again in a moment")
        }
        return fingerprint
    }

    /// The app's wiring for ``fingerprint(_:_:)``: ask `gate` (a probe if it
    /// must).
    public static func fingerprintOf(_ gate: IdentityGate) -> @Sendable (ServerURL) async throws -> String {
        { base in
            let verdict: IdentityVerdict
            do {
                verdict = try await gate.verdict(for: base)
            } catch {
                if let failure = error as? DomovoiError, case .probeWiringCycle = failure { throw failure }
                throw DomovoiError.unreachable("couldn't reach the server")
            }
            return try fingerprint(verdict, base)
        }
    }
}
