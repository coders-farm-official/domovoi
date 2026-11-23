package com.domovoi.app.ui.screens.chat

import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import okio.BufferedSource
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The chat answer stream's `replace` event (1.4.1): the reply streamed so
 * far becomes exactly its text and later deltas append to it. An empty one
 * starts over (a web answer follows), so the old words never show joined
 * to the new ones; older servers never send it.
 */
class ChatStreamReplaceTest {
    private fun frame(event: String, data: String) = "event: $event\ndata: $data\n\n"

    /** What the bubble shows after each event, read the way sendStreaming's
     *  callers apply them to the pending reply. */
    private fun shown(source: BufferedSource, live: LiveMessage = LiveMessage("assistant", "", pending = true)): List<String> {
        val seen = ArrayList<String>()
        var clock = 1000L
        runBlocking {
            readChatStream(source) { ev ->
                clock += 10
                when (ev) {
                    is ChatStreamEvent.Delta -> live.appendStreamed(ev.text, clock)
                    is ChatStreamEvent.Replace -> live.replaceStreamed(ev.text, clock)
                    is ChatStreamEvent.Done -> live.finishWith(ev.row)
                    else -> Unit
                }
                seen += live.content
            }
        }
        return seen
    }

    private val startOver = frame("delta", """{"text":"A"}""") +
        frame("delta", """{"text":"B"}""") +
        frame("replace", """{"text":""}""") +
        frame("delta", """{"text":"C"}""") +
        frame("done", """{"id":7,"role":"assistant","content":"C"}""")

    @Test fun anEmptyReplaceStartsTheReplyOver() {
        val seen = shown(Buffer().writeUtf8(startOver))
        assertEquals(listOf("A", "AB", "", "C", "C"), seen)
        assertFalse(seen.any { it.contains("AB") && it.contains("C") })
    }

    /** The same stream as the server sends it, read over HTTP. */
    @Test fun anEmptyReplaceStartsOverOverTheWire() {
        val server = MockWebServer().also { it.start() }
        try {
            server.enqueue(
                MockResponse().setHeader("Content-Type", "text/event-stream")
                    .setChunkedBody(startOver, 8),
            )
            val req = Request.Builder().url(server.url("/api/chat/threads/1/messages")).build()
            OkHttpClient().newCall(req).execute().use { resp ->
                val live = LiveMessage("assistant", "", pending = true)
                val seen = shown(resp.body!!.source(), live)
                assertEquals("C", seen.last())
                assertFalse("ABC" in seen)
                assertEquals(7L, live.id)
            }
        } finally {
            server.shutdown()
        }
    }

    @Test fun aReplaceWithTextIsTheReplyAndDeltasAppendToIt() {
        val stream = frame("delta", """{"text":"old"}""") +
            frame("replace", """{"text":"X"}""") +
            frame("delta", """{"text":"Y"}""")
        assertEquals(listOf("old", "X", "XY"), shown(Buffer().writeUtf8(stream)))
    }

    @Test fun theFirstWordsAreTheOnesShownAfterAStartOver() {
        val live = LiveMessage("assistant", "", pending = true)
        live.appendStreamed("A", now = 100)
        assertEquals(100L, live.firstWordsAtMs)
        live.replaceStreamed("", now = 200)
        assertNull(live.firstWordsAtMs)
        live.appendStreamed("C", now = 300)
        assertEquals(300L, live.firstWordsAtMs)
        // A corrected reply keeps the first words' time; one that was
        // empty until then is shown at the replace.
        live.replaceStreamed("X", now = 400)
        assertEquals(300L, live.firstWordsAtMs)
        val fresh = LiveMessage("assistant", "", pending = true)
        fresh.replaceStreamed("X", now = 500)
        assertEquals(500L, fresh.firstWordsAtMs)
        assertEquals("X", fresh.content)
    }

    @Test fun unknownEventsAreStillSkipped() {
        val stream = frame("delta", """{"text":"A"}""") +
            frame("something_new", """{"text":"ignored"}""") +
            frame("replace", "not json") +
            frame("delta", """{"text":"B"}""")
        assertEquals(listOf("A", "AB"), shown(Buffer().writeUtf8(stream)))
        assertNull(chatStreamEvent("something_new", """{"text":"x"}"""))
        assertEquals(ChatStreamEvent.Replace(""), chatStreamEvent("replace", """{"text":""}"""))
        assertTrue(chatStreamEvent("done", """{"id":1,"content":"x"}""") is ChatStreamEvent.Done)
    }
}
