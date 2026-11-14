import Foundation

/// One video in the swipe feed.
///
/// `FeedModels.kt:14`. The feed is built from whatever list the video was
/// tapped in, in that list's order, so one player serves the whole screen.
///
/// ### The stream address is a **path**, not a URL
///
/// Android's `FeedVideo.uri` is an absolute URL built once when the feed
/// opens, because its player fetches through the app's own HTTP client and
/// the household token rides in a header. iOS's `AVPlayer` makes its own
/// requests and cannot be given a header without private API, so the token
/// has to be in the URL — and a URL carrying the token must only be built
/// **at the moment the server has proved itself**. Hence a relative path
/// here and ``VideoStream/url(for:in:)`` at the instant a page is activated.
/// That is also why a feed left open across a network change cannot play a
/// stale URL: there is no URL to go stale.
public struct FeedVideo: Equatable, Sendable, Identifiable {

    /// The list's own item key — ``VideoRow/id``. The feed hands it back on
    /// close so the list can find and centre the video the viewer ended on,
    /// even after videos were dropped from the session queue.
    public let key: String

    public var id: String { key }

    public let title: String

    /// `/api/videos/stream?…`, relative to the server. See the type's note.
    public let streamPath: String

    /// `/api/videos/poster?…`, or nil for a video with no frame to show.
    public let posterPath: String?

    public let sizeBytes: Int64?
    public let modifiedEpochSec: Double?
    public let location: String?

    /// Only a phone-local video can be shared: a server stream needs the
    /// household token, and handing another app a URL with the token in it
    /// would be handing out the token. `FeedModels.kt:24` says the same for
    /// the same reason; on iOS the stakes are higher because the token is in
    /// the URL rather than a header.
    public let shareable: Bool

    public init(
        key: String,
        title: String,
        streamPath: String,
        posterPath: String? = nil,
        sizeBytes: Int64? = nil,
        modifiedEpochSec: Double? = nil,
        location: String? = nil,
        shareable: Bool = false
    ) {
        self.key = key
        self.title = title
        self.streamPath = streamPath
        self.posterPath = posterPath
        self.sizeBytes = sizeBytes
        self.modifiedEpochSec = modifiedEpochSec
        self.location = location
        self.shareable = shareable
    }

    /// The feed entry for one library row.
    public init(_ video: VideoRow) {
        self.init(
            key: video.id,
            title: video.displayTitle,
            streamPath: VideoPath.stream(video),
            posterPath: VideoPath.poster(video),
            sizeBytes: video.size,
            modifiedEpochSec: video.mtime,
            location: video.location,
            shareable: false
        )
    }
}

/// What the player is doing, as much of it as the feed's rules need.
///
/// Android reads ExoPlayer's `playbackState` ints directly
/// (`FeedModels.kt:61`). `AVPlayer` says it differently — `timeControlStatus`
/// for the intent and `currentItem.status` for readiness — so the four states
/// the rule actually distinguishes are named here and the view maps to them.
/// A rule over an enum is one a test can state; a rule over
/// `AVPlayer.TimeControlStatus` would need an `AVPlayer`.
public enum FeedPlayback: Sendable, Equatable {
    /// Nothing loaded, or loading has not got anywhere yet.
    case idle
    /// It means to play and is waiting for bytes.
    case buffering
    /// Playable now.
    case ready
    /// Ran to the end.
    case ended
}

/// The swipe feed's pure rules: where a page starts, how the queue shifts
/// when a video is dropped, and what the screen timeout does.
public enum FeedRules {

    /// How close to the end of the loaded videos the feed asks the list for
    /// more. `VideoFeed.kt:123`.
    public static let prefetch = 3

    /// A double-tap at the edge moves this far. `VideoFeed.kt:120`.
    public static let seekStepMs: Int64 = 15_000

    /// How often a playing video saves where it is. `VideoFeed.kt:298`.
    public static let saveEveryMs: Int64 = 5_000

    /// A resume shorter than this is "never really started", and the video
    /// opens at the beginning instead. `FeedModels.kt:34`.
    public static let resumeFloorSec: Int64 = 5

