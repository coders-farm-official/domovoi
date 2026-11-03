import Foundation
import XCTest
@testable import DomovoiKit

/// The engine the player tests drive instead of `AVQueuePlayer`.
///
/// It **records every call**, which is what lets a case assert that the model
/// and the engine stayed in step through a move or a remove — the thing that
/// cannot be checked by looking at either one alone. `AVQueuePlayer` has no
/// move and no jump-to-index (MAP §4.2), so `setItems` is the call that stands
/// in for all of them, and a case on a jump is a case on what `setItems` was
/// handed.
@MainActor
final class FakePlayerEngine: PlayerAudioEngine {

    enum Call: Equatable {
        case setItems(urls: [String], startingAt: Int, offsetSec: Double)
        case play
        case pause
        case seek(Double)
        case rate(Float)
        case removeAll
    }

    var calls: [Call] = []

    var didFinishItem: (@MainActor () -> Void)?
    var didTick: (@MainActor () -> Void)?

    /// What the engine pretends to be doing. The tests set these, since a fake
    /// that always answered "playing" would make `resumeLocal`'s
    /// re-preparation untestable.
    var currentTimeSec: Double = 0
    var durationSec: Double?
    var status: PlayerEngineStatus = .idle
    var canPlayFastForward = true
    var canPlaySlowForward = true

    /// What the model last said about the sources it handed over — the pitch
    /// algorithm is per item, so a case can check that a book in a mixed queue
    /// gets the speech one.
    var spokenAudio = false

    private(set) var held: [String] = []

    var itemCount: Int { held.count }
    var currentIndex: Int? { held.isEmpty ? nil : 0 }

    /// The last list the engine was handed, as paths — what a case compares
    /// against the model's queue.
    var lastSetItems: [String] {
        for call in calls.reversed() {
            if case .setItems(let urls, _, _) = call { return urls }
        }
        return []
    }

    var lastOffset: Double? {
        for call in calls.reversed() {
            if case .setItems(_, _, let offset) = call { return offset }
        }
        return nil
    }

    var playCount: Int { calls.filter { $0 == .play }.count }
    var pauseCount: Int { calls.filter { $0 == .pause }.count }

    var lastRate: Float? {
        for call in calls.reversed() {
            if case .rate(let r) = call { return r }
        }
        return nil
    }

    var seeks: [Double] {
        calls.compactMap { if case .seek(let s) = $0 { return s } else { return nil } }
    }

    func setItems(_ urls: [URL], startingAt: Int, offsetSec: Double) {
        held = urls.map(\.absoluteString)
        calls.append(.setItems(urls: held, startingAt: startingAt, offsetSec: offsetSec))
        currentTimeSec = offsetSec
    }

    /// The current source reaching its end, **as `AVQueuePlayer` does it**: the
    /// finished item is gone from the queue before anyone is told. Without the
    /// drop a fake can never run out of sources, which is how an engine left
    /// holding nothing after the last part of a folder book went unseen.
    func finishCurrentItem() {
        if !held.isEmpty { held.removeFirst() }
        didFinishItem?()
    }

    func play() {
        calls.append(.play)
        status = .playing
    }

    func pause() {
        calls.append(.pause)
        status = .readyToPlay
    }

    func seek(toSec sec: Double) {
        calls.append(.seek(sec))
        currentTimeSec = sec
    }

    func setRate(_ rate: Float) { calls.append(.rate(rate)) }

    func removeAll() {
        held = []
        calls.append(.removeAll)
        status = .idle
    }

    func forget() { calls = [] }
}

/// A position store, for the Audiobooks seam.
final class FakePlayerPositionStore: PlayerPositionStore, @unchecked Sendable {
    var owned: Set<PlayerKind> = [.audiobook]
    var place = PlayerPosition(positionSec: 120, speed: 1.5)
    private(set) var recorded: [(uid: String, server: PlayerServerScope?, positionSec: Double, speed: Float)] = []

    func owns(_ item: PlayerItem) -> Bool { owned.contains(item.kind) }

    func open(_ item: PlayerItem, server: PlayerServerScope?) async -> PlayerPosition { place }

    func record(_ item: PlayerItem, server: PlayerServerScope?, positionSec: Double, speed: Float) async {
        recorded.append((item.uid, server, positionSec, speed))
    }
}

// MARK: - the house these tests talk to

/// A ``PlayerModel`` and the four fakes under it, over a stubbed server.
///
/// Every player case needs the same five pieces, so they are assembled once.
/// The client is real — a cast is a POST and a position is a GET, and a case
/// that faked the client would be testing its own fake instead of the body
/// this model sends.
@MainActor
final class PlayerHarness {
    let model: PlayerModel
    let engine: FakePlayerEngine
    let session: RecordingPlayerAudioSession
    let nowPlaying: RecordingNowPlayingPublisher
    let clock: PlayerTestClock

    static let serverAddress = "http://10.0.0.42:6369"

