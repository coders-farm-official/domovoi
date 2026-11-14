package com.domovoi.app.ui.screens.calendar

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId
import java.util.TimeZone

/** 1.3.0: events from linked and phone calendars, read-only. */
class CalendarImportedTest {

    @Test fun theNewFieldsDecodeAndOldRowsStillDo() {
        val e = DomovoiJson.parseToJsonElement(
            """{"id":9,"title":"Dentist","starts_at":"2026-11-20T15:00:00Z","source":"phone","all_day":false,
               "calendar_id":4,"calendar_name":"Work","calendar_color":"#33b679","calendar_kind":"phone",
               "owner_name":"Pixel 8","read_only":true}""",
        ).decode<CalendarEvent>()
        assertTrue(e.read_only)
        assertEquals("Work", e.calendar_name)
        assertEquals(4L, e.calendar_id)
        val old = DomovoiJson.parseToJsonElement("""{"id":1,"title":"x","starts_at":"2026-11-20T15:00:00Z"}""").decode<CalendarEvent>()
        assertFalse(old.read_only)
        assertFalse(old.isImported())
        assertNull(old.readOnlyNote())
        assertEquals("household", old.sourceLabel())
    }

    @Test fun theNoteSaysWhereToChangeIt() {
        val phone = CalendarEvent(1, "x", "2026-11-20T15:00:00Z", calendar_name = "Work", calendar_kind = "phone",
            owner_name = "Pixel 8", read_only = true, source = "phone")
        assertEquals("from Work on Pixel 8 — change it on that phone", phone.readOnlyNote())
        assertEquals("from Work — change it on that phone", phone.copy(owner_name = null).readOnlyNote())
        val link = CalendarEvent(2, "x", "2026-11-20T15:00:00Z", calendar_name = "School", calendar_kind = "link",
            read_only = true, source = "link")
        assertEquals("from School — change it there", link.readOnlyNote())
        assertEquals("from another calendar — change it there", link.copy(calendar_name = null).readOnlyNote())
        assertTrue(link.isImported())
        assertEquals("School", link.sourceLabel())
        assertEquals("linked calendar", link.copy(calendar_name = null).sourceLabel())
    }

    @Test fun theDotColourIsReadOrIgnored() {
        assertEquals(0xFF33B679L, parseHexColor("#33b679"))
        assertEquals(0xFF33B679L, parseHexColor("33B679"))
        assertNull(parseHexColor("#33b6"))
        assertNull(parseHexColor("red"))
        assertNull(parseHexColor(null))
    }

    /** The server's real shape: midnight in the HOUSEHOLD's offset
     *  (-05:00), read on a phone in another zone (Honolulu, -10:00), where
     *  that instant is 7pm the day BEFORE. Read by its written date. */
    @Test fun anAllDayEventIsOnItsWrittenDate() {
        val saved = TimeZone.getDefault()
        TimeZone.setDefault(TimeZone.getTimeZone("Pacific/Honolulu"))
        try {
            assertEquals(ZoneId.of("Pacific/Honolulu"), ZoneId.systemDefault())
            val e = DomovoiJson.parseToJsonElement(
                """{"id":1,"title":"Holiday","starts_at":"2026-11-26T00:00:00-05:00",
                   "ends_at":"2026-11-27T00:00:00-05:00","all_day":true,"calendar_kind":"phone","read_only":true}""",
            ).decode<CalendarEvent>()
            // The instant itself is the 25th here: the reading must not use it.
            assertEquals(LocalDate.of(2026, 11, 25), parseLocal(e.starts_at)!!.toLocalDate())
            assertEquals(LocalDate.of(2026, 11, 26), e.startLocal().toLocalDate())
            assertEquals(LocalDate.of(2026, 11, 27).atStartOfDay(), e.endLocal())
            assertEquals("all day", fmtRange(e))
            assertEquals(listOf(e), visibleEvents(CalView.Day, LocalDate.of(2026, 11, 26), listOf(e)))
            assertEquals(emptyList<CalendarEvent>(), visibleEvents(CalView.Day, LocalDate.of(2026, 11, 25), listOf(e)))
        } finally {
            TimeZone.setDefault(saved)
        }
    }

