import Foundation
import XCTest
@testable import DomovoiKit

/// `ManualRepository` — the fetch, the three verdicts, and the copy kept per
/// server.
///
/// The per-server part is why this is a package test and not a Simulator
/// check: "server A's manual is not served for server B" is a claim about a
/// file name, and ``ManualCacheBox`` makes it one assertion instead of two
/// launches.
final class ManualStoreTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let otherServer = "http://10.0.0.99:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// A document small enough to read in a failure message, real enough to
    /// pass ``ManualDoc/parse(_:)``.
    static func document(_ sentence: String) -> String {
        """
        {"schema":"domovoi-manual/2","doc_base":"https://example.com/blob/{ref}/",
         "labels":{"start_here":"start here","net":{"no":"works offline"}},
         "centre":{"id":"domovoi","title":"Domovoi","opens":"how-it-works"},
         "start":{"title":"Start here","blocks":[{"type":"p","text":"\(sentence)"}],
                  "checklist":[]},
         "topics":[{"id":"how-it-works","title":"How it works","pieces":["what-it-is"]}],
         "pieces":[{"id":"what-it-is","topic":"how-it-works","title":"What it is"}]}
        """
    }

    nonisolated private static func health(_ parts: URLComponents) -> StubURLProtocol.Answer {
        let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
        return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
    }

    /// The house: health signed, and `/manual-content.json` answered from
    /// whatever the test last set.
    private final class House: Sendable {
        let manual = Shared<StubURLProtocol.Answer>(
            .json(ManualStoreTests.document("the house keeps its own manual"))
        )
        let reads = Shared<Int>(0)

        func install() {
            let manual = self.manual
            let reads = self.reads
            StubURLProtocol.dispatch { request in
                guard let parts = request.url.flatMap({
                    URLComponents(url: $0, resolvingAgainstBaseURL: false)
                }) else { return .json("{}") }
                if parts.path == "/api/health" { return ManualStoreTests.health(parts) }
                if parts.path == manualPath {
                    reads.mutate { $0 += 1 }
                    return manual.current
                }
                return .json("{}")
            }
        }
    }

    private func rig(
        server address: String? = nil,
        cache: ManualCacheBox = ManualCacheBox()
    ) -> (ManualStore, House, ManualCacheBox, InMemoryShellStore) {
        let house = House()
        house.install()
        let url = address ?? server
        let store = InMemoryShellStore(serverURL: url, token: "household-abc")
        store.trustServer(url, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        return (ManualStore(in: container, cache: cache), house, cache, store)
    }

    private func sentence(_ load: ManualLoad) -> String? {
        guard case .ready(let doc, _) = load else { return nil }
        return doc.start.blocks.first?.text
    }

    // MARK: - the file name

    func testTheFileNameIsTheServersOwn() {
        let one = ManualStore.key(for: server)
        XCTAssertEqual(24, one.count)
        XCTAssertTrue(one.allSatisfy { $0.isHexDigit && !$0.isUppercase })
        XCTAssertEqual(one, ManualStore.key(for: server), "the same address is the same file")
        XCTAssertNotEqual(one, ManualStore.key(for: otherServer))
        // The number Android writes, so the two files' comments agree about
        // what the name is (nothing reads the other's cache).
        XCTAssertEqual(
            "9f86d081884c7d659a2feaa0",
            ManualStore.key(for: "test"),
            "the first 24 hex characters of SHA-256(\"test\")"
        )
    }

    func testAnAddressWithATrailingSlashIsTheSameServer() {
        XCTAssertEqual("http://10.0.0.42:6369", ManualStore.normalise(" http://10.0.0.42:6369/// "))
        XCTAssertEqual(
            ManualStore.key(for: ManualStore.normalise("\(server)/")),
            ManualStore.key(for: server)
        )
    }

    // MARK: - the three verdicts

    func testTheManualComesFromTheServerAndIsKept() async {
        let (store, _, cache, _) = rig()
        let (address, load) = await store.load()
        XCTAssertEqual(server, address)
        XCTAssertEqual(.ready(true, false), Self.shape(load))
        XCTAssertEqual("the house keeps its own manual", sentence(load))
        XCTAssertEqual([ManualStore.key(for: server)], Array(cache.keys))
        XCTAssertEqual(1, cache.writes)
        XCTAssertNotNil(store.current(), "and it is in memory, with no I/O")
    }

    /// A server older than the manual's file answers 404. There is no copy
    /// offered for it: the server **answered**, and what it said was "no
    /// manual here".
    func testAServerWithNoManualIsNotAFailure() async {
        let (store, house, cache, _) = rig()
        house.manual.set(.json(#"{"detail":"Not Found"}"#, status: 404))
        let (_, load) = await store.load()
        XCTAssertEqual(.noManual, Self.shape(load))
        XCTAssertEqual(0, cache.writes, "nothing worth keeping")
    }

    /// The static mount is `html=True`, so an unknown path is a **200** of
    /// the dashboard's shell. This is the case that would have drawn a manual
    /// with no pages.
    func testTheDashboardsIndexPageIsNoManualAndNotACrash() async {
        let (store, house, cache, _) = rig()
        house.manual.set(.json("<!doctype html><title>Domovoi</title>"))
        let (_, load) = await store.load()
        XCTAssertEqual(.noManual, Self.shape(load))
        XCTAssertEqual(0, cache.writes)
    }

    func testAnUnreachableServerWithNoCopyIsAFailureWithItsReason() async {
        let (store, house, _, _) = rig()
        house.manual.set(StubURLProtocol.Answer(error: URLError(.cannotConnectToHost)))
        let (_, load) = await store.load()
        guard case .failed(let why) = load else { return XCTFail("\(load)") }
        XCTAssertFalse(why.isEmpty, "the reason is what the screen's detail line says")
    }

    func testAnEmptyAddressNeverLeavesThePhone() async {
        let (store, house, _, _) = rig(server: "")
        let (address, load) = await store.load()
        XCTAssertEqual("", address)
        XCTAssertEqual(.failed, Self.shape(load))
        XCTAssertEqual(0, house.reads.current, "and no request was made")
    }

    // MARK: - the copy from last time

    func testAnUnreachableServerShowsTheCopyFromLastTime() async {
        let (store, house, cache, _) = rig()
        _ = await store.load()
        house.manual.set(StubURLProtocol.Answer(error: URLError(.timedOut)))
        let (_, load) = await store.load()
        XCTAssertEqual(.ready(true, true), Self.shape(load), "ready, and marked as the old copy")
        XCTAssertEqual("the house keeps its own manual", sentence(load))
        XCTAssertEqual(1, cache.writes, "a cached read writes nothing back")
    }

    /// The copy is read from **disk**, which is the half a memory-only cache
    /// would pass without: a fresh store with the same box is a relaunch.
    func testTheCopySurvivesARelaunch() async {
        let box = ManualCacheBox()
        let (first, house, _, _) = rig(cache: box)
        _ = await first.load()
        house.manual.set(StubURLProtocol.Answer(error: URLError(.notConnectedToInternet)))

        let (second, _, _, _) = rig(cache: box)
        let (_, load) = await second.load()
        XCTAssertEqual(.ready(true, true), Self.shape(load))
        XCTAssertEqual("the house keeps its own manual", sentence(load))
        XCTAssertNotNil(second.current(), "and the relaunched store now holds it in memory too")
    }

    /// The whole reason the file is named after the address. A copy kept for
    /// one house must never be drawn as another house's manual — on this track
    /// that is not a hypothetical, it is the defect class the night keeps
    /// finding.
    func testOneServersCopyIsNeverAnothersManual() async {
        let box = ManualCacheBox()
        let (first, house, _, _) = rig(cache: box)
        _ = await first.load()
        XCTAssertEqual([ManualStore.key(for: server)], Array(box.keys))

        house.manual.set(StubURLProtocol.Answer(error: URLError(.cannotFindHost)))
        let (second, _, _, _) = rig(server: otherServer, cache: box)
        let (address, load) = await second.load()
        XCTAssertEqual(otherServer, address)
        XCTAssertEqual(
            .failed,
            Self.shape(load),
            "the other house's manual was on the phone, and it is not this house's"
        )
    }

    func testCurrentIsOnlyTheCurrentServersDocument() async {
        let box = ManualCacheBox()
        let (store, _, _, shellStore) = rig(cache: box)
        _ = await store.load()
        XCTAssertNotNil(store.current())
        _ = shellStore.setServerURL(otherServer)
        XCTAssertNil(
            store.current(),
            "the document in memory belongs to the house it was read from"
        )
    }

    /// The read-after-await case for the store itself: the address the answer
    /// is **for** is the one captured before the request, so a caller that has
    /// moved house can tell. ``ManualModel`` is what drops it, and
    /// `ManualModelTests` is where that is asserted.
    func testTheAnswerKnowsWhichServerItWasFor() async {
        let arrived = Shared<Bool>(false)
        let release = Shared<Bool>(false)
        let body = Self.document("the first house's own sentence")
        StubURLProtocol.dispatch { request in
            guard let parts = request.url.flatMap({
                URLComponents(url: $0, resolvingAgainstBaseURL: false)
            }) else { return .json("{}") }
            if parts.path == "/api/health" { return Self.health(parts) }
            guard parts.path == manualPath else { return .json("{}") }
            arrived.set(true)
            for _ in 0..<400 {
                if release.current { break }
                Thread.sleep(forTimeInterval: 0.002)
            }
            return .json(body)
        }
        let shellStore = InMemoryShellStore(serverURL: server, token: "household-abc")
        shellStore.trustServer(server, identity: TestIdentity.real.pin)
        shellStore.trustServer(otherServer, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: shellStore,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 5)
        )
        let store = ManualStore(in: container, cache: ManualCacheBox())

        let load = Task { await store.load() }
        // Bounded, so a read that never leaves fails this test instead of
        // hanging the suite on the shared Mac.
        await settle(for: arrived, what: "the manual read")
        // Inside the round trip.
        _ = shellStore.setServerURL(otherServer)
        release.set(true)
        let (address, verdict) = await load.value

        XCTAssertEqual(server, address, "the answer is for the house that was asked")
        XCTAssertEqual("the first house's own sentence", sentence(verdict))
        XCTAssertNil(
            store.current(),
            "and it is not the current server's document, so nothing hands it over"
        )
    }

    // MARK: - a verdict as a shape, so a failure message names the case

    private enum Shape: Equatable {
        case ready(Bool, Bool)
        case noManual
        case failed
    }

    private static func shape(_ load: ManualLoad) -> Shape {
        switch load {
        case .ready(_, let cached): return .ready(true, cached)
        case .noManual: return .noManual
        case .failed: return .failed
        }
    }
}
