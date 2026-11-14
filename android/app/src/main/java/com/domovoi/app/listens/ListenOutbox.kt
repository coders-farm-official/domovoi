package com.domovoi.app.listens

import com.domovoi.app.data.AtomicFiles
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.File
import java.time.Instant
import java.time.temporal.ChronoUnit

/**
 * One listen waiting to go to `POST /api/music/listens`.
 *
 * [id] is this outbox's own (never sent): a flush deletes exactly the ids
 * it sent, so a song that ended while that flush was on its way stays.
 * [server] is the server the song came from (normalised address): a
 * track id means nothing on another server, so a row only ever goes to its
 * own.
 */
@Serializable
data class OutboxRow(
    val id: String,
    val server: String,
    @SerialName("track_id") val trackId: Long,
    @SerialName("device_id") val deviceId: String,
    @SerialName("started_at") val startedAt: String,
    @SerialName("played_sec") val playedSec: Int,
    @SerialName("duration_sec") val durationSec: Int? = null,
    val outcome: String,
    @SerialName("listener_person_id") val listenerPersonId: Long? = null,
    @SerialName("as_household") val asHousehold: Boolean = false,
) {
    /** The row as the server takes it (API_REFERENCE §3.5e). */
    fun toWire(): JsonObject = buildJsonObject {
        put("track_id", trackId)
        put("device_id", deviceId)
        put("started_at", startedAt)
        put("played_sec", playedSec)
        durationSec?.let { put("duration_sec", it) }
        put("outcome", outcome)
        listenerPersonId?.let { put("listener_person_id", it) }
        if (asHousehold) put("as_household", true)
        put("player", "android")
        put("origin", "explicit")
    }

    companion object {
        /** `2026-11-12T18:03:11Z`: whole seconds, UTC. */
        fun isoUtc(epochMs: Long): String = Instant.ofEpochMilli(epochMs).truncatedTo(ChronoUnit.SECONDS).toString()
    }
}

/**
 * The listening-history outbox: the phone's listens wait here until the
 * server has taken them, across restarts and days offline (the server
 * takes rows up to 7 days late).
 *
 * Kept as ONE JSON file in the app's private files, replaced atomically on
 * every change ([AtomicFiles], a temp file renamed over it): a reader sees
 * the old list or the new one, never half of one, and a process killed
 * mid-write loses at most that write. Not Room (the hand-off's word): the
 * app has no androidx.room dependency and builds offline, and the
 * framework's SQLite can't be exercised by the JVM tests; at 500 short rows
 * a file is small (~100 KB at most) and read once per change.
 *
 * At most [MAX] rows: the oldest go first. Thread safe (one lock).
 */
class ListenOutbox(private val file: File) {
    private val lock = Any()

    fun all(): List<OutboxRow> = synchronized(lock) { load() }

    /** Add [row] at the end, dropping the oldest past [MAX]. */
    fun add(row: OutboxRow) = synchronized(lock) {
        save((load() + row).takeLast(MAX))
    }

    /** The oldest [max] rows waiting for [server]. */
    fun batch(server: String, max: Int = BATCH): List<OutboxRow> = synchronized(lock) {
        load().filter { it.server == server }.take(max)
    }

    /** Delete exactly [ids] (a flush's own rows). */
    fun remove(ids: Collection<String>) = synchronized(lock) {
        if (ids.isEmpty()) return@synchronized
        val gone = ids.toHashSet()
        val rows = load()
        val kept = rows.filterNot { it.id in gone }
        if (kept.size != rows.size) save(kept)
    }

    /** Forget every row for [server] (the phone forgot that server). */
    fun forgetServer(server: String) = synchronized(lock) {
        val rows = load()
        val kept = rows.filterNot { it.server == server }
        if (kept.size != rows.size) save(kept)
    }

    private fun load(): List<OutboxRow> {
        if (!file.exists()) return emptyList()
        return runCatching { JSON.decodeFromString(SERIALIZER, file.readText()) }.getOrDefault(emptyList())
    }

    private fun save(rows: List<OutboxRow>) {
        file.parentFile?.mkdirs()
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(JSON.encodeToString(SERIALIZER, rows))
        AtomicFiles.replace(tmp, file)
    }

    companion object {
        /** The most rows kept (the oldest go first). */
        const val MAX = 500

        /** The most rows one POST carries (the server's MAX_BATCH). */
        const val BATCH = 200

        private val JSON = Json { ignoreUnknownKeys = true; explicitNulls = false }
        private val SERIALIZER = ListSerializer(OutboxRow.serializer())
    }
}
