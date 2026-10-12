package com.domovoi.app.assistant

import com.domovoi.app.assistant.ModelDownloader.Companion.toHex
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.security.MessageDigest

class ModelDownloaderTest {
    private lateinit var server: MockWebServer
    private lateinit var dir: File
    private val http = OkHttpClient()

    private val bytesA = ByteArray(300_000) { (it * 7 % 251).toByte() }
    private val bytesB = ByteArray(1_000) { (it % 13).toByte() }

    @Before fun setUp() {
        server = MockWebServer()
        dir = Files.createTempDirectory("models").toFile()
    }

    @After fun tearDown() {
        server.shutdown()
        dir.deleteRecursively()
    }

    private fun sha(b: ByteArray) = MessageDigest.getInstance("SHA-256").digest(b).toHex()

    private fun file(path: String, bytes: ByteArray, sha256: String = sha(bytes)) =
        ModelFile(path, server.url("/$path").toString(), bytes.size.toLong(), sha256)

    private fun model(vararg files: ModelFile) =
        CatalogModel("test-model", "Test", ModelKind.STT, files.toList(), "MIT", "https://example.org/license")

    /** Serves [content] by path, honouring Range unless [ignoreRange]. */
    private fun serve(content: Map<String, ByteArray>, ignoreRange: Boolean = false) {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = content[request.path!!.removePrefix("/")] ?: return MockResponse().setResponseCode(404)
                val range = request.getHeader("Range")
                if (range != null && !ignoreRange) {
                    val start = range.removePrefix("bytes=").substringBefore('-').toInt()
                    return MockResponse().setResponseCode(206)
                        .setHeader("Content-Range", "bytes $start-${body.size - 1}/${body.size}")
                        .setBody(Buffer().write(body.copyOfRange(start, body.size)))
                }
                return MockResponse().setBody(Buffer().write(body))
            }
        }
    }

    @Test fun installsVerifiesAndMarksTheModel() = runBlocking {
        serve(mapOf("a.bin" to bytesA, "sub/b.txt" to bytesB))
        val m = model(file("a.bin", bytesA), file("sub/b.txt", bytesB))
        val store = ModelStore(dir)
        val progress = mutableListOf<Pair<Long, Long>>()
        ModelDownloader(http).install(m, store) { done, total -> progress += done to total }

        assertTrue(store.isInstalled(m))
        assertArrayEquals(bytesA, store.fileFor(m, m.files[0]).readBytes())
        assertArrayEquals(bytesB, File(dir, "test-model/sub/b.txt").readBytes())
        assertFalse(ModelDownloader.partOf(store.fileFor(m, m.files[0])).exists())
        assertEquals(m.totalBytes to m.totalBytes, progress.last())
        assertTrue(progress.zipWithNext().all { (a, b) -> b.first >= a.first })
    }

    @Test fun anInstalledModelIsNotFetchedAgain() = runBlocking {
        serve(mapOf("a.bin" to bytesA))
        val m = model(file("a.bin", bytesA))
        val store = ModelStore(dir)
        ModelDownloader(http).install(m, store)
        val before = server.requestCount
        ModelDownloader(http).install(m, store)
        assertEquals(before, server.requestCount)
    }

    @Test fun aHashMismatchDeletesThePartialFileRetriesOnceThenFails() = runBlocking {
        serve(mapOf("a.bin" to bytesA))
        val m = model(file("a.bin", bytesA, sha256 = "0".repeat(64)))
        val store = ModelStore(dir)
        try {
            ModelDownloader(http).install(m, store)
            fail("expected a checksum failure")
        } catch (e: ModelDownloader.HashMismatch) {
            assertEquals("a.bin", e.path)
        }
        assertEquals(2, server.requestCount)
        val target = store.fileFor(m, m.files[0])
        assertFalse(target.exists())
        assertFalse(ModelDownloader.partOf(target).exists())
        assertFalse(store.isInstalled(m))
    }

    @Test fun aPartialFileResumesWithARangeRequest() = runBlocking {
        serve(mapOf("a.bin" to bytesA))
        val m = model(file("a.bin", bytesA))
        val store = ModelStore(dir)
        val target = store.fileFor(m, m.files[0])
        target.parentFile!!.mkdirs()
        ModelDownloader.partOf(target).writeBytes(bytesA.copyOfRange(0, 120_000))

        ModelDownloader(http).install(m, store)

        assertEquals("bytes=120000-", server.takeRequest().getHeader("Range"))
        assertArrayEquals(bytesA, target.readBytes())
        assertTrue(store.isInstalled(m))
    }

    @Test fun aHostThatIgnoresRangeRestartsTheFile() = runBlocking {
        serve(mapOf("a.bin" to bytesA), ignoreRange = true)
        val m = model(file("a.bin", bytesA))
        val store = ModelStore(dir)
        val target = store.fileFor(m, m.files[0])
        target.parentFile!!.mkdirs()
        // A stale partial that does not even match: a 200 must replace it.
        ModelDownloader.partOf(target).writeBytes(ByteArray(50_000) { 9 })

        ModelDownloader(http).install(m, store)

        assertArrayEquals(bytesA, target.readBytes())
        assertTrue(store.isInstalled(m))
    }

    @Test fun aCompletePartialFileIsVerifiedWithoutARequest() = runBlocking {
        serve(mapOf("a.bin" to bytesA))
        val m = model(file("a.bin", bytesA))
        val store = ModelStore(dir)
        val target = store.fileFor(m, m.files[0])
        target.parentFile!!.mkdirs()
        ModelDownloader.partOf(target).writeBytes(bytesA)

        ModelDownloader(http).install(m, store)

        assertEquals(0, server.requestCount)
        assertTrue(store.isInstalled(m))
    }

    @Test fun anHttpErrorKeepsNothingInstalled() = runBlocking {
        serve(emptyMap())
        val m = model(file("a.bin", bytesA))
        val store = ModelStore(dir)
        try {
            ModelDownloader(http).install(m, store)
            fail("expected an HTTP failure")
        } catch (e: java.io.IOException) {
            assertTrue(e.message!!.contains("404"))
        }
        assertFalse(store.isInstalled(m))
    }

    @Test fun notEnoughSpaceFailsBeforeAnyRequest() = runBlocking {
        serve(mapOf("a.bin" to bytesA))
        val huge = ModelFile("a.bin", server.url("/a.bin").toString(), Long.MAX_VALUE / 4, "0".repeat(64))
        try {
            ModelDownloader(http).install(model(huge), ModelStore(dir))
            fail("expected a space check failure")
        } catch (e: ModelDownloader.NotEnoughSpace) {
            assertTrue(e.needed > e.available)
        }
        assertEquals(0, server.requestCount)
    }

    @Test fun aCatalogChangeReadsAsNotInstalled() = runBlocking {
        serve(mapOf("a.bin" to bytesA))
        val m = model(file("a.bin", bytesA))
        val store = ModelStore(dir)
        ModelDownloader(http).install(m, store)
        val newer = m.copy(files = listOf(m.files[0].copy(url = m.files[0].url + "?v=2", sha256 = "f".repeat(64))))
        assertFalse(store.isInstalled(newer))
        assertTrue(store.delete(m))
        assertFalse(store.dir(m).exists())
    }

    @Test fun contentRangeStartIsParsed() {
        assertEquals(100L, ModelDownloader.contentRangeStart("bytes 100-999/1000"))
        assertEquals(0L, ModelDownloader.contentRangeStart("bytes 0-9/10"))
        assertNull(ModelDownloader.contentRangeStart(null))
        assertNull(ModelDownloader.contentRangeStart("bytes */1000"))
    }
}
