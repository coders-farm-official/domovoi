package com.domovoi.app.ui.screens.files

import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

/** Files → Documents → New: the dashboard's "+ New", as far as the app opens in-app. */
class NewDocumentTest {

    @Test fun offersOnlyWhatOpensInTheApp() {
        // The dashboard's kinds are doc/sheet/drawing/text; the app has no
        // drawing editor, so Drawing is not offered (no control that opens nowhere).
        assertEquals(listOf("doc", "sheet", "text"), NewDocKind.entries.map { it.api })
        assertEquals(listOf("Document", "Spreadsheet", "Text file"), NewDocKind.entries.map { it.label })
    }

    @Test fun bodyCarriesNameKindAndDevice() {
        val b = newDocumentBody("  Budget ", NewDocKind.Spreadsheet, "android-1a2b")
        assertEquals("Budget", b["name"]!!.jsonPrimitive.content)
        assertEquals("sheet", b["kind"]!!.jsonPrimitive.content)
        assertEquals("android-1a2b", b["device_id"]!!.jsonPrimitive.content)
        // An id the server's model would refuse (3..64 chars) is left out, not sent.
        assertFalse(newDocumentBody("x", NewDocKind.Text, "").containsKey("device_id"))
    }

    @Test fun createdFileOpensAsItsRowWould() {
        val doc = CreatedDocument(relPath = "Letter.html", category = "doc").asEntry()
        assertEquals("Letter.html", doc.name)
        assertEquals("Letter.html", doc.rel)
        assertFalse(doc.isDir)
        val sheet = CreatedDocument(relPath = "Budget.xlsx", category = "sheet").asEntry()
        assertEquals(OpenWith.SheetEditor, openWith(sheet.name, sheet.kind))
        val text = CreatedDocument(relPath = "shopping.txt").asEntry()
        assertEquals(OpenWith.TextEditor, openWith(text.name, text.kind))
    }

    @Test fun refusalsInPlainWords() {
        assertEquals(
            "There's already a file called \"Budget.xlsx\". Pick another name.",
            createFailureMessage(409, "Budget.xlsx already exists"),
        )
        assertEquals(
            "A file with that name already exists. Pick another name.",
            createFailureMessage(409, null),
        )
        // Any other refusal is the server's own sentence (name rules, blocked device).
        assertEquals("empty file name", createFailureMessage(400, "empty file name"))
        assertEquals("This device can't make files in Documents.", createFailureMessage(403, null))
        assertEquals("Couldn't make the file.", createFailureMessage(500, " "))
    }

    @Test fun nothingNullLeaks() {
        assertNull(CreatedDocument(relPath = "a.txt").asEntry().lockedBy)
    }
}
