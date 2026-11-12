package com.domovoi.app.listens

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The listen rules (domovoi/music_listens.py `outcome_for`, player.jsx
 * ListenLog): seconds actually playing, finished at 90 %, skipped when a
 * skip ended it, stopped otherwise, nothing under a second.
 */
class ListenTrackerTest {
    private var mono = 0L
    private val t = ListenTracker(wallMs = { 1_762_970_591_000L }, monoMs = { mono })

    private fun play(sec: Int) { mono += sec * 1000L }

    @Test fun aSkipAt20sOf200IsSkipped() {
        t.begin(812, "s", 200.0, playing = true)
        play(20)
        t.skip()
        val row = t.close(natural = false)!!
        assertEquals("skipped", row.outcome)
        assertEquals(20, row.playedSec)
        assertEquals(200, row.durationSec)
        assertEquals(812L, row.trackId)
    }

    @Test fun at185Of200ThenNextIsFinished() {
        t.begin(812, "s", 200.0, playing = true)
        play(185)
        t.skip()
        assertEquals("finished", t.close(natural = false)!!.outcome)
    }

    @Test fun pauseTimeIsNotCounted() {
        t.begin(812, "s", 200.0, playing = true)
        play(30)
        t.playing(false)
        play(600) // ten minutes paused
        t.playing(true)
        play(10)
        val row = t.close(natural = false)!!
        assertEquals(40, row.playedSec)
        assertEquals("stopped", row.outcome)
    }

    @Test fun aSongThatRanToItsEndIsFinishedEvenIfTheClockSaysLess() {
        t.begin(812, "s", 200.0, playing = true)
        play(150) // a seek forward isn't listening, but the song did end
        t.skip()  // a stale skip flag doesn't make a natural end a skip
        val row = t.close(natural = true)!!
        assertEquals("finished", row.outcome)
        assertEquals(200, row.playedSec)
    }

    @Test fun playedNeverExceedsTheLength() {
        t.begin(812, "s", 100.0, playing = true)
        play(130)
        assertEquals(100, t.close(natural = false)!!.playedSec)
    }

    @Test fun underASecondIsNoListen() {
        t.begin(812, "s", 200.0, playing = true)
        mono += 900
        assertNull(t.close(natural = false))
    }

    @Test fun startedAtIsWhenItBegan() {
        t.begin(812, "s", 200.0, playing = true)
        play(5)
        assertEquals("2025-11-12T18:03:11Z", OutboxRow.isoUtc(t.close(natural = false)!!.startedAtMs))
    }

    @Test fun theRuleIsTheServers() {
        assertEquals("finished", ListenTracker.outcomeFor(180.0, 200.0, skipped = true))
        assertEquals("skipped", ListenTracker.outcomeFor(179.0, 200.0, skipped = true))
        assertEquals("stopped", ListenTracker.outcomeFor(179.0, 200.0, skipped = false))
        assertEquals("stopped", ListenTracker.outcomeFor(500.0, null, skipped = false))
    }
}
