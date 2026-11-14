package com.domovoi.app.calendarshare

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The body of `PUT /api/calendar/phones/{device_id}` from the phone's rows. */
class SharePayloadTest {
    private val now = 1_800_000_000_000L
    private val day = SharePayload.DAY_MS
    private val work = PhoneCalendar(12, "Work", "alex@example.com", 0xFF33B679.toInt())
    private val home = PhoneCalendar(3, "Home", null, null)

    private fun row(id: Long, cal: Long = 12, begin: Long = now + id * 3_600_000, end: Long? = begin + 3_600_000,
                    allDay: Boolean = false, title: String? = "Event $id", location: String? = null, status: Int? = 1) =
        InstanceRow(id, cal, begin, end, allDay, title, location, status)

    private fun cals(b: JsonObject): JsonArray = b["calendars"]!!.jsonArray
    private fun events(c: Any?): JsonArray = (c as JsonObject)["events"]!!.jsonArray

    @Test fun theWindowIsThirtyDaysBackAndOneEightyAhead() {
        val w = SharePayload.window(now)
        assertEquals(now - 30 * day, w.startMs)
        assertEquals(now + 180 * day, w.endMs)
    }

    @Test fun colourIntBecomesLowercaseHexWithoutAlpha() {
        assertEquals("#33b679", SharePayload.colorHex(0xFF33B679.toInt()))
        assertEquals("#000000", SharePayload.colorHex(0xFF000000.toInt()))
        assertEquals("#0000ff", SharePayload.colorHex(0x800000FF.toInt()))
        assertNull(SharePayload.colorHex(null))
    }

    @Test fun anEventAsTheServerTakesIt() {
        val r = row(345, begin = 1_700_003_600_000, end = 1_700_007_200_000, title = "Dentist", location = " Main St ")
        val b = SharePayload.build("Pixel 8", SharePayload.window(now), listOf(work), listOf(r), setOf(12L), now).body
        assertEquals("Pixel 8", b["phone_name"]!!.jsonPrimitive.content)
        val c = cals(b).single().jsonObject
        assertEquals("12", c["calendar_id"]!!.jsonPrimitive.content)
        assertEquals("Work", c["name"]!!.jsonPrimitive.content)
        assertEquals("alex@example.com", c["account_name"]!!.jsonPrimitive.content)
        assertEquals("#33b679", c["color"]!!.jsonPrimitive.content)
        val e = events(c).single().jsonObject
        assertEquals("345:1700003600000", e["uid"]!!.jsonPrimitive.content)
        assertEquals("Dentist", e["title"]!!.jsonPrimitive.content)
        assertEquals(1_700_003_600_000, e["starts_at_ms"]!!.jsonPrimitive.long)
        assertEquals(1_700_007_200_000, e["ends_at_ms"]!!.jsonPrimitive.long)
        assertFalse(e["all_day"]!!.jsonPrimitive.boolean)
        assertEquals("Main St", e["location"]!!.jsonPrimitive.content)
        // Minimum data: no description, guests or reminders, ever.
        assertEquals(setOf("uid", "title", "starts_at_ms", "ends_at_ms", "all_day", "location"), e.keys)
    }

    @Test fun nullsAndAllDayGoAsTheProviderHasThem() {
        val midnight = 1_700_006_400_000L    // a UTC midnight
        val r = row(7, begin = midnight, end = null, allDay = true, title = null, location = "  ")
        val b = SharePayload.build(null, SharePayload.window(now), listOf(home), listOf(r.copy(calendarId = 3)), setOf(3L), now).body
        assertFalse("no phone_name when unknown", "phone_name" in b)
        val c = cals(b).single().jsonObject
        assertEquals(JsonNull, c["account_name"])
        assertEquals(JsonNull, c["color"])
        val e = events(c).single().jsonObject
        assertEquals("", e["title"]!!.jsonPrimitive.content)
        assertTrue(e["all_day"]!!.jsonPrimitive.boolean)
        assertEquals(midnight, e["starts_at_ms"]!!.jsonPrimitive.long)     // unchanged
        assertEquals(JsonNull, e["ends_at_ms"])
        assertEquals(JsonNull, e["location"])
        assertEquals("7:$midnight", SharePayload.uid(r))
    }

