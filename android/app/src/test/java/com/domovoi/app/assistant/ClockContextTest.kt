package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale

/**
 * The phone's clock as it is written (the phone's region and 12/24-hour
 * setting, English words), as the model is told it every turn, and how that
 * reaches the model without costing the kept conversation.
 */
class ClockContextTest {
    private val la: ZoneId = ZoneId.of("America/Los_Angeles")
    private val afternoon: ZonedDateTime = ZonedDateTime.of(2026, 10, 15, 15, 42, 0, 0, la)
    private val morning: ZonedDateTime = ZonedDateTime.of(2026, 10, 15, 9, 5, 0, 0, la)

    // ── Formatting ──────────────────────────────────────────────────────

    @Test fun twelveAndTwentyFourHours() {
        val us = ClockStyle(la, Locale.US, is24Hour = false)
        assertEquals("3:42 PM", us.time(afternoon))
        assertEquals("9:05 AM", us.time(morning))
        val us24 = us.copy(is24Hour = true)
        assertEquals("15:42", us24.time(afternoon))
        assertEquals("09:05", us24.time(morning))

        // A region that writes 24 hours, on a phone set to 12: the setting wins.
        val uk = ClockStyle(la, Locale.UK, is24Hour = true)
        assertEquals("15:42", uk.time(afternoon))
        val uk12 = uk.copy(is24Hour = false)
        assertTrue(uk12.time(afternoon), uk12.time(afternoon).matches(Regex("3:42 (PM|pm|p\\.m\\.)")))
    }

    @Test fun theDateFollowsTheRegion() {
        assertEquals("Thursday, October 15, 2026", ClockStyle(la, Locale.US, false).date(afternoon))
        val uk = ClockStyle(la, Locale.UK, true).date(afternoon)
        assertTrue(uk, uk.startsWith("Thursday") && uk.endsWith("15 October 2026"))
        assertEquals("Thursday", ClockStyle(la, Locale.UK, true).weekday(afternoon))
        assertEquals("October", ClockStyle(la, Locale.UK, true).month(afternoon))
    }

    /** The assistant speaks English: another language keeps its region's
     *  order and clock, with English words. */
    @Test fun anotherLanguageGetsEnglishWordsInItsRegionsOrder() {
        val de = ClockStyle(ZoneId.of("Europe/Berlin"), Locale.GERMANY, is24Hour = true)
        assertEquals("en", de.words.language)
        assertEquals("DE", de.words.country)
        val t = ZonedDateTime.of(2026, 10, 15, 15, 42, 0, 0, ZoneId.of("Europe/Berlin"))
        assertEquals("15:42", de.time(t))
        val date = de.date(t)
        assertTrue(date, date.contains("Thursday") && date.contains("15 October 2026"))
        assertFalse(date, date.contains("Donnerstag"))

        assertEquals(Locale.ENGLISH, ClockStyle(la, Locale.FRENCH, true).words)   // no region: plain English
        assertEquals(Locale.US, ClockStyle(la, Locale.US, true).words)
    }

    @Test fun noOddSpacesInWhatIsSpoken() {
        val all = listOf(Locale.US, Locale.UK, Locale.CANADA, Locale.forLanguageTag("en-AU"), Locale.forLanguageTag("en-IN"), Locale.GERMANY, Locale.JAPAN)
            .flatMap { l -> listOf(false, true).map { ClockStyle(la, l, it) } }
            .flatMap { listOf(it.time(afternoon), it.date(afternoon), it.zoneName(afternoon)) }
        all.forEach { assertFalse("\"$it\"", it.contains(' ') || it.contains(' ')) }
    }

    @Test fun utcOffsets() {
        assertEquals("UTC-07:00", ClockStyle.utcOffset(afternoon))
        assertEquals("UTC", ClockStyle.utcOffset(ZonedDateTime.of(2026, 1, 15, 12, 0, 0, 0, ZoneId.of("Europe/London"))))
        assertEquals("UTC+05:30", ClockStyle.utcOffset(ZonedDateTime.of(2026, 1, 15, 12, 0, 0, 0, ZoneId.of("Asia/Kolkata"))))
    }

    // ── What the model is told ──────────────────────────────────────────

    @Test fun theClockLineNamesTheDateTimeAndZone() {
        assertEquals(
            "The phone's clock says it is Thursday, October 15, 2026, 3:42 PM, Pacific Daylight Time " +
                "(America/Los_Angeles, UTC-07:00).",
            Persona.clockLine(afternoon, ClockStyle(la, Locale.US, is24Hour = false)),
        )
        val line = Persona.clockLine(afternoon, ClockStyle(la, Locale.US, is24Hour = true))
        assertTrue(line, line.contains(", 15:42, "))
    }

    @Test fun theClockLineIsReadAfreshEachTurn() {
        val style = ClockStyle(la, Locale.US, is24Hour = false)
        val first = Persona.clockLine(afternoon, style)
        val later = Persona.clockLine(afternoon.plusMinutes(1), style)
        val tomorrow = Persona.clockLine(afternoon.plusDays(1), style)
        assertTrue(later, later.contains("3:43 PM"))
        assertTrue(tomorrow, tomorrow.contains("Friday, October 16, 2026"))
        assertEquals(3, setOf(first, later, tomorrow).size)
    }

    /** A rebuilt conversation has the clock in its system instruction; a
     *  kept one gets a newer reading ahead of the question, never a changed
     *  system prompt (which would rebuild it every minute). */
    @Test fun theClockReachesTheModelWithoutBreakingTheKeptConversation() {
        val sys = Persona.SYSTEM_PROMPT
        val t1 = "The phone's clock says it is 3:42 PM."
        val t2 = "The phone's clock says it is 3:43 PM."

        assertEquals(ClockedTurn("$sys\n\n$t1", "Hi"), withClock(sys, "Hi", t1, told = null, rebuilt = true))
        assertEquals(ClockedTurn(sys, "Hi"), withClock(sys, "Hi", t1, told = t1, rebuilt = false))
        assertEquals(ClockedTurn(sys, "$t2\n\nHi"), withClock(sys, "Hi", t2, told = t1, rebuilt = false))
        assertEquals(ClockedTurn(sys, "$t2\n\nHi"), withClock(sys, "Hi", t2, told = null, rebuilt = false))
        assertEquals(ClockedTurn(sys, "Hi"), withClock(sys, "Hi", null, told = t1, rebuilt = false))
        assertEquals(ClockedTurn(sys, "Hi"), withClock(sys, "Hi", null, told = null, rebuilt = true))

        // The kept conversation is judged on the system prompt alone.
        val u1 = ChatTurn(true, "Hi")
        val a1 = ChatTurn(false, "Hello.")
        val u2 = ChatTurn(true, "What day is it tomorrow?")
        assertTrue(canContinue(sys, listOf(u1, a1), sys, listOf(u1, a1, u2)))
    }
}
