import Foundation
import Observation

/// Which long-press sheet is up. `VideoFeed.kt:118`.
public enum VideoFeedSheet: Sendable, Equatable, Identifiable {
    case options
    case playlist
    case details

    public var id: String {
        switch self {
        case .options: return "options"
        case .playlist: return "playlist"
        case .details: return "details"
        }
    }
}

/// What the player should do next.
///
/// Android's `activate(page:)` calls `player.setMediaItem`, `prepare` and
/// `play` itself (`VideoFeed.kt:213`), which makes the decision and the side
/// effect one thing that only an `ExoPlayer` can exercise. Here the decision
/// is a value, so every rule it encodes — a page the player already holds is
/// not reloaded, the resume is spent on the first page shown, leaving a page
/// saves its position — is a thing a test can state without an `AVPlayer`.
public enum FeedAction: Sendable, Equatable {
    /// Load this video and seek to `startMs` before playing.
    case load(FeedVideo, startMs: Int64)
    /// The player is already on this video: play it.
    case play
    /// There is no such page.
    case nothing
}

/// What became of the queue when a video was dropped from it.
public enum FeedRemoval: Sendable, Equatable {
    /// No video with that key — a double-tap on the same remove button.
    case notThere
    /// The viewer dropped the last one: the feed closes, with **no key**,
    /// because there is no video they ended on.
    case emptied
    /// Still playing; this is what the player does about it.
    case stillThere(FeedAction)
}

/// The swipe feed's state: the session queue, which page is showing, which
/// sheet is up, and what the player is asked to do.
///
/// `VideoFeed.kt:144`'s `VideoFeedPlayer`, whose state is eleven `remember`s
/// and five effects around an `ExoPlayer`. The split here is deliberate and
/// it is the one this project arrived at over four batches: **the player
/// lives in the view and every decision lives here**, because a builder
/// cannot run a Simulator and a review cannot see a layout, so anything
/// worth getting right has to be somewhere a test can reach.
@MainActor
@Observable
public final class VideoFeedModel {

    /// The videos on offer, in viewing order. A removal takes one out for
    /// this session only — it never deletes a file (`VideoFeed.kt:709`).
    public private(set) var queue: [FeedVideo]

    public private(set) var current: Int

    /// Every video the feed was ever given, so one the viewer removed is not
    /// added back when the list behind it loads another page
    /// (`VideoFeed.kt:166`).
    private var known: Set<String>

    /// Which page the resume belongs to, and how far in.
    private let startIndex: Int
    private let resumeSec: Int64

    /// Spent by the first page the feed shows. Swiping back to the tapped
    /// video later starts it over like any other
    /// (``FeedRules/startPositionMs(page:startIndex:resumeSec:resumeUsed:)``).
    public private(set) var resumeUsed = false

    /// The video the player currently holds, so leaving a page can save its
    /// position and arriving at the same one again costs no reload.
    public private(set) var loaded: FeedVideo?

    /// The video whose stream the player could not decode. Only that one
    /// page says so; the rest of the feed is fine.
    public private(set) var failedKey: String?

    /// The feed could not be given a URL at all — the token is held, the
    /// phone is unpaired, there is no server. Different from ``failedKey``
    /// and said differently (``VideoStream/refusal(_:)``), because the next
    /// video will not play either.
    public private(set) var refusal: (title: String, detail: String)?

    public private(set) var sheet: VideoFeedSheet?

    /// The skip indicator: which way, and a count so that two taps in a row
    /// re-trigger the animation instead of looking like one.
    public private(set) var seekFlash: (forward: Bool, count: Int)?

    /// Called when a page is left, every five seconds while playing, when a
    /// video loops and on close. The model owns **when**; the view, which is
    /// the only thing that can read the player, supplies the numbers.
    private let persist: @MainActor (FeedVideo, Int64, Int64?, Bool) -> Void

    /// Asked for when the viewer nears the end of what is loaded, or nil when
    /// the list behind the feed has no more pages to give (the recently-played
    /// strip, which is one short list).
    private let loadMore: (@MainActor () -> Void)?

    private var flashCount = 0

    public init(
        videos: [FeedVideo],
        startIndex: Int,
        resumeSec: Int64 = 0,
        persist: @MainActor @escaping (FeedVideo, Int64, Int64?, Bool) -> Void = { _, _, _, _ in },
        loadMore: (@MainActor () -> Void)? = nil
    ) {
        self.queue = videos
        self.known = Set(videos.map(\.key))
        let last = max(0, videos.count - 1)
        self.startIndex = min(max(0, startIndex), last)
        self.current = self.startIndex
        self.resumeSec = max(0, resumeSec)
        self.persist = persist
        self.loadMore = loadMore
    }

    // MARK: - what is on screen

    public var showing: FeedVideo? {
        queue.indices.contains(current) ? queue[current] : nil
    }

    public var title: String { showing?.title ?? "" }

    /// `"3 / 48"`.
    public var counter: String {
        VideoStrings.counter(min(current + 1, queue.count), of: queue.count)
    }

    public var isEmpty: Bool { queue.isEmpty }

    /// Whether the page showing is the one that failed to decode.
    public func hasFailed(_ video: FeedVideo) -> Bool {
        failedKey == video.key
    }

    // MARK: - the pager settled

