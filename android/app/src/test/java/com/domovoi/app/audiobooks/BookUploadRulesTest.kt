package com.domovoi.app.audiobooks

import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayKind
import com.domovoi.app.player.ServerScope
import com.domovoi.app.player.ServerSwitch
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files

/** "Add to server" rules, and saved books as the player sees them. */
class BookUploadRulesTest {
    private lateinit var root: File

    @Before fun setUp() {
        root = Files.createTempDirectory("books").toFile()
    }

    @After fun tearDown() {
        root.deleteRecursively()
    }

    @Test fun audioIsKnownByTypeOrExtension() {
        assertTrue(BookUploadRules.isAudio("x.m4b", "application/octet-stream"))
        assertTrue(BookUploadRules.isAudio("noext", "audio/mpeg"))
        assertTrue(BookUploadRules.isAudio("Chapter 1.MP3", null))
        assertFalse(BookUploadRules.isAudio("cover.jpg", "image/jpeg"))
        assertFalse(BookUploadRules.isAudio("notes.txt", null))
    }

    @Test fun aFoldersFilesGoUpInTheOrderAPersonNumberedThem() {
        val names = listOf("10 End.mp3", "2 Middle.mp3", "1 Start.mp3", "02b Aside.mp3")
        assertEquals(
            listOf("1 Start.mp3", "2 Middle.mp3", "02b Aside.mp3", "10 End.mp3"),
            names.sortedWith(BookUploadRules.naturalOrder),
        )
    }

    @Test fun onlyTheFirstFileOfAFolderMakesTheFolder() {
        val first = BookUploadRules.fields("android-1a2b", "Dune", 0).toMap()
        assertEquals("core:audiobooks", first["library_id"])
        assertEquals("Dune", first["path"])
        assertEquals("android-1a2b", first["device_id"])
        assertEquals("true", first["create_dir"])
        assertNull(BookUploadRules.fields("android-1a2b", "Dune", 1).toMap()["create_dir"])
        val single = BookUploadRules.fields("android-1a2b", null, 0).toMap()
        assertEquals("", single["path"])
        assertNull(single["create_dir"])
    }

    @Test fun aFolderNameIsOneTheServerKeepsAsItIs() {
        assertEquals("Dune_ Part 1", BookUploadRules.folderName("Dune: Part 1"))
        assertEquals("a_b", BookUploadRules.folderName("a/b"))
        assertEquals("audiobook", BookUploadRules.folderName("..."))
        assertEquals("Two words", BookUploadRules.folderName("Two   words  "))
    }

    // ---- saved books in the player --------------------------------------------------

    private fun savedBook(complete: Boolean): Pair<BookStore, SavedBook> {
        val store = BookStore(root)
        val b = store.create(
            SavedBookMeta(
                bookId = 7, serverAddress = "http://192.168.1.20:6369", serverFingerprint = "fp", title = "Dune",
                isFolder = true, complete = complete,
                files = listOf(SavedFile("a.mp3", "001-a.mp3", 0.0, 100.0), SavedFile("b.mp3", "002-b.mp3", 100.0, 50.0)),
                chapters = listOf(SavedChapter("One", 0.0), SavedChapter("Two", 100.0)),
            ),
        )
        return store to b
    }

    @Test fun aSavedBookPlaysFromItsOwnFilesWithItsChapters() {
        val (store, b) = savedBook(complete = true)
        val item = BookItems.saved(b, store)!!
        assertEquals(PlayKind.Audiobook, item.kind)
        assertEquals(7L, item.id)
        assertTrue(item.src.startsWith("file:"))
        assertEquals(2, item.parts.size)
        assertTrue(item.parts.all { it.src.startsWith("file:") && store.dirOf(b.key).name in it.src })
        assertEquals(listOf("One", "Two"), item.chapters.map { it.title })
        assertEquals(b.key, item.savedKey)
        assertEquals(ServerScope("http://192.168.1.20:6369", "fp"), item.origin)
    }

    @Test fun anUnfinishedSaveIsNotPlayable() {
        val (store, b) = savedBook(complete = false)
        assertNull(BookItems.saved(b, store))
    }

    /** A saved book is the phone's own files: a move to another server
     *  leaves it in the queue, as it leaves the phone's songs. */
    @Test fun aSavedBookStaysWhenTheServerChanges() {
        val (store, b) = savedBook(complete = true)
        val saved = BookItems.saved(b, store)!!
        val streamed = PlayItem.fromBook(9, "Other", null, null, null, emptyList())
        assertFalse(ServerSwitch.fromServer(saved))
        assertTrue(ServerSwitch.fromServer(streamed))
        val plan = ServerSwitch.plan(listOf(streamed, saved), 1)
        assertEquals(listOf(saved), plan.kept)
        assertFalse(plan.currentGoes)
    }
}
