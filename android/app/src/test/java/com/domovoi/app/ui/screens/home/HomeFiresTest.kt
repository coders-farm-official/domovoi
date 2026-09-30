package com.domovoi.app.ui.screens.home

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import com.domovoi.app.ui.components.Tone
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.Instant

/**
 * A7: Home's "done · garage" lines come from the server's fire ledger when
 * the timers read carries one — a minute on the server's clock, newest first,
 * with where they were heard — and from the old vanished-row guess only when
 * the server has no ledger (`fires` absent or null).
 */
class HomeFiresTest {
    private val t0 = Instant.parse("2026-09-30T15:10:00Z").toEpochMilli()
    private fun iso(ms: Long) = Instant.ofEpochMilli(ms).toString()

    private fun fire(
        id: Long,
        firedAt: Long,
        room: String? = "garage",
        reminder: Boolean = false,
        heard: List<String> = listOf("garage"),
        outcomes: List<String> = listOf("spoken"),
        summary: String = "heard in garage",
    ) = HomeFire(
        id = id, timer_id = 100 + id, kind = if (reminder) "reminder" else "timer", is_reminder = reminder,
        label = if (reminder) "call mom" else "pasta", message = if (reminder) "call mom" else null,
        room_id = room, created_at = iso(firedAt - 600_000), due_at = iso(firedAt), fired_at = iso(firedAt),
        heard_in = heard, summary = summary,
        deliveries = outcomes.map { HomeFireDelivery("garage", it) },
    )

    @Test fun doneLinesComeFromFiresForAMinuteNewestFirst() {
        val list = HomeTimerList(iso(t0), emptyList(), listOf(fire(1, t0 - 50_000), fire(2, t0 - 10_000), fire(3, t0 - 61_000)))
        val view = homeTimerView(list, TimerBook(), nowMs = t0)
        assertEquals(listOf(102L, 101L), view.done.map { it.timer.id })
        assertEquals("heard in garage", view.done.first().summary)
        assertEquals("pasta", timerTitle(view.done.first().timer, shared = false))
        assertEquals("garage", view.done.first().timer.room_id)
        assertEquals(emptyList<DoneTimer>(), homeTimerView(list, TimerBook(), t0 + 60_000).done)
    }

    @Test fun theMinuteIsOnTheServersClock() {
        // This phone runs 3 minutes slow: the read said 15:10 at 15:07 here.
        val receivedAt = t0 - 180_000
        val offset = serverOffsetMs(iso(t0), receivedAt)
        val list = HomeTimerList(iso(t0), emptyList(), listOf(fire(1, t0 - 30_000)))
        assertEquals(1, homeTimerView(list, TimerBook(), receivedAt + offset).done.size)
        // By the phone's own clock the fire is in the future — and by the
        // server's, 40 s later, it is gone.
        assertEquals(0, homeTimerView(list, TimerBook(), receivedAt + 40_000 + offset).done.size)
    }

    @Test fun aRowThatVanishesWithNoFireDrawsNothing() {
        val book = TimerBook()
        val running = HomeTimer(7, expires_at = iso(t0), created_at = iso(t0 - 600_000), label = "pasta", room_id = "kitchen")
        book.observe(listOf(running), t0 - 5_000)
        val gone = HomeTimerList(iso(t0), emptyList(), fires = emptyList())
        book.observe(gone.timers, t0 + 500)
        assertEquals(emptyList<DoneTimer>(), homeTimerView(gone, book, t0 + 500).done)
    }

    @Test fun anOlderServerKeepsTheBooksGuess() {
        val book = TimerBook()
        val running = HomeTimer(7, expires_at = iso(t0), created_at = iso(t0 - 600_000), label = "pasta", room_id = "kitchen")
        book.observe(listOf(running), t0 - 5_000)
        val old = HomeTimerList(iso(t0), emptyList(), fires = null)
        book.observe(old.timers, t0 + 500)
        val done = homeTimerView(old, book, t0 + 500).done.single()
        assertEquals(7L, done.timer.id)
        assertNull(done.summary)
        assertEquals(Tone.Ok, done.tone)
    }

    @Test fun activeTimersAreTheSameEitherWay() {
        val running = HomeTimer(7, expires_at = iso(t0 + 60_000), created_at = iso(t0), label = "pasta")
        val withFires = HomeTimerList(iso(t0), listOf(running), fires = emptyList())
        val without = HomeTimerList(iso(t0), listOf(running), fires = null)
        assertEquals(listOf(running), homeTimerView(withFires, TimerBook(), t0).active)
        assertEquals(listOf(running), homeTimerView(without, TimerBook(), t0).active)
    }

    @Test fun theDotSaysHeardStillAnnouncingOrHeardNowhere() {
        assertEquals(Tone.Ok, fireTone(fire(1, t0)))
        assertEquals(Tone.Warn, fireTone(fire(1, t0, heard = emptyList(), outcomes = listOf("pending"))))
        assertEquals(Tone.Warn, fireTone(fire(1, t0, heard = emptyList(), outcomes = listOf("offline", "sending"))))
        assertEquals(Tone.Err, fireTone(fire(1, t0, heard = emptyList(), outcomes = listOf("offline"))))
        assertEquals(Tone.Err, fireTone(fire(1, t0, heard = emptyList(), outcomes = emptyList())))
    }

    @Test fun aSharedScreenNeverShowsAReminderDoneLinesWords() {
        val r = fireDoneLines(listOf(fire(1, t0 - 1_000, reminder = true)), t0).single()
        assertEquals("call mom", timerTitle(r.timer, shared = false))
        assertEquals("reminder · garage", timerTitle(r.timer, shared = true))
        // A read the server masked (no token) has no words to show anyway.
        val masked = fire(2, t0 - 1_000, reminder = true).copy(label = null, message = null, masked = true)
        assertEquals("reminder", timerTitle(fireDoneLines(listOf(masked), t0).single().timer, shared = false))
        // A roomless one says so.
        assertEquals("reminder · no room", timerTitle(fireAsTimer(fire(3, t0, room = null, reminder = true)), true))
    }

    @Test fun theTimersReadDecodesFiresAndTheirAbsence() {
        val withFires = DomovoiJson.parseToJsonElement(
            """{"server_now":"2026-09-30T15:10:05Z","timers":[],"fires":[{"id":42,"timer_id":17,"kind":"reminder",
               "is_reminder":true,"label":null,"message":null,"masked":true,"room_id":"garage",
               "created_at":"2026-09-30T15:00:00Z","due_at":"2026-09-30T15:10:00Z","fired_at":"2026-09-30T15:10:00.6Z",
               "settled_at":null,"acked_at":null,"acked_by":null,"heard_in":["garage"],"summary":"heard in garage",
               "deliveries":[{"room_id":"garage","is_origin":true,"outcome":"spoken","detail":null,"finished_at":null}]}]}""",
        ).decode<HomeTimerList>()
        val f = withFires.fires!!.single()
        assertEquals(17L, f.timer_id)
        assertEquals(listOf("spoken"), f.deliveries.map { it.outcome })
        assertEquals(true, f.masked)

        val old = DomovoiJson.parseToJsonElement("""{"server_now":"2026-09-30T15:10:05Z","timers":[]}""").decode<HomeTimerList>()
        assertNull(old.fires)
        val noLedger = DomovoiJson.parseToJsonElement("""{"server_now":null,"timers":[],"fires":null}""").decode<HomeTimerList>()
        assertNull(noLedger.fires)
    }
}
