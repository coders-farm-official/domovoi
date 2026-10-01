package com.domovoi.app.ui.screens.music

import com.domovoi.app.player.Chapter
import com.domovoi.app.player.PlayItem
import org.junit.Assert.assertEquals
import org.junit.Test

class PlayerTabModelTest {

    private val chapters = listOf(
        Chapter("one", 0.0),
        Chapter("two", 60.0),
        Chapter("three", 125.5),
    )

    @Test fun currentChapterIsTheLastOneStarted() {
        assertEquals(0, currentChapterIndex(chapters, 0.0))
        assertEquals(0, currentChapterIndex(chapters, 59.9))
        assertEquals(1, currentChapterIndex(chapters, 60.0))
        assertEquals(1, currentChapterIndex(chapters, 125.4))
        assertEquals(2, currentChapterIndex(chapters, 125.5))
        assertEquals(2, currentChapterIndex(chapters, 9_999.0))
    }

    @Test fun beforeTheFirstChapterOrWithNoneIsZero() {
        assertEquals(0, currentChapterIndex(listOf(Chapter("late", 30.0)), 5.0))
        assertEquals(0, currentChapterIndex(emptyList(), 42.0))
    }

    @Test fun queueKeysAreUniqueEvenForATrackQueuedTwice() {
        // Lazy keys must be unique across the whole list or the LazyColumn
        // throws; "queue next" can put the same track in twice.
        val a = PlayItem.fromTrack(7, "a", null, null, null)
        val b = PlayItem.fromTrack(8, "b", null, null, null)
        val queue = listOf(a, b, a, a)
        val keys = queue.mapIndexed { i, item -> playerQueueKey(i, item) }
        assertEquals(keys.size, keys.toSet().size)
        val chapterKeys = (0 until 3).map { playerChapterKey(it) }
        assertEquals(0, keys.intersect(chapterKeys.toSet()).size)
    }
}
