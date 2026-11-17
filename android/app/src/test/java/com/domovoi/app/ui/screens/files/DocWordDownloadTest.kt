package com.domovoi.app.ui.screens.files

import com.domovoi.app.net.ApiException
import com.domovoi.app.net.ServerFileTransfer
import com.domovoi.app.ui.screens.documents.docWordExportPath
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.URI

/**
 * 2026-11-17: Download on a Domovoi document (a formatted .html in the
 * Documents library, the editor's own format) saved the raw web page.
 * It now saves the server's Word copy
 * (`GET /api/documents/export/doc/{rel_path:path}?fmt=docx`, documents.py
 * `export_doc`) as `<stem>.docx`; everything else downloads as before.
 */
class DocWordDownloadTest {

    private fun file(rel: String) = FileEntry(name = rel.substringAfterLast('/'), rel = rel)

    @Test fun onlyDocumentsHtmlConverts() {
        assertTrue(downloadsAsWord(DOCUMENTS_LIBRARY_ID, file("Letter.html")))
        assertTrue(downloadsAsWord(DOCUMENTS_LIBRARY_ID, file("Letters/Old.htm")))
        assertTrue("case-insensitive", downloadsAsWord(DOCUMENTS_LIBRARY_ID, file("SHOUTY.HTML")))
        assertTrue(downloadsAsWord(DOCUMENTS_LIBRARY_ID, file("Mixed.HtM")))
        // Other kinds in Documents keep their own file.
        for (name in listOf("notes.md", "list.txt", "scan.pdf", "letter.docx", "page.xhtml", "html", "a.html.bak")) {
            assertFalse(name, downloadsAsWord(DOCUMENTS_LIBRARY_ID, file(name)))
        }
        // The same name in any other library is somebody's web page: as is.
        assertFalse(downloadsAsWord(OTHER_LIBRARY_ID, file("Letter.html")))
        assertFalse(downloadsAsWord("core:music", file("Letter.html")))
        assertFalse(downloadsAsWord("plugin:notes", file("Letter.html")))
        // A folder (even one named like a document) comes back as the server's zip.
        assertFalse(downloadsAsWord(DOCUMENTS_LIBRARY_ID, FileEntry(name = "x.html", rel = "x.html", isDir = true)))
    }

    @Test fun exportPathEncodesEachSegmentLikeTheServerRoute() {
        val rel = "Letters/Notes for Gran/To Gran.html"
        val path = docWordExportPath(rel)
        assertEquals("/api/documents/export/doc/Letters/Notes%20for%20Gran/To%20Gran.html?fmt=docx", path)
        // The server's {rel_path:path} decodes back to the entry's rel exactly.
        assertEquals("/api/documents/export/doc/$rel", URI(path).path)
        assertEquals("fmt=docx", URI(path).rawQuery)

        // Characters that would end or split the path are encoded; "/" is never.
        val odd = "Q&A #2?/Café + tea.htm"
        val oddPath = docWordExportPath(odd)
        assertEquals("/api/documents/export/doc/Q%26A%20%232%3F/Caf%C3%A9%20%2B%20tea.htm?fmt=docx", oddPath)
        assertEquals("/api/documents/export/doc/$odd", URI(oddPath).path)
        assertEquals("fmt=docx", URI(oddPath).rawQuery)
    }

    @Test fun savedAsStemDotDocxWithWordsMime() {
        val plan = downloadPlan(DOCUMENTS_LIBRARY_ID, file("Letters/Notes for Gran/To Gran.html"))
        assertEquals("/api/documents/export/doc/Letters/Notes%20for%20Gran/To%20Gran.html?fmt=docx", plan.path)
        assertEquals("To Gran.docx", plan.fileName)
        assertEquals("application/vnd.openxmlformats-officedocument.wordprocessingml.document", plan.mime)
        // Only the last extension goes, as the server's Path.stem does.
        assertEquals("v1.2 plan.docx", wordSaveName("v1.2 plan.HTM"))
        // A name a file can't hold is scrubbed as every download's is.
        assertEquals("a b.docx", wordSaveName("a:b.html"))
    }

    @Test fun everythingElseDownloadsAsBefore() {
        val md = downloadPlan(DOCUMENTS_LIBRARY_ID, file("Notes/todo.md"))
        assertTrue(md.path, md.path.startsWith("/api/documents/raw/"))
        assertEquals("todo.md", md.fileName)
        assertNull(md.mime)

        val pdf = downloadPlan(DOCUMENTS_LIBRARY_ID, file("scan.pdf"))
        assertTrue(pdf.path.startsWith("/api/documents/raw/"))
        assertEquals("scan.pdf", pdf.fileName)
        assertNull(pdf.mime)

        val page = downloadPlan(OTHER_LIBRARY_ID, file("saved/page.html"))
        assertTrue(page.path, page.path.startsWith("/api/files/download?"))
        assertEquals("page.html", page.fileName)
        assertNull(page.mime)

        val folder = downloadPlan(DOCUMENTS_LIBRARY_ID, FileEntry(name = "Letters", rel = "Letters", isDir = true))
        assertTrue(folder.path.startsWith("/api/files/download?"))
        assertEquals("Letters.zip", folder.fileName)
    }

    /** A conversion the server refuses is reported in its words; nothing
     *  is saved in its place (the plan has no raw fallback to try). */
    @Test fun anExportErrorShowsTheServersDetail() {
        // Shaped as ServerFileTransfer.get throws it for a non-2xx answer.
        val body = """{"detail":"this document could not be converted"}"""
        val e = ApiException(422, "422 Unprocessable Entity: $body", body = body)
        assertEquals("download failed: this document could not be converted", ServerFileTransfer.downloadFailureText(e))

        val missing = """{"detail":"Letters/To Gran.html not found"}"""
        assertEquals(
            "download failed: Letters/To Gran.html not found",
            ServerFileTransfer.downloadFailureText(ApiException(404, "404 Not Found: $missing", body = missing)),
        )
        // No detail: the app's usual download-failed text.
        assertEquals(
            "download failed: 502 Bad Gateway:", // failureText trims the message
            ServerFileTransfer.downloadFailureText(ApiException(502, "502 Bad Gateway: ", body = "")),
        )
        assertEquals("download failed (offline?)", ServerFileTransfer.downloadFailureText(java.io.IOException("reset")))
    }
}
