import Foundation
import XCTest
@testable import DomovoiKit

/// The Domovoi server as admin on this phone (1.5.0): Android's
/// `ServerUpdateTest`, case for case, then the model against a stub house.
final class ServerUpdateTests: XCTestCase {

    private let server = "http://192.168.1.20:6369"
    private static let bearer = "Bearer " + String(repeating: "a", count: 64)

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private func v(_ json: String) -> ServerVersion { ServerVersion(data: Data(json.utf8))! }

    func testTheCardIsThereOnlyWithAdminAndNeverOnASharedScreen() {
        let signedIn = AdminSession.State.signedIn(server: server, idleUntilMs: nil, keptUntilMs: nil)
        let kept = AdminSession.State.kept(server: server, untilMs: 1)
        XCTAssertTrue(serverAdminHeld(signedIn, sharedScreen: false))
        XCTAssertTrue(serverAdminHeld(kept, sharedScreen: false))
        XCTAssertFalse(serverAdminHeld(signedIn, sharedScreen: true))
        XCTAssertFalse(serverAdminHeld(.signedOut, sharedScreen: false))
        XCTAssertTrue(ServerUpdateWords.buildLineOrPhone.hasPrefix(String(SystemSettingsStrings.serverBuildLine.dropLast())))
        XCTAssertTrue(ServerUpdateWords.buildLineOrPhone.hasSuffix(", or sign in as admin on this phone."))
        XCTAssertTrue(AdminStrings.jobs.contains("install an update or restart the Domovoi server (Settings → About & updates)"))
    }

