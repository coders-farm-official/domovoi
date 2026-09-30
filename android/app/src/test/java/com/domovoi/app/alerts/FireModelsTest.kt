package com.domovoi.app.alerts

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * A1 and the decoding half of A8: what an alert says, what it never says
 * (a reminder's words on a shared or masked read, anything past the title on
 * the lock screen), and that every model tolerates a backend that sends less.
 */
class FireModelsTest {

    private fun fire(
        kind: String = "timer",
        label: String? = null,
        message: String? = null,
        masked: Boolean = false,
        room: String? = "garage",
        created: String? = "2026-09-30T15:00:00Z",
        due: String? = "2026-09-30T15:10:00Z",
        summary: String? = "heard in garage · still announcing",
    ) = TimerFire(
        id = 42, timer_id = 17, kind = kind, is_reminder = kind == "reminder",
        label = label, message = message, masked = masked, room_id = room,
        created_at = created, due_at = due, fired_at = "2026-09-30T15:10:00.611Z",
        summary = summary,
    )

    // ---- A1: titles, bodies, nouns -----------------------------------------

    @Test fun titlesSayTheKindAndTheRoomOnly() {
        assertEquals("Reminder · garage", fireTitle("reminder", "garage"))
        assertEquals("Timer done · garage", fireTitle("timer", "garage"))
        assertEquals("Timer done · no room", fireTitle("timer", null))
        assertEquals("Reminder · no room", fireTitle("reminder", ""))
        // Anything that isn't a reminder reads as a timer.
        assertEquals("Timer done · kitchen", fireTitle(null, "kitchen"))
    }

    @Test fun unlabelledTimersAreNamedByTheirLength() {
        assertEquals("10 min timer", timerNounFromSpan("2026-09-30T15:00:00Z", "2026-09-30T15:10:00Z"))
        assertEquals("45s timer", timerNounFromSpan("2026-09-30T15:00:00Z", "2026-09-30T15:00:45Z"))
        assertEquals("2 min timer", timerNounFromSpan("2026-09-30T15:00:00Z", "2026-09-30T15:01:30Z"))
        assertEquals("timer", timerNounFromSpan(null, "2026-09-30T15:00:45Z"))
        assertEquals("timer", timerNounFromSpan("2026-09-30T15:00:45Z", "2026-09-30T15:00:45Z"))
    }

    @Test fun aReminderShowsItsWordsOnlyToAPrivateUnmaskedPhone() {
        val r = fire(kind = "reminder", label = "call mom", message = "call mom")
        assertEquals("call mom", fireBody(r, shared = false))
        assertNull("shared screen", fireBody(r, shared = true))
        assertNull("masked by the server", fireBody(fire(kind = "reminder", masked = true), shared = false))
        // A reminder with no words at all still says what it is.
        assertEquals("reminder", fireBody(fire(kind = "reminder", message = ""), shared = false))
    }

    @Test fun aTimersLabelShowsEvenOnASharedScreen() {
        assertEquals("pasta", fireBody(fire(label = "pasta"), shared = true))
        assertEquals("pasta", fireBody(fire(label = "pasta"), shared = false))
        assertEquals("10 min timer", fireBody(fire(), shared = true))
    }

    @Test fun theKindRuleReadsEitherSpelling() {
        assertTrue(TimerFire(is_reminder = true).reminder)
        assertTrue(TimerFire(kind = "reminder").reminder)
        assertFalse(TimerFire(kind = "timer").reminder)
    }

    @Test fun aLivePostCarriesTheSummaryAndASharedScreenCarriesNothingPastTheTitle() {
        val r = fire(kind = "reminder", message = "call mom")
        val private = fireContent(r, shared = false, serverOffsetMs = 0)
        assertEquals(17L, private.timerId)
        assertEquals("Reminder · garage", private.title)
        assertEquals("call mom", private.text)
        assertEquals("heard in garage · still announcing", private.subText)
        assertEquals(Instant.parse("2026-09-30T15:10:00.611Z").toEpochMilli(), private.whenMs)
        // The lock screen's version is the title and nothing else.
        assertEquals("Reminder · garage", private.publicTitle)

        val shared = fireContent(r, shared = true, serverOffsetMs = 0)
        assertEquals("Reminder · garage", shared.title)
        assertNull(shared.text)
        assertNull(shared.subText)
    }

    @Test fun whenIsTheFireOnThisPhonesClock() {
        // The server runs 3 minutes ahead of this phone.
        val c = fireContent(fire(), shared = false, serverOffsetMs = 180_000)
        assertEquals(Instant.parse("2026-09-30T15:07:00.611Z").toEpochMilli(), c.whenMs)
    }

