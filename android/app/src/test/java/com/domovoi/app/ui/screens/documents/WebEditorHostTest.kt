package com.domovoi.app.ui.screens.documents

import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The one in-app host for the server's document and sheet editors hands the
 * household token to a WebView, so the rules that keep it on the paired
 * server are pinned here as written (WebEditorHost.kt): the same gate every
 * other request passes, on every request the page makes, no navigation off
 * the server, no file access, a one-shot bridge, no Safe Browsing, and
 * nothing left in the page's storage afterwards. Merged from the two hosts'
 * tests (SheetWebEditorTest, WebDocEditorTest) in integration 1027a.
 */
class WebEditorHostTest {

    private fun moduleFile(rel: String): File =
        listOf(File(rel), File("app/$rel")).firstOrNull { it.isFile }
            ?: error("cannot find $rel from ${File(".").absolutePath}")

    private val src: String by lazy {
        moduleFile("src/main/java/com/domovoi/app/ui/screens/documents/WebEditorHost.kt").readText()
    }

    @Test
    fun `the token is only handed over after the server proved itself`() {
        assertTrue(src.contains("app.api.tokenForDownload(page)"))
        // A failed or empty check means the editor does not open at all.
        assertTrue(src.contains("if (t == null) failure ="))
        assertTrue(src.contains("if (!CleartextPolicy.permits(page))"))
    }

    @Test
    fun `every request is checked again and other servers are refused`() {
        val intercept = src.substringAfter("override fun shouldInterceptRequest")
            .substringBefore("private fun refused")
        assertTrue(intercept.contains("!sameOrigin(base, url) || !CleartextPolicy.permits(url)"))
        assertTrue(intercept.contains("tokenForDownload(url)"))
        assertTrue(src.contains("return !sameOrigin(base, url)"))
    }

    @Test
    fun `no file access and the storage is wiped on close`() {
        assertTrue(src.contains("s.allowFileAccess = false"))
        assertTrue(src.contains("s.allowContentAccess = false"))
        assertTrue(src.contains("WebStorage.getInstance().deleteAllData()"))
        assertFalse(src.contains("onReceivedSslError"))
        assertFalse("the token never goes into page storage from here", src.contains("localStorage"))
    }

    @Test
    fun `the token is handed over once and Safe Browsing is off`() {
        assertTrue(src.contains("fun deviceToken(): String = pending.getAndSet(null).orEmpty()"))
        assertTrue(src.contains("s.safeBrowsingEnabled = false"))
        assertTrue(src.contains("const val NAME = \"DomovoiAppBridge\""))
    }

    @Test
    fun `the print copy is released and only documents print or share`() {
        assertTrue(src.contains("ReleasingAdapter(inner) { release(view) }"))
        assertTrue(src.contains("if (!kind.share) return"))
        assertTrue(src.contains("if (!kind.print) return"))
        assertTrue(WebEditorKind.Document.share && WebEditorKind.Document.print)
        assertFalse(WebEditorKind.Sheet.share || WebEditorKind.Sheet.print || WebEditorKind.Sheet.pickPictures)
    }

    @Test
    fun `each kind opens its own page with the path where that page reads it`() {
        val base = "http://192.168.1.20:6369".toHttpUrl()
        val doc = WebEditorKind.Document.page(base, "Plans/A b.html")
        assertEquals("/doc.html", doc.encodedPath)
        assertEquals("Plans/A b.html", doc.queryParameter("path"))
        assertEquals(null, doc.fragment)
        val sheet = WebEditorKind.Sheet.page(base, "Money/budget.xlsx")
        assertEquals("/sheet.html", sheet.encodedPath)
        assertEquals("path=Money/budget.xlsx", sheet.encodedFragment)
        assertEquals(null, sheet.query)
        assertEquals("a%20b%26c/%C3%A9%23.xlsx", encodeFragmentPath("a b&c/é#.xlsx"))
    }

    @Test
    fun `only the server's documents and sheets open in the web editors`() {
        val files = moduleFile("src/main/java/com/domovoi/app/ui/screens/files/FilesScreen.kt").readText()
        assertTrue(files.contains("if (!source.onPhone && selectedId == DOCUMENTS_LIBRARY_ID) {"))
        assertTrue(files.contains("WebEditorHost(app, rel, WebEditorKind.Sheet, close)"))
        assertTrue(files.contains("WebEditorHost(app, rel, WebEditorKind.Document) {"))
        assertTrue(files.contains("SheetEditorOverlay(source, selectedId ?: \"\", rel, close)"))
    }

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

    @Test fun theShareSheetOnlyGetsTheTwoDownloadKinds() {
        assertEquals("text/markdown", shareMime("text/markdown"))
        assertEquals(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            shareMime("text/html"),
        )
    }
}
