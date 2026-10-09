package com.domovoi.app.ui.screens.videos

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The swipe feed's pure rules: which list it plays, where each video
 * starts, how the queue shifts when a video is dropped, and the arithmetic
 * that puts the video you ended on back in the middle of the list.
 */
class FeedModelsTest {

    private fun row(lib: String, rel: String, label: String? = null) =
        VideoRow(library_id = lib, library_label = label, rel = rel, name = rel.substringAfterLast('/'))

    // ---- startPositionMs ----------------------------------------------------

    @Test fun tappedVideoResumesWhereItWasLeft() {
        assertEquals(90_000L, startPositionMs(page = 3, startIndex = 3, resumeSec = 90, resumeUsed = false))
    }

    @Test fun everyOtherPageStartsFromTheBeginning() {
        assertEquals(0L, startPositionMs(page = 4, startIndex = 3, resumeSec = 90, resumeUsed = false))
    }

    @Test fun swipingBackToTheTappedVideoStartsItOver() {
        assertEquals(0L, startPositionMs(page = 3, startIndex = 3, resumeSec = 90, resumeUsed = true))
    }

    @Test fun aResumeInTheFirstFewSecondsIsIgnored() {
        // Matches the old single-video player: under 5 s is "not started yet".
        assertEquals(0L, startPositionMs(page = 0, startIndex = 0, resumeSec = 5, resumeUsed = false))
    }

    // ---- indexAfterRemoval --------------------------------------------------

    @Test fun removingAnEarlierVideoShiftsTheCurrentOneLeft() {
        assertEquals(2, indexAfterRemoval(current = 3, removed = 1, newSize = 5))
    }

    @Test fun removingALaterVideoLeavesTheCurrentOneAlone() {
        assertEquals(3, indexAfterRemoval(current = 3, removed = 4, newSize = 5))
    }

    @Test fun removingTheCurrentVideoLandsOnTheOneThatSlidIntoItsPlace() {
        assertEquals(3, indexAfterRemoval(current = 3, removed = 3, newSize = 5))
    }

    @Test fun removingTheCurrentLastVideoLandsOnTheNewLast() {
        assertEquals(3, indexAfterRemoval(current = 4, removed = 4, newSize = 4))
    }

    @Test fun anEmptyQueueHasIndexZero() {
        assertEquals(0, indexAfterRemoval(current = 0, removed = 0, newSize = 0))
    }

    // ---- centerScrollDelta --------------------------------------------------

    @Test fun anItemAtTheTopScrollsBackToTheMiddle() {
        // Viewport 0..1000, item 200 tall at the top: its centre (100) is 400 above the middle.
        assertEquals(-400, centerScrollDelta(itemOffset = 0, itemSize = 200, viewportStart = 0, viewportEnd = 1000))
    }

    @Test fun aCentredItemNeedsNoScroll() {
        assertEquals(0, centerScrollDelta(itemOffset = 400, itemSize = 200, viewportStart = 0, viewportEnd = 1000))
    }

    @Test fun anItemBelowTheMiddleScrollsForward() {
        assertEquals(300, centerScrollDelta(itemOffset = 700, itemSize = 200, viewportStart = 0, viewportEnd = 1000))
    }

    @Test fun aViewportWithContentPaddingCentresOnItsOwnMiddle() {
        // A grid with top padding reports a negative viewport start.
        assertEquals(-300, centerScrollDelta(itemOffset = 0, itemSize = 200, viewportStart = -100, viewportEnd = 900))
    }

    // ---- videoGridEntries / gridFeedOrder ----------------------------------

    private val grouped = linkedMapOf(
        "movies" to listOf(row("movies", "a.mp4", "Movies"), row("movies", "b.mp4", "Movies")),
        "home" to listOf(row("home", "clips/c.mp4", "Home videos")),
    )

    @Test fun gridEntriesAreRecentStripThenEachLibrarysHeaderAndTiles() {
        val keys = videoGridEntries(showRecent = true, grouped = grouped).map { it.key }
        assertEquals(
            listOf("recent", "hdr:movies", "movies:a.mp4", "movies:b.mp4", "hdr:home", "home:clips/c.mp4"),
            keys,
        )
    }

    @Test fun aFilteredGridHasNoRecentStrip() {
        val entries = videoGridEntries(showRecent = false, grouped = grouped)
        assertTrue(entries.none { it is VideoGridEntry.Recent })
        // The first tile now sits right after its header, at grid index 1.
        assertEquals(1, entries.indexOfFirst { it.key == "movies:a.mp4" })
    }

    @Test fun headersCarryTheLibraryLabelAndCount() {
        val header = videoGridEntries(showRecent = false, grouped = grouped)
            .filterIsInstance<VideoGridEntry.Header>().first()
        assertEquals("Movies", header.label)
        assertEquals(2, header.count)
    }

    @Test fun headerFallsBackToTheLibraryIdWithoutALabel() {
        val header = videoGridEntries(false, linkedMapOf("lib9" to listOf(row("lib9", "x.mp4"))))
            .filterIsInstance<VideoGridEntry.Header>().single()
        assertEquals("lib9", header.label)
    }

    @Test fun anEmptyLibraryGetsNoHeader() {
        val entries = videoGridEntries(false, linkedMapOf("empty" to emptyList(), "movies" to grouped.getValue("movies")))
        assertTrue(entries.none { it.key == "hdr:empty" })
    }

    @Test fun theFeedPlaysTheTilesInTheOrderTheGridShowsThem() {
        val order = gridFeedOrder(videoGridEntries(showRecent = true, grouped = grouped)).map(::videoKey)
        assertEquals(listOf("movies:a.mp4", "movies:b.mp4", "home:clips/c.mp4"), order)
    }

    @Test fun aVideosFeedPositionMapsBackToItsGridIndex() {
        // The close hands back a key; the grid index is that key's position in the entries.
        val entries = videoGridEntries(showRecent = true, grouped = grouped)
        val feed = gridFeedOrder(entries)
        val endedOn = videoKey(feed[2])
        assertEquals(5, entries.indexOfFirst { it.key == endedOn })
    }
}
