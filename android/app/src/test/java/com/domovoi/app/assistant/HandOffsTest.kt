package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

/** The hand-offs' requests (N1): what each phrasing fills in, what is left
 *  to the model, and the cards' words. */
class HandOffsTest {
    private val la = ZoneId.of("America/Los_Angeles")
    private val now = ZonedDateTime.of(2026, 10, 21, 15, 42, 0, 0, la) // a Wednesday

    private fun match(text: String, last: String? = "The capital of France is Paris.") = HandOffs.match(text, last, now)

    @Test fun callsByNameOrNumber() {
        assertEquals(HandOff(HandOffKind.CALL, who = "Sam"), match("Call Sam"))
        assertEquals(HandOff(HandOffKind.CALL, who = "Mom"), match("call my Mom please"))
        assertEquals(HandOff(HandOffKind.CALL, who = "Sam"), match("Give Sam a call"))
        assertEquals(HandOff(HandOffKind.CALL, who = "Dr Lee"), match("can you phone Dr Lee"))
        assertEquals(HandOff(HandOffKind.CALL, number = "5550123"), match("dial 555 0123"))
        assertTrue(match("call Sam on his mobile")!!.who == "Sam")
    }

    @Test fun textsWithAndWithoutWords() {
        assertEquals(HandOff(HandOffKind.TEXT, who = "Sam"), match("Text Sam"))
        assertEquals(HandOff(HandOffKind.TEXT, who = "Sam", body = "I'm running late"), match("text Sam saying I'm running late"))
        assertEquals(HandOff(HandOffKind.TEXT, who = "Sam", body = "I'm running late"), match("Text Sam I'm running late"))
        assertEquals(HandOff(HandOffKind.TEXT, who = "Alex", body = "Dinner at 7?"), match("message Alex: dinner at 7?"))
        assertEquals(HandOff(HandOffKind.TEXT, who = "Sam"), match("send a text to Sam"))
    }

    @Test fun notAPersonIsNotACall() {
        listOf("call it a day", "call me later", "call back", "text me", "call someone", "call the police",
            "call mom and tell her I will be late tonight").forEach {
            assertNull(it, match(it))
        }
    }

    @Test fun emailDrafts() {
        assertEquals(HandOff(HandOffKind.EMAIL, who = "Sam", subject = "The trip"), match("email Sam about the trip"))
        assertEquals(HandOff(HandOffKind.EMAIL, email = "alex@example.org"), match("email alex@example.org"))
    }

    @Test fun directions() {
        assertEquals(HandOff(HandOffKind.DIRECTIONS, place = "Pike Place Market"), match("Directions to Pike Place Market"))
        assertEquals(HandOff(HandOffKind.DIRECTIONS, place = "the airport"), match("navigate to the airport"))
        assertEquals(HandOff(HandOffKind.DIRECTIONS, place = "Grandma's house"), match("take me to Grandma's house"))
        assertNull(match("how do I get to sleep faster"))
    }

    @Test fun calendarEventsGoToTheCalendarAppsEditor() {
        val e = match("add lunch with Sam to my calendar tomorrow at noon")!!
        assertEquals(HandOffKind.EVENT, e.kind)
        assertEquals("Lunch with Sam", e.title)
        assertEquals(now.plusDays(1).withHour(12).withMinute(0).withSecond(0).toInstant().toEpochMilli(), e.beginMs)
        assertEquals(e.beginMs!! + 3_600_000, e.endMs)
        val f = match("put dentist on my calendar on friday at 3")!!
        assertEquals("Dentist", f.title)
        assertEquals(now.plusDays(2).withHour(15).withMinute(0).withSecond(0).toInstant().toEpochMilli(), f.beginMs)
        val allDay = match("add Mom's birthday to my calendar on saturday")!!
        assertTrue(allDay.allDay)
        val inside = match("add lunch with Sam tomorrow at 1 pm to my calendar")!!
        assertEquals("Lunch with Sam", inside.title)
        assertEquals(now.plusDays(1).withHour(13).withMinute(0).withSecond(0).toInstant().toEpochMilli(), inside.beginMs)
        val noTime = match("add yoga class to my calendar")!!
        assertNull(noTime.beginMs)
    }

