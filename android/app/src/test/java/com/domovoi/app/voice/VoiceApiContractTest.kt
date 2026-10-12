package com.domovoi.app.voice

import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.phonemizer.PhonemizerGoldenTest
import com.domovoi.app.phonemizer.RepoPhonemizer
import com.domovoi.app.ui.screens.settings.voiceLabel
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * `GET /api/voice` as the server really serves it, against the phone's
 * parser. `docs/uml/voice-api-fixture.json` is held to the route by
 * domovoi/tests/test_web_voice_api.py (the route serves exactly its
 * `response`, `updated_at` aside); here the phone's [VoiceInfo] decodes the
 * same body strictly, every field both sides name is the same, and the
 * phone's voice catalog is the server's three voices. A failure here after
 * a server change means the phone no longer reads what the server sends.
 */
class VoiceApiContractTest {

    private val fixture: JsonObject by lazy {
        val repo = PhonemizerGoldenTest.phonemizerDir().parentFile.parentFile
        val f = System.getProperty("domovoi.voice_api.fixture")?.let(::File)
            ?: File(repo, "docs/uml/voice-api-fixture.json")
        Json.parseToJsonElement(f.readText(Charsets.UTF_8)).jsonObject
    }
    private val response: JsonObject get() = fixture.getValue("response").jsonObject

    /** No unknown keys allowed: a field the server adds or renames fails here. */
    private val strict = Json { ignoreUnknownKeys = false }

    private fun SerialDescriptor.names(): Set<String> = (0 until elementsCount).map { getElementName(it) }.toSet()

    @Test fun thePhoneModelsEveryFieldTheServerSendsAndNoOther() {
        assertEquals(response.keys, VoiceInfo.serializer().descriptor.names())
        val voiceFields = ServerVoice.serializer().descriptor.names()
        assertEquals(setOf("id", "name", "sex", "sample_rate", "size_bytes", "sha256"), voiceFields)
        for (v in response.getValue("voices").jsonArray) assertEquals(voiceFields, v.jsonObject.keys)
        val entryFields = SayItLike.serializer().descriptor.names()
        assertEquals(setOf("word", "say_like"), entryFields)
        for (p in response.getValue("pronunciations").jsonArray) assertEquals(entryFields, p.jsonObject.keys)
    }

    @Test fun theServersBodyDecodesStrictlyWithEveryValueRead() {
        val info = strict.decodeFromJsonElement(VoiceInfo.serializer(), response)
        // The app's own (lenient) decoder reads the same.
        assertEquals(info, VoiceInfo.decode(response))
        assertEquals(3, info.voices.size)
        for (v in info.voices) {
            assertTrue(v.id.isNotBlank())
            assertTrue(!v.name.isNullOrBlank() && !v.sex.isNullOrBlank() && !v.sha256.isNullOrBlank())
            assertEquals(22_050, v.sample_rate)
            assertTrue((v.size_bytes ?: 0) > 0)
        }
        assertEquals("en_US-norman-medium", info.server_voice)
        assertNotNull(info.updated_at)
        assertEquals(listOf("Domovoi" to "DOH-moe-voy", "Domo" to "DOH-moe", "Nguyen" to "win"), info.pairs())
    }

    @Test fun thePhonesCatalogIsTheServersVoices() {
        val info = VoiceInfo.decode(response)
        assertEquals(info.voices.map { it.id }, ModelCatalog.voices.map { it.id })
        for (v in info.voices) {
            val model = ModelCatalog.voice(v.id)!!
            assertEquals(v.id, v.name, model.name)
            val onnx = model.files.single { it.path.endsWith(".onnx") }
            assertEquals(v.id, v.size_bytes, onnx.size)
            assertEquals(v.id, v.sha256, onnx.sha256)
            assertEquals("${v.name} (${v.sex})", voiceLabel(v.id))
        }
        // A fresh server speaks with the phone's default voice.
        assertEquals(info.server_voice, ModelCatalog.defaultVoice.id)
    }

    @Test fun aFreshServersListIsTheConvertersShippedList() {
        assertEquals(RepoPhonemizer.data.defaultPronunciations, VoiceInfo.decode(response).pairs())
    }
}
