package com.domovoi.app.ui.screens.chat

import com.domovoi.app.net.decode
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pictures in chat when the vision model isn't installed (2026-10-11: a
 * picture got "Client error '404 Not Found' for url 'http://localhost:
 * 11434/api/chat'" in red): the paperclip explains instead of opening the
 * picker, and a reply's coded error reads as the same plain sentence the
 * server and the dashboard use (web/static/chat_details.js).
 */
class ChatErrorsTest {

    private val missing = ChatModelsInfo(
        installed = listOf("llama3.2:3b", "qwen2.5:7b", "qwen3:8b"),
        vision_model = "qwen2.5vl:7b",
        vision_installed = false,
    )

    @Test fun theVisionRefusalIsWordedLikeTheServer() {
        assertEquals(
            "Pictures need the vision model qwen2.5vl:7b, which isn't installed on this Domovoi. " +
                "An admin can install it in Settings → Models, or pick another vision model there.",
            visionMissingText("qwen2.5vl:7b"),
        )
        assertEquals(
            "Pictures need a vision model, and none is set on this Domovoi. " +
                "An admin can pick one in Settings → Models.",
            visionMissingText(" "),
        )
        assertEquals(visionMissingText(""), visionMissingText(null))
    }

    @Test fun theServerSaysWhetherAPictureWouldBeRefused() {
        assertTrue(visionMissing(missing))
        assertFalse(visionMissing(missing.copy(vision_installed = true)))
        // Not read yet: leave it to the send.
        assertFalse(visionMissing(null))
    }

    @Test fun anOlderServerIsJudgedByItsList() {
        val old = missing.copy(vision_installed = null)
        assertTrue(visionMissing(old))
        assertFalse(visionMissing(old.copy(installed = old.installed + "qwen2.5vl:7b")))
        // Ollama names an untagged model ":latest", and case doesn't count.
        assertFalse(visionMissing(old.copy(vision_model = "LLaVA", installed = listOf("llava:latest"))))
        // An empty list is Ollama not answering, not "nothing installed".
        assertFalse(visionMissing(old.copy(installed = emptyList())))
        // No vision model set at all.
        assertTrue(visionMissing(old.copy(vision_model = "")))
    }

    @Test fun thePaperclipExplainsBeforeItOpensThePicker() {
        assertEquals(AttachAction.ExplainVision, attachAction(missing, attached = 0))
        // The explanation wins over the cap: no picture can go anyway.
        assertEquals(AttachAction.ExplainVision, attachAction(missing, attached = MAX_CHAT_IMAGES))
        val ok = missing.copy(vision_installed = true)
        assertEquals(AttachAction.Pick, attachAction(ok, attached = 0))
        assertEquals(AttachAction.AtCap, attachAction(ok, attached = MAX_CHAT_IMAGES))
        // Unknown (Ollama down, or the read not back yet): the picker opens.
        assertEquals(AttachAction.Pick, attachAction(null, attached = 0))
    }

    @Test fun theAcceptedFilesAreSpelledOut() {
        assertEquals(
            "Chat takes text, pictures and documents, up to 4 of each a message. Pictures: PNG, JPEG, GIF, WebP " +
                "or BMP, and phone photos in HEIC or AVIF, which this phone turns into a JPEG first. Documents: " +
                "PDF, Word (.docx), OpenDocument (.odt), Excel (.xlsx), CSV, text or markdown.",
            CHAT_ACCEPTS,
        )
        assertEquals(4, MAX_CHAT_IMAGES)
        assertEquals(4, MAX_CHAT_DOCUMENTS)
    }

    @Test fun aVisionRefusalRendersAsTheExplanation() {
        val view = chatErrorView("anything the server said", "vision_model_missing", "qwen2.5vl:7b")!!
        assertEquals(visionMissingText("qwen2.5vl:7b"), view.text)
        assertEquals("vision_model_missing", view.code)
        assertTrue(view.models)
    }

    @Test fun otherCodedErrorsKeepTheServersSentence() {
        val gone = chatErrorView("Domovoi couldn't reach its language model.", "model_unreachable", "m")!!
        assertEquals("Domovoi couldn't reach its language model.", gone.text)
        assertFalse(gone.models)
        for (code in listOf("model_missing", "vision_unsupported", "model_memory")) {
            assertTrue(code, chatErrorView("x", code, "m")!!.models)
        }
        for (code in listOf("model_timeout", "model_error", "empty_reply")) {
            assertFalse(code, chatErrorView("x", code, "m")!!.models)
        }
    }

    @Test fun anUncodedErrorIsShownAsItIs() {
        val own = chatErrorView("send failed (offline?)", null, null)!!
        assertEquals("send failed (offline?)", own.text)
        assertNull(own.code)
        assertFalse(own.models)
        assertNull(chatErrorView(null, "model_error", "m"))
        assertNull(chatErrorView(" ", null, null))
    }

    @Test fun theRowsCodeAndTheModelsReadDecode() {
        val stats = Json.parseToJsonElement("""{"model_role":"vision","error_code":"vision_model_missing"}""")
            .decode<ChatStats>()
        assertEquals("vision_model_missing", stats.error_code)
        val info = Json.parseToJsonElement(
            """{"installed":["llama3.2:3b"],"default_model":"llama3.2:3b",
               "vision_model":"qwen2.5vl:7b","vision_installed":false,"extra":1}""",
        ).decode<ChatModelsInfo>()
        assertEquals(false, info.vision_installed)
        assertTrue(visionMissing(info))
        // A server that predates the field.
        val old = Json.parseToJsonElement("""{"installed":[],"vision_model":"x"}""").decode<ChatModelsInfo>()
        assertNull(old.vision_installed)
    }
}