    @Test fun links() {
        assertEquals(HandOff(HandOffKind.LINK, url = "https://example.org"), match("open example.org"))
        assertEquals(HandOff(HandOffKind.LINK, url = "https://en.wikipedia.org/wiki/Domovoi"), match("go to https://en.wikipedia.org/wiki/Domovoi"))
        assertNull(match("open the door"))
        assertFalse(HandOffs.safeLink("intent://x#Intent;end"))
        assertFalse(HandOffs.safeLink("file:///sdcard/x"))
        assertFalse(HandOffs.safeLink("https://user@evil.example"))
        assertTrue(HandOffs.safeLink("https://example.org/a?b=c"))
    }

    @Test fun shareTheLastReply() {
        assertEquals(HandOff(HandOffKind.SHARE, body = "The capital of France is Paris."), match("share this"))
        assertNull("nothing to share yet", match("share that", last = null))
    }

    @Test fun questionsAreNotHandOffs() {
        listOf(
            "how do I call someone abroad",
            "what should I text my friend for her birthday",
            "what is the best way to the airport",
            "who called me",
            "tell me about calendars",
            "can you text",
        ).forEach { assertNull(it, match(it)) }
    }

    @Test fun cardsAskBeforeAnythingOpens() {
        assertEquals("Text Sam?", HandOffs.cardTitle(HandOff(HandOffKind.TEXT, who = "Sam")))
        assertEquals("Open Maps to Pike Place?", HandOffs.cardTitle(HandOff(HandOffKind.DIRECTIONS, place = "Pike Place")))
        assertEquals("choose a contact", HandOffs.buttonLabel(HandOff(HandOffKind.CALL, who = "Sam")))
        assertEquals("open the dialer", HandOffs.buttonLabel(HandOff(HandOffKind.CALL, name = "Sam Lee", number = "5550123")))
        assertTrue(HandOffs.replyText(HandOff(HandOffKind.CALL, name = "Sam Lee", number = "5550123"), null).contains("you press call"))
        assertTrue(HandOff(HandOffKind.TEXT, who = "Sam").needsContact)
        assertFalse(HandOff(HandOffKind.EMAIL, who = "Sam").needsContact)
    }

    /** Round 1 review: chit-chat the patterns caught. */
    @Test fun chitChatIsNotAHandOff() {
        listOf("Write to me in French", "Message from the kitchen: dinner is ready", "Ring a bell",
            "open invoice.zip", "open report.pdf", "open notes.txt", "open readme.md", "open holiday.mov")
            .forEach { assertNull(it, match(it)) }
        assertEquals(HandOff(HandOffKind.LINK, url = "https://www.example.zip"), match("open www.example.zip"))
        assertEquals(HandOff(HandOffKind.LINK, url = "https://files.example.org/a.zip"), match("open https://files.example.org/a.zip"))
    }

    @Test fun aMeetingWithOnlyAWhenIsCalledMeeting() {
        val e = match("Schedule a meeting for next tuesday")!!
        assertEquals("Meeting", e.title)
        assertTrue(e.allDay)
        assertEquals(now.plusDays(6).toLocalDate(), java.time.Instant.ofEpochMilli(e.beginMs!!).atZone(la).toLocalDate())
    }

    /** Google Messages reads a text's words from the link only. */
    @Test fun aTextsWordsAreInTheLink() {
        assertEquals("smsto:5550123?body=I%27m%20on%20my%20way%21", HandOffs.smsLink("5550123", "I'm on my way!"))
        assertEquals("smsto:%2B15550123", HandOffs.smsLink("+15550123", null))
    }

    @Test fun clockTimes() {
        assertEquals(15 to 0, HandOffs.clockTime("3"))
        assertEquals(9 to 0, HandOffs.clockTime("9"))
        assertEquals(15 to 30, HandOffs.clockTime("3:30 pm"))
        assertEquals(0 to 15, HandOffs.clockTime("12:15 am"))
        assertEquals(12 to 0, HandOffs.clockTime("noon"))
        assertNull(HandOffs.clockTime("soon"))
    }
}
