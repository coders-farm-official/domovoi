package com.domovoi.app.roommode

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The room phone's wake gate against the Pi's (satellite/wake_gate.py and
 * satellite/tests/test_wake_gate.py, the same cases): patience, the floor
 * a run is counted against, the cooldown after a turn that went wrong, and
 * the defaults a phone wakes with when nothing is set.
 */
class WakeGateTest {
    private fun feedAll(g: WakeGate, scores: List<Float>, from: Long = 0L): List<Boolean> =
        scores.mapIndexed { i, s -> g.feed(s, from + i * 80L) }

    @Test
    fun theDefaultsWantTwoChunksInARow() {
        // Patience 2 and threshold 0.7 since 2026-11-14 (were 1 and 0.5).
        assertEquals(0.7f, RoomStore.DEFAULT_THRESHOLD, 0f)
        val g = WakeGate(RoomStore.DEFAULT_THRESHOLD)
        assertEquals(2, g.patience)
        assertEquals(2.0, g.cooldownSec, 0.0)
        assertFalse("one chunk is not a wake", g.feed(0.71f, 1_000))
        assertTrue(g.feed(0.5f, 1_080))
        assertEquals(0.71f, g.lastPeak!!, 0f)
        // At the threshold is not over it.
        assertFalse(g.feed(0.7f, 2_000))
        assertFalse(g.feed(0.7f, 2_080))
    }

    @Test
    fun withPatienceTwoOneChunkIsNotAWakeAndTwoInARowAre() {
        val g = WakeGate(0.5f, patience = 2, cooldownSec = 0.0)
        assertFalse(g.feed(0.99f, 1_000))
        assertFalse(g.feed(0.1f, 1_080)) // the dip ends the run
        assertFalse(g.feed(0.7f, 1_160))
        assertTrue(g.feed(0.93f, 1_240))
        assertEquals(0.93f, g.lastPeak!!, 0f)
        assertEquals(2, g.lastRun)
        // The run starts again after a wake.
        assertFalse(g.feed(0.9f, 1_320))
    }

    @Test
    fun patienceThreeNeedsThreeInARowAndADipStartsAgain() {
        val g = WakeGate(0.5f, patience = 3, cooldownSec = 0.0)
        assertEquals(listOf(false, false, false, false, false, true), feedAll(g, listOf(0.9f, 0.9f, 0.2f, 0.9f, 0.9f, 0.9f)))
        assertEquals(3, g.lastRun)
    }

    @Test
    fun aFarFieldWakeAtARaisedThresholdStillWakesAndALoneBlipDoesNot() {
        // Threshold 0.8: the run counts against max(0.3, 0.8 x 0.6) = 0.48.
        val g = WakeGate(0.8f, patience = 2, cooldownSec = 0.0)
        assertEquals(0.48f, g.floor, 1e-6f)
        assertEquals(listOf(false, false, true, false), feedAll(g, listOf(0.1f, 0.6f, 0.85f, 0.6f)))
        assertEquals(0.85f, g.lastPeak!!, 0f)
        assertEquals(listOf(false, true), feedAll(WakeGate(0.8f, 2, 0.0), listOf(0.85f, 0.6f)))
        assertFalse(feedAll(WakeGate(0.8f, 2, 0.0), listOf(0.1f, 0.85f, 0.15f, 0.1f)).any { it })
        // Over the floor for ever, never reaching the threshold: no wake.
        assertFalse(feedAll(WakeGate(0.8f, 2, 0.0), List(10) { 0.7f }).any { it })
    }

    @Test
    fun theFloorNeverSitsAboveTheThreshold() {
        assertEquals(0.3f, WakeGate.floorFor(0.5f), 0f)
        assertEquals(0.2f, WakeGate.floorFor(0.2f), 0f)
        assertEquals(0.6f, WakeGate.floorFor(1.0f), 1e-6f)
    }

    @Test
    fun aResetBreaksTheRun() {
        val g = WakeGate(0.5f, patience = 2, cooldownSec = 0.0)
        g.feed(0.9f, 1_000)
        g.reset()
        assertFalse(g.feed(0.9f, 1_160))
        assertTrue(g.feed(0.9f, 1_240))
    }

    @Test
    fun nothingWakesInTheCooldownAfterATurnThatWentWrong() {
        val g = WakeGate(0.5f, patience = 1) // the default cooldown: 2 s
        g.quietFor(10_000)
        assertTrue(g.inCooldown(11_999))
        for (t in listOf(10_000L, 10_100L, 11_000L, 11_999L)) assertFalse("woke at $t", g.feed(0.98f, t))
        assertFalse(g.inCooldown(12_000))
        assertTrue(g.feed(0.9f, 12_000))
    }

    @Test
    fun aRunStraddlingTheEndOfTheCooldownCountsOnlyItsOutsidePart() {
        val g = WakeGate(0.5f, patience = 2, cooldownSec = 2.0)
        g.quietFor(10_000)
        assertFalse(g.feed(0.9f, 11_920))
        assertFalse(g.feed(0.9f, 12_000))
        assertTrue(g.feed(0.9f, 12_080))
    }

    @Test
    fun afterAnOrdinaryTurnThereIsNoCooldown() {
        val g = WakeGate(0.5f, patience = 1, cooldownSec = 2.0)
        g.quietFor(10_000, 0L)
        assertTrue(g.feed(0.9f, 10_000))
    }

    @Test
    fun aZeroCooldownSettingQuietsNothing() {
        val g = WakeGate(0.5f, patience = 1, cooldownSec = 0.0)
        g.quietFor(10_000)
        assertTrue(g.feed(0.9f, 10_000))
    }

    @Test
    fun theSettingsFallBackToTheDefaults() {
        assertEquals(2, WakeGate.PATIENCE_DEFAULT)
        assertEquals(2.0, WakeGate.COOLDOWN_SEC_DEFAULT, 0.0)
        assertEquals(2, WakeGate.patienceSetting(null))
        assertEquals(3, WakeGate.patienceSetting(3))
        assertEquals(3, WakeGate.patienceSetting(3.0))
        assertEquals(1, WakeGate.patienceSetting(0))
        assertEquals(5, WakeGate.patienceSetting(9))
        for (bad in listOf<Any>("x", true, 2.5)) assertEquals("$bad", 2, WakeGate.patienceSetting(bad))
        assertEquals(2.0, WakeGate.cooldownSetting(null), 0.0)
        assertEquals(0.0, WakeGate.cooldownSetting(0), 0.0)
        assertEquals(10.0, WakeGate.cooldownSetting(99), 0.0)
        assertEquals(0.0, WakeGate.cooldownSetting(-1), 0.0)
        for (bad in listOf<Any>("x", true, Double.NaN)) assertEquals("$bad", 2.0, WakeGate.cooldownSetting(bad), 0.0)
        // The constructor takes them through the same rules.
        assertEquals(5, WakeGate(0.5f, patience = 40).patience)
        assertEquals(10.0, WakeGate(0.5f, cooldownSec = 60.0).cooldownSec, 0.0)
    }
}
