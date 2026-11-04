package com.domovoi.app.calendar

import com.domovoi.app.ui.screens.calendar.CalView
import com.domovoi.app.ui.screens.calendar.CalendarEvent
import com.domovoi.app.ui.screens.calendar.calendarInstant
import com.domovoi.app.ui.screens.calendar.draftOf
import com.domovoi.app.ui.screens.calendar.editDraftOf
import com.domovoi.app.ui.screens.calendar.visibleEvents
import com.domovoi.app.ui.screens.calendar.withReadableStart
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId

/**
 * What the calendar sends when an event is saved (D13), and what it does
 * with a `starts_at` it cannot read (D18).
 *
 * The save builds its body at CalendarScreen.kt's `save()`:
 * `put("starts_at", calendarInstant(d.start))`. A naive wall-clock string
 * went before, which the server read in ITS zone — right on a house whose
 * zone is the household's, and hours off whenever this phone's zone is not
 * the server's (ledger #27: travelling, or a server never given a zone).
 */
class CalendarInstantTest {

    private val ny = ZoneId.of("America/New_York")
    private val kolkata = ZoneId.of("Asia/Kolkata")
    private val afternoon = LocalDateTime.of(2026, 7, 10, 14, 30)

    @Test fun theSavedTimeIsAnAbsoluteInstantInUtc() {
        // 2:30pm in New York in July (EDT, UTC-4) is 18:30 UTC …
        assertEquals("2026-07-10T18:30:00Z", calendarInstant(afternoon, ny))
        // … and the same wall clock in Kolkata (UTC+5:30) is 09:00 UTC.
        assertEquals("2026-07-10T09:00:00Z", calendarInstant(afternoon, kolkata))
        // The naive string the app used to send, for contrast: it names no
        // instant at all, so two phones in different zones meant two times.
        assertNotEquals("2026-07-10T14:30:00", calendarInstant(afternoon, ny))
    }

    @Test fun theZoneOffsetIsTheOneInForceOnTheDay() {
        // January in New York is EST (UTC-5), not EDT: a fixed offset would
        // put this event an hour out.
        assertEquals(
            "2026-01-10T19:30:00Z",
            calendarInstant(LocalDateTime.of(2026, 1, 10, 14, 30), ny),
        )
    }

    @Test fun theShapeIsTheOneTheDashboardAndTheIPhoneSend() {
        // web/static/calendar.jsx:36 `new Date(s).toISOString()` and the
        // iPhone's `calendarInstant(_:)`: seconds, a literal Z, no offset
        // and no fraction. The route takes a datetime either way
        // (web/backend/api/calendar.py:37-72).
        val out = calendarInstant(LocalDateTime.of(2026, 12, 31, 23, 5, 9), ny)
        assertEquals("2027-01-01T04:05:09Z", out)
        assertTrue(out.matches(Regex("""\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z""")))
    }

    // ---- D18: a row whose start cannot be read -----------------------------

    private fun bad(id: Long = 1) = CalendarEvent(id = id, title = "when?", starts_at = "not a date")
    private fun good(id: Long = 2) = CalendarEvent(id = id, title = "dentist", starts_at = "2026-09-15T09:00:00")

    @Test fun anUnreadableStartIsNotDrawnAsHappeningNow() {
        // The bug: startLocal() fell back to LocalDateTime.now(), so the row
        // appeared on TODAY — in the day view, and in the month and week
        // grids around it — as if it were happening at this moment.
        val today = LocalDate.now()
        assertEquals(emptyList<Long>(), visibleEvents(CalView.Day, today, listOf(bad())).map { it.id })
        assertEquals(emptyList<Long>(), visibleEvents(CalView.Month, today, listOf(bad())).map { it.id })
        assertEquals(emptyList<Long>(), visibleEvents(CalView.Week, today, listOf(bad())).map { it.id })
    }

    @Test fun theScreenDropsTheRowBeforeItCountsOrDrawsAnything() {
        // The agenda is why the drop has to happen at the source: its filter
        // keeps anything that has not ended, and an unreadable row's end is
        // the far future, so the agenda alone would carry it forever.
        assertEquals(listOf(2L), listOf(bad(), good()).withReadableStart().map { it.id })
        assertEquals(
            listOf(2L),
            visibleEvents(CalView.Agenda, LocalDate.of(2026, 9, 1), listOf(bad(), good()).withReadableStart())
                .map { it.id },
        )
    }

    @Test fun theEditorRefusesToOpenForAnUnreadableStart() {
        // iOS's `CalendarDraft(_ entry:)` returns nil for the same row: the
        // editor opens nothing rather than a lie about when the event is.
        assertNull(editDraftOf(bad()))
        val d = editDraftOf(good())
        assertNotNull(d)
        assertEquals(draftOf(good()).start, d!!.start)
        assertEquals(2L, d.id)
    }
}
