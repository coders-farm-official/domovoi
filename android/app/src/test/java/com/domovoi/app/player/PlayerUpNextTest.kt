package com.domovoi.app.player

import androidx.media3.common.MediaItem
import com.domovoi.app.net.ApiException
import com.domovoi.app.testing.CastRig
import com.domovoi.app.testing.RecordingExoPlayer
import com.domovoi.app.testing.awaitUntil
import com.domovoi.app.testing.libraryQueue
import com.domovoi.app.testing.testPlayer
import com.domovoi.app.ui.screens.music.UpNextContext
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Up next inside the real [PlayerController] (its queue and the media
 * items it hands the engine): where "add to queue" and "play next" land
 * among the picks, what each kind of play tags its songs with and starts,
 * a newer play replacing the session, a removed pick told to the session,
 * and the cast carrying the session to a room and back.
 */
class PlayerUpNextTest {

    /** The server's side, scripted; every call logged. */
    private class Api : UpNextApi {
        val calls = java.util.concurrent.CopyOnWriteArrayList<Pair<String, JsonObject?>>()
        var picks: List<UpNextPick> = listOf(p(1), p(2), p(3))
        var moveFails: Int? = null
        override suspend fun start(server: String, body: JsonObject): UpNextSession {
            calls += "start" to body
            return UpNextSession("s1", mode = "library", picks = picks, haveThroughSeq = picks.maxOfOrNull { it.seq } ?: 0)
        }
        override suspend fun next(server: String, id: String, haveThrough: Int): UpNextBatch {
            calls += "next $haveThrough" to null
            return UpNextBatch()
        }
        override suspend fun feedback(server: String, id: String, seq: Int, kind: String): List<Int> {
            calls += "feedback $seq $kind" to null
            return listOf(seq)
        }
        override suspend fun patch(server: String, id: String, body: JsonObject): UpNextSession? = null
        override suspend fun read(server: String, id: String): UpNextSession = UpNextSession(id)
        override suspend fun move(server: String, id: String, target: JsonObject): UpNextSession? {
            calls += "move $id" to target
            moveFails?.let { throw ApiException(it, "$it") }
            return null
        }
        fun named(prefix: String) = calls.filter { it.first.startsWith(prefix) }

        companion object {
            fun p(seq: Int) = UpNextPick(seq, 900L + seq, "Pick $seq", "Artist", "Album", 200.0, "same_artist", "same artist")
        }
    }

    private class Env(private val base: () -> String) : PhoneUpNext.Env {
        var autoplay = true
        override fun server(): String = base()
        override fun paired() = true
        override fun deviceId() = "android-1a2b"
        override fun autoplay(): Boolean = autoplay
        override fun saveAutoplay(on: Boolean) { autoplay = on }
        override fun listenerPick(server: String): String? = null
        override fun shared(server: String) = false
        override fun setListener(personId: String?) {}
    }

    private val api = Api()

    private fun wire(player: PlayerController, server: () -> String = { "http://192.0.2.1:6369" }): PhoneUpNext =
        PhoneUpNext(api, player.upNextHost, Env(server), launch = { block -> runBlocking { block() } })
            .also { player.upNext = it }

    private val songs = libraryQueue("Stone Floor", "Old Barrels", "Quiet Jars")

    @Test fun addToQueueGoesBeforeTheWaitingPicksAndPlayNextRightAfterTheSong() {
        val exo = RecordingExoPlayer()
        val player = testPlayer(exo)
        wire(player)
        player.playFromList(songs, 0, null)
        assertEquals(listOf(songs[0].id, 901L, 902L, 903L), player.queue.value.map { it.id })
        exo.calls.clear()

        val added = PlayItem.fromTrack(77, "Added", "Band", null, 100.0)
        player.enqueue(listOf(added))
        assertEquals(listOf(songs[0].id, 77L, 901L, 902L, 903L), player.queue.value.map { it.id })
        val (at, item) = exo.named("addMediaItem").single()
        assertEquals("the engine gets it at the same place", 1, at)
        assertEquals(added.uid, (item as MediaItem).mediaId)
        assertEquals(QueueOrigin.Explicit, player.queue.value[1].queueOrigin)

        val next = PlayItem.fromTrack(88, "Next", "Band", null, 100.0)
        player.playNext(listOf(next))
        assertEquals(listOf(songs[0].id, 88L, 77L, 901L, 902L, 903L), player.queue.value.map { it.id })
        // The count leaves the waiting picks out.
        assertEquals(3, UpNextRules.count(player.queue.value, player.index.value))
    }

    @Test fun aSongTappedInAListPlaysAloneTaggedForTheSession() {
        val player = testPlayer()
        wire(player)
        player.playFromList(songs, 1, UpNextContext("playlist", ref = "4", name = "Cellar songs"))
        val q = player.queue.value
        assertEquals(songs[1].id, q[0].id)
        assertEquals(QueueOrigin.Explicit, q[0].queueOrigin)
        assertEquals("the session's id came back onto the song", "s1", q[0].sessionId)
        assertTrue(q.drop(1).all { it.queueOrigin == QueueOrigin.Auto && it.sessionId == "s1" })
        val start = api.named("start").single().second!!["start"]!!.jsonObject
        assertEquals(songs[1].id, start["track_id"]!!.jsonPrimitive.long)
        assertEquals("playlist", start["context"]!!.jsonObject["kind"]!!.jsonPrimitive.content)
    }

