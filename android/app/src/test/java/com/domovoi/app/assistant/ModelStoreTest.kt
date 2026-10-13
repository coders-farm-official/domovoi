package com.domovoi.app.assistant

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

/** Where the models live: filesDir/models/<id>/, installed only when every
 *  file is whole and the marker names this catalog's files. */
class ModelStoreTest {
    private val root: File = Files.createTempDirectory("models").toFile()
    private val store = ModelStore(root)

    private val model = CatalogModel(
        "test-llm", "Test", ModelKind.LLM,
        listOf(ModelFile("a.bin", "https://example.org/a.bin", 100, "0".repeat(64)),
            ModelFile("sub/b.txt", "https://example.org/b.txt", 50, "1".repeat(64))),
        "MIT", "https://example.org/license",
    )

    @After fun tearDown() {
        root.deleteRecursively()
    }

    private fun put(path: String, bytes: Int) = File(store.dir(model), path).apply { parentFile!!.mkdirs(); writeBytes(ByteArray(bytes)) }

    @Test fun installedOnlyWithEveryFileWholeAndTheMarker() {
        assertFalse(store.isInstalled(model))
        put("a.bin", 100)
        put("sub/b.txt", 50)
        assertFalse("no marker yet", store.isInstalled(model))
        store.markInstalled(model)
        assertTrue(store.isInstalled(model))
        put("sub/b.txt", 49)
        assertFalse("a file of the wrong size", store.isInstalled(model))
    }

    @Test fun bytesDoneCountsWholeFilesAndPartials() {
        assertEquals(0, store.bytesDone(model))
        put("a.bin", 100)
        put("sub/b.txt.part", 20)
        assertEquals(120, store.bytesDone(model))
        assertEquals(120, ModelDownloader(okhttp3.OkHttpClient()).bytesDone(model, store))
    }

    @Test fun deletingFreesEverythingTheModelHeld() {
        put("a.bin", 100)
        put("sub/b.txt", 50)
        store.markInstalled(model)
        put("a.bin.part", 30)
        assertTrue(store.bytesOnDisk(model) >= 180)
        assertTrue(store.delete(model))
        assertFalse(store.dir(model).exists())
        assertEquals(0, store.bytesOnDisk(model))
        assertFalse(store.isInstalled(model))
        assertTrue(store.delete(model))   // nothing there: fine
    }

    @Test fun deletePartialsKeepsVerifiedFiles() {
        put("a.bin", 100)
        put("sub/b.txt.part", 20)
        store.deletePartials(model)
        assertTrue(File(store.dir(model), "a.bin").isFile)
        assertFalse(File(store.dir(model), "sub/b.txt.part").exists())
    }

    @Test fun freeSpaceIsReadWhereTheModelsGo() {
        File(root, "gone").let { sub ->
            val s = ModelStore(sub)
            assertTrue(s.usableBytes() > 0)
            assertTrue("the directory is made to measure it", sub.isDirectory)
        }
    }
}