    init(deviceID: String = "phone-1", address: String = PlayerHarness.serverAddress) {
        let engine = FakePlayerEngine()
        let session = RecordingPlayerAudioSession()
        let publisher = RecordingNowPlayingPublisher()
        let clock = PlayerTestClock(nowMs: 10_000)
        let store = InMemoryShellStore(serverURL: address, token: "household-abc")
        store.trustServer(address, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        self.engine = engine
        self.session = session
        self.nowPlaying = publisher
        self.clock = clock
        self.model = PlayerModel(
            client: container.client,
            deviceID: deviceID,
            engine: engine,
            session: session,
            nowPlaying: publisher,
            clock: clock
        )
    }

    /// Wait for the model to reach a state, by yielding rather than sleeping.
    func settle(
        until done: @MainActor () -> Bool,
        _ what: String = "the player",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }
}

/// A house that answers the player's routes, with each answer a test can move.
///
/// Shaped like `MusicRoomActionsTests.House`: a dispatcher rather than a queue,
/// because the room poll asks for now-playing over and over and a queue would
/// run out.
final class PlayerHouse: Sendable {
    /// A **bare array**, not `{"rooms": …}` — the wrapper trap that left four
    /// of Home's sections unaudited (`fixtures/README.md`).
    let nowPlaying = Shared("""
    [{"room_id":"office","state":"play","elapsed_sec":42,"favorited":false,"track_id":12,\
    "song":{"title":"Atmosphere","artist":"Joy Division","duration_sec":390}},\
    {"room_id":"den","state":"stop"}]
    """)
    let podcastPosition = Shared(#"{"position_sec":0,"speed":1.0}"#)
    let writeStatus = Shared(200)
    let writeBody = Shared(#"{"ok":true}"#)

    /// How many position requests have arrived, and a gate to park one on.
    ///
    /// Parking a request in the dispatcher is what `StubURLProtocol`'s own
    /// comment says the dispatcher is called outside its lock **for**. It is
    /// the only way to be certain a change lands *inside* a round trip rather
    /// than before or after it — and a read-after-await case that switched
    /// before or after would pass against the broken code.
    let positionArrivals = Shared(0)
    let holdPosition = Shared(false)
    private let gate = DispatchSemaphore(value: 0)

    /// Let a parked position request answer.
    func release() {
        holdPosition.set(false)
        gate.signal()
    }

    func install() {
        StubURLProtocol.dispatch { [self] request in
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            switch parts.path {
            case "/api/health":
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            case "/api/music/now-playing":
                return .json(self.nowPlaying.current)
            default:
                if parts.path.hasPrefix("/api/podcasts/positions/")
                    || parts.path.hasSuffix("/position") {
                    self.positionArrivals.mutate { $0 += 1 }
                    if self.holdPosition.current {
                        // Bounded, so a test that forgets to release fails on
                        // its assertion rather than hanging the suite.
                        _ = self.gate.wait(timeout: .now() + 0.8)
                    }
                    return .json(self.podcastPosition.current)
                }
                guard self.writeStatus.current == 200 else {
                    return .json(#"{"detail":"the music player is not answering","failed":"music_player"}"#,
                                 status: self.writeStatus.current)
                }
                return .json(self.writeBody.current)
            }
        }
    }

    /// The paths the phone asked for, in order — what a case asserts the cast
    /// actually sent, and in which order.
    static var paths: [String] {
        StubURLProtocol.recorded.compactMap { $0.url?.path }
    }

    static func methods(for path: String) -> [String] {
        StubURLProtocol.recorded
            .filter { $0.url?.path == path }
            .map { $0.httpMethod ?? "" }
    }

    /// The bytes that actually left the phone for `path`.
    ///
    /// `StubURLProtocol` reads the body stream back into `httpBody` for this
    /// reason: a `URLProtocol` is handed the body as a stream, so a test
    /// asserting on `recorded[0].httpBody` saw nil for a POST that plainly had
    /// one (measured 2026-10-28, in that file's own comment).
    static func bodies(for path: String) -> [[String: Any]] {
        StubURLProtocol.recorded
            .filter { $0.url?.path == path }
            .compactMap { $0.httpBody }
            .compactMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
    }
}

// MARK: - the seed, chosen so an assertion can only pass for the right reason

enum PlayerSeed {
    /// No word is shared between two of these, so a case asserting "the room's
    /// track, not the phone's" cannot pass by accident.
    static let transmission = PlayerItem.fromTrack(id: 11, title: "Transmission", artist: "Joy Division", durationSec: 210)
    static let atmosphere = PlayerItem.fromTrack(id: 12, title: "Atmosphere", artist: "Joy Division", durationSec: 390)
    static let decades = PlayerItem.fromTrack(id: 13, title: "Decades", artist: "Joy Division", durationSec: 400)
    static let station = PlayerItem.fromStation(id: 4, name: "Hollow Earth Radio")
    static let episode = PlayerItem.fromEpisode(
        id: 9, title: "The Lost Coast", show: "Nightwaves", durationSec: 3600,
        artwork: "/api/podcasts/subscriptions/3/artwork",
        chapters: [
            PlayerChapter(title: "Opening", startSec: 0),
            PlayerChapter(title: "The Lighthouse", startSec: 600),
            PlayerChapter(title: "Closing", startSec: 3000),
        ]
    )
    static let phoneSong = PlayerItem.fromDeviceAudio(
        id: 77, title: "Pocketful", artist: "Nobody", uri: "file:///var/music/pocketful.m4a"
    )
    /// The folder book: three chapters, a different file each.
    static let folderBook = PlayerItem.fromBook(
        id: 2, title: "Wayfinding", author: "M. Hale",
        chapters: [
            PlayerChapter(title: "Setting Out", startSec: 0),
            PlayerChapter(title: "The Long Road", startSec: 100),
            PlayerChapter(title: "Coming Home", startSec: 250),
        ],
        parts: [
            PlayerMediaPart(src: "/api/audiobooks/2/audio?file=01.mp3", durationSec: 100),
            PlayerMediaPart(src: "/api/audiobooks/2/audio?file=02.mp3", durationSec: 150),
            PlayerMediaPart(src: "/api/audiobooks/2/audio?file=03.mp3", durationSec: 50),
        ]
    )
}
