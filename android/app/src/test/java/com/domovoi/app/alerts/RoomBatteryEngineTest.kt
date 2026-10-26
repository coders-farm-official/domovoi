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
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

/**
 * A phone room's low battery on the household's phones: one notification
 * per room, the 10 % warning replacing the 20 % one, never twice for the
 * same warning (push, read, both), taken back once the room is charging,
 * and held while notifications are off.
 */
class RoomBatteryEngineTest {
    private class FakeSink : RoomBatterySink {
        var can = true
        val posted = CopyOnWriteArrayList<Pair<String, RoomBatteryWarning>>()
        val withdrawn = CopyOnWriteArrayList<Pair<String, String>>()
        override fun canPost() = can
        override fun post(serverKey: String, warning: RoomBatteryWarning) {
            posted += serverKey to warning
        }
        override fun withdraw(serverKey: String, roomId: String) {
            withdrawn += serverKey to roomId
        }
    }

    private class FakeBook : RoomBatteryBook {
        val books = HashMap<String, Map<String, Long>>()
        override suspend fun posted(serverKey: String) = books[serverKey].orEmpty()
        override suspend fun setPosted(serverKey: String, book: Map<String, Long>) {
            books[serverKey] = book
        }
    }

    private lateinit var web: MockWebServer
    private var answer: String? = null
    private val sink = FakeSink()
    private val book = FakeBook()
    private lateinit var url: String
    private lateinit var engine: RoomBatteryEngine

    @Before fun setUp() {
        web = MockWebServer()
        web.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse =
                if (request.path == RoomBatteryApi.PATH && answer != null) MockResponse().setBody(answer!!)
                else MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}""")
        }
        web.start()
        url = web.url("/").toString().trimEnd('/')
        engine = RoomBatteryEngine(ApiClient({ url }), book, sink, serverUrl = { url })
    }

    @After fun tearDown() {
        web.shutdown()
    }

    private fun w(id: Long, room: String = "pako", threshold: Int = 20, percent: Int = 19) =
        """{"id":$id,"room_id":"$room","name":"${room.replaceFirstChar { it.uppercase() }}","threshold":$threshold,""" +
            """"percent":$percent,"text":"${room.replaceFirstChar { it.uppercase() }} is at $percent%. Plug it in.","created_at":"2026-10-25T10:00:00Z"}"""

    private fun push(vararg rows: String) = runBlocking {
        engine.onEvent(DomovoiJson.parseToJsonElement("""{"warnings":[${rows.joinToString(",")}],"rooms":[]}"""))
    }

    private val key get() = serverKey(url)

    @Test fun aWarningPostsOnceWithTheCoresWords() {
        push(w(1))
        push(w(1))
        assertEquals(1, sink.posted.size)
        val (k, posted) = sink.posted.single()
        assertEquals(key, k)
        assertEquals("Pako is at 19%. Plug it in.", posted.text)
        assertEquals(mapOf("pako" to 1L), book.books[key])
    }

    @Test fun theTenPercentWarningReplacesTheTwenty() {
        push(w(1))
        push(w(1), w(2, threshold = 10, percent = 10))
        assertEquals(listOf(1L, 2L), sink.posted.map { it.second.id })
        assertEquals(mapOf("pako" to 2L), book.books[key])
        assertEquals(emptyList<Any>(), sink.withdrawn)
    }

    @Test fun chargingTakesItBack() {
        push(w(1), w(5, room = "den", percent = 18))
        push(w(5, room = "den", percent = 18))
        assertEquals(listOf(key to "pako"), sink.withdrawn.toList())
        assertEquals(mapOf("den" to 5L), book.books[key])
        push()
        assertEquals(listOf(key to "pako", key to "den"), sink.withdrawn.toList())
        assertEquals(emptyMap<String, Long>(), book.books[key])
    }

    @Test fun theReadAndThePushNeverDoubleAPost() {
        answer = """{"warnings":[${w(3)}]}"""
        runBlocking { engine.refresh() }
        push(w(3))
        runBlocking { engine.refresh() }
        assertEquals(listOf(3L), sink.posted.map { it.second.id })
    }

    @Test fun notificationsOffHoldTheWarningUntilTheyAreBack() {
        sink.can = false
        push(w(4))
        assertEquals(emptyList<Any>(), sink.posted)
        sink.can = true
        push(w(4))
        assertEquals(listOf(4L), sink.posted.map { it.second.id })
    }

    @Test fun anOlderServerIsNone() {
        answer = null
        runBlocking { engine.refresh() }
        assertTrue(sink.posted.isEmpty() && sink.withdrawn.isEmpty())
    }

    @Test fun thePlanAndTheBookArePure() {
        val a = RoomBatteryWarning(1, "pako", "Pako", 20, 19, "Pako is at 19%. Plug it in.")
        val b = a.copy(id = 2, threshold = 10, percent = 9)
        val plan = planBatteryNotifications(listOf(a, b), mapOf("den" to 7), canPost = true)
        assertEquals(listOf(b), plan.post)
        assertEquals(listOf("den"), plan.withdraw)
        assertEquals(mapOf("pako" to 2L), plan.book)
        assertEquals("den=7,pako=2", encodeBook(mapOf("pako" to 2L, "den" to 7L)))
        assertEquals(mapOf("pako" to 2L, "den" to 7L), decodeBook("den=7,pako=2"))
        assertEquals(emptyMap<String, Long>(), decodeBook(null))
        assertEquals("Pako is at 9%. Plug it in.", batteryText("Pako", 9))
    }
}
