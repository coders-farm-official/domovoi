package com.domovoi.app.player

import android.content.ContextWrapper
import androidx.media3.common.MediaItem
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ServerIdentity
import com.domovoi.app.testing.StatefulExoPlayer
import com.domovoi.app.testing.allocateWithoutConstructor
import com.domovoi.app.testing.field
import com.domovoi.app.testing.libraryQueue
import com.domovoi.app.testing.phoneSong
import com.domovoi.app.testing.setField
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

/**
 * 2026-10-12, Kamron's phone: music played from one Domovoi through the
 * chat mic; the app was then connected to a fresh Domovoi at another
 * address, and the mini player still showed the first one's song. What the
 * phone queued from a server belongs to that server, by identity
 * (ServerScope.kt):
 *
 *  - a move to a DIFFERENT server (or to none) stops and clears the old
 *    one's media, ends a cast to one of its rooms, and sends the new server
 *    nothing of the old one's — no stream, no position save, no room call;
 *  - the same server again — the same identity at another address, an
 *    identity learned later, a reconnect, the local/server mode switch —
 *    keeps it all, playing;
 *  - the phone's own songs stay (and keep playing when one is playing).
 *
 * Two MockWebServers stand in for the two Domovois; the controller's
 * ApiClient points at whichever one "the app is connected to".
 */
class PlayerServerSwitchTest {
    private val logA = CopyOnWriteArrayList<String>()
    private val logB = CopyOnWriteArrayList<String>()
    private val serverA = recordingServer(logA)
    private val serverB = recordingServer(logB)

    @Volatile private var connectedTo: MockWebServer = serverA

    private val exoLog = CopyOnWriteArrayList<String>()
    private val exo = StatefulExoPlayer(exoLog)
    private val player = PlayerController(
        ContextWrapper(null),
        ApiClient({ connectedTo.url("/").toString().trimEnd('/') }),
        allocateWithoutConstructor(Prefs::class.java),
    ).also { setField(it, "exoPlayer\$delegate", lazyOf(exo.player)) }

    private fun scopeOf(server: MockWebServer, fingerprint: String?) =
        ServerScope.of(server.url("/").toString(), fingerprint?.let { ServerIdentity.Pin("key-$it", it) })

    private val beelink get() = scopeOf(serverA, "SHA256:beelink")
    private val fresh get() = scopeOf(serverB, "SHA256:fresh")

