package com.domovoi.app.ui.screens.music

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import java.io.IOException

/*
 * A room's queue, through the web's existing room-queue routes
 * (web/backend/api/music_queue.py): read, add, move, remove, clear. Nothing
 * new on the server: "play next" and "play this one now" are made of them.
 *
 *  - play next  = add (appends; the answer names the new entries' songids),
 *                 then move each of those to just after the playing entry.
 *  - play now   = move the entry to just after the playing one, then skip
 *                 (`POST /api/music/skip/{room}`). The web has no jump for a
 *                 room's queue; this is that, from the same two routes.
 *
 * Every step after the first goes only to the server the first went to: a
 * chain that started on one Domovoi stops if the app moved to another
 * meanwhile ([ServerMovedException]), rather than moving or skipping some
 * other house's songs by the same ids.
 */

/** The app was pointed at another server between two steps of one action. */
internal class ServerMovedException : IOException("the app moved to another server")

internal class RoomQueueApi(
    private val api: ApiClient,
    /** Which server [api] talks to now (Prefs.serverUrl). */
    private val server: () -> String,
    private val deviceId: () -> String,
) {
    private fun path(room: String, tail: String = "") = "/api/music/queue/${encComponent(room)}$tail"

    /** Runs [steps] against the server current now, each step checking it still is. */
    private suspend fun <T> onOneServer(steps: suspend (check: () -> Unit) -> T): T {
        val at = server()
        return steps { if (server() != at) throw ServerMovedException() }
    }

    suspend fun read(room: String): RoomQueue =
        api.get(path(room, "?device_id=${encComponent(deviceId())}")).decode()

    /** Appends [trackIds]; the songids the room gave the new entries, in order. */
    suspend fun add(room: String, trackIds: List<Long>): List<Long> {
        val answer = api.post(path(room, "/add"), buildJsonObject {
            putJsonArray("track_ids") { trackIds.forEach { add(it) } }
            put("device_id", deviceId())
        })
        return queuedSongIds(answer as? JsonObject)
    }

    suspend fun move(room: String, songId: Long, toPosition: Int) {
        api.post(path(room, "/move"), buildJsonObject {
            put("song_id", songId)
            put("to_position", toPosition)
            put("device_id", deviceId())
        })
    }

    suspend fun remove(room: String, songIds: List<Long>) {
        api.post(path(room, "/remove"), buildJsonObject {
            putJsonArray("song_ids") { songIds.forEach { add(it) } }
            put("device_id", deviceId())
        })
    }

    suspend fun clear(room: String) {
        api.post(path(room, "/clear"), buildJsonObject { put("device_id", deviceId()) })
    }

    /**
     * [trackIds] right after the song the room is playing, in order. With
     * nothing playing they are simply added (and an idle room starts on
     * them, as an add does). Returns how many the room took.
     */
    suspend fun playNext(room: String, trackIds: List<Long>): Int = onOneServer { check ->
        val before = read(room)
        check()
        val at = playingPos(before)
        val added = add(room, trackIds)
        if (at != null) {
            added.forEachIndexed { i, songId ->
                check()
                move(room, songId, at + 1 + i)
            }
        }
        added.size
    }

    /** Plays [songId] now: it moves to just after the playing entry and the room skips to it. */
    suspend fun playNow(room: String, queue: RoomQueue, songId: Long) = onOneServer { check ->
        val at = playingPos(queue) ?: error("nothing is playing in $room")
        val pos = queue.items.firstOrNull { it.songId == songId }?.pos ?: error("that song left the queue")
        if (pos != at + 1) {
            move(room, songId, if (pos > at) at + 1 else at)
            check()
        }
        api.post("/api/music/skip/${encComponent(room)}")
    }

    companion object {
        /** The playing entry's position, or null when nothing is. */
        fun playingPos(q: RoomQueue): Int? =
            q.currentSongId?.let { cur -> q.items.firstOrNull { it.songId == cur }?.pos }

        /** `queued: [{song_id, file}]` from an add's answer. */
        fun queuedSongIds(answer: JsonObject?): List<Long> =
            (answer?.get("queued") as? JsonArray).orEmpty().mapNotNull {
                (it as? JsonObject)?.get("song_id")?.jsonPrimitive?.longOrNull
            }
    }
}

/**
 * The server's reason in words (the web's apiErrorText): `detail` from the
 * error body when it is a sentence, `detail.error.message` for a coded one,
 * else the message.
 */
internal fun apiErrorText(e: Throwable): String {
    if (e is ApiException && e.body.isNotBlank()) {
        val detail = runCatching { DomovoiJson.parseToJsonElement(e.body).jsonObject["detail"] }.getOrNull()
        when {
            detail is JsonPrimitive && detail.isString -> return detail.content
            detail is JsonObject -> {
                val coded = (detail["error"] as? JsonObject)?.get("message") as? JsonPrimitive
                return if (coded != null && coded.isString) coded.content else detail.toString()
            }
        }
    }
    return e.message?.takeIf { it.isNotBlank() } ?: e.javaClass.simpleName
}
