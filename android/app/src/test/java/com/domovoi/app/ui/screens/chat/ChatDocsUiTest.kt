package com.domovoi.app.ui.screens.chat

import com.domovoi.app.assistant.docs.DocNote
import com.domovoi.app.assistant.docs.DocPart
import com.domovoi.app.ui.screens.files.FileEntry
import com.domovoi.app.ui.screens.files.askable
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The pieces of documents in Chat that are plain logic: the sources'
 *  order and marks, the server's fields as the app reads them, and where
 *  "Ask about this document" is offered. */
class ChatDocsUiTest {
    private fun src(label: String, cited: Boolean) = SourceView(label, "The deposit is \$1,500. It comes back.", "It comes back.", cited, 3)

    @Test fun citedSourcesComeFirstAndUncitedOnesSaySo() {
        val list = listOf(src("p. 1", false), src("p. 3", true), src("p. 2", false))
        assertEquals(listOf("p. 3", "p. 1", "p. 2"), orderedSources(list).map { it.label })
        assertEquals("p. 3", sourceChipText(list[1]))
        assertEquals("p. 1 · read", sourceChipText(list[0]))
    }

    @Test fun theMatchedSentenceIsFoundInItsPassage() {
        assertEquals(23 until 37, matchRange("The deposit is \$1,500. It comes back.", "It comes back."))
        assertNull(matchRange("abc", "xyz"))
        assertNull(matchRange("abc", " "))
    }

    @Test fun theServersDocumentFieldsRead() {
        val json = Json { ignoreUnknownKeys = true }
        val row = json.decodeFromString(
            MessageRow.serializer(),
            """{"id":5,"role":"assistant","content":"x [p. 3]","doc":{"mode":"question","found":true,
               "sources":[{"token":"ab","name":"lease.pdf","label":"p. 3","page":3,"text":"t","match":"t","cited":true}],
               "notes":[{"code":"uncited","text":"n"}]},"documents":null}""",
        )
        assertEquals("p. 3", row.doc!!.sources.single().label)
        assertEquals(listOf(DocNote("uncited", "n")), row.doc!!.notes)
        val sources = serverSources(row.doc) { null }
        assertEquals(3, sources.single().page)
        val ex = json.decodeFromString(
            ServerExtract.serializer(),
            """{"kind":"pdf","pages":2,"parts":[{"label":"p. 1","page":1,"text":"hi","head":""}],"scanned_pages":[2],"words":1,"chars":2,"notes":[]}""",
        ).toExtraction()
        assertEquals(listOf(DocPart("p. 1", "hi", "", 1)), ex.parts)
        assertEquals(listOf(2), ex.scannedPages)
        assertTrue(ex.readByServer)
    }

    @Test fun askIsOfferedForReadableFilesWhereTheirPlaceCanChat() {
        val pdf = FileEntry(name = "lease.pdf", rel = "lease.pdf")
        val song = FileEntry(name = "song.mp3", rel = "song.mp3")
        val dir = FileEntry(name = "docs.pdf", rel = "docs.pdf", isDir = true)
        assertTrue(askable(pdf, phoneChats = true, serverChats = false))
        assertTrue(askable(pdf, phoneChats = false, serverChats = true))
        assertFalse(askable(pdf, phoneChats = false, serverChats = false))
        assertFalse(askable(song, phoneChats = true, serverChats = true))
        assertFalse(askable(dir, phoneChats = true, serverChats = true))
    }

    @Test fun askingTheServerSaysWhatHappensFirst() {
        assertEquals("Sends lease.pdf to your home server to read its text. Nothing is kept there.", askServerText("lease.pdf"))
    }
}
