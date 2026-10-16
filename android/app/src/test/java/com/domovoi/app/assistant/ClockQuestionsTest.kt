package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale

/**
 * The questions the phone answers from its own clock, without the model:
 * the forms people say (as Whisper and Moonshine write them), and the near
 * misses that must still go to the model.
 */
class ClockQuestionsTest {
    private val la: ZoneId = ZoneId.of("America/Los_Angeles")
    private val now: ZonedDateTime = ZonedDateTime.of(2026, 10, 15, 15, 42, 0, 0, la)
    private val us = ClockStyle(la, Locale.US, is24Hour = false)

    private fun assertAsks(expected: ClockQuestion, vararg said: String) =
        said.forEach { assertEquals("\"$it\"", expected, ClockQuestions.match(it)) }

    @Test fun theTime() = assertAsks(
        ClockQuestion.TIME,
        "What time is it?",
        "what time is it",
        "What time is it.",
        "WHAT TIME IS IT",
        "What's the time?",
        "Whats the time",
        "what’s the time",
        "What is the time?",
        "what is the current time",
        "What time is it now?",
        "What time is it right now?",
        "what time is it please",
        "What time is it, Domovoi?",
        "Hey Domovoi, what time is it?",
        "Domovoi what's the time",
        "Um, what time is it?",
        "Excuse me, what time is it?",
        "Do you know what time it is?",
        "Can you tell me the time?",
        "Could you please tell me what time it is?",
        "Tell me the time.",
        "what hour is it",
    )

    @Test fun theDate() = assertAsks(
        ClockQuestion.DATE,
        "What's the date?",
        "what is the date",
        "What's the date today?",
        "What is today's date?",
        "whats todays date",
        "Today's date?",
        "What date is it?",
        "What's today?",
        "Can you tell me the date?",
        "Do you know what the date is?",
        "what is the current date",
    )

    @Test fun theDay() = assertAsks(
        ClockQuestion.DAY,
        "What day is it?",
        "what day is it today",
        "What day is today?",
        "What day of the week is it?",
        "Which day is it?",
        "What's the day today?",
        "Do you know what day it is?",
        "what weekday is it",
    )

    @Test fun theMonthTheYearAndBoth() {
        assertAsks(ClockQuestion.MONTH, "What month is it?", "what month are we in", "What's the current month?")
        assertAsks(ClockQuestion.YEAR, "What year is it?", "what year is it now", "What's the year?", "Do you know what year it is?")
        assertAsks(ClockQuestion.DATE_AND_TIME, "What's the date and time?", "What is the time and date?", "Tell me the date and time.")
    }

    /** Anything more than the clock's own question is the model's. */
    @Test fun nearMissesGoToTheModel() {
        listOf(
            "What time is it in Tokyo?",
            "what time is it in london right now",
            "What's the time in New York?",
            "What time is it there?",
            "How long until Friday?",
            "How many days until Christmas?",
            "What day is Christmas this year?",
            "What day is it tomorrow?",
            "What's the date tomorrow?",
            "What's tomorrow's date?",
            "What day was it yesterday?",
            "What's the date of Easter?",
            "What year did the war end?",
            "What time does the store close?",
            "What time is sunset?",
            "What is time?",
            "What's the time difference between London and Paris?",
            "What time zone am I in?",
            "Is it Friday?",
            "Set a timer for five minutes.",
            "What's today's weather?",
            "Do you know what time the game starts?",
            "time",
            "the time",
            "",
            "   ",
            "?",
            "what time is it ".repeat(10),
        ).forEach { assertNull("\"$it\"", ClockQuestions.match(it)) }
    }

    @Test fun theAnswers() {
        assertEquals("It's 3:42 PM.", ClockQuestions.answer(ClockQuestion.TIME, now, us))
        assertEquals("Today is Thursday, October 15, 2026.", ClockQuestions.answer(ClockQuestion.DATE, now, us))
        assertEquals("It's Thursday.", ClockQuestions.answer(ClockQuestion.DAY, now, us))
        assertEquals("It's October.", ClockQuestions.answer(ClockQuestion.MONTH, now, us))
        assertEquals("It's 2026.", ClockQuestions.answer(ClockQuestion.YEAR, now, us))
        assertEquals("It's 3:42 PM on Thursday, October 15, 2026.", ClockQuestions.answer(ClockQuestion.DATE_AND_TIME, now, us))
        assertEquals("It's 15:42.", ClockQuestions.answer(ClockQuestion.TIME, now, us.copy(is24Hour = true)))
    }
}
