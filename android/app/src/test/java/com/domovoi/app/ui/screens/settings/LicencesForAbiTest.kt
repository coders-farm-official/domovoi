package com.domovoi.app.ui.screens.settings

import com.domovoi.app.assistant.ModelCatalog
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The licences card on an install without the on-device assistant (a
 * 32-bit phone or APK): the assistant's downloads are not listed, as
 * nothing there downloads them, and LiteRT-LM, whose Java part is still in
 * the APK, says it is not used on that phone.
 */
class LicencesForAbiTest {

    @Test fun with64BitEverythingIsListed() {
        assertEquals(OPEN_SOURCE_PARTS, openSourceParts(assistant = true))
    }

    // One Chat: the phone's own downloads are for phone chats, not "the assistant".
    @Test fun thePhonesDownloadsSayTheyAreForPhoneChats() {
        val mine = OPEN_SOURCE_PARTS.filter { it.assistantOnly }
        assertTrue(mine.isNotEmpty())
        mine.forEach { p -> assertTrue("${p.name}: ${p.note}", p.note!!.startsWith("Downloaded for phone chats")) }
        assertTrue(OPEN_SOURCE_PARTS.none { (it.note ?: "").contains("assistant") })
    }

    @Test fun without64BitTheAssistantsDownloadsAreLeftOut() {
        val parts = openSourceParts(assistant = false)
        ModelCatalog.assistant.forEach { m -> assertTrue(m.name, parts.none { it.name == m.name }) }
        listOf("CMUdict", "Piper voices", "ONNX Runtime", "sherpa-onnx").forEach { name ->
            assertTrue(name, parts.any { it.name.contains(name) })
        }
        val engine = parts.single { it.name.startsWith("LiteRT-LM") }
        assertTrue(engine.note, engine.note!!.contains("not used on this one"))
        // Everything the app itself is built from keeps its notices.
        assertEquals(OPEN_SOURCE_PARTS.filterNot { it.assistantOnly }.flatMap { it.notices }, parts.flatMap { it.notices })
    }
}
