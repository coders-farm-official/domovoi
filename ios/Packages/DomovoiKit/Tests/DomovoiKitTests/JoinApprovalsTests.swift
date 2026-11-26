import Foundation
import XCTest
@testable import DomovoiKit

/// Devices asking to join, as the phone's admin sees them (§3.7a). Android's
/// `JoinApprovalsTest` (bd5c4b5e), case for case, and then the card's model
/// against ``StubURLProtocol``: what it reads, and what the two writes send.
final class JoinApprovalsTests: XCTestCase {

    private let sample = """
        [{"satellite_id": "kitchen_2", "room_id": "kitchen_2",
          "requested_room": "kitchen", "requested_room_name": "Kitchen",
          "has_code": true, "proof": false, "waiting_for_device": false,
          "code_rejected": false, "proof_failures": 0,
          "mac": null, "board": "android_room", "sat_type": "voice",
          "first_seen_at": "2026-11-22T18:00:00Z", "last_seen_at": "2026-11-22T18:02:10Z",
          "attempts": 3}]
        """
    private let rooms = [JoinRoom(roomID: "den", name: "Den"), JoinRoom(roomID: "kitchen", name: "Kitchen")]

    private func body(_ data: Data) -> [String: String] {
        ((try? JSONSerialization.jsonObject(with: data)) as? [String: Any])?.compactMapValues { $0 as? String } ?? [:]
    }

    // MARK: - the pure half (JoinApprovalsTest.kt)

    func testTheApiReferenceExampleReads() throws {
        let r = try XCTUnwrap(JSONDecoder().decode([JoinRequest].self, from: Data(sample.utf8)).first)
        XCTAssertEqual("kitchen_2", r.id)
        XCTAssertEqual("An Android phone or tablet", JoinApprovals.deviceWords(r))
        XCTAssertEqual("An Android phone or tablet asking to join the Kitchen", JoinApprovals.askingLine(r))
    }

