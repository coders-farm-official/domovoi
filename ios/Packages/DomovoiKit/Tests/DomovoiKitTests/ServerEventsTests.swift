import Foundation
import XCTest
@testable import DomovoiKit

/// The SSE framing, and the stream the client opens through the same gate
/// everything else goes through.
///
/// ``ServerEventReader`` is where the mistakes in a streaming reader live, and
/// a socket cannot be unit-tested, so almost everything here feeds it lines
/// directly. The three tests at the end drive the real
/// ``DomovoiClient/events(method:path:body:)`` through ``StubURLProtocol``,
/// because the properties they are about — the token on the wire, a refusal
/// read before the first byte, the delegate the request carries — are not
/// properties of the framing.
final class ServerEventsTests: XCTestCase {

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the reader

    /// Feed `lines` and collect what came out.
    private func read(_ lines: [String]) -> [ServerEvent] {
        var reader = ServerEventReader()
        return lines.compactMap { reader.take($0) }
    }

    /// Feed a whole body the way `AsyncLineSequence` would, and take the
    /// trailing event the way ``DomovoiClient/events(method:path:body:)``
    /// does.
    private func readBody(_ body: String) -> [ServerEvent] {
        var reader = ServerEventReader()
        var out: [ServerEvent] = []
        for line in body.components(separatedBy: "\n") {
            if let event = reader.take(line) { out.append(event) }
        }
        if let last = reader.take("") { out.append(last) }
        return out
    }