    /// The pager settled on `page`.
    ///
    /// - Parameters:
    ///   - leavingAtSec: where the player had got to in the page being left,
    ///     so its resume row is saved before the player moves on.
    ///   - leavingDurationSec: that video's length, when the player knew it.
    ///
    /// Returns what the player should do. A page the player already holds
    /// returns ``FeedAction/play`` and **does not** persist or reload: a
    /// swipe that bounces back to where it started should not cost a
    /// rebuffer, and Android has the same early return (`VideoFeed.kt:216`).
    @discardableResult
    public func settled(
        on page: Int,
        leavingAtSec: Int64 = 0,
        leavingDurationSec: Int64? = nil
    ) -> FeedAction {
        guard queue.indices.contains(page) else { return .nothing }
        current = page
        let video = queue[page]
        if loaded?.key == video.key { return .play }
        if let outgoing = loaded {
            persist(outgoing, leavingAtSec, leavingDurationSec, false)
        }
        failedKey = nil
        refusal = nil
        let startMs = FeedRules.startPositionMs(
            page: page,
            startIndex: startIndex,
            resumeSec: resumeSec,
            resumeUsed: resumeUsed
        )
        resumeUsed = true
        loaded = video
        askForMoreIfNear()
        return .load(video, startMs: startMs)
    }

    private func askForMoreIfNear() {
        guard let loadMore, FeedRules.wantsAnotherPage(current: current, loaded: queue.count) else { return }
        loadMore()
    }

    /// The list behind the feed loaded another page: its new videos join the
    /// end. One the viewer removed is **not** added back.
    public func grew(to videos: [FeedVideo]) {
        for video in videos where known.insert(video.key).inserted {
            queue.append(video)
        }
    }

    // MARK: - the player reported something

    /// The stream for this video would not decode.
    public func failed(_ key: String) {
        failedKey = key
    }

    /// There was no URL to give the player. ``VideoStream/url(for:in:)``
    /// said why.
    public func refused(_ trouble: DomovoiError) {
        refusal = VideoStream.refusal(trouble)
    }

    /// The video ran to its end and looped: the resume row is saved at zero,
    /// which is what "watched" means in the strip.
    public func looped(durationSec: Int64?) {
        guard let loaded else { return }
        persist(loaded, 0, durationSec, true)
    }

    /// The five-second tick while a video plays, and the save on close.
    public func tick(positionSec: Int64, durationSec: Int64?) {
        guard let loaded else { return }
        persist(loaded, positionSec, durationSec, false)
    }

    // MARK: - what the viewer did

    /// A double tap. Returns where to seek to, or nil when it landed in the
    /// middle third (which pauses, like a single tap).
    public func doubleTapped(
        x: Double,
        width: Double,
        positionMs: Int64,
        durationMs: Int64?
    ) -> Int64? {
        switch FeedRules.doubleTapZone(x: x, width: width) {
        case .middle:
            return nil
        case .back:
            flashCount += 1
            seekFlash = (forward: false, count: flashCount)
            return FeedRules.seekTarget(positionMs - FeedRules.seekStepMs, durationMs: durationMs)
        case .forward:
            flashCount += 1
            seekFlash = (forward: true, count: flashCount)
            return FeedRules.seekTarget(positionMs + FeedRules.seekStepMs, durationMs: durationMs)
        }
    }

    /// The skip indicator's time is up.
    public func flashFaded() {
        seekFlash = nil
    }

    /// A drag along the scrubber, as a fraction of the width.
    public func scrubbed(to fraction: Double, durationMs: Int64?) -> Int64? {
        guard let durationMs, durationMs > 0 else { return nil }
        let clamped = min(max(0, fraction), 1)
        return Int64(clamped * Double(durationMs))
    }

    public func open(_ which: VideoFeedSheet) {
        sheet = which
    }

    public func closeSheet() {
        sheet = nil
    }

    /// A pick in the playlist sheet: the page the view should scroll to.
    ///
    /// The sheet closes and the **view scrolls**, which then settles and
    /// calls ``settled(on:leavingAtSec:leavingDurationSec:)``. Android goes
    /// the same way round (`VideoFeed.kt:475` sets `current`, and an effect
    /// scrolls the pager to it), and it matters: a model that loaded the
    /// video itself would leave the pager showing a different page from the
    /// one playing.
    public func picked(_ index: Int) -> Int? {
        guard queue.indices.contains(index) else { return nil }
        sheet = nil
        current = index
        return index
    }

    /// Drop a video from this session's queue. It is never deleted.
    @discardableResult
    public func remove(_ key: String, atSec: Int64 = 0, durationSec: Int64? = nil) -> FeedRemoval {
        guard let index = queue.firstIndex(where: { $0.key == key }) else { return .notThere }
        let wasCurrent = index == current
        queue.remove(at: index)
        if queue.isEmpty {
            sheet = nil
            loaded = nil
            return .emptied
        }
        current = FeedRules.indexAfterRemoval(current: current, removed: index, newSize: queue.count)
        guard wasCurrent else { return .stillThere(.nothing) }
        // The video that was playing has gone, so the player must be told to
        // load the one that slid into its place — and `loaded` is cleared
        // first, or `settled` would see the removed video as still held and
        // answer `.play` to a player holding nothing.
        loaded = nil
        return .stillThere(settled(on: current, leavingAtSec: atSec, leavingDurationSec: durationSec))
    }

    /// The key to hand back on close: the video on screen, or nil when the
    /// queue was emptied.
    public var closingKey: String? { showing?.key }
}