    @Test fun aWholeListIsTheQueueAsItIsTaggedList() {
        val player = testPlayer()
        wire(player)
        api.picks = emptyList() // continue mode: nothing until asked
        player.playList(songs, UpNextContext("genre", ref = "soul", name = "Soul"))
        assertEquals(songs.map { it.id }, player.queue.value.map { it.id })
        assertTrue(player.queue.value.all { it.queueOrigin == QueueOrigin.WholeList && it.sessionId == "s1" })
        val start = api.named("start").single().second!!["start"]!!.jsonObject
        assertTrue(start["whole_list"]!!.jsonPrimitive.boolean)
        assertEquals(songs[0].id, start["track_id"]!!.jsonPrimitive.long)
    }

    @Test fun aNewPlayReplacesTheSessionAndWhatItPlaysWasAskedFor() {
        val player = testPlayer()
        val upNext = wire(player)
        player.playFromList(songs, 0, null)
        val old = player.queue.value
        assertEquals("s1", upNext.state.value!!.id)
        // A restored queue (the assistant's snapshot) brings old picks back.
        api.picks = emptyList()
        player.playItems(old, 0)
        assertTrue(
            "its picks are just songs now",
            player.queue.value.all { it.queueOrigin == QueueOrigin.Explicit && it.upNextSeq == null },
        )
        // The old session is gone: the queue (four songs, near its end)
        // starts one of its own.
        val starts = api.named("start")
        assertEquals(2, starts.size)
        assertEquals("this queue", starts[1].second!!["start"]!!.jsonObject["context"]!!.jsonObject["name"]!!.jsonPrimitive.content)
        player.clearQueue()
        assertNull(upNext.state.value)
    }

    @Test fun aRemovedPickIsToldToTheSession() {
        val exo = RecordingExoPlayer()
        val player = testPlayer(exo)
        wire(player)
        player.playFromList(songs, 0, null)
        player.removeAt(2)
        assertEquals("feedback 2 remove", api.named("feedback").single().first)
        assertEquals(listOf(901, 903).map { it.toLong() }, player.queue.value.drop(1).map { it.id })
    }

    @Test fun picksTakenBackLeaveTheEngineToo() {
        val exo = RecordingExoPlayer()
        val player = testPlayer(exo)
        wire(player)
        player.playFromList(songs, 0, null)
        exo.calls.clear()
        player.upNextHost.removeWaiting { it.upNextSeq == 1 || it.upNextSeq == 3 }
        assertEquals("highest first, so the indices hold", listOf(listOf<Any?>(3), listOf<Any?>(1)), exo.named("removeMediaItem"))
        assertEquals(listOf(songs[0].id, 902L), player.queue.value.map { it.id })
    }

    // ── Casting (the web's music routes on a MockWebServer) ─────────────

    @Test fun aCastCarriesTheSessionAndAHandBackMovesItHome() {
        CastRig().use { rig ->
            val player = rig.player
            val upNext = wire(player)
            player.playFromList(libraryQueue("Stone Floor", "Old Barrels"), 0, null)
            assertEquals("s1", upNext.state.value!!.id)
            rig.room("office", "play", "Stone Floor", 3.0)
            runBlocking { player.castTo("office") }
            val cast = rig.log.first { it.startsWith("POST /api/music/play-tracks") }
            assertTrue(cast, "\"up_next_session_id\":\"s1\"" in cast)
            rig.awaitRemote("office")

            // While casting the phone asks nothing: the room refills itself.
            val before = api.named("next").size
            upNext.check()
            assertEquals(before, api.named("next").size)

            runBlocking { player.castTo(null) }
            awaitUntil(what = "the move") { api.named("move").isNotEmpty() }
            val (call, target) = api.named("move").single()
            assertEquals("move s1", call)
            assertEquals("android-1a2b", target!!["device_id"]!!.jsonPrimitive.content)
            assertEquals("s1", upNext.state.value!!.id)
        }
    }

    @Test fun aHandBackTheRoomRefusedDropsTheSession() {
        CastRig().use { rig ->
            val player = rig.player
            val upNext = wire(player)
            api.moveFails = 409
            player.playFromList(libraryQueue("Stone Floor", "Old Barrels"), 0, null)
            rig.room("office", "play", "Stone Floor", 3.0)
            runBlocking { player.castTo("office") }
            rig.awaitRemote("office")
            api.picks = emptyList()
            runBlocking { player.castTo(null) }
            awaitUntil(what = "the move") { api.named("move").isNotEmpty() }
            // Dropped; the queue starts a new one of its own near its end.
            awaitUntil(what = "a new start") { api.named("start").size == 2 }
            assertTrue(upNext.gen > 1)
            assertEquals("this queue", api.named("start")[1].second!!["start"]!!.jsonObject["context"]!!.jsonObject["name"]!!.jsonPrimitive.content)
        }
    }
}
