package com.domovoi.app.audiobooks

import com.domovoi.app.player.ServerScope
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.util.Collections

/**
 * The download state machine: the Range rules alone, then whole books
 * against a stub server that serves files with Range the way the server's
 * audio route does — a dropped connection, a server that ignores Range, a
 * file that changed, a book that is gone.
 */
class BookFetchTest {
    private lateinit var server: MockWebServer
    private lateinit var root: File
    private lateinit var store: BookStore
    private val http = OkHttpClient()

    private val one = ByteArray(200_000) { (it * 7 % 251).toByte() }
    private val two = ByteArray(90_000) { (it % 13).toByte() }
    private val files = mapOf("01 One.mp3" to one, "02 Two.mp3" to two)
    private val requests = Collections.synchronizedList(mutableListOf<RecordedRequest>())

    /** Cut the next answer for [file] off after this many bytes. */
    @Volatile private var cutAfter: Pair<String, Int>? = null
    /** Answer every request with a full 200, ignoring Range. */
    @Volatile private var ignoreRange = false
    @Volatile private var gone = false

    @Before fun setUp() {
        root = Files.createTempDirectory("books").toFile()
        store = BookStore(root)
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request
                if (gone) return MockResponse().setResponseCode(404).setBody("""{"detail":"audiobook 7 not found"}""")
                val url = request.requestUrl!!
                if (url.encodedPath.endsWith("/cover")) {
                    return MockResponse().setHeader("Content-Type", "image/jpeg").setBody(Buffer().write(byteArrayOf(1, 2, 3)))
                }
                val name = url.queryParameter("file") ?: return MockResponse().setResponseCode(400)
                val bytes = files[name] ?: return MockResponse().setResponseCode(404)
                val range = request.getHeader("Range")?.removePrefix("bytes=")?.substringBefore('-')?.toLongOrNull()
                val start = if (ignoreRange) 0L else (range ?: 0L)
                if (start >= bytes.size) {
                    return MockResponse().setResponseCode(416).setHeader("Content-Range", "bytes */${bytes.size}")
                }
                val body = bytes.copyOfRange(start.toInt(), bytes.size)
                val r = MockResponse()
                if (range != null && !ignoreRange) {
                    r.setResponseCode(206).setHeader("Content-Range", "bytes $start-${bytes.size - 1}/${bytes.size}")
                }
                val cut = cutAfter
                if (cut != null && cut.first == name) {
                    cutAfter = null
                    // Send the headers for the whole body, then only part of
                    // it: the connection drops mid-file.
                    return r.setHeader("Content-Length", body.size)
                        .setBody(Buffer().write(body.copyOf(cut.second)))
                        .setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY)
                }
                return r.setBody(Buffer().write(body))
            }
        }
        server.start()
    }

    @After fun tearDown() {
        server.shutdown()
        root.deleteRecursively()
    }

    private fun book(withSizes: Boolean = true): SavedBook {
        val meta = SavedBookMeta(
            bookId = 7, serverAddress = "http://192.168.1.20:6369", title = "Dune", isFolder = true,
            files = listOf(
                SavedFile("01 One.mp3", "001-01 One.mp3", 0.0, 250.0, if (withSizes) one.size.toLong() else null),
                SavedFile("02 Two.mp3", "002-02 Two.mp3", 250.0, 350.0, if (withSizes) two.size.toLong() else null),
            ),
            coverPath = "/api/audiobooks/7/cover",
            sizeBytes = if (withSizes) (one.size + two.size).toLong() else null,
        )
        return store.create(meta) { "abcd" }
    }

    private fun url(path: String) = server.url(path).toString()

    private fun fetch(b: SavedBook, progress: MutableList<Long>? = null) = runBlocking {
        BookFetcher(http, store).fetch(store.read(b.key)!!, ::url) { done, _ -> progress?.add(done) }
    }

    private fun assertWhole(b: SavedBook) {
        assertArrayEquals(one, store.fileOf(b.key, "001-01 One.mp3").readBytes())
        assertArrayEquals(two, store.fileOf(b.key, "002-02 Two.mp3").readBytes())
        assertFalse(store.partOf(b.key, "001-01 One.mp3").exists())
        assertFalse(store.partOf(b.key, "002-02 Two.mp3").exists())
    }

    // ---- the rules alone ----------------------------------------------------

    @Test fun aRangeAnswerAtTheRightPlaceIsAppended() {
        assertEquals(RangeStep.Append, RangeRules.step(100, 1000, 206, "bytes 100-999/1000"))
    }

    @Test fun aRangeAnswerElsewhereOrForAnotherSizeRestarts() {
        assertEquals(RangeStep.Restart, RangeRules.step(100, 1000, 206, "bytes 0-999/1000"))
        assertEquals(RangeStep.Restart, RangeRules.step(100, 1000, 206, "bytes 100-1199/1200"))
        assertEquals(RangeStep.Restart, RangeRules.step(100, 1000, 206, null))
    }

    @Test fun aWholeFileAnswerRewrites() {
        assertEquals(RangeStep.Rewrite, RangeRules.step(0, 1000, 200, null))
        assertEquals(RangeStep.Rewrite, RangeRules.step(100, 1000, 200, null))
        assertEquals(RangeStep.Complete, RangeRules.step(1000, 1000, 200, null))
    }

    @Test fun pastTheEndIsCompleteOnlyWhenTheSizesAgree() {
        assertEquals(RangeStep.Complete, RangeRules.step(1000, 1000, 416, "bytes */1000"))
        assertEquals(RangeStep.Restart, RangeRules.step(1000, 1000, 416, "bytes */900"))
    }

    @Test fun aGoneBookIsRefusedAndAnOutageIsForLater() {
        assertEquals(RangeStep.Refused(404), RangeRules.step(0, 1000, 404, null))
        assertEquals(RangeStep.Refused(403), RangeRules.step(0, 1000, 403, null))
        assertEquals(RangeStep.TryLater(502), RangeRules.step(0, 1000, 502, null))
        assertEquals(RangeStep.TryLater(503), RangeRules.step(0, 1000, 503, null))
    }

    @Test fun contentRangeParses() {
        assertEquals(RangeRules.ContentRange(5, 9, 10), RangeRules.parseContentRange("bytes 5-9/10"))
        assertEquals(RangeRules.ContentRange(5, 9, null), RangeRules.parseContentRange("bytes 5-9/*"))
        assertEquals(RangeRules.ContentRange(-1, null, 10), RangeRules.parseContentRange("bytes */10"))
        assertNull(RangeRules.parseContentRange(null))
        assertNull(RangeRules.parseContentRange("bytes x-y/z"))
    }

    // ---- whole books ---------------------------------------------------------

    @Test fun aFolderBookDownloadsFileByFileWithItsCover() {
        val b = book()
        val progress = mutableListOf<Long>()
        assertEquals(BookFetchOutcome.Done, fetch(b, progress))
        assertWhole(b)
        assertEquals((one.size + two.size).toLong(), progress.last())
        assertTrue(progress.zipWithNext().all { (a, c) -> c >= a })
        assertEquals("cover.jpg", store.read(b.key)!!.meta.cover)
        // Every audio request named its file.
        assertTrue(requests.filter { "/audio?" in it.path!! }.all { "file=" in it.path!! })
    }

    @Test fun aDroppedConnectionKeepsThePartAndTheNextRunResumesIt() {
        val b = book()
        cutAfter = "02 Two.mp3" to 30_000
        val first = fetch(b)
        assertTrue("got $first", first is BookFetchOutcome.Retry)
        // The first file is whole; the second is a .part of what arrived.
        assertArrayEquals(one, store.fileOf(b.key, "001-01 One.mp3").readBytes())
        val part = store.partOf(b.key, "002-02 Two.mp3")
        assertTrue(part.isFile && part.length() in 1L until two.size)
        val have = part.length()
        requests.clear()

        assertEquals(BookFetchOutcome.Done, fetch(b))
        assertWhole(b)
        // Only the missing part was asked for, from where it stopped.
        val audio = requests.filter { "/audio?" in it.path!! }
        assertEquals(1, audio.size)
        assertEquals("bytes=$have-", audio.single().getHeader("Range"))
    }

    @Test fun aServerThatIgnoresRangeStillEndsWithTheWholeFile() {
        val b = book()
        cutAfter = "01 One.mp3" to 50_000
        assertTrue(fetch(b) is BookFetchOutcome.Retry)
        ignoreRange = true
        assertEquals(BookFetchOutcome.Done, fetch(b))
        assertWhole(b)
    }

    @Test fun aPartLargerThanTheFileStartsOver() {
        val b = book()
        store.partOf(b.key, "001-01 One.mp3").writeBytes(ByteArray(one.size + 10))
        assertEquals(BookFetchOutcome.Done, fetch(b))
        assertWhole(b)
    }

    @Test fun aWholePartLeftUnrenamedIsFinishedWithoutDownloading() {
        val b = book()
        store.partOf(b.key, "001-01 One.mp3").writeBytes(one)
        store.partOf(b.key, "002-02 Two.mp3").writeBytes(two)
        assertEquals(BookFetchOutcome.Done, fetch(b))
        assertWhole(b)
    }

    @Test fun aBookThatIsGoneFailsAndIsNotRetried() {
        val b = book()
        gone = true
        val out = fetch(b)
        assertEquals(BookFetchOutcome.Failed("this book is no longer on the server"), out)
    }

    @Test fun aServerOutOfReachIsARetry() {
        val b = book()
        server.shutdown()
        assertTrue(fetch(b) is BookFetchOutcome.Retry)
    }

    @Test fun withoutSizesTheBookStillDownloads() {
        val b = book(withSizes = false)
        assertEquals(BookFetchOutcome.Done, fetch(b))
        assertWhole(b)
    }

    @Test fun aBookTheFolderLostIsNotRecreated() {
        val b = book()
        store.remove(b.key)
        val out = runBlocking { BookFetcher(http, store).fetch(b, ::url) }
        assertTrue(out is BookFetchOutcome.Failed)
        assertFalse(store.dirOf(b.key).exists())
    }

    @Test fun notEnoughSpaceFailsBeforeAnyByte() {
        val tiny = store
        val meta = SavedBookMeta(
            bookId = 8, title = "Huge", files = listOf(SavedFile(null, "book.m4b", sizeBytes = Long.MAX_VALUE / 4)),
            sizeBytes = Long.MAX_VALUE / 4,
        )
        val b = tiny.create(meta)
        val out = runBlocking { BookFetcher(http, tiny).fetch(b, ::url) }
        assertEquals(BookFetchOutcome.Failed("not enough free space on this phone"), out)
        assertTrue(requests.isEmpty())
    }

    // ---- the store ------------------------------------------------------------

    @Test fun theStoreFindsABookByItsServerNotItsFolder() {
        val home = ServerScope("http://192.168.1.20:6369", "fp-home")
        val a = store.create(SavedBookMeta(bookId = 7, serverAddress = home.address, serverFingerprint = "fp-home", title = "A", savedAtMs = 1))
        store.create(SavedBookMeta(bookId = 7, serverAddress = "http://192.168.1.30:6369", serverFingerprint = "fp-other", title = "B", savedAtMs = 2))
        assertEquals(a.key, store.find(7, home)?.key)
        // The same server at a new address is still the same server.
        assertEquals(a.key, store.find(7, ServerScope("http://192.168.1.21:6369", "fp-home"))?.key)
        assertNull(store.find(8, home))
        assertEquals(listOf("B", "A"), store.list().map { it.meta.title })
    }

    @Test fun aFolderWithoutARecordIsNotABook() {
        File(root, "b99-zzzz").mkdirs()
        File(root, "other").mkdirs()
        assertTrue(store.list().isEmpty())
    }

    @Test fun removeOnlyTakesBookFolders() {
        val b = book()
        assertTrue(store.remove(b.key))
        assertFalse(store.dirOf(b.key).exists())
        for (bad in listOf("..", "b1/../..", "x", "b1\\..")) {
            try {
                store.remove(bad)
                throw AssertionError("removed $bad")
            } catch (e: IllegalArgumentException) {
                // refused
            }
        }
    }
}
