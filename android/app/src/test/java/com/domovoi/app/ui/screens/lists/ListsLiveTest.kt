package com.domovoi.app.ui.screens.lists

import androidx.compose.runtime.CompositionLocalProvider
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.StateBus
import com.domovoi.app.net.WsEvent
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.JvmComposition
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.allocateWithoutConstructor
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.setField
import com.domovoi.app.testing.withHeadlessWindow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit

/**
 * §7.6: a `lists.changed` on /ws/state re-reads the lists AND the open
 * list. The screen's own reads ([rememberListsData], what ListsScreen
 * renders) composed for real on the JVM ([HeadlessUi]) against a
 * MockWebServer, with a state bus the test emits into. The event's payload
 * is never read: one that is not even an object still re-reads.
 */
class ListsLiveTest {

    private val calls = CopyOnWriteArrayList<String>()

    private fun server(): MockWebServer = MockWebServer().apply {
        dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                calls += path
                val list = """{"id": 2, "name": "Shopping", "position": 2, "open_count": 1, "done_count": 0,
                    "created_at": "2026-11-11T20:55:21.123+00:00", "updated_at": "2026-11-11T21:03:00+00:00"}"""
                val body = when (path) {
                    "/api/lists" -> "[$list]"
                    "/api/lists/2" -> """{"list": $list, "items": [{"id": 41, "list_id": 2, "text": "Milk",
                        "done": false, "done_at": null, "position": 4, "added_by_person_id": null,
                        "added_by_name": null, "added_by_room": null, "added_via": "screen",
                        "created_at": "...", "updated_at": "..."}]}"""
                    else -> return MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}""")
                }
                return MockResponse().setBody(body).setHeader("Content-Type", "application/json")
            }
        }
        start()
    }

    private fun app(base: String, events: MutableSharedFlow<WsEvent>): AppContainer {
        val prefs = allocateWithoutConstructor(Prefs::class.java)
        setField(prefs, "serverUrl", MutableStateFlow(base))
        setField(prefs, "listenerPersonId", MutableStateFlow<String?>(null))
        setField(prefs, "deviceId", "android-test")
        val bus = allocateWithoutConstructor(StateBus::class.java)
        setField(bus, "events", events)
        return allocateWithoutConstructor(AppContainer::class.java).also {
            setField(it, "prefs", prefs)
            setField(it, "api", ApiClient({ base }))
            setField(it, "bus", bus)
        }
    }

    private fun count(path: String) = calls.count { it == path }

    private suspend fun JvmComposition.waitFor(what: String, ok: () -> Boolean) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10)
        while (!ok()) {
            check(System.nanoTime() < deadline) { "timed out waiting for $what (calls: $calls)" }
            delay(10)
            settle(untilQuiet = false, frames = 2)
        }
    }

    private suspend fun JvmComposition.quiet() = repeat(30) { delay(10); settle(untilQuiet = false, frames = 2) }

    @Test fun aListsChangedEventReReadsTheListsAndTheOpenList() {
        val server = server()
        try {
            val events = MutableSharedFlow<WsEvent>(extraBufferCapacity = 16)
            val app = app(server.url("/").toString().trimEnd('/'), events)
            var seen: ListsData? = null
            withHeadlessWindow {
                composeTest(NodeTree()) {
                    setContent {
                        HeadlessUi {
                            CompositionLocalProvider(LocalApp provides app) {
                                seen = rememberListsData(paired = true) { it.firstOrNull()?.id }
                            }
                        }
                    }
                    settle(untilQuiet = false, frames = 20)
                    waitFor("the first reads") { count("/api/lists") >= 1 && count("/api/lists/2") >= 1 }
                    quiet()
                    assertEquals(1, count("/api/lists"))
                    assertEquals(1, count("/api/lists/2"))
                    assertEquals(2L, seen!!.selectedId)
                    assertTrue((seen!!.detail.data as DetailRead.Loaded).detail.items.single().text == "Milk")

                    // Another feature's event is not a reason to re-read.
                    events.emit(WsEvent("calendar.events.changed", null))
                    quiet()
                    assertEquals(1, count("/api/lists"))
                    assertEquals(1, count("/api/lists/2"))

                    // The digest is not read: any payload re-reads both.
                    events.emit(WsEvent(LISTS_CHANGED, JsonPrimitive("x")))
                    waitFor("both re-reads") { count("/api/lists") >= 2 && count("/api/lists/2") >= 2 }
                    quiet()
                    assertEquals(2, count("/api/lists"))
                    assertEquals(2, count("/api/lists/2"))
                }
            }
        } finally {
            server.shutdown()
        }
    }
}
