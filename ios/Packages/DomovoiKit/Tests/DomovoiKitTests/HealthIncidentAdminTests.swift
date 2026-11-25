import Foundation
import XCTest
@testable import DomovoiKit

/// Health incidents as admin on this phone: what the Bearer read adds, when
/// acknowledge and clear are offered, what clearing says, and the routes —
/// Android's `IncidentAdminTest`, case for case, on ``StubURLProtocol``.
final class HealthIncidentAdminTests: XCTestCase {

    private let server = "http://192.168.1.20:6369"
    private static let bearer = "Bearer " + String(repeating: "a", count: 64)

    private static let adminRead = """
        {"incidents":[
          {"id":7,"class":"tamper","kind":"image","open":true,"can_acknowledge":false,"can_clear":true,
           "expected":{"image":"sha256:aaa","pin":"digest","tags":["a","b"],"empty":"","none":null},
           "found":{"image":"sha256:bbb","ports":[]},
           "acknowledged_by":null,"cleared_by":null,"clear_note":null},
          {"id":8,"class":"failure","kind":"stopped","open":true,"can_acknowledge":true,"can_clear":false},
          {"id":9,"class":"tamper","open":false,"can_clear":false,"cleared_by":"admin via phone: iPhone","clear_note":"I swapped it"},
          {"class":"failure"}
        ],"banner":{"show":true},"frozen":true,"attention":true}
        """

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the pure half

