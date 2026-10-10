package com.domovoi.app.ui.screens.videos

import androidx.media3.common.Player

/**
 * One video in the swipe feed. The feed is built from whatever list the
 * video was tapped in, in that list's order, so the same player serves the
 * server library (http stream) and the phone's own videos (content:// uri).
 *
 * [key] is the list's own item key: the feed hands it back on close so the
 * list can find (and centre) the video the viewer ended on, even after
 * videos were dropped from the session queue.
 */
data class FeedVideo(
    val key: String,
    val title: String,
    val uri: String,
    /** Still image for pages that are not playing; null = glyph. */
    val posterModel: Any? = null,
    val sizeBytes: Long? = null,
    val modifiedEpochSec: Double? = null,
    val location: String? = null,
    /** Only a phone-local uri can be shared: a server stream needs the household token. */
    val shareable: Boolean = false,
)

/**
 * Where playback starts when the pager settles on [page]. The video the
 * viewer tapped opens where they left off, as it always has; every other
 * page — and the tapped one once they have swiped away from it — starts
 * from the beginning (owner decision 2026-10-08).
 */
internal fun startPositionMs(page: Int, startIndex: Int, resumeSec: Long, resumeUsed: Boolean): Long =
    if (page == startIndex && !resumeUsed && resumeSec > 5) resumeSec * 1000 else 0L

/**
 * The current page after dropping [removed] from a queue whose current page
 * was [current]: a removal before it shifts it left, removing the current
 * video lands on the one that slid into its place (or the new last one).
 */
internal fun indexAfterRemoval(current: Int, removed: Int, newSize: Int): Int = when {
    newSize <= 0 -> 0
    removed < current -> current - 1
    removed == current -> current.coerceAtMost(newSize - 1)
    else -> current
}

/**
 * How far to scroll so an item ends up centred in the viewport: positive
 * scrolls forward. Lazy lists clamp the scroll at either end, so an item
 * near the top or bottom lands as close to centre as the list allows.
 */
internal fun centerScrollDelta(itemOffset: Int, itemSize: Int, viewportStart: Int, viewportEnd: Int): Int =
    (itemOffset + itemSize / 2) - (viewportStart + viewportEnd) / 2

/**
 * Whether the feed holds the screen on: while it means to play, a stall to
 * buffer included, so the phone doesn't dim mid-video. Paused, failed or
 * idle, the screen times out as usual.
 */
internal fun feedKeepsScreenOn(playWhenReady: Boolean, playbackState: Int, failed: Boolean): Boolean =
    playWhenReady && !failed &&
        (playbackState == Player.STATE_READY || playbackState == Player.STATE_BUFFERING)

/**
 * Leaving the app pauses the feed, and coming back resumes it only if it was
 * playing when the app was left (owner report 2026-10-09: the video played on
 * in the background). Leaving is the app going out of sight — home, another
 * app, the screen turning off — not split-screen focus moving to the other app.
 */
internal class BackgroundPause {
    private var resumeOnReturn = false

    /** The app left the screen while [playing]; true when the player should pause now. */
    fun onLeave(playing: Boolean): Boolean {
        resumeOnReturn = playing
        return playing
    }

    /** The app is back on screen; true when the player should play again. */
    fun onReturn(): Boolean = resumeOnReturn.also { resumeOnReturn = false }
}

// ---------------------------------------------------------------------------
// The server Videos grid, as data. The grid renders straight from this list,
// so a video's position here IS its grid item index — which is what lets the
// feed's close put the right tile back in the middle of the screen.
// ---------------------------------------------------------------------------

internal sealed interface VideoGridEntry {
    val key: String

    /** The "recently played" strip; one full-width grid item. */
    data object Recent : VideoGridEntry {
        override val key = "recent"
    }

    data class Header(val libraryId: String, val label: String, val count: Int) : VideoGridEntry {
        override val key = "hdr:$libraryId"
    }

    data class Tile(val video: VideoRow) : VideoGridEntry {
        override val key = videoKey(video)
    }
}

internal fun videoKey(v: VideoRow): String = "${v.library_id}:${v.rel}"

/** Grid entries in render order: the recent strip (when shown), then each library's header and tiles. */
internal fun videoGridEntries(showRecent: Boolean, grouped: Map<String, List<VideoRow>>): List<VideoGridEntry> =
    buildList {
        if (showRecent) add(VideoGridEntry.Recent)
        grouped.forEach { (libId, vids) ->
            if (vids.isEmpty()) return@forEach
            add(VideoGridEntry.Header(libId, vids.first().library_label ?: libId, vids.size))
            vids.forEach { add(VideoGridEntry.Tile(it)) }
        }
    }

/** The feed for a tap in the library grid: every tile, in the order the grid shows them. */
internal fun gridFeedOrder(entries: List<VideoGridEntry>): List<VideoRow> =
    entries.mapNotNull { (it as? VideoGridEntry.Tile)?.video }
