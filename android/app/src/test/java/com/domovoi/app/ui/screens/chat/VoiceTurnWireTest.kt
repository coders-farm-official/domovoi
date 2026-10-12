package com.domovoi.app.ui.screens.chat

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/**
 * The request the chat mic sends, as the dashboard's
 * POST /api/chat/threads/{id}/voice reads it (web/backend/api/chat.py
 * send_voice): multipart `audio` + `phone_id` (+ `player`), the household
 * token and the preflight header every write carries.
 */
class VoiceTurnWireTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun setUp() {
        server = MockWebServer().also { it.start() }
        api = ApiClient(
            baseUrlProvider = { server.url("/").toString().trimEnd('/') },
            deviceTokenProvider = { "household-token" },
        )
    }

    @After fun tearDown() {
        server.shutdown()
    }

    @Test fun aTurnIsPostedAsTheDashboardReadsIt() = runBlocking {
        server.enqueue(
            MockResponse().setBody(
                """{"heard": true, "transcript": "what time is it", "reply": "It's 9:05.",
                    "messages": [{"id": 1, "role": "user", "source": "voice"},
                                 {"id": 2, "role": "assistant", "source": "voice"}]}""",
            ),
        )
        val pcm = ByteArray(32_000) { (it % 251).toByte() }
        val player = buildJsonObject { put("title", "Glass Harbor"); put("playing", true) }
        val turn = sendVoiceTurn(api, "android-1a2b", 7, pcm, player)
        assertEquals("It's 9:05.", turn.reply)
        assertEquals(listOf("user", "assistant"), turn.messages.map { it.role })

        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/api/chat/threads/7/voice", req.path)
        assertEquals("household-token", req.getHeader("X-Device-Token"))
        assertEquals("DomovoiApp", req.getHeader("X-Requested-With"))
        assertTrue(req.getHeader("Content-Type")!!.startsWith("multipart/form-data"))
        val body = req.body.readByteArray()
        val text = String(body, Charsets.ISO_8859_1)
        assertTrue(text.contains("name=\"audio\"; filename=\"turn.pcm\""))
        assertTrue(text.contains("name=\"phone_id\""))
        assertTrue(text.contains("\r\n\r\nandroid-1a2b\r\n"))
        assertTrue(text.contains("name=\"player\""))
        assertTrue(text.contains("\"title\":\"Glass Harbor\""))
        // The recording travels byte for byte.
        val at = indexOf(body, pcm.copyOfRange(0, 64))
        assertTrue("the PCM is in the body", at > 0)
        assertTrue(body.size >= at + pcm.size)
        assertEquals(pcm.toList(), body.copyOfRange(at, at + pcm.size).toList())
    }

    @Test fun aRefusalCarriesTheServersSentence() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(409)
                .setBody("""{"detail": "this phone's last turn is still being answered", "code": "busy"}"""),
        )
        try {
            sendVoiceTurn(api, "android-1a2b", 7, ByteArray(10), null)
            fail("expected a refusal")
        } catch (e: VoiceTurnException) {
            assertEquals(409, e.status)
            assertEquals("busy", e.code)
            assertEquals("this phone's last turn is still being answered", e.message)
        }
        // No player state: no player part.
        assertTrue(!server.takeRequest().body.readUtf8().contains("name=\"player\""))
    }

    @Test fun anUnpairedPhoneIsSentToPairing() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(401).setBody("""{"detail": "X-Device-Token or admin session required"}"""),
        )
        try {
            sendVoiceTurn(api, "android-1a2b", 7, ByteArray(10), null)
            fail("expected a refusal")
        } catch (e: ApiException) {
            assertTrue(e.deviceTokenRequired)
        }
        assertTrue(api.pairingRequired.value)
    }

    private fun indexOf(haystack: ByteArray, needle: ByteArray): Int {
        outer@ for (i in 0..haystack.size - needle.size) {
            for (j in needle.indices) if (haystack[i + j] != needle[j]) continue@outer
            return i
        }
        return -1
    }
}
