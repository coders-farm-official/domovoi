package com.domovoi.app.ui.screens.documents

import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The in-app document editor's rules: what opens in it, which pages may
 *  use its bridge, and what a shared file may be called. */
class WebDocEditorTest {

    @Test fun documentsOpenInTheEditor() {
        for (name in listOf("Plan.html", "old.HTM", "notes.md", "a.markdown", "Letter.docx")) {
            assertTrue(name, opensInDocEditor(name))
        }
        for (name in listOf("budget.xlsx", "list.csv", "scan.pdf", "old.doc", "photo.jpg", "readme.txt", "html")) {
            assertFalse(name, opensInDocEditor(name))
        }
    }

    @Test fun onlyTheServersOwnPagesCount() {
        val server = "http://192.168.1.20:6369/doc.html?path=a.html".toHttpUrl()
        assertTrue(sameOrigin(server, "http://192.168.1.20:6369/api/documents".toHttpUrl()))
        assertTrue(sameOrigin(server, "http://192.168.1.20:6369/".toHttpUrl()))
        assertFalse(sameOrigin(server, "http://192.168.1.20:6370/".toHttpUrl()))
        assertFalse(sameOrigin(server, "https://192.168.1.20:6369/".toHttpUrl()))
        assertFalse(sameOrigin(server, "http://example.org:6369/".toHttpUrl()))
    }

    @Test fun aSharedFileNameIsJustAName() {
        assertEquals("Holiday plan.docx", safeFileName("Holiday plan.docx"))
        assertEquals(".._.._x.md".trimStart('.'), safeFileName("../../x.md"))
        assertEquals("a_b_c.md", safeFileName("a/b\\c.md"))
        assertEquals("Document", safeFileName("   "))
    }
}
