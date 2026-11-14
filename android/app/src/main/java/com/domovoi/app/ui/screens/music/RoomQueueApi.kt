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
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray

/*
 * A room's queue, through the web's room-queue routes
 * (web/backend/api/music_queue.py, API_REFERENCE §3.5a): read, add (after
 * the playing song, or at the end before the room's Up next picks), move,
 * remove, clear, play one entry now (the room's real jump, MPD playid) and
 * save the queue as a playlist; and the room's Up next session (§3.5f):
 * its autoplay switch and "less like this". Each is ONE request: nothing
 * here chains a second call on the first one's answer.
 */

internal class RoomQueueApi(
    private val api: ApiClient,
    private val deviceId: () -> String,
) {
    private fun path(room: String, tail: String = "") = "/api/music/queue/${encComponent(room)}$tail"

    suspend fun read(room: String): RoomQueue =
        api.get(path(room, "?device_id=${encComponent(deviceId())}")).decode()

    /**
     * Adds [trackIds]: [where] `next` right after the song playing, `end`
     * after the songs somebody asked for, before the room's Up next picks.
     * The songids the room gave the new entries, in order.
     */
    suspend fun add(room: String, trackIds: List<Long>, where: String = WHERE_END): List<Long> {
        val answer = api.post(path(room, "/add"), buildJsonObject {
            putJsonArray("track_ids") { trackIds.forEach { add(it) } }
            put("device_id", deviceId())
            put("where", where)
        })
        return queuedSongIds(answer as? JsonObject)
    }

    /** [trackIds] right after the song the room is playing, in order. How many the room took. */
    suspend fun playNext(room: String, trackIds: List<Long>): Int = add(room, trackIds, WHERE_NEXT).size

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

    /** Plays the entry [songId] now: the room's own jump (`404` when it left the queue). */
    suspend fun play(room: String, songId: Long) {
        api.post(path(room, "/play"), buildJsonObject {
            put("song_id", songId)
            put("device_id", deviceId())
        })
    }

    /** The room's queue, in order, as a new playlist named [name] (`409` taken, `422` nothing to save). */
    suspend fun savePlaylist(room: String, name: String): RoomSaveResult =
        api.post(path(room, "/save-playlist"), buildJsonObject {
            put("name", name)
            put("device_id", deviceId())
        }).decode()

    /** The room's autoplay switch: its session, PATCHed. */
    suspend fun setAutoplay(sessionId: String, on: Boolean) {
        api.patch(sessionPath(sessionId), buildJsonObject { put("autoplay", on) })
    }

    /** "Less like this" on the pick [seq]: the seqs the session took back. */
    suspend fun lessLikeThis(sessionId: String, seq: Int): List<Int> {
        val answer = api.post(sessionPath(sessionId, "/feedback"), buildJsonObject {
            put("seq", seq)
            put("kind", "less_like_this")
        })
        return removedSeqs(answer as? JsonObject)
    }

    private fun sessionPath(id: String, tail: String = "") = "/api/music/up-next/sessions/${encComponent(id)}$tail"

    companion object {
        const val WHERE_NEXT = "next"
        const val WHERE_END = "end"

        /** The playing entry's position, or null when nothing is. */
        fun playingPos(q: RoomQueue): Int? =
            q.currentSongId?.let { cur -> q.items.firstOrNull { it.songId == cur }?.pos }

        /** `queued: [{song_id, file}]` from an add's answer. */
        fun queuedSongIds(answer: JsonObject?): List<Long> =
            (answer?.get("queued") as? JsonArray).orEmpty().mapNotNull {
                (it as? JsonObject)?.get("song_id")?.jsonPrimitive?.longOrNull
            }

        /** `{removed: [seq]}` from a feedback's answer. */
        fun removedSeqs(answer: JsonObject?): List<Int> =
            (answer?.get("removed") as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.intOrNull }
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
