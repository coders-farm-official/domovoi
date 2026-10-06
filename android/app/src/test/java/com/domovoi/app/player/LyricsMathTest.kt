package com.domovoi.app.player

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The timing math every lyrics surface shares (lyrics-build CONTRACT [D1],
 * [D12]): which line is being sung, and where a room or this phone is
 * between two readings. All lines here are invented.
 */
class LyricsMathTest {

    private val lines = listOf(
        LyricLine(12_400, "the lantern hums beside the river door"),
        LyricLine(16_850, "and every copper kettle sings at dawn"),
        LyricLine(21_100, ""),
        LyricLine(21_300, "we carried paper boats along the hall"),
        LyricLine(65_000, "the river door is open tonight"),
    )

    // ── activeIndex ─────────────────────────────────────────────────────

    @Test fun noLinesIsNoLine() {
        assertEquals(-1, LyricsMath.activeIndex(emptyList(), 0))
        assertEquals(-1, LyricsMath.activeIndex(emptyList(), 99_000))
    }

    @Test fun beforeTheFirstLineIsMinusOne() {
        assertEquals(-1, LyricsMath.activeIndex(lines, 0))
        assertEquals(-1, LyricsMath.activeIndex(lines, 12_400 - LyricsMath.LEAD_MS - 1))
    }

    @Test fun aLineShowsExactlyLeadMsEarly() {
        assertEquals(150L, LyricsMath.LEAD_MS)
        assertEquals(0, LyricsMath.activeIndex(lines, 12_400 - LyricsMath.LEAD_MS))
        assertEquals(0, LyricsMath.activeIndex(lines, 16_850 - LyricsMath.LEAD_MS - 1))
        assertEquals(1, LyricsMath.activeIndex(lines, 16_850 - LyricsMath.LEAD_MS))
    }

    @Test fun betweenLinesIsTheLastOneStarted() {
        assertEquals(1, LyricsMath.activeIndex(lines, 19_000))
        // The gap is a line of its own: the break is "being sung" too.
        assertEquals(2, LyricsMath.activeIndex(lines, 21_100))
        assertEquals(3, LyricsMath.activeIndex(lines, 30_000))
    }

    @Test fun afterTheLastLineIsTheLastLine() {
        assertEquals(4, LyricsMath.activeIndex(lines, 65_000))
        assertEquals(4, LyricsMath.activeIndex(lines, 600_000))
        assertEquals(4, LyricsMath.activeIndex(lines, Long.MAX_VALUE))
    }

    @Test fun equalTimesTakeTheLastOfThem() {
        val twins = listOf(LyricLine(1_000, "a"), LyricLine(1_000, "b"), LyricLine(2_000, "c"))
        assertEquals(1, LyricsMath.activeIndex(twins, 1_000))
    }

    @Test fun theBinarySearchAgreesWithALinearScan() {
        val many = (0 until 997).map { LyricLine(it * 1_337L, "line $it") }
        for (pos in listOf(-5L, 0L, 1L, 1_186L, 1_187L, 500_000L, 1_332_000L, 1_400_000L)) {
            val linear = many.indexOfLast { it.t <= pos + LyricsMath.LEAD_MS }
            assertEquals("at $pos", linear, LyricsMath.activeIndex(many, pos))
        }
    }

    // ── roomPositionMs ──────────────────────────────────────────────────

    @Test fun aPlayingRoomRunsOnFromItsReading() {
        // Read at 42.37 s; 1.5 s later on this phone's clock.
        assertEquals(43_870, LyricsMath.roomPositionMs(42.37, 10_000, 11_500, true, 205.0, 0))
    }

    @Test fun aPausedRoomStaysWhereItWas() {
        assertEquals(42_370, LyricsMath.roomPositionMs(42.37, 10_000, 99_000, false, 205.0, 0))
    }

    @Test fun theRoomPositionIsClampedToTheSong() {
        assertEquals(205_000, LyricsMath.roomPositionMs(204.0, 0, 9_000, true, 205.0, 0))
        assertEquals(0, LyricsMath.roomPositionMs(0.1, 0, 0, true, 205.0, 400))
        // No duration: only the floor.
        assertEquals(300_000, LyricsMath.roomPositionMs(299.0, 0, 1_000, true, null, 0))
    }

