package com.domovoi.app.ui.components

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** Pure formatting helpers shared by every screen (web Format.js analogs). */
class FormatTest {

    private fun ago(seconds: Long): String = Instant.now().minusSeconds(seconds).toString()
    private fun ahead(seconds: Long): String = Instant.now().plusSeconds(seconds).toString()

    // ---- parseInstant -------------------------------------------------------

    @Test fun parseInstant_acceptsOffsetForm() {
        val t = parseInstant("2026-09-15T10:00:00+02:00")
        assertEquals(Instant.parse("2026-09-15T08:00:00Z"), t)
    }

    @Test fun parseInstant_acceptsZuluForm() {
        assertEquals(Instant.parse("2026-09-15T08:00:00Z"), parseInstant("2026-09-15T08:00:00Z"))
    }

    @Test fun parseInstant_treatsNaiveLocalAsSystemZone() {
        val t = parseInstant("2026-09-15T10:00:00")
        assertNotNull(t)
        val local = t!!.atZone(ZoneId.systemDefault()).toLocalDateTime()
        assertEquals("2026-09-15T10:00", local.format(DateTimeFormatter.ISO_LOCAL_DATE_TIME).take(16))
    }

    @Test fun parseInstant_nullBlankGarbage() {
        assertNull(parseInstant(null))
        assertNull(parseInstant(""))
        assertNull(parseInstant("   "))
        assertNull(parseInstant("yesterday-ish"))
    }

    // ---- relTime ------------------------------------------------------------

    @Test fun relTime_buckets() {
        assertEquals("—", relTime(null))
        assertEquals("just now", relTime(ago(10)))
        assertEquals("4m ago", relTime(ago(4 * 60)))
        assertEquals("2h ago", relTime(ago(2 * 3600)))
        assertEquals("3d ago", relTime(ago(3 * 86_400)))
    }

    @Test fun relTime_olderThanAMonthShowsDate() {
        val out = relTime(ago(40L * 86_400))
        // "Aug 6, 2026" style: month name, day, comma, year.
        assertTrue(out, Regex("^[A-Z][a-z]{2} \\d{1,2}, \\d{4}$").matches(out))
    }

    @Test fun relTime_futureUsesInPrefix() {
        assertEquals("in 30s", relTime(ahead(30)))
        assertEquals("in 5m", relTime(ahead(5 * 60)))
        assertEquals("in 3h", relTime(ahead(3 * 3600)))
        assertEquals("in 2d", relTime(ahead(2 * 86_400)))
    }

    // ---- fmtDur / fmtBigDur -------------------------------------------------

    @Test fun fmtDur_minutesAndHours() {
        assertEquals("0:00", fmtDur(0.0))
        assertEquals("3:04", fmtDur(184.0))
        assertEquals("1:02:33", fmtDur(3753.0))
        assertEquals("3:05", fmtDur(184.6)) // rounds, doesn't truncate
    }

    @Test fun fmtDur_rejectsNullNegativeAndNonFinite() {
        assertEquals("—", fmtDur(null))
        assertEquals("—", fmtDur(-1.0))
        assertEquals("—", fmtDur(Double.NaN))
        assertEquals("—", fmtDur(Double.POSITIVE_INFINITY))
    }

    @Test fun fmtBigDur_statsStyle() {
        assertEquals("—", fmtBigDur(null))
        assertEquals("12m", fmtBigDur(12 * 60.0))
        assertEquals("41h 12m", fmtBigDur(41 * 3600.0 + 12 * 60))
    }

    // ---- fmtBytes -----------------------------------------------------------

    @Test fun fmtBytes_units() {
        assertEquals("—", fmtBytes(null))
        assertEquals("0 B", fmtBytes(0))
        assertEquals("1023 B", fmtBytes(1023))
        assertEquals("1 KB", fmtBytes(1024))
        assertEquals("12.4 MB", fmtBytes((12.4 * 1024 * 1024).toLong()))
        assertEquals("2.00 GB", fmtBytes(2L * 1024 * 1024 * 1024))
    }

    // ---- fmtRemaining / isLive ---------------------------------------------

    @Test fun fmtRemaining_countdown() {
        assertEquals("—" to 0L, fmtRemaining(null))
        assertEquals("now" to 0L, fmtRemaining(ago(5)))
        val (label, secs) = fmtRemaining(ahead(125))
        assertTrue(secs in 123L..125L)
        assertTrue(label, label == "2:05" || label == "2:04" || label == "2:03")
    }

    @Test fun isLive_window() {
        assertTrue(isLive(ago(10)))
        assertTrue(isLive(ahead(10)))
        assertFalse(isLive(ago(600)))
        assertTrue(isLive(ago(600), withinSec = 700))
        assertFalse(isLive(null))
    }
}
