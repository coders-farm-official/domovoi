import Foundation
import Synchronization
import XCTest
@testable import DomovoiKit

/// Admin on this phone: the password signs in once, the Bearer lives in
/// memory only and goes only to the server that issued it, and a refusal that
/// says the session ended signs the phone out. Then the day's grant: kept
/// behind Face ID, bound to the server and the identity it proved, opened
/// only for that server after it proves that identity again.
///
/// Android's `AdminSessionTest`, case for case, on ``StubURLProtocol`` where
/// it has `MockWebServer`. Its one source-reading case (`Prefs` needs an
/// Android context) is replaced by real hook tests against both stores, which
/// run here. The real Keychain and `LAContext` are never touched: the locker
/// is ``FakeLocker``.
final class AdminSessionTests: XCTestCase {

    private let server = "http://192.168.1.20:6369"
    private let other = "http://192.168.1.30:6369"
    private let household = "household-abc"

    private static let grant = String(repeating: "0123456789abcdef", count: 4)
    private static let bearerA = "Bearer " + String(repeating: "a", count: 64)
    private static let bearerB = "Bearer " + String(repeating: "b", count: 64)
    private static let restartPath = "/api/satellites/kitchen/restart"

    private var active: Shared<String>!
    private var verdict: Shared<IdentityVerdict>!
    private var clock: Shared<Int64>!
    private var sessionLive: Shared<Bool>!
    private var freshSignIn: Shared<Bool>!
    private var grantExpired: Shared<Bool>!
    private var lock: HouseholdLock!
    private var client: DomovoiClient!
    private var locker: FakeLocker!
    private var shelf: InMemoryGrantShelf!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        active = Shared(server)
        verdict = Shared(.verified(fingerprint: "SHA256:test", pinnedNow: false))
        clock = Shared(1_000_000)
        sessionLive = Shared(true)
        freshSignIn = Shared(true)
        grantExpired = Shared(false)
        lock = HouseholdLock()
        locker = FakeLocker()
        shelf = InMemoryGrantShelf()
        let active = active!
        let household = household
        client = DomovoiClient(
            book: ServerBook(activeServer: { active.current }, tokenForServer: { _ in household }),
            gate: AdmitsAnyServer(),
            lock: lock,
            configuration: StubURLProtocol.session(timeout: 5)
        )
        installServer()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    /// The server: the login, the grant endpoints and one admin action.
    private func installServer() {
        let sessionLive = sessionLive!
        let freshSignIn = freshSignIn!
        let grantExpired = grantExpired!
        StubURLProtocol.dispatch { request in
            let path = request.url?.path ?? ""
            let body = Self.sentBody(path)
            switch path {
            case "/api/auth/login":
                switch body["password"] {
                case "right-password":
                    return .json(#"{"ok":true,"token":"\#(String(repeating: "a", count: 64))","idle_timeout_min":60,"idle_in_sec":3600}"#)
                case "slow-down":
                    return .json(#"{"detail":"too many failed attempts — retry in 30s"}"#, status: 429)
                default:
                    return .json(#"{"detail":"wrong password"}"#, status: 401)
                }
            case "/api/auth/logout":
                return .json(#"{"ok":true,"revoked":true}"#)
            case "/api/auth/phone-grant":
                if request.value(forHTTPHeaderField: "Authorization") != Self.bearerA {
                    return .json(#"{"detail":"admin session required"}"#, status: 401)
                }
                if !freshSignIn.current {
                    return .json(#"{"detail":"sign in with the password again"}"#, status: 403)
                }
                return .json(#"{"grant":"\#(Self.grant)","expires_at":"2026-11-25T10:00:00Z"}"#)
            case "/api/auth/phone-grant/exchange":
                if grantExpired.current { return .json(#"{"detail":"phone grant has expired"}"#, status: 401) }
                guard body["grant"] == Self.grant, body["device_id"] == "ios-test" else {
                    return .json(#"{"detail":"phone grant not valid"}"#, status: 401)
                }
                return .json(
                    #"{"token":"\#(String(repeating: "b", count: 64))","grant_expires_at":"2026-11-25T10:00:00Z","idle_timeout_min":60,"idle_until":"x","idle_in_sec":3600}"#
                )
            case "/api/auth/phone-grant/revoke":
                return .json(#"{"ok":true}"#)
            case Self.restartPath:
                let auth = request.value(forHTTPHeaderField: "Authorization")
                if sessionLive.current, auth == Self.bearerA || auth == Self.bearerB {
                    return .json(#"{"ok":true}"#)
                }
                return .json(#"{"detail":"admin session required"}"#, status: 401)
            default:
                return .json(#"{"detail":"Not Found"}"#, status: 404)
            }
        }
    }

    /// The JSON body the phone sent to `path` last, as the recording kept it
    /// (a dispatcher is handed the body as a stream the recording has read).
    private static func sentBody(_ path: String) -> [String: String] {
        guard let data = StubURLProtocol.recorded.last(where: { $0.url?.path == path })?.httpBody,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return object.compactMapValues { $0 as? String }
    }

    private func sent(_ path: String) -> [URLRequest] {
        StubURLProtocol.recorded.filter { $0.url?.path == path }
    }

    private func prove() -> @Sendable (ServerURL) async throws -> String {
        let verdict = verdict!
        return { base in try IdentityAdmission.fingerprint(verdict.current, base) }
    }

    private func session(registry: AdminSessions = .shared) -> AdminSession {
        let clock = clock!
        return AdminSession(client: client, prove: prove(), now: { clock.current }, registry: registry)
    }

    private func keeping(client: DomovoiClient? = nil) -> AdminSession {
        let clock = clock!
        return AdminSession(
            client: client ?? self.client, prove: prove(), now: { clock.current },
            grants: shelf, locker: locker, deviceID: { "ios-test" }
        )
    }

    @discardableResult
    private func restart(_ s: AdminSession) async throws -> Data {
        let client = s.client
        return try await s.call { headers, server in
            try await client.raw(method: "POST", path: Self.restartPath, headers: headers, onlyTo: server)
        }
    }

    private func signIn(_ s: AdminSession, _ password: String = "right-password", label: String = "phone") async -> AdminSession.SignIn {
        await s.signIn(password: password, label: label)
    }

    private static func ms(_ iso: String) -> Int64 {
        WireNumber.epochMs(ISO8601DateFormatter().date(from: iso)!)
    }

    // MARK: - the password

    func testThePasswordSignsInAndTheBearerGoesOnAdminCalls() async throws {
        let s = session()
        let out = await signIn(s, label: "phone: iPhone")
        XCTAssertEqual(.ok, out)
        XCTAssertTrue(s.signedIn)
        XCTAssertEqual(.signedIn(server: server, idleUntilMs: clock.current + 3_600_000, keptUntilMs: nil), s.state)
        try await restart(s)
        XCTAssertEqual("phone: iPhone", Self.sentBody("/api/auth/login")["label"])
        XCTAssertEqual(Self.bearerA, sent(Self.restartPath).last?.value(forHTTPHeaderField: "Authorization"))
        // Nothing but admin calls carries it.
        XCTAssertNil(sent("/api/auth/login").first?.value(forHTTPHeaderField: "Authorization"))
    }

    func testAWrongPasswordAndAThrottleSayWhy() async {
        let s = session()
        let wrong = await signIn(s, "nope")
        XCTAssertEqual(.refused("wrong admin password"), wrong)
        let slow = await signIn(s, "slow-down")
        XCTAssertEqual(.refused("too many failed attempts — retry in 30s"), slow)
        XCTAssertFalse(s.signedIn)
    }

    func testThePasswordGoesOnlyToAServerThatProvedItself() async {
        verdict.set(.legacy(offered: nil))
        let out = await signIn(session())
        guard case .refused = out else { return XCTFail("\(out)") }
        XCTAssertTrue(sent("/api/auth/login").isEmpty)
    }

    func testASessionTheServerEndedSignsThePhoneOut() async {
        let s = session()
        _ = await signIn(s)
        sessionLive.set(false)
        do {
            try await restart(s)
            XCTFail("the refusal should be thrown")
        } catch {
            XCTAssertEqual(adminOnly, (error as? DomovoiError)?.message)
        }
        XCTAssertFalse(s.signedIn)
        XCTAssertEqual(.signedOut, s.state)
    }

    func testAnotherServerNeverGetsTheBearer() async {
        let s = session()
        _ = await signIn(s)
        active.set(other)
        XCTAssertFalse(s.signedIn)
        do {
            try await restart(s)
            XCTFail("no admin session for this server")
        } catch {
            XCTAssertEqual(AdminSession.NotSignedIn(), error as? AdminSession.NotSignedIn)
            XCTAssertEqual(adminOnly, AdminSession.NotSignedIn().message)
        }
        XCTAssertEqual(1, sent("/api/auth/login").count)
        XCTAssertTrue(sent(Self.restartPath).isEmpty)
    }

    // MARK: - security review 2026-11-22, L57

    func testAServerThatProvesAnotherKeyNeverGetsTheBearer() async {
        let s = session()
        _ = await signIn(s)
        // A re-pin, or another machine answering at the same address.
        verdict.set(.verified(fingerprint: "SHA256:other", pinnedNow: true))
        do {
            try await restart(s)
            XCTFail("no admin session for this identity")
        } catch {
            XCTAssertTrue(error is AdminSession.NotSignedIn, "\(error)")
        }
        XCTAssertFalse(s.signedIn)
        XCTAssertTrue(sent(Self.restartPath).isEmpty)
    }

    func testAServerThatCannotProveItselfNowGetsNothing() async {
        let s = session()
        _ = await signIn(s)
        verdict.set(.legacy(offered: nil))
        do {
            try await restart(s)
            XCTFail("the identity check should refuse")
        } catch {
            // Refused before anything was sent.
            guard case .identityHeld? = error as? DomovoiError else { return XCTFail("\(error)") }
        }
        XCTAssertTrue(sent(Self.restartPath).isEmpty)
    }

    func testRoomModeTurnsAdminOff() async {
        let s = session()
        _ = await signIn(s)
        lock.setRoomModeActive(true)
        XCTAssertFalse(s.signedIn)
        do {
            try await restart(s)
            XCTFail("no admin calls from a room")
        } catch {
            XCTAssertTrue(error is AdminSession.NotSignedIn, "\(error)")
        }
        let again = await signIn(s)
        XCTAssertEqual(.refused(AdminSession.roomMode), again)
        lock.setRoomModeActive(false)
        XCTAssertFalse(s.signedIn, "forgotten, not just hidden")
    }

    func testABearerNeverGoesToAnAbsoluteUrl() async {
        let s = session()
        _ = await signIn(s)
        let client = client!
        do {
            _ = try await s.call { headers, server in
                try await client.raw(method: "POST", path: "\(self.other)/api/x", headers: headers, onlyTo: server)
            }
            XCTFail("an absolute URL with the Bearer is refused")
        } catch {
            XCTAssertEqual(.notPermitted("that is not a server address"), error as? DomovoiError)
        }
        XCTAssertTrue(sent("/api/x").isEmpty)
    }

    func testSigningOutEndsTheSessionAtTheServer() async {
        let s = session()
        _ = await signIn(s)
        await s.signOut()
        XCTAssertFalse(s.signedIn)
        XCTAssertEqual(Self.bearerA, sent("/api/auth/logout").last?.value(forHTTPHeaderField: "Authorization"))
    }

    // MARK: - security review 2026-11-23, L56: sign-out proves the server first

    func testSignOutToAServerThatNowProvesAnotherKeySendsNothing() async {
        let s = session()
        _ = await signIn(s)
        verdict.set(.verified(fingerprint: "SHA256:other", pinnedNow: true))
        await s.signOut()
        XCTAssertFalse(s.signedIn)
        XCTAssertTrue(sent("/api/auth/logout").isEmpty, "the Bearer never went out")
    }

    func testSignOutToAServerThatCannotProveItselfSendsNothing() async {
        let s = session()
        _ = await signIn(s)
        verdict.set(.legacy(offered: nil))
        await s.signOut()
        XCTAssertFalse(s.signedIn)
        XCTAssertTrue(sent("/api/auth/logout").isEmpty)
    }

    func testSignOutAfterTheServerChangedJustForgets() async {
        let s = session()
        _ = await signIn(s)
        active.set(other)
        await s.signOut()
        active.set(server)
        XCTAssertFalse(s.signedIn)
        XCTAssertTrue(sent("/api/auth/logout").isEmpty)
    }

    func testSignOutInRoomModeJustForgets() async {
        let s = session()
        _ = await signIn(s)
        lock.setRoomModeActive(true)
        await s.signOut()
        lock.setRoomModeActive(false)
        XCTAssertTrue(sent("/api/auth/logout").isEmpty)
    }

    // MARK: - security review 2026-11-23, L57: forgotten at once

    func testEnteringRoomModeForgetsAtOnceNotAtTheNextCall() async {
        let s = session()
        _ = await signIn(s)
        lock.setRoomModeActive(true)
        lock.setRoomModeActive(false)
        XCTAssertEqual(.signedOut, s.state)
        XCTAssertFalse(s.signedIn, "forgotten while no call was made")
        XCTAssertTrue(sent(Self.restartPath).isEmpty)
    }

    /// The seal reaches the sessions that obey it, and no others.
    func testRoomModeOnAnotherLockLeavesThisSessionAlone() async {
        let s = session()
        _ = await signIn(s)
        HouseholdLock().setRoomModeActive(true)
        XCTAssertTrue(s.signedIn)
    }

    func testForgetAllEndsEverySessionOnThePhone() async {
        let registry = AdminSessions()
        let a = session(registry: registry)
        let b = session(registry: registry)
        _ = await signIn(a)
        _ = await signIn(b)
        registry.forgetAll()
        XCTAssertFalse(a.signedIn)
        XCTAssertFalse(b.signedIn)
        XCTAssertEqual(.signedOut, a.state)
        XCTAssertEqual(.signedOut, b.state)
    }

    // MARK: - the stores' hooks (Android holds Prefs to its source; these run)

    /// A session on a client that reads its server from `store`, signed in
    /// and keeping admin today.
    private func keptOn(_ store: any ShellStore) async -> AdminSession {
        let household = household
        let storeClient = DomovoiClient(
            book: ServerBook(activeServer: { store.serverURL }, tokenForServer: { _ in household }),
            gate: AdmitsAnyServer(),
            lock: lock,
            configuration: StubURLProtocol.session(timeout: 5)
        )
        let s = keeping(client: storeClient)
        _ = await signIn(s)
        let kept = await s.keepToday(label: "phone")
        XCTAssertEqual(.ok(untilMs: Self.ms("2026-11-25T10:00:00Z")), kept)
        return s
    }

    private func stores() -> [(String, any ShellStore)] {
        let memory = InMemoryShellStore(serverURL: server, token: household)
        // Rows for both, or a switch drops the other's trust as an orphan.
        memory.upsertKnownServer(url: server, name: nil)
        memory.trustServer(other, identity: nil)
        memory.upsertKnownServer(url: other, name: nil)
        let saved = SavedShellStore(place: SavedStateBox(), vault: TokenVault(items: InMemoryKeychain()))
        saved.trustServer(server, identity: TestIdentity.real.pin)
        saved.upsertKnownServer(url: server, name: nil)
        XCTAssertTrue(saved.setServerURL(server))
        saved.trustServer(other, identity: nil)
        saved.upsertKnownServer(url: other, name: nil)
        return [("in memory", memory), ("saved", saved)]
    }

    func testAServerSwitchForgetsAtOnceAndKeepsTheGrantForItsServer() async {
        for (name, store) in stores() {
            shelf.writeGrant(nil)
            let s = await keptOn(store)
            XCTAssertTrue(store.setServerURL(other), name)
            XCTAssertTrue(store.setServerURL(server), name)
            XCTAssertFalse(s.signedIn, "\(name): forgotten, not just hidden")
            XCTAssertNotNil(shelf.readGrant(), "\(name): kept for its own server")
            guard case .kept = s.state else { return XCTFail("\(name): \(s.state)") }
        }
    }

    func testForgettingTheActiveServerForgetsAdmin() async {
        for (name, store) in stores() {
            let s = await keptOn(store)
            XCTAssertEqual(server, store.forgetActiveServer(), name)
            XCTAssertFalse(s.signedIn, name)
            XCTAssertEqual(.signedOut, s.state, name)
        }
    }

    func testAPinChangeForgetsAdminAndDropsTheGrant() async {
        for (name, store) in stores() {
            let s = await keptOn(store)
            let destroyed = locker.destroyed
            store.replacePin(url: server, pin: TestIdentity.rogue.pin)
            XCTAssertFalse(s.signedIn, name)
            XCTAssertNil(shelf.readGrant(), name)
            XCTAssertGreaterThan(locker.destroyed, destroyed, name)
            XCTAssertEqual(.signedOut, s.state, name)
        }
    }

    /// Trusting another server with an identity is a pin change too.
    func testAnyNewPinForgetsAdmin() async {
        for (name, store) in stores() {
            let s = await keptOn(store)
            store.trustServer("http://192.168.1.41:6369", identity: TestIdentity.rogue.pin)
            XCTAssertFalse(s.signedIn, name)
            XCTAssertNil(shelf.readGrant(), name)
        }
    }

    func testAWriteThatMovesNeitherLeavesAdminAlone() async {
        for (name, store) in stores() {
            shelf.writeGrant(nil)
            let s = await keptOn(store)
            store.upsertKnownServer(url: server, name: "Home")
            store.setPreferLocal(true)
            XCTAssertTrue(store.setServerURL(server), name)
            XCTAssertTrue(s.signedIn, name)
        }
    }

    // MARK: - keep admin today: the day's grant

    func testKeepTodaySealsTheGrantBoundToThisServerAndIdentity() async throws {
        let s = keeping()
        _ = await signIn(s, label: "phone: iPhone")
        let kept = await s.keepToday(label: "phone: iPhone")
        guard case .ok = kept else { return XCTFail("\(kept)") }
        let g = try XCTUnwrap(shelf.readGrant())
        XCTAssertEqual(server, g.server)
        XCTAssertEqual("SHA256:test", g.fingerprint)
        XCTAssertEqual("ios-test", g.deviceID)
        XCTAssertEqual(Self.ms("2026-11-25T10:00:00Z"), g.expiresAtMs)
        let sealed = try XCTUnwrap(Data(base64Encoded: g.sealed))
        XCTAssertFalse(g.sealed.contains(Self.grant), "never stored in the clear")
        XCTAssertNotEqual(Self.grant, String(decoding: sealed, as: UTF8.self))
        XCTAssertEqual("ios-test", Self.sentBody("/api/auth/phone-grant")["device_id"])
        XCTAssertEqual("phone: iPhone", Self.sentBody("/api/auth/phone-grant")["label"])
        // Both credentials: the Bearer and the household token.
        let asked = try XCTUnwrap(sent("/api/auth/phone-grant").last)
        XCTAssertEqual(Self.bearerA, asked.value(forHTTPHeaderField: "Authorization"))
        XCTAssertEqual(household, asked.value(forHTTPHeaderField: deviceTokenHeader))
        guard case .signedIn(_, _, let keptUntil) = s.state else { return XCTFail("\(s.state)") }
        XCTAssertEqual(g.expiresAtMs, keptUntil)
    }

    func testKeepTodayNeedsAFreshPasswordSignIn() async {
        let s = keeping()
        _ = await signIn(s)
        freshSignIn.set(false)
        let kept = await s.keepToday(label: "phone")
        guard case .refused(let why) = kept else { return XCTFail("\(kept)") }
        XCTAssertTrue(why.contains("sign in with the password again"), why)
        XCTAssertNil(shelf.readGrant())
    }

    func testKeepTodayWithoutBiometricsOrASessionSaysSo() async {
        let none = await session().keepToday(label: "phone")
        XCTAssertEqual(.refused(AdminSession.noBiometrics), none)
        let s = keeping()
        let first = await s.keepToday(label: "phone")
        XCTAssertEqual(.refused("sign in with the admin password first"), first)
        XCTAssertTrue(sent("/api/auth/phone-grant").isEmpty)
    }

    func testAGrantTheFaceIDCheckDidntSealIsRevokedAtOnce() async {
        let s = keeping()
        _ = await signIn(s)
        locker.ok = false
        let kept = await s.keepToday(label: "phone")
        guard case .refused = kept else { return XCTFail("\(kept)") }
        XCTAssertNil(shelf.readGrant())
        XCTAssertEqual(Self.grant, Self.sentBody("/api/auth/phone-grant/revoke")["grant"])
    }

    func testAfterTheIdleHourFaceIDSwapsTheGrantForASession() async throws {
        let s = keeping()
        _ = await signIn(s)
        _ = await s.keepToday(label: "phone")
        s.forget() // the idle hour lapsed, or the app restarted
        guard case .kept = s.state else { return XCTFail("\(s.state)") }
        let resumed = await s.resume()
        XCTAssertEqual(.ok, resumed)
        XCTAssertEqual(Self.grant, Self.sentBody("/api/auth/phone-grant/exchange")["grant"])
        XCTAssertEqual("ios-test", Self.sentBody("/api/auth/phone-grant/exchange")["device_id"])
        XCTAssertNil(sent("/api/auth/phone-grant/exchange").last?.value(forHTTPHeaderField: "Authorization"))
        try await restart(s)
        XCTAssertEqual(Self.bearerB, sent(Self.restartPath).last?.value(forHTTPHeaderField: "Authorization"))
    }

    func testTheGrantGoesOnlyToTheServerThatProvesTheSameIdentity() async {
        let s = keeping()
        _ = await signIn(s)
        _ = await s.keepToday(label: "phone")
        s.forget()
        verdict.set(.verified(fingerprint: "SHA256:other", pinnedNow: true))
        let resumed = await s.resume()
        guard case .refused = resumed else { return XCTFail("\(resumed)") }
        XCTAssertNil(shelf.readGrant(), "dropped")
        XCTAssertEqual(0, locker.opens)
        XCTAssertTrue(sent("/api/auth/phone-grant/exchange").isEmpty)
    }

    func testAnotherServerDoesntSeeTheKeptGrant() async {
        let s = keeping()
        _ = await signIn(s)
        _ = await s.keepToday(label: "phone")
        active.set(other)
        s.refresh()
        XCTAssertEqual(.signedOut, s.state)
        let resumed = await s.resume()
        guard case .refused = resumed else { return XCTFail("\(resumed)") }
        active.set(server)
        XCTAssertNotNil(shelf.readGrant(), "kept for its own server")
    }

    func testNewBiometricsOrAnEndedGrantDropIt() async {
        let s = keeping()
        _ = await signIn(s)
        _ = await s.keepToday(label: "phone")
        s.forget()
        locker.gone = true
        let gone = await s.resume()
        guard case .refused = gone else { return XCTFail("\(gone)") }
        XCTAssertNil(shelf.readGrant())
        // A revoked or expired grant at the server: dropped too.
        locker.gone = false
        _ = await signIn(s)
        _ = await s.keepToday(label: "phone")
        s.forget()
        grantExpired.set(true)
        let expired = await s.resume()
        XCTAssertEqual(.refused(AdminSession.dayOver), expired)
        XCTAssertNil(shelf.readGrant())
    }

    func testACancelledFaceIDCheckKeepsTheGrant() async {
        let s = keeping()
        _ = await signIn(s)
        _ = await s.keepToday(label: "phone")
        s.forget()
        locker.ok = false
        let resumed = await s.resume()
        guard case .refused = resumed else { return XCTFail("\(resumed)") }
        XCTAssertNotNil(shelf.readGrant(), "a cancel changes nothing")
        XCTAssertTrue(sent("/api/auth/phone-grant/exchange").isEmpty)
    }

    func testPastTheDayTheGrantMadeSessionIsNotUsed() async {
        let s = keeping()
        _ = await signIn(s)
        _ = await s.keepToday(label: "phone")
        s.forget()
        _ = await s.resume()
        XCTAssertTrue(s.signedIn)
        clock.set(Self.ms("2026-11-25T10:00:01Z"))
        XCTAssertFalse(s.signedIn)
    }

    func testSignOutRevokesTheGrantAndDeletesTheKey() async {
        let s = keeping()
        _ = await signIn(s)
        _ = await s.keepToday(label: "phone")
        await s.signOut()
        XCTAssertNil(shelf.readGrant())
        XCTAssertGreaterThan(locker.destroyed, 0)
        XCTAssertEqual(Self.grant, Self.sentBody("/api/auth/phone-grant/revoke")["grant"])
        XCTAssertEqual(.signedOut, s.state)
    }

    func testRoomModeDropsTheKeptGrant() async {
        let s = keeping()
        _ = await signIn(s)
        _ = await s.keepToday(label: "phone")
        lock.setRoomModeActive(true)
        lock.setRoomModeActive(false)
        XCTAssertNil(shelf.readGrant())
        XCTAssertEqual(.signedOut, s.state)
    }

    // MARK: - the pieces

    func testTheSavedShelfKeepsTheHandleAndNeverTheGrant() throws {
        let place = SavedStateBox()
        let shelf = SavedGrantShelf(place: place)
        XCTAssertNil(shelf.readGrant())
        let g = StoredGrant(server: server, fingerprint: "SHA256:test", deviceID: "ios-test", expiresAtMs: 42, sealed: "aGFuZGxl")
        shelf.writeGrant(g)
        XCTAssertEqual(g, SavedGrantShelf(place: place).readGrant(), "it comes back after a launch")
        shelf.writeGrant(nil)
        XCTAssertNil(SavedGrantShelf(place: place).readGrant())
    }

    /// What a Keychain read's status means after a Face ID check that passed.
    func testTheKeychainLockerReadsStatusesAsCancelOrGone() {
        XCTAssertNil(KeychainGrantLocker.failure(errSecSuccess))
        guard case .cancelled? = KeychainGrantLocker.failure(errSecUserCanceled) else { return XCTFail() }
        guard case .cancelled? = KeychainGrantLocker.failure(errSecInteractionNotAllowed) else { return XCTFail() }
        XCTAssertEqual(.keyGone(GrantLockerError.keyGoneMessage), KeychainGrantLocker.failure(errSecItemNotFound))
        XCTAssertEqual(.keyGone(GrantLockerError.keyGoneMessage), KeychainGrantLocker.failure(errSecAuthFailed))
    }

    func testTheContainerHasOneSessionOnItsOwnClient() {
        let container = AppContainer(
            store: InMemoryShellStore(serverURL: server, token: household),
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session(timeout: 1)
        )
        XCTAssertTrue(container.admin.client === container.client)
        XCTAssertEqual(.signedOut, container.admin.state)
        XCTAssertFalse(container.admin.canKeepToday)
    }

    func testTheWireRules() {
        XCTAssertTrue(AdminSession.isGrant(Self.grant))
        XCTAssertFalse(AdminSession.isGrant(Self.grant.uppercased()))
        XCTAssertFalse(AdminSession.isGrant(String(Self.grant.dropLast())))
        XCTAssertEqual(Self.ms("2026-11-25T10:00:00Z"), AdminSession.zonedMs("2026-11-25T11:00:00+01:00"))
        XCTAssertEqual(Self.ms("2026-11-25T10:00:00Z") + 500, AdminSession.zonedMs("2026-11-25T10:00:00.500Z"))
        XCTAssertNil(AdminSession.zonedMs("2026-11-25T10:00:00"), "no zone, no guess")
        XCTAssertEqual("Live(\(server))", "\(AdminSession.Live(server: server, token: "secret", idleMin: nil, fingerprint: "f"))")
    }
}

/// The phone's biometric lock, faked: sealing scrambles, and the test says
/// whether the person passes the check.
private final class FakeLocker: GrantLocker, Sendable {

    private struct State {
        var ok = true
        var gone = false
        var destroyed = 0
        var opens = 0
    }

    private let state = Mutex(State())

    var ok: Bool {
        get { state.withLock { $0.ok } }
        set { state.withLock { $0.ok = newValue } }
    }

    var gone: Bool {
        get { state.withLock { $0.gone } }
        set { state.withLock { $0.gone = newValue } }
    }

    var destroyed: Int { state.withLock { $0.destroyed } }
    var opens: Int { state.withLock { $0.opens } }

    var available: Bool { true }

    func seal(_ grant: String) async throws -> GrantSealed {
        guard ok else { throw GrantLockerError.cancelled("cancelled") }
        return GrantSealed(bytes: Data(grant.utf8.map { $0 ^ 0x5a }))
    }

    func open(_ sealed: GrantSealed) async throws -> String {
        let (gone, ok) = state.withLock { s -> (Bool, Bool) in
            s.opens += 1
            return (s.gone, s.ok)
        }
        if gone { throw GrantLockerError.keyGone(GrantLockerError.keyGoneMessage) }
        guard ok else { throw GrantLockerError.cancelled("cancelled") }
        return String(decoding: sealed.bytes.map { $0 ^ 0x5a }, as: UTF8.self)
    }

    func destroy() {
        state.withLock { $0.destroyed += 1 }
    }
}