    /** Review: the edit/delete hiding, pure. */
    @Test fun editAndDeleteAreOfferedOnlyWhereTheyCanWork() {
        val own = CalendarEvent(1, "Dinner", "2026-11-20T23:00:00Z")
        assertTrue(canEdit(own))
        assertTrue(canDelete(own))
        val phone = own.copy(calendar_kind = "phone", source = "phone", read_only = true)
        assertFalse("a phone calendar's copy is changed on that phone", canEdit(phone))
        assertFalse(canDelete(phone))
        val link = own.copy(calendar_kind = "link", source = "link", read_only = true)
        assertFalse(canEdit(link))
        assertFalse(canDelete(link))
        val unreadable = own.copy(starts_at = "not a time")
        assertFalse("no honest start to edit from (D18)", canEdit(unreadable))
        assertTrue("but it can still be deleted", canDelete(unreadable))
    }

    /** Review HIGH 1: the six weeks of the month grid, never the whole
     *  list (the server's default is its 500 OLDEST). */
    @Test fun theCalendarAsksForTheSixWeeksAroundTheMonth() {
        val chicago = ZoneId.of("America/Chicago")
        // November 2026: the grid starts Sunday 1 Nov, ends before Sunday 13 Dec.
        assertEquals(
            "/api/calendar/events?start=2026-11-01T05%3A00%3A00Z&end=2026-12-13T06%3A00%3A00Z&limit=2000",
            calendarRangePath(LocalDate.of(2026, 11, 14), chicago),
        )
        // Any day of the month asks the same; the next month asks again.
        assertEquals(calendarRangePath(LocalDate.of(2026, 11, 30), chicago), calendarRangePath(LocalDate.of(2026, 11, 1), chicago))
        assertTrue(calendarRangePath(LocalDate.of(2026, 12, 1), chicago).startsWith("/api/calendar/events?start=2026-11-29T06"))
        // Every view's window falls inside what was read.
        val anchor = LocalDate.of(2026, 11, 14)
        val gs = monthGridStart(anchor)
        assertTrue(!startOfWeekSun(anchor).isBefore(gs) && startOfWeekSun(anchor).plusDays(7) <= gs.plusDays(42))
    }

    /** Server V057: a busy event and one held by several calendars, as the
     *  server writes them (docs/API_REFERENCE.md §3.8). */
    @Test fun busyAndAlsoInDecodeAndShowQuietly() {
        val list = DomovoiJson.parseToJsonElement(
            """[{"id":7,"title":"Busy","starts_at":"2026-11-20T15:00:00Z","ends_at":"2026-11-20T16:00:00Z",
                 "location":null,"source":"phone","all_day":false,"calendar_id":4,"calendar_name":"Work",
                 "calendar_color":"#33b679","calendar_kind":"phone","owner_name":"Pixel 8","read_only":true,
                 "busy":true,"also_in":[]},
                {"id":8,"title":"Swim","starts_at":"2026-11-21T15:00:00Z","source":"phone","all_day":false,
                 "calendar_name":"Family","calendar_kind":"phone","read_only":true,"busy":false,
                 "also_in":["Family · Galaxy","School"]}]""",
        ).decode<List<CalendarEvent>>()
        assertTrue(list[0].busy)
        assertEquals("Busy", list[0].displayTitle())
        assertEquals("Busy", list[0].copy(title = "anything").displayTitle())
        assertNull(list[0].alsoInLine())
        assertEquals(listOf("Family · Galaxy", "School"), list[1].also_in)
        assertEquals("also in Family · Galaxy, School", list[1].alsoInLine())
        assertEquals("Swim", list[1].displayTitle())
        // An older server sends neither.
        val old = DomovoiJson.parseToJsonElement("""{"id":1,"title":"x","starts_at":"2026-11-20T15:00:00Z"}""").decode<CalendarEvent>()
        assertFalse(old.busy)
        assertNull(old.alsoInLine())
    }
}
