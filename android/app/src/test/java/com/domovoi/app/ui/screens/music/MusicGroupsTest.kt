package com.domovoi.app.ui.screens.music

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/**
 * Groups (V060) held to the web: the wire shapes (group present, absent,
 * null; everywhere; host first), the calls' paths and bodies, the server's
 * 400/409 sentences shown as they are, and when every control is hidden.
 */
class MusicGroupsTest {
    private lateinit var server: MockWebServer
    private lateinit var api: MusicGroupsApi
    private val base get() = server.url("/").toString().trimEnd('/')

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        api = MusicGroupsApi(ApiClient(baseUrlProvider = { base }, deviceTokenProvider = { "household-token-1234" }))
    }

    @After fun down() = server.shutdown()

    private fun body() = Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject

    // ─── parsing ──────────────────────────────────────────────────────────

    @Test fun theListReadsGroupsAndSharedQueuesHostFirst() = runBlocking {
        server.enqueue(
            MockResponse().setBody(
                """{"groups":[{"id":3,"name":"Upstairs","rooms":["office","bedroom"],"created_at":"x"}],
                   "shared":[{"id":"9f1c","name":"Upstairs","host_room":"office","rooms":["office","bedroom"],"group_id":3,"everywhere":false},
                             {"id":"a2b3","name":"Everywhere","host_room":"kitchen","rooms":["kitchen","garage","porch"],"group_id":null,"everywhere":true}]}""",
            ),
        )
        val read = readGroups(api, base)
        assertEquals("/api/music/groups", server.takeRequest().path)
        val list = read.list!!
        assertEquals(listOf(MusicGroup(3, "Upstairs", listOf("office", "bedroom"))), list.groups)
        assertEquals("office", list.shared[0].hostRoom)
        assertEquals(3L, list.shared[0].groupId)
        assertEquals(listOf("kitchen", "garage", "porch"), list.shared[1].rooms)
        assertEquals("kitchen", list.shared[1].rooms.first())
        assertTrue(list.shared[1].everywhere)
        assertNull(list.shared[1].groupId)
    }

    @Test fun aQueueCarriesItsGroupOrNone() {
        val inGroup = DomovoiJson.decodeFromString(
            RoomQueue.serializer(),
            """{"room_id":"bedroom","items":[],"group":{"id":"9f1c","name":null,"host_room":"office","rooms":["office","bedroom"],"group_id":null,"everywhere":false}}""",
        )
        assertEquals("office", inGroup.group?.hostRoom)
        assertNull(inGroup.group?.name)
        assertNull(DomovoiJson.decodeFromString(RoomQueue.serializer(), """{"room_id":"office","group":null}""").group)
        assertNull(DomovoiJson.decodeFromString(RoomQueue.serializer(), """{"room_id":"office"}""").group)
    }

    // ─── what the calls send ──────────────────────────────────────────────

    @Test fun makeRenameDeleteAndLeaveSendWhatTheWebSends() = runBlocking {
        repeat(4) { server.enqueue(MockResponse().setBody("{}")) }
        api.create("Upstairs", listOf("bedroom", "office"))
        server.takeRequest().let { r ->
            assertEquals("POST", r.method)
            assertEquals("/api/music/groups", r.path)
            val b = Json.parseToJsonElement(r.body.readUtf8()).jsonObject
            assertEquals("Upstairs", b["name"]!!.jsonPrimitive.content)
            assertEquals(listOf("bedroom", "office"), b["rooms"]!!.jsonArray.map { it.jsonPrimitive.content })
        }
        api.update(3, "Up", listOf("bedroom", "office", "garage"))
        server.takeRequest().let { r ->
            assertEquals("PATCH", r.method)
            assertEquals("/api/music/groups/3", r.path)
        }
        api.delete(3)
        server.takeRequest().let { r ->
            assertEquals("DELETE", r.method)
            assertEquals("/api/music/groups/3", r.path)
        }
        api.send(MusicGroupsApi.LEAVE, MusicGroupsApi.leaveBody("bedroom"))
        val leave = server.takeRequest()
        assertEquals("/api/music/groups/shared/leave", leave.path)
        assertEquals("bedroom", Json.parseToJsonElement(leave.body.readUtf8()).jsonObject["room_id"]!!.jsonPrimitive.content)
    }

    // ─── the server's sentences ───────────────────────────────────────────

    @Test fun aRefusedNameIsTheServersSentenceAsItIs() = runBlocking {
        val sentence = "“Jazz FM” sounds like a radio station. Pick a name that doesn’t."
        server.enqueue(MockResponse().setResponseCode(400).setBody(buildJsonObject { put("detail", sentence) }.toString()))
        val e = try {
            api.create("Jazz FM", listOf("office", "bedroom")); fail("no 400"); null
        } catch (e: ApiException) { e }!!
        assertEquals(sentence, groupSaveFailure(e))
        // Anything else is the web's "save the group failed: …".
        val other = ApiException(500, "500 Server Error: {\"detail\":\"database down\"}", body = "{\"detail\":\"database down\"}")
        assertEquals("save the group failed: database down", groupSaveFailure(other))
    }

    // ─── visibility ───────────────────────────────────────────────────────

    @Test fun aRefusalOrAnOlderServerHidesTheGroupsQuietly() = runBlocking {
        for (status in listOf(401, 403, 404)) {
            server.enqueue(MockResponse().setResponseCode(status).setBody("""{"detail":"no"}"""))
            val read = readGroups(api, base)
            assertTrue("$status is an answer", read.refused)
            assertNull(groupsFor(read, base, paired = true))
            server.takeRequest()
        }
        // A real failure is an error (the read keeps nothing to show).
        server.enqueue(MockResponse().setResponseCode(502).setBody("""{"detail":"core down"}"""))
        try {
            readGroups(api, base); fail("502 is not an answer")
        } catch (e: ApiException) {
            assertEquals(502, e.status)
        }
    }

    @Test fun groupsShowOnlyForAPairedPhoneOnTheServerThatAnswered() {
        val list = MusicGroupsList(listOf(MusicGroup(1, "Upstairs", listOf("office", "bedroom"))))
        val read = GroupsRead("http://192.168.1.20:6370", list)
        assertNotNull(groupsFor(read, "http://192.168.1.20:6370", paired = true))
        assertNull(groupsFor(read, "http://192.168.1.20:6370", paired = false))
        // Server A's answer never shows under server B.
        assertNull(groupsFor(read, "http://192.168.1.30:6370", paired = true))
        assertNull(groupsFor(null, "http://192.168.1.20:6370", paired = true))
    }

    @Test fun leaveIsTheHouseholdsAndOnlyInAGroup() {
        val g = NowPlayingGroup("s1", null, "office", listOf("office", "bedroom"))
        assertTrue(roomQueueControls(RoomQueue(roomId = "bedroom", group = g)).leave)
        assertFalse(roomQueueControls(RoomQueue(roomId = "bedroom", group = g, guest = true)).leave)
        assertFalse(roomQueueControls(RoomQueue(roomId = "bedroom")).leave)
    }

    // ─── words ────────────────────────────────────────────────────────────

    @Test fun theChipNamesTheGroupOrTheRoomsItPlaysWith() {
        val named = NowPlayingGroup("s1", "Upstairs", "office", listOf("office", "garage", "bedroom"))
        assertEquals("Upstairs · with garage, bedroom", groupChipText("office", named))
        assertEquals("Upstairs · with office, bedroom", groupChipText("garage", named))
        val loose = NowPlayingGroup("s2", null, "office", listOf("office", "garage"))
        assertEquals("with garage", groupChipText("office", loose))
        val everywhere = NowPlayingGroup("s3", "Everywhere", "kitchen", listOf("kitchen", "porch"), everywhere = true)
        assertEquals("Everywhere · with porch", groupChipText("kitchen", everywhere))
        assertNull(groupChipText("office", null))
    }

    @Test fun theQueueLineCountsTheOtherRooms() {
        val one = NowPlayingGroup("s1", null, "office", listOf("office", "garage"))
        assertEquals("shared with 1 other room (garage)", queueGroupLine("office", one))
        val two = NowPlayingGroup("s1", "Upstairs", "office", listOf("office", "garage", "bedroom"))
        assertEquals("Upstairs · shared with 2 other rooms (office, garage)", queueGroupLine("bedroom", two))
        assertNull(queueGroupLine("office", null))
    }

    @Test fun theFormNeedsANameAndTwoRooms() {
        assertFalse(groupFormReady("  ", setOf("a", "b"), busy = false))
        assertFalse(groupFormReady("Upstairs", setOf("a"), busy = false))
        assertFalse(groupFormReady("Upstairs", setOf("a", "b"), busy = true))
        assertTrue(groupFormReady("Upstairs", setOf("a", "b"), busy = false))
        assertEquals("no groups yet", groupsEmptyText(2))
        assertEquals("set up two rooms to make a group", groupsEmptyText(1))
        // A room gone from the roster stays pickable in its group's form.
        assertEquals(listOf("attic", "garage", "office"), groupFormRooms(listOf("office", "garage"), MusicGroup(1, "x", listOf("attic", "office"))))
    }
}