    func testTheVersionIsReadAsTheDashboardReadsIt() {
        let x = v("""
            {"version":"1.5.0","sha":"aaaa111","running_sha":"aaaa111","checkout_sha":"bbbb222","started_at":1700000000,
             "restart_required":true,"code_restart_required":true,"restart_capable":true,"restart_mode":"update",
             "last_update":{"status":"ok","started_at":"2026-11-20T10:00:00Z","to_sha":"aaaa111"},
             "plugins_pending_restart":[{"slug":"radio","from_version":"1.1.0","to_version":"1.2.0"}],
             "windows_side":{"present":true,"kind":"windows"}}
            """)
        XCTAssertEqual("1.5.0", x.version)
        XCTAssertEqual("1700000000", x.startedAt)
        XCTAssertTrue(x.codePending && x.updateUnit && x.restartCapable)
        XCTAssertEqual(["radio 1.1.0 → 1.2.0"], x.pluginsPending)
        XCTAssertEqual("windows", x.hostSide)
        XCTAssertNil(v(#"{"windows_side":{"present":true,"kind":"mac"}}"#).hostSide)
        XCTAssertEqual("mac", v(#"{"windows_side":{"present":true,"kind":"mac","updates":true}}"#).hostSide)
        XCTAssertNil(v(#"{"windows_side":{"present":false}}"#).hostSide)
        XCTAssertTrue(v(#"{"restart_required":true}"#).codePending)
        XCTAssertNil(ServerVersion(data: Data("[]".utf8)))
    }

    func testTheModeFollowsTheDashboardsOrder() {
        let idle = v(#"{"restart_capable":true}"#)
        XCTAssertEqual(.check, serverMode(idle, check: nil, following: false))
        XCTAssertEqual(.busy, serverMode(idle, check: nil, following: true))
        XCTAssertEqual(.busy, serverMode(v(#"{"restart_in_progress":true,"restart_required":true}"#), check: nil, following: false))
        XCTAssertEqual(.restart, serverMode(v(#"{"restart_required":true}"#), check: nil, following: false))
        var behind = UpdateCheck()
        behind.upstream = true
        behind.behind = 3
        behind.upstreamSha = "cccc333"
        XCTAssertEqual(.pull, serverMode(idle, check: behind, following: false))
        XCTAssertEqual(.held, serverMode(v(#"{"bad_sha":"cccc333"}"#), check: behind, following: false))
        XCTAssertTrue(serverOnePress(v(#"{"restart_mode":"update","restart_capable":true}"#)))
        XCTAssertFalse(serverOnePress(v(#"{"restart_mode":"restart","restart_capable":true}"#)))
        XCTAssertFalse(serverOnePress(v(#"{"restart_mode":"update","restart_capable":true,"windows_side":{"present":true}}"#)))
    }

    func testTheConfirmsAreTheDashboards() {
        let split = ServerUpdateWords.split(ServerUpdateWords.onePressConfirm)
        XCTAssertEqual("Update Domovoi?", split.title)
        XCTAssertTrue(split.body.hasPrefix("Domovoi pulls the latest code, then backs up, applies it and restarts"))
        XCTAssertTrue(ServerUpdateWords.pullConfirm.contains("but does not start it yet"))
        let plain = v(#"{"restart_mode":"restart"}"#)
        XCTAssertTrue(ServerUpdateWords.restartConfirm(plain, plain: true).hasPrefix("Restart Domovoi now?\n\ndomovoi-core and domovoi-web restart"))
        XCTAssertTrue(ServerUpdateWords.restartConfirm(plain, plain: false).hasPrefix("Restart the Domovoi services to load the pulled code?\n\n"))
        XCTAssertTrue(ServerUpdateWords.restartConfirm(v(#"{"restart_mode":"update"}"#), plain: false).hasPrefix("Apply the pulled code?\n\nThis backs up the database"))
        let pulled = v(#"{"restart_mode":"update","restart_required":true,"sha":"aaaa111","checkout_sha":"bbbb222"}"#)
        XCTAssertEqual("Pulled code is waiting to load (running aaaa111, checked out bbbb222)", serverRestartRunsFullUpdate(pulled))
        XCTAssertTrue(ServerUpdateWords.restartConfirm(pulled, plain: true).hasPrefix("Restart Domovoi now, as a full update?\n\nPulled code is waiting"))
        let moved = v(#"{"restart_mode":"update","checkout_sha":"bbbb222","last_update":{"status":"ok","to_sha":"aaaa1112"}}"#)
        XCTAssertEqual("The checkout (bbbb222) isn’t the commit the update unit last applied (aaaa111)", serverRestartRunsFullUpdate(moved))
        XCTAssertNil(serverRestartRunsFullUpdate(v(#"{"restart_mode":"update","checkout_sha":"aaaa111","last_update":{"status":"ok","to_sha":"aaaa1112"}}"#)))
    }

    /// The words are the Kotlin's: read from `ServerUpdate.kt` itself.
    func testTheWordsAreTheKotlins() throws {
        var root = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { root = root.deletingLastPathComponent() }
        let kotlin = root.appendingPathComponent("android/app/src/main/java/com/domovoi/app/ui/screens/settings/ServerUpdate.kt")
        guard let source = try? String(contentsOf: kotlin, encoding: .utf8) else {
            throw XCTSkip("the Android source isn't next to this package")
        }
        for words in [
            ServerUpdateWords.title, ServerUpdateWords.sub, ServerUpdateWords.buildLineOrPhone, ServerUpdateWords.check,
            ServerUpdateWords.update, ServerUpdateWords.restartApply, ServerUpdateWords.restart,
        ] {
            XCTAssertTrue(source.contains("\"\(words)\""), words)
        }
    }

    func testTheWordsAreTheDashboards() {
        func check(_ upstream: Bool, _ channel: String?, behind: Int? = nil, ahead: Int? = nil, version: String? = nil) -> UpdateCheck {
            var c = UpdateCheck()
            c.upstream = upstream
            c.channel = channel
            c.behind = behind
            c.ahead = ahead
            c.upstreamVersion = version
            return c
        }
        XCTAssertEqual("3 commits behind", ServerUpdateWords.checkWords(check(true, "main", behind: 3)))
        XCTAssertEqual("1 commit behind", ServerUpdateWords.checkWords(check(true, "main", behind: 1)))
        XCTAssertEqual("up to date", ServerUpdateWords.checkWords(check(true, "main", behind: 0)))
        XCTAssertEqual("Domovoi 1.6.0 is available", ServerUpdateWords.checkWords(check(true, "stable", behind: 2, version: "1.6.0")))
        XCTAssertEqual("newer than the latest release", ServerUpdateWords.checkWords(check(true, "stable", behind: 0, ahead: 4)))
        XCTAssertEqual("the latest release", ServerUpdateWords.checkWords(check(true, "stable", behind: 0)))
        XCTAssertNil(ServerUpdateWords.checkWords(check(false, nil)))
        XCTAssertEqual("no release has been published on the stable channel yet", ServerUpdateWords.checked(check(false, "stable")))
        XCTAssertEqual("no upstream configured — can’t check", ServerUpdateWords.checked(check(false, "main")))
        XCTAssertEqual(
            "New code is on disk but this process is still running the old modules — restart below to load it.",
            ServerUpdateWords.codePending(restartCapable: true)
        )
        XCTAssertEqual(
            "Plugin upgrade waiting for a restart: radio 1.1.0 → 1.2.0 — restart below to finish.",
            ServerUpdateWords.pluginsPending(["radio 1.1.0 → 1.2.0"], restartCapable: true)
        )
        XCTAssertEqual(
            "systemctl not found — not a systemd host. Restart the Domovoi services the way they were started.",
            ServerUpdateWords.byHand(v(#"{"restart_hint":"systemctl not found — not a systemd host"}"#))
        )
    }

    func testARestartIsFollowedUntilANewProcessAnswers() {
        let press = RestartPress(mode: "restart", plain: true, previousRun: nil, previousStart: "100")
        XCTAssertEqual(.waiting, press.after(v(#"{"started_at":100}"#), wentAway: true))
        XCTAssertEqual(.waiting, press.after(v(#"{"started_at":200,"restart_in_progress":true}"#), wentAway: true))
        XCTAssertEqual(.done(ok: true, said: "restarted — now running aaaa111"), press.after(v(#"{"started_at":200,"sha":"aaaa111"}"#), wentAway: false))
        let blind = RestartPress(mode: "restart", plain: true, previousRun: nil, previousStart: nil)
        XCTAssertEqual(.waiting, blind.after(v("{}"), wentAway: false))
        XCTAssertNotEqual(.waiting, blind.after(v("{}"), wentAway: true))
        XCTAssertEqual("restart is taking longer than expected — check the service by hand", press.tooLong())
    }

    func testAnUpdateEndsWithTheUnitsResult() {
        let press = RestartPress(mode: "update", plain: false, previousRun: "t1", previousStart: "100")
        XCTAssertEqual(.waiting, press.after(v(#"{"started_at":200,"last_update":{"status":"ok","started_at":"t1"}}"#), wentAway: true))
        XCTAssertEqual(.waiting, press.after(v(#"{"last_update":{"status":"running","started_at":"t2"}}"#), wentAway: true))
        XCTAssertEqual(
            .done(ok: false, said: "update failed at health and was rolled back"),
            press.after(v(#"{"last_update":{"status":"rolled_back","started_at":"t2","error":"update failed at health and was rolled back"}}"#), wentAway: true)
        )
        XCTAssertEqual(
            .done(ok: false, said: "update not applied — see journalctl -u domovoi-update"),
            press.after(v(#"{"last_update":{"status":"refused","started_at":"t2"}}"#), wentAway: true)
        )
        XCTAssertEqual(
            .done(ok: true, said: "restarted — now running bbbb222"),
            press.after(v(#"{"running_sha":"bbbb222","last_update":{"status":"ok","started_at":"t2"}}"#), wentAway: true)
        )
        XCTAssertEqual("the update is taking longer than expected — check journalctl -u domovoi-update", press.tooLong())
    }

    // MARK: - the model, against a stub house

    @MainActor
    private func signedIn(_ fh: FhHouse) async -> AdminSession {
        fh.answer("POST", "/api/auth/login", #"{"ok":true,"token":"\#(String(repeating: "a", count: 64))","idle_timeout_min":60}"#)
        let (container, _) = fhContainer(fh, server: server)
        let session = container.admin
        let out = await session.signIn(password: "right-password", label: "phone")
        XCTAssertEqual(.ok, out)
        return session
    }

    private func sent(_ method: String, _ path: String) -> URLRequest? {
        StubURLProtocol.recorded.last { $0.httpMethod == method && $0.url?.path == path }
    }

    @MainActor
    func testOnePressPullsRestartsAndFollowsTheUpdate() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/config/version",
                  #"{"version":"1.5.0","sha":"aaaa111","restart_capable":true,"restart_mode":"update","started_at":100,"last_update":{"status":"ok","started_at":"t1"}}"#)
        fh.answer("POST", "/api/config/version/check", #"{"upstream":"origin/main","channel":"main","behind":2,"upstream_sha":"bbbb222"}"#)
        fh.answer("POST", "/api/config/version/pull", #"{"pulled":true,"new_sha":"bbbb222"}"#)
        fh.answer("POST", "/api/config/version/restart", #"{"ok":true,"mode":"update"}"#)
        let clock = Shared<Int64>(0)
        let zone = ServerUpdateModel(
            admin: await signedIn(fh),
            nowMs: { clock.current },
            wait: { ms in
                clock.mutate { $0 += ms }
                // The update unit's run lands after the first wait.
                fh.answer("GET", "/api/config/version",
                          #"{"version":"1.5.1","sha":"bbbb222","restart_capable":true,"restart_mode":"update","started_at":300,"last_update":{"status":"ok","started_at":"t2"}}"#)
            }
        )
        var said: [String] = []
        zone.say = { said.append($0) }
        await zone.load()
        XCTAssertEqual(.check, zone.mode)
        await zone.checkForUpdates()
        XCTAssertEqual(.pull, zone.mode)
        XCTAssertEqual(["2 commits behind"], said)
        XCTAssertNil(sent("POST", "/api/config/version/check")?.value(forHTTPHeaderField: "Authorization"))

        await zone.update(onePress: true)
        XCTAssertEqual(["2 commits behind", "updating…", "restarted — now running bbbb222"], said)
        XCTAssertEqual(Self.bearer, sent("POST", "/api/config/version/pull")?.value(forHTTPHeaderField: "Authorization"))
        XCTAssertEqual(Self.bearer, sent("POST", "/api/config/version/restart")?.value(forHTTPHeaderField: "Authorization"))
        XCTAssertEqual("1.5.1", zone.version?.version)
        XCTAssertNil(zone.following)
        XCTAssertNil(zone.problem)
    }

    @MainActor
    func testARefusedRestartIsSaidAndNothingIsFollowed() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/config/version", #"{"restart_capable":true,"restart_mode":"restart","started_at":100}"#)
        fh.answer("POST", "/api/config/version/restart", #"{"detail":"admin session required"}"#, status: 403)
        let zone = ServerUpdateModel(admin: await signedIn(fh), wait: { _ in XCTFail("nothing to wait for") })
        var said: [String] = []
        zone.say = { said.append($0) }
        await zone.load()
        await zone.restart(plain: true)
        XCTAssertEqual(1, said.count)
        XCTAssertEqual(said.first, zone.problem)
        XCTAssertNil(zone.following)
    }

    @MainActor
    func testWithoutAdminNothingIsSent() async {
        let fh = FhHouse()
        fh.answer("GET", "/api/config/version", #"{"restart_capable":true,"restart_mode":"restart","started_at":100}"#)
        let session = await signedIn(fh)
        session.forget()
        let zone = ServerUpdateModel(admin: session, wait: { _ in })
        await zone.load()
        await zone.restart(plain: true)
        XCTAssertEqual(adminOnly, zone.problem)
        XCTAssertNil(sent("POST", "/api/config/version/restart"))
    }
}
