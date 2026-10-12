package com.domovoi.app.ui.shell

import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * When the app drops to local media because the saved server is out of
 * reach ([Reachability], [askWhileOnScreen]). The grace counts only time
 * the app spent on screen asking: a phone that sat 30 s behind the system
 * folder picker — frozen, its socket missing a ping, the first request
 * after the thaw failing on a stale connection — used to come back to local
 * media next to a healthy server, because the wall clock said the grace was
 * long over. The real fallback (on screen, out of the house) still happens.
 */
class ReachabilityTest {

    // ── the tracker ─────────────────────────────────────────────────────

    @Test fun onScreenWithTheServerGoneItFallsBackAfterTheGrace() {
        val r = Reachability(graceMs = 10_000)
        r.resume(0)
        r.missed(5_000)
        assertFalse(r.unreachable)
        r.missed(9_999)
        assertFalse("not a moment before the grace", r.unreachable)
        r.missed(10_000)
        assertTrue(r.unreachable)
    }

    @Test fun timeInTheBackgroundIsNotCountedButTimeOnScreenIs() {
        val r = Reachability(graceMs = 10_000)
        r.resume(0)
        r.missed(6_000)
        r.pause(6_000) // six seconds of asking, then the app left the screen
        r.resume(606_000) // ten minutes later
        r.missed(606_500)
        assertFalse("6.5 s on screen, whatever the clock says", r.unreachable)
        r.missed(610_000)
        assertTrue("10 s on screen", r.unreachable)
    }

    @Test fun anAnswerLandingWhileTheAppIsAwayCountsForNothing() {
        val r = Reachability(graceMs = 10_000)
        r.resume(0)
        r.pause(1_000)
        r.missed(200_000)
        assertFalse(r.unreachable)
    }

    @Test fun outOfReachStaysSoThroughABackgroundSpellUntilTheServerAnswers() {
        val r = Reachability(graceMs = 10_000)
        r.resume(0)
        r.missed(12_000)
        assertTrue(r.unreachable)
        r.pause(13_000)
        r.resume(200_000)
        assertTrue("coming back is not an answer", r.unreachable)
        r.missed(200_500)
        assertTrue(r.unreachable)
        r.answered(204_500)
        assertFalse(r.unreachable)
    }

    @Test fun anAnswerOrTheSocketStartsTheGraceOver() {
        val r = Reachability(graceMs = 10_000)
        r.resume(0)
        r.missed(9_000)
        r.answered(9_500)
        r.missed(15_000)
        assertFalse("5.5 s since the answer", r.unreachable)
        r.missed(19_500)
        assertTrue(r.unreachable)
        r.connected()
        assertFalse(r.unreachable)
        r.resume(30_000)
        r.missed(35_000)
        assertFalse("the socket wiped what was owed", r.unreachable)
    }

    // ── the loop AppShell runs under repeatOnLifecycle(STARTED) ─────────

    private class Rig {
        var t = 0L
        val reach = Reachability(graceMs = UNREACHABLE_GRACE_MS)
        val verdicts = mutableListOf<Boolean>()
        var probes = 0

        /** On screen for [count] probes answering [answers] (each takes [probeMs]); then the app leaves. */
        fun onScreen(count: Int, answers: (Int) -> Boolean, probeMs: Long = 300) = runBlocking {
            var left = count
            val job = launch(start = CoroutineStart.UNDISPATCHED) {
                askWhileOnScreen(
                    reach,
                    probe = { t += probeMs; answers(probes++) },
                    clock = { t },
                    wait = { ms -> if (--left <= 0) awaitCancellation() else t += ms },
                ) { verdicts += it }
            }
            job.cancelAndJoin() // ON_STOP
        }

        /** On screen with a probe out when the app leaves: it never answers. */
        fun leavesMidProbe() = runBlocking {
            val job = launch(start = CoroutineStart.UNDISPATCHED) {
                askWhileOnScreen(reach, probe = { awaitCancellation() }, clock = { t }, wait = {}) { verdicts += it }
            }
            job.cancelAndJoin()
        }
    }

    @Test fun twoMinutesInTheBackgroundWithTheServerFineStaysOnTheWorkspace() {
        val rig = Rig()
        // The socket died while the app was away (the folder picker in front,
        // the process frozen); a probe was out when it left.
        rig.leavesMidProbe()
        rig.t += 120_000
        // Back on screen: the first probe fails on a stale connection, the
        // next one gets through.
        rig.onScreen(3, answers = { it != 0 })
        assertEquals(listOf(false, false, false), rig.verdicts)
        assertFalse(rig.reach.unreachable)
    }

    @Test fun backOnScreenAFreshProbeRunsFirst() {
        val rig = Rig()
        rig.onScreen(1, answers = { false })
        rig.t += 120_000
        val before = rig.probes
        rig.onScreen(1, answers = { true })
        assertEquals("one probe on the way back, before any verdict", before + 1, rig.probes)
        assertEquals(false, rig.verdicts.last())
    }

    @Test fun onScreenWithTheServerGoneTheLoopFallsBackAfterTheGrace() {
        val rig = Rig()
        // 300 ms probes every 4 s: answers at 0.3, 4.6, 8.9, 13.2 s.
        rig.onScreen(5, answers = { false })
        assertEquals(listOf(false, false, false, true, true), rig.verdicts)
        assertTrue(rig.reach.unreachable)
    }

    @Test fun anAnswerBringsTheWorkspaceBack() {
        val rig = Rig()
        rig.onScreen(6, answers = { it >= 5 })
        assertEquals(listOf(false, false, false, true, true, false), rig.verdicts)
    }
}
