import Foundation
import XCTest
@testable import DomovoiKit

/// The Audiobooks screens' data layer: the place sync's state machine, the
/// cover cache, and what the library asks the server for.
///
/// None of this is tested on Android — `AudiobooksScreen.kt` and
/// `BookPage.kt` are composables, and `PositionSync`'s in-flight guard has no
/// JVM test over there either — so the behaviour is specified here or
/// nowhere.
@MainActor
final class AudiobooksModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the place sync's rig

    private static let home = BookServerKey(address: "http://10.0.0.42:6369", fingerprint: "aa")
    private static let away = BookServerKey(address: "http://10.0.0.9:6369", fingerprint: "bb")

    /// A server's slot, driven by a test.
    ///
    /// `@unchecked Sendable` with its reason: every piece of mutable state is
    /// a ``Shared``, which holds its own lock, and `onPut` is set once before
    /// the remote is used.
    private final class Slot: BookPositionRemote, @unchecked Sendable {

        let server: BookServerKey
        let answer = Shared<ServerBookPosition>(
            ServerBookPosition(positionSec: 0, speed: 1, updatedAt: nil)
        )
        let puts = Shared<[Double]>([])
        let gets = Shared<Int>(0)
        let failPut = Shared<Bool>(false)

        /// Hangs the read until the task is cancelled, which is what a server
        /// out of reach looks like.
        let hangGet = Shared<Bool>(false)

        /// Holds the read open and **lets it go again** — the window a house
        /// change has to land in for a read-after-await test. Unlike
        /// ``hangGet`` this is re-read while it waits, so a test can set it
        /// false and watch the answer arrive under the new house.
        let heldGet = Shared<Bool>(false)

        /// Run **while a PUT is in flight** — the only moment the in-flight
        /// guard is about.
        private let onPut: (@Sendable () -> Void)?

        init(server: BookServerKey, onPut: (@Sendable () -> Void)? = nil) {
            self.server = server
            self.onPut = onPut
        }

        func get(_ bookID: Int64) async throws -> ServerBookPosition {
            gets.mutate { $0 += 1 }
            if hangGet.current {
                for _ in 0..<settleYieldLimit {
                    try Task.checkCancellation()
                    await Task.yield()
                }
                throw CancellationError()
            }
            for _ in 0..<settleYieldLimit {
                if !heldGet.current { break }
                try Task.checkCancellation()
                await Task.yield()
            }
            return answer.current
        }

        func put(_ bookID: Int64, positionSec: Double, speed: Double) async throws {
            puts.mutate { $0.append(positionSec) }
            onPut?()
            if failPut.current { throw DomovoiError.unreachable("away") }
        }

        func others(_ bookID: Int64) async throws -> [OtherBookPosition] {
            []
        }
    }

    private func sync(
        store: BookPositionStore,
        remote: (any BookPositionRemote)?,
        nowMs: Int64 = 1_000
    ) -> BookPositionSync {
        BookPositionSync(
            store: store,
            remote: { remote },
            clock: { nowMs },
            // **Held shut, not opened at once.** `sleep: { _ in }` stood here
            // and was the cause of this class's intermittent red —
            // ``BookTimeout/heldShut(yields:)`` carries the whole account and
            // `BookTimeoutTests` measures it.
            sleep: BookTimeout.heldShut()
        )
    }

    // MARK: - opening a book

    func testOpeningABookWithNoPlaceAnywhereStartsAtTheBeginning() async {
        let store = BookPositionStore(place: BookPositionMemory())
        let slot = Slot(server: Self.home)
        let places = sync(store: store, remote: slot)

        let place = await places.open(bookID: 7, server: Self.home)

        XCTAssertEqual(0, place.positionSec)
        XCTAssertEqual(1, place.speed)
        XCTAssertNil(store.get(bookID: 7, server: Self.home), "nothing is written for a book nobody started")
    }

    func testOpeningABookTakesTheServersPlaceWhenOnlyItHasOne() async throws {
        let store = BookPositionStore(place: BookPositionMemory())
        let slot = Slot(server: Self.home)
        slot.answer.set(
            ServerBookPosition(positionSec: 3672, speed: 1.25, updatedAt: "2026-11-01T22:14:03Z")
        )
        let places = sync(store: store, remote: slot)

        let place = await places.open(bookID: 7, server: Self.home)

        XCTAssertEqual(3672, place.positionSec)
        XCTAssertEqual(1.25, place.speed)
        let kept = try XCTUnwrap(store.get(bookID: 7, server: Self.home))
        XCTAssertEqual(3672, kept.positionSec)
        XCTAssertFalse(kept.dirty, "an adopted place has nothing to send back")
        XCTAssertEqual("2026-11-01T22:14:03Z", kept.serverStamp)
        XCTAssertEqual([], slot.puts.current)
    }

    /// The phone has a place and the server has none: the place goes up as
    /// part of opening the book.
    func testOpeningABookSendsThePhonesPlaceWhenTheServerHasNone() async {
        let store = BookPositionStore(place: BookPositionMemory())
        store.put(
            LocalBookPosition(bookID: 7, serverFingerprint: "aa", positionSec: 900, updatedAtMs: 1)
        )
        let slot = Slot(server: Self.home)
        let places = sync(store: store, remote: slot)

        let place = await places.open(bookID: 7, server: Self.home)

        XCTAssertEqual(900, place.positionSec)
        XCTAssertEqual([900], slot.puts.current)
    }

    /// A book of another house: the phone's own place stands and the
    /// connected server is asked nothing. A place belongs to the server the
    /// book came from.
    func testABooksPlaceIsNeverReadFromAnotherHouse() async {
        let store = BookPositionStore(place: BookPositionMemory())
        store.put(
            LocalBookPosition(bookID: 7, serverFingerprint: "bb", positionSec: 500, updatedAtMs: 1)
        )
        let slot = Slot(server: Self.home)
        let places = sync(store: store, remote: slot)

        let place = await places.open(bookID: 7, server: Self.away)

        XCTAssertEqual(500, place.positionSec)
        XCTAssertEqual(0, slot.gets.current, "the connected server has nothing to say about another's book")
    }

    /// The bound: a server that does not answer leaves the phone's place and
    /// does not hold up play.
    func testAServerOutOfReachLeavesThePhonesPlace() async {
        let store = BookPositionStore(place: BookPositionMemory())
        store.put(
            LocalBookPosition(bookID: 7, serverFingerprint: "aa", positionSec: 640, updatedAtMs: 1)
        )
        let slot = Slot(server: Self.home)
        slot.hangGet.set(true)
        let places = sync(store: store, remote: slot)

        let place = await places.open(bookID: 7, server: Self.home)

        XCTAssertEqual(640, place.positionSec)
        XCTAssertEqual([], slot.puts.current, "a read that never answered is not a reason to write")
    }

    func testWithNoRemoteAtAllThePlaceIsStillKeptHere() async {
        let store = BookPositionStore(place: BookPositionMemory())
        store.put(
            LocalBookPosition(bookID: 7, serverFingerprint: "aa", positionSec: 77, speed: 2, updatedAtMs: 1)
        )
        let places = sync(store: store, remote: nil)

        let place = await places.open(bookID: 7, server: Self.home)

        XCTAssertEqual(77, place.positionSec)
        XCTAssertEqual(2, place.speed)
    }

    // MARK: - recording and pushing

    func testRecordingAPlaceClampsItAndKeepsTheServersLastStamp() async throws {
        let store = BookPositionStore(place: BookPositionMemory())
        store.put(
            LocalBookPosition(
                bookID: 7,
                serverFingerprint: "aa",
                positionSec: 100,
                updatedAtMs: 1,
                dirty: false,
                serverStamp: "2026-11-01T20:00:00Z"
            )
        )
        let places = sync(store: store, remote: nil, nowMs: 5_000)

        await places.record(bookID: 7, server: Self.home, positionSec: -9, speed: 1.5)

        let kept = try XCTUnwrap(store.get(bookID: 7, server: Self.home))
        XCTAssertEqual(0, kept.positionSec, "a negative place is the start")
        XCTAssertEqual(1.5, kept.speed)
        XCTAssertEqual(5_000, kept.updatedAtMs)
        XCTAssertTrue(kept.dirty)
        XCTAssertEqual(
            "2026-11-01T20:00:00Z",
            kept.serverStamp,
            "what the server last held has not changed just because the phone moved"
        )
    }

    func testAPushClearsTheFlagAndTakesTheServersNewStamp() async throws {
        let store = BookPositionStore(place: BookPositionMemory())
        store.put(
            LocalBookPosition(bookID: 7, serverFingerprint: "aa", positionSec: 900, updatedAtMs: 1)
        )
        let slot = Slot(server: Self.home)
        slot.answer.set(
            ServerBookPosition(positionSec: 900, speed: 1, updatedAt: "2026-11-01T23:00:00Z")
        )
        let places = sync(store: store, remote: slot)

        let went = await places.push(bookID: 7, server: Self.home)

        XCTAssertTrue(went)
        XCTAssertEqual([900], slot.puts.current)
        let kept = try XCTUnwrap(store.get(bookID: 7, server: Self.home))
        XCTAssertFalse(kept.dirty)
        XCTAssertEqual("2026-11-01T23:00:00Z", kept.serverStamp)
    }

    /// **The read-after-await case.** A place recorded while the POST is on
    /// its way must stay dirty: the record's `updatedAtMs` is captured before
    /// the request and compared after it, so the newer place is not marked as
    /// sent. Without the guard the phone's latest place is lost for good —
    /// nothing would ever send it.
    func testAPlaceSetWhileThePushWasInFlightStaysUnsent() async throws {
        let kept = BookPositionMemory()
        let store = BookPositionStore(place: kept)
        store.put(
            LocalBookPosition(bookID: 7, serverFingerprint: "aa", positionSec: 900, updatedAtMs: 1)
        )
        // The record moves on, in the middle of the round trip, exactly as a
        // player five seconds further on would move it.
        let slot = Slot(server: Self.home, onPut: {
            BookPositionStore(place: kept).put(
                LocalBookPosition(
                    bookID: 7,
                    serverFingerprint: "aa",
                    positionSec: 1_500,
                    updatedAtMs: 2
                )
            )
        })
        slot.answer.set(ServerBookPosition(positionSec: 900, speed: 1, updatedAt: "2026-11-01T23:00:00Z"))
        let places = sync(store: store, remote: slot)

        let went = await places.push(bookID: 7, server: Self.home)

        XCTAssertTrue(went, "the 900 did go up")
        let now = try XCTUnwrap(store.get(bookID: 7, server: Self.home))
        XCTAssertEqual(1_500, now.positionSec)
        XCTAssertTrue(now.dirty, "the 1500 was recorded after the push read its record: it still has to go")
        XCTAssertNil(now.serverStamp)
    }

    func testAFailedPushLeavesThePlaceDirty() async throws {
        let store = BookPositionStore(place: BookPositionMemory())
        store.put(
            LocalBookPosition(bookID: 7, serverFingerprint: "aa", positionSec: 900, updatedAtMs: 1)
        )
        let slot = Slot(server: Self.home)
        slot.failPut.set(true)
        let places = sync(store: store, remote: slot)

        let went = await places.push(bookID: 7, server: Self.home)

        XCTAssertFalse(went)
        XCTAssertTrue(try XCTUnwrap(store.get(bookID: 7, server: Self.home)).dirty)
    }

    func testNothingIsPushedWhenNothingIsNew() async {
        let store = BookPositionStore(place: BookPositionMemory())
        store.put(
            LocalBookPosition(
                bookID: 7,
                serverFingerprint: "aa",
                positionSec: 900,
                updatedAtMs: 1,
                dirty: false
            )
        )
        let slot = Slot(server: Self.home)
        let places = sync(store: store, remote: slot)

        let went = await places.push(bookID: 7, server: Self.home)

        XCTAssertFalse(went)
        XCTAssertEqual([], slot.puts.current)
    }

    /// What the library does once it has answered: every place set while away
    /// goes up — and **only** this house's.
    func testPushAllSendsEveryDirtyPlaceOfThisHouseAndNoOthers() async {
        let store = BookPositionStore(place: BookPositionMemory())
        store.put(LocalBookPosition(bookID: 7, serverFingerprint: "aa", positionSec: 100, updatedAtMs: 3))
        store.put(LocalBookPosition(bookID: 8, serverFingerprint: "aa", positionSec: 200, updatedAtMs: 2))
        store.put(LocalBookPosition(bookID: 9, serverFingerprint: "bb", positionSec: 300, updatedAtMs: 1))
        store.put(
            LocalBookPosition(
                bookID: 10,
                serverFingerprint: "aa",
                positionSec: 400,
                updatedAtMs: 4,
                dirty: false
            )
        )
        let slot = Slot(server: Self.home)
        let places = sync(store: store, remote: slot)

        let sent = await places.pushAll(Self.home)

        XCTAssertEqual(2, sent)
        XCTAssertEqual([100, 200], slot.puts.current.sorted())
    }

    func testPushAllSendsNothingWhenTheHouseIsNotTheConnectedOne() async {
        let store = BookPositionStore(place: BookPositionMemory())
        store.put(LocalBookPosition(bookID: 7, serverFingerprint: "bb", positionSec: 100, updatedAtMs: 1))
        let slot = Slot(server: Self.home)
        let places = sync(store: store, remote: slot)

        let sent = await places.pushAll(Self.away)

        XCTAssertEqual(0, sent)
        XCTAssertEqual([], slot.puts.current)
    }

    // MARK: - the start plan

    /// The offer is read only from the book's own server, and a server with
    /// no `/positions` at all is no offer rather than no play.
    func testThePlanAsksAnotherHouseForNothing() async {
        let store = BookPositionStore(place: BookPositionMemory())
        store.put(LocalBookPosition(bookID: 7, serverFingerprint: "bb", positionSec: 600, updatedAtMs: 1))
        let slot = Slot(server: Self.home)
        let places = sync(store: store, remote: slot)

        let plan = await BookStartPlanner.plan(
            book: AudiobookRow(id: 7, title: "Dune"),
            server: Self.away,
            sync: places,
            remote: slot,
            // Held shut for the rig's reason above.
            sleep: BookTimeout.heldShut()
        )

        XCTAssertEqual(600, plan.positionSec)
        XCTAssertNil(plan.offer)
        XCTAssertTrue(plan.asks)
        XCTAssertEqual("Dune", plan.title)
    }

    func testThePlanOffersTheRoomThatGotFurther() async throws {
        let store = BookPositionStore(place: BookPositionMemory())
        store.put(LocalBookPosition(bookID: 7, serverFingerprint: "aa", positionSec: 3672, updatedAtMs: 1))
        let slot = Elsewhere(
            server: Self.home,
            others: [
                OtherBookPosition(
                    deviceLabel: "kitchen",
                    room: "kitchen",
                    positionSec: 8013,
                    updatedAt: "2026-11-01T22:14:03Z"
                )
            ]
        )
        let places = sync(store: store, remote: slot)

        let plan = await BookStartPlanner.plan(
            book: AudiobookRow(id: 7, title: "Dune"),
            server: Self.home,
            sync: places,
            remote: slot,
            // Held shut for the rig's reason above.
            sleep: BookTimeout.heldShut()
        )

        let offer = try XCTUnwrap(plan.offer)
        XCTAssertEqual("continue from the kitchen (2 h 13 m)", BookContinueRules.text(offer))
    }

    /// An older server with no `/positions`: the read throws, and that is a
    /// missing offer, not a missing book.
    func testAServerWithNoPositionsRouteSimplyOffersNothing() async {
        let store = BookPositionStore(place: BookPositionMemory())
        store.put(LocalBookPosition(bookID: 7, serverFingerprint: "aa", positionSec: 3672, updatedAtMs: 1))
        let slot = Elsewhere(server: Self.home, others: [], fail: true)
        let places = sync(store: store, remote: slot)

        let plan = await BookStartPlanner.plan(
            book: AudiobookRow(id: 7, title: "Dune"),
            server: Self.home,
            sync: places,
            remote: slot,
            // Held shut for the rig's reason above.
            sleep: BookTimeout.heldShut()
        )

        XCTAssertNil(plan.offer)
        XCTAssertEqual(3672, plan.positionSec)
    }

    /// A slot that also answers the other places.
    private final class Elsewhere: BookPositionRemote, @unchecked Sendable {
        let server: BookServerKey
        private let rows: [OtherBookPosition]
        private let fail: Bool

        init(server: BookServerKey, others: [OtherBookPosition], fail: Bool = false) {
            self.server = server
            self.rows = others
            self.fail = fail
        }

        func get(_ bookID: Int64) async throws -> ServerBookPosition {
            ServerBookPosition(positionSec: 0, speed: 1, updatedAt: nil)
        }

        func put(_ bookID: Int64, positionSec: Double, speed: Double) async throws {}

        func others(_ bookID: Int64) async throws -> [OtherBookPosition] {
            if fail { throw DomovoiError.unreachable("no such route") }
            return rows
        }
    }

    // MARK: - the covers

    /// A cover store a test drives.
    private final class Covers: Sendable {
        let asked = Shared<[Int64]>([])
        let bytes = Shared<Int>(512)
        /// 404: this book has no cover.
        let missing = Shared<Set<Int64>>([])
        /// Any other failure.
        let broken = Shared<Set<Int64>>([])
        let held = Shared<Bool>(false)

        var fetch: @Sendable (Int64) async throws -> Data {
            let asked = self.asked
            let bytes = self.bytes
            let missing = self.missing
            let broken = self.broken
            let held = self.held
            return { id in
                asked.mutate { $0.append(id) }
                while held.current {
                    try Task.checkCancellation()
                    await Task.yield()
                }
                if missing.current.contains(id) {
                    throw DomovoiError.httpError(
                        ApiFailure(status: 404, message: "404: no cover art", deviceTokenRequired: false, body: "")
                    )
                }
                if broken.current.contains(id) { throw DomovoiError.unreachable("away") }
                return Data(repeating: 0x42, count: bytes.current)
            }
        }
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the model",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    /// Give every task that could have been started its chance to run, for
    /// asserting a **negative**: a bare `Task.yield()` would make the
    /// assertion pass because nothing has run yet rather than because nothing
    /// happens.
    private func nothingHappens() async {
        for _ in 0..<3_000 { await Task.yield() }
    }

    /// A shorter version of ``nothingHappens()``, for a negative asserted
    /// while a ``Waits`` gate is open.
    ///
    /// ``Waits/sleeper`` is bounded — it gives up after `settleYieldLimit`
    /// yields rather than spinning for ever — and a three-thousand-yield
    /// negative lets that bound expire, which the model then reads as the
    /// debounce having elapsed. Two hundred yields is far more than the
    /// debounce needs to be *missing* in (no debounce applies the needle
    /// inside `typed` itself, with no yields at all) and far less than the
    /// gate's own patience.
    private func briefly() async {
        for _ in 0..<200 { await Task.yield() }
    }

    func testACoverArrivesAsItsBytes() async {
        let house = Covers()
        let store = AudiobookCoverStore(fetch: house.fetch)

        store.want(7)
        await settleUntil({ store.cover(7) != nil && store.cover(7) != .loading }, "the cover")

        XCTAssertEqual(.image(Data(repeating: 0x42, count: 512)), store.cover(7))
    }

    /// The one difference from ``VideoPosterStore``: the cover route answers
    /// **404** for a book with no cover, and a store that called that a
    /// failure would re-fetch every coverless book on every scroll pass, for
    /// ever.
    func testACoverlessBookIsSettledAndNeverAskedForAgain() async {
        let house = Covers()
        house.missing.set([13])
        let store = AudiobookCoverStore(fetch: house.fetch)

        store.want(13)
        await settleUntil({ store.cover(13) == .absent }, "the 404")
        store.want(13)
        store.want(13)
        await nothingHappens()

        XCTAssertEqual(.absent, store.cover(13))
        XCTAssertEqual([13], house.asked.current, "asked once, and once only")
    }

    func testAFailureThatIsNotA404IsWorthAnotherTry() async {
        let house = Covers()
        house.broken.set([7])
        let store = AudiobookCoverStore(fetch: house.fetch)

        store.want(7)
        await settleUntil({ store.cover(7) == .failed }, "the failure")
        store.want(7)
        await settleUntil({ house.asked.current.count == 2 }, "the retry")

        XCTAssertEqual([7, 7], house.asked.current)
    }

    func testABodyTooSmallToBeAPictureIsNoPicture() async {
        let house = Covers()
        house.bytes.set(8)
        let store = AudiobookCoverStore(fetch: house.fetch)

        store.want(7)
        await settleUntil({ store.cover(7) == .absent }, "the tiny body")

        XCTAssertEqual(.absent, store.cover(7))
    }

    /// **Read-after-await, on the covers.** Book 7 exists in both houses and
    /// is a different book in each. A fetch in flight when the app moves must
    /// not be drawn as the new house's cover.
    func testACoverInFlightWhenTheHouseChangesLandsNowhere() async {
        let house = Covers()
        house.held.set(true)
        let store = AudiobookCoverStore(fetch: house.fetch)

        store.want(7)
        await settleUntil({ house.asked.current == [7] }, "the request")
        // The move happens INSIDE the round trip.
        store.serverChanged()
        house.held.set(false)
        await nothingHappens()

        XCTAssertNil(store.cover(7), "the old house's cover is not this house's")
        XCTAssertEqual(0, store.count)
    }

    func testOnlyAHundredCoversAreHeld() async {
        let house = Covers()
        let store = AudiobookCoverStore(fetch: house.fetch)

        for id in 0..<(AudiobookCoverStore.capacity + 20) { store.want(Int64(id)) }
        await settleUntil(
            { house.asked.current.count == AudiobookCoverStore.capacity + 20 },
            "every request"
        )
        await settleUntil({ store.count <= AudiobookCoverStore.capacity }, "the eviction")

        XCTAssertEqual(AudiobookCoverStore.capacity, store.count)
        XCTAssertNil(store.cover(0), "the oldest went")
    }

    // MARK: - the library model's rig

    /// The house's `/api/audiobooks`, in the server's shapes.
    private final class House: Sendable {
        let total = Shared<Int>(9)
        let reindexStatus = Shared<Int>(200)
        let listStatus = Shared<Int>(200)

        /// Row `index`: the title sorts by index, and row 3 is the only one
        /// whose **narrator** holds `quill`, so a needle matching it cannot
        /// pass on an unfiltered list.
        func row(_ index: Int) -> String {
            """
            {"id":\(index),"title":"book \(index)","author":"a\(index)",\
            "narrator":\(index == 3 ? "\"Ganymede Quill\"" : "null"),\
            "is_folder":false,"duration_sec":36720.0,"size_bytes":1024,\
            "chapters":[{"title":"One","start_sec":0}],\
            "files":[{"file":"b\(index).m4b","start_sec":0,"size_bytes":1024}],\
            "cover_url":"/api/audiobooks/\(index)/cover"}
            """
        }

        func list(offset: Int, limit: Int, narrowed: Bool) -> String {
            let count = narrowed ? min(1, total.current) : total.current
            let from = min(offset, count)
            let to = min(from + limit, count)
            let rows = from < to ? (from..<to).map(row).joined(separator: ",") : ""
            return #"{"items":[\#(rows)],"total":\#(count),"limit":\#(limit),"has_more":\#(to < count)}"#
        }

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let query = parts.queryItems ?? []
                func value(_ name: String) -> String? { query.first { $0.name == name }?.value }

                switch parts.path {
                case "/api/health":
                    // The identity gate's probe. Without it every read below
                    // is held and every assertion is about nothing.
                    return .json((try? TestIdentity.real.healthBody(challenge: value("challenge"))) ?? "{}")
                case "/api/audiobooks":
                    guard self.listStatus.current == 200 else {
                        return .json(#"{"detail":"indexing"}"#, status: self.listStatus.current)
                    }
                    return .json(
                        self.list(
                            offset: Int(value("offset") ?? "0") ?? 0,
                            limit: Int(value("limit") ?? "50") ?? 50,
                            narrowed: value("q")?.isEmpty == false
                        )
                    )
                case "/api/audiobooks/reindex":
                    guard self.reindexStatus.current == 200 else {
                        return .json(#"{"detail":"reindex failed: no folder"}"#, status: self.reindexStatus.current)
                    }
                    return .json(#"{"scanned":9,"inserted":1,"errors":0,"pruned":0}"#)
                case let path where path.hasSuffix("/positions"):
                    // A bare array, as the route answers it.
                    return .json("[]")
                case let path where path.hasSuffix("/position"):
                    guard request.httpMethod == "POST" else {
                        // No row: `position_sec` and `speed`, and NO
                        // `updated_at` at all.
                        return .json(#"{"position_sec":0,"speed":1.0}"#)
                    }
                    return .json(#"{"saved":true}"#)
                default:
                    // `/api/audiobooks/{id}` — one `_book_row`, or a 404 for
                    // an id the house has not got.
                    let tail = parts.path.replacingOccurrences(of: "/api/audiobooks/", with: "")
                    guard let id = Int(tail), id < self.total.current else {
                        return .json(#"{"detail":"audiobook \#(tail) not found"}"#, status: 404)
                    }
                    return .json(self.row(id))
                }
            }
        }
    }

    /// The debounce, as a gate a test opens. Bounded rather than spinning for
    /// ever, which would hang the suite instead of failing a case.
    private final class Waits: Sendable {
        let asked = Shared<[Int64]>([])
        private let opened = Shared<Bool>(false)

        func release() { opened.set(true) }

        var sleeper: @Sendable (Int64) async throws -> Void {
            let asked = self.asked
            let opened = self.opened
            return { ms in
                asked.mutate { $0.append(ms) }
                for _ in 0..<settleYieldLimit {
                    if opened.current { return }
                    try Task.checkCancellation()
                    await Task.yield()
                }
                throw CancellationError()
            }
        }
    }

    /// Whatever plays audio, as a recorder.
    @MainActor
    private final class Ears: AudiobooksPlayer {
        var played: [AudiobookPlayRequest] = []

        func play(_ request: AudiobookPlayRequest) {
            played.append(request)
        }
    }

    private func rig(
        house: House = House(),
        deviceID: String? = "ios-1",
        waits: Waits = Waits()
    ) -> (AudiobooksModel, House, Waits, AppContainer) {
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = AudiobooksModel(
            in: container,
            deviceID: deviceID,
            places: BookPositionMemory(),
            wait: waits.sleeper
        )
        return (model, house, waits, container)
    }

    /// The same house, built through the initialiser a test drives: the
    /// server key is a box a case can set, and the place slot is a ``Slot``
    /// whose read can be held open. The two together are what a move *during*
    /// a request needs.
    private func switchableRig(
        house: House = House()
    ) -> (AudiobooksModel, Slot, Shared<BookServerKey>) {
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let pointedAt = Shared<BookServerKey>(Self.home)
        let slot = Slot(server: Self.home)
        let places = BookPositionSync(
            store: BookPositionStore(place: BookPositionMemory()),
            remote: { slot },
            clock: { 1_000 },
            // **The opposite of ``sync(store:remote:)``'s timer.** There the
            // bound is opened at once, so a hung read costs no time; here the
            // whole point is that the read is *in flight*, so the bound must
            // not fire — ``BookTimeout/bounded`` races the work against this
            // sleep and returns whichever lands first. Twenty thousand yields
            // is far longer than any assertion below waits, so the leg that
            // answers is always the slot's read.
            sleep: { _ in
                for _ in 0..<settleYieldLimit {
                    try Task.checkCancellation()
                    await Task.yield()
                }
            }
        )
        let model = AudiobooksModel(
            in: container,
            serverKey: { pointedAt.current },
            remote: { slot },
            knowsThisPhone: true,
            positions: places,
            covers: AudiobookCoverStore(in: container),
            wait: { _ in }
        )
        return (model, slot, pointedAt)
    }

    private func queries(to path: String) -> [String] {
        StubURLProtocol.recorded.compactMap { request in
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
                parts.path == path
            else { return nil }
            return parts.query ?? ""
        }
    }

    private func started(_ model: AudiobooksModel) async {
        model.start()
        await settleUntil({ model.list.state.loaded }, "the library")
    }

    // MARK: - the library

    func testTheLibraryReadsTheServersListAndCountsIt() async {
        let (model, _, _, _) = rig()

        await started(model)

        XCTAssertEqual(9, model.books.count)
        XCTAssertEqual("showing 9 of 9 books", model.countText)
        XCTAssertEqual("book 3", model.books[3].displayTitle)
        XCTAssertEqual("10:12:00 · 1 chapter · 1 KB", model.meta(model.books[3]))
        XCTAssertNil(model.emptyNote)
    }

    func testTheFirstReadAsksForTheTitleSortAndNoNeedle() async {
        let (model, _, _, _) = rig()

        await started(model)

        let asked = try? XCTUnwrap(queries(to: "/api/audiobooks").first)
        XCTAssertEqual(true, asked?.contains("sort=title"))
        XCTAssertEqual(false, asked?.contains("q="))
    }

    func testASortChipAsksTheServerForThatSort() async {
        let (model, _, _, _) = rig()
        await started(model)

        model.choose(.author)
        await settleUntil({ self.queries(to: "/api/audiobooks").count > 1 }, "the second read")

        XCTAssertEqual(.author, model.sort)
        XCTAssertEqual(
            true,
            queries(to: "/api/audiobooks").last?.contains("sort=author"),
            "a chip that did not reach ?sort= would be decorative"
        )
    }

    func testAKeystrokeBecomesOneRequestAfterTheDebounce() async {
        let (model, _, waits, _) = rig()
        await started(model)

        model.typed("qu")
        model.typed("qui")
        model.typed("quill")
        await briefly()
        // Asserted as "no NARROWED read went out" rather than "one read went
        // out in total": the shell re-reads an unfiltered list of its own
        // when the identity gate pins the house, and counting every request
        // to the path made this case about that instead of about the
        // debounce. Three keystrokes with no debounce would put three `q=`
        // reads here, so the assertion still fails if the feature is gone.
        XCTAssertEqual("", model.needle, "the needle does not move while the typing settles")
        XCTAssertEqual(
            [],
            queries(to: "/api/audiobooks").filter { $0.contains("q=") },
            "nothing narrowed goes out while the typing settles"
        )

        waits.release()
        await settleUntil({ model.needle == "quill" }, "the needle")
        await settleUntil({ self.queries(to: "/api/audiobooks").count > 1 }, "the search")

        XCTAssertEqual([250], Array(Set(waits.asked.current)), "250 ms, as Android waits")
        XCTAssertEqual(true, queries(to: "/api/audiobooks").last?.contains("q=quill"))
    }

    /// A new sort or needle is a new list, and it opens at the top — the bug
    /// Android's batch 18 had, where the grid followed the old first row's
    /// key into the new list.
    func testANewListAsksTheViewToGoToTheTop() async {
        let (model, _, _, _) = rig()
        await started(model)
        XCTAssertFalse(model.wantsTop, "the first list is already at the top")

        model.choose(.newest)
        await settleUntil({ model.wantsTop }, "the top request")

        model.toppedTheList()
        XCTAssertFalse(model.wantsTop)
    }

    /// A new **needle** is a new list too, and it opens at the top.
    ///
    /// `listKey` is the sort *and* the needle (`AudiobooksModel:234`), so a
    /// search that narrowed the list without asking for the top would leave
    /// the reader parked where the wide list was — the same batch 18 bug
    /// reached by the other control on the screen. The sort leg is
    /// ``testANewListAsksTheViewToGoToTheTop()``; this is the search leg, and
    /// nothing else covered it.
    func testANewNeedleAlsoAsksTheViewToGoToTheTop() async {
        let (model, _, waits, _) = rig()
        await started(model)
        XCTAssertFalse(model.wantsTop, "the first list is already at the top")

        model.typed("quill")
        waits.release()
        await settleUntil({ model.needle == "quill" }, "the needle")
        await settleUntil({ model.wantsTop }, "the top request")
    }

    /// The top request is once per **list**, not once per answer.
    ///
    /// `toppedFor` is what makes it once (`:257-259`): a second page lands
    /// through the same `onLoaded` callback, and a `wantsTop` raised on every
    /// answer would throw the reader back to book one each time they reached
    /// the foot of a page — the batch 18 fix gone wrong in the other
    /// direction. The UI case for it is
    /// `testAudiobooks_PagingDoesNotYankTheListBackToTheTop`.
    func testASecondPageDoesNotAskTheViewToGoToTheTopAgain() async {
        let house = House()
        house.total.set(120)
        let (model, _, _, _) = rig(house: house)
        await started(model)

        model.choose(.newest)
        await settleUntil({ model.wantsTop }, "the new list's top request")
        model.toppedTheList()
        await settleUntil({ model.books.count == 50 }, "the new list's first page")

        model.list.loadMore()
        await settleUntil({ model.books.count > 50 }, "the second page")
        await nothingHappens()

        XCTAssertFalse(
            model.wantsTop,
            "a second page of the same list asked the view to go back to the top"
        )
    }

    func testTheTwoEmptyMessagesAreNotSwapped() async {
        let house = House()
        house.total.set(0)
        let (model, _, waits, _) = rig(house: house)
        await started(model)

        XCTAssertEqual("no audiobooks yet", model.emptyNote?.title)

        model.typed("qqqq")
        waits.release()
        await settleUntil({ model.needle == "qqqq" }, "the needle")
        await settleUntil({ model.emptyNote?.title == "no books match" }, "the empty search")

        XCTAssertEqual(#"search = "qqqq""#, model.emptyNote?.detail)
    }

    /// A failed read must not draw an empty library: that tells someone with
    /// books that they have none.
    func testAFailedReadIsNotAnEmptyLibrary() async {
        let house = House()
        house.listStatus.set(503)
        let (model, _, _, _) = rig(house: house)

        await started(model)

        XCTAssertFalse(model.isEmpty)
        XCTAssertNil(model.emptyNote)
        XCTAssertNotNil(model.list.state.error)
    }

    func testTheReindexSaysWhatItIndexedAndReReadsTheList() async {
        let (model, _, _, _) = rig()
        await started(model)

        model.reindex()
        await settleUntil({ model.toast != nil }, "the toast")

        XCTAssertEqual("Indexed 9 book(s)", model.toast)
        XCTAssertFalse(model.reindexing)
        await settleUntil({ self.queries(to: "/api/audiobooks").count > 1 }, "the reload")
    }

    func testAFailedReindexSaysSoRatherThanNothing() async {
        let house = House()
        house.reindexStatus.set(502)
        let (model, _, _, _) = rig(house: house)
        await started(model)

        model.reindex()
        await settleUntil({ model.toast != nil }, "the toast")

        XCTAssertEqual(true, model.toast?.contains("Reindex"))
        XCTAssertFalse(model.reindexing)
    }

    /// The list goes up only once the page has answered on this server
    /// (`AudiobooksScreen.kt:155`), and it is `pushAll` only — Android's
    /// `kickWaiting` beside it is the phone's downloads, which are another
    /// item's.
    func testThePlacesSetWhileAwayGoUpOnceTheListHasAnswered() async {
        let (model, _, _, _) = rig()
        await model.positions.record(bookID: 7, server: BookServerKey.of(serverURL: server, pin: nil), positionSec: 900, speed: 1)

        await started(model)
        await settleUntil(
            { StubURLProtocol.recorded.contains { $0.httpMethod == "POST" && $0.url?.path == "/api/audiobooks/7/position" } },
            "the place going up"
        )
    }

    // MARK: - the controls that are not drawn

    func testNoPlayIsOfferedWithNothingToPlayItWith() async {
        let (model, _, _, _) = rig()
        await started(model)

        XCTAssertFalse(model.canPlay(model.books[0]), "no player, no play control")

        let ears = Ears()
        model.player = ears
        XCTAssertTrue(model.canPlay(model.books[0]))
    }

    /// The seam **owns** what it is handed, which is the I003 fix.
    ///
    /// `AudiobooksView`'s `.task` writes
    /// `books.player = AudiobooksPhonePlayer.over(model.player, …)` — an
    /// adapter made on that line, over a player the shell owns, and held by
    /// nothing else. While ``AudiobooksModel/player`` was `weak` it was gone
    /// before the next statement ran, so ``AudiobooksModel/canPlay(_:)`` was
    /// false on every book: no `play`, no `resume`, and every chapter row
    /// drawn as plain text (train I003, six cases).
    ///
    /// The assignment is in a scope of its own with nothing else naming the
    /// object, which is the shape of the real call site; the pool drain is
    /// what makes a `weak` seam answer false here rather than at some later
    /// point of the test runner's choosing.
    func testTheSeamKeepsAPlayerNothingElseHolds() async {
        let (model, _, _, _) = rig()
        await started(model)

        autoreleasepool {
            model.player = Ears()
        }
        XCTAssertTrue(
            model.canPlay(model.books[0]),
            "the player was let go of the moment it was handed over, so no book "
                + "on this screen can be played"
        )
    }

    /// `Sabriel`'s shape: a folder book whose files name nothing. Offering a
    /// play would be offering a request the server answers 400.
    func testNoPlayIsOfferedForABookWithNoPlayableFile() async {
        let (model, _, _, _) = rig()
        await started(model)
        let ears = Ears()
        model.player = ears

        let unplayable = AudiobookRow(
            id: 11,
            title: "Sabriel",
            isFolder: true,
            files: [BookFileRow(file: nil, startSec: 0)]
        )

        XCTAssertFalse(model.canPlay(unplayable))
        model.start(unplayable)
        await nothingHappens()
        XCTAssertEqual([], ears.played.map { $0.bookID })
    }

    func testNoBadgeIsDrawnWithoutAShelf() async {
        let (model, _, _, _) = rig()
        await started(model)

        XCTAssertNil(model.shelfState(model.books[0]), "no shelf, no save badge")
    }

    /// A phone with no device id keeps its place here and asks the server
    /// nothing: an empty `device_id` is a *different* device to the server.
    func testAPhoneWithNoDeviceIdAsksTheServerAboutNoPlaces() async {
        let (model, _, _, container) = rig(deviceID: nil)
        await started(model)

        XCTAssertFalse(model.knowsThisPhone)
        XCTAssertNil(
            ApiBookPositionRemote(client: container.client, deviceID: nil, server: Self.home),
            "there is no remote to build without a device id"
        )
        XCTAssertNil(
            ApiBookPositionRemote(client: container.client, deviceID: "  ", server: Self.home),
            "and a blank one is not an id"
        )
        XCTAssertEqual(
            "this phone hasn't introduced itself to the Domovoi yet, so your place is kept here only",
            model.detail(bookID: 7).noDeviceNote
        )
    }

    // MARK: - starting a book

    func testAFreshBookGoesStraightToPlay() async {
        let (model, _, _, _) = rig()
        await started(model)
        let ears = Ears()
        model.player = ears

        model.start(model.books[0])
        await settleUntil({ !ears.played.isEmpty }, "the play")

        XCTAssertNil(model.ask, "nobody has started this book: there is nothing to ask")
        XCTAssertEqual(0, ears.played[0].startSec)
        XCTAssertEqual("/api/audiobooks/0/audio", ears.played[0].parts.first?.path)
    }

    func testABookWithAPlaceAsksFirstAndThenPlaysFromTheAnswer() async {
        let (model, _, _, _) = rig()
        await started(model)
        let ears = Ears()
        model.player = ears
        let key = BookServerKey.of(serverURL: server, pin: nil)
        await model.positions.record(bookID: 3, server: key, positionSec: 3672, speed: 1.25)

        model.start(model.books[3])
        await settleUntil({ model.ask != nil }, "the question")

        XCTAssertEqual(3, model.ask?.book.id)
        XCTAssertEqual(3672, model.ask?.plan.positionSec)
        XCTAssertEqual([], ears.played.map { $0.bookID }, "nothing plays until the question is answered")

        model.playPlan(from: 3672)
        XCTAssertNil(model.ask)
        XCTAssertEqual(3672, ears.played.first?.startSec)
        XCTAssertEqual(1.25, ears.played.first?.speed)
    }

    func testDismissingTheQuestionPlaysNothing() async {
        let (model, _, _, _) = rig()
        await started(model)
        let ears = Ears()
        model.player = ears
        await model.positions.record(
            bookID: 3,
            server: BookServerKey.of(serverURL: server, pin: nil),
            positionSec: 600,
            speed: 1
        )

        model.start(model.books[3])
        await settleUntil({ model.ask != nil }, "the question")
        model.dismissAsk()
        await nothingHappens()

        XCTAssertEqual([], ears.played.map { $0.bookID })
    }

    /// **Read-after-await, on starting a book** — the top defect class on
    /// this track, on the path that owns a place.
    ///
    /// The slot's read is held open, the app moves house *while the plan is
    /// in flight*, and then the read is let go. ``AudiobooksModel/start(_:)``
    /// captured the old key before its `await`: what comes back is a plan for
    /// a library this phone is no longer looking at, and it must neither
    /// raise the resume dialog over the new house's screen (which
    /// ``AudiobooksModel/serverChanged()`` has just cleared) nor start
    /// playing a book the connected house has never heard of.
    ///
    /// Note what the switch is: both the model's `serverKey` **and** the
    /// screen's `serverChanged()`, inside the round trip, which is what a
    /// move actually is. `testABooksPlaceIsNeverReadFromAnotherHouse` above
    /// is a *different* property — a book of another house, settled before
    /// any await — and does not cover this one.
    func testAStartInFlightWhenTheHouseChangesNeitherAsksNorPlays() async {
        let (model, slot, pointedAt) = switchableRig()
        await started(model)
        let ears = Ears()
        model.player = ears
        await model.positions.record(bookID: 3, server: Self.home, positionSec: 3672, speed: 1.25)
        slot.heldGet.set(true)

        model.start(model.books[3])
        await settleUntil({ slot.gets.current > 0 }, "the slot read")
        // The move happens INSIDE the round trip.
        pointedAt.set(Self.away)
        model.serverChanged()
        slot.heldGet.set(false)
        await nothingHappens()

        XCTAssertNil(model.ask, "a plan for the old library is not a question about this one")
        XCTAssertEqual(
            [],
            ears.played.map { $0.bookID },
            "and a book of the house we have left does not start playing"
        )
    }

    /// The same, on a chapter tap: ``AudiobooksModel/play(_:from:)`` goes
    /// through the sync too, and hands the result straight to the player.
    func testAChapterTapInFlightWhenTheHouseChangesPlaysNothing() async {
        let (model, slot, pointedAt) = switchableRig()
        await started(model)
        let ears = Ears()
        model.player = ears
        slot.heldGet.set(true)

        model.play(model.books[3], from: 1_200)
        await settleUntil({ slot.gets.current > 0 }, "the slot read")
        pointedAt.set(Self.away)
        model.serverChanged()
        slot.heldGet.set(false)
        await nothingHappens()

        XCTAssertEqual([], ears.played.map { $0.bookID })
    }

    // MARK: - one book's page

    func testABookPageReadsTheBookItsChaptersAndItsPlace() async throws {
        let (model, _, _, _) = rig()
        await started(model)
        let key = BookServerKey.of(serverURL: server, pin: nil)
        await model.positions.record(bookID: 3, server: key, positionSec: 3672, speed: 1)

        let page = model.detail(bookID: 3)
        page.start()
        await settleUntil({ page.placeSec != nil && page.book != nil }, "the page")

        XCTAssertEqual(3672, page.placeSec)
        XCTAssertEqual("read by Ganymede Quill", page.narratorLine)
        XCTAssertEqual("10:12:00 · 1 KB · 1 chapter", page.metaLine)
        XCTAssertEqual("your place: 1:01:12 of 10:12:00 · 10%", page.placeText)
        XCTAssertEqual("resume", page.playWord)
        XCTAssertEqual(["One"], page.chapters.map { $0.title })
        XCTAssertEqual(0, page.currentChapter, "chapter one holds the place")
    }

    func testAPageWithNoPlaceSaysPlay() async {
        let (model, _, _, _) = rig()
        await started(model)

        let page = model.detail(bookID: 3)
        page.start()
        await settleUntil({ page.book != nil }, "the book")
        await nothingHappens()

        XCTAssertNil(page.placeSec)
        XCTAssertNil(page.placeText)
        XCTAssertEqual("play", page.playWord)
        XCTAssertNil(page.currentChapter, "nothing is highlighted in a book nobody started")
    }
}
