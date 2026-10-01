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
}
