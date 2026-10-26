package com.domovoi.app.ui.screens.documents

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The in-app sheet editor hands the household token to a WebView, so the
 * rules that keep it on the paired server are pinned here as written
 * (SheetWebEditor.kt): the same gate every other request passes, on every
 * request the page makes, no navigation off the server, no file access,
 * and nothing left in the page's storage afterwards.
 */
class SheetWebEditorTest {

    private fun moduleFile(rel: String): File =
        listOf(File(rel), File("app/$rel")).firstOrNull { it.isFile }
            ?: error("cannot find $rel from ${File(".").absolutePath}")

    private val src: String by lazy {
        moduleFile("src/main/java/com/domovoi/app/ui/screens/documents/SheetWebEditor.kt").readText()
    }

    @Test
    fun `the token is only handed over after the server proved itself`() {
        assertTrue(src.contains("app.api.tokenForDownload(page)"))
    }

    @Test
    fun `every request is checked again and other servers are refused`() {
        val intercept = src.substringAfter("override fun shouldInterceptRequest")
            .substringBefore("private fun refused")
        assertTrue(intercept.contains("!sameServer(url) || !CleartextPolicy.permits(url)"))
        assertTrue(intercept.contains("tokenForDownload(url)"))
        assertTrue(src.contains("return !sameServer(url)"))
    }

    @Test
    fun `no file access and the storage is wiped on close`() {
        assertTrue(src.contains("settings.allowFileAccess = false"))
        assertTrue(src.contains("settings.allowContentAccess = false"))
        assertTrue(src.contains("WebStorage.getInstance().deleteAllData()"))
        assertFalse(src.contains("onReceivedSslError"))
    }

    @Test
    fun `only the server's documents open in the web editor`() {
        val files = moduleFile("src/main/java/com/domovoi/app/ui/screens/files/FilesScreen.kt").readText()
        assertTrue(files.contains("if (!source.onPhone && selectedId == DOCUMENTS_LIBRARY_ID) {"))
        assertTrue(files.contains("SheetWebEditor(app, rel, close)"))
        assertTrue(files.contains("SheetEditorOverlay(source, selectedId ?: \"\", rel, close)"))
    }
}
