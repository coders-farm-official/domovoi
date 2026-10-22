package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale

/**
 * Spoken clock times on the phone against the server's reading of the same
 * words (Fold check 2026-10-21: "what time is twelve to nine?" got 11:00 AM,
 * "twelve to night" got 11:00 PM).
 *
 * The table is the server's `domovoi/handlers/shared/clock_time.py`
 * (parse_when, then candidate_hours) on each phrase, printed by
 * functional-testing/fold-fixes/clock_compare.py: the hours it may be on a
 * 24-hour clock, and the minute.
 */
class ClockWordsTest {
    private val la: ZoneId = ZoneId.of("America/Los_Angeles")
    private val now: ZonedDateTime = ZonedDateTime.of(2026, 10, 21, 15, 42, 0, 0, la)
    private val us = ClockStyle(la, Locale.US, is24Hour = false)

    private val server: List<Triple<String, Set<Int>, Int>> = listOf(
        Triple("twelve to nine", setOf(8, 20), 48),
        Triple("quarter to seven", setOf(6, 18), 45),
        Triple("half past six", setOf(6, 18), 30),
        Triple("ten past six", setOf(6, 18), 10),
        Triple("20 minutes to 8", setOf(7, 19), 40),
        Triple("twenty five past four", setOf(4, 16), 25),
        Triple("five to twelve", setOf(11, 23), 55),
        Triple("a quarter past 3", setOf(3, 15), 15),
        Triple("quarter after nine", setOf(9, 21), 15),
        Triple("ten after five", setOf(5, 17), 10),
        Triple("twenty to one", setOf(0, 12), 40),
        Triple("ten before eight", setOf(7, 19), 50),
        Triple("half past twelve", setOf(0, 12), 30),
        Triple("one minute past ten", setOf(10, 22), 1),
        Triple("fifty five past eleven", setOf(11, 23), 55),
        Triple("three minutes to four", setOf(3, 15), 57),
        Triple("quarter to one", setOf(0, 12), 45),
        Triple("a quarter to 10 in the morning", setOf(9), 45),
        Triple("half past 7 at night", setOf(19), 30),
        Triple("five till six", setOf(5, 17), 55),
        Triple("twelve tonight", setOf(0), 0),
        Triple("12 at night", setOf(0), 0),
        Triple("1 at night", setOf(1), 0),
        Triple("11 at night", setOf(23), 0),
        Triple("seven thirty", setOf(7, 19), 30),
        Triple("6:45 pm", setOf(18), 45),
        Triple("7 o'clock", setOf(7, 19), 0),
        Triple("nine in the morning", setOf(9), 0),
        Triple("midnight", setOf(0), 0),
        Triple("noon", setOf(12), 0),
        Triple("eight pm", setOf(20), 0),
        Triple("quarter past twelve", setOf(0, 12), 15),
        Triple("twenty past 12", setOf(0, 12), 20),
        Triple("ten to one in the afternoon", setOf(12), 50),
    )

    /** The hours a phone reading may be on a 24-hour clock. */
    private fun hours(s: SpokenTime): Set<Int> = if (s.pm == null) setOf(s.hour % 12, s.hour % 12 + 12) else setOf(s.hour)

    @Test fun thePhoneReadsClockWordsAsTheServerDoes() {
        val wrong = server.mapNotNull { (said, hrs, min) ->
            val s = parseTime(said)
            if (s != null && hours(s) == hrs && s.minute == min) null else "\"$said\": phone $s, server $hrs :$min"
        }
        println("clock words: ${server.size - wrong.size}/${server.size} agree with clock_time.py")
        assertTrue(wrong.joinToString("\n"), wrong.isEmpty())
        assertEquals(34, server.size)
    }

    @Test fun whatTimeIsTwelveToNine() {
        assertEquals("Twelve to nine is 8:48.", ClockQuestions.reading("What time is twelve to nine?", us, now))
        assertEquals("Quarter past seven pm is 7:15 PM.", ClockQuestions.reading("what's quarter past seven pm", us, now))
        assertEquals("20 minutes to 8 is 7:40.", ClockQuestions.reading("What is 20 minutes to 8?", us, now))
    }

    /** "12 tonight" as speech to text writes it: midnight, never 11 PM. */
    @Test fun twelveToNightIsMidnight() {
        assertEquals("Twelve tonight is 12:00 AM, midnight.", ClockQuestions.reading("What time is twelve to night?", us, now))
        assertEquals(SpokenTime(0, 0, false), parseTime("twelve to night"))
        val alarm = (ActionPatterns.match("Wake me at twelve to night", now) as ActionAsk.Do).action
        assertEquals(0 to 0, alarm.hour to alarm.minute)
        val nine = (ActionPatterns.match("Set an alarm for twelve to nine", now) as ActionAsk.Do).action
        assertEquals(20 to 48, nine.hour to nine.minute)
    }

    /** Still the clock's own answers or the model's: no reading here. */
    @Test fun otherQuestionsAreNotReadings() {
        for (q in listOf("what time is it", "What time is it in Tokyo?", "what is the time", "what is ten past",
            "what is the capital of France", "what time is the game tonight", "what is twelve times nine")) {
            assertNull(q, ClockQuestions.reading(q, us, now))
        }
    }
}
