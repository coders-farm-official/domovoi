package com.domovoi.app.player

import com.domovoi.app.player.ChapterSkip.Move
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Next and previous for a book (batch 18, 2026-10-20): a book is one queue
 * item, so the mini player's next did nothing. Now they move by chapter.
 */
class ChapterSkipTest {
    private val chapters = listOf(
        Chapter("Opening", 0.0),
        Chapter("The Road", 600.0),
        Chapter("The Inn", 1_500.0),
    )

    private fun book(id: Long, chapters: List<Chapter>) =
        PlayItem.fromBook(id, "Book $id", "Author", 2_400.0, null, chapters)

    private fun song(id: Long) = PlayItem.fromTrack(id, "Song $id", "Band", "Album", 200.0)

    @Test fun nextGoesToTheNextChapter() {
        assertEquals(Move.Seek(600.0), ChapterSkip.next(chapters, 0.0, hasNextItem = false))
        assertEquals(Move.Seek(1_500.0), ChapterSkip.next(chapters, 700.0, hasNextItem = false))
        // Just after a seek to a chapter (read back a hair before its start),
        // next is the chapter after it, not the same one again.
        assertEquals(Move.Seek(1_500.0), ChapterSkip.next(chapters, 599.8, hasNextItem = false))
    }

    @Test fun fromTheLastChapterNextIsTheQueuesNextItemOrNothing() {
        assertEquals(Move.NextItem, ChapterSkip.next(chapters, 1_600.0, hasNextItem = true))
        assertEquals(Move.None, ChapterSkip.next(chapters, 1_600.0, hasNextItem = false))
        // A book of one chapter, or none listed, has no next of its own.
        assertEquals(Move.None, ChapterSkip.next(listOf(Chapter("", 0.0)), 10.0, hasNextItem = false))
        assertEquals(Move.None, ChapterSkip.next(emptyList(), 10.0, hasNextItem = false))
        assertEquals(Move.NextItem, ChapterSkip.next(emptyList(), 10.0, hasNextItem = true))
    }

    @Test fun previousRestartsTheChapterPastThreeSeconds() {
        assertEquals(Move.Seek(600.0), ChapterSkip.previous(chapters, 700.0, hasPreviousItem = false))
        assertEquals(Move.Seek(600.0), ChapterSkip.previous(chapters, 603.5, hasPreviousItem = true))
        assertEquals(Move.Seek(0.0), ChapterSkip.previous(chapters, 40.0, hasPreviousItem = true))
    }

    @Test fun previousNearAChaptersStartGoesToTheOneBefore() {
        assertEquals(Move.Seek(600.0), ChapterSkip.previous(chapters, 1_501.0, hasPreviousItem = false))
        assertEquals(Move.Seek(0.0), ChapterSkip.previous(chapters, 602.0, hasPreviousItem = false))
        // At the very start of the first chapter: the queue's previous item,
        // else the book's start.
        assertEquals(Move.PreviousItem, ChapterSkip.previous(chapters, 1.0, hasPreviousItem = true))
        assertEquals(Move.Seek(0.0), ChapterSkip.previous(chapters, 1.0, hasPreviousItem = false))
    }

    @Test fun chaptersOutOfOrderAreReadInOrder() {
        val shuffled = listOf(chapters[2], chapters[0], chapters[1])
        assertEquals(Move.Seek(600.0), ChapterSkip.next(shuffled, 100.0, hasNextItem = false))
        assertEquals(Move.Seek(600.0), ChapterSkip.previous(shuffled, 1_501.0, hasPreviousItem = false))
    }

    @Test fun canNextIsWhetherNextDoesAnything() {
        val one = book(1, listOf(Chapter("", 0.0)))
        val three = book(2, chapters)
        // A book of one chapter, alone: nothing to go to.
        assertFalse(ChapterSkip.canNext(listOf(one), 0, 30.0, casting = false))
        // ...with something queued after it: that.
        assertTrue(ChapterSkip.canNext(listOf(one, three), 0, 30.0, casting = false))
        // A book with chapters: until its last one.
        assertTrue(ChapterSkip.canNext(listOf(three), 0, 30.0, casting = false))
        assertFalse(ChapterSkip.canNext(listOf(three), 0, 1_600.0, casting = false))
        // Songs keep the queue's next.
        assertTrue(ChapterSkip.canNext(listOf(song(1), song(2)), 0, 0.0, casting = false))
        assertFalse(ChapterSkip.canNext(listOf(song(1), song(2)), 1, 0.0, casting = false))
        // While casting it is the room's skip; with no queue, nothing.
        assertTrue(ChapterSkip.canNext(emptyList(), 0, 0.0, casting = true))
        assertFalse(ChapterSkip.canNext(emptyList(), 0, 0.0, casting = false))
    }

    @Test fun onlyBooksMoveByChapter() {
        assertTrue(ChapterSkip.applies(book(1, chapters)))
        val episode = PlayItem.fromEpisode(3, "Ep", "Show", 1_000.0, null, chapters)
        assertFalse(ChapterSkip.applies(episode))
        assertFalse(ChapterSkip.applies(song(1)))
        assertFalse(ChapterSkip.applies(null))
    }
}
