package com.domovoi.app.ui.screens.music

import androidx.compose.runtime.CompositionLocalProvider
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.StateBus
import com.domovoi.app.net.WsEvent
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.allocateWithoutConstructor
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.setField
import com.domovoi.app.testing.withHeadlessWindow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.put
import okhttp3.Call
import okhttp3.EventListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * The playlist drawer lists a playlist's tracks. It drew them in a
 * scrolling Column, so opening a long playlist (or a big Favorites) built
 * every row — a clickable row with up to three icon buttons — on the main
 * thread in one frame: the freeze the player tab had. Its rows are now
 * lazy items, built only once the list is measured and only for what is on
 * screen.
 *
 * The drawer is composed for real on the JVM ([HeadlessUi]: no window, so
 * nothing is measured) against a MockWebServer serving the playlist, and
 * the nodes it emitted are counted: a 2,000-track playlist must build no
 * more than a one-track one.
 */
class PlaylistDrawerLazyTest {

    private val playlist = Playlist(id = 7, name = "Long drive", trackCount = 0)

    private fun tracksJson(n: Int): String = buildJsonArray {
        repeat(n) { i ->
            addJsonObject {
                put("id", 1_000 + i)
                put("title", "song $i")
                put("artist", "artist $i")
                put("duration_sec", 200.0)
            }
        }
    }.toString()

    /** The drawer, composed with [tracks] tracks in its playlist; how many
     *  nodes it holds once the tracks have arrived. */
    private fun nodesWith(tracks: Int): Int {
        val server = MockWebServer()
        server.enqueue(MockResponse().setBody(tracksJson(tracks)))
        server.start()
        val fetched = CountDownLatch(1)
        try {
            val base = "http://127.0.0.1:${server.port}"
            val api = ApiClient({ base })
            setField(
                api, "http",
                api.http.newBuilder().eventListener(object : EventListener() {
                    override fun callEnd(call: Call) = fetched.countDown()
                }).build(),
            )
            val prefs = allocateWithoutConstructor(Prefs::class.java)
            setField(prefs, "serverUrl", MutableStateFlow(base))
            val bus = allocateWithoutConstructor(StateBus::class.java)
            setField(bus, "events", MutableSharedFlow<WsEvent>())
            val app = allocateWithoutConstructor(AppContainer::class.java)
            setField(app, "prefs", prefs)
            setField(app, "api", api)
            setField(app, "bus", bus)

            val tree = NodeTree()
            var nodes = -1
            withHeadlessWindow {
                composeTest(tree) {
                    setContent {
                        HeadlessUi {
                            CompositionLocalProvider(LocalApp provides app) {
                                PlaylistDrawer(
                                    playlist = playlist,
                                    rooms = listOf("office", "kitchen"),
                                    onClose = {},
                                    onEdited = {},
                                    refreshPlaylists = {},
                                    refreshNP = {},
                                )
                            }
                        }
                    }
                    settle(untilQuiet = false, frames = 20)
                    val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10)
                    while (fetched.count > 0) {
                        check(System.nanoTime() < deadline) { "the drawer never fetched its tracks" }
                        delay(10)
                        settle(untilQuiet = false, frames = 2)
                    }
                    // The response is parsed and the drawer's state set back
                    // on this thread; let that and its recomposition run.
                    repeat(20) {
                        delay(10)
                        settle(untilQuiet = false, frames = 5)
                    }
                    nodes = tree.size()
                }
            }
            assertEquals(1, server.requestCount)
            assertEquals("/api/playlists/7/tracks", server.takeRequest().path)
            return nodes
        } finally {
            server.shutdown()
        }
    }

    @Test fun aLongPlaylistBuildsNoMoreThanAShortOne() {
        val one = nodesWith(1)
        val many = nodesWith(2_000)
        assertTrue("the drawer composed nothing", one >= 10)
        assertEquals("the drawer built its rows up front, not lazily", one, many)
    }

    @Test fun theServedTracksAreWhatTheDrawerDecodes() {
        // The JSON above is a real playlist answer: what the drawer gets is
        // 2,000 tracks, not an error it would show as an empty list.
        val tracks = DomovoiJson.decodeFromString<List<LibraryTrack>>(tracksJson(2_000))
        assertEquals(2_000, tracks.size)
        assertEquals("song 1999", tracks.last().title)
    }
}
