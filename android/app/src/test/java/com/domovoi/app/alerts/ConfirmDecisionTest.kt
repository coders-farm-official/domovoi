package com.domovoi.app.alerts

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * A6: what a ringing alarm does once it has asked the server — post the
 * recorded fire, post "going off now", re-arm, stay quiet about a timer
 * that was cancelled, or post "couldn't reach Domovoi to confirm" when the
 * server could not say (any error, a 404 from an older server, a 503 with
 * no fire ledger — all of which reach this as [Lookup.Failed]).
 */
class ConfirmDecisionTest {
    private val t0 = Instant.parse("2026-09-30T15:10:00Z").toEpochMilli()
    private fun iso(ms: Long) = Instant.ofEpochMilli(ms).toString()

    private val fire = TimerFire(id = 42, timer_id = 17, kind = "timer", room_id = "garage", summary = "heard in garage")
    private val noFires = Lookup.Ok(TimerFireList(iso(t0), emptyList()), t0)

    private fun listing(expires: Long, serverNow: Long = t0, receivedAt: Long = t0) = Lookup.Ok(
        AlertTimerList(iso(serverNow), listOf(AlertTimer(id = 17, expires_at = iso(expires), room_id = "garage"))),
        receivedAt,
    )

    @Test fun aRecordedFireIsPostedWithItsSummary() {
        val d = confirmDecision(17, Lookup.Ok(TimerFireList(iso(t0), listOf(fire)), t0), null)
        assertEquals(ConfirmDecision.PostFire(fire), d)
        assertEquals("heard in garage", (d as ConfirmDecision.PostFire).fire.summary)
    }

    @Test fun anotherTimersFireIsNotThisOne() {
        val other = Lookup.Ok(TimerFireList(iso(t0), listOf(fire.copy(timer_id = 99))), t0)
        assertEquals(ConfirmDecision.Suppress, confirmDecision(17, other, Lookup.Ok(AlertTimerList(iso(t0)), t0)))
    }

    @Test fun stillListedAndDueIsGoingOffNow() {
        val d = confirmDecision(17, noFires, listing(expires = t0 + 2_000))
        assertTrue(d is ConfirmDecision.PostNow)
        assertEquals(17L, (d as ConfirmDecision.PostNow).timer.id)
        assertTrue(confirmDecision(17, noFires, listing(expires = t0 - 1_000)) is ConfirmDecision.PostNow)
    }

    @Test fun stillListedButLaterIsReArmedOnTheServersClock() {
        // The server says 15:10:00 when this phone reads 15:07:00, and the
        // timer is due 15:15:00: re-arm for 15:12:00 on this phone's clock.
        val d = confirmDecision(17, noFires, listing(expires = t0 + 5 * 60_000, receivedAt = t0 - 3 * 60_000))
        assertEquals(t0 + 2 * 60_000, (d as ConfirmDecision.Reschedule).triggerAtMs)
    }

    @Test fun neitherListedNorFiredWasCancelled() {
        assertEquals(
            ConfirmDecision.Suppress,
            confirmDecision(17, noFires, Lookup.Ok(AlertTimerList(iso(t0), emptyList()), t0)),
        )
    }

    @Test fun anyFailureRingsAnyway() {
        assertEquals(ConfirmDecision.PostUnconfirmed, confirmDecision(17, Lookup.Failed, null))
        assertEquals(ConfirmDecision.PostUnconfirmed, confirmDecision(17, noFires, Lookup.Failed))
        assertEquals(ConfirmDecision.PostUnconfirmed, confirmDecision(17, noFires, null))
        assertEquals("couldn't reach Domovoi to confirm", SUB_UNCONFIRMED)
        assertEquals("going off now", SUB_GOING_OFF)
    }
}
