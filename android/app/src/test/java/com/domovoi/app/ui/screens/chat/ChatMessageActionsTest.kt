package com.domovoi.app.ui.screens.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.ZoneId

class ChatMessageActionsTest {
    private val zone = ZoneId.of("America/Chicago")
    private val now = Instant.parse("2026-09-30T17:37:00Z")   // wed 30 sep, 12:37 local

    @Test fun stampsReadByHowLongAgo() {
        assertEquals("12:29", chatStamp("2026-09-30T17:29:10+00:00", now, zone))
        assertEquals("yesterday 9:05", chatStamp("2026-09-29T14:05:00Z", now, zone))
        assertEquals("sat 21:40", chatStamp("2026-09-27T02:40:00Z", now, zone))
        assertEquals("3 sep 8:00", chatStamp("2026-09-03T13:00:00Z", now, zone))
        assertEquals("24 dec 2025 18:00", chatStamp("2025-12-25T00:00:00Z", now, zone))
        assertNull(chatStamp(null, now, zone))
        assertNull(chatStamp("not a time", now, zone))
    }

    @Test fun detailsForAStreamedReply() {
        val rows = detailRows(
            MessageFacts(
                id = 42, threadId = 7, role = "assistant", content = "Hope this helps!\nBye",
                createdAt = "2026-09-30T17:37:05Z", model = "llama3.2:3b", error = null,
                imageNames = emptyList(), firstWordsMs = 1_240, totalMs = 18_600,
            ),
            zone,
        ).toMap()
        assertEquals("domovoi", rows["from"])
        assertTrue(rows["sent"]!!.startsWith("Wed 30 Sep 2026, 12:37:05"))
        assertEquals("llama3.2:3b", rows["model"])
        assertEquals("first words after 1.2s · finished in 19s", rows["reply time"])
        assertEquals("20 characters · 4 words · 2 lines", rows["length"])
        assertEquals("#42 in thread #7", rows["message"])
        assertFalse(rows.containsKey("error"))
    }

    @Test fun detailsForAUserMessageWithImagesNotSavedYet() {
        val rows = detailRows(
            MessageFacts(
                id = null, threadId = 7, role = "user", content = "what is this?",
                createdAt = null, model = "ignored", error = "500 boom",
                imageNames = listOf("cat.jpg", ""),
            ),
            zone,
        ).toMap()
        assertEquals("you", rows["from"])
        assertFalse(rows.containsKey("model"))           // a model answers, it doesn't ask
        assertFalse(rows.containsKey("sent"))
        assertFalse(rows.containsKey("reply time"))
        assertEquals("2 · cat.jpg, image", rows["images"])
        assertEquals("500 boom", rows["error"])
        assertEquals("thread #7 (not saved yet)", rows["message"])
    }

    @Test fun lengthOfNothing() {
        assertEquals("0 characters · 0 words · 0 lines", lengthLine(""))
        assertEquals("1 character · 1 word · 1 line", lengthLine("a"))
    }
}
