package com.domovoi.app.ui.screens.satellites

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** A room's history records every voice turn since server V043
 *  (2026-11-09): pending ones say they are waiting, failed ones say there
 *  was no answer and why, and a request speech recognition never made out
 *  says so with its recording's length. An older server sends no status. */
class SatTurnStatusTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test fun anAnsweredTurnShowsItsReply() {
        val c = SatTurn(user_text = "what time is it", assistant_text = "It's noon.", status = "answered")
        assertNull(satTurnUnanswered(c))
        assertEquals("“what time is it”", satTurnSaid(c))
    }

    @Test fun anOlderServerReadsAsAnswered() {
        val c = json.decodeFromString(SatTurn.serializer(),
            """{"id":3,"user_text":"hi","assistant_text":"Hello.","utterance_trigger":"wake_word"}""")
        assertNull(c.status)
        assertNull(satTurnUnanswered(c))
    }

    @Test fun pendingAndFailedTurnsSaySo() {
        assertEquals("waiting for an answer…", satTurnUnanswered(SatTurn(status = "pending")))
        assertEquals("no answer — timed out",
            satTurnUnanswered(SatTurn(status = "no_answer", no_answer_reason = "timed_out")))
        assertEquals("no answer — something went wrong",
            satTurnUnanswered(SatTurn(status = "no_answer", no_answer_reason = "error")))
        assertEquals("no answer — interrupted",
            satTurnUnanswered(SatTurn(status = "no_answer", no_answer_reason = "interrupted")))
        assertEquals("no answer — Domovoi restarted",
            satTurnUnanswered(SatTurn(status = "no_answer", no_answer_reason = "restarted")))
        assertEquals("no answer", satTurnUnanswered(SatTurn(status = "no_answer")))
    }

    @Test fun anUnheardRequestSaysItWasNotMadeOut() {
        val c = json.decodeFromString(SatTurn.serializer(),
            """{"id":9,"user_text":"","assistant_text":null,"status":"unheard",
               "no_answer_reason":"timed_out","audio_ms":3400}""")
        assertEquals("couldn’t make out what was said · 3.4 s recording", satTurnSaid(c))
        assertEquals("no answer — timed out", satTurnUnanswered(c))
        assertEquals("couldn’t make out what was said", satTurnSaid(SatTurn(status = "unheard")))
    }

    @Test fun anOverheardTurnReadsIgnoredNotFailed() {
        val c = json.decodeFromString(SatTurn.serializer(),
            """{"id":11,"user_text":"and then the guy says","assistant_text":null,
               "status":"no_answer","no_answer_reason":"overheard","audio_ms":30000}""")
        assertTrue(satTurnIgnored(c))
        assertEquals("ignored — sounded like background talk", satTurnUnanswered(c))
        assertFalse(satTurnIgnored(SatTurn(status = "no_answer", no_answer_reason = "timed_out")))
        assertFalse(satTurnIgnored(SatTurn(status = "answered", no_answer_reason = "overheard")))
    }

    @Test fun theListIsReadAgainOnlyWhileATurnIsPending() {
        assertTrue(satAnyPending(listOf(SatTurn(status = "answered"), SatTurn(status = "pending"))))
        assertFalse(satAnyPending(listOf(SatTurn(status = "answered"), SatTurn(status = "no_answer"))))
        assertFalse(satAnyPending(null))
    }
}
