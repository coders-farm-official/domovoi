package com.domovoi.app.alerts

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * A2, the book-level half: the dedupe book that makes the live path and the
 * alarm path post one timer once, its 24-hour memory, and which fires a
 * catch-up (or a push) posts — the first-run rule and the 30-minute window.
 * The path-level half (live then alarm, alarm then live) is AlertEngineTest.
 */
class AlertBookTest {
    private val now = Instant.parse("2026-09-30T15:30:00Z").toEpochMilli()
    private val hour = 60 * 60 * 1000L

    // ---- the dedupe book ------------------------------------------------------

    @Test fun aTimerIsMarkedOnceAndOnlyOnce() {
        val (b1, first) = markInBook(emptyList(), "abcd1234", 17, now)
        assertTrue(first)
        assertEquals(listOf("abcd1234|17|$now"), b1)
        val (b2, second) = markInBook(b1, "abcd1234", 17, now + 5_000)
        assertFalse("the other path already posted it", second)
        assertEquals(b1, b2)
    }

    @Test fun anotherServersTimerWithTheSameIdIsAnotherTimer() {
        val (b1, _) = markInBook(emptyList(), "aaaaaaaa", 17, now)
        val (_, ok) = markInBook(b1, "bbbbbbbb", 17, now)
        assertTrue(ok)
    }

    @Test fun entriesOlderThanADayAreForgotten() {
        val (b1, _) = markInBook(emptyList(), "k", 17, now - 24 * hour - 1)
        val (b2, _) = markInBook(b1, "k", 18, now - 23 * hour)
        val (b3, again) = markInBook(b2, "k", 17, now)
        assertTrue("a day on, 17 is a stranger again", again)
        assertEquals(listOf("k|18|${now - 23 * hour}", "k|17|$now"), b3)
    }

    @Test fun theBookKeepsAtMostFiveHundredAndDropsJunk() {
        var book = listOf("junk", "k|x|y", "k|1|2|3")
        for (i in 1..520) book = markInBook(book, "k", i.toLong(), now).first
        assertEquals(ALERTED_MAX, book.size)
        assertEquals("k|21|$now", book.first())
        assertTrue(book.none { it == "junk" })
    }

    @Test fun aCorruptBookReadsEmpty() {
        assertEquals(emptyList<String>(), decodeAlerted("{not json"))
        assertEquals(emptyList<String>(), decodeAlerted(null))
        assertEquals(listOf("a|1|2"), decodeAlerted(encodeAlerted(listOf("a|1|2"))))
        assertEquals(mapOf("k" to 5L), decodeSeen(encodeSeen(mapOf("k" to 5L))))
        assertEquals(emptyMap<String, Long>(), decodeSeen("[]"))
    }

    // ---- which fires post -----------------------------------------------------

    private fun f(id: Long, agoMs: Long) =
        TimerFire(id = id, timer_id = 100 + id, fired_at = Instant.ofEpochMilli(now - agoMs).toString())

    @Test fun aFirstRunPostsOnlyTheLastTwoMinutes() {
        val fires = listOf(f(5, 30_000), f(4, 100_000), f(3, 10 * 60_000), f(2, 20 * 60_000), f(1, 50 * 60_000))
        val plan = planFires(fires, seen = null, serverNowMs = now, maxAgeMs = CATCH_UP_WINDOW_MS)
        assertEquals(listOf(4L, 5L), plan.post.map { it.id })
        assertEquals(5L, plan.seen)
    }

    @Test fun aFirstRunWithNothingRecentPostsNothingButRemembers() {
        val plan = planFires(listOf(f(9, 10 * 60_000)), seen = null, serverNowMs = now, maxAgeMs = CATCH_UP_WINDOW_MS)
        assertEquals(emptyList<TimerFire>(), plan.post)
        assertEquals(9L, plan.seen)
        assertEquals(0L, planFires(emptyList(), null, now, CATCH_UP_WINDOW_MS).seen)
    }

    @Test fun aCatchUpPostsWhatIsNewAndUnderThirtyMinutesOld() {
        val fires = listOf(f(4, 40 * 60_000), f(5, 29 * 60_000), f(6, 60_000), f(3, 1_000))
        val plan = planFires(fires, seen = 3, serverNowMs = now, maxAgeMs = CATCH_UP_WINDOW_MS)
        assertEquals(listOf(5L, 6L), plan.post.map { it.id })
        assertEquals("the old one still moves seen on", 6L, plan.seen)
    }

    @Test fun nothingNewLeavesSeenWhereItWas() {
        val plan = planFires(listOf(f(2, 1_000), f(3, 1_000)), seen = 7, serverNowMs = now, maxAgeMs = CATCH_UP_WINDOW_MS)
        assertEquals(emptyList<TimerFire>(), plan.post)
        assertEquals(7L, plan.seen)
    }

    @Test fun theWindowIsOnTheServersClock() {
        // Fired 20 minutes before the SERVER's now: in the window, whatever
        // this phone's own clock says.
        val fires = listOf(f(8, 20 * 60_000))
        assertEquals(1, planFires(fires, 7, now, CATCH_UP_WINDOW_MS).post.size)
        assertEquals(0, planFires(fires, 7, now + 15 * 60_000, CATCH_UP_WINDOW_MS).post.size)
    }

    @Test fun aFireWithNoReadableTimeNeverPosts() {
        val plan = planFires(listOf(TimerFire(id = 8, fired_at = "yesterday")), 7, now, CATCH_UP_WINDOW_MS)
        assertEquals(emptyList<TimerFire>(), plan.post)
        assertEquals(8L, plan.seen)
    }
}
