import Foundation
import XCTest
@testable import DomovoiKit

/// A room as admin on this phone (1.5.0): when its controls are offered, what
/// they say, the settings editor's rules, and the three routes — Android's
/// `SatAdminTest`, case for case.
final class RoomAdminTests: XCTestCase {

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

    func testTheControlsAreThereOnlyWithAdminAndNeverOnASharedScreen() {
        let signedIn = AdminSession.State.signedIn(server: server, idleUntilMs: nil, keptUntilMs: nil)
        let kept = AdminSession.State.kept(server: server, untilMs: 1)
        XCTAssertTrue(roomAdminHeld(signedIn, sharedScreen: false))
        XCTAssertTrue(roomAdminHeld(kept, sharedScreen: false), "kept today: the tap asks for Face ID first")
        XCTAssertFalse(roomAdminHeld(.signedOut, sharedScreen: false))
        XCTAssertFalse(roomAdminHeld(signedIn, sharedScreen: true))
        XCTAssertFalse(roomAdminHeld(kept, sharedScreen: true))
    }

    func testWhatADeviceDeclaredDecidesRestartAndUpgrade() throws {
        let pi = RoomsSatellite(roomID: "kitchen")
        XCTAssertTrue(roomsCan(pi, "restart") && roomsCan(pi, "code_sync"))
        let phone = try JSONDecoder().decode(
            RoomsSatellite.self, from: Data(#"{"room_id":"den","supports":["speech_pause","room_display"]}"#.utf8)
        )
        XCTAssertEqual(["speech_pause", "room_display"], phone.supports)
        XCTAssertFalse(roomsCan(phone, "restart"))
        XCTAssertFalse(roomsCan(phone, "code_sync"))
        XCTAssertEqual("this device restarts and updates itself", RoomAdminWords.notOffered(canRestart: false, canUpgrade: false))
        XCTAssertEqual("this device updates itself", RoomAdminWords.notOffered(canRestart: true, canUpgrade: false))
        XCTAssertNil(RoomAdminWords.notOffered(canRestart: true, canUpgrade: true))
    }

    func testTheButtonsFollowTheRoomAndTheServer() {
        let behind = RoomMaintenanceButtons(
            room: RoomsSatellite(roomID: "kitchen", status: "online", version: "aaaa111"),
            coreSha: "bbbb222", restarting: false, asking: false
        )
        XCTAssertTrue(behind.restartEnabled && behind.upgradeEnabled && behind.upgradePrimary)
        XCTAssertEqual("Upgrade room", behind.upgradeLabel)
        XCTAssertNil(behind.notOffered)

        let current = RoomMaintenanceButtons(
            room: RoomsSatellite(roomID: "kitchen", status: "online", version: "bbbb222"),
            coreSha: "bbbb222", restarting: false, asking: false
        )
        XCTAssertTrue(current.restartEnabled)
        XCTAssertFalse(current.upgradeEnabled, "up to date: nothing to upgrade to")
        XCTAssertFalse(current.upgradePrimary)

        let unknown = RoomMaintenanceButtons(
            room: RoomsSatellite(roomID: "kitchen", status: "online"), coreSha: nil, restarting: false, asking: false
        )
        XCTAssertTrue(unknown.upgradeEnabled, "a version not known is not up to date")
        XCTAssertFalse(unknown.upgradePrimary)

        let offline = RoomMaintenanceButtons(
            room: RoomsSatellite(roomID: "kitchen", status: "offline", version: "aaaa111"),
            coreSha: "bbbb222", restarting: false, asking: false
        )
        XCTAssertFalse(offline.restartEnabled || offline.upgradeEnabled)

        let running = RoomMaintenanceButtons(
            room: RoomsSatellite(roomID: "kitchen", status: "online", version: "aaaa111", upgrade: RoomsUpgrade(state: "running")),
            coreSha: "bbbb222", restarting: false, asking: false
        )
        XCTAssertFalse(running.restartEnabled || running.upgradeEnabled)
        XCTAssertEqual("Upgrading…", running.upgradeLabel)

        let busy = RoomMaintenanceButtons(
            room: RoomsSatellite(roomID: "kitchen", status: "online", version: "aaaa111"),
            coreSha: "bbbb222", restarting: true, asking: true
        )
        XCTAssertFalse(busy.restartEnabled || busy.upgradeEnabled)

        let phone = RoomMaintenanceButtons(
            room: RoomsSatellite(roomID: "den", status: "online", version: "aaaa111", supports: ["room_display"]),
            coreSha: "bbbb222", restarting: false, asking: false
        )
        XCTAssertFalse(phone.restartEnabled || phone.upgradeEnabled || phone.upgradePrimary)
        XCTAssertEqual("this device restarts and updates itself", phone.notOffered)
    }

    func testTheConfirmsAreTheDashboards() {
        XCTAssertEqual(
            "Restart the kitchen room? It'll drop offline for a few seconds while the service bounces.",
            RoomAdminWords.restartConfirm("kitchen")
        )
        XCTAssertTrue(RoomAdminWords.upgradeConfirm("kitchen", coreSha: "abc1234").hasPrefix("Upgrade the kitchen room to abc1234?\n\n"))
        XCTAssertTrue(RoomAdminWords.upgradeConfirm("kitchen", coreSha: nil).hasPrefix("Upgrade the kitchen room to the latest domovoi code?"))
        XCTAssertTrue(RoomAdminWords.upgradeConfirm("kitchen", coreSha: nil).contains("If the new code won't start, it rolls back automatically."))
        XCTAssertEqual(
            "Save and restart the kitchen room? It'll drop offline for a few seconds while it applies the change.",
            RoomAdminWords.saveConfirm("kitchen")
        )
    }

    func testSignedOutTheDashboardLinesNameThePhoneToo() {
        XCTAssertTrue(RoomAdminWords.maintenanceOrPhone.hasPrefix(RoomsStrings.upgradeIsOnTheDashboard))
        XCTAssertTrue(RoomAdminWords.maintenanceOrPhone.hasSuffix(", or sign in as admin on this phone"))
        XCTAssertTrue(RoomAdminWords.configOrPhone.hasPrefix(String(RoomsStrings.configIsReadOnly.dropLast())))
        XCTAssertTrue(RoomAdminWords.configOrPhone.hasSuffix(", or sign in as admin on this phone."))
        XCTAssertTrue(AdminStrings.jobs.contains("restart, upgrade or change the settings of a room"))
    }

    /// The words are the Kotlin's: read from `SatAdmin.kt` itself.
    func testTheWordsAreTheKotlins() throws {
        let here = URL(fileURLWithPath: #filePath)
        let kotlin = here.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("android/app/src/main/java/com/domovoi/app/ui/screens/satellites/SatAdmin.kt")
        guard let source = try? String(contentsOf: kotlin, encoding: .utf8) else {
            throw XCTSkip("the Android source isn't next to this package")
        }
        let flat = source.replacingOccurrences(of: "\" +\n            \"", with: "")
            .replacingOccurrences(of: "\" +\n        \"", with: "")
        for words in [
            RoomAdminWords.maintenanceOrPhone, RoomAdminWords.configOrPhone, RoomAdminWords.configEdit,
            RoomAdminWords.advancedOnDashboard, RoomAdminWords.restart, RoomAdminWords.upgrade,
            RoomAdminWords.save, RoomAdminWords.undo,
        ] {
            XCTAssertTrue(flat.contains("\"\(words)\""), words)
        }
    }

    private func field(
        _ name: String, _ type: String, _ value: LooseJSON?, section: String? = nil,
        min: Double? = nil, max: Double? = nil, supported: Bool? = nil
    ) -> RoomsConfigField {
        RoomsConfigField(name: name, section: section, type: type, min: min, max: max, value: value, supported: supported)
    }

    func testOnlyTheCommonSettingsTheRoomHasAreEditableHere() {
        XCTAssertTrue(field("wake.threshold", "float", .number(0.5)).editableHere)
        XCTAssertTrue(field("audio.mute", "bool", .bool(false)).editableHere)
        XCTAssertFalse(field("audio.device", "str", .string("hw:1"), section: "advanced").editableHere, "advanced stays on the dashboard")
        XCTAssertFalse(field("x.y", "bool", nil, supported: false).editableHere, "not on this room's software yet")
        XCTAssertFalse(field("x.z", "secret", nil).editableHere, "a kind the phone doesn't draw")
        XCTAssertEqual("4", field("mic.gain", "int", .number(4)).editText)
        XCTAssertEqual("0.5", field("wake.threshold", "float", .number(0.5)).editText)
    }

    func testNumbersAreCheckedAsTheDashboardChecksThem() {
        let threshold = field("wake.threshold", "float", .number(0.5), min: 0, max: 1)
        let gain = field("mic.gain", "int", .number(4), min: 0, max: 10)
        XCTAssertNil(roomsConfigEditProblem(threshold, .text("0.7")))
        XCTAssertEqual("must be at most 1", roomsConfigEditProblem(threshold, .text("1.5")))
        XCTAssertEqual("must be at least 0", roomsConfigEditProblem(threshold, .text("-0.1")))
        XCTAssertEqual("expected a number", roomsConfigEditProblem(threshold, .text("")))
        XCTAssertEqual("expected a number", roomsConfigEditProblem(threshold, .text("abc")))
        XCTAssertEqual("expected a whole number", roomsConfigEditProblem(gain, .text("2.5")))
        XCTAssertNil(roomsConfigEditProblem(gain, .text("7")))
        XCTAssertNil(roomsConfigEditProblem(field("audio.mute", "bool", .bool(false)), .text("abc")), "only numbers have rules")
        XCTAssertEqual(
            ["mic.gain"],
            roomsConfigEditProblems([threshold, gain], ["wake.threshold": .text("0.6"), "mic.gain": .text("x")])
        )
    }

    func testOnlyRealChangesAreSentAndTypedNumbersGoAsNumbers() throws {
        let fields = [
            field("wake.threshold", "float", .number(0.5)),
            field("mic.gain", "int", .number(4)),
            field("audio.mute", "bool", .bool(false)),
            field("greeting.mode", "choice", .string("chime")),
            field("audio.device", "str", .string("hw:1"), section: "advanced"),
        ]
        let changes = roomsConfigChanges(fields, [
            "wake.threshold": .text("0.50"),   // the same value: no change
            "mic.gain": .text("6"),
            "audio.mute": .bool(true),
            "greeting.mode": .text("chime"),
            "audio.device": .text("hw:2"),     // advanced: never from here
            "nope": .int(1),
        ])
        XCTAssertEqual(["mic.gain", "audio.mute"], changes.map { $0.name })
        XCTAssertEqual([.int(6), .bool(true)], changes.map { $0.value })
        XCTAssertEqual(
            #"{"changes":{"audio.mute":true,"mic.gain":6}}"#,
            String(decoding: roomsConfigSaveBody(changes), as: UTF8.self)
        )
    }

    func testWhatASaveSaysFollowsTheAnswer() {
        let ok = RoomsConfigSaveResult(data: Data(#"{"sent":["mic.gain"],"rejected":{},"restarting":true}"#.utf8))
        XCTAssertEqual(["mic.gain"], ok.sent)
        XCTAssertEqual("kitchen restarting to apply…", RoomAdminWords.saved("kitchen", ok))
        let some = RoomsConfigSaveResult(data: Data(#"{"sent":["a"],"rejected":{"b":"not an editable setting"},"restarting":true}"#.utf8))
        XCTAssertEqual("kitchen restarting to apply… (1 not saved)", RoomAdminWords.saved("kitchen", some))
        let none = RoomsConfigSaveResult(data: Data(
            #"{"sent":[],"rejected":{"b":"this room device doesn't have this setting yet — upgrade it first"},"restarting":false}"#.utf8
        ))
        XCTAssertEqual("nothing saved: this room device doesn't have this setting yet — upgrade it first", RoomAdminWords.saved("kitchen", none))
        XCTAssertEqual(RoomsConfigSaveResult(sent: [], rejected: [], restarting: false), RoomsConfigSaveResult(data: nil))
    }

    func testAFieldTheRoomLacksDecodesAsUnsupported() throws {
        let f = try JSONDecoder().decode(
            RoomsConfigField.self, from: Data(#"{"name":"x","type":"bool","supported":false}"#.utf8)
        )
        XCTAssertEqual(false, f.supported)
        XCTAssertFalse(f.editableHere)
    }

    // MARK: - the routes, against a stub house

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
    func testTheRoutesCarryTheBearer() async throws {
        let fh = FhHouse()
        let zone = RoomAdminModel(admin: await signedIn(fh))
        fh.answer("POST", "/api/satellites/kitchen/restart", #"{"ok":true}"#)
        fh.answer("POST", "/api/satellites/kitchen/upgrade", #"{"requested":true,"room_id":"kitchen","expected_sha":"abc1234"}"#)
        fh.answer("PATCH", "/api/satellites/kitchen/config", #"{"sent":["mic.gain"],"rejected":{},"restarting":true}"#)

        let restarted = await zone.restart("kitchen")
        XCTAssertEqual("restarting kitchen…", restarted)
        let upgraded = await zone.upgrade("kitchen")
        XCTAssertEqual("upgrading kitchen…", upgraded.said)
        XCTAssertTrue(upgraded.reread)
        let saved = await zone.save("kitchen", [(name: "mic.gain", value: .int(6))])
        XCTAssertEqual(true, saved.result?.restarting)
        XCTAssertEqual("kitchen restarting to apply…", saved.said)
        XCTAssertNil(zone.problem)

        for (method, path) in [
            ("POST", "/api/satellites/kitchen/restart"),
            ("POST", "/api/satellites/kitchen/upgrade"),
            ("PATCH", "/api/satellites/kitchen/config"),
        ] {
            XCTAssertEqual(Self.bearer, sent(method, path)?.value(forHTTPHeaderField: "Authorization"), path)
        }
        let body = try XCTUnwrap(sent("PATCH", "/api/satellites/kitchen/config")?.httpBody)
        let changes = (try JSONSerialization.jsonObject(with: body) as? [String: Any])?["changes"] as? [String: Any]
        XCTAssertEqual(6, changes?["mic.gain"] as? Int)
    }

    @MainActor
    func testARefusalIsSaidAndAnUpgradeUnderWayRereads() async {
        let fh = FhHouse()
        let zone = RoomAdminModel(admin: await signedIn(fh))
        fh.answer("POST", "/api/satellites/kitchen/upgrade", #"{"detail":"an upgrade is already running"}"#, status: 409)
        let out = await zone.upgrade("kitchen")
        XCTAssertTrue(out.reread)
        XCTAssertTrue(out.said.hasPrefix("upgrade failed"), out.said)
        XCTAssertEqual(out.said, zone.problem)
    }

    @MainActor
    func testWithoutAdminNothingIsSent() async {
        let fh = FhHouse()
        let session = await signedIn(fh)
        session.forget()
        let zone = RoomAdminModel(admin: session)
        let said = await zone.restart("kitchen")
        XCTAssertEqual(adminOnly, said)
        XCTAssertNil(sent("POST", "/api/satellites/kitchen/restart"))
    }
}
