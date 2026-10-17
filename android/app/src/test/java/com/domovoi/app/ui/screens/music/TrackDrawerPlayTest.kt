package com.domovoi.app.ui.screens.music

import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.StateBus
import com.domovoi.app.net.WsEvent
import com.domovoi.app.player.CoverArt
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.allocateWithoutConstructor
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.setField
import com.domovoi.app.testing.textButton
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.TimeUnit
import kotlin.coroutines.ContinuationInterceptor

/**
 * The owner's rc2 report (2026-10-17): Music → a track → "play in mobile"
 * toasted "play failed: The coroutine scope left the composition" and the
 * room never played. The drawer closes itself on that tap (so it feels
 * quick) and then sent the request from its OWN composition scope, which
 * closing cancelled.
 *
 * Here the real [TrackDrawer] is composed on the JVM ([HeadlessUi]) against
 * a MockWebServer that answers the play a little late, its "play in mobile"
 * button is pressed, and the drawer leaves composition as it does in the
 * app. The request must still reach the server, its answer must still land
 * (the now-playing refresh), and nothing may say "play failed".
 */
class TrackDrawerPlayTest {

    private val track = LibraryTrack(
        id = 42, title = "ZipLoc Bag Boys", artist = "someone", addedVia = "manual",
    )

    @Test fun playInARoomReachesTheServerAfterTheDrawerCloses() {
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse =
                if (request.path == "/api/music/play-track") {
                    // Late enough that the drawer is long gone when it lands.
                    MockResponse().setBody("""{"ok":true}""").setBodyDelay(300, TimeUnit.MILLISECONDS)
                } else {
                    MockResponse().setResponseCode(404)
                }
        }
        server.start()
        val toasts = mutableListOf<String>()
        var refreshed = 0
        var open by mutableStateOf(true)
        var appScope: CoroutineScope? = null
        try {
            val base = "http://127.0.0.1:${server.port}"
            val api = ApiClient({ base })
            // No cover for this track (Coil needs a Main dispatcher the JVM
            // has not got): the drawer draws its placeholder instead.
            CoverArt.noteFailure(api.absolute(CoverArt.libraryPath(track.id)), 404)
            val prefs = allocateWithoutConstructor(Prefs::class.java)
            setField(prefs, "serverUrl", MutableStateFlow(base))
            val bus = allocateWithoutConstructor(StateBus::class.java)
            setField(bus, "events", MutableSharedFlow<WsEvent>())
            val app = allocateWithoutConstructor(AppContainer::class.java)
            setField(app, "prefs", prefs)
            setField(app, "api", api)
            setField(app, "bus", bus)

            val tree = NodeTree()
            withHeadlessWindow {
                composeTest(tree) {
                    // The app's scope, on this test's one thread (the app's
                    // runs on Main): it lives past the drawer, like the app.
                    val scope = CoroutineScope(SupervisorJob() + currentCoroutineContext()[ContinuationInterceptor]!!)
                    appScope = scope
                    setField(app, "scope", scope)
                    setContent {
                        HeadlessUi {
                            CompositionLocalProvider(
                                LocalApp provides app,
                                LocalToast provides { msg: String -> toasts += msg },
                            ) {
                                if (open) {
                                    TrackDrawer(
                                        track = track,
                                        rooms = listOf("mobile"),
                                        onClose = { open = false },
                                        onBrowserPlay = {},
                                        onQueueTrack = {},
                                        refreshLib = {},
                                        refreshStats = {},
                                        refreshNP = { refreshed++ },
                                    )
                                }
                            }
                        }
                    }
                    settle(untilQuiet = false, frames = 20)
                    tree.textButton("play in mobile").click()
                    settle(untilQuiet = false, frames = 20)
                    assertFalse("the drawer should close on the tap", open)
                    assertFalse("the drawer is still composed", "play in mobile" in tree.texts())

                    val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10)
                    while (refreshed == 0 && toasts.none { it.startsWith("play failed") }) {
                        check(System.nanoTime() < deadline) { "the play never finished; toasts: $toasts" }
                        delay(10)
                        settle(untilQuiet = false, frames = 2)
                    }
                }
            }
            val sent = server.takeRequest(1, TimeUnit.SECONDS)
            assertEquals("/api/music/play-track", sent?.path)
            val body = sent!!.body.readUtf8()
            assertTrue(body, "\"room_id\":\"mobile\"" in body && "\"track_id\":42" in body)
            assertEquals("the play's answer never landed", 1, refreshed)
            assertEquals(listOf("playing \"ZipLoc Bag Boys\" in mobile…"), toasts)
        } finally {
            appScope?.cancel()
            CoverArt.forgetMisses()
            server.shutdown()
        }
    }

    @Test fun noSourceIsNoPillNotASecondManual() {
        // A file the library found on disk: added via "manual", no source.
        // The row and the drawer read "manual manual" when no source showed
        // as "manual" too.
        assertNull(trackSource(null))
        assertNull(trackSource(""))
        assertNull(trackSource("  "))
        assertEquals("radio", trackSource("radio"))
    }
}
