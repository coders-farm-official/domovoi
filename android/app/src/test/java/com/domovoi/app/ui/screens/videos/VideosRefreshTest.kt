package com.domovoi.app.ui.screens.videos

import androidx.compose.runtime.CompositionLocalProvider
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.StateBus
import com.domovoi.app.net.WsEvent
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.allocateWithoutConstructor
import com.domovoi.app.testing.button
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.setField
import com.domovoi.app.testing.withHeadlessWindow
import com.domovoi.app.ui.components.FILES_CHANGED
import com.domovoi.app.ui.components.LibraryRefreshButton
import com.domovoi.app.ui.screens.files.ServerFileSource
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
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
 * The owner uploaded videos from his phone (2026-10-17) and the Videos screen
 * kept its old grid: it listened to no change event and had no refresh, so
 * the only way to see them was to leave and come back. The server now says
 * when a files library changed (`files.changed`, web/backend/api/listing_cache.py)
 * and the screen re-reads on it; its header gained "rescan videos", which
 * re-walks the folders (`refresh=true`), as the pull-to-refresh does.
 *
 * The screen's list and refresh ([rememberVideosList], what VideosScreen
 * renders) composed for real on the JVM ([HeadlessUi]) under the header's
 * button, against a MockWebServer, with a state bus the test emits into.
 */
class VideosRefreshTest {

    private val listCalls = CopyOnWriteArrayList<String>()

    private fun server(): MockWebServer = MockWebServer().apply {
        dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                val body = when {
                    path.startsWith("/api/videos/list") -> {
                        listCalls += path
                        """{"videos": [{"library_id": "core:videos", "library_label": "Videos",
                             "rel": "clips/from-the-phone.mp4", "name": "from-the-phone.mp4",
                             "size": 10, "mtime": 1760000000}],
                            "libraries": [{"library_id": "core:videos", "library_label": "Videos", "total": 1}],
                            "total": 1, "has_more": false, "next_cursor": null}"""
                    }
                    path.startsWith("/api/videos/recent") -> """{"recent": []}"""
                    else -> "{}"
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

    private suspend fun com.domovoi.app.testing.JvmComposition.waitFor(what: String, ok: () -> Boolean) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10)
        while (!ok()) {
            check(System.nanoTime() < deadline) { "timed out waiting for $what (list calls: $listCalls)" }
            delay(10)
            settle(untilQuiet = false, frames = 2)
        }
    }

    @Test fun aFilesChangedEventAndTheRescanButtonReReadTheGrid() {
        val server = server()
        try {
            val events = MutableSharedFlow<WsEvent>(extraBufferCapacity = 16)
            val app = app(server.url("/").toString().trimEnd('/'), events)
            val tree = NodeTree()
            withHeadlessWindow {
                composeTest(tree) {
                    setContent {
                        HeadlessUi {
                            CompositionLocalProvider(LocalApp provides app) {
                                // The screen's own list and refresh, under its
                                // own header button (the screen's filter field
                                // cannot compose without a window).
                                val (_, refresh) = rememberVideosList("/api/videos/list?sort=library")
                                LibraryRefreshButton(refresh, "rescan videos")
                            }
                        }
                    }
                    settle(untilQuiet = false, frames = 20)
                    waitFor("the first page") { listCalls.size >= 1 }
                    repeat(10) { delay(10); settle(untilQuiet = false, frames = 3) }
                    assertEquals(1, listCalls.size)

                    // Another library's event is not a reason to re-read.
                    events.emit(WsEvent("library.indexer.changed", null))
                    // An upload from this phone's "Your device" page, or anywhere.
                    events.emit(WsEvent(FILES_CHANGED, null))
                    waitFor("the re-read after files.changed") { listCalls.size >= 2 }
                    repeat(60) { delay(10); settle(untilQuiet = false, frames = 2) }
                    assertEquals(2, listCalls.size)
                    assertTrue(listCalls[1], "refresh=true" !in listCalls[1])

                    tree.button("rescan videos").click()
                    waitFor("the rescan") { listCalls.size >= 3 }
                    assertTrue(listCalls[2], "refresh=true" in listCalls[2])
                }
            }
        } finally {
            server.shutdown()
        }
    }

    @Test fun theServerFilesBrowserFollowsFilesChangedToo() {
        val events = MutableSharedFlow<WsEvent>()
        val app = app("http://127.0.0.1:9", events)
        val browse = ServerFileSource(app).browseEvents
        assertTrue(browse.toString(), FILES_CHANGED in browse)
        assertTrue(browse.toString(), "library.indexer.changed" in browse)
    }
}
