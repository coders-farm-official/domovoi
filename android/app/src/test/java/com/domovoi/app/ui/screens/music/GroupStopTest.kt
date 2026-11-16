package com.domovoi.app.ui.screens.music

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.player.PlayTarget
import com.domovoi.app.player.RemoteNowPlaying
import com.domovoi.app.ui.screens.home.HomeApi
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.decodeFromString
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

/**
 * A stop in a room of a group stops the whole group (owner, 2026-11-15):
 * every stop button names the group in the web's words (home.jsx
 * HomeStopTitle, music.jsx NPCard), a room alone keeps its plain name, and
 * the request stays the bare stop (the server's default scope is the group;
 * "leave the group" is the stop for one room, as on the web).
 */
class GroupStopTest {
    private lateinit var server: MockWebServer
    private val base get() = server.url("/").toString().trimEnd('/')

    @Before fun up() {
        server = MockWebServer().also { it.start() }
    }

    @After fun down() = server.shutdown()

    private val upstairs = NowPlayingGroup("s1", "Upstairs", "office", listOf("office", "garage", "bedroom"), groupId = 3)
    private val joined = NowPlayingGroup("s2", null, "office", listOf("office", "garage"))

    // ─── the label ────────────────────────────────────────────────────────

    @Test fun aNamedGroupsStopNamesItAndItsRooms() {
        assertEquals("stop Upstairs: office, garage, bedroom", groupStopLabel(upstairs, "stop garage"))
        assertEquals("stop Upstairs: office, garage, bedroom", groupStopLabel(upstairs, "stop"))
    }

    @Test fun roomsJoinedOneByOneAreTheGroup() {
        assertEquals("stop the group: office, garage", groupStopLabel(joined, "stop office"))
        // An empty name is no name (the web's `g.name || 'the group'`).
        assertEquals("stop the group: office, garage", groupStopLabel(joined.copy(name = ""), "stop"))
    }

    @Test fun everywhereIsNamedAsTheServerNamesIt() {
        val all = NowPlayingGroup("s3", "Everywhere", "kitchen", listOf("kitchen", "porch"), everywhere = true)
        assertEquals("stop Everywhere: kitchen, porch", groupStopLabel(all, "stop kitchen"))
    }

    @Test fun aRoomAloneKeepsItsPlainStop() {
        assertEquals("stop office", groupStopLabel(null, "stop office"))
        assertEquals("stop", groupStopLabel(null, "stop"))
        // A group of one room left (the others gone) stops only this room.
        assertEquals("stop office", groupStopLabel(joined.copy(rooms = listOf("office")), "stop office"))
    }

    @Test fun stopAndCloseNamesTheGroupItStops() {
        assertEquals("Stop and close", stopAndCloseLabel(null))
        assertEquals("Stop and close · stop the group: office, garage", stopAndCloseLabel(joined))
    }

    // ─── which group the phone's stop reaches ─────────────────────────────

    @Test fun thePhonesStopReachesTheCastRoomsGroupOnly() {
        val reading = RemoteNowPlaying("office", "play", "t", "a", 1.0, 2.0, group = joined)
        assertEquals(joined, castStopGroup(PlayTarget.Room("office"), reading))
        // Playing on the phone: its stop is its own.
        assertNull(castStopGroup(PlayTarget.Local, reading))
        // A reading of the room the cast left says nothing about this one.
        assertNull(castStopGroup(PlayTarget.Room("garage"), reading))
        assertNull(castStopGroup(PlayTarget.Room("office"), null))
        assertNull(castStopGroup(PlayTarget.Room("office"), reading.copy(group = null)))
    }

    // ─── the wire ─────────────────────────────────────────────────────────

    @Test fun theMusicCardsReadTheGroup() {
        val rows = DomovoiJson.decodeFromString<List<NowPlayingRoom>>(
            """[{"room_id":"garage","state":"play","group":{"id":"s1","name":"Upstairs","host_room":"office",
                 "rooms":["office","garage"],"group_id":3,"everywhere":false}},
                {"room_id":"porch","state":"stop","group":null},
                {"room_id":"attic","state":"stop"}]""",
        )
        assertEquals("stop Upstairs: office, garage", groupStopLabel(rows[0].group, "stop"))
        assertNull(rows[1].group)
        assertNull(rows[2].group)
    }

    @Test fun theStopIsTheBareStopWithNoScope() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok":true,"text":"Stopped in the office and the garage."}"""))
        val api = ApiClient(baseUrlProvider = { base }, deviceTokenProvider = { "household-token-1234" })
        HomeApi.roomAction(api, "dining room", "stop")
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        // The server's default scope is the group, which the label says.
        assertEquals("/api/music/stop/dining%20room", req.path)
    }
}
