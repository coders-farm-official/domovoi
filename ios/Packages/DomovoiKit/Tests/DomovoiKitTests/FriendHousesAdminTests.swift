import Foundation
import XCTest
@testable import DomovoiKit

/// Friend Houses' friends and connections, as admin on this phone (1.5.0):
/// the tab only with admin and never on a shared screen, the admin tab held
/// to design §1's jobs, and what each change sends — Android 3ac999df's
/// `theAdminTabIsThereOnlyWithAdmin` and job-list check, then the routes
/// against the fake dashboard (``FhHouse``).
final class FriendHousesAdminTests: XCTestCase {

    private let server = "http://192.168.1.20:6369"
    private let smiths = String(repeating: "ab", count: 32)
    private static let bearer = "Bearer " + String(repeating: "a", count: 64)

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the tab

    func testTheAdminTabIsThereOnlyWithAdmin() {
        let signedIn = AdminSession.State.signedIn(server: server, idleUntilMs: nil, keptUntilMs: nil)
        let kept = AdminSession.State.kept(server: server, untilMs: 1)
        XCTAssertEqual([.messages, .shared, .connections], fhTabsFor(signedIn, sharedScreen: false))
        XCTAssertEqual([.messages, .shared, .connections], fhTabsFor(kept, sharedScreen: false))
        XCTAssertEqual([.messages, .shared], fhTabsFor(signedIn, sharedScreen: true))
        XCTAssertEqual([.messages, .shared], fhTabsFor(.signedOut, sharedScreen: false))
        XCTAssertEqual("friends & connections", FhTab.connections.label)
    }