    func testTheBearerReadAddsTheDetailsById() throws {
        let f = healthAdminIncidents(Data(Self.adminRead.utf8))
        XCTAssertEqual([7, 8, 9], f.keys.sorted())
        let seven = try XCTUnwrap(f[7])
        XCTAssertTrue(seven.canClear)
        XCTAssertFalse(seven.canAcknowledge)
        XCTAssertEqual(
            [HealthDetailRow("image", "sha256:aaa"), HealthDetailRow("pin", "digest"), HealthDetailRow("tags", "a, b")],
            seven.expected
        )
        XCTAssertEqual([HealthDetailRow("image", "sha256:bbb"), HealthDetailRow("ports", "—")], seven.found)
        XCTAssertNil(HealthIncidentAdminWords.who(seven))
        XCTAssertEqual("cleared by admin via phone: iPhone · I swapped it", HealthIncidentAdminWords.who(try XCTUnwrap(f[9])))
        // A household read (no admin fields) is just ids and two noes.
        let household = healthAdminIncidents(Data(#"{"incidents":[{"id":1,"class":"failure"}]}"#.utf8))
        XCTAssertEqual(HealthIncidentAdminFields(id: 1), household[1])
        XCTAssertTrue(healthAdminIncidents(Data("[]".utf8)).isEmpty)
    }

    func testAcknowledgeAndClearAreOfferedOnlyWhereTheyCanWork() {
        let f = healthAdminIncidents(Data(Self.adminRead.utf8))
        let tamper = HealthIncident(id: 7, cls: healthClassTamper, isOpen: true)
        let failure = HealthIncident(id: 8, cls: healthClassFailure, isOpen: true)
        XCTAssertTrue(healthOffersClear(tamper, f[7]))
        XCTAssertFalse(healthOffersAcknowledge(tamper, f[7]))
        XCTAssertTrue(healthOffersAcknowledge(failure, f[8]))
        XCTAssertFalse(healthOffersClear(failure, f[8]))
        // A stale admin read never offers either on a row that has moved on.
        XCTAssertFalse(healthOffersClear(HealthIncident(id: 7, cls: healthClassTamper, isOpen: false), f[7]))
        XCTAssertFalse(healthOffersAcknowledge(HealthIncident(id: 8, acknowledged: true, isOpen: true), f[8]))
        // No admin half (signed out): nothing.
        XCTAssertFalse(healthOffersClear(tamper, nil))
        XCTAssertFalse(healthOffersAcknowledge(failure, nil))
    }

    func testTheModeFollowsTheSessionAndNeverOnASharedScreen() {
        let signedIn = AdminSession.State.signedIn(server: server, idleUntilMs: nil, keptUntilMs: nil)
        let kept = AdminSession.State.kept(server: server, untilMs: 1)
        XCTAssertEqual(.live, healthIncidentAdminMode(signedIn, sharedScreen: false))
        XCTAssertEqual(.kept, healthIncidentAdminMode(kept, sharedScreen: false))
        XCTAssertEqual(.none, healthIncidentAdminMode(.signedOut, sharedScreen: false))
        XCTAssertEqual(.none, healthIncidentAdminMode(signedIn, sharedScreen: true))
        XCTAssertEqual(.none, healthIncidentAdminMode(kept, sharedScreen: true))
    }

    func testWhatClearingSaysFollowsTheAnswer() {
        XCTAssertEqual("cleared · paused changes resume", HealthIncidentAdminWords.cleared(HealthClearResult(frozen: false, keptImage: nil)))
        XCTAssertEqual(
            "cleared · risky changes stay paused until every tampering incident is cleared",
            HealthIncidentAdminWords.cleared(HealthClearResult(frozen: true, keptImage: nil))
        )
        XCTAssertTrue(HealthIncidentAdminWords.cleared(HealthClearResult(frozen: false, keptImage: "sha256:ccc"))
            .hasSuffix("the music player image it found is now the one expected"))
        XCTAssertEqual(
            HealthClearResult(frozen: false, keptImage: "sha256:ccc"),
            HealthClearResult(data: Data(#"{"incident":{},"frozen":false,"kept_image":"sha256:ccc"}"#.utf8))
        )
        XCTAssertEqual(HealthClearResult(frozen: true, keptImage: nil), HealthClearResult(data: Data(#"{"frozen":true,"kept_image":null}"#.utf8)))
    }

    func testANoteIsAtMost500CharactersAndBlankIsNone() throws {
        func note(_ typed: String) throws -> Any? {
            let object = try JSONSerialization.jsonObject(with: healthClearBody(note: typed)) as? [String: Any]
            return object?["note"]
        }
        XCTAssertTrue(try note("   ") is NSNull)
        XCTAssertEqual("checked it", try note("  checked it ") as? String)
        XCTAssertEqual(500, (try note(String(repeating: "x", count: 600)) as? String)?.count)
    }

    func testSignedOutTheLinesNameThePhoneAndASharedScreenKeepsItsWords() {
        XCTAssertEqual(HealthIncidentAdminWords.dashboardOrPhone, HealthPanelStrings.adminClearsThese)
        XCTAssertTrue(HealthPanelStrings.adminClearsThese.hasPrefix("an admin acknowledges or clears these on the dashboard's Settings → Health"))
        let household = HealthPanelState(body: .loading, shared: false)
        let shared = HealthPanelState(body: .loading, shared: true)
        XCTAssertEqual(HealthIncidentAdminWords.detailsOrPhone, HealthIncidentAdminWords.noDetailsNote(household, mode: .none))
        XCTAssertEqual(HealthPanelStrings.adminSeesDetails, HealthIncidentAdminWords.noDetailsNote(household, mode: .live))
        XCTAssertEqual(HealthPanelStrings.sharedScreenNote, HealthIncidentAdminWords.noDetailsNote(shared, mode: .none))
        XCTAssertEqual("That one is a failure.", HealthIncidentAdminWords.refusal(status: 409, detail: "That one is a failure."))
        XCTAssertEqual("that incident is gone", HealthIncidentAdminWords.refusal(status: 404, detail: nil))
        for area in ["Settings → Rooms", "Friend Houses → friends & connections", "Settings → Health"] {
            XCTAssertTrue(AdminStrings.jobs.contains(area), area)
        }
    }

    // MARK: - the model, against a stub house

    @MainActor
    private func signedIn() async -> AdminSession {
        let read = Self.adminRead
        StubURLProtocol.dispatch { request in
            let path = request.url?.path ?? ""
            let bearer = request.value(forHTTPHeaderField: "Authorization") == Self.bearer
            switch path {
            case "/api/auth/login":
                return .json(#"{"ok":true,"token":"\#(String(repeating: "a", count: 64))","idle_timeout_min":60}"#)
            case "/api/containers/incidents":
                return .json(read)
            case "/api/containers/incidents/8/acknowledge":
                return bearer ? .json(#"{"incident":{}}"#) : .json(#"{"detail":"admin session required"}"#, status: 401)
            case "/api/containers/incidents/7/clear":
                return bearer ? .json(#"{"incident":{},"frozen":false,"kept_image":null}"#)
                    : .json(#"{"detail":"admin session required"}"#, status: 401)
            case "/api/containers/incidents/8/clear":
                return .json(#"{"detail":"That one is a failure: it resolves by itself."}"#, status: 409)
            default:
                return .json(#"{"detail":"Not Found"}"#, status: 404)
            }
        }
        let server = server
        let client = DomovoiClient(
            book: .fixed(server: server, token: "household-abc"),
            gate: AdmitsAnyServer(),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session(timeout: 5)
        )
        let session = AdminSession(client: client, prove: { _ in "SHA256:test" }, registry: AdminSessions())
        let out = await session.signIn(password: "right-password", label: "phone")
        XCTAssertEqual(.ok, out)
        return session
    }

    private func sent(_ path: String) -> URLRequest? {
        StubURLProtocol.recorded.last { $0.url?.path == path }
    }

    @MainActor
    func testSignedInTheDetailsComeWithTheBearerAndEachChangeReadsAgain() async throws {
        let zone = HealthIncidentAdminModel(admin: await signedIn())
        await zone.load(viaAct: false)
        XCTAssertEqual([7, 8, 9], zone.fields?.keys.sorted())
        XCTAssertEqual(Self.bearer, sent("/api/containers/incidents")?.value(forHTTPHeaderField: "Authorization"))

        let reads = StubURLProtocol.recorded.filter { $0.url?.path == "/api/containers/incidents" }.count
        let acknowledged = await zone.acknowledge(HealthIncident(id: 8))
        XCTAssertEqual("acknowledged", acknowledged)
        XCTAssertEqual(Self.bearer, sent("/api/containers/incidents/8/acknowledge")?.value(forHTTPHeaderField: "Authorization"))

        let cleared = await zone.clear(HealthIncident(id: 7, cls: healthClassTamper), note: " checked the image ")
        XCTAssertEqual("cleared · paused changes resume", cleared)
        let body = try XCTUnwrap(sent("/api/containers/incidents/7/clear")?.httpBody)
        XCTAssertEqual("checked the image", (try JSONSerialization.jsonObject(with: body) as? [String: Any])?["note"] as? String)
        XCTAssertEqual(reads + 2, StubURLProtocol.recorded.filter { $0.url?.path == "/api/containers/incidents" }.count,
                       "each change reads the incidents again")
        XCTAssertNil(zone.problem)
        XCTAssertFalse(zone.busy)
    }

    @MainActor
    func testARefusalIsSaidAndNothingIsToasted() async {
        let zone = HealthIncidentAdminModel(admin: await signedIn())
        let said = await zone.clear(HealthIncident(id: 8), note: "")
        XCTAssertNil(said)
        XCTAssertEqual("That one is a failure: it resolves by itself.", zone.problem)
    }

    @MainActor
    func testWithoutAdminNothingIsReadOrSent() async {
        let session = await signedIn()
        session.forget()
        let zone = HealthIncidentAdminModel(admin: session)
        await zone.load(viaAct: false)
        XCTAssertNil(zone.fields)
        let said = await zone.acknowledge(HealthIncident(id: 8))
        XCTAssertNil(said)
        XCTAssertEqual(adminOnly, zone.problem)
        XCTAssertNil(sent("/api/containers/incidents"))
        XCTAssertNil(sent("/api/containers/incidents/8/acknowledge"))
    }
}