    func testAnOlderServersRequestUsesItsRoomIdAsTheId() throws {
        let r = try JSONDecoder().decode(JoinRequest.self, from: Data(#"{"room_id":"porch","has_code":true}"#.utf8))
        XCTAssertEqual("porch", r.id)
    }

    func testTheRoomItAskedForIsPreselectedWhenItExists() {
        let r = JoinRequest(satelliteID: "kitchen_2", roomID: "kitchen_2", requestedRoom: "kitchen", requestedRoomName: "Kitchen")
        XCTAssertEqual(.existing(roomID: "kitchen", name: "Kitchen"), JoinApprovals.preselect(r, rooms: rooms))
        // Asked for a room that's gone (no name): a new room after the device.
        let gone = JoinRequest(satelliteID: "porch_2", roomID: "porch_2", requestedRoom: "porch")
        XCTAssertEqual(.newRoom(name: "Porch 2"), JoinApprovals.preselect(gone, rooms: rooms))
        // Asked for nothing (a device from before 1.5.0).
        XCTAssertEqual(.newRoom(name: "Garage"), JoinApprovals.preselect(JoinRequest(roomID: "garage"), rooms: rooms))
    }

    func testTheChoicesAreEveryRoomThenANewOne() {
        let c = JoinApprovals.choices(JoinRequest(satelliteID: "kitchen_2", roomID: "kitchen_2"), rooms: rooms)
        XCTAssertEqual(
            [.existing(roomID: "den", name: "Den"), .existing(roomID: "kitchen", name: "Kitchen"), .newRoom(name: "Kitchen 2")],
            c
        )
    }

    func testApproveSendsTheCodeAndExactlyOneRoomChoice() {
        let a = body(JoinApprovals.approveBody(code: "123456", choice: .existing(roomID: "kitchen", name: "Kitchen")))
        XCTAssertEqual(["code": "123456", "room_id": "kitchen"], a)
        let b = body(JoinApprovals.approveBody(code: "123456", choice: .newRoom(name: "  Kitchen 2 ")))
        XCTAssertEqual(["code": "123456", "new_room_name": "Kitchen 2"], b)
    }

    func testOnlySixDigitsGoOut() {
        XCTAssertTrue(JoinApprovals.codeOk("012345"))
        for bad in ["", "12345", "1234567", "12a456", "12 456", "١٢٣٤٥٦"] {
            XCTAssertFalse(JoinApprovals.codeOk(bad), bad)
        }
        XCTAssertEqual("123456", JoinApprovals.cleanCode("12-34 5678"))
    }

    func testWhatHappenedIsSaidInHouseholdWords() {
        XCTAssertEqual("Added to Kitchen.", JoinApprovals.approvedText(waitingForDevice: false, choice: .existing(roomID: "kitchen", name: "Kitchen")))
        XCTAssertTrue(JoinApprovals.approvedText(waitingForDevice: true, choice: .newRoom(name: "Den 2")).hasPrefix("Code sent. The device joins Den 2"))
        XCTAssertEqual("That code doesn't match the one the device shows.", JoinApprovals.refusalText(status: 422, detail: "wrong code"))
        XCTAssertTrue(JoinApprovals.refusalText(status: 429, detail: nil).contains("five minutes"))
        XCTAssertNil(JoinApprovals.status(JoinRequest(roomID: "x")))
        XCTAssertTrue(JoinApprovals.status(JoinRequest(roomID: "x", waitingForDevice: true))?.contains("next visit") == true)
        XCTAssertTrue(JoinApprovals.status(JoinRequest(roomID: "x", codeRejected: true))?.contains("wasn't this device's") == true)
    }

    func testNoIdIsShownForWhatIsAsking() {
        for board in ["android_room", "ios_room", "raspberry_pi_zero_2_w", "linux_pc", nil] {
            let r = JoinRequest(satelliteID: "kitchen_2", roomID: "kitchen_2", board: board)
            let words = JoinApprovals.deviceWords(r)
            XCTAssertFalse(words.contains("kitchen_2") || words.contains("_"), words)
            XCTAssertFalse(JoinApprovals.askingLine(r).contains("_"))
        }
    }

    // MARK: - the card's model, against a stub house

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

    @MainActor
    private func signedIn(listing: String) async -> AdminSession {
        let sample = listing
        StubURLProtocol.dispatch { request in
            let path = request.url?.path ?? ""
            let bearer = request.value(forHTTPHeaderField: "Authorization") == Self.bearer
            switch path {
            case "/api/auth/login":
                return .json(#"{"ok":true,"token":"\#(String(repeating: "a", count: 64))","idle_timeout_min":60}"#)
            case "/api/satellites/approvals":
                return bearer ? .json(sample) : .json(#"{"detail":"admin session required"}"#, status: 401)
            case "/api/rooms":
                return .json(#"{"rooms":[{"room_id":"den","name":"Den"},{"room_id":"kitchen","name":"Kitchen"}]}"#)
            case "/api/satellites/approvals/porch 2/approve":
                guard bearer else { return .json(#"{"detail":"admin session required"}"#, status: 401) }
                let sent = StubURLProtocol.recorded.last { $0.url?.path == path }?.httpBody ?? Data()
                let code = ((try? JSONSerialization.jsonObject(with: sent)) as? [String: Any])?["code"] as? String
                return code == "123456"
                    ? .json(#"{"ok":true,"waiting_for_device":true}"#)
                    : .json(#"{"detail":"wrong code"}"#, status: 422)
            case "/api/satellites/approvals/porch 2/reject":
                return bearer ? .json(#"{"ok":true}"#) : .json(#"{"detail":"admin session required"}"#, status: 401)
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

    private let porch = #"[{"satellite_id":"porch 2","room_id":"porch 2","has_code":true,"board":"linux_pc"}]"#

    @MainActor
    func testSignedInItListsTheRequestsAndTheRooms() async {
        let joins = JoinRequestsModel(admin: await signedIn(listing: sample))
        await joins.load(viaAct: false)
        XCTAssertEqual(["kitchen_2"], joins.requests?.map(\.id))
        XCTAssertEqual(rooms, joins.rooms)
        XCTAssertNil(joins.error)
        let read = StubURLProtocol.recorded.last { $0.url?.path == "/api/satellites/approvals" }
        XCTAssertEqual(Self.bearer, read?.value(forHTTPHeaderField: "Authorization"))
        // The household read carries no Bearer.
        XCTAssertNil(StubURLProtocol.recorded.last { $0.url?.path == "/api/rooms" }?.value(forHTTPHeaderField: "Authorization"))
    }

    @MainActor
    func testWithNoSessionThereIsNoList() async {
        let session = await signedIn(listing: sample)
        await session.signOut()
        let joins = JoinRequestsModel(admin: session)
        await joins.load(viaAct: false)
        XCTAssertNil(joins.requests)
        XCTAssertNil(joins.error)
        await joins.load(viaAct: true)
        XCTAssertNil(joins.requests, "nothing kept: still no list, and no Face ID asked")
    }

    @MainActor
    func testApproveGoesToTheEncodedPathWithTheCodeAndTheRoom() async throws {
        let joins = JoinRequestsModel(admin: await signedIn(listing: porch))
        await joins.load(viaAct: false)
        let r = try XCTUnwrap(joins.requests?.first)
        XCTAssertEqual("A computer", JoinApprovals.deviceWords(r))
        let done = await joins.send(r, approve: true, code: "123456", choice: .existing(roomID: "den", name: "Den"))
        XCTAssertEqual(.done(toast: "Code sent. The device joins Den once it checks the code, within ten minutes."), done)
        let sent = try XCTUnwrap(StubURLProtocol.recorded.last { $0.url?.path == "/api/satellites/approvals/porch 2/approve" })
        XCTAssertTrue(sent.url?.absoluteString.hasSuffix("/api/satellites/approvals/porch%202/approve") == true,
                      sent.url?.absoluteString ?? "")
        XCTAssertEqual(Self.bearer, sent.value(forHTTPHeaderField: "Authorization"))
        XCTAssertEqual(["code": "123456", "room_id": "den"], body(sent.httpBody ?? Data()))
    }

    @MainActor
    func testAWrongCodeAndATurnAwaySayWhatHappened() async throws {
        let joins = JoinRequestsModel(admin: await signedIn(listing: porch))
        await joins.load(viaAct: false)
        let r = try XCTUnwrap(joins.requests?.first)
        let wrong = await joins.send(r, approve: true, code: "654321", choice: .newRoom(name: "Porch 2"))
        XCTAssertEqual(.refused(note: "That code doesn't match the one the device shows."), wrong)
        let away = await joins.send(r, approve: false, code: "", choice: .newRoom(name: "Porch 2"))
        XCTAssertEqual(.done(toast: JoinApprovals.turnedAway), away)
        let reject = StubURLProtocol.recorded.last { $0.url?.path == "/api/satellites/approvals/porch 2/reject" }
        XCTAssertNil(reject?.httpBody, "turning away sends no code")
    }
}
