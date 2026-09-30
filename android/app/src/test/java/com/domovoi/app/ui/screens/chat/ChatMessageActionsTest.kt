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

    @Test fun serverStatsFillTheDetails() {
        val st = ChatStats(
            prompt_tokens = 1_234, output_tokens = 42, tokens_per_sec = 35.04,
            total_ms = 3_100.0, load_ms = 1_800.0, prompt_ms = 300.0, generate_ms = 1_200.0,
            first_token_ms = 2_150.0, wall_ms = 3_400.0, done_reason = "length",
            context_sent = 30, context_in_thread = 44, context_limit = 30, num_ctx = 8_192,
            model_role = "vision",
        )
        val rows = detailRows(
            MessageFacts(
                id = 9, threadId = 2, role = "assistant", content = "x", createdAt = null,
                model = "llava:7b", error = null, imageNames = emptyList(),
                firstWordsMs = 99_000, totalMs = 99_000, stats = st,
            ),
            zone,
        ).toMap()
        assertEquals("llava:7b (vision model: images attached)", rows["model"])
        // The server's timing wins over the phone's.
        assertEquals("first words after 2.2s · finished in 3.4s", rows["reply time"])
        assertEquals("loading the model 1.8s · reading 0.3s · writing 1.2s", rows["time spent"])
        assertEquals("1,234 in · 42 out", rows["tokens"])
        assertEquals("35.0 tokens/s", rows["speed"])
        assertEquals("cut off at the length limit", rows["stopped"])
        assertEquals("saw the last 30 of 44 messages; older ones were left out · 8,192-token window", rows["context"])
    }

    @Test fun aWarmModelAndAShortThreadReadPlainly() {
        val st = ChatStats(load_ms = 40.0, prompt_ms = 90.0, generate_ms = 800.0, done_reason = "stop",
            context_sent = 3, context_in_thread = 3)
        val rows = detailRows(
            MessageFacts(1, 1, "assistant", "x", null, "m", null, emptyList(), stats = st), zone,
        ).toMap()
        assertEquals("reading 0.1s · writing 0.8s", rows["time spent"])   // no cold start to report
        assertEquals("finished normally", rows["stopped"])
        assertEquals("saw all 3 messages in the thread", rows["context"])
        assertEquals("m", rows["model"])
    }

    @Test fun aUserMessageNamesItsDevice() {
        val rows = detailRows(
            MessageFacts(1, 1, "user", "hi", null, null, null, emptyList(), device = "Kitchen tablet"), zone,
        ).toMap()
        assertEquals("Kitchen tablet", rows["device"])
    }
}
