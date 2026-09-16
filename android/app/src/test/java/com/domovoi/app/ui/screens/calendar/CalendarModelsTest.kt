package com.domovoi.app.ui.screens.calendar

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.LocalDateTime

/**
 * Date math behind the calendar views. Event timestamps here are naive
 * local strings so the expected dates hold in any system zone.
 */
class CalendarModelsTest {

    private fun ev(id: Long, start: String, end: String? = null, title: String = "e$id") =
        CalendarEvent(id = id, title = title, starts_at = start, ends_at = end)

    // 2026-09-15 is a Tuesday.
    private val tue = LocalDate.of(2026, 9, 15)

    @Test fun startOfWeekSun_backsUpToSunday() {
        assertEquals(LocalDate.of(2026, 9, 13), startOfWeekSun(tue))
        assertEquals(LocalDate.of(2026, 9, 13), startOfWeekSun(LocalDate.of(2026, 9, 13)))
        assertEquals(LocalDate.of(2026, 9, 13), startOfWeekSun(LocalDate.of(2026, 9, 19)))
    }

    @Test fun monthGridStart_isSundayOnOrBeforeTheFirst() {
        // Sep 1 2026 is a Tuesday: grid starts Sun Aug 30.
        assertEquals(LocalDate.of(2026, 8, 30), monthGridStart(tue))
        // Nov 1 2026 is a Sunday: grid starts on the 1st itself.
        assertEquals(LocalDate.of(2026, 11, 1), monthGridStart(LocalDate.of(2026, 11, 20)))
    }

    @Test fun stepAnchor_perView() {
        assertEquals(LocalDate.of(2026, 10, 15), stepAnchor(CalView.Month, tue, 1))
        assertEquals(LocalDate.of(2026, 9, 22), stepAnchor(CalView.Week, tue, 1))
        assertEquals(LocalDate.of(2026, 9, 8), stepAnchor(CalView.Agenda, tue, -1))
        assertEquals(LocalDate.of(2026, 9, 16), stepAnchor(CalView.Day, tue, 1))
    }

    @Test fun visibleEvents_month_coversTheSixWeekGrid() {
        val events = listOf(
            ev(1, "2026-08-30T09:00:00"), // first grid cell
            ev(2, "2026-08-29T09:00:00"), // day before the grid
            ev(3, "2026-10-10T09:00:00"), // last grid cell (Aug 30 + 41)
            ev(4, "2026-10-11T09:00:00"), // first day after the grid
        )
        assertEquals(listOf(1L, 3L), visibleEvents(CalView.Month, tue, events).map { it.id })
    }

    @Test fun visibleEvents_week_and_day() {
        val events = listOf(
            ev(1, "2026-09-13T00:00:00"),
            ev(2, "2026-09-19T23:59:00"),
            ev(3, "2026-09-20T00:00:00"),
            ev(4, "2026-09-15T12:00:00"),
        )
        assertEquals(listOf(1L, 2L, 4L), visibleEvents(CalView.Week, tue, events).map { it.id })
        assertEquals(listOf(4L), visibleEvents(CalView.Day, tue, events).map { it.id })
    }

    @Test fun visibleEvents_agenda_dropsFinishedSortsAndCaps() {
        val past = ev(1, "2026-09-10T09:00:00", "2026-09-10T10:00:00")
        val stillRunning = ev(2, "2026-09-14T09:00:00", "2026-09-15T01:00:00")
        val later = (3L..40L).map { ev(it, "2026-09-%02dT09:00:00".format(15 + (it % 10).toInt())) }
        val out = visibleEvents(CalView.Agenda, tue, (later + stillRunning + past).shuffled())
        assertFalse(out.any { it.id == 1L })
        assertTrue(out.any { it.id == 2L })
        assertEquals(25, out.size)
        assertEquals(out.map { it.starts_at }, out.map { it.starts_at }.sorted())
    }

    @Test fun rangeLabel_weekSameMonthVsCrossMonth() {
        assertEquals("sep 13 – 19, 2026", rangeLabel(CalView.Week, tue))
        // Week of Sun Aug 30 to Sat Sep 5 spans two months.
        assertEquals("aug 30 – sep 5, 2026", rangeLabel(CalView.Week, LocalDate.of(2026, 9, 2)))
        assertEquals("september 2026", rangeLabel(CalView.Month, tue))
        assertEquals("tuesday, september 15", rangeLabel(CalView.Day, tue))
        assertEquals("from tue, sep 15", rangeLabel(CalView.Agenda, tue))
    }

    @Test fun clockAndHourLabels() {
        assertEquals("9:05am", fmtClock(LocalDateTime.of(2026, 9, 15, 9, 5)))
        assertEquals("12:00pm", fmtClock(LocalDateTime.of(2026, 9, 15, 12, 0)))
        assertEquals("12am", hourLabel(0))
        assertEquals("11am", hourLabel(11))
        assertEquals("12pm", hourLabel(12))
        assertEquals("1pm", hourLabel(13))
        assertEquals("11pm", hourLabel(23))
    }

    @Test fun fmtRange_usesStartWhenEndMissing() {
        val e = ev(1, "2026-09-15T09:00:00")
        assertEquals("9:00am – 9:00am", fmtRange(e))
    }

    @Test fun draftOf_fillsDefaults() {
        val d = draftOf(ev(7, "2026-09-15T09:00:00", title = "dentist"))
        assertEquals(7L, d.id)
        assertEquals("dentist", d.title)
        assertEquals(LocalDateTime.of(2026, 9, 15, 9, 0), d.start)
        assertEquals(LocalDateTime.of(2026, 9, 15, 10, 0), d.end) // +1h when ends_at absent
        assertEquals("", d.location)
        assertEquals("", d.description)
    }

    @Test fun blankDraft_futureDayStartsAtNine() {
        val d = blankDraft(LocalDate.of(2030, 1, 1))
        assertEquals(LocalDateTime.of(2030, 1, 1, 9, 0), d.start)
        assertEquals(LocalDateTime.of(2030, 1, 1, 10, 0), d.end)
    }

    @Test fun isGoogle_onlyForGoogleSource() {
        assertTrue(ev(1, "2026-09-15T09:00:00").copy(source = "google").isGoogle())
        assertFalse(ev(1, "2026-09-15T09:00:00").copy(source = "local").isGoogle())
        assertFalse(ev(1, "2026-09-15T09:00:00").isGoogle())
    }
}