    @Test fun theNudgeShowsLyricsLaterWhenPositive() {
        assertEquals(41_870, LyricsMath.roomPositionMs(42.37, 0, 0, false, 205.0, 500))
        assertEquals(42_620, LyricsMath.roomPositionMs(42.37, 0, 0, false, 205.0, -250))
    }

    @Test fun aClockBeforeTheReadingAddsNothing() {
        assertEquals(42_370, LyricsMath.roomPositionMs(42.37, 10_000, 9_000, true, 205.0, 0))
        assertEquals(0, LyricsMath.roomPositionMs(Double.NaN, 0, 0, false, 205.0, 0))
    }

    // ── localPositionMs and steady ──────────────────────────────────────

    @Test fun thisPhoneRunsOnBetweenTicksAtItsSpeed() {
        assertEquals(10_250, LyricsMath.localPositionMs(10.0, 5_000, 5_250, true, 1f, 205.0))
        assertEquals(10_375, LyricsMath.localPositionMs(10.0, 5_000, 5_250, true, 1.5f, 205.0))
        assertEquals(10_000, LyricsMath.localPositionMs(10.0, 5_000, 5_250, false, 1f, 205.0))
        assertEquals(205_000, LyricsMath.localPositionMs(204.9, 0, 5_000, true, 1f, 205.0))
        // A speed that is not a speed counts as 1x.
        assertEquals(10_250, LyricsMath.localPositionMs(10.0, 5_000, 5_250, true, 0f, null))
    }

    @Test fun aSmallStepBackWhilePlayingIsHeldButASeekIsNot() {
        assertEquals(20_000, LyricsMath.steady(20_000, 19_960, playing = true))
        assertEquals(20_000, LyricsMath.steady(20_000, 20_000 - LyricsMath.STEADY_WINDOW_MS + 1, playing = true))
        assertEquals(19_700, LyricsMath.steady(20_000, 20_000 - LyricsMath.STEADY_WINDOW_MS, playing = true))
        assertEquals(4_000, LyricsMath.steady(20_000, 4_000, playing = true))
        assertEquals(20_100, LyricsMath.steady(20_000, 20_100, playing = true))
        // Paused, and with nothing before, everything is followed.
        assertEquals(19_960, LyricsMath.steady(20_000, 19_960, playing = false))
        assertEquals(19_960, LyricsMath.steady(null, 19_960, playing = true))
    }

    // ── the room nudge ──────────────────────────────────────────────────

    @Test fun nudgesRoundTripThroughTheirStoredJson() {
        val map = mapOf("kitchen" to 250L, "office" to -750L)
        assertEquals(map, LyricsNudge.decode(LyricsNudge.encode(map)))
        assertEquals("{\"kitchen\":250,\"office\":-750}", LyricsNudge.encode(map))
    }

    @Test fun storedNudgesAreReadForgivingly() {
        assertEquals(emptyMap<String, Long>(), LyricsNudge.decode(null))
        assertEquals(emptyMap<String, Long>(), LyricsNudge.decode(""))
        assertEquals(emptyMap<String, Long>(), LyricsNudge.decode("not json"))
        assertEquals(emptyMap<String, Long>(), LyricsNudge.decode("[1, 2]"))
        assertEquals(
            mapOf("den" to 10_000L, "hall" to -10_000L),
            LyricsNudge.decode("{\"den\": 99999, \"hall\": -99999, \"porch\": \"250\", \"attic\": 0, \"\": 5}"),
        )
    }

    @Test fun settingANudgeClampsAndZeroForgetsTheRoom() {
        val one = LyricsNudge.with(emptyMap(), "kitchen", LyricsNudge.STEP_MS)
        assertEquals(mapOf("kitchen" to 250L), one)
        assertEquals(mapOf("kitchen" to 10_000L), LyricsNudge.with(one, "kitchen", 60_000))
        assertEquals(emptyMap<String, Long>(), LyricsNudge.with(one, "kitchen", 0))
    }

    @Test fun theNudgeLabelSaysWhichWay() {
        assertEquals("timing", LyricsNudge.label(0))
        assertEquals("timing +0.25 s", LyricsNudge.label(250))
        assertEquals("timing −0.5 s", LyricsNudge.label(-500))
        assertEquals("timing +2 s", LyricsNudge.label(2_000))
        assertEquals("timing +1.75 s", LyricsNudge.label(1_750))
    }
}
