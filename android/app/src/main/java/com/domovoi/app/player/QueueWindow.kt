package com.domovoi.app.player

/**
 * How much of a long list goes into this phone's play queue.
 *
 * Tapping a song in the on-device music list used to queue EVERY track on the
 * phone. With a few thousand tracks that made starting playback stall the
 * main thread (one MediaItem per track, and the media session publishes the
 * whole queue), and the player tab then built a row per track. The queue is
 * now a window around the tapped track: a short run before it (so "previous"
 * still goes somewhere) and the rest after it.
 *
 * [MAX] matches the server's own cap on handing a queue to a room
 * (play-tracks accepts at most 500 ids), so a phone queue never holds more
 * than a room would take.
 */
object QueueWindow {
    const val MAX = 500
    const val LEAD = 50

    /**
     * The slice of [items] to queue when [index] was tapped, and where the
     * tapped item sits inside it. A list within [max] comes back whole. An
     * out-of-range [index] is clamped, as playItems would.
     */
    fun <T> around(items: List<T>, index: Int, max: Int = MAX, lead: Int = LEAD): Slice<T> {
        require(max > 0) { "max must be positive" }
        val at = index.coerceIn(0, (items.size - 1).coerceAtLeast(0))
        if (items.size <= max) return Slice(items, at)
        val keepBefore = lead.coerceIn(0, max - 1)
        var start = (at - keepBefore).coerceAtLeast(0)
        val end = (start + max).coerceAtMost(items.size)
        // Near the end of the list: fill the window from before instead.
        start = (end - max).coerceAtLeast(0)
        return Slice(items.subList(start, end).toList(), at - start)
    }

    data class Slice<T>(val items: List<T>, val index: Int)
}
