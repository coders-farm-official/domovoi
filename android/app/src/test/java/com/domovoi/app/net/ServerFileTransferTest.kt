package com.domovoi.app.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * 2026-10-28: a Files download handed `http://<server>:6369/api/files/download?...`
 * to the phone's browser, which can't send the device token and showed
 * `{"detail":"X-Device-Token or admin session required"}`. Server files are
 * fetched by the app now (ServerFileTransfer); these pin that.
 */
class ServerFileTransferTest {

    @Test fun savedUnderTheEntrysOwnName() {
        assertEquals("app-release.apk", ServerFileTransfer.saveName("Installers/app-release.apk", false))
        assertEquals("song.mp3", ServerFileTransfer.saveName("song.mp3", false))
        // A folder comes back from the server as a zip.
        assertEquals("Household paperwork.zip", ServerFileTransfer.saveName("a/Household paperwork/", true))
        // Characters a file name can't hold are scrubbed, as DeviceDownloads does.
        assertEquals("a b.txt", ServerFileTransfer.saveName("a:b.txt", false))
        assertEquals("download", ServerFileTransfer.saveName("", false))
    }

    private fun moduleFile(rel: String): File {
        val here = File(rel)
        return if (here.exists()) here else File("app", rel)
    }

    /** No server API address is ever handed to another app: an ACTION_VIEW
     *  built from `api.absolute(...)` must name a dashboard page ("/#..."),
     *  never an `/api/` route a browser can't authenticate to. */
    @Test fun noServerApiAddressGoesToAnotherApp() {
        val root = moduleFile("src/main/java")
        assertTrue("sources not found from ${File(".").absolutePath}", root.isDirectory)
        val offenders = mutableListOf<String>()
        root.walkTopDown().filter { it.isFile && it.extension == "kt" }.forEach { f ->
            val lines = f.readLines()
            lines.forEachIndexed { i, line ->
                if ("ACTION_VIEW" !in line) return@forEachIndexed
                val window = lines.subList(i, minOf(lines.size, i + 4)).joinToString(" ")
                val absolute = "api.absolute(" in window
                val routeHelper = listOf("filesDownloadPath", "docRawPath", "imagesRawPath", "filesDownloadUrl")
                    .any { it in window }
                if ((absolute && "\"/#" !in window) || routeHelper) offenders += "${f.name}:${i + 1}"
            }
        }
        assertEquals("server API addresses handed to another app", emptyList<String>(), offenders)
        // The old helpers that did it are gone.
        val filesIo = File(root, "com/domovoi/app/net/FilesIo.kt").readText()
        assertTrue("openFileDownload" !in filesIo && "filesDownloadUrl" !in filesIo)
    }

    @Test fun openedFilesAreSharedFromTheirOwnCacheFolder() {
        val xml = moduleFile("src/main/res/xml/shared_files.xml").readText()
        assertTrue(xml.contains("path=\"${ServerFileTransfer.OPEN_DIR}/\""))
    }
}
