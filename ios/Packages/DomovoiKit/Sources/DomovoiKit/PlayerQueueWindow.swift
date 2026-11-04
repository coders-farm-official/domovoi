import Foundation

/// How much of a long list goes into this phone's play queue.
///
/// `android/.../player/QueueWindow.kt:1-39`, ported unchanged. Tapping a song
/// in the on-device music list used to queue **every** track on the phone;
/// with a few thousand tracks that stalled the main thread building one media
/// item per track. The queue is a window around the tapped row instead: a
/// short run before it, so previous still goes somewhere, and the rest after.
///
/// ``maximum`` matches the server's own cap on handing a queue to a room
/// (`play-tracks` accepts at most 500 ids), so a phone queue never holds more
/// than a room would take — and here it matters *more* than on Android, not
/// less: 500 `AVPlayerItem`s is already a lot of key-value observing, and
/// `AVQueuePlayer` has no jump-to-index, so a window change is a rebuild
/// (MAP §4.2).
public enum PlayerQueueWindow {
    public static let maximum = 500
    public static let lead = 50

    /// The slice of `items` to queue when `index` was tapped, and where the
    /// tapped item sits inside it. A list within `max` comes back whole; an
    /// out-of-range `index` is clamped, as ``PlayerModel/play(_:startAt:resumeSec:speed:)``
    /// would.
    public struct Slice<Element>: Equatable where Element: Equatable {
        public let items: [Element]
        public let index: Int

        public init(items: [Element], index: Int) {
            self.items = items
            self.index = index
        }
    }

    public static func around<Element: Equatable>(
        _ items: [Element],
        index: Int,
        max: Int = maximum,
        lead: Int = lead
    ) -> Slice<Element> {
        precondition(max > 0, "max must be positive")
        let at = min(Swift.max(index, 0), Swift.max(items.count - 1, 0))
        if items.count <= max { return Slice(items: items, index: at) }
        let keepBefore = min(Swift.max(lead, 0), max - 1)
        let provisional = Swift.max(at - keepBefore, 0)
        let end = min(provisional + max, items.count)
        // Near the end of the list: fill the window from before instead
        // (`QueueWindow.kt:34` — the same correction, for the same reason).
        let start = Swift.max(end - max, 0)
        return Slice(items: Array(items[start..<end]), index: at - start)
    }
}
