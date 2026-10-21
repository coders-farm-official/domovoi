package com.domovoi.app.assistant

import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

/** Review round 1: the emulator's Clock app dropped a second intent sent
 *  ~40 ms after the first. [ClockPacer] spaces this app's Clock intents. */
class ClockPacerTest {
    private var nowMs = 10_000L
    private val waits = CopyOnWriteArrayList<Long>()
    private val pacer = ClockPacer(gapMs = 600, nowMs = { nowMs }, pause = { waits += it; nowMs += it })

    @Test fun aSecondIntentRightAfterTheFirstWaitsOutTheGap() = runBlocking {
        pacer.send { "timer" }
        nowMs += 40
        pacer.send { "alarm" }
        assertEquals(listOf(560L), waits)
    }

    @Test fun aClockThatGoesBackNeverWaitsLongerThanTheGap() = runBlocking {
        pacer.send { }
        nowMs -= 3_600_000
        pacer.send { }
        assertEquals(listOf(600L), waits)
    }

    @Test fun intentsFarApartDoNotWait() = runBlocking {
        pacer.send { }
        nowMs += 5_000
        pacer.send { }
        assertEquals(emptyList<Long>(), waits)
    }

    @Test fun intentsSentTogetherGoOneAtATime() = runBlocking {
        val order = CopyOnWriteArrayList<Int>()
        (1..3).map { i -> async { pacer.send { order += i } } }.awaitAll()
        assertEquals(3, order.size)
        assertEquals("two of the three waited the whole gap", listOf(600L, 600L), waits)
    }
}
