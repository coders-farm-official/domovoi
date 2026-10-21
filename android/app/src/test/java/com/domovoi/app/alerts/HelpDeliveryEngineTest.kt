package com.domovoi.app.alerts

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Help steps sent by voice, on the phone: only this phone's owner's rows
 * post, a first run posts nothing old, a row never posts twice (push, read,
 * both), a shared screen never posts, and each server keeps its own mark.
 */
class HelpDeliveryEngineTest {
    private class FakeSink : HelpSink {
        var can = true
        var isShared = false
        val posted = CopyOnWriteArrayList<Pair<String, HelpDelivery>>()
        override fun canPost() = can
        override fun shared() = isShared
        override fun post(serverKey: String, row: HelpDelivery) {
            posted += serverKey to row
        }
    }

    private class FakeBook : HelpSeenBook {
        val marks = HashMap<String, Long>()
        override suspend fun seen(serverKey: String): Long? = marks[serverKey]
        override suspend fun setSeen(serverKey: String, id: Long) {
            marks[serverKey] = id
        }
    }

    private lateinit var web: MockWebServer
    private val answers = HashMap<String, String>()
    private val paths = CopyOnWriteArrayList<String>()
    private val sink = FakeSink()
    private val book = FakeBook()
    private var owner: String? = "3"
    private lateinit var url: String
    private lateinit var engine: HelpDeliveryEngine

    @Before fun setUp() {
        web = MockWebServer()
        web.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path ?: ""
                paths += path
                return answers[path]?.let { MockResponse().setBody(it) }
                    ?: MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}""")
            }
        }
        web.start()
        url = web.url("/").toString().trimEnd('/')
        engine = HelpDeliveryEngine(ApiClient({ url }), book, sink, serverUrl = { url }, owner = { owner })
    }

    @After fun tearDown() {
        web.shutdown()
    }

    private fun row(id: Long, person: Long, thread: Long = 100 + id, title: String = "Help: add a room") =
        """{"id":$id,"person_id":$person,"thread_id":$thread,"title":"$title","created_at":"2026-10-21T10:00:00Z"}"""

    private fun push(vararg rows: String) = runBlocking {
        engine.onEvent(DomovoiJson.parseToJsonElement("[${rows.joinToString(",")}]"))
    }

    private val key get() = serverKey(url)

    @Test fun theFirstRunRemembersTheNewestAndPostsNothingOld() {
        push(row(7, 3), row(5, 3))
        assertEquals(emptyList<Any>(), sink.posted)
        assertEquals(7L, book.marks[key])
        // The next one for this phone's owner posts.
        push(row(8, 3), row(7, 3), row(5, 3))
        assertEquals(listOf(8L), sink.posted.map { it.second.id })
        assertEquals(key, sink.posted.single().first)
        assertEquals(8L, book.marks[key])
    }

    @Test fun onlyTheOwnersRowsPostAndEveryRowMovesTheMark() {
        book.marks[key] = 1
        push(row(4, 9), row(3, 3, thread = 42, title = "Help: rename a room"), row(2, 9))
        val posted = sink.posted.map { it.second }
        assertEquals(listOf(3L), posted.map { it.id })
        assertEquals(42L, posted.single().threadId)
        assertEquals("Help: rename a room", helpTitle(posted.single()))
        assertEquals("someone else's row moved the mark too", 4L, book.marks[key])
        // A new owner never gets what was already there.
        owner = "9"
        push(row(4, 9), row(3, 3), row(2, 9))
        assertEquals(1, sink.posted.size)
    }

    @Test fun aRowNeverPostsTwice() {
        book.marks[key] = 1
        push(row(2, 3))
        push(row(2, 3))
        answers[HelpDeliveriesApi.path(3, 2)] = """{"deliveries":[]}"""
        runBlocking { engine.refresh() }
        assertEquals(listOf(2L), sink.posted.map { it.second.id })
    }

    @Test fun theCatchUpReadAsksSinceTheMarkForTheOwner() {
        book.marks[key] = 5
        answers[HelpDeliveriesApi.path(3, 5)] = """{"deliveries":[${row(6, 3)},${row(7, 3)}]}"""
        runBlocking { engine.refresh() }
        assertEquals("/api/help/deliveries?person_id=3&since_id=5", paths.single())
        assertEquals(listOf(6L, 7L), sink.posted.map { it.second.id })
        assertEquals(7L, book.marks[key])
    }

    @Test fun aFirstCatchUpReadOnlySetsTheMark() {
        answers[HelpDeliveriesApi.path(3, null)] = """{"deliveries":[${row(9, 3)},${row(6, 3)}]}"""
        runBlocking { engine.refresh() }
        assertEquals("/api/help/deliveries?person_id=3", paths.single())
        assertEquals(emptyList<Any>(), sink.posted)
        assertEquals(9L, book.marks[key])
    }

    @Test fun noOwnerAsksNothingAndPostsNothing() {
        owner = null
        runBlocking { engine.refresh() }
        assertEquals(emptyList<String>(), paths)
        book.marks[key] = 1
        push(row(2, 3))
        assertEquals(emptyList<Any>(), sink.posted)
        assertEquals(2L, book.marks[key])
    }

    @Test fun aSharedScreenNeverPosts() {
        book.marks[key] = 1
        sink.isShared = true
        push(row(2, 3))
        assertEquals(emptyList<Any>(), sink.posted)
        assertEquals("nothing piles up for later", 2L, book.marks[key])
        sink.isShared = false
        push(row(2, 3))
        assertEquals(emptyList<Any>(), sink.posted)
    }

    @Test fun withNotificationsOffTheRowWaits() {
        book.marks[key] = 1
        sink.can = false
        push(row(2, 3))
        assertEquals(emptyList<Any>(), sink.posted)
        assertEquals(1L, book.marks[key])
        sink.can = true
        push(row(2, 3))
        assertEquals(listOf(2L), sink.posted.map { it.second.id })
    }

    @Test fun eachServerKeepsItsOwnMark() {
        book.marks[key] = 1
        push(row(2, 3))
        val other = MockWebServer().also { it.start() }
        try {
            val otherUrl = other.url("/").toString().trimEnd('/')
            url = otherUrl
            push(row(2, 3))
            assertEquals("the other server's first run", 1, sink.posted.size)
            assertEquals(2L, book.marks[serverKey(otherUrl)])
        } finally {
            other.shutdown()
        }
    }

    @Test fun parsingAndTitles() {
        assertNull(parseDeliveries(DomovoiJson.parseToJsonElement("""{"detail":"x"}""")))
        val rows = parseDeliveries(DomovoiJson.parseToJsonElement("""[${row(1, 3)},{"id":"bad"}]"""))!!
        assertEquals(1, rows.size)
        assertEquals(HelpDelivery(1, 3, 101, "Help: add a room", "2026-10-21T10:00:00Z"), rows.single())
        assertEquals("Help: add a room", helpTitle(rows.single().copy(title = "add a room")))
        assertEquals("Help from Domovoi", helpTitle(rows.single().copy(title = " ")))
        assertTrue(planHelp(emptyList(), 3, null, false).post.isEmpty())
        assertNull(planHelp(emptyList(), 3, null, false).seen)
    }
}
