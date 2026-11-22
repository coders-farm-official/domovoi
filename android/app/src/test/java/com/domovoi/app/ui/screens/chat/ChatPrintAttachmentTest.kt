package com.domovoi.app.ui.screens.chat

import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.ui.screens.printing.ATTACHMENT_GONE
import com.domovoi.app.ui.screens.printing.PrintOptions
import com.domovoi.app.ui.screens.printing.PrintTarget
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** "Print this" on a file attached in a home-server chat (1.4.1): the reply
 *  carries `print_attachment` (never a `draft`), and Print goes to the
 *  attachment's own routes. */
class ChatPrintAttachmentTest {

    private val token = "0123456789abcdef".repeat(4)

    @Test fun theReplyCarriesTheAttachmentAndNoDraft() {
        val row = DomovoiJson.decodeFromString<MessageRow>(
            """{"id":7,"role":"assistant","content":"Here's cat.jpg, ready to print.",
               "print_attachment":{"token":"$token","name":"cat.jpg","kind":"image"}}""",
        )
        assertEquals(PrintAttachmentRef(token, "cat.jpg", "image"), row.print_attachment)
        assertNull(row.draft)
        assertTrue(row.print_attachment!!.usable)
    }

    @Test fun aServerWithoutItLeavesItOut() {
        val row = DomovoiJson.decodeFromString<MessageRow>("""{"id":7,"role":"assistant","content":"hi"}""")
        assertNull(row.print_attachment)
    }

    @Test fun onlyAPlainHexTokenGoesIntoAPath() {
        assertFalse(PrintAttachmentRef("", "a.pdf").usable)
        assertFalse(PrintAttachmentRef("../drafts/1", "a.pdf").usable)
        assertFalse(PrintAttachmentRef("abc?x=1", "a.pdf").usable)
        assertFalse(PrintAttachmentRef("a".repeat(129), "a.pdf").usable)
        assertTrue(PrintAttachmentRef("ABCdef09", "a.pdf").usable)
    }

    @Test fun printGoesToTheAttachmentsRoutes() {
        val t = PrintTarget.ChatAttachment(token)
        assertEquals("/api/chat/attachments/$token/print/preview", t.previewPath)
        assertEquals("/api/chat/attachments/$token/print/jobs", t.submitPath)
        val body = t.body(3, PrintOptions())
        assertEquals("3", body["printer_id"].toString())
        assertTrue(body["options"]!!.jsonObject.isEmpty())
        assertFalse("library_id" in body || "path" in body || "version" in body)
    }

    @Test fun aGoneAttachmentSaysSo() {
        val t = PrintTarget.ChatAttachment(token)
        assertEquals(ATTACHMENT_GONE, t.failureText("print", ApiException(404, "404 Not Found: gone")))
        assertTrue(t.failureText("print", ApiException(500, "500 boom")).isNotEmpty())
    }
}
