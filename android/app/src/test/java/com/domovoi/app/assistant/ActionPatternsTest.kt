package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

/** Every pattern of [ActionPatterns], beyond the bench's phrases: what each
 *  reads, and what it must leave to the model. */
class ActionPatternsTest {
    private val la = ZoneId.of("America/Los_Angeles")
    private val afternoon = ZonedDateTime.of(2026, 10, 21, 15, 42, 0, 0, la)
    private val earlyMorning = ZonedDateTime.of(2026, 10, 21, 5, 10, 0, 0, la)

    private fun act(text: String, now: ZonedDateTime = afternoon): PhoneAction? =
        (ActionPatterns.match(text, now) as? ActionAsk.Do)?.action

    private fun kind(text: String) = act(text)?.kind

    @Test fun timers() {
        val cases = mapOf(
            "set a timer for 10 minutes" to (600 to null),
            "Timer 5 minutes" to (300 to null),
            "start a 3 minute egg timer" to (180 to "egg"),
            "set a timer for 2 and a half minutes" to (150 to null),
            "set a timer for one and a half hours" to (5400 to null),
            "set a tea timer for 4 minutes" to (240 to "tea"),
            "set a timer called bread for 40 minutes" to (2400 to "bread"),
            "set a timer for 25 mins" to (1500 to null),
            "make a timer for 1 hour 15 minutes" to (4500 to null),
            "countdown 90 seconds" to (90 to null),
            "set a timer for a couple of minutes" to (120 to null),
            "set a timer for three quarters of an hour" to (2700 to null),
            "okay domovoi set a timer for 7 minutes please" to (420 to null),
        )
        cases.forEach { (said, want) ->
            val a = act(said)
            assertEquals(said, ActionKind.SET_TIMER, a?.kind)
            assertEquals(said, want.first, a?.seconds)
            assertEquals(said, want.second, a?.label)
        }
    }

    @Test fun alarmsWithAndWithoutAmPm() {
        fun alarm(text: String, now: ZonedDateTime = afternoon) = act(text, now)!!.let { Triple(it.kind, it.hour, it.minute) }
        assertEquals(Triple(ActionKind.SET_ALARM, 7, 0), alarm("set an alarm for 7 a.m."))
        assertEquals(Triple(ActionKind.SET_ALARM, 19, 15), alarm("set an alarm for 7:15 pm"))
        // No am/pm: a wake-up is the morning; a plain alarm is the next to come.
        assertEquals(Triple(ActionKind.SET_ALARM, 6, 30), alarm("wake me up at 6:30"))
        assertEquals(Triple(ActionKind.SET_ALARM, 18, 30), alarm("set an alarm for 6:30"))
        assertEquals(Triple(ActionKind.SET_ALARM, 6, 30), alarm("set an alarm for 6:30", earlyMorning))
        assertEquals(Triple(ActionKind.SET_ALARM, 7, 0), alarm("set an alarm for 7 tomorrow"))
        assertEquals(Triple(ActionKind.SET_ALARM, 7, 0), alarm("set an alarm for tomorrow at 7"))
        assertEquals(Triple(ActionKind.SET_ALARM, 6, 45), alarm("set an alarm for a quarter to seven in the morning".replace("a quarter", "quarter")))
        assertEquals(Triple(ActionKind.SET_ALARM, 12, 0), alarm("set an alarm for noon"))
        assertEquals(Triple(ActionKind.SET_ALARM, 0, 0), alarm("set an alarm for midnight"))
        assertEquals(Triple(ActionKind.SET_ALARM, 21, 5), alarm("set an alarm for 21:05"))
        assertEquals(Triple(ActionKind.SET_ALARM, 7, 5), alarm("wake me at seven oh five"))
        assertEquals(Triple(ActionKind.SET_ALARM, 6, 40), alarm("wake me at twenty to seven"))
        assertEquals(Triple(ActionKind.SET_ALARM, 8, 10), alarm("wake me at 10 past 8"))
        assertEquals(Triple(ActionKind.SET_ALARM, 7, 0), alarm("wake me at 7 o'clock"))
        assertEquals(Triple(ActionKind.SET_ALARM, 23, 0), alarm("set an alarm for 11 tonight"))
    }

