import XCTest
@testable import DomovoiKit

/// Home's four writes against a server that is not a server: what each one
/// sends, what it does with the answer, and the two guards that exist because
/// these controls touch other people's rooms and other people's timers.
///
/// **``testADoubleTapOnCancelSendsOneRequest()`` is the sixth bug-shaped
/// test** of this batch (the other five are in `HomeSectionRulesTests`): a
/// second DELETE arrives after the server has already deleted the timer, so
/// it 404s, so it toasts `that one already finished` on top of the correct
/// `cancelled pasta timer` and tells the person the opposite of what
/// happened.
///
/// Android's nearest equivalent is `HomeApiTest.kt` against `MockWebServer`;
/// this is that plus the two guards, which on Android live in the screen and
/// have no test at all (map §1.9).
@MainActor
final class HomeWritesTests: XCTestCase {

    private let address = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private func client(token: String? = "device-token") -> DomovoiClient {
        let address = address
        return DomovoiClient(
            book: ServerBook(activeServer: { address }, tokenForServer: { _ in token }),
            gate: AdmitsAnyServer(),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session(timeout: 2)
        )
    }

    private var paths: [String] {
        StubURLProtocol.recorded.compactMap { $0.url?.path }
    }

    // MARK: - one room

    /// The room is a person's words in a **path segment**, so a space is
    /// `%20` and never `+` — Java's `URLEncoder` is a form encoder and the
    /// Kotlin fixes its `+` by hand (`HomeIo.kt:90`).
    func testTheRoomPathIsPercentEncodedWithNoPluses() {
        XCTAssertEqual("front%20room", encodedRoomPathSegment("front room"))
        XCTAssertEqual("Mum%27s%20office", encodedRoomPathSegment("Mum's office"))
        XCTAssertEqual("a%2Fb", encodedRoomPathSegment("a/b"))
        XCTAssertEqual("a%2Bb", encodedRoomPathSegment("a+b"))
        XCTAssertEqual("caf%C3%A9", encodedRoomPathSegment("café"))
        XCTAssertEqual("kitchen", encodedRoomPathSegment("kitchen"))
        XCTAssertEqual("a-b_c.d~e", encodedRoomPathSegment("a-b_c.d~e"), "unreserved characters are left alone")
        XCTAssertFalse(encodedRoomPathSegment("front room").contains("+"))
    }

    func testTheThreeTransportVerbsPostToTheMusicRoutes() async throws {
        StubURLProtocol.dispatch { _ in .json("{}") }
        let client = client()
        for verb in ["pause", "resume", "stop"] {
            try await HomeApi.roomAction(client, room: "front room", verb: verb)
        }
        XCTAssertEqual(
            [
                "/api/music/pause/front%20room",
                "/api/music/resume/front%20room",
                "/api/music/stop/front%20room",
            ],
            StubURLProtocol.recorded.compactMap { $0.url?.absoluteString.replacingOccurrences(of: address, with: "") }
        )
        XCTAssertEqual(["POST", "POST", "POST"], StubURLProtocol.recorded.map { $0.httpMethod ?? "" })
    }

    /// A refusal raises the pairing screen **the ordinary way**: these are
    /// device-tier writes and they go through the tracked verbs, so nothing
    /// here inspects or re-raises pairing itself.
    func testARefusedWriteRaisesThePairingScreenThroughTheClient() async {
        StubURLProtocol.dispatch { _ in .json("X-Device-Token or admin session required", status: 401) }
        let client = client()
        XCTAssertFalse(client.pairingRequired)
        do {
            try await HomeApi.roomAction(client, room: "kitchen", verb: "pause")
            XCTFail("a 401 is a failure")
        } catch {
            XCTAssertEqual(401, error.failure?.status)
        }
        XCTAssertTrue(client.pairingRequired, "part 1's path, not a second one")
    }

    // MARK: - stop all

