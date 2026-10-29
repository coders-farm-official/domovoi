import XCTest

@testable import DomovoiKit

/// This phone's row on the server.
///
/// The register body is the part that matters most: `owner_person_id` rides
/// along on every register, as a number or as `null`, and is never left out —
/// the route documents that a field left out keeps the stored value, so
/// omitting it would keep a stale owner there for ever. A port of
/// `android/.../net/DeviceRegistryTest.kt`, with the reply and the cadence
/// added.
final class DeviceRegistryTests: XCTestCase {

    private func body(owner: String?) throws -> [String: Any] {
        let data = try DeviceRegistry.registerPayload(
            deviceID: "ios-1a2b3c4d5e6f7a8b",
            name: "iPhone",
            userAgent: "iOS 18.3; iPhone",
            ownerPersonID: owner
        )
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    func testTheBodyIsWhatTheRouteDocuments() throws {
        let sent = try body(owner: "3")
        XCTAssertEqual(sent["device_id"] as? String, "ios-1a2b3c4d5e6f7a8b")
        XCTAssertEqual(sent["name"] as? String, "iPhone")
        XCTAssertEqual(sent["platform"] as? String, "ios")
        XCTAssertEqual(sent["user_agent"] as? String, "iOS 18.3; iPhone")
        XCTAssertEqual(sent["owner_person_id"] as? Int, 3)
        XCTAssertEqual(sent.count, 5, "and nothing else")
    }

    func testNobodyClearsTheOwnerAndTheFieldIsStillThere() throws {
        let sent = try body(owner: nil)
        XCTAssertTrue(sent.keys.contains("owner_person_id"), "sent, not left out")
        XCTAssertTrue(sent["owner_person_id"] is NSNull)
    }

    func testAnIdThatIsNotAPersonClearsIt() throws {
        for raw in ["abc", "0", "", "-1", "1.5"] {
            XCTAssertTrue(try body(owner: raw)["owner_person_id"] is NSNull, raw)
        }
        XCTAssertEqual(try body(owner: " 12 ")["owner_person_id"] as? Int, 12)
        XCTAssertEqual(DeviceRegistry.personID("7"), 7)
        XCTAssertNil(DeviceRegistry.personID(nil))
    }

    /// `platform` is `"ios"`, honestly, even though the server mislabels an
    /// iPhone in two places until it learns about this app (see the comment
    /// on ``DeviceRegistry/platform`` and the batch report).
    func testThePlatformIsIos() {
        XCTAssertEqual(DeviceRegistry.platform, "ios")
    }

    func testTheRenameBodyIsJustTheName() throws {
        let sent = try XCTUnwrap(
            JSONSerialization.jsonObject(with: try DeviceRegistry.renamePayload(name: "Kitchen tablet")) as? [String: Any]
        )
        XCTAssertEqual(sent as? [String: String], ["name": "Kitchen tablet"])
    }

    // ---- the reply -----------------------------------------------------------

    func testTheReplyDecodesIncludingTheSharedScreenFlag() throws {
        let row = try JSONDecoder().decode(DeviceRegistry.DeviceRow.self, from: Data(#"""
            {"device_id":"ios-1a2b","name":"Kitchen","platform":"ios",
             "user_agent":"iOS 18.3; iPhone","first_seen_at":"2026-10-01T00:00:00Z",
             "last_seen_at":"2026-10-28T12:00:00Z","shared_screen":true}
        """#.utf8))
        XCTAssertEqual(row.deviceID, "ios-1a2b")
        XCTAssertEqual(row.name, "Kitchen")
        XCTAssertEqual(row.lastSeenAt, "2026-10-28T12:00:00Z")
        XCTAssertTrue(DeviceRegistry.sharedScreenAnswer(row))
    }

    /// A row with no `shared_screen` comes from a web backend older than
    /// V014, which cannot mark a device — so it is "no", never "unknown".
    func testASharedScreenAnswerAnOlderServerCannotGiveIsNo() throws {
        let row = try JSONDecoder().decode(
            DeviceRegistry.DeviceRow.self,
            from: Data(#"{"device_id":"ios-1a2b","name":"Kitchen"}"#.utf8)
        )
        XCTAssertNil(row.sharedScreen)
        XCTAssertFalse(DeviceRegistry.sharedScreenAnswer(row))
    }

    /// Until the server has answered, a PAIRED install counts as shared (it
    /// may well be the kitchen tablet) and an unpaired one never does (it
    /// cannot learn, because its registration would be refused).
    func testWhatThisInstallIsTreatedAsBeforeTheServerAnswers() {
        XCTAssertTrue(DeviceRegistry.isSharedScreen(answer: nil, paired: true))
        XCTAssertFalse(DeviceRegistry.isSharedScreen(answer: nil, paired: false))
        XCTAssertFalse(DeviceRegistry.isSharedScreen(answer: false, paired: true), "the server's word wins")
        XCTAssertTrue(DeviceRegistry.isSharedScreen(answer: true, paired: false))
    }

    // ---- when to ask ---------------------------------------------------------

    func testAnUnpairedPhoneWithNoSocketIsNotAskedToRegister() {
        XCTAssertFalse(DeviceRegistry.canRegister(connected: false, paired: false))
        XCTAssertTrue(DeviceRegistry.canRegister(connected: true, paired: false), "the server evidently lets it in")
        XCTAssertTrue(
            DeviceRegistry.canRegister(connected: false, paired: true),
            "a paired phone must be able to get a shared-screen answer with the socket down"
        )
    }

    func testTheCadenceRule() {
        XCTAssertEqual(DeviceRegistry.reaskEveryMs, 120_000)
        XCTAssertEqual(DeviceRegistry.reaskGapMs, 15_000)

        func should(_ reason: DeviceRegistry.ReaskReason, since: Int64?) -> Bool {
            DeviceRegistry.shouldRegister(
                now: 1_000_000,
                lastAskedAt: since.map { 1_000_000 - $0 },
                reason: reason,
                connected: true,
                paired: true
            )
        }
        // Never asked: ask, whatever the reason.
        XCTAssertTrue(should(.changed, since: nil))
        XCTAssertTrue(should(.onScreenTick, since: nil))
        // Something changed: ask, but not twice inside the gap.
        XCTAssertFalse(should(.changed, since: 14_999))
        XCTAssertTrue(should(.changed, since: 15_000))
        // The on-screen poll: every couple of minutes.
        XCTAssertFalse(should(.onScreenTick, since: 119_999))
        XCTAssertTrue(should(.onScreenTick, since: 120_000))
        // And never at all when there is no business asking.
        XCTAssertFalse(DeviceRegistry.shouldRegister(
            now: 1_000_000, lastAskedAt: nil, reason: .changed, connected: false, paired: false
        ))
    }

    // ---- the name and the user agent ------------------------------------------

    func testTheNameIsASeedAPersonWouldRecognise() {
        XCTAssertEqual(DeviceRegistry.suggestedDeviceName(model: "iPhone"), "iPhone")
        XCTAssertEqual(DeviceRegistry.suggestedDeviceName(model: "  iPad  "), "iPad")
        XCTAssertEqual(DeviceRegistry.suggestedDeviceName(model: ""), "iPhone")
        XCTAssertEqual(DeviceRegistry.userAgent(systemVersion: "18.3", model: "iPhone"), "iOS 18.3; iPhone")
    }

    // ---- the device id --------------------------------------------------------

    func testAMintedIdIsOursAndTheServerWouldAcceptIt() {
        let id = DeviceIdentity.mint()
        XCTAssertTrue(id.hasPrefix("ios-"))
        XCTAssertEqual(id.count, 20, "`ios-` plus 64 bits of hex")
        XCTAssertTrue(DeviceIdentity.isWellFormed(id))
    }

    /// 64 bits rather than Android's 16: two phones in one house must not
    /// share a row, a name and an admin block.
    func testMintingTwiceGivesTwoIds() {
        let ids = Set((0..<200).map { _ in DeviceIdentity.mint() })
        XCTAssertEqual(ids.count, 200)
    }

    func testTheWellFormedRuleIsTheServersOwn() {
        // ^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$
        XCTAssertTrue(DeviceIdentity.isWellFormed("abc"))
        XCTAssertFalse(DeviceIdentity.isWellFormed("ab"), "three characters at least")
        XCTAssertTrue(DeviceIdentity.isWellFormed("a" + String(repeating: "b", count: 63)))
        XCTAssertFalse(DeviceIdentity.isWellFormed("a" + String(repeating: "b", count: 64)))
        XCTAssertFalse(DeviceIdentity.isWellFormed("-abc"), "it has to start with a letter or a digit")
        XCTAssertFalse(DeviceIdentity.isWellFormed(".abc"))
        XCTAssertTrue(DeviceIdentity.isWellFormed("a.b_c:d-e"))
        XCTAssertFalse(DeviceIdentity.isWellFormed("a b c"))
        XCTAssertFalse(DeviceIdentity.isWellFormed("añbc"))
        XCTAssertFalse(DeviceIdentity.isWellFormed(""))
    }

    func testTheIdIsMintedOnceAndThenRemembered() {
        let store = Box()
        let first = DeviceIdentity.stable(in: store)
        XCTAssertEqual(DeviceIdentity.stable(in: store), first)
        XCTAssertEqual(store.writes, 1)
    }

    /// A stored id the server would refuse is replaced rather than sent: a
    /// `400` the person cannot act on is worse than a new row.
    func testAStoredIdTheServerWouldRefuseIsReplaced() {
        let store = Box(saved: "a b c")
        let id = DeviceIdentity.stable(in: store)
        XCTAssertTrue(DeviceIdentity.isWellFormed(id))
        XCTAssertNotEqual(id, "a b c")
    }

    // ---- the calls ------------------------------------------------------------

    private func client() -> DomovoiClient {
        DomovoiClient(
            book: .fixed(server: "http://10.0.0.9:6369", token: "household-abc"),
            configuration: StubURLProtocol.session(timeout: 5)
        )
    }

    func testRegisteringPostsTheBodyAndReadsTheRowBack() async throws {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(.json(#"{"device_id":"ios-1a2b","name":"Kitchen","shared_screen":true}"#))
        let row = await DeviceRegistry.register(
            with: client(),
            deviceID: "ios-1a2b",
            name: "iPhone",
            userAgent: "iOS 18.3; iPhone",
            ownerPersonID: "7"
        )
        XCTAssertEqual(row?.deviceID, "ios-1a2b")
        XCTAssertEqual(row?.name, "Kitchen", "the name the server already had, not the seed we sent")
        XCTAssertTrue(DeviceRegistry.sharedScreenAnswer(try XCTUnwrap(row)))
        let sent = try XCTUnwrap(StubURLProtocol.recorded.first)
        XCTAssertEqual(sent.httpMethod, "POST")
        XCTAssertEqual(sent.url?.path, "/api/devices/register")
        XCTAssertEqual(sent.value(forHTTPHeaderField: deviceTokenHeader), "household-abc")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: try XCTUnwrap(sent.httpBody)) as? [String: Any])
        XCTAssertEqual(body["platform"] as? String, "ios")
        XCTAssertEqual(body["owner_person_id"] as? Int, 7)
    }

    /// Android swallows every registration failure: the app works unnamed,
    /// its queue entries just carry no tag.
    func testARegistrationThatFailsIsNotAnError() async {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(.json(#"{"detail":"X-Device-Token or admin session required"}"#, status: 401))
        let row = await DeviceRegistry.register(
            with: client(), deviceID: "ios-1a2b", name: "iPhone", userAgent: "iOS 18.3; iPhone", ownerPersonID: nil
        )
        XCTAssertNil(row)
    }

    /// Rename throws instead, because a person pressed a button and has to
    /// be told.
    func testRenamingThrowsSoTheSettingsPanelCanSayWhatHappened() async {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(.json(#"{"detail":"no such device"}"#, status: 404))
        do {
            _ = try await DeviceRegistry.rename(with: client(), deviceID: "ios-1a2b", name: "Kitchen")
            XCTFail("expected a failure")
        } catch {
            XCTAssertEqual(error.failure?.status, 404)
        }
    }

    func testRenamingPatchesTheDevicesOwnRouteWithItsIdEscaped() async throws {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(.json(#"{"device_id":"a:b","name":"Kitchen"}"#))
        let row = try await DeviceRegistry.rename(with: client(), deviceID: "a:b", name: "Kitchen")
        XCTAssertEqual(row.name, "Kitchen")
        let sent = try XCTUnwrap(StubURLProtocol.recorded.first)
        XCTAssertEqual(sent.httpMethod, "PATCH")
        XCTAssertEqual(sent.url?.absoluteString, "http://10.0.0.9:6369/api/devices/a%3Ab")
        XCTAssertEqual(DeviceRegistry.pathEscaped("a:b/c?d"), "a%3Ab%2Fc%3Fd")
        XCTAssertEqual(DeviceRegistry.pathEscaped("ios-1a2b3c4d5e6f7a8b"), "ios-1a2b3c4d5e6f7a8b", "a well-formed id is untouched")
    }

    /// What `pathEscaped` produces for an id with non-ASCII in it, which is
    /// the half of it that is not decoration: an id is self-asserted, and this
    /// is a path segment.
    ///
    /// It is a characterisation test, not a guard on the safe set, and the
    /// difference is worth stating: it passes under the Unicode-wide
    /// `CharacterSet.alphanumerics` too, because `addingPercentEncoding`
    /// encodes non-ASCII whatever the set says (`PlatformFactsTests` fact 5
    /// measures that). What it does hold is that these ids come out encoded
    /// exactly as Android's `Uri.encode` encodes them.
    func testANonAsciiDeviceIdIsPercentEncodedAsAndroidEncodesIt() {
        XCTAssertEqual(DeviceRegistry.pathEscaped("ios-caf\u{00E9}"), "ios-caf%C3%A9")
        XCTAssertEqual(DeviceRegistry.pathEscaped("ios-\u{0434}"), "ios-%D0%B4")
        XCTAssertEqual(DeviceRegistry.pathEscaped("ios-\u{0663}"), "ios-%D9%A3")
        XCTAssertEqual(
            DeviceRegistry.pathEscaped("aZ09_-!.~'()*"), "aZ09_-!.~'()*",
            "and the whole safe set still goes through untouched"
        )
    }

    private final class Box: DeviceIdentity.Store {
        private struct State: Sendable {
            var saved: String?
            var writes = 0
        }

        private let state: Shared<State>

        init(saved: String? = nil) {
            state = Shared(State(saved: saved))
        }

        var writes: Int { state.current.writes }

        func read() -> String? { state.current.saved }

        func write(_ deviceID: String) {
            state.mutate { state in
                state.saved = deviceID
                state.writes += 1
            }
        }
    }
}
