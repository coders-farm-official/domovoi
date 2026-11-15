package com.domovoi.app.ui.screens.home

import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.ui.screens.music.MusicGroup
import com.domovoi.app.ui.screens.music.NowPlayingGroup
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A room row's share menu held to web/static/home.jsx HomeGroupChoices:
 * what a playing room, a quiet room and a room in a group are offered, and
 * the toasts after.
 */
class HomeGroupsTest {

    private fun room(id: String, state: String? = null, status: String = "online", group: NowPlayingGroup? = null) =
        HomeRoom(
            room_id = id,
            status = status,
            now_playing = HomeNowPlaying(
                state = state ?: "stop",
                song = if (state != null) HomeSong(title = "Song") else null,
                group = group,
            ),
        )

    private val upstairs = MusicGroup(3, "Upstairs", listOf("office", "bedroom"))

    @Test fun theRoomsReadCarriesTheGroupOrNot() {
        val rooms = DomovoiJson.decodeFromString(
            ListSerializer(HomeRoom.serializer()),
            """[{"room_id":"bedroom","status":"online","now_playing":{"state":"play","song":{"title":"Creep"},
                 "group":{"id":"9f1c","name":"Upstairs","host_room":"office","rooms":["office","bedroom"],"group_id":3,"everywhere":false}}},
                {"room_id":"office","status":"online","now_playing":{"state":"stop","group":null}},
                {"room_id":"garage","status":"online","now_playing":{"state":"stop"}}]""",
        )
        assertEquals("office", rooms[0].now_playing?.group?.rooms?.first())
        assertEquals(3L, rooms[0].now_playing?.group?.groupId)
        assertNull(rooms[1].now_playing?.group)
        assertNull(rooms[2].now_playing?.group)
    }

    @Test fun aPlayingRoomOffersGroupsEverywhereEachRoomAndJoins() {
        val office = room("office", "play")
        val rooms = listOf(office, room("bedroom"), room("kitchen", "play"), room("porch", status = "offline"))
        val keys = homeGroupChoices(office, rooms, listOf(upstairs)).map { it.key }
        assertEquals(listOf("group:3", "everywhere", "share:bedroom", "share:kitchen", "join:kitchen"), keys)
        val c = homeGroupChoices(office, rooms, listOf(upstairs))
        assertEquals("play in Upstairs too", c[0].label)
        assertEquals("office", c[0].body["host_room"]!!.jsonPrimitive.content)
        assertEquals("3", c[0].body["group_id"]!!.jsonPrimitive.content)
        assertEquals("shared", c[0].path)
        assertEquals("true", c[1].body["everywhere"]!!.jsonPrimitive.content)
        assertEquals("every connected room hears it", c[1].hint)
        assertEquals(listOf("bedroom"), c[2].body["rooms"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(listOf("office", "bedroom"), c[2].touched)
        assertEquals("shared/join", c[4].path)
        assertEquals("kitchen", c[4].body["host_room"]!!.jsonPrimitive.content)
        assertEquals("hear what plays in kitchen", c[4].hint)
    }

    @Test fun aQuietRoomOnlyGetsTheJoins() {
        val bedroom = room("bedroom")
        val rooms = listOf(bedroom, room("office", "play"), room("garage"))
        assertEquals(listOf("join:office"), homeGroupChoices(bedroom, rooms, listOf(upstairs)).map { it.key })
        // ...and nothing at all when nothing plays anywhere: no menu.
        assertTrue(homeGroupChoices(bedroom, listOf(bedroom, room("garage")), listOf(upstairs)).isEmpty())
    }

    @Test fun aStoppedRoomWithASongLoadedIsQuiet() {
        val stopped = HomeRoom("office", "online", now_playing = HomeNowPlaying("stop", HomeSong(title = "Old")))
        val rooms = listOf(stopped, room("bedroom"))
        assertTrue(homeGroupChoices(stopped, rooms, listOf(upstairs)).isEmpty())
    }

    @Test fun aRoomInAGroupLeavesAndIsNotOfferedItsOwnRooms() {
        val g = NowPlayingGroup("s1", "Upstairs", "office", listOf("office", "bedroom"), groupId = 3)
        val bedroom = room("bedroom", "play", group = g)
        val rooms = listOf(room("office", "play", group = g), bedroom, room("garage"))
        val c = homeGroupChoices(bedroom, rooms, listOf(upstairs))
        // Upstairs is whole: no "play in Upstairs too"; office is in the group: no share, no join.
        assertEquals(listOf("everywhere", "share:garage", "leave"), c.map { it.key })
        assertEquals("leave the group", c.last().label)
        assertEquals("shared/leave", c.last().path)
        assertEquals("bedroom", c.last().body["room_id"]!!.jsonPrimitive.content)
        assertEquals("bedroom left the group", groupChoiceDoneToast("bedroom", c.last()))
        assertEquals("play everywhere · done", groupChoiceDoneToast("bedroom", c.first()))
    }

    @Test fun leaveOnlyForARoomInAGroup() {
        val office = room("office", "play")
        assertTrue(homeGroupChoices(office, listOf(office, room("garage")), emptyList()).none { it.kind == GroupChoiceKind.Leave })
    }

    @Test fun aNoIsTheServersSentence() {
        val office = room("office", "play")
        val share = homeGroupChoices(office, listOf(office, room("garage")), emptyList()).first()
        val e409 = ApiException(
            409, "409 Conflict: {\"detail\":\"Nothing is playing in office.\"}",
            body = "{\"detail\":\"Nothing is playing in office.\"}",
        )
        assertEquals("share failed: Nothing is playing in office.", groupChoiceFailedToast(share, e409))
        val g = NowPlayingGroup("s1", null, "office", listOf("office", "garage"))
        val leave = homeGroupChoices(room("garage", "play", group = g), listOf(room("office", "play", group = g)), emptyList()).last()
        assertEquals("leave the group failed: Nothing is playing in office.", groupChoiceFailedToast(leave, e409))
    }
}
