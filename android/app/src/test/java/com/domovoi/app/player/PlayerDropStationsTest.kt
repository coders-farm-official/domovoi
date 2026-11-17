package com.domovoi.app.player

import android.content.ContextWrapper
import androidx.media3.common.MediaItem
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.CAP_IMAGEGEN
import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.LoadedManifest
import com.domovoi.app.net.ServerIdentity
import com.domovoi.app.net.dropsStations
import com.domovoi.app.testing.ourCaps
import com.domovoi.app.testing.StatefulExoPlayer
import com.domovoi.app.testing.allocateWithoutConstructor
import com.domovoi.app.testing.field
import com.domovoi.app.testing.libraryQueue
import com.domovoi.app.testing.phoneSong
import com.domovoi.app.testing.setField
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.cancel
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

/**
 * A station in the phone's queue belongs to our Radio plugin (2026-11-16):
 * once the server's manifest answers without it, the shell calls
 * [PlayerController.dropStations] (AppShell), and its stations leave the
 * queue, the mini player and the media notification. Anything else stays,
 * and plays on when it is what plays.
 */
class PlayerDropStationsTest {
    private val server = MockWebServer().apply {
        dispatcher = object : okhttp3.mockwebserver.Dispatcher() {
            override fun dispatch(request: okhttp3.mockwebserver.RecordedRequest) =
                MockResponse().setBody("""{"ok":true}""")
        }
        start()
    }
    private val exoLog = CopyOnWriteArrayList<String>()
    private val exo = StatefulExoPlayer(exoLog)
    private val player = PlayerController(
        ContextWrapper(null),
        ApiClient({ server.url("/").toString().trimEnd('/') }),
        allocateWithoutConstructor(Prefs::class.java),
    ).also { setField(it, "exoPlayer\$delegate", lazyOf(exo.player)) }

    @After fun tearDown() {
        (field(player, "scope") as CoroutineScope).cancel()
        server.shutdown()
    }

    private fun engineItems(): List<String> = exo.items.map(MediaItem::mediaId)
    private val jazz = PlayItem.fromStation(4, "Jazz FM")
    private val news = PlayItem.fromStation(9, "News Talk")

    @Test fun aPlayingStationStopsAndGoes() {
        player.playItems(listOf(jazz), 0)
        assertTrue(exo.playing)

        player.dropStations()

        assertTrue(player.queue.value.isEmpty())
        assertNull("the mini player still shows the station", player.current)
        assertFalse(exo.playing)
        assertTrue(exo.items.isEmpty())
    }

    @Test fun songsAroundAStationStayAndTheOnePlayingPlaysOn() {
        val songs = libraryQueue("Roses", "Hey Ya")
        player.playItems(listOf(songs[0], jazz, phoneSong(3, "Mine"), news, songs[1]), 0)
        assertTrue(exo.playing)

        player.dropStations()

        assertEquals(listOf("lib-101", "dev-3", "lib-102"), player.queue.value.map { it.uid })
        assertEquals(engineItems(), player.queue.value.map { it.uid })
        assertEquals("Roses", player.current?.title)
        assertTrue(exo.playing)
    }

    @Test fun nothingToDropTouchesNothing() {
        player.playItems(libraryQueue("Roses", "Hey Ya"), 1)
        exoLog.clear()
        val before = player.queue.value

        player.dropStations()

        assertEquals(before, player.queue.value)
        assertTrue(exo.playing)
        assertEquals(emptyList<String>(), exoLog.toList())
    }

    /** The reset of a server switch is not a manifest that loaded
     *  (net/Capabilities.kt dropsStations): the same server at another
     *  address keeps its queue, stations included, until ITS manifest says
     *  our Radio is gone. */
    @Test fun aServerSwitchsResetDoesNotWipeTheQueue() {
        val pin = ServerIdentity.Pin("key-home", "SHA256:home")
        val before = server.url("/").toString()
        val after = "http://192.168.1.20:6369"
        player.serverChanged(ServerScope.of(before, pin))
        val songs = libraryQueue("Roses")
        player.playItems(listOf(jazz, songs[0]), 0)

        // The same server, moved: the player keeps everything (ServerScope).
        player.serverChanged(ServerScope.of(after, pin))
        // The shell's fetch helper still holds the old address's answer —
        // even one without Radio — and the shell shows EMPTY: nothing goes.
        for (stale in listOf(LoadedManifest(before, ourCaps(CAP_IMAGEGEN)), LoadedManifest(before, ourCaps(CAP_STATIONS)), null)) {
            assertFalse(dropsStations(stale, after))
            if (dropsStations(stale, after)) player.dropStations()
        }
        assertEquals(listOf(jazz, songs[0]), player.queue.value)
        assertTrue(exo.playing)

        // Its own manifest loads with our Radio: still nothing goes.
        val withRadio = LoadedManifest(after, ourCaps(CAP_STATIONS))
        if (dropsStations(withRadio, after)) player.dropStations()
        assertEquals(listOf(jazz, songs[0]), player.queue.value)

        // Its manifest loads WITHOUT it: the station goes, the song stays.
        val without = LoadedManifest(after, Capabilities.EMPTY)
        assertTrue(dropsStations(without, after))
        player.dropStations()
        assertEquals(songs, player.queue.value)
    }

    @Test fun thePlanKeepsTheRestInOrder() {
        val songs = libraryQueue("A", "B")
        val plan = ServerSwitch.planDropping(listOf(songs[0], jazz, songs[1]), 1) { it.kind == PlayKind.Radio }
        assertEquals(songs, plan.kept)
        assertEquals(listOf(1), plan.removed)
        assertTrue(plan.currentGoes)
        assertEquals(1, plan.keptIndex)   // the next one on, B
        // The server move's plan is the same planner with its own rule.
        assertEquals(
            ServerSwitch.plan(listOf(songs[0], phoneSong(3, "Mine")), 0),
            ServerSwitch.planDropping(listOf(songs[0], phoneSong(3, "Mine")), 0, ServerSwitch::fromServer),
        )
    }
}