    /// The admin tab does design §1's Friend Houses jobs only: no friend
    /// files, renaming, verifying, this house's name, apps, save or delete.
    func testTheAdminTabDoesTheDesignsJobsOnly() {
        let files = [
            "ios/Domovoi/FriendHouses/FriendHousesAdminZone.swift",
            "ios/Packages/DomovoiKit/Sources/DomovoiKit/FriendHousesAdmin.swift",
        ]
        for file in files {
            // Comment lines dropped, string literals kept: a path or a
            // label is exactly what this looks for.
            var root = URL(fileURLWithPath: #filePath)
            for _ in 0..<6 { root = root.deletingLastPathComponent() }
            let text = (try? String(contentsOf: root.appendingPathComponent(file), encoding: .utf8)) ?? ""
            let code = text.components(separatedBy: "\n").filter { line in
                let t = line.trimmingCharacters(in: .whitespaces)
                return !(t.hasPrefix("//") || t.hasPrefix("*") || t.hasPrefix("/*"))
            }.joined(separator: "\n")
            XCTAssertTrue(code.contains("FriendHousesAdmin"), "\(file) was read")
            for gone in ["invites", "preview", "/verified", "PATCH", "settings", "/apps", "export", "Save a copy", "\"Delete\""] {
                XCTAssertFalse(code.contains(gone), "\(file): \(gone)")
            }
        }
    }

    func testTheWordsSayWhatIsAsking() {
        let waiting = FhRequest(id: "r1", direction: "in", state: "waiting", theirName: "The Smiths", code: "123456")
        XCTAssertTrue(waiting.waitingHere)
        XCTAssertEqual("The Smiths (calls itself) wants to be friends", FhAdminWords.incoming(waiting))
        XCTAssertEqual("A house (calls itself) wants to be friends", FhAdminWords.incoming(FhRequest(theirName: "  ")))
        XCTAssertEqual("Asked Gran: not reached yet", FhAdminWords.outgoing(FhRequest(direction: "out", state: "unsent", name: "Gran")))
        XCTAssertEqual("Asked That house: waiting for them to accept", FhAdminWords.outgoing(FhRequest(direction: "out", state: "sent")))
        XCTAssertEqual("The Smiths is blocked · this house is moving to a new private address",
                       FhAdminWords.blocked("The Smiths", moving: true))
        XCTAssertEqual("this house", FhAdminWords.houseName(""))
        // Names from another house are passed on unchanged: the view shows
        // them through FhPlainText.
        let hostile = "**z** [link](http://y)"
        XCTAssertTrue(FhAdminWords.incoming(FhRequest(theirName: hostile)).hasPrefix(hostile))
    }

    func testOnlyAnIdOrAHouseKeyGoesIntoAPath() throws {
        XCTAssertEqual("/api/friend-houses/requests/r_1/accept", try FhPaths.acceptRequest("r_1"))
        XCTAssertEqual("/api/friend-houses/requests/r_1/decline", try FhPaths.declineRequest("r_1"))
        XCTAssertEqual("/api/friend-houses/friends/f-1/block", try FhPaths.blockFriend("f-1"))
        XCTAssertEqual("/api/friend-houses/blocked/\(smiths)", try FhPaths.blockedHouse(smiths))
        XCTAssertThrowsError(try FhPaths.friend("../x"))
        XCTAssertThrowsError(try FhPaths.blockedHouse("not-a-key"))
    }

    // MARK: - the tab's model, against the fake dashboard

    @MainActor
    private func signedIn(_ fh: FhHouse) async -> AdminSession {
        fh.answer("POST", "/api/auth/login", #"{"ok":true,"token":"\#(String(repeating: "a", count: 64))","idle_timeout_min":60}"#)
        fh.answer("GET", "/api/friend-houses/requests", """
            {"requests":[{"id":"r1","direction":"in","state":"waiting","their_name":"The Smiths","code":"123456"},
            {"id":"r2","direction":"out","state":"unsent","name":"Gran","last_error":"no route"}]}
            """)
        fh.answer("GET", "/api/friend-houses/blocked", #"{"blocked":[{"house":"\#(smiths)","name":"Next door"}]}"#)
        fh.answer("GET", "/api/friend-houses/friends", #"{"friends":[{"id":"f1","name":"The Joneses","house":"\#(smiths)"}]}"#)
        let (container, _) = fhContainer(fh, server: server)
        let session = container.admin
        let out = await session.signIn(password: "right-password", label: "phone")
        XCTAssertEqual(.ok, out)
        return session
    }

    private func sent(_ method: String, _ path: String) -> URLRequest? {
        StubURLProtocol.recorded.last { $0.httpMethod == method && $0.url?.path == path }
    }

    private func json(_ request: URLRequest?) -> [String: Any] {
        (request?.httpBody).flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
    }

    @MainActor
    func testSignedInItReadsRequestsFriendsAndBlockedAtOnce() async {
        let fh = FhHouse()
        let zone = FriendHousesAdminModel(admin: await signedIn(fh))
        await zone.load(viaAct: false)
        XCTAssertEqual(["r1", "r2"], zone.requests?.map(\.id))
        XCTAssertEqual(["The Joneses"], zone.friends.map(\.name))
        XCTAssertEqual(["Next door"], zone.blocked.map { $0.name ?? "" })
        XCTAssertNil(zone.problem)
        XCTAssertEqual(Self.bearer, sent("GET", "/api/friend-houses/requests")?.value(forHTTPHeaderField: "Authorization"))
        XCTAssertEqual(Self.bearer, sent("GET", "/api/friend-houses/blocked")?.value(forHTTPHeaderField: "Authorization"))
        // The friends list is the household's read: no Bearer on it.
        XCTAssertNil(sent("GET", "/api/friend-houses/friends")?.value(forHTTPHeaderField: "Authorization"))
    }

    @MainActor
    func testWithoutAdminThereIsNothingToShow() async {
        let fh = FhHouse()
        let session = await signedIn(fh)
        session.forget()
        let zone = FriendHousesAdminModel(admin: session)
        await zone.load(viaAct: false)
        XCTAssertNil(zone.requests)
        await zone.load(viaAct: true)
        XCTAssertNil(zone.requests, "nothing kept: no Face ID asked, nothing read")
        XCTAssertNil(sent("GET", "/api/friend-houses/requests"))
    }

    @MainActor
    func testEachChangeSendsWhatTheDashboardSends() async throws {
        let fh = FhHouse()
        let zone = FriendHousesAdminModel(admin: await signedIn(fh))
        await zone.load(viaAct: false)
        fh.answer("POST", "/api/friend-houses/requests/r1/accept", #"{"ok":true}"#)
        fh.answer("POST", "/api/friend-houses/requests/r1/decline", #"{"ok":true}"#)
        fh.answer("DELETE", "/api/friend-houses/friends/f1", #"{"ok":true}"#)
        fh.answer("POST", "/api/friend-houses/friends/f1/block", #"{"ok":true,"new_address":true}"#)
        fh.answer("DELETE", "/api/friend-houses/blocked/\(smiths)", #"{"ok":true}"#)

        let request = try XCTUnwrap(zone.requests?.first)
        let friend = try XCTUnwrap(zone.friends.first)
        let blocked = try XCTUnwrap(zone.blocked.first)

        let accepted = await zone.accept(request, name: "  Smiths  ")
        XCTAssertEqual("friends now", accepted)
        XCTAssertEqual("Smiths", json(sent("POST", "/api/friend-houses/requests/r1/accept"))["name"] as? String)
        XCTAssertEqual(Self.bearer, sent("POST", "/api/friend-houses/requests/r1/accept")?.value(forHTTPHeaderField: "Authorization"))

        let declined = await zone.decline(request)
        XCTAssertEqual("request declined", declined)

        let removed = await zone.remove(friend)
        XCTAssertEqual("The Joneses removed", removed)

        let blockedSaid = await zone.block(friend, keepMessages: true, newAddress: false)
        XCTAssertEqual("The Joneses is blocked · this house is moving to a new private address", blockedSaid)
        let block = json(sent("POST", "/api/friend-houses/friends/f1/block"))
        XCTAssertEqual(true, block["keep_messages"] as? Bool)
        XCTAssertEqual(false, block["new_address"] as? Bool)

        let unblocked = await zone.unblock(blocked)
        XCTAssertEqual("Next door unblocked", unblocked)
        XCTAssertEqual(Self.bearer, sent("DELETE", "/api/friend-houses/blocked/\(smiths)")?.value(forHTTPHeaderField: "Authorization"))
        XCTAssertNil(zone.problem)
        XCTAssertFalse(zone.busy)
    }

    @MainActor
    func testARefusalIsSaidNotToasted() async throws {
        let fh = FhHouse()
        let zone = FriendHousesAdminModel(admin: await signedIn(fh))
        await zone.load(viaAct: false)
        fh.answer("POST", "/api/friend-houses/requests/r1/decline", #"{"detail":"That request has already gone."}"#, status: 404)
        let said = await zone.decline(try XCTUnwrap(zone.requests?.first))
        XCTAssertNil(said)
        XCTAssertEqual("That request has already gone.", zone.problem)
    }
}
