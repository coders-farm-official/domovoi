package com.domovoi.app.player

/**
 * What "next" and "previous" do for a book (batch 18, 2026-10-20).
 *
 * A book plays as ONE queue item (a folder book's files are joined,
 * [BookMediaSources]), so the queue's next item was nothing and the mini
 * player's next did nothing. For a book they move by chapter:
 *  - next: to the next chapter's start; from the last chapter (or a book
 *    without chapters), to the queue's next item, else nothing;
 *  - previous: back to the start of this chapter when more than
 *    [RESTART_AFTER_SEC] in, else to the previous chapter; in the first
 *    chapter, the queue's previous item, else the book's start.
 *
 * Everything else keeps the queue's own next and previous. [canNext] is
 * what every next button (mini player, sheet, player tab, notification and
 * lock screen) reads, so one that would do nothing is greyed or not offered.
 * Pure, so the JVM tests drive it directly.
 */
internal object ChapterSkip {
    /** Previous restarts the chapter past this far in (the web's 3 s). */
    const val RESTART_AFTER_SEC = 3.0

    /** A chapter start counts as reached this close to it: a seek to a
     *  chapter reads back a hair before its start. */
    private const val AT_START_SEC = 0.5

    sealed interface Move {
        data class Seek(val sec: Double) : Move
        data object NextItem : Move
        data object PreviousItem : Move
        data object None : Move
    }

    /** Books move by chapter; everything else by queue item. */
    fun applies(item: PlayItem?): Boolean = item?.kind == PlayKind.Audiobook

    /** The chapter at [positionSec]: the last one started, -1 before any. */
    private fun at(starts: List<Double>, positionSec: Double): Int =
        starts.indexOfLast { it <= positionSec + AT_START_SEC }

    fun next(chapters: List<Chapter>, positionSec: Double, hasNextItem: Boolean): Move {
        val starts = chapters.map { it.startSec }.sorted()
        val after = starts.getOrNull(at(starts, positionSec) + 1)
        return when {
            after != null -> Move.Seek(after)
            hasNextItem -> Move.NextItem
            else -> Move.None
        }
    }

    fun previous(chapters: List<Chapter>, positionSec: Double, hasPreviousItem: Boolean): Move {
        val starts = chapters.map { it.startSec }.sorted()
        val i = at(starts, positionSec)
        val start = starts.getOrNull(i) ?: 0.0
        return when {
            positionSec - start > RESTART_AFTER_SEC -> Move.Seek(start)
            i > 0 -> Move.Seek(starts[i - 1])
            hasPreviousItem -> Move.PreviousItem
            else -> Move.Seek(0.0)
        }
    }

    /**
     * Whether next does anything now: while casting it is the room's skip
     * (always offered); on this phone, the book's next chapter or the
     * queue's next item.
     */
    fun canNext(queue: List<PlayItem>, index: Int, positionSec: Double, casting: Boolean): Boolean {
        if (casting) return true
        val item = queue.getOrNull(index) ?: return false
        val hasNextItem = index < queue.lastIndex
        if (!applies(item)) return hasNextItem
        return next(item.chapters, positionSec, hasNextItem) != Move.None
    }
}
