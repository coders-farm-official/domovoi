package com.domovoi.app.audiobooks

import com.domovoi.app.testing.awaitUntil
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

/**
 * Batch 18's finding (2026-10-20): "remove from phone" on the your-device
 * side left the Domovoi side showing the "on this phone" badge and "remove
 * from phone" until Audiobooks was left, because each side read the disk for
 * itself. Both now read the one [SavedShelf], which re-reads whenever the
 * store changes — whichever store instance made the change.
 */
class SavedShelfTest {
    private val root: File = Files.createTempDirectory("shelf").toFile()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    @After fun tearDown() {
        scope.cancel()
        root.deleteRecursively()
    }

    private fun meta(id: Long, title: String) =
        SavedBookMeta(bookId = id, serverAddress = "http://192.168.1.20:6369", title = title, savedAtMs = id, complete = true)

    private fun SavedShelf.titles() = state.value.books.map { it.meta.title }

    @Test fun everyStoreOnTheFolderSharesOneChangeSignal() {
        val app = BookStore(root)
        val job = BookStore(root) // the download job keeps its own
        val other = BookStore(Files.createTempDirectory("elsewhere").toFile())
        val before = app.changes.value
        val otherBefore = other.changes.value

        val book = job.create(meta(7, "Dune"))
        assertEquals(before + 1, app.changes.value)
        job.write(book.key, book.meta.copy(cover = "cover.jpg"))
        assertEquals(before + 2, app.changes.value)
        app.remove(book.key)
        assertEquals(before + 3, job.changes.value)
        assertEquals("another folder's signal moved", otherBefore, other.changes.value)
    }

    @Test fun aRemovalShowsOnEverySideAtOnceWithNoRefresh() {
        val store = BookStore(root)
        val dune = store.create(meta(7, "Dune"))
        store.create(meta(8, "Emma"))
        val shelf = SavedShelf(store, scope, Dispatchers.Default)
        awaitUntil(what = "the first read") { shelf.state.value.loaded && shelf.titles().size == 2 }

        // The your-device side and the Domovoi side both read the shelf.
        val deviceSide = shelf.state
        val domovoiSide = shelf.state

        // "remove from phone" on the your-device side: the store, nothing else.
        store.remove(dune.key)

        awaitUntil(what = "the removal on the shelf") { shelf.titles() == listOf("Emma") }
        assertTrue(deviceSide.value.books.none { it.meta.bookId == 7L })
        // The Domovoi side's badge reads the same state: no copy of book 7.
        val server = dune.meta.server
        assertTrue(domovoiSide.value.books.none { it.meta.bookId == 7L && it.meta.server.isSameServer(server) })
        assertTrue(dune.key !in deviceSide.value.bytes)
    }

    @Test fun aBookTheDownloadJobFinishesShowsAsSaved() {
        val app = BookStore(root)
        val job = BookStore(root)
        val book = job.create(meta(9, "Kim").copy(complete = false))
        val shelf = SavedShelf(app, scope, Dispatchers.Default)
        awaitUntil(what = "the first read") { shelf.state.value.books.singleOrNull()?.meta?.complete == false }

        job.write(book.key, book.meta.copy(complete = true))

        awaitUntil(what = "the finished record on the shelf") { shelf.state.value.books.singleOrNull()?.meta?.complete == true }
        assertEquals(SaveState.Saved, SaveStates.of(shelf.state.value.books.single().meta, null))
    }
}
