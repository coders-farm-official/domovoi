package com.domovoi.app.voice

import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.phonemizer.OrtG2pModel
import com.domovoi.app.phonemizer.Phonemizer
import com.domovoi.app.phonemizer.RepoPhonemizer
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.AfterClass
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.BeforeClass
import org.junit.Test

/**
 * The phone's voice choice and the server's `/api/voice` answer, against a
 * fake server shaped like the contract in VOICE-B-PHONE-BRIEF.md (the
 * server agent builds the real one; the integration run checks the two).
 */
class PhoneVoiceTest {
    companion object {
        private lateinit var model: OrtG2pModel

        @BeforeClass @JvmStatic fun load() {
            model = OrtG2pModel.load(RepoPhonemizer.files)
        }

        @AfterClass @JvmStatic fun close() = model.close()

        /** The brief's example answer. */
        const val CONTRACT = """{"voices": [{"id": "en_US-norman-medium", "name": "Norman", "sex": "male",
                "sample_rate": 22050, "size_bytes": 63511038, "sha256": "abc"}],
            "server_voice": "en_US-norman-medium",
            "pronunciations": [{"word": "Domovoi", "say_like": "DOH-moe-voy"}],
            "updated_at": "2026-10-12T00:00:00Z"}"""
    }

    private fun phonemizer() = Phonemizer(RepoPhonemizer.data, model)

    @Test fun theVoiceInUse() {
        val same = PhoneVoiceChoice.SAME_AS_SERVER
        assertEquals(ModelCatalog.kristin, PhoneVoiceChoice.effective(same, "en_US-kristin-medium"))
        assertEquals(ModelCatalog.kristin, PhoneVoiceChoice.effective(null, "en_US-kristin-medium"))
        // Offline with no server choice known: Norman.
        assertEquals(ModelCatalog.kristin, PhoneVoiceChoice.effective(same, null))     // the default
        // A server voice the app does not have (an old lessac install): Norman, and say so.
        assertEquals(ModelCatalog.kristin, PhoneVoiceChoice.effective(same, "en_US-lessac-medium"))
        assertEquals("en_US-lessac-medium", PhoneVoiceChoice.unknownServerVoice("en_US-lessac-medium"))
        assertNull(PhoneVoiceChoice.unknownServerVoice("en_US-norman-medium"))
        assertNull(PhoneVoiceChoice.unknownServerVoice(null))
        // The phone's own pick wins over the server's.
        assertEquals(ModelCatalog.ljspeech, PhoneVoiceChoice.effective("en_US-ljspeech-medium", "en_US-kristin-medium"))
        assertEquals(ModelCatalog.kristin, PhoneVoiceChoice.effective("nonsense", "en_US-kristin-medium"))
    }

    @Test fun theContractDecodesAndRoundTrips() {
        val info = VoiceInfo.decode(CONTRACT)!!
        assertEquals("en_US-norman-medium", info.server_voice)
        assertEquals(listOf("Domovoi" to "DOH-moe-voy"), info.pairs())
        assertEquals(63_511_038L, info.voices.single().size_bytes)
        assertEquals(info, VoiceInfo.decode(VoiceInfo.encode(info)))
        // Missing fields are tolerated (the backend evolves); blank entries dropped.
        val sparse = VoiceInfo.decode("""{"pronunciations": [{"word": "x"}, {"word": "Nguyen", "say_like": "win"}], "extra": 1}""")!!
        assertNull(sparse.server_voice)
        assertEquals(listOf("Nguyen" to "win"), sparse.pairs())
        assertNull(VoiceInfo.decode("not json"))
    }

    @Test fun itAsksTheServerAtApiVoice() = runBlocking {
        val server = MockWebServer().also { it.start() }
        try {
            val api = ApiClient(baseUrlProvider = { server.url("/").toString().trimEnd('/') })
            server.enqueue(MockResponse().setBody(CONTRACT))
            val info = fetchVoiceInfo(api)
            assertEquals("/api/voice", server.takeRequest().path)
            assertEquals("en_US-norman-medium", info.server_voice)
            // An older server without the endpoint: an error the caller swallows (the cache stays).
            server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}"""))
            try {
                fetchVoiceInfo(api)
                fail("a 404 was not an error")
            } catch (e: ApiException) {
                assertEquals(404, e.status)
            }
        } finally {
            server.shutdown()
        }
    }

    @Test fun theServersListIsWhatTheVoiceSays() {
        val p = phonemizer()
        // The fixture's say_it_like-002: the owner's DOM-uh-voy.
        val dom = VoiceInfo(pronunciations = listOf(SayItLike("Domovoi", "DOM-uh-voy")))
        assertEquals(PronunciationState("server", 1, null), applyPronunciations(p, dom))
        assertEquals(listOf("hˈeɪ dˈɒməvˌɔɪ."), p.phonemize("Hey Domovoi."))
        // An empty list is the owner's choice (say_it_like-013): no entries.
        assertEquals(PronunciationState("server", 0, null), applyPronunciations(p, VoiceInfo(pronunciations = emptyList())))
        assertEquals(listOf("hˈeɪ dˈəʊməvˌɔɪ.", "nuːjˈɛn ɪz hˈiə."), p.phonemize("Hey Domovoi. Nguyen is here."))
        // No server answer yet: the shipped list (DOH-moe-voy).
        assertEquals(PronunciationState("default", 3, null), applyPronunciations(p, null))
        assertEquals(listOf("hˈeɪ dˈəʊməʊvˌɔɪ!"), p.phonemize("Hey Domovoi!"))
    }

    @Test fun aListWithABadEntryIsNotTakenAtAll() {
        val p = phonemizer()
        val bad = VoiceInfo(pronunciations = listOf(SayItLike("Domovoi", "DOM-uh-voy"), SayItLike("Bob", "b@b")))
        val state = applyPronunciations(p, bad)
        assertEquals("default", state.source)
        assertNotNull(state.error)
        assertEquals(true, state.error!!.startsWith("Bob:"))
        assertEquals(listOf("hˈeɪ dˈəʊməʊvˌɔɪ!"), p.phonemize("Hey Domovoi!"))
    }
}