    @Test fun aVagueTimeIsAGuessTheCardAsksAbout() {
        val before = act("wake me a bit before seven tomorrow")!!
        assertEquals(6 to 45, before.hour to before.minute)
        assertTrue(before.approximate)
        assertTrue(before.tomorrow)
        val after = act("wake me up a little after 6")!!
        assertEquals(6 to 15, after.hour to after.minute)
        val around = act("set an alarm for around 7 am")!!
        assertEquals(7 to 0, around.hour to around.minute)
        assertTrue(around.approximate)
        assertEquals(false, act("wake me at 7")!!.approximate)
    }

    @Test fun repeatingAlarms() {
        assertEquals(listOf(2, 3, 4, 5, 6), act("set an alarm for 6:30 am every weekday")!!.days)
        assertEquals((1..7).toList(), act("wake me up at 7 every day")!!.days)
        assertEquals(listOf(2, 4), act("set an alarm for 8 am on mondays and wednesdays")!!.days)
        assertEquals(listOf(1, 7), act("wake me at 9 on weekends")!!.days)
        assertEquals(emptyList<Int>(), act("wake me at 9")!!.days)
    }

    @Test fun playbackSleepIsNeverAClockTimer() {
        mapOf(
            "set a sleep timer for 20 minutes" to 20,
            "sleep timer 45 minutes" to 45,
            "stop the music in an hour" to 60,
            "stop playing in 15 minutes" to 15,
            "pause the music after half an hour" to 30,
            "turn the music off after 45 minutes" to 45,
            "turn off the music in 10 minutes" to 10,
            "stop in 5 minutes" to 5,
            "stop the audiobook in 30 minutes" to 30,
            "set a sleep timer for 90 seconds" to 2,
        ).forEach { (said, min) ->
            val a = act(said)
            assertEquals(said, ActionKind.SLEEP, a?.kind)
            assertEquals(said, min, a?.minutes)
        }
        assertEquals(ActionKind.CANCEL_SLEEP, kind("cancel the sleep timer"))
        assertEquals(ActionKind.CANCEL_SLEEP, kind("turn off the sleep timer"))
    }

    @Test fun playerControls() {
        mapOf(
            ActionKind.PAUSE to listOf("pause", "Pause it.", "pause the song", "hold on, pause for a sec", "pause the podcast"),
            ActionKind.RESUME to listOf("resume", "unpause", "keep playing", "carry on", "play", "continue playing", "resume the music"),
            ActionKind.NEXT to listOf("next", "skip", "skip it", "next song please", "skip ahead", "play the next song", "next chapter"),
            ActionKind.PREVIOUS to listOf("go back", "back", "previous track", "go back a song", "play the last song again", "skip back"),
            ActionKind.STOP to listOf("stop", "stop the music", "turn off the music", "stop playing music", "turn the music off"),
        ).forEach { (k, phrases) -> phrases.forEach { assertEquals(it, k, kind(it)) } }
    }

    @Test fun showTheClockApp() {
        listOf("show my timers", "open my timers", "show me the timers").forEach { assertEquals(it, ActionKind.SHOW_TIMERS, kind(it)) }
        listOf("show my alarms", "open the clock app", "show me my alarms").forEach { assertEquals(it, ActionKind.SHOW_ALARMS, kind(it)) }
        // A question about the alarms stays the phone's read.
        assertNull(act("what alarms do i have"))
        assertEquals(PhoneQuestion.NEXT_ALARM, PhoneQuestions.match("what alarms do i have"))
    }

    @Test fun musicAndWhatToLookFor() {
        mapOf(
            "play some jazz" to "jazz",
            "play songs by velvet kites" to "velvet kites",
            "put on the album blue train" to "blue train",
            "play music by the beatles" to "beatles",
            "play my chill playlist" to "chill",
            "listen to some lo-fi" to "lo-fi",
            "i want to listen to glass harbor" to "glass harbor",
            "play anything" to "",
            "play my music" to "",
            "queue up some bach" to "bach",
        ).forEach { (said, q) ->
            val a = act(said)
            assertEquals(said, ActionKind.PLAY_MUSIC, a?.kind)
            assertEquals(said, q, a?.query)
            assertEquals(said, false, a?.shuffle)
        }
        listOf("shuffle", "shuffle my music", "shuffle all my songs", "play random music").forEach {
            val a = act(it)!!
            assertEquals(it, ActionKind.PLAY_MUSIC, a.kind)
            assertEquals(it, "", a.query)
            assertTrue(it, a.shuffle)
        }
        assertEquals("velvet kites", act("shuffle velvet kites")!!.query)
        assertTrue(act("shuffle velvet kites")!!.shuffle)
    }

