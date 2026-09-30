package com.domovoi.app.alerts

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * A3 reconcile, A4 the server-clock offset, A5 exact or inexact by API
 * level — the alarm mirror's pure rules.
 */
class TimerAlarmMirrorTest {
    private val t0 = Instant.parse("2026-09-30T15:00:00Z").toEpochMilli()
    private val min = 60_000L

    private fun iso(ms: Long) = Instant.ofEpochMilli(ms).toString()

    private fun alarm(id: Long, trigger: Long) = MirrorAlarm(timer_id = id, trigger_at_ms = trigger)

    // ---- A3: reconcile ----------------------------------------------------------

    @Test fun aCancelledOrFiredTimerLosesItsAlarm() {
        val r = reconcile(desired = listOf(alarm(1, t0)), current = listOf(alarm(1, t0), alarm(2, t0)))
        assertEquals(listOf(2L), r.toCancel)
        assertEquals(emptyList<MirrorAlarm>(), r.toSchedule)
    }

    @Test fun aNewTimerIsArmed() {
        val r = reconcile(desired = listOf(alarm(1, t0), alarm(3, t0 + min)), current = listOf(alarm(1, t0)))
        assertEquals(listOf(3L), r.toSchedule.map { it.timer_id })
        assertEquals(emptyList<Long>(), r.toCancel)
    }

    @Test fun aMovedTriggerIsReArmedButJitterIsNot() {
        val moved = reconcile(listOf(alarm(1, t0 + 1_500)), listOf(alarm(1, t0)))
        assertEquals(listOf(t0 + 1_500), moved.toSchedule.map { it.trigger_at_ms })
        val jitter = reconcile(listOf(alarm(1, t0 + 900)), listOf(alarm(1, t0)))
        assertEquals(emptyList<MirrorAlarm>(), jitter.toSchedule)
        assertEquals(emptyList<Long>(), jitter.toCancel)
    }

    @Test fun nothingArmedAndNothingWantedIsNothingToDo() {
        assertEquals(Reconcile(emptyList(), emptyList()), reconcile(emptyList(), emptyList()))
    }

    private fun timer(id: Long, expires: Long, reminder: Boolean = false) = AlertTimer(
        id = id, expires_at = iso(expires), created_at = iso(t0 - 10 * min),
        label = if (reminder) "call mom" else "pasta", message = if (reminder) "call mom" else null,
        room_id = "garage", is_reminder = reminder,
    )

    @Test fun pastDueAndAboutToFireTimersAreNotArmed() {
        val list = AlertTimerList(
            server_now = iso(t0),
            timers = listOf(timer(1, t0 - 5_000), timer(2, t0 + 1_000), timer(3, t0 + 1_001), timer(4, t0 + 10 * min)),
        )
        assertEquals(listOf(3L, 4L), desiredAlarms(list, receivedAtMs = t0).map { it.timer_id })
    }

    @Test fun bothKindsAreMirroredWithTheirWords() {
        val list = AlertTimerList(server_now = iso(t0), timers = listOf(timer(1, t0 + min), timer(2, t0 + 2 * min, reminder = true)))
        val d = desiredAlarms(list, t0)
        assertEquals(listOf("timer", "reminder"), d.map { it.kind })
        assertEquals("call mom", d[1].message)
        assertEquals("garage", d[1].room_id)
    }

    @Test fun theMirrorHoldsTheFiftySoonest() {
        val list = AlertTimerList(
            server_now = iso(t0),
            timers = (1..60).map { timer(it.toLong(), t0 + (61 - it) * min) },
        )
        val d = desiredAlarms(list, t0)
        assertEquals(MIRROR_MAX, d.size)
        assertEquals(60L, d.first().timer_id)
        assertEquals(11L, d.last().timer_id)
    }

    @Test fun aStoredMirrorRoundTripsAndACorruptOneIsEmpty() {
        val book = MirrorBook("abcd1234", listOf(MirrorAlarm(7, "reminder", "x", "y", "garage", null, null, 99, masked = true)))
        assertEquals(book, decodeMirror(encodeMirror(book)))
        assertEquals(MirrorBook(), decodeMirror("nope"))
        assertEquals(MirrorBook(), decodeMirror(null))
    }

    // ---- A4: the phone's clock is not the server's -------------------------------

    @Test fun aPhoneThreeMinutesSlowRingsOnTheServersTime() {
        // The server said 15:00:00 when this phone's clock read 14:57:00.
        val trigger = triggerAtMs(expiresAtMs = t0 + 10 * min, serverNowMs = t0, receivedAtMs = t0 - 3 * min)
        // 15:07 by the phone's clock is 15:10 by the server's.
        assertEquals(t0 + 7 * min, trigger)
    }

    @Test fun aPhoneThreeMinutesFastRingsOnTheServersTime() {
        val trigger = triggerAtMs(expiresAtMs = t0 + 10 * min, serverNowMs = t0, receivedAtMs = t0 + 3 * min)
        assertEquals(t0 + 13 * min, trigger)
    }

    @Test fun desiredAlarmsApplyTheOffset() {
        val list = AlertTimerList(server_now = iso(t0), timers = listOf(timer(1, t0 + 10 * min)))
        assertEquals(t0 + 7 * min, desiredAlarms(list, receivedAtMs = t0 - 3 * min).single().trigger_at_ms)
        // No server clock in the read: the phone's own is all there is.
        val noClock = list.copy(server_now = null)
        assertEquals(t0 + 10 * min, desiredAlarms(noClock, receivedAtMs = t0).single().trigger_at_ms)
    }

    // ---- A5: exact or inexact -------------------------------------------------------

    @Test fun exactAlarmsByApiLevel() {
        // 26-30: always exact.
        assertTrue(useExact(26, canScheduleExact = false))
        assertTrue(useExact(30, canScheduleExact = false))
        // 31-32: only with the SCHEDULE_EXACT_ALARM grant.
        assertTrue(useExact(31, canScheduleExact = true))
        assertFalse(useExact(31, canScheduleExact = false))
        assertTrue(useExact(32, canScheduleExact = true))
        assertFalse(useExact(32, canScheduleExact = false))
        // 33+: USE_EXACT_ALARM, and the platform is still asked.
        assertTrue(useExact(33, canScheduleExact = true))
        assertFalse(useExact(33, canScheduleExact = false))
        assertTrue(useExact(35, canScheduleExact = true))
        assertFalse(useExact(35, canScheduleExact = false))
    }
}
