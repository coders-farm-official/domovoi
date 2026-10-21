package com.domovoi.app.ui.screens.chat

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * While the household's AI features are off the server's chat answers a
 * send or a picture upload with 409, `X-Domovoi-Refusal: ai-off` and its
 * sentence (domovoi/ai_features.py). The app says so in plain words, never
 * "409 Conflict" or an empty reply.
 */
class AiOffRefusalTest {
    private val detail = """{"detail":"AI features are turned off for this home (Settings → Models)"}"""

    @Test fun theHeaderOrThe409sSentenceIsTheAiOffRefusal() {
        assertTrue(isAiOffRefusal(409, "ai-off", detail))
        assertTrue(isAiOffRefusal(409, " AI-OFF ", ""))
        // A proxy that dropped the header: the 409 still carries the sentence.
        assertTrue(isAiOffRefusal(409, null, detail))
        // Another 409 is not it, nor another refusal, nor the sentence on a 500.
        assertFalse(isAiOffRefusal(409, null, """{"detail":"still answering the last one"}"""))
        assertFalse(isAiOffRefusal(409, "internet-off", ""))
        assertFalse(isAiOffRefusal(500, null, detail))
        assertFalse(isAiOffRefusal(409, null, null))
    }

    @Test fun aSendTheServerRefusedReadsInPlainWords() {
        assertEquals(AI_CHAT_OFF_TEXT, sendFailureLine(409, "Conflict", "ai-off", detail))
        assertEquals(AI_CHAT_OFF_TEXT, sendFailureLine(409, "", null, detail))
        assertEquals("AI chat is turned off on your home server", AI_CHAT_OFF_TEXT)
        // Anything else keeps the status line it had.
        assertEquals("409 Conflict", sendFailureLine(409, "Conflict", null, "{}"))
        assertEquals("500 Internal Server Error", sendFailureLine(500, "Internal Server Error", null, null))
        assertEquals("502", sendFailureLine(502, "", null, null))
    }

    /** The upload goes through ApiClient: the refusal header rides on the
     *  ApiException, and the picture's note says it in plain words. */
    @Test fun aPictureTheServerRefusedReadsInPlainWords() = runBlocking {
        val server = MockWebServer().also { it.start() }
        try {
            val api = ApiClient(baseUrlProvider = { server.url("/").toString().trimEnd('/') })
            val form = MultipartBody.Builder().setType(MultipartBody.FORM)
                .addFormDataPart("file", "a.png", byteArrayOf(1, 2).toRequestBody("image/png".toMediaType()))
                .build()
            server.enqueue(MockResponse().setResponseCode(409).setHeader("X-Domovoi-Refusal", "ai-off").setBody(detail))
            try {
                api.upload("/api/chat/uploads", form)
                fail("expected the 409")
            } catch (e: ApiException) {
                assertEquals(409, e.status)
                assertEquals("ai-off", e.refusal)
                assertTrue(isAiOffRefusal(e))
                assertEquals(AI_CHAT_OFF_TEXT, photoFailureText(e, 35))
            }
            // Any other 409 keeps its own words, and no refusal is read into it.
            server.enqueue(MockResponse().setResponseCode(409).setBody("""{"detail":"busy"}"""))
            try {
                api.upload("/api/chat/uploads", form)
                fail("expected the 409")
            } catch (e: ApiException) {
                assertNull(e.refusal)
                assertFalse(isAiOffRefusal(e))
                assertTrue(photoFailureText(e, 35).startsWith("upload failed"))
            }
        } finally {
            server.shutdown()
        }
    }
}
