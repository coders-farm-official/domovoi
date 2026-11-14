package com.domovoi.app.ui.screens.calendar

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

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

    @Test fun anAllDayEventIsOnItsWrittenDate() {
        val e = CalendarEvent(1, "Holiday", "2026-11-26T00:00:00Z", "2026-11-27T00:00:00Z", all_day = true)
        assertEquals(LocalDate.of(2026, 11, 26), e.startLocal().toLocalDate())
        assertEquals(LocalDate.of(2026, 11, 27).atStartOfDay(), e.endLocal())
        assertEquals("all day", fmtRange(e))
        assertEquals(listOf(e), visibleEvents(CalView.Day, LocalDate.of(2026, 11, 26), listOf(e)))
    }
}
