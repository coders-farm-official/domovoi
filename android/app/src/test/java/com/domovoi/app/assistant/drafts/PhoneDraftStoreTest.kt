package com.domovoi.app.assistant.drafts

import com.domovoi.app.ui.screens.chat.DraftVersionView
import com.domovoi.app.ui.screens.chat.changesOnFor
import com.domovoi.app.ui.screens.chat.firstLinesOf
import com.domovoi.app.ui.screens.chat.sourceLabel
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * A phone chat's drafts on disk (PhoneDraftStore), the "Chat documents"
 * library, and the canvas's small rules: every version kept with what made
 * it, a hand edit as a version "edited by you", saved files recorded on
 * their version, the chat's drafts gone with the chat, unique file names
 * named as the server names them, and changes shown for an unseen version.
 */
class PhoneDraftStoreTest {
    @get:Rule val tmp = TemporaryFolder()

    private val conv = "c-1792600000000-abc"

    @Test fun everyVersionIsKeptWithWhatMadeIt() {
        val s = PhoneDraftStore(tmp.newFolder("drafts"))
        val d = s.create(conv, "cover_letter", "letter-block", "Cover letter for Acme", "Dear Ms. Lee,\n\nHello.", 1000)
        assertEquals(1, d.latest)
        s.addVersion(conv, d.id, "Dear Ms. Lee,\n\nHi.", "shorter", "model", 2000)
        val e = s.addVersion(conv, d.id, "Dear Ms. Lee,\n\nHi there.", null, "person", 3000)!!
        assertEquals(listOf("first", "revision", "edited"), e.versions.map { it.source })
        assertEquals("Dear Ms. Lee,\n\nHi.", s.body(conv, d.id, 2))
        val (latest, body) = s.latest(conv)!!
        assertEquals(3, latest.latest)
        assertEquals("Dear Ms. Lee,\n\nHi there.", body)
        val saved = s.recordSave(conv, d.id, 2, SavedDraftFile("pdf", "Cover letter for Acme v2.pdf", "phone", DraftAts.PASSED, true, 4000))!!
        assertEquals("Cover letter for Acme v2.pdf", saved.versions[1].saved.single().name)
        assertTrue(s.delete(conv))
        assertNull(s.latest(conv))
        assertNull(s.addVersion(conv, d.id, "x", null, "person", 5000))
    }

    @Test fun theNewestDraftOfTheChatIsTheOneRevised() {
        val s = PhoneDraftStore(tmp.newFolder("drafts"))
        s.create(conv, "resume", "resume-plain", "Resume", "# A", 1000)
        val second = s.create(conv, "letter", "letter-personal", "Letter to my landlord", "Dear Sam,", 2000)
        assertEquals(second.id, s.latest(conv)!!.first.id)
    }

    @Test fun aPathInAnIdReadsNothing() {
        val root = tmp.newFolder("drafts")
        val s = PhoneDraftStore(root)
        assertNull(s.load(conv, "dr-1"))
        assertNull(s.load("../escape", "dr-1"))
        assertNull(s.body(conv, "../../x", 1))
        try {
            s.create("../escape", "letter", "letter-block", "x", "y", 1)
            throw AssertionError("a path in a conversation id was taken")
        } catch (e: IllegalArgumentException) {
            assertNotNull(e.message)
        }
        assertTrue(root.listFiles().orEmpty().isEmpty())
    }

    @Test fun chatDocumentsNamesAsTheServerAndNeverOverwrites() {
        assertEquals("Cover letter for the warehouse lead job at Acme Logistics v3.pdf",
            ChatDocumentsLibrary.fileName("Cover letter for the warehouse lead job at Acme Logistics", 3, "pdf"))
        assertEquals("Letter to my landlord v3.pdf", ChatDocumentsLibrary.fileName("Letter to: my/landlord?", 3, "pdf"))
        assertEquals("Draft v3.pdf", ChatDocumentsLibrary.fileName("  ...  ", 3, "pdf"))
        assertEquals("A".repeat(100) + " v3.pdf", ChatDocumentsLibrary.fileName("A".repeat(130), 3, "pdf"))
        val lib = ChatDocumentsLibrary(tmp.newFolder("Chat documents"))
        val a = lib.save("Letter v1.pdf", byteArrayOf(1))
        val b = lib.save("Letter v1.pdf", byteArrayOf(2))
        assertEquals("Letter v1.pdf", a.name)
        assertEquals("Letter v1 (1).pdf", b.name)
        assertEquals(2, lib.files().size)
        assertNull(lib.file("../x.pdf"))
        assertTrue(lib.delete("Letter v1.pdf"))
        assertFalse(lib.delete("Letter v1.pdf"))
    }

    @Test fun theCanvasRules() {
        assertFalse(changesOnFor(1, 0))
        assertTrue(changesOnFor(3, 2))
        assertFalse(changesOnFor(2, 3))
        assertEquals("first draft", sourceLabel(DraftVersionView(1, "", "", null, "first", null)))
        assertEquals("edited by you", sourceLabel(DraftVersionView(4, "", "", null, "edited", null)))
        assertEquals("“shorter”", sourceLabel(DraftVersionView(2, "", "", null, "revision", "shorter")))
        assertEquals("Jordan Avery\nToledo, OH\nSummary", firstLinesOf("# Jordan Avery\nToledo, OH\n\n## Summary\nWorks."))
    }
}