    @Test fun anAlarmPostSaysWhatTheMirrorHeldAndWhyItCouldNotConfirm() {
        val a = MirrorAlarm(
            timer_id = 9, kind = "reminder", label = "call mom", message = "call mom", room_id = "garage",
            created_at = "2026-09-30T15:00:00Z", expires_at = "2026-09-30T15:10:00Z", trigger_at_ms = 1234,
        )
        val c = alarmContent(a, shared = false, subText = SUB_UNCONFIRMED)
        assertEquals("Reminder · garage", c.title)
        assertEquals("call mom", c.text)
        assertEquals("couldn't reach Domovoi to confirm", c.subText)
        assertEquals(1234L, c.whenMs)
        val s = alarmContent(a, shared = true, subText = SUB_GOING_OFF)
        assertNull(s.text)
        assertNull(s.subText)
        val masked = alarmContent(a.copy(label = null, message = null, masked = true), shared = false, subText = SUB_GOING_OFF)
        assertNull(masked.text)
        assertEquals("going off now", masked.subText)
        val t = alarmContent(a.copy(kind = "timer", label = null, message = null), shared = false, subText = SUB_GOING_OFF)
        assertEquals("Timer done · garage", t.title)
        assertEquals("10 min timer", t.text)
    }

    // ---- server identity ----------------------------------------------------

    @Test fun serverKeyIsEightHexDigitsOfTheNormalisedUrl() {
        val k = serverKey("http://192.168.0.117:6369")
        assertTrue(k, Regex("[0-9a-f]{8}").matches(k))
        assertEquals(k, serverKey("http://192.168.0.117:6369/ "))
        assertNotEquals(k, serverKey("http://10.0.2.2:6390"))
        assertEquals("timer_fire:$k", alertTag(k))
    }

    // ---- A8: decoding ---------------------------------------------------------

    @Test fun aFireToleratesMissingFieldsAndIgnoresNewOnes() {
        val bare = DomovoiJson.parseToJsonElement("""{"id":3}""").decode<TimerFire>()
        assertEquals(3L, bare.id)
        assertEquals(0L, bare.timer_id)
        assertFalse(bare.reminder)
        assertEquals(emptyList<String>(), bare.heard_in)
        assertNull(bare.summary)

        val full = DomovoiJson.parseToJsonElement(
            """{"id":42,"timer_id":17,"kind":"reminder","is_reminder":true,"label":"call mom","message":"call mom",
               "masked":false,"room_id":"garage","created_at":"2026-09-30T15:00:00.123000Z",
               "due_at":"2026-09-30T15:10:00.123000Z","fired_at":"2026-09-30T15:10:00.611000Z",
               "settled_at":null,"acked_at":null,"acked_by":null,"heard_in":["garage"],
               "summary":"heard in garage · still announcing","future_field":{"x":1},
               "deliveries":[{"room_id":"garage","is_origin":true,"outcome":"spoken","detail":null,"finished_at":"2026-09-30T15:10:02.900000Z"},
                             {"room_id":"kitchen","is_origin":false,"outcome":"pending","detail":"capturing","finished_at":null}]}""",
        ).decode<TimerFire>()
        assertEquals(17L, full.timer_id)
        assertEquals(listOf("garage", "kitchen"), full.deliveries.map { it.room_id })
        assertEquals("capturing", full.deliveries[1].detail)
        assertEquals("Reminder · garage", fireTitle(kindOf(full.reminder), full.room_id))
    }

    @Test fun anOlderServersTimerListHasNoFiresAndANewOnesMayHaveNone() {
        val old = DomovoiJson.parseToJsonElement(
            """{"server_now":"2026-09-30T15:00:00Z","timers":[{"id":7,"expires_at":"2026-09-30T15:10:00Z"}]}""",
        ).decode<AlertTimerList>()
        assertNull(old.fires)
        assertEquals(7L, old.timers.single().id)
        assertFalse(old.timers.single().masked)

        val none = DomovoiJson.parseToJsonElement("""{"server_now":null,"timers":[],"fires":[]}""").decode<AlertTimerList>()
        assertEquals(emptyList<TimerFire>(), none.fires)

        val list = DomovoiJson.parseToJsonElement("""{"server_now":"2026-09-30T15:00:00Z","fires":[{"id":1}]}""")
            .decode<TimerFireList>()
        assertEquals(1, list.fires.size)
    }
}
