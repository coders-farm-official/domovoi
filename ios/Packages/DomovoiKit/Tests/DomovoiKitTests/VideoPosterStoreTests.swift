import Foundation
import XCTest
@testable import DomovoiKit

/// The poster cache, and the one URL in this app that carries the household
/// token.
///
/// Neither has a counterpart on Android: Coil holds that phone's thumbnails
/// and its player carries the token in a header, so both of these are iOS's
/// own and neither is specified anywhere else.
@MainActor
final class VideoPosterStoreTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// A fetch a test drives: it records what was asked for and answers with
    /// whatever has been set for that path.
    private final class Posters: Sendable {
        /// Path → the bytes to answer with, or nil to throw.
        let answers = Shared<[String: Data]>([:])
        let asked = Shared<[String]>([])
        /// Paths whose answer waits until ``release()``.
        let held = Shared<Set<String>>([])

        func release() {
            held.set([])
        }

        /// `count` bytes of nothing, which is a picture as far as this store
        /// is concerned.
        static func image(_ count: Int) -> Data {
            Data(repeating: 0x42, count: count)
        }

        var fetch: @Sendable (String) async throws -> Data {
            let answers = self.answers
            let asked = self.asked
            let held = self.held
            return { path in
                asked.mutate { $0.append(path) }
                for _ in 0..<settleYieldLimit {
                    if !held.current.contains(path) { break }
                    try Task.checkCancellation()
                    await Task.yield()
                }
                guard let bytes = answers.current[path] else {
                    throw DomovoiError.unreachable("no poster")
                }
                return bytes
            }
        }
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the store",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    // MARK: - asking for one

    func testAPosterIsLoadingThenAnImage() async throws {
        let posters = Posters()
        posters.answers.set(["/p/1": Posters.image(512)])
        let store = VideoPosterStore(fetch: posters.fetch)

        XCTAssertNil(store.poster("/p/1"))
        store.want("/p/1")
        XCTAssertEqual(.loading, store.poster("/p/1"))

        await settleUntil({ store.poster("/p/1") != .loading }, "the poster")
        XCTAssertEqual(.image(Posters.image(512)), store.poster("/p/1"))
        XCTAssertEqual(["/p/1"], posters.asked.current)
    }

    /// A 204 from the poster route is "ffmpeg got no frame", which arrives as
    /// an empty body rather than an error. It is a **settled** answer: the
    /// tile draws its glyph and never asks again.
    func testAnEmptyAnswerIsAbsentRatherThanFailed() async throws {
        let posters = Posters()
        posters.answers.set(["/p/1": Data()])
        let store = VideoPosterStore(fetch: posters.fetch)

        store.want("/p/1")
        await settleUntil({ store.poster("/p/1") != .loading }, "the poster")

        XCTAssertEqual(.absent, store.poster("/p/1"))
    }

    /// A body too small to be a picture is absent too. A handful of bytes is
    /// what a proxy's error page or a truncated answer looks like, and
    /// handing those to the image decoder draws a broken tile.
    func testABodyTooSmallToBeAPictureIsAbsent() async throws {
        let posters = Posters()
        posters.answers.set([
            "/small": Posters.image(VideoPosterStore.smallestImage - 1),
            "/just": Posters.image(VideoPosterStore.smallestImage),
        ])
        let store = VideoPosterStore(fetch: posters.fetch)

        store.want("/small")
        store.want("/just")
        await settleUntil(
            { store.poster("/small") != .loading && store.poster("/just") != .loading },
            "both posters"
        )

        XCTAssertEqual(.absent, store.poster("/small"))
        XCTAssertEqual(.image(Posters.image(VideoPosterStore.smallestImage)), store.poster("/just"))
    }

    /// A tile is recycled and `want` is called again on every appearance, so
    /// nothing may re-fetch what is settled or in flight.
    func testNothingSettledOrInFlightIsAskedForTwice() async throws {
        let posters = Posters()
        posters.answers.set(["/p/1": Posters.image(512), "/p/2": Data()])
        let store = VideoPosterStore(fetch: posters.fetch)
        posters.held.set(["/p/1", "/p/2"])

        store.want("/p/1")
        store.want("/p/1")
        store.want("/p/1")
        // Settled for, not asserted straight away: the fetch records what it
        // was asked for from inside its own task, and nothing of that task
        // has run yet at this point in a method with no await in it.
        await settleUntil({ !posters.asked.current.isEmpty }, "the first fetch")
        XCTAssertEqual(["/p/1"], posters.asked.current)

        posters.release()
        await settleUntil({ store.poster("/p/1") != .loading }, "the image")
        store.want("/p/1")
        XCTAssertEqual(["/p/1"], posters.asked.current)

        store.want("/p/2")
        await settleUntil({ store.poster("/p/2") != .loading }, "the absence")
        store.want("/p/2")
        XCTAssertEqual(["/p/1", "/p/2"], posters.asked.current)
    }

    /// A failure **is** retried, because that one may have been the network.
    func testAFailedPosterIsAskedForAgain() async throws {
        let posters = Posters()
        let store = VideoPosterStore(fetch: posters.fetch)

        store.want("/p/1")
        await settleUntil({ store.poster("/p/1") == .failed }, "the failure")
        XCTAssertEqual(["/p/1"], posters.asked.current)

        posters.answers.set(["/p/1": Posters.image(512)])
        store.want("/p/1")
        await settleUntil({ store.poster("/p/1") != .loading }, "the retry")

        XCTAssertEqual(["/p/1", "/p/1"], posters.asked.current)
        XCTAssertEqual(.image(Posters.image(512)), store.poster("/p/1"))
    }

    // MARK: - the cap

    func testItHoldsAtMostItsCapacity() async throws {
        let posters = Posters()
        var answers: [String: Data] = [:]
        for index in 0...VideoPosterStore.capacity { answers["/p/\(index)"] = Posters.image(128) }
        posters.answers.set(answers)
        let store = VideoPosterStore(fetch: posters.fetch)

        // One at a time, each landing before the next is asked for: with a
        // hundred in flight at once nothing can be evicted at all (every
        // candidate is still being fetched), which is the *next* test.
        for index in 0...VideoPosterStore.capacity {
            store.want("/p/\(index)")
            await settleUntil({ store.poster("/p/\(index)") != .loading }, "poster \(index)")
        }

        XCTAssertEqual(VideoPosterStore.capacity, store.count)
        // Oldest first: the very first one asked for is the one that went.
        XCTAssertNil(store.poster("/p/0"))
        XCTAssertNotNil(store.poster("/p/1"))
    }

    /// Never evict something still being fetched.
    ///
    /// What the guard buys, precisely: without it the overflow drops `/p/0`
    /// from the dictionary while its fetch is still out, and the tile that
    /// asked for it goes from "loading" to knowing nothing — no spinner, no
    /// glyph, no second request either, because `inFlight` still holds the
    /// path. So the assertion that catches it is the `.loading` below, taken
    /// while the cache is a hundred and one deep.
    func testSomethingStillBeingFetchedIsNotEvicted() async throws {
        let posters = Posters()
        var answers: [String: Data] = [:]
        for index in 0...VideoPosterStore.capacity { answers["/p/\(index)"] = Posters.image(128) }
        posters.answers.set(answers)
        let store = VideoPosterStore(fetch: posters.fetch)

        // The first one is held open while the rest fill the cache past its
        // capacity, so it is the one eviction would reach for.
        posters.held.set(["/p/0"])
        store.want("/p/0")
        for index in 1...VideoPosterStore.capacity { store.want("/p/\(index)") }

        // **This is the assertion that catches it.** Nothing asynchronous has
        // run yet — the method has no await above this line — so the only
        // thing that has touched `/p/0` is the overflow inside the hundred
        // and first `want`. With the guard it is still loading; without it,
        // it is gone.
        XCTAssertEqual(.loading, store.poster("/p/0"))

        posters.release()
        await settleUntil({ store.poster("/p/0") != .loading }, "the held poster")

        // What it becomes afterwards is **not** asserted, and that is not a
        // gap. Once its fetch has landed, `/p/0` is the oldest entry in a
        // cache holding one more than it may, so the next poster to arrive
        // evicts it — correctly. The guard's whole job is that it is not
        // dropped *while the fetch is out*, which is the line above. What
        // must hold either way is that it was fetched once: an eviction
        // mid-flight is what would make a tile ask a second time.
        XCTAssertEqual(1, posters.asked.current.filter { $0 == "/p/0" }.count)
        XCTAssertLessThanOrEqual(store.count, VideoPosterStore.capacity + 1)
    }

    // MARK: - moving house

    /// **The read-after-await guard.** Two houses can hold a library with the
    /// same id and a video with the same path, so the old server's answer and
    /// the new server's request have the *same key*. An answer that landed
    /// after the move would be drawn as the new house's thumbnail for a video
    /// it has never seen.
    func testAnAnswerFromTheOldHouseLandsNowhere() async throws {
        let posters = Posters()
        posters.answers.set(["/p/1": Posters.image(512)])
        let store = VideoPosterStore(fetch: posters.fetch)
        posters.held.set(["/p/1"])

        store.want("/p/1")
        XCTAssertEqual(.loading, store.poster("/p/1"))

        store.serverChanged()
        XCTAssertNil(store.poster("/p/1"))
        XCTAssertEqual(0, store.count)

        // The old house answers now. It must land nowhere at all.
        posters.release()
        for _ in 0..<2000 { await Task.yield() }
        XCTAssertNil(store.poster("/p/1"))
        XCTAssertEqual(0, store.count)
    }

    /// And the same path may be asked for again straight away: the move must
    /// not leave it marked in flight for ever.
    func testThePathMayBeAskedForAgainAfterTheMove() async throws {
        let posters = Posters()
        posters.answers.set(["/p/1": Posters.image(512)])
        let store = VideoPosterStore(fetch: posters.fetch)
        posters.held.set(["/p/1"])

        store.want("/p/1")
        store.serverChanged()
        posters.release()

        store.want("/p/1")
        await settleUntil({ store.poster("/p/1") != .loading }, "the new house's poster")

        XCTAssertEqual(.image(Posters.image(512)), store.poster("/p/1"))
        XCTAssertEqual(["/p/1", "/p/1"], posters.asked.current)
    }

    /// `absent` rather than `none`, because the type is read as
    /// `VideoPoster?` everywhere and `case .none` in a switch over that
    /// optional means **the optional's** none. A test cannot catch the
    /// mistake, so this is the note that says why the name is what it is —
    /// and the assertion that the two really are different things.
    func testAnAbsentPosterIsNotAMissingOne() {
        let absent: VideoPoster? = .absent
        let missing: VideoPoster? = nil

        XCTAssertNotEqual(absent, missing)
        XCTAssertNotNil(absent)
    }

    // MARK: - the stream URL, which carries the token

    private func container(
        serverURL: String? = nil,
        token: String? = "household-abc"
    ) -> AppContainer {
        let store = InMemoryShellStore(serverURL: serverURL ?? server, token: token)
        store.trustServer(server, identity: TestIdentity.real.pin)
        return AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
    }

    private func answerHealth() {
        StubURLProtocol.dispatch { request in
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            guard parts.path == "/api/health" else { return .json("{}", status: 404) }
            let challenge = parts.queryItems?.first { $0.name == "challenge" }?.value
            return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
        }
    }

    /// The token goes in the **query**, because `AVPlayer` cannot be given a
    /// header without private API, and the server offers `?device_token=` for
    /// exactly this.
    func testTheStreamUrlCarriesTheTokenInItsQuery() async throws {
        answerHealth()
        let box = container()
        let path = VideoPath.stream(VideoRow(libraryID: "core:videos", rel: "a b.mp4"))

        let url = try await VideoStream.url(for: path, in: box)

        XCTAssertEqual(
            "http://10.0.0.42:6369/api/videos/stream?library_id=core%3Avideos&path=a%20b.mp4&device_token=household-abc",
            url.absoluteString
        )
    }

    /// A path that already has no query would take a `?`; every videos route
    /// has one, so this is the `&` branch — asserted on the wire spelling,
    /// because `URL.path` percent-decodes and would hide an escaping bug.
    func testTheTokenIsJoinedWithTheQueryThatIsAlreadyThere() async throws {
        answerHealth()
        let box = container()

        let withQuery = try await VideoStream.url(for: "/api/videos/stream?library_id=a", in: box)
        let without = try await VideoStream.url(for: "/api/videos/poster", in: box)

        XCTAssertTrue(withQuery.absoluteString.hasSuffix("?library_id=a&device_token=household-abc"))
        XCTAssertTrue(without.absoluteString.hasSuffix("/api/videos/poster?device_token=household-abc"))
    }

    /// An unpaired phone gets a sentence, not a URL with an empty token on
    /// the end — which the server would answer 401 to and the player would
    /// report as "can't play this video".
    func testAnUnpairedPhoneIsRefusedRatherThanGivenAnEmptyToken() async throws {
        answerHealth()
        let box = container(token: nil)
        let path = VideoPath.stream(VideoRow(libraryID: "aa", rel: "a.mp4"))

        do throws(DomovoiError) {
            let url = try await VideoStream.url(for: path, in: box)
            XCTFail("expected a refusal, got \(url.absoluteString)")
        } catch let trouble {
            XCTAssertEqual(.notPermitted(VideoStrings.pairToPlay), trouble)
            XCTAssertEqual(VideoStrings.pairToPlay, VideoStream.refusal(trouble).title)
        }
    }

    /// No server, no URL. `absolute("/api/…")` on an empty base would make a
    /// relative string that `URL(string:)` accepts and nothing can play.
    func testWithNoServerThereIsNoUrlAtAll() async throws {
        answerHealth()
        let box = container(serverURL: "")

        do throws(DomovoiError) {
            let url = try await VideoStream.url(for: "/api/videos/stream?a=1", in: box)
            XCTFail("expected a refusal, got \(url.absoluteString)")
        } catch let trouble {
            XCTAssertEqual(.noServer, trouble)
            XCTAssertEqual(DomovoiError.noServer.message, VideoStream.refusal(trouble).title)
        }
    }

    /// A server that has not proved itself on this network **right now** does
    /// not get the token. The gate is taken on every activation, not once
    /// when the feed opened — which is the difference between a phone that
    /// stops handing out the token when the network changes and one that
    /// does not.
    func testAServerThatWillNotProveItselfGetsNoToken() async throws {
        // `/api/health` answers with something that is not this server's
        // signed proof, so the gate refuses.
        StubURLProtocol.dispatch { _ in .json(#"{"ok":true}"#) }
        let box = container()
        let path = VideoPath.stream(VideoRow(libraryID: "aa", rel: "a.mp4"))

        do throws(DomovoiError) {
            let url = try await VideoStream.url(for: path, in: box)
            XCTFail("expected a refusal, got \(url.absoluteString)")
        } catch let trouble {
            // Which refusal depends on the gate; what matters is that it is
            // one, and that the sentence drawn over the video says the video
            // cannot play *yet*.
            XCTAssertNotEqual(.noServer, trouble)
            let said = VideoStream.refusal(trouble)
            XCTAssertFalse(said.title.isEmpty)
            XCTAssertEqual("", said.detail)
        }
    }

    /// Every refusal's second line is empty, and the view draws it only when
    /// it is not. There is nothing true to put there: the reason is already
    /// the whole sentence.
    func testEveryRefusalIsOneSentenceAndNoAdvice() {
        let troubles: [DomovoiError] = [
            .noServer,
            .notPermitted("nope"),
            .unreachable("no route"),
            .tokenThrottled(waitMs: 5000),
            .identityHeld("checking"),
            .notDomovoi("that is a printer"),
        ]

        for trouble in troubles {
            let said = VideoStream.refusal(trouble)
            XCTAssertFalse(said.title.isEmpty, "\(trouble)")
            XCTAssertEqual("", said.detail, "\(trouble)")
        }
    }

    /// A refusal the person is meant to act on says what to do; the rest say
    /// the video cannot play yet and why.
    func testAPairingRefusalIsSaidInItsOwnWordsAndTheRestAreWrapped() {
        XCTAssertEqual(
            VideoStrings.pairToPlay,
            VideoStream.refusal(.notPermitted(VideoStrings.pairToPlay)).title
        )
        XCTAssertEqual(
            VideoStrings.cannotPlayYet(DomovoiError.tokenThrottled(waitMs: 5000).message),
            VideoStream.refusal(.tokenThrottled(waitMs: 5000)).title
        )
    }
}
