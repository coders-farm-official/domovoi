import Foundation
import AVFoundation

/// The only file in this item that touches AVFoundation, and the seam that
/// makes everything else testable without a Simulator.
///
/// `AVQueuePlayer` cannot do what ExoPlayer's playlist does. It has
/// `insert(_:after:)`, `remove(_:)`, `removeAllItems()` and advancing to the
/// next item — and that is all: **no** `seekToPreviousMediaItem`, **no** move,
/// **no** jump to index. So ``PlayerModel`` keeps `queue` and `index` as the
/// truth and tells the engine what to hold, and a jump, a previous across
/// items and a move are all one call — ``setItems(_:startingAt:offsetSec:)``,
/// a rebuild from the new index. That is the whole reason this protocol has
/// the shape it has (MAP §4.2).
public enum PlayerEngineStatus: Equatable, Sendable {
    /// Nothing loaded, or the player was stopped: a `play()` needs the items
    /// set again. `PlayerController.kt:409`'s `STATE_IDLE` recovery.
    case idle
    case readyToPlay
    case playing
    case failed(String)
}

/// What ``PlayerModel`` asks of an audio engine. Implemented for real by
/// ``AVQueueAudioEngine`` and by a fake in the package tests.
@MainActor
public protocol PlayerAudioEngine: AnyObject {

    /// Hand the engine these sources, start on `startingAt`, `offsetSec` into
    /// it. The rebuild-from-index that stands in for every move
    /// `AVQueuePlayer` cannot do.
    func setItems(_ urls: [URL], startingAt: Int, offsetSec: Double)

    func play()
    func pause()

    /// Seconds into the **current source**. The model converts a folder book's
    /// global position (``PlayerBookTimeline``), so the engine never knows a
    /// book is one thing.
    func seek(toSec sec: Double)

    /// Whether what the engine is about to hold is **speech**.
    ///
    /// Set before ``setItems(_:startingAt:offsetSec:)`` — the pitch algorithm
    /// is a per-item property, and one engine holds a queue that mixes songs,
    /// podcasts and books, so a value fixed for the engine's life gave a
    /// narrator the music algorithm (and the other way round) for every item
    /// after the first.
    var spokenAudio: Bool { get set }

    /// Both `rate` **and** `defaultRate`.
    ///
    /// Setting only `rate` means the next `play()` silently goes back to 1.0 —
    /// `defaultRate` is what `play()` resumes at. Two properties, one honest
    /// speed, and it is the implementation's job to keep them equal.
    func setRate(_ rate: Float)

    var currentTimeSec: Double { get }
    /// Nil until the asset has loaded one, and for a live stream.
    var durationSec: Double? { get }
    var status: PlayerEngineStatus { get }
    /// How many sources it is holding; what the model compares its queue
    /// against before asking for an index that is not there.
    var itemCount: Int { get }
    /// Which source is current, or nil when none is.
    var currentIndex: Int? { get }

    /// Whether this source can actually play at a rate above 1 / below 1.
    ///
    /// A speed control's options come from **this**, not a fixed list: a 2×
    /// button on a stream that cannot do 2× is a control that cannot do what
    /// it says (`DECISIONS.md`, "A playback rate the track cannot do is not
    /// offered").
    var canPlayFastForward: Bool { get }
    var canPlaySlowForward: Bool { get }

    /// Drop everything. The model calls this on `clear()` and on a server
    /// change that takes the whole queue.
    func removeAll()

    /// Called when the current source reaches its end — the model's cue to
    /// advance the index, or to move to the next **part** of a folder book.
    var didFinishItem: (@MainActor () -> Void)? { get set }

    /// Called when the engine's own position or status moved without the model
    /// asking: the 500 ms ticker's job on Android, a periodic time observer
    /// here.
    var didTick: (@MainActor () -> Void)? { get set }
}

/// The real engine.
///
/// ## What is not, and why there is no control for it
///
/// **No `AVMutableComposition` for a folder book.** See
/// ``PlayerBookTimeline``: one part at a time, with the arithmetic in the
/// model, rather than 60 round trips before the first second of audio.
///
/// **No `AVAssetResourceLoaderDelegate`, and no token in a URL.** Every audio
/// route this player reads is **open** on the server — `music.py:1271`,
/// `podcasts.py:498`, `audiobooks.py:304` carry no auth dependency, and the
/// cover route's docstring says why. `VideoStream.swift` puts the household
/// token in a query string because `/api/videos/stream` is a device-tier read;
/// that compromise is **not** repeated here, and a later route that does need
/// a token must come back to `DECISIONS.md` rather than copy `VideoStream` by
/// reflex.
@MainActor
public final class AVQueueAudioEngine: PlayerAudioEngine {

    private let player = AVQueuePlayer()

