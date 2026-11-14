import Foundation

/// A folder book's files as **one** timeline.
///
/// Android joins a folder book's files with `ConcatenatingMediaSource2`
/// (`BookMediaSources.kt:11-19`) so the book has one position, one seek bar and
/// its chapters across the files. The literal iOS analog is
/// `AVMutableComposition` over remote `AVURLAsset`s, which loads every part's
/// tracks before playback can start — for a 60-file book that is 60 network
/// round trips before the first second of audio, and a failure in part 43 takes
/// the whole item down.
///
/// So this port maps the timeline instead: pure arithmetic over the parts'
/// durations. The engine plays one part at a time; ``PlayerModel`` reports the
/// **global** position, converts a global seek into (part, within) and rebuilds
/// from that part, and advances the part on end-of-item. The screen, the
/// chapters, the saved place and the server's slot all see one position from
/// the book's start, which is the property the Android comment asks for.
///
/// ## What is not, and why there is no control for it
///
/// A part whose `durationSec` is nil makes **everything after it** unmappable:
/// nothing can say where part 5 starts if part 3's length is unknown. Then
/// ``totalSec`` is nil and a consumer draws **no** whole-book seek bar — the
/// book plays part by part, chapter by chapter. A total guessed from the parts
/// we do know is a seek bar whose thumb sits in the wrong place for the whole
/// book, which is exactly the misleading control the owner's rule is about.
public struct PlayerBookTimeline: Equatable, Sendable {

    /// The parts' lengths in seconds, in order; nil for one that is unknown.
    public let durations: [Double?]

    public init(durations: [Double?]) {
        self.durations = durations
    }

    /// From a queue item's parts. A single-file book has no parts at all
    /// (``PlayerItem/fromBook(id:title:author:durationSec:artwork:chapters:parts:savedKey:origin:)``
    /// keeps them only above one), and then the timeline is the item's own
    /// duration: one part, which maps trivially.
    public init(_ item: PlayerItem) {
        if item.parts.isEmpty {
            self.durations = [item.durationSec]
        } else {
            self.durations = item.parts.map(\.durationSec)
        }
    }

    public var partCount: Int { durations.count }

    /// Whether every part's length is known — the one condition under which a
    /// whole-book position means anything.
    public var isMappable: Bool {
        durations.allSatisfy { ($0 ?? -1) > 0 }
    }

    /// The book's length, or nil when a part's is unknown.
    public var totalSec: Double? {
        guard isMappable else { return nil }
        return durations.reduce(0) { $0 + ($1 ?? 0) }
    }

    /// Where part `index` starts, from the book's beginning; nil when a part
    /// before it has an unknown length, or `index` is out of range.
    public func offset(ofPart index: Int) -> Double? {
        guard index >= 0, index < durations.count else { return nil }
        var sum: Double = 0
        for i in 0..<index {
            guard let d = durations[i], d > 0 else { return nil }
            sum += d
        }
        return sum
    }

    /// The part a whole-book position lands in, and how far into it.
    ///
    /// A position at or past the end answers the **last** part at its end
    /// rather than nil: that is where a seek to the end of a book goes, and a
    /// nil there would make the end of the last chapter unreachable.
    public func part(at globalSec: Double) -> (index: Int, within: Double)? {
        guard isMappable, !durations.isEmpty, globalSec.isFinite else { return nil }
        let target = max(globalSec, 0)
        var start: Double = 0
        for (i, duration) in durations.enumerated() {
            let d = duration ?? 0
            if target < start + d { return (i, target - start) }
            start += d
        }
        return (durations.count - 1, durations[durations.count - 1] ?? 0)
    }

    /// The whole-book position of being `within` seconds into part `index`;
    /// nil when the parts before it cannot be measured.
    public func global(part index: Int, within: Double) -> Double? {
        guard let offset = offset(ofPart: index) else { return nil }
        return offset + max(within, 0)
    }
}