    @Test fun cancelledOccurrencesAndUnpickedCalendarsAreNotSent() {
        val rows = listOf(row(1), row(2, status = STATUS_CANCELED), row(3, cal = 3), row(4, status = null))
        val out = SharePayload.build(null, SharePayload.window(now), listOf(work, home), rows, setOf(12L), now)
        val c = cals(out.body).single().jsonObject
        assertEquals("12", c["calendar_id"]!!.jsonPrimitive.content)
        assertEquals(listOf("1", "4"), events(c).map { it.jsonObject["uid"]!!.jsonPrimitive.content.substringBefore(":") })
        // A picked calendar no longer on the phone is just not listed (the
        // server deletes it).
        val gone = SharePayload.build(null, SharePayload.window(now), listOf(home), rows, setOf(12L, 3L), now)
        assertEquals(listOf("3"), cals(gone.body).map { it.jsonObject["calendar_id"]!!.jsonPrimitive.content })
    }

    @Test fun nothingPickedIsAnEmptyList() {
        val b = SharePayload.build(null, SharePayload.window(now), listOf(work), listOf(row(1)), emptySet(), now).body
        assertEquals(0, cals(b).size)
    }

    @Test fun textsAreClippedToTheCapsWithoutSplittingACharacter() {
        val long = "a".repeat(250)
        assertEquals(200, SharePayload.clip(long, 200)!!.length)
        // An emoji (two chars) straddling the cut is dropped whole.
        val straddle = "a".repeat(199) + "😀" + "b"
        assertEquals("a".repeat(199), SharePayload.clip(straddle, 200))
        assertNull(SharePayload.clip("   ", 200))
        val b = SharePayload.build(null, SharePayload.window(now), listOf(work), listOf(row(1, title = long, location = long)), setOf(12L), now).body
        val e = events(cals(b).single()).single().jsonObject
        assertEquals(200, e["title"]!!.jsonPrimitive.content.length)
        assertEquals(200, e["location"]!!.jsonPrimitive.content.length)
    }

    @Test fun overThePerCalendarCapTheFurthestPastGoesFirst() {
        val past = (1..10).map { row(it.toLong(), begin = now - it * day, end = now - it * day + 1000) }
        val ahead = (11..20).map { row(it.toLong(), begin = now + (it - 10) * day) }
        val kept = SharePayload.keep(past + ahead, 15, now)
        assertEquals(15, kept.size)
        assertTrue("every upcoming event kept", ahead.all { it in kept })
        assertTrue("the five most recent past kept", past.take(5).all { it in kept })
        assertEquals(kept.sortedBy { it.begin }, kept)
        // At or under the cap: all, in time order.
        assertEquals((past + ahead).sortedBy { it.begin }, SharePayload.keep(past + ahead, 20, now))
    }

    @Test fun capsHoldForCalendarsEventsAndTheWhole() {
        val many = (1L..30L).map { PhoneCalendar(it, "C$it") }
        val out = SharePayload.build(null, SharePayload.window(now), many, emptyList(), many.map { it.id }.toSet(), now)
        assertEquals(SharePayload.MAX_CALENDARS, out.calendars)
        assertEquals(5, out.droppedCalendars)

        val rows = (1L..(SharePayload.MAX_EVENTS_PER_CALENDAR + 10)).map { row(it, begin = now + it * 60_000) }
        val one = SharePayload.build(null, SharePayload.window(now), listOf(work), rows, setOf(12L), now)
        assertEquals(SharePayload.MAX_EVENTS_PER_CALENDAR, one.events)
        assertEquals(10, one.droppedEvents)

        val small = SharePayload.build(null, SharePayload.window(now), listOf(work, home),
            (1L..10L).map { row(it) } + (11L..20L).map { row(it, cal = 3) }, setOf(12L, 3L), now, maxTotal = 12)
        assertEquals(12, small.events)
        assertEquals(8, small.droppedEvents)
        assertEquals(12, cals(small.body).sumOf { events(it).size })
    }

    @Test fun theBodyIsKeptUnderTheSizeBudget() {
        val big = "x".repeat(200)
        val rows = (1L..200L).map { row(it, title = big, location = big) }
        val full = SharePayload.build(null, SharePayload.window(now), listOf(work), rows, setOf(12L), now)
        val budget = SharePayload.bytes(full.body) / 2
        val fit = SharePayload.buildWithinSize(null, SharePayload.window(now), listOf(work), rows, setOf(12L), now, budget)
        assertTrue(SharePayload.bytes(fit.body) <= budget)
        assertTrue(fit.events in 1 until 200)
        assertEquals(200 - fit.events, fit.droppedEvents)
    }
}