    /// The end-of-item observers, in a box the `deinit` can reach.
    ///
    /// A `@MainActor` class cannot touch its isolated storage from `deinit`,
    /// and an item observer that outlives the engine keeps a closure alive
    /// pointing at a dead player — so the tokens live in a nonisolated box
    /// instead of a property, which is the only shape that lets both the main
    /// actor and the deallocator clear them.
    /// The periodic time observer's token lives here too, for the same
    /// reason: `removeTimeObserver` has to be called before the player is
    /// deallocated, and until it is the block keeps firing `didTick` into a
    /// model being torn down. An isolated property could not be reached from
    /// `deinit`, and nothing else ever cleared it — `removeAll()` does not.
    private final class Observers: @unchecked Sendable {
        var tokens: [NSObjectProtocol] = []
        var player: AVQueuePlayer?
        var timeToken: Any?

        /// Just the end-of-item notifications: what a new list of sources
        /// replaces. The time observer outlives every rebuild.
        func clearItemObservers() {
            for token in tokens { NotificationCenter.default.removeObserver(token) }
            tokens = []
        }

        func clear() {
            clearItemObservers()
            if let timeToken { player?.removeTimeObserver(timeToken) }
            timeToken = nil
            player = nil
        }
    }

    private nonisolated let endObservers = Observers()

    /// What the engine was last told to hold, so ``currentIndex`` can answer
    /// without asking `AVQueuePlayer` to tell us where it is — it cannot.
    private var sources: [URL] = []
    private var base = 0

    public var didFinishItem: (@MainActor () -> Void)?
    public var didTick: (@MainActor () -> Void)?

    /// `.timeDomain` for speech, `.spectral` above 2×.
    ///
    /// `.varispeed` is the one that makes a narrator sound like a chipmunk and
    /// must never be the default; `.timeDomain` is cheap and good to about 2×,
    /// which is where almost every podcast listener lives.
    public var spokenAudio: Bool

    public init(spokenAudio: Bool = false) {
        self.spokenAudio = spokenAudio
        player.actionAtItemEnd = .advance
        endObservers.player = player
        endObservers.timeToken = player.addPeriodicTimeObserver(
            forInterval: CMTime(seconds: 0.5, preferredTimescale: 600),
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.didTick?() }
        }
    }

    deinit {
        endObservers.clear()
    }

    public func setItems(_ urls: [URL], startingAt: Int, offsetSec: Double) {
        endObservers.clearItemObservers()
        player.removeAllItems()
        sources = urls
        base = min(max(startingAt, 0), Swift.max(urls.count - 1, 0))
        guard !urls.isEmpty else { return }
        for url in urls[base...] {
            let item = AVPlayerItem(url: url)
            item.audioTimePitchAlgorithm = spokenAudio ? .timeDomain : .spectral
            endObservers.tokens.append(
                NotificationCenter.default.addObserver(
                    forName: AVPlayerItem.didPlayToEndTimeNotification,
                    object: item,
                    queue: .main
                ) { [weak self] _ in
                    MainActor.assumeIsolated { self?.didFinishItem?() }
                }
            )
            player.insert(item, after: nil)
        }
        if offsetSec > 0 { seek(toSec: offsetSec) }
    }

    public func play() { player.play() }

    public func pause() { player.pause() }

    public func seek(toSec sec: Double) {
        player.seek(
            to: CMTime(seconds: Swift.max(sec, 0), preferredTimescale: 600),
            toleranceBefore: .zero,
            toleranceAfter: .zero
        )
    }

    public func setRate(_ rate: Float) {
        // Both, always: see the protocol's comment.
        player.defaultRate = rate
        if player.rate != 0 { player.rate = rate }
    }

    public var currentTimeSec: Double {
        let t = player.currentTime().seconds
        return t.isFinite ? t : 0
    }

    public var durationSec: Double? {
        guard let d = player.currentItem?.duration.seconds, d.isFinite, d > 0 else { return nil }
        return d
    }

    public var status: PlayerEngineStatus {
        guard let item = player.currentItem else { return .idle }
        switch item.status {
        case .failed:
            return .failed(item.error?.localizedDescription ?? "")
        case .readyToPlay:
            return player.timeControlStatus == .playing ? .playing : .readyToPlay
        default:
            return .idle
        }
    }

    public var itemCount: Int { player.items().count }

    public var currentIndex: Int? {
        guard player.currentItem != nil, !sources.isEmpty else { return nil }
        // The engine holds the tail from `base` on, and `AVQueuePlayer` drops
        // each item as it finishes, so what is left says how far it has got.
        let remaining = player.items().count
        let advanced = Swift.max(sources.count - base - remaining, 0)
        return min(base + advanced, sources.count - 1)
    }

    public var canPlayFastForward: Bool { player.currentItem?.canPlayFastForward ?? false }

    public var canPlaySlowForward: Bool { player.currentItem?.canPlaySlowForward ?? false }

    public func removeAll() {
        endObservers.clearItemObservers()
        player.removeAllItems()
        sources = []
        base = 0
    }
}