    @Test fun booksAndVideos() {
        assertEquals(PhoneAction(ActionKind.PLAY_BOOK), act("continue my audiobook"))
        assertEquals(PhoneAction(ActionKind.PLAY_BOOK), act("back to my book"))
        assertEquals("dune", act("keep listening to Dune")!!.query)
        assertEquals("the hobbit".removePrefix("the "), act("play the hobbit audiobook")!!.query)
        assertEquals(ActionKind.RESUME, kind("continue playing the music"))
        val v = act("play the video glacier trip")!!
        assertEquals(ActionKind.PLAY_VIDEO to "glacier trip", v.kind to v.query)
        assertEquals("birthday", act("watch the birthday video")!!.query)
        assertTrue(ActionKind.PLAY_VIDEO.asksFirst)
    }

    @Test fun houseActionsAreNotThisRound() {
        listOf(
            "play jazz in the kitchen", "pause the music in the living room", "set a timer for 10 minutes in the kitchen",
            "play music everywhere", "set a server timer for 5 minutes", "cast this to the office", "announce dinner is ready",
            "stop the music in all rooms",
        ).forEach { assertEquals(it, ActionAsk.House, ActionPatterns.match(it, afternoon)) }
        // House timers too (owner, 2026-10-21: phone chats are phone-only).
        listOf("cancel the kitchen timer", "stop the office alarm", "how long is left on the kitchen timer",
            "delete the garage timer").forEach { assertEquals(it, ActionAsk.House, ActionPatterns.match(it, afternoon)) }
        // ...not the phone's own playback sleep timer.
        assertEquals(ActionKind.CANCEL_SLEEP, kind("cancel the sleep timer"))
        // ...but a time or a duration isn't a room.
        assertEquals(ActionKind.SLEEP, kind("stop the music in an hour"))
        assertEquals(ActionKind.SET_ALARM, kind("set an alarm for 7 in the morning"))
        assertEquals(ActionKind.PLAY_MUSIC, kind("play the end"))
    }

    @Test fun undo() {
        listOf("undo", "Undo that.", "take that back", "put it back").forEach { assertEquals(it, ActionAsk.Undo, ActionPatterns.match(it, afternoon)) }
    }

    @Test fun nearMissesAndChatAreLeftAlone() {
        listOf(
            "how do i set a timer", "what is a timer", "timers are useful in the kitchen", "i set an alarm yesterday",
            "can you stop talking", "stop it with the jokes please, tell me about rome", "play is good for children",
            "let's play chess", "play along with me", "play nice", "who played james bond", "listen to me", "put on a coat",
            "put on some weight", "skip the intro and tell me", "next tuesday is my birthday", "go back to bed",
            "should i wake up at 6", "is 10 minutes enough to boil an egg", "the music was great", "pause for thought",
            "resume of a software engineer", "previous experience matters", "i stopped smoking", "how long is a movie",
            "alarm bells are ringing", "a timer for what", "my book club meets on friday", "what video games do you like",
            "watch out", "continue the story", "keep going with the story", "tell me how to shuffle cards",
            "can you play a song for me on the guitar", "show me how to do it", "open the door",
        ).forEach { assertNull(it, ActionPatterns.match(it, afternoon)) }
    }

    @Test fun numbersDurationsAndTimes() {
        assertEquals(45, parseNumber("forty-five"))
        assertEquals(25, parseNumber("twenty five"))
        assertNull(parseNumber("lots"))
        assertEquals(5400, parseDurationSeconds("an hour and a half"))
        assertEquals(5400, parseDurationSeconds("1 hour and 30 minutes"))
        assertEquals(900, parseDurationSeconds("a quarter of an hour"))
        assertNull(parseDurationSeconds("the kitchen"))
        assertEquals(SpokenTime(6, 45, null), parseTime("quarter to seven"))
        assertEquals(SpokenTime(19, 30, true), parseTime("7:30 pm"))
        assertEquals(SpokenTime(8, 0, false), parseTime("8 in the morning"))
        assertNull(parseTime("lunchtime"))
    }

    @Test fun longTextIsNeverAnAction() {
        assertNull(ActionPatterns.match("play " + "very ".repeat(40) + "long", afternoon))
    }
}
