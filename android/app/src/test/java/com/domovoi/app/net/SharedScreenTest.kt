package com.domovoi.app.net

import com.domovoi.app.data.ServerCredentials
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Whether this install is a shared screen: what the server's device row
 * says, what the app assumes before it has said anything, and how the
 * answer is kept per server.
 */
class SharedScreenTest {

    @Test fun theDeviceRowCarriesTheAnswer() = runBlocking {
        val server = MockWebServer().also { it.start() }
        try {
            val api = ApiClient({ server.url("/").toString().trimEnd('/') })
            server.enqueue(
                MockResponse().setBody(
                    """{"device_id":"android-1a2b","name":"Kitchen tablet","platform":"android",
                       "first_seen_at":"2026-09-01T00:00:00Z","last_seen_at":"2026-09-28T00:00:00Z","shared_screen":true}""",
                ),
            )
            val row = api.post("/api/devices/register").decode<DeviceRow>()
            assertEquals(true, row.sharedScreen)
            assertTrue(sharedScreenAnswer(row))

            // A web backend older than shared screens sends no field: that
            // is "no", never "unknown" (the app would otherwise hide Chat
            // for good on meeting it).
            server.enqueue(MockResponse().setBody("""{"device_id":"android-1a2b","name":"Pixel 8"}"""))
            val old = api.post("/api/devices/register").decode<DeviceRow>()
            assertNull(old.sharedScreen)
            assertFalse(sharedScreenAnswer(old))
        } finally {
            server.shutdown()
        }
    }

    @Test fun beforeTheServerAnswersAPairedInstallCountsAsShared() {
        // The web's rule: a paired browser with no answer may well be the
        // kitchen tablet, so it paints the shared view until told otherwise.
        assertTrue(isSharedScreen(answer = null, paired = true))
        // An unpaired one can't learn, so it is never masked.
        assertFalse(isSharedScreen(answer = null, paired = false))
        // Once answered, the answer wins either way.
        assertFalse(isSharedScreen(answer = false, paired = true))
        assertTrue(isSharedScreen(answer = true, paired = false))
    }

    @Test fun answersAreKeptPerServerAndSurviveABadBlob() {
        val answers = mapOf("http://10.0.0.5:6369" to true, "http://10.0.0.9:6369" to false)
        assertEquals(answers, ServerCredentials.decodeSharedAnswers(ServerCredentials.encodeSharedAnswers(answers)))
        assertEquals(emptyMap<String, Boolean>(), ServerCredentials.decodeSharedAnswers(null))
        assertEquals(emptyMap<String, Boolean>(), ServerCredentials.decodeSharedAnswers("{not json"))
    }
}
