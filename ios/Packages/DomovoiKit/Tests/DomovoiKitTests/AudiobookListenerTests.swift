import XCTest
@testable import DomovoiKit

/// Whose place a book's position is — D11, the plumbing half.
///
/// Until 2026-11-03 `person_id` was **left out of every request** this app
/// made, which is the server's *unidentified listener* slot: a different row
/// from any person's (`domovoi/spoken_audio.py:145-147`). So a place set on
/// the phone and a place a room's player saved under a recognised person were
/// different rows, and "continue from the kitchen" could not see the one the
/// voice path had written. Kamron's answer 11: "yes, match Android: build the
/// 'listening as' picker."
///
/// The three things a comment cannot carry, and which these cases measure on
/// the **bytes that leave the phone**:
///
/// 1. The listener is on all three routes — the slot read, the save, and the
///    other-places read that "continue from the kitchen" is built on.
/// 2. It is a **number**, and an id the server could not parse is **left out**
///    rather than sent. A 422 on the read is swallowed into "start at the top"
///    (`PlayerController.kt:588`), which is a three-hour book silently
///    resuming at zero — the worst possible failure for this feature.
/// 3. It is read **per request**, not captured when the remote was built, so
///    a person picked in Settings takes effect on the next save rather than on
///    the next launch. Android's shape (`ApiPositionRemote.kt:27`).
@MainActor
final class AudiobookListenerTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the paths and the body

    /// All three position routes carry the listener, as a number.
    func testTheListenerIsOnBothReadsAndTheSave() {
        XCTAssertEqual(
            "/api/audiobooks/7/position?device_id=ios-1&person_id=9",
            AudiobookPath.position(7, deviceID: "ios-1", personID: "9")
        )
        XCTAssertEqual(
            "/api/audiobooks/7/positions?device_id=ios-1&person_id=9",
            AudiobookPath.positions(7, deviceID: "ios-1", personID: "9")
        )
        let body = String(
            decoding: AudiobookPath.saveBody(
                deviceID: "ios-1", positionSec: 120, speed: 1.5, personID: "9"
            ),
            as: UTF8.self
        )
        XCTAssertEqual(#"{"device_id":"ios-1","position_sec":120,"person_id":9,"speed":1.5}"#, body)
        XCTAssertFalse(body.contains("\"9\""), "person_id went as a string, which is a 422")
    }

    /// No listener is the **unidentified** slot: the parameter is absent, not
    /// empty.
    ///
    /// `person_id=` with nothing after it is a *different key* to the server
    /// rather than an absent one, and it is also a 422 on a route that types
    /// it as an int. This is the state every phone is in until somebody opens
    /// the picker, so it is the common case rather than an edge.
    func testNoListenerSendsNoParameterAtAll() {
        XCTAssertFalse(AudiobookPath.position(7, deviceID: "ios-1").contains("person_id"))
        XCTAssertFalse(AudiobookPath.positions(7, deviceID: "ios-1").contains("person_id"))
        XCTAssertFalse(
            String(
                decoding: AudiobookPath.saveBody(deviceID: "ios-1", positionSec: 1, speed: 1),
                as: UTF8.self
            ).contains("person_id")
        )
    }

    /// An id the server cannot read is **left out**, on all three.
    ///
    /// The stored id is a `String?` because Android's is, so anything can be
    /// in it: a blank, a name typed into a field once, a row id from another
    /// house. Sending one would be a 422, and a 422 on the read is swallowed
    /// into "start at the top".
    func testAnIdTheServerCouldNotParseIsLeftOut() {
        for bad in ["", "   ", "kamron", "9.5", "9;drop", "٩"] {
            XCTAssertFalse(
                AudiobookPath.position(7, deviceID: "ios-1", personID: bad).contains("person_id"),
                "the read sent person_id=\(bad), which is a 422 and a book resuming at zero"
            )
            XCTAssertFalse(
                AudiobookPath.positions(7, deviceID: "ios-1", personID: bad).contains("person_id"),
                "the other-places read sent person_id=\(bad)"
            )
            XCTAssertFalse(
                String(
                    decoding: AudiobookPath.saveBody(
                        deviceID: "ios-1", positionSec: 1, speed: 1, personID: bad
                    ),
                    as: UTF8.self
                ).contains("person_id"),
                "the save sent person_id=\(bad), which is a place never saved at all"
            )
        }
    }

    /// A negative id is still a number and goes as one.
    ///
    /// Not a nicety: `Int64("-1")` parses, so leaving this unsaid would make
    /// the reader guess. The server answers no row for it, which is the right
    /// outcome — a phone with a nonsense id reads an empty slot rather than
    /// somebody else's.
    func testANegativeIdIsStillANumber() {
        XCTAssertTrue(
            AudiobookPath.position(7, deviceID: "ios-1", personID: "-1")
                .hasSuffix("&person_id=-1")
        )
    }

    // MARK: - the remote asks at each request

    /// A person picked in Settings reaches the **next** request, with nothing
    /// rebuilt.
    ///
    /// This is the case the closure exists for, and the one a captured
    /// `String?` would fail: the remote is built once per house
    /// (`AudiobooksModel`'s convenience initialiser builds two, one for the
    /// page and one for the sync), so a captured id would make the picker a
    /// control that worked only after a relaunch — the misleading control this
    /// project will not ship.
    func testTheRemoteReadsTheListenerAtEachRequest() async throws {
        let place = SavedStateBox()
        let listeners = PlayerListenerStore(place: place)
        let client = Self.client()
        Self.answerEverything(#"{"position_sec":0,"speed":1.0}"#)

        let remote = try XCTUnwrap(
            ApiBookPositionRemote(
                client: client,
                deviceID: "ios-1",
                server: BookServerKey(address: server, fingerprint: nil),
                listener: { listeners.personID }
            )
        )

        _ = try? await remote.get(7)
        XCTAssertEqual([false], Self.sawPerson(), "the first read already claimed a person")

        listeners.set("9")
        _ = try? await remote.get(7)
        try? await remote.put(7, positionSec: 120, speed: 1)
        XCTAssertEqual(
            [false, true, true], Self.sawPerson(),
            "the read and the save after the pick did not carry the person — "
                + "the listener was captured when the remote was built, so the "
                + "picker works only after a relaunch"
        )

        listeners.set(nil)
        _ = try? await remote.get(7)
        XCTAssertEqual(
            [false, true, true, false], Self.sawPerson(),
            "going back to \"me (this device)\" kept sending the person"
        )
    }

    /// The other-places read — what "continue from the kitchen" is built on —
    /// carries the listener too.
    ///
    /// The route answers rows for **exactly** this `person_id` and no other
    /// (`audiobooks.py:545-552`), so this is the one request where the
    /// listener decides whether the offer appears at all. It was the omission
    /// with the most visible consequence.
    func testContinueFromTheKitchenAsksForTheListenersPlaces() async throws {
        let listeners = PlayerListenerStore(place: SavedStateBox())
        listeners.set("9")
        Self.answerEverything("[]")
        let remote = try XCTUnwrap(
            ApiBookPositionRemote(
                client: Self.client(),
                deviceID: "ios-1",
                server: BookServerKey(address: server, fingerprint: nil),
                listener: { listeners.personID }
            )
        )

        _ = try? await remote.others(7)

        let asked = StubURLProtocol.recorded.compactMap { $0.url?.absoluteString }
            .filter { $0.contains("/positions") }
        // `first`, not `[0]`: a subscript on an empty array is a *fatal*
        // error, which takes the whole test process with it and loses every
        // other case's result — the Swift trap this track has already paid
        // for ([[ios-app-track]]).
        let went = try XCTUnwrap(asked.first, "the other-places read never went out")
        XCTAssertEqual(1, asked.count, "the other-places read was asked \(asked.count) times")
        XCTAssertTrue(
            went.contains("person_id=9"),
            "the other-places read went out as \(went) — without the listener "
                + "the server answers the unidentified one's rows, so a place "
                + "the kitchen saved under a person is invisible"
        )
    }

    /// A phone with no device id gets **no remote at all**, listener or not.
    ///
    /// Both routes require `device_id`, and an empty one is a *different
    /// device* to the server rather than an absent one: a place written under
    /// it would be stranded and a `/positions` read under it would answer this
    /// phone's own rows.
    func testNoDeviceIdStillMeansNoRemote() {
        let listeners = PlayerListenerStore(place: SavedStateBox())
        listeners.set("9")
        XCTAssertNil(
            ApiBookPositionRemote(
                client: Self.client(),
                deviceID: nil,
                server: .none,
                listener: { listeners.personID }
            )
        )
        XCTAssertNil(
            ApiBookPositionRemote(
                client: Self.client(),
                deviceID: "  ",
                server: .none,
                listener: { listeners.personID }
            )
        )
    }

    // MARK: - what the phone's own record deliberately does not carry

    /// The phone's place is **not** keyed by listener, and picking a person
    /// does not lose it.
    ///
    /// The owner's decision of 2026-10-20 is one place per book on the phone,
    /// shared by a streamed book and a saved copy. Keying it by listener as
    /// well would make "where am I in this book" depend on who Settings last
    /// said was listening — so a person picking themselves would find their
    /// book back at the start. What the listener decides is which *server* row
    /// the place goes to, which is `ApiBookPositionRemote`'s job above.
    ///
    /// Asserted here rather than left to `AudiobookPositions.swift`'s comment,
    /// because a comment saying "deliberately absent" is exactly what a later
    /// reader would "fix".
    func testPickingAPersonDoesNotMoveThePhonesOwnPlace() {
        let key = BookServerKey(address: server, fingerprint: nil)
        let store = BookPositionStore(place: BookPositionMemory())
        store.put(
            LocalBookPosition(
                bookID: 7,
                serverAddress: server,
                positionSec: 3600,
                updatedAtMs: 1_000
            )
        )
        let listeners = PlayerListenerStore(place: SavedStateBox())
        listeners.set("9")
        XCTAssertEqual(3600, store.get(bookID: 7, server: key)?.positionSec)
        listeners.set(nil)
        XCTAssertEqual(
            3600, store.get(bookID: 7, server: key)?.positionSec,
            "the phone's own place moved when the listener did"
        )
    }

    // MARK: - the rig

    /// A client over the stub, with a token so a device-tier route is not
    /// refused before it is recorded.
    private static func client() -> DomovoiClient {
        let store = InMemoryShellStore(serverURL: "http://10.0.0.42:6369", token: "household-abc")
        store.trustServer("http://10.0.0.42:6369", identity: TestIdentity.real.pin)
        return AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        ).client
    }

    /// Answer every route with `body` — **except `/api/health`, which gets a
    /// real signed identity**.
    ///
    /// Not a nicety. The client will not send the household token to a server
    /// that has not proved the pinned key (``IdentityGate``), so a dispatcher
    /// that answered JSON to the health probe too made every device-tier
    /// request below fail *before it was ever sent*: the first version of
    /// these two cases measured zero requests and read as "the listener is not
    /// on the wire", which is the same sentence the real defect produced.
    /// `PlayerHouse` answers health the same way and for the same reason.
    private static func answerEverything(_ body: String) {
        StubURLProtocol.dispatch { request in
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            if parts.path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            return .json(body)
        }
    }

    /// Whether each request that reached the stub carried a `person_id`, in
    /// order — the query for a GET, the body for the POST.
    ///
    /// On the **bytes**, not on what the model thinks it sent: the whole
    /// defect was a parameter that was never on the wire.
    private static func sawPerson() -> [Bool] {
        StubURLProtocol.recorded.compactMap { request in
            guard let url = request.url, url.path.contains("/position") else { return nil }
            if let body = request.httpBody {
                return String(decoding: body, as: UTF8.self).contains("person_id")
            }
            return (url.query ?? "").contains("person_id")
        }
    }
}