    func testStopAllSendsOnePostPerRoomAndReportsEachOne() async {
        StubURLProtocol.dispatch { request in
            (request.url?.path ?? "").hasSuffix("/shed")
                ? .json(#"{"detail":"no such room"}"#, status: 404)
                : .json("{}")
        }
        let outcome = await HomeApi.stopRooms(client(), rooms: ["kitchen", "office", "shed"])
        XCTAssertEqual(["kitchen", "office"], outcome.stopped)
        XCTAssertEqual(["shed"], outcome.failed.map(\.room))
        XCTAssertEqual(3, StubURLProtocol.requestCount, "one POST per room, and no retry")
        XCTAssertEqual(
            ["/api/music/stop/kitchen", "/api/music/stop/office", "/api/music/stop/shed"],
            paths.sorted()
        )
    }

    func testTheStopAllToastSaysHowManyOfHowMany() async {
        XCTAssertEqual(
            "stopped 3 rooms",
            stopAllToast(total: 3, outcome: StopOutcome(stopped: ["a", "b", "c"]))
        )
        XCTAssertEqual(
            "stopped 1 room",
            stopAllToast(total: 1, outcome: StopOutcome(stopped: ["a"])),
            "the toast's plural is built, unlike the button's"
        )
        let refused = DomovoiError.httpError(ApiFailure(status: 500, message: "500: boom"))
        XCTAssertEqual(
            "stopped 2 of 3 rooms · stop failed: 500: boom",
            stopAllToast(total: 3, outcome: StopOutcome(stopped: ["a", "b"], failed: [("c", refused)]))
        )
        // Everything failed: the server's own words, because "stopped 0 of 3"
        // is a sentence about success.
        XCTAssertEqual(
            "stop failed: 500: boom",
            stopAllToast(total: 1, outcome: StopOutcome(failed: [("c", refused)]))
        )
    }

    // MARK: - the cancel

    func testACancelSendsADeleteAndSaysWhatItCancelled() async {
        StubURLProtocol.dispatch { _ in .json("{}") }
        let timer = HomeTimer(id: 42, label: "pasta")
        let outcome = await HomeApi.cancelTimer(client(), timer: timer, shared: false)
        XCTAssertEqual(.cancelled("cancelled pasta timer"), outcome)
        XCTAssertEqual(["/api/timers/42"], paths)
        XCTAssertEqual(["DELETE"], StubURLProtocol.recorded.map { $0.httpMethod ?? "" })
    }

    /// A 404 is **not** a failure: it means the timer fired, or somebody else
    /// cancelled it, between the read and the tap.
    func testAFourOhFourMeansItAlreadyFinished() async {
        StubURLProtocol.dispatch { _ in .json(#"{"detail":"gone"}"#, status: 404) }
        let outcome = await HomeApi.cancelTimer(client(), timer: HomeTimer(id: 42), shared: false)
        XCTAssertEqual(.gone("that one already finished"), outcome)
    }

    func testAnyOtherFailureQuotesTheServer() async {
        StubURLProtocol.dispatch { _ in .json(#"{"detail":"boom"}"#, status: 500) }
        let outcome = await HomeApi.cancelTimer(client(), timer: HomeTimer(id: 42), shared: false)
        guard case .failed(let toast) = outcome else { return XCTFail("a 500 is a failure, not a 'gone'") }
        XCTAssertTrue(toast.hasPrefix("cancel failed: "), toast)
    }

    /// A shared screen's cancel toast names no reminder's words.
    func testTheCancelToastOfAReminderIsJustTheWordReminder() async {
        StubURLProtocol.dispatch { _ in .json("{}") }
        let reminder = HomeTimer(id: 7, message: "take the tablets", roomID: "office", isReminder: true)
        let outcome = await HomeApi.cancelTimer(client(), timer: reminder, shared: true)
        XCTAssertEqual(.cancelled("cancelled reminder"), outcome)
    }

    /// **The double tap sends one request.**
    ///
    /// The guard and the optimistic mark are one call
    /// (``TimerBook/beginCancel(_:)``), so a caller cannot write the first and
    /// forget the second. The second tap is refused *before* a request is
    /// made, which is the half that matters: a second DELETE would 404 and
    /// toast `that one already finished` over the correct `cancelled pasta
    /// timer`.
    func testADoubleTapOnCancelSendsOneRequest() async {
        StubURLProtocol.dispatch { _ in .json("{}") }
        let book = TimerBook()
        let timer = HomeTimer(id: 42, label: "pasta")
        let client = client()

        // The first tap claims it, and marks it cancelled before the DELETE
        // has even left.
        XCTAssertTrue(book.beginCancel(42))
        XCTAssertTrue(book.cancelled.contains(42), "optimistic: the row stops reading done at the tap")
        XCTAssertTrue(book.isCancelling(42))

        // The second tap, while the first is still in flight, sends nothing.
        XCTAssertFalse(book.beginCancel(42), "a second tap must not send a second request")

        let outcome = await HomeApi.cancelTimer(client, timer: timer, shared: false)
        book.settleCancel(42, outcome: outcome)

        XCTAssertEqual(1, StubURLProtocol.requestCount, "one DELETE, one toast")
        XCTAssertEqual(.cancelled("cancelled pasta timer"), outcome)
        XCTAssertTrue(book.cancelled.contains(42), "the server confirmed, so the mark stays")
        XCTAssertFalse(book.isCancelling(42))

        // And the tap after it settles is allowed again — the guard is
        // single-flight, not once-ever.
        XCTAssertTrue(book.beginCancel(42))
    }

    /// The optimistic mark is **taken back** unless the outcome was
    /// `Cancelled`, so a timer that in fact fired can still show its `done ·`
    /// line.
    func testTheOptimisticMarkIsTakenBackOnAnythingButAConfirmation() {
        for outcome in [
            CancelOutcome.gone("that one already finished"),
            CancelOutcome.failed("cancel failed: 500: boom"),
        ] {
            let book = TimerBook()
            XCTAssertTrue(book.beginCancel(42))
            book.settleCancel(42, outcome: outcome)
            XCTAssertFalse(book.cancelled.contains(42), "\(outcome) is not a confirmation")
            XCTAssertFalse(book.isCancelling(42))
        }
    }

    // MARK: - announce

    func testAnnounceSendsTheMessageAndReadsWhoTookIt() async throws {
        StubURLProtocol.dispatch { _ in .json(#"{"announced_to":["kitchen","office"]}"#) }
        let delivered = try await HomeApi.announceAll(client(), message: "tea is ready")
        XCTAssertEqual(["kitchen", "office"], delivered)
        XCTAssertEqual(["/api/satellites/announce-all"], paths)
        let body = StubURLProtocol.recorded.first?.httpBody ?? Data()
        XCTAssertEqual(
            ["message": "tea is ready"],
            try JSONSerialization.jsonObject(with: body) as? [String: String]
        )
    }

    /// **The toast reads the rooms that took it, not the 200**: "a 200 can
    /// still mean dead connections under the server's active-sessions map".
    func testTheAnnounceToastDoesNotTrustTheTwoHundred() async throws {
        StubURLProtocol.dispatch { _ in .json(#"{"announced_to":[]}"#) }
        let delivered = try await HomeApi.announceAll(client(), message: "tea")
        XCTAssertTrue(delivered.isEmpty, "a successful POST, and nobody heard it")
        XCTAssertEqual(
            "broadcast queued but no rooms accepted it (dead connections?)",
            announceToast(delivered: delivered, onlineCount: 3)
        )
    }

    func testTheThreeAnnounceToasts() {
        XCTAssertEqual(
            "broadcast queued but no rooms accepted it (dead connections?)",
            announceToast(delivered: [], onlineCount: 0)
        )
        XCTAssertEqual(
            "broadcast partial — 2/3 reached (kitchen, office)",
            announceToast(delivered: ["kitchen", "office"], onlineCount: 3)
        )
        // `broadcasted`, not `broadcast` — the Kotlin's word.
        XCTAssertEqual(
            "broadcasted to 3 rooms",
            announceToast(delivered: ["kitchen", "office", "hall"], onlineCount: 3)
        )
        XCTAssertEqual("broadcasted to 1 room", announceToast(delivered: ["kitchen"], onlineCount: 1))
        // More took it than were counted online: still the plain form, not a
        // "partial" reading 4/3.
        XCTAssertEqual(
            "broadcasted to 4 rooms",
            announceToast(delivered: ["a", "b", "c", "d"], onlineCount: 3)
        )
    }

    func testAnEmptyAnnounceAnswerDecodesRatherThanThrowing() throws {
        let decoded = try JSONDecoder().decode(HomeAnnounceResult.self, from: Data("{}".utf8))
        XCTAssertEqual([], decoded.announcedTo, "an older server that names nobody is not a failed read")
    }
}
