package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** N1's prompt facts: what the line says, that it stays small, and how a
 *  kept conversation is told only what changed. */
class PhoneFactsTest {
    private val playing = NowPlayingFact("Glass Harbor", "Velvet Kites", positionSec = 72.0, durationSec = 238.0)
    private val full = PromptFacts(
        playing = playing,
        sleepRemainingSec = 20 * 60,
        server = ServerLink.CONNECTED,
        rooms = listOf("kitchen", "office", "garage"),
        listener = "Kamron Kennedy",
    )

    @Test fun theLineSaysEachFact() {
        assertEquals(
            "Phone facts: playing \"Glass Harbor\" by Velvet Kites on this phone, sleep timer 20 minutes left. " +
                "Connected to the home server, rooms: kitchen, office, garage. The person talking is Kamron (say the name only if asked).",
            PhoneFacts.line(full),
        )
    }

    @Test fun nothingToSayIsNoLine() {
        assertNull(PhoneFacts.line(PromptFacts.NONE))
    }

    /** The plan's budget: under about 60 tokens in all. English runs about
     *  0.75 words a token; a full line with long names stays well inside. */
    @Test fun theLineStaysSmall() {
        val house = full.copy(
            playing = playing.copy(title = "Somewhere Over the Rainbow / What a Wonderful World", artist = "Israel Kamakawiwo'ole"),
            rooms = listOf("kitchen", "living room", "office", "garage", "bedroom", "kids room", "porch", "basement"),
            listener = "Bartholomew Fitzgerald",
        )
        val line = PhoneFacts.line(house)!!
        val words = line.split(Regex("\\s+")).size
        assertTrue("$words words: $line", words <= 48)
        assertTrue(line, line.contains("and 4 more"))
        // Even absurd names are cut: a title, artist or room is 40
        // characters at most, and four rooms are named.
        val absurd = house.copy(
            playing = playing.copy(title = "A".repeat(500), artist = "B".repeat(500), room = "C".repeat(500)),
            rooms = (1..50).map { "R".repeat(100) },
        )
        assertTrue(PhoneFacts.line(absurd)!!.length < 520)
    }

    /** The position changes every second: kept out so a kept conversation
     *  isn't told the line again every turn. */
    @Test fun thePositionStaysOutOfThePrompt() {
        val a = PhoneFacts.line(full.copy(playing = playing.copy(positionSec = 10.0)))
        val b = PhoneFacts.line(full.copy(playing = playing.copy(positionSec = 70.0)))
        assertEquals(a, b)
        assertFalse(a!!.contains("1:12"))
    }

    @Test fun pausedInARoomAndNotConnected() {
        val line = PhoneFacts.line(
            PromptFacts(playing = NowPlayingFact("Song", null, paused = true, room = "kitchen"), server = ServerLink.NOT_CONNECTED,
                rooms = listOf("kitchen")),
        )!!
        assertEquals("Phone facts: paused: \"Song\" in the kitchen. Not connected to the home server.", line)
    }

    @Test fun noServerSetUpSaysNothingAboutOne() {
        assertEquals("The person talking is Sam (say the name only if asked).", PhoneFacts.line(PromptFacts(listener = "  Sam  ")))
    }

    @Test fun connectedWithNoRoomsKnown() {
        assertEquals("Phone facts: connected to the home server.", PhoneFacts.line(PromptFacts(server = ServerLink.CONNECTED)))
    }

    @Test fun minutesAndClock() {
        assertEquals("under a minute", PhoneFacts.minutes(30))
        assertEquals("1 minute", PhoneFacts.minutes(60))
        assertEquals("2 minutes", PhoneFacts.minutes(61))
        assertEquals("1:12", PhoneFacts.clock(72.4))
        assertEquals("1:02:05", PhoneFacts.clock(3725.0))
    }

    // ── How the lines reach the model ───────────────────────────────────

    private val clock = ContextLine(ContextLine.CLOCK, "The phone's clock says it is 3:42 PM.")
    private val facts = ContextLine(ContextLine.FACTS, "Phone facts: playing \"Song\" on this phone.")

    @Test fun aBuiltConversationHasEveryLineInItsSystemInstruction() {
        val t = withContextLines("SYS", "hi", listOf(clock, facts), emptyMap(), rebuilt = true)
        assertEquals("SYS\n\n${clock.text}\n${facts.text}", t.system)
        assertEquals("hi", t.question)
        assertEquals(mapOf(ContextLine.CLOCK to clock.text, ContextLine.FACTS to facts.text), t.told)
    }

    @Test fun aKeptConversationIsToldOnlyWhatChanged() {
        val told = mapOf(ContextLine.CLOCK to "The phone's clock says it is 3:41 PM.", ContextLine.FACTS to facts.text)
        val t = withContextLines("SYS", "hi", listOf(clock, facts), told, rebuilt = false)
        assertEquals("SYS", t.system)
        assertEquals("${clock.text}\n\nhi", t.question)
        val same = withContextLines("SYS", "again", listOf(clock, facts), t.told, rebuilt = false)
        assertEquals("again", same.question)
    }

    @Test fun aNewFactsLineIsToldOnTheTurnItAppears() {
        val told = mapOf(ContextLine.CLOCK to clock.text)
        val t = withContextLines("SYS", "what now", listOf(clock, facts), told, rebuilt = false)
        assertEquals("${facts.text}\n\nwhat now", t.question)
    }

    @Test fun oneClockLineBehavesAsWithClockDid() {
        val old = withClock("SYS", "q", clock.text, told = null, rebuilt = true)
        val new = withContextLines("SYS", "q", listOf(clock), emptyMap(), rebuilt = true)
        assertEquals(old.system, new.system)
        assertEquals(old.question, new.question)
    }

    @Test fun thePersonaSaysUseTheFactsOnlyWhenAsked() {
        val p = Persona.SYSTEM_PROMPT
        assertTrue(p.contains(Persona.FACTS_SENTENCE))
        assertTrue(p.contains("never mention them otherwise"))
    }
}