    func testOneEventIsItsNameAndItsData() {
        XCTAssertEqual(
            [ServerEvent(name: "delta", data: #"{"text":"hello"}"#)],
            read(["event: delta", #"data: {"text":"hello"}"#, ""])
        )
    }

    /// **Two `data:` lines are one event, joined with a newline.**
    ///
    /// The specification says a newline; Android concatenates with nothing
    /// between (`ChatScreen.kt:376`). For the one-line JSON this server sends
    /// the two are identical, and for anything else the spec's answer is the
    /// one that parses — which is the divergence `ServerEvents.swift` records,
    /// pinned here so it cannot drift back.
    func testAnEventSplitOverTwoDataLinesIsJoinedWithANewline() {
        let events = read(["event: delta", "data: first", "data: second", ""])
        XCTAssertEqual([ServerEvent(name: "delta", data: "first\nsecond")], events)
    }

    /// `data:` with **no space** after the colon, which the specification
    /// requires and Android does not accept: its `startsWith("data: ")` would
    /// read an event whose data is empty, and the reply would stop.
    func testDataWithNoSpaceAfterTheColonIsAccepted() {
        XCTAssertEqual(
            [ServerEvent(name: "delta", data: #"{"text":"x"}"#)],
            read(["event:delta", #"data:{"text":"x"}"#, ""])
        )
    }

    /// Exactly **one** leading space is removed, so a delta that is itself
    /// indented keeps its indentation.
    func testOnlyOneLeadingSpaceIsRemovedFromAValue() {
        XCTAssertEqual(
            [ServerEvent(name: "delta", data: "  indented")],
            read(["event: delta", "data:   indented", ""])
        )
    }

    /// A comment line — how a server keeps a connection warm through a proxy
    /// that times idle ones out. It must not end the event it sits inside.
    func testACommentLineIsIgnoredAndDoesNotEndAnEvent() {
        let events = read(["event: delta", ": keep-alive", "data: one", ": keep-alive", ""])
        XCTAssertEqual([ServerEvent(name: "delta", data: "one")], events)
    }

    /// A comment arriving between two events delivers nothing of its own.
    func testACommentOnItsOwnIsNoEvent() {
        XCTAssertTrue(read([": keep-alive", ":", ": anything at all"]).isEmpty)
    }

    /// **A blank line with nothing gathered delivers nothing — and still
    /// clears a pending `event:` name.**
    ///
    /// Both halves matter. A stream that opens with a blank line, or sends two
    /// in a row, must not hand a caller an empty event it would read as a reply
    /// saying nothing. And `event: delta` followed by a blank line is not a
    /// claim about the *next* payload: without the reset, an unnamed event
    /// after it would arrive named `delta` and be applied as words.
    func testABlankLineWithNothingGatheredDeliversNothingAndForgetsTheName() {
        var reader = ServerEventReader()
        XCTAssertNil(reader.take(""))
        XCTAssertNil(reader.take("event: delta"))
        XCTAssertNil(reader.take(""), "nothing was gathered, so there is no event")
        XCTAssertNil(reader.take(""))
        // The name `delta` must not have survived that blank line.
        let next = reader.take("data: orphan")
        XCTAssertNil(next)
        XCTAssertEqual(ServerEvent(name: ServerEventReader.unnamed, data: "orphan"), reader.take(""))
    }

    /// An event that names nothing is `message`, which is what the
    /// specification calls it — and which this app treats as a server it does
    /// not understand rather than guessing.
    func testAnEventWithNoNameIsCalledMessage() {
        XCTAssertEqual("message", ServerEventReader.unnamed)
        XCTAssertEqual(
            [ServerEvent(name: "message", data: "x")],
            read(["data: x", ""])
        )
    }

    /// CRLF: the CR must not end up inside the payload, where it would make
    /// `{"text":"hi"}\r` fail to decode.
    func testCRLFLeavesNoCarriageReturnInThePayload() {
        let events = readBody("event: delta\r\ndata: {\"text\":\"hi\"}\r\n\r\n")
        XCTAssertEqual([ServerEvent(name: "delta", data: #"{"text":"hi"}"#)], events)
        let delta: ChatDelta? = events.first?.payload()
        XCTAssertEqual("hi", delta?.text)
    }

    /// **The last event arrives when the stream ends without its blank line.**
    ///
    /// A server that closes after `data: {…}` with no trailing newline would
    /// otherwise lose the `done` row, and with it the reply's id, model and
    /// `retryable` — so nothing could be retried or shared.
    func testTheLastEventArrivesWhenTheStreamEndsWithoutItsBlankLine() {
        var reader = ServerEventReader()
        XCTAssertNil(reader.take("event: done"))
        XCTAssertNil(reader.take(#"data: {"id":7}"#))
        XCTAssertEqual(ServerEvent(name: "done", data: #"{"id":7}"#), reader.take(""))
        // And asking again gives nothing: the event is not delivered twice.
        XCTAssertNil(reader.take(""))
    }

    /// `id:` and `retry:` are ignored — nothing here reconnects, so there is
    /// nothing to remember. They must not be mistaken for data.
    func testIdAndRetryAreIgnored() {
        let events = read([
            "id: 42",
            "retry: 3000",
            "event: delta",
            "id: 43",
            "data: words",
            "retry: 1000",
            "",
        ])
        XCTAssertEqual([ServerEvent(name: "delta", data: "words")], events)
    }

    /// A field name this reader does not know, and a line with no colon at
    /// all: ignored rather than guessed at, and neither ends an event.
    func testAnUnknownFieldAndALineWithNoColonAreIgnored() {
        let events = read(["event: delta", "nonsense", "fruit: banana", "data: words", ""])
        XCTAssertEqual([ServerEvent(name: "delta", data: "words")], events)
    }

    /// The name is trimmed, because `event: delta ` is still `delta`.
    func testTheEventNameIsTrimmed() {
        XCTAssertEqual([ServerEvent(name: "delta", data: "x")], read(["event:  delta  ", "data: x", ""]))
    }

    /// Several events in one body, in order, each ending at its blank line.
    func testAWholeReplyReadsAsItsEventsInOrder() {
        let body = """
        event: delta
        data: {"text":"one "}

        : keep-alive

        event: delta
        data: {"text":"two"}

        event: done
        data: {"id":11,"role":"assistant","content":"one two","retryable":true}

        """
        let events = readBody(body)
        XCTAssertEqual(["delta", "delta", "done"], events.map(\.name))
        let row: ChatMessage? = events.last?.payload()
        XCTAssertEqual(11, row?.id)
        XCTAssertEqual(true, row?.retryable)
    }

    // MARK: - payload(as:)

    /// **A payload this app cannot read is nil, not a throw.**
    ///
    /// The server sends `job`, `progress`, `status` and `lookup_permission`
    /// for features this app has not built, and a reply that stopped halfway
    /// because one of them would not decode into the shape a caller named is a
    /// far worse bug than an ignored event.
    func testPayloadIsNilRatherThanThrowingForAShapeItDoesNotKnow() {
        let event = ServerEvent(name: "job", data: #"{"state":"running","parts":4}"#)
        let delta: ChatDelta? = event.payload()
        // `ChatDelta` reads a missing `text` as empty, so this one decodes;
        // the shape that genuinely will not is a different kind.
        XCTAssertEqual("", delta?.text)
        let wrongKind: [String]? = event.payload()
        XCTAssertNil(wrongKind, "an object is not an array of strings")
        let notJSON = ServerEvent(name: "done", data: "<html>a captive portal</html>")
        let row: ChatMessage? = notJSON.payload()
        XCTAssertNil(row)
        // And not even valid UTF-8 shapes do not throw out of the stream.
        XCTAssertNil(ServerEvent(name: "done", data: "").payload(as: ChatMessage.self))
    }

    // MARK: - the stream the client opens

    private let server = "http://10.0.0.42:6369"

    private func client(_ token: String? = "household-abc") -> DomovoiClient {
        let store = InMemoryShellStore(serverURL: server, token: token)
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 2)
        )
        return container.client
    }

    private static func sse(_ body: String) -> StubURLProtocol.Answer {
        StubURLProtocol.Answer.json(body, headers: ["Content-Type": "text/event-stream"])
    }

    /// Answer `/api/health` as the pinned server, and everything else with
    /// `answer`.
    ///
    /// **Not `enqueue`**: the identity gate probes `/api/health` before the
    /// first request of a session, so a single queued answer is eaten by the
    /// probe and the stream then gets `{}` — the gate rejects it, the test
    /// fails on an identity error, and the thing it meant to assert is never
    /// reached. Measured here on the first run of this file.
    private static func serveHealth(then answer: @Sendable @escaping () -> StubURLProtocol.Answer) {
        StubURLProtocol.dispatch { request in
            guard let url = request.url, let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            guard parts.path == "/api/health" else { return answer() }
            let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
            return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
        }
    }

    /// The whole round trip: the request says it wants an event stream, carries
    /// the household token, and the events come out in order.
    ///
    /// The token is the point. Android's SSE call builds its own OkHttp request
    /// and is policed by the interceptors on the shared client
    /// (`ApiClient.kt:137`); this goes through `prepare` and so carries the
    /// per-request ``RedirectDelegate``, which is what makes a streamed hop
    /// identical to a read one. A stream reaching for `URLSession.shared`
    /// would pass every other assertion in this file and fail this one.
    func testAStreamCarriesTheTokenAndDeliversItsEventsInOrder() async throws {
        Self.serveHealth(then: {
            Self.sse("""
            event: delta
            data: {"text":"hel"}

            event: delta
            data: {"text":"lo"}

            event: done
            data: {"id":3,"role":"assistant","content":"hello"}

            """)
        })
        let events = try await client().events(
            method: "POST",
            path: ChatPath.messages(5),
            body: Data(#"{"content":"hi"}"#.utf8)
        )
        var seen: [ServerEvent] = []
        for try await event in events { seen.append(event) }
        XCTAssertEqual(["delta", "delta", "done"], seen.map(\.name))
        // The stream's own request, found by its path: the identity gate's
        // `/api/health` probe is recorded ahead of it.
        let sent = try XCTUnwrap(StubURLProtocol.recorded.first { $0.url?.path == ChatPath.messages(5) })
        XCTAssertEqual("household-abc", sent.value(forHTTPHeaderField: "X-Device-Token"))
        XCTAssertEqual("text/event-stream", sent.value(forHTTPHeaderField: "Accept"))
        XCTAssertEqual("DomovoiApp", sent.value(forHTTPHeaderField: "X-Requested-With"))
        XCTAssertEqual("POST", sent.httpMethod)
    }

    /// A refusal is thrown by `events(…)` itself — **before** the stream — so
    /// the caller can word it. And the body it carries is what
    /// ``isAiOffRefusal(_:)`` reads.
    func testARefusalIsThrownBeforeTheStreamAndKeepsItsWords() async {
        Self.serveHealth(then: {
            .json(
                #"{"detail":"AI features are turned off for this home"}"#,
                status: 409,
                headers: ["X-Domovoi-Refusal": "ai-off"]
            )
        })
        do {
            _ = try await client().events(method: "POST", path: ChatPath.messages(5))
            XCTFail("a 409 is not a stream")
        } catch let trouble {
            XCTAssertTrue(isAiOffRefusal(trouble), "the refusal header reached the error")
            XCTAssertEqual("ai-off", trouble.failure?.refusal)
            XCTAssertEqual(aiChatOffText, sendFailureLine(trouble))
        }
    }

    /// **A stream a gate refuses never leaves the phone.**
    ///
    /// The one assertion that proves `events(…)` goes through `prepare` rather
    /// than opening its own socket: a gate that admits no server holds the
    /// request, and `requestCount` is zero. A stream built on
    /// `URLSession.shared` would answer every other test in this file and
    /// fail this one.
    func testAStreamAGateRefusesNeverLeavesThePhone() async {
        let held = DomovoiClient(
            book: .fixed(server: server, token: "household-abc"),
            gate: AdmitsNoServer(),
            lock: HouseholdLock(),
            configuration: StubURLProtocol.session(timeout: 2)
        )
        do {
            _ = try await held.events(method: "POST", path: ChatPath.messages(5))
            XCTFail("a server the gate refuses is not streamed to")
        } catch let trouble {
            XCTAssertEqual(DomovoiError.identityHeld("this client admits no server"), trouble)
            XCTAssertEqual(0, StubURLProtocol.requestCount, "nothing left the phone")
        }
    }
}
