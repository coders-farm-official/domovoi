import XCTest
@testable import DomovoiKit

/// A cover can change inside its file with its URL unchanged (V058: Domovoi
/// writes the Cover Art Archive's front cover in, Undo puts the original
/// back). The server's answer carries a strong `ETag` and `max-age=86400`;
/// the phone keeps the picture and its `ETag`, and once the app has come
/// back to the screen it asks again with `If-None-Match` before trusting it.
///
/// `CoverArtTest.kt` (Android c57e255a): the revalidation, the epoch, and
/// the forgotten misses.
@MainActor
final class MusicCoverRevalidationTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        PlayerArtwork.forgetMisses()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        PlayerArtwork.forgetMisses()
        super.tearDown()
    }

    /// Two different pictures, both over ``MusicCoverStore/smallestImage``.
    static let first = Data([0x89, 0x50, 0x4E, 0x47] + Array(repeating: 0x20, count: 200))
    static let second = Data([0x89, 0x50, 0x4E, 0x47] + Array(repeating: 0x41, count: 200))

    /// The cover route as `music.py` answers it: the picture with its ETag
    /// and max-age, a 304 when `If-None-Match` matches, a 404 for none.
    private final class House: Sendable {
        /// The file's picture and its ETag, or nil for "no cover".
        let art = Shared<(Data, String)?>((MusicCoverRevalidationTests.firstBytes, "\"v1\""))
        /// Every cover request's `If-None-Match` ("" for none) and status.
        let asks = Shared<[(String, Int)]>([])

        func answer(_ request: URLRequest) -> StubURLProtocol.Answer {
            guard let url = request.url,
                  let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            if parts.path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            guard parts.path == "/api/music/library/1/cover" else { return .json("{}", status: 404) }
            let sent = request.value(forHTTPHeaderField: "If-None-Match") ?? ""
            guard let held = art.current else {
                asks.mutate { $0.append((sent, 404)) }
                return StubURLProtocol.Answer(
                    status: 404,
                    headers: ["Content-Type": "application/json", "Cache-Control": "public, max-age=300"],
                    body: Data(#"{"detail":"no cover"}"#.utf8)
                )
            }
            let headers = ["ETag": held.1, "Cache-Control": "public, max-age=86400"]
            if sent == held.1 {
                asks.mutate { $0.append((sent, 304)) }
                return StubURLProtocol.Answer(status: 304, headers: headers, body: Data())
            }
            asks.mutate { $0.append((sent, 200)) }
            var withType = headers
            withType["Content-Type"] = "image/png"
            return StubURLProtocol.Answer(status: 200, headers: withType, body: held.0)
        }
    }

    /// Plain data at file scope's reach for the nonisolated `House`.
    nonisolated static let firstBytes = Data([0x89, 0x50, 0x4E, 0x47] + Array(repeating: 0x20, count: 200))

    private func rig(_ house: House = House()) -> (MusicCoverStore, MusicCoverRenewal, House) {
        StubURLProtocol.dispatch { house.answer($0) }
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let renewal = MusicCoverRenewal()
        return (MusicCoverStore(in: container, renewal: renewal), renewal, house)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    private func picture(_ covers: MusicCoverStore) -> Data? {
        if case .image(let bytes) = covers.cover(1) { return bytes }
        return nil
    }

    // MARK: - the revalidation

    /// The defect: a cover held was shown for as long as the app ran,
    /// without asking, so art written into the file never appeared.
    func testAHeldCoverIsAskedAgainWithItsETagAfterTheAppReturns() async {
        let (covers, renewal, house) = rig()
        covers.want(1)
        await settleUntil({ self.picture(covers) != nil }, "the first picture")
        XCTAssertEqual(Self.first, picture(covers))

        // The same epoch: a list scrolled back and forth asks nothing.
        for _ in 0..<5 { covers.want(1) }
        for _ in 0..<50 { await Task.yield() }
        XCTAssertEqual(1, house.asks.current.count, "asked again within one epoch")

        renewal.appReturned()
        covers.want(1)
        await settleUntil({ house.asks.current.count == 2 }, "the second ask")
        let second = house.asks.current.last
        XCTAssertEqual("\"v1\"", second?.0, "the held picture's ETag goes as If-None-Match")
        XCTAssertEqual(304, second?.1)
        await settleUntil({ !covers.isAsking(1) }, "the 304 settling")
        XCTAssertEqual(Self.first, picture(covers), "a 304 keeps the picture")

        // Confirmed for this epoch: not asked again.
        covers.want(1)
        for _ in 0..<50 { await Task.yield() }
        XCTAssertEqual(2, house.asks.current.count)
    }

    /// The defect: the new art written into the file never replaced the old.
    func testAChangedCoverReplacesTheHeldOne() async {
        let (covers, renewal, house) = rig()
        covers.want(1)
        await settleUntil({ self.picture(covers) != nil }, "the first picture")

        house.art.set((Self.second, "\"v2\""))
        renewal.appReturned()
        covers.want(1)
        // Still drawn while it is asked about: nothing flickers.
        XCTAssertEqual(Self.first, picture(covers), "the held picture stays up while it is asked about")
        await settleUntil({ self.picture(covers) == Self.second }, "the new picture")
        XCTAssertEqual("\"v1\"", house.asks.current.last?.0)
        XCTAssertEqual(200, house.asks.current.last?.1)

        // And the new ETag is the one sent next time.
        renewal.appReturned()
        covers.want(1)
        await settleUntil({ house.asks.current.count == 3 }, "the third ask")
        XCTAssertEqual("\"v2\"", house.asks.current.last?.0)
    }

    /// The defect: Undo took the art out of the file and the phone kept
    /// showing it.
    func testACoverTakenOutOfTheFileIsTakenAway() async {
        let (covers, renewal, house) = rig()
        covers.want(1)
        await settleUntil({ self.picture(covers) != nil }, "the first picture")
        house.art.set(nil)
        renewal.appReturned()
        covers.want(1)
        await settleUntil({ covers.cover(1) == .absent }, "the 404")
    }

    /// The defect: a remembered "no cover" was never asked again, so a song
    /// that gained a cover kept the placeholder for the rest of the run.
    func testARememberedNoCoverIsAskedAgainAfterTheAppReturns() async {
        let house = House()
        house.art.set(nil)
        let (covers, renewal, _) = rig(house)
        covers.want(1)
        await settleUntil({ covers.cover(1) == .absent }, "the 404")
        covers.want(1)
        for _ in 0..<50 { await Task.yield() }
        XCTAssertEqual(1, house.asks.current.count, "a 404 is not asked again within an epoch")

        house.art.set((Self.second, "\"v2\""))
        renewal.appReturned()
        covers.want(1)
        await settleUntil({ self.picture(covers) == Self.second }, "the cover it gained")
        XCTAssertEqual("", house.asks.current.last?.0, "nothing held, so nothing to condition on")
    }

    /// A blip while asking keeps the picture drawn, and asks again next time.
    func testAFailedRevalidationKeepsThePicture() async {
        let failing = Shared(false)
        let renewal = MusicCoverRenewal()
        let covers = MusicCoverStore(renewal: renewal, revalidate: { _, etag in
            if failing.current {
                throw DomovoiError.serverTrouble(ApiFailure(status: 503, message: "503: x"))
            }
            return .picture(Self.firstBytes, etag: etag ?? "\"v1\"")
        })
        covers.want(1)
        await settleUntil({ self.picture(covers) != nil }, "the first picture")
        failing.set(true)
        renewal.appReturned()
        covers.want(1)
        await settleUntil({ !covers.isAsking(1) }, "the failed ask")
        XCTAssertEqual(Self.first, picture(covers), "a blip does not take the picture away")
    }

    // MARK: - the pieces

    func testA304ReadsAsUnchangedAndAnETagIsKept() {
        XCTAssertEqual(.unchanged, MusicCoverAnswer.from(status: 304, body: Data(), etag: "\"v1\""))
        XCTAssertEqual(
            .picture(Self.first, etag: "\"v1\""),
            MusicCoverAnswer.from(status: 200, body: Self.first, etag: " \"v1\" ")
        )
        XCTAssertEqual(.picture(Self.first, etag: nil), MusicCoverAnswer.from(status: 200, body: Self.first, etag: ""))
    }

    /// The key is ready for a versioned cover URL; today there is none.
    func testTheKeysPathIsTheCoverRouteAndAVersionIsItsOwnKey() {
        XCTAssertEqual("/api/music/library/7/cover", MusicCoverKey(trackID: 7).path)
        XCTAssertEqual("/api/music/library/7/cover?v=abc123", MusicCoverKey(trackID: 7, version: "abc123").path)
        XCTAssertNotEqual(MusicCoverKey(trackID: 7, version: "a"), MusicCoverKey(trackID: 7, version: "b"))
    }

    /// The defect: the lock screen's fetch went through `URLSession.shared`,
    /// whose cache obeys `max-age=86400`, so a changed cover kept its old
    /// sleeve on the lock screen for a day.
    func testTheLockScreenAlwaysRevalidates() throws {
        let url = try XCTUnwrap(URL(string: "http://10.0.0.42:6369/api/music/library/7/cover"))
        let request = PlayerArtwork.request(url)
        XCTAssertEqual(.reloadRevalidatingCacheData, request.cachePolicy)
        XCTAssertEqual("no-cache", request.value(forHTTPHeaderField: "Cache-Control"))
        XCTAssertEqual(url, request.url)
    }
}
