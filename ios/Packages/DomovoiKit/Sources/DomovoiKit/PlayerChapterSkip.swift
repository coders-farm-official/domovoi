import Foundation

/// What next and previous do for a book.
///
/// `android/.../player/ChapterSkip.kt:1-76`, ported as-is. A book plays as ONE
/// queue item, so the queue's next item was nothing and the mini player's next
/// did nothing. For a book they move by chapter:
///
/// * next: to the next chapter's start; from the last chapter, or a book with
///   no chapters, to the queue's next item, else nothing;
/// * previous: back to the start of this chapter when more than
///   ``restartAfterSec`` in, else to the previous chapter; in the first
///   chapter, the queue's previous item, else the book's start.
///
/// Everything else keeps the queue's own next and previous. ``canNext(queue:index:positionSec:casting:)``
/// is what **every** next button reads — the mini player, a sheet, a player
/// tab and the lock screen's `MPRemoteCommandCenter.nextTrackCommand` — so one
/// that would do nothing is greyed or disabled rather than drawn live and
/// silent (Kamron's no-misleading-controls rule).
public enum PlayerChapterSkip {
    /// Previous restarts the chapter past this far in (the web's 3 s).
    public static let restartAfterSec: Double = 3.0

    /// A chapter start counts as reached this close to it: a seek to a
    /// chapter reads back a hair before its start (`ChapterSkip.kt:26`).
    static let atStartSec: Double = 0.5

    public enum Move: Equatable, Sendable {
        case seek(Double)
        case nextItem
        case previousItem
        case none
    }

    /// Books move by chapter; everything else by queue item
    /// (`ChapterSkip.kt:36`).
    public static func applies(_ item: PlayerItem?) -> Bool { item?.kind == .audiobook }

    /// The chapter at `positionSec`: the last one started, -1 before any.
    static func at(_ starts: [Double], _ positionSec: Double) -> Int {
        starts.lastIndex(where: { $0 <= positionSec + atStartSec }) ?? -1
    }

    public static func next(
        chapters: [PlayerChapter],
        positionSec: Double,
        hasNextItem: Bool
    ) -> Move {
        let starts = chapters.map(\.startSec).sorted()
        let i = at(starts, positionSec) + 1
        if i >= 0, i < starts.count { return .seek(starts[i]) }
        return hasNextItem ? .nextItem : .none
    }

    public static func previous(
        chapters: [PlayerChapter],
        positionSec: Double,
        hasPreviousItem: Bool
    ) -> Move {
        let starts = chapters.map(\.startSec).sorted()
        let i = at(starts, positionSec)
        let start = (i >= 0 && i < starts.count) ? starts[i] : 0
        if positionSec - start > restartAfterSec { return .seek(start) }
        if i > 0 { return .seek(starts[i - 1]) }
        return hasPreviousItem ? .previousItem : .seek(0)
    }

    /// Whether next does anything now (`ChapterSkip.kt:69`).
    ///
    /// **Always true while casting**: a room's skip always does something —
    /// the room has its own queue on the server's disk and MPD will move it,
    /// whatever this phone's queue looks like.
    public static func canNext(
        queue: [PlayerItem],
        index: Int,
        positionSec: Double,
        casting: Bool
    ) -> Bool {
        if casting { return true }
        guard index >= 0, index < queue.count else { return false }
        let item = queue[index]
        let hasNextItem = index < queue.count - 1
        if !applies(item) { return hasNextItem }
        return next(chapters: item.chapters, positionSec: positionSec, hasNextItem: hasNextItem) != .none
    }

    /// Whether previous does anything now.
    ///
    /// Android has no `canPrevious`: `CastAwarePlayer.kt:190` answers `true`
    /// for a book unconditionally ("previous always does something — this
    /// chapter's start at least") and the phone's own previous restarts the
    /// track, which is also always something. Both of those are true here, so
    /// this is only false for an **empty** queue, where there is nothing to
    /// restart. Written out rather than left implicit because the lock screen
    /// needs a flag either way and a consumer should not invent one.
    public static func canPrevious(queue: [PlayerItem], index: Int, casting: Bool) -> Bool {
        if casting { return true }
        return index >= 0 && index < queue.count
    }
}