    private fun recordingServer(log: MutableList<String>) = MockWebServer().apply {
        dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                log += "${request.method} ${request.path}"
                if (request.path == "/api/music/now-playing") {
                    return MockResponse().setBody(
                        """[{"room_id":"office","state":"play","elapsed_sec":3.0,"song":{"title":"Zora (Interlude)","artist":"Outkast","duration_sec":16.0}}]""",
                    )
                }
                return MockResponse().setBody("""{"ok":true}""")
            }
        }
        start()
    }

    @After fun tearDown() {
        (field(player, "scope") as CoroutineScope).cancel()
        serverA.shutdown()
        serverB.shutdown()
    }

    /** The app moves to [server] as [scope] (Prefs' url, then the watcher). */
    private fun connect(server: MockWebServer, scope: ServerScope) {
        connectedTo = server
        player.serverChanged(scope)
    }

    /** What the engine holds, by queue uid (a unit test's android.net.Uri
     *  is a stub, so the streams are told apart by the item they play). */
    private fun engineItems(): List<String> = exo.items.map(MediaItem::mediaId)

    private fun playingFromTheBeelink() {
        connect(serverA, beelink)
        player.playItems(libraryQueue("Zora (Interlude)", "Roses", "Hey Ya"), 0)
        assertTrue(exo.playing)
        logA.clear(); exoLog.clear()
    }

    // ---- a different server -------------------------------------------------

    @Test fun anotherServerStopsAndClearsTheOldOnesMedia() {
        playingFromTheBeelink()

        connect(serverB, fresh)

        assertEquals(emptyList<PlayItem>(), player.queue.value)
        assertNull("the mini player still shows the old song", player.current)
        assertFalse(exo.playing)
        assertTrue("the player still holds the old streams", exo.items.isEmpty())
        Thread.sleep(300)
        assertEquals("the new server was sent something of the old one's", emptyList<String>(), logB.toList())
    }

    @Test fun nothingOfTheOldServerIsEverPlayedFromTheNewOne() {
        playingFromTheBeelink()
        assertEquals(listOf("lib-101", "lib-102", "lib-103"), engineItems())

        connect(serverB, fresh)
        // Whatever the person does next on the new server's player…
        player.toggle(); player.next(); player.prev()
        player.enqueue(listOf(PlayItem.fromTrack(7, "Fresh Song", "New Band", null, 100.0)))

        assertEquals("only the new server's own song", listOf("lib-7"), player.queue.value.map { it.uid })
        assertEquals(listOf("lib-7"), engineItems())
        Thread.sleep(300)
        assertEquals("the new server was asked about the old one's media", emptyList<String>(), logB.toList())
    }

    @Test fun aSpokenPositionIsNotSavedToTheNewServer() {
        connect(serverA, beelink)
        player.playItems(listOf(PlayItem.fromEpisode(42, "Episode", "Show", 1800.0, null, emptyList())), 0)
        logA.clear()

        connect(serverB, fresh)
        player.clearQueue()   // what a person might press next

        Thread.sleep(300)
        assertTrue("a position was posted to the new server: $logB", logB.none { "positions" in it })
    }

    @Test fun aCastToTheOldServersRoomEndsWithoutAWordToTheNewOne() {
        playingFromTheBeelink()
        runBlocking { player.castTo("office") }
        assertEquals(PlayTarget.Room("office"), player.target.value)

        connect(serverB, fresh)

        assertEquals(PlayTarget.Local, player.target.value)
        assertNull(player.remote.value)
        assertTrue(player.queue.value.isEmpty())
        Thread.sleep(2_500)  // longer than the room poll's 2 s
        assertEquals("the new server heard about the old one's room", emptyList<String>(), logB.toList())
    }

    @Test fun forgettingTheServerClearsItsMediaToo() {
        playingFromTheBeelink()
        connect(serverA, ServerScope.NONE)
        assertTrue(player.queue.value.isEmpty())
        assertFalse(exo.playing)
    }

    @Test fun aServerAtTheOldAddressWithAnotherIdentityIsAnotherServer() {
        playingFromTheBeelink()
        connect(serverA, scopeOf(serverA, "SHA256:reinstalled"))
        assertTrue(player.queue.value.isEmpty())
    }

    // ---- the same server ----------------------------------------------------

    @Test fun theSameServerAtAnotherAddressKeepsEverything() {
        playingFromTheBeelink()
        val before = player.queue.value

        connect(serverA, scopeOf(serverB, "SHA256:beelink"))

        assertEquals(before, player.queue.value)
        assertTrue(exo.playing)
        assertEquals("the player was touched", emptyList<String>(), exoLog.toList())
    }

    @Test fun anIdentityLearnedLaterIsTheSameServer() {
        connect(serverA, scopeOf(serverA, null))
        player.playItems(libraryQueue("Zora (Interlude)", "Roses"), 1)

        connect(serverA, beelink)          // pinned since
        connect(serverA, beelink)          // a reconnect, the mode switch: no change

        assertEquals(2, player.queue.value.size)
        assertTrue(exo.playing)
        // …and it is that identity from now on: a fresh server at the same
        // address is not the Beelink.
        connect(serverA, scopeOf(serverA, "SHA256:fresh"))
        assertTrue(player.queue.value.isEmpty())
    }

    // ---- the phone's own songs ----------------------------------------------

    @Test fun thePhonesOwnSongsAreNotTheServers() {
        connect(serverA, beelink)
        val mine = listOf(phoneSong(1, "Pocket Tune"), phoneSong(2, "Desk Hum"))
        player.playItems(mine, 1)
        exoLog.clear()

        connect(serverB, fresh)

        assertEquals(mine, player.queue.value)
        assertTrue(exo.playing)
        assertEquals(emptyList<String>(), exoLog.toList())
    }

    @Test fun aPhoneSongPlayingInAMixedQueuePlaysOnWithoutTheServersSongs() {
        connect(serverA, beelink)
        val queue = listOf(
            PlayItem.fromTrack(1, "Roses", "Outkast", null, 200.0),
            phoneSong(10, "Pocket Tune"),
            PlayItem.fromTrack(2, "Hey Ya", "Outkast", null, 200.0),
            phoneSong(11, "Desk Hum"),
        )
        player.playItems(queue, 1)

        connect(serverB, fresh)

        assertEquals(listOf("dev-10", "dev-11"), player.queue.value.map { it.uid })
        assertEquals("dev-10", player.current?.uid)
        assertTrue("the phone's song stopped", exo.playing)
        assertEquals(listOf("dev-10", "dev-11"), engineItems())
    }

    @Test fun aServerSongPlayingInAMixedQueueStopsAndLeavesThePhonesSongsPaused() {
        connect(serverA, beelink)
        val queue = listOf(
            phoneSong(10, "Pocket Tune"),
            PlayItem.fromTrack(1, "Roses", "Outkast", null, 200.0),
            phoneSong(11, "Desk Hum"),
        )
        player.playItems(queue, 1)

        connect(serverB, fresh)

        assertEquals(listOf("dev-10", "dev-11"), player.queue.value.map { it.uid })
        assertEquals("the next of the phone's songs", "dev-11", player.current?.uid)
        assertFalse(exo.playing)
        assertEquals(listOf("dev-10", "dev-11"), engineItems())
    }

    // ---- the scope itself ---------------------------------------------------

    @Test fun serversAreToldApartByIdentityThenAddress() {
        val a = ServerScope("http://192.168.0.117:6369", "SHA256:a")
        assertTrue(a.isSameServer(ServerScope("http://beelink.lan:6369", "SHA256:a")))
        assertFalse(a.isSameServer(ServerScope("http://192.168.0.117:6369", "SHA256:b")))
        assertTrue(a.isSameServer(ServerScope("http://192.168.0.117:6369", null)))
        assertFalse(a.isSameServer(ServerScope("http://192.168.0.240:6369", null)))
        assertFalse(ServerScope.NONE.isSameServer(ServerScope.NONE))
        assertFalse(a.isSameServer(ServerScope.NONE))
        assertEquals(ServerScope("http://beelink.lan:6369", "SHA256:a"), ServerScope("http://beelink.lan:6369", null).merge(a.copy(address = null)))
        assertEquals(ServerScope.NONE, ServerScope.of("", null))
        assertEquals(ServerScope("http://192.168.0.240:6369", null), ServerScope.of("http://192.168.0.240:6369/", null))
    }

    @Test fun thePlanKeepsThePhonesSongsInOrder() {
        val q = listOf(
            PlayItem.fromTrack(1, "a", null, null, 1.0), phoneSong(10, "x"),
            PlayItem.fromStation(3, "radio"), phoneSong(11, "y"),
        )
        val onServerSong = ServerSwitch.plan(q, 2)
        assertEquals(listOf("dev-10", "dev-11"), onServerSong.kept.map { it.uid })
        assertEquals(listOf(2, 0), onServerSong.removed)
        assertTrue(onServerSong.currentGoes)
        assertEquals(1, onServerSong.keptIndex)
        val onPhoneSong = ServerSwitch.plan(q, 3)
        assertFalse(onPhoneSong.currentGoes)
        assertEquals(1, onPhoneSong.keptIndex)
        val serverOnly = ServerSwitch.plan(libraryQueue("a", "b"), 1)
        assertTrue(serverOnly.kept.isEmpty() && serverOnly.currentGoes && serverOnly.keptIndex == 0)
    }
}
