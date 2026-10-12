package com.domovoi.app.ui.screens.settings

import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.phonemizer.RepoPhonemizer
import com.domovoi.app.voice.PhoneVoiceChoice
import com.domovoi.app.voice.PhoneVoiceUi
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class VoicePanelTest {
    private val same = PhoneVoiceChoice.SAME_AS_SERVER

    @Test fun theMenuNamesWhatEachChoiceComesTo() {
        assertEquals("Norman (male)", voiceLabel("en_US-norman-medium"))
        assertEquals("Kristin (female)", voiceLabel("en_US-kristin-medium"))
        assertEquals("Linda (female)", voiceLabel("en_US-ljspeech-medium"))
        assertEquals("Same as the server (Kristin)", voiceChoiceLabel(same, PhoneVoiceUi(serverVoice = "en_US-kristin-medium")))
        assertEquals("Same as the server", voiceChoiceLabel(same, PhoneVoiceUi(serverVoice = null)))
        assertEquals("Kristin (female)", voiceChoiceLabel("en_US-kristin-medium", PhoneVoiceUi()))
    }

    @Test fun itSaysWhereTheVoiceComesFrom() {
        assertEquals("The voice your Domovoi speaks with, on the phone and in the chat mic's replies.",
            voiceSourceNote(PhoneVoiceUi(serverVoice = "en_US-norman-medium")))
        assertTrue(voiceSourceNote(PhoneVoiceUi(serverVoice = null)).contains("speaks with Norman"))
        assertTrue(voiceSourceNote(PhoneVoiceUi(serverVoice = "en_US-lessac-medium")).contains("en_US-lessac-medium, which this app does not have"))
        assertTrue(voiceSourceNote(PhoneVoiceUi(choice = "en_US-kristin-medium")).startsWith("This phone's own pick"))
        // The pick says it covers the chat mic's replies (Kamron's test, 2026-10-12).
        val kristin = "en_US-kristin-medium"
        val ready = voiceSourceNote(PhoneVoiceUi(choice = kristin, effective = kristin, installed = setOf(kristin)))
        assertTrue(ready, ready.contains("also speaks the chat mic's replies, on the phone"))
        val notYet = voiceSourceNote(PhoneVoiceUi(choice = kristin, effective = kristin))
        assertTrue(notYet, notYet.contains("once it is downloaded") && notYet.contains("in the server's voice"))
    }

    @Test fun itSaysWhichListIsInForce() {
        assertTrue(pronunciationNote(PhoneVoiceUi(pronunciationSource = "server", pronunciationCount = 1)).contains("(1 entry)"))
        assertTrue(pronunciationNote(PhoneVoiceUi(pronunciationSource = "default", pronunciationCount = 3)).contains("built-in"))
        assertTrue(pronunciationNote(PhoneVoiceUi(pronunciationError = "Bob: no")).contains("could not be used (Bob: no)"))
        assertEquals(false, PhoneVoiceUi(effective = "en_US-norman-medium").effectiveInstalled)
        assertEquals(true, PhoneVoiceUi(effective = "en_US-norman-medium", installed = setOf("en_US-norman-medium")).effectiveInstalled)
    }

    /** Every notice the licences card offers is a file the build copies
     *  into the assets (app/build.gradle.kts voiceAssets), from its source. */
    @Test fun everyNoticeComesFromItsSource() {
        val parts = OPEN_SOURCE_PARTS
        val notices = parts.flatMap { it.notices }
        assertTrue(notices.size >= 10)
        notices.forEach { path ->
            val source = when {
                path.startsWith("phonemizer/") -> File(RepoPhonemizer.dir, "data/" + path.removePrefix("phonemizer/"))
                path == "licences/sherpa-onnx/LICENSE" -> File(RepoPhonemizer.androidDir, "native/sherpa-onnx/LICENSE")
                path.startsWith("licences/sherpa-onnx/") ->
                    File(RepoPhonemizer.androidDir, "native/sherpa-onnx/licenses/" + path.removePrefix("licences/sherpa-onnx/"))
                else -> error("no source for $path")
            }
            assertTrue("$path: $source", source.isFile)
        }
        val names = parts.joinToString("\n") { it.name }
        listOf("CMUdict", "Piper voices", "ONNX Runtime", "sherpa-onnx", "LiteRT-LM").forEach {
            assertTrue("missing $it", names.contains(it))
        }
        ModelCatalog.assistant.forEach { m -> assertTrue(m.name, parts.any { it.name == m.name && it.licence == m.license }) }
        val allowed = setOf("MIT", "Apache-2.0", "BSD-2-Clause style", "public domain")
        parts.forEach { assertTrue("${it.name}: ${it.licence}", it.licence in allowed) }
    }
}
