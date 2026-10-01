package com.domovoi.app.diagnostics

import org.junit.Assert.assertEquals
import org.junit.Test

class FreezeDetectorTest {

    @Test fun answeredPingsAreQuiet() {
        val d = FreezeDetector(3_000)
        assertEquals(FreezeDetector.Step.Ok, d.observe(postedAtMs = 0, ranAtMs = 4, nowMs = 250))
        assertEquals(FreezeDetector.Step.Ok, d.observe(postedAtMs = 1_000, ranAtMs = null, nowMs = 1_250))
        assertEquals(FreezeDetector.Step.Ok, d.observe(postedAtMs = 1_000, ranAtMs = 1_300, nowMs = 1_500))
    }

    @Test fun shortStallUnderTheThresholdIsNotAFreeze() {
        val d = FreezeDetector(3_000)
        assertEquals(FreezeDetector.Step.Ok, d.observe(0, null, 2_999))
        assertEquals(FreezeDetector.Step.Ok, d.observe(0, 2_999, 3_100))
    }

    @Test fun freezeIsReportedOnceThenItsEnd() {
        val d = FreezeDetector(3_000)
        assertEquals(FreezeDetector.Step.Ok, d.observe(0, null, 2_750))
        assertEquals(FreezeDetector.Step.Frozen(3_000), d.observe(0, null, 3_000))
        // Still frozen: not reported again.
        assertEquals(FreezeDetector.Step.Ok, d.observe(0, null, 3_250))
        assertEquals(FreezeDetector.Step.Ok, d.observe(0, null, 4_500))
        // The ping finally ran: the whole stall, measured to when it ran.
        assertEquals(FreezeDetector.Step.Recovered(4_600), d.observe(0, 4_600, 4_750))
        // A later freeze is a new episode.
        assertEquals(FreezeDetector.Step.Frozen(3_200), d.observe(10_000, null, 13_200))
    }

    /**
     * The watchdog loop's timing (MainThreadWatchdog.loop), replayed against
     * a main thread that is idle except for one block: post a ping, check
     * every CHECK_EVERY_MS until it has run, pause PING_EVERY_MS, repeat.
     * True when the detector reported the block as a freeze.
     */
    private fun watchdogSees(blockStart: Long, blockMs: Long): Boolean {
        val check = MainThreadWatchdog.CHECK_EVERY_MS
        val pause = MainThreadWatchdog.PING_EVERY_MS
        val d = FreezeDetector(MainThreadWatchdog.FREEZE_THRESHOLD_MS)
        val blockEnd = blockStart + blockMs
        var now = 0L
        var frozen = false
        while (now < blockEnd + 2_000) {
            val postedAt = now
            val ranAt = if (postedAt in blockStart until blockEnd) blockEnd else postedAt
            while (true) {
                now += check
                val seen = ranAt.takeIf { it <= now }
                if (d.observe(postedAt, seen, now) is FreezeDetector.Step.Frozen) frozen = true
                if (seen != null) break
            }
            now += pause
        }
        return frozen
    }

    @Test fun aFreezeHalfASecondOverTheThresholdIsAlwaysRecorded() {
        // The 2026-09-30 player-tab freeze took 3.48 s on the emulator; with a
        // ping a second it went unrecorded unless it began just after a ping.
        val cycle = MainThreadWatchdog.CHECK_EVERY_MS + MainThreadWatchdog.PING_EVERY_MS
        for (phase in 0 until cycle * 4 step 10) {
            assertEquals("block starting at $phase ms", true, watchdogSees(10_000 + phase, 3_500))
        }
    }

    @Test fun aStallUnderTheThresholdIsNeverRecorded() {
        for (phase in 0 until 1_000L step 10) {
            assertEquals("block starting at $phase ms", false, watchdogSees(10_000 + phase, 2_900))
        }
    }
}