    /// Where playback starts when the pager settles on `page`.
    ///
    /// The video the viewer tapped opens where they left off; every other
    /// page — and the tapped one once they have swiped away from it — starts
    /// from the beginning (owner decision 2026-10-08, `FeedModels.kt:27`).
    public static func startPositionMs(
        page: Int,
        startIndex: Int,
        resumeSec: Int64,
        resumeUsed: Bool
    ) -> Int64 {
        guard page == startIndex, !resumeUsed, resumeSec > resumeFloorSec else { return 0 }
        return resumeSec * 1000
    }

    /// The current page after dropping `removed` from a queue whose current
    /// page was `current`.
    ///
    /// A removal before it shifts it left; removing the current video lands
    /// on the one that slid into its place, or on the new last one.
    /// `FeedModels.kt:41`.
    public static func indexAfterRemoval(current: Int, removed: Int, newSize: Int) -> Int {
        guard newSize > 0 else { return 0 }
        if removed < current { return current - 1 }
        if removed == current { return min(current, newSize - 1) }
        return current
    }

    /// Whether the feed holds the screen awake: while it means to play, a
    /// stall to buffer included, so the phone does not dim mid-video. Paused,
    /// failed or idle, the screen times out as usual. `FeedModels.kt:61`.
    public static func keepsScreenOn(
        wantsToPlay: Bool,
        state: FeedPlayback,
        failed: Bool
    ) -> Bool {
        guard wantsToPlay, !failed else { return false }
        return state == .ready || state == .buffering
    }

    /// A seek target clamped to the video. A duration of nil (a stream whose
    /// length is not known yet) clamps at zero only.
    public static func seekTarget(_ wantedMs: Int64, durationMs: Int64?) -> Int64 {
        guard let durationMs, durationMs > 0 else { return max(0, wantedMs) }
        return min(max(0, wantedMs), durationMs)
    }

    /// Whether a page this close to the end of the loaded queue should ask
    /// the list behind it for another page.
    ///
    /// False for an empty queue, which is the state a feed is in for one
    /// frame after the last video is removed — and Android's version would
    /// ask for another page there (`VideoFeed.kt:174` guards it with
    /// `queue.isNotEmpty()`, so it is a port, not a fix).
    public static func wantsAnotherPage(current: Int, loaded: Int) -> Bool {
        guard loaded > 0 else { return false }
        return current >= loaded - prefetch
    }

    /// Which third of the width a double-tap landed in.
    ///
    /// `VideoFeed.kt:359`: the edge thirds skip, the middle third pauses like
    /// a single tap. A width of zero — which a view can report during layout
    /// — reads as the middle, because skipping on a tap whose position means
    /// nothing is the worse guess.
    public static func doubleTapZone(x: Double, width: Double) -> FeedTapZone {
        guard width > 0 else { return .middle }
        if x < width / 3 { return .back }
        if x > width * 2 / 3 { return .forward }
        return .middle
    }
}

public enum FeedTapZone: Sendable, Equatable {
    case back, middle, forward
}

/// Leaving the app pauses the feed, and coming back resumes it **only if it
/// was playing when the app was left**.
///
/// `FeedModels.kt:71`, from the owner's report of 2026-10-09: the video
/// played on in the background. Leaving is the app going out of sight — home,
/// another app, the screen off.
///
/// A class with one `Bool` rather than two lines in a view, because the thing
/// that goes wrong is subtle: the first notification after subscribing is the
/// *arrival*, and a resume that trusted it would start a video nobody asked
/// for.
public final class BackgroundPause {

    private var resumeOnReturn = false

    public init() {}

    /// The app left the screen while `playing`. True when the player should
    /// pause now.
    @discardableResult
    public func onLeave(playing: Bool) -> Bool {
        resumeOnReturn = playing
        return playing
    }

    /// The app is back on screen. True when the player should play again —
    /// **once**: the answer is spent by the call.
    @discardableResult
    public func onReturn() -> Bool {
        let resume = resumeOnReturn
        resumeOnReturn = false
        return resume
    }
}

/// What the player learned about the stream once it loaded, for the details
/// sheet. `VideoFeed.kt:763` reads ExoPlayer's `videoFormat`.
public struct FeedStreamFacts: Equatable, Sendable {
    public let durationSec: Double?
    public let width: Int?
    public let height: Int?
    public let bitsPerSecond: Double?
    public let frameRate: Double?
    /// The codec's short name, as the platform spells it.
    public let codec: String?

    public init(
        durationSec: Double? = nil,
        width: Int? = nil,
        height: Int? = nil,
        bitsPerSecond: Double? = nil,
        frameRate: Double? = nil,
        codec: String? = nil
    ) {
        self.durationSec = durationSec
        self.width = width
        self.height = height
        self.bitsPerSecond = bitsPerSecond
        self.frameRate = frameRate
        self.codec = codec
    }
}

public extension FeedVideo {

    /// The details sheet's rows.
    ///
    /// `VideoFeed.kt:766-780`, and the same shape as ``LibraryTrack/metaRows``
    /// one batch ago: data, so that "what does this row say about a video the
    /// player could not measure?" is a decision a test can state rather than
    /// a `?:` in the middle of a sheet.
    ///
    /// `size`, `duration` and `resolution` are **always drawn**, saying
    /// `unknown` when they are not known — Android draws those three
    /// unconditionally and leaves the rest out, and that is right: a video
    /// with no duration is worth saying out loud, while a missing frame rate
    /// is not.
    func detailRows(facts: FeedStreamFacts? = nil, timeZone: TimeZone = .current) -> [VideoMetaRow] {
        var out: [VideoMetaRow] = [
            VideoMetaRow(label: VideoStrings.metaSize, value: fmtBytes(sizeBytes))
        ]
        if let modifiedEpochSec {
            out.append(
                VideoMetaRow(
                    label: VideoStrings.metaModified,
                    value: fmtEpochDate(modifiedEpochSec, timeZone: timeZone)
                )
            )
        }
        let duration = facts?.durationSec
        out.append(
            VideoMetaRow(
                label: VideoStrings.metaDuration,
                value: (duration ?? 0) > 0 ? fmtDur(duration) : VideoStrings.unknown
            )
        )
        let width = facts?.width ?? 0
        let height = facts?.height ?? 0
        out.append(
            VideoMetaRow(
                label: VideoStrings.metaResolution,
                value: width > 0 && height > 0 ? "\(width) × \(height)" : VideoStrings.unknown
            )
        )
        if let bits = facts?.bitsPerSecond, bits > 0 {
            out.append(VideoMetaRow(label: VideoStrings.metaBitrate, value: "\(Int(bits / 1000)) kbps"))
        }
        if let rate = facts?.frameRate, rate > 0 {
            out.append(VideoMetaRow(label: VideoStrings.metaFrameRate, value: "\(Int(rate.rounded())) fps"))
        }
        if let codec = facts?.codec, !codec.isEmpty {
            out.append(VideoMetaRow(label: VideoStrings.metaCodec, value: codec))
        }
        if let location, !location.isEmpty {
            out.append(VideoMetaRow(label: VideoStrings.metaLocation, value: location))
        }
        return out
    }
}

/// One row of the details sheet.
public struct VideoMetaRow: Equatable, Sendable, Identifiable {
    public var id: String { label }
    public let label: String
    public let value: String

    public init(label: String, value: String) {
        self.label = label
        self.value = value
    }
}

/// `"2026-10-31 14:05"` in `timeZone`. `VideoFeed.kt:796`.
///
/// The locale is fixed to POSIX, not the phone's: the format is already an
/// explicit `yyyy-MM-dd HH:mm`, and a phone set to a Hindi or Arabic locale
/// would otherwise render those digits in that locale's numerals beside a
/// monospaced size in Western ones. The time zone is the caller's so a test
/// can state one.
public func fmtEpochDate(_ epochSec: Double, timeZone: TimeZone = .current) -> String {
    guard epochSec.isFinite else { return "—" }
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = timeZone
    formatter.dateFormat = "yyyy-MM-dd HH:mm"
    return formatter.string(from: Date(timeIntervalSince1970: epochSec))
}

// ---------------------------------------------------------------------------
// Deliberately not ported: `centerScrollDelta` and `LazyGridState.centerOn`
// (`FeedModels.kt:53`, `FeedCentering.kt`).
//
// Android computes how far to scroll so that an item lands in the middle of
// the viewport, because a lazy list only offers "put this at the top".
// SwiftUI has the behaviour itself: `ScrollViewProxy.scrollTo(id,
// anchor: .center)`, which also clamps at either end. Porting the
// arithmetic would mean maintaining a formula with no caller — the shape of
// thing a review on this project has found twice — so the mechanism is used
// and the formula is not.
// ---------------------------------------------------------------------------
