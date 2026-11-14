package com.domovoi.app.calendarshare

import com.domovoi.app.data.AtomicFiles
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import java.io.File

/**
 * Calendar sharing for one server (by normalised address): a server this
 * phone is paired with does not get the calendars just because another
 * one does.
 *
 * [pendingDelete]: sharing was turned off and the server has not yet
 * confirmed it deleted its copy. It stays true (and the job keeps trying)
 * until a DELETE answers, so the house's copy never stays behind silently.
 */
@Serializable
data class ShareEntry(
    val enabled: Boolean = false,
    /** CalendarContract.Calendars._ID of the picked calendars. */
    val calendarIds: List<Long> = emptyList(),
    val pendingDelete: Boolean = false,
    /** When the server last took a send (epoch ms). */
    val lastSentAtMs: Long? = null,
    /** What the last send got: a plain sentence; null when it went. */
    val lastError: String? = null,
    /** Not before this (a 429's Retry-After), epoch ms. */
    val notBeforeMs: Long? = null,
    /** Events left out of the last send to stay under the server's caps. */
    val lastDropped: Int = 0,
)

/** What the job does for a server now. */
enum class ShareAction { Put, Delete, Nothing }

object ShareRules {
    /** Sharing on → send the set (once something is picked, or to empty a
     *  copy sent before); off with a copy to remove → delete it. */
    fun action(e: ShareEntry?): ShareAction = when {
        e == null -> ShareAction.Nothing
        e.enabled -> if (e.calendarIds.isNotEmpty() || e.lastSentAtMs != null) ShareAction.Put else ShareAction.Nothing
        e.pendingDelete -> ShareAction.Delete
        else -> ShareAction.Nothing
    }

    /** Turned on: nothing is waiting to be deleted any more (the next send
     *  replaces the house's copy whole). */
    fun turnOn(e: ShareEntry?): ShareEntry =
        (e ?: ShareEntry()).copy(enabled = true, pendingDelete = false, lastError = null)

    /** Turned off: the house's copy must go. Only a phone that may have sent
     *  something has anything to delete; marking it anyway is harmless (the
     *  server answers 204 for a phone it has nothing from). */
    fun turnOff(e: ShareEntry?): ShareEntry =
        (e ?: ShareEntry()).copy(enabled = false, pendingDelete = true, lastError = null)

    /** The calendars picked now. An unpicked one is simply left out of the
     *  next send, and the server deletes it. */
    fun pick(e: ShareEntry?, ids: Collection<Long>): ShareEntry =
        (e ?: ShareEntry()).copy(calendarIds = ids.distinct().sorted())

    /** Ticked or unticked one calendar. Nothing is ticked until the person
     *  ticks it: turning sharing on sends nothing by itself. */
    fun toggle(e: ShareEntry?, id: Long, on: Boolean): ShareEntry {
        val now = (e ?: ShareEntry()).calendarIds
        return pick(e, if (on) now + id else now - id)
    }

    /** A send went: when, and how many events were left out. */
    fun sent(e: ShareEntry, nowMs: Long, dropped: Int): ShareEntry =
        e.copy(lastSentAtMs = nowMs, lastError = null, notBeforeMs = null, lastDropped = dropped)

    /** The delete went (or there was nothing to delete). */
    fun deleted(e: ShareEntry): ShareEntry =
        e.copy(pendingDelete = false, lastError = null, notBeforeMs = null, lastSentAtMs = null, lastDropped = 0)

    /** A send or delete failed: [error] for the settings line, and a
     *  [retryAfterSec] from a 429 kept as a "not before". The pending
     *  delete stays set, so it is tried again. */
    fun failed(e: ShareEntry, error: String, nowMs: Long, retryAfterSec: Long? = null): ShareEntry =
        e.copy(lastError = error, notBeforeMs = retryAfterSec?.let { nowMs + it * 1000 } ?: e.notBeforeMs)

    /** How long to wait before the next call (0: go). */
    fun waitMs(e: ShareEntry?, nowMs: Long): Long =
        (e?.notBeforeMs?.minus(nowMs) ?: 0L).coerceAtLeast(0L)

    /** Whether anything needs the background job: a server sharing, or a
     *  copy still to delete. */
    fun needsJob(all: Map<String, ShareEntry>): Boolean = all.values.any { it.enabled || it.pendingDelete }

    /** Plain words for a failed call, for the settings line. */
    fun errorText(status: Int?): String = when (status) {
        null -> "couldn't reach your Domovoi; trying again later"
        401, 403 -> "your Domovoi refused this phone; check it is still paired"
        404 -> "your Domovoi is too old to show phone calendars; update it"
        413 -> "too many events to send; pick fewer calendars"
        429 -> "sent a moment ago; trying again shortly"
        else -> if (status >= 500) "your Domovoi had a problem; trying again later" else "your Domovoi turned it down ($status)"
    }
}

/**
 * The per-server entries, one JSON file in the app's private files
 * (outside backups like everything the app keeps), replaced atomically.
 * [state] is what the settings panel watches. Thread safe.
 */
class ShareStore(private val file: File) {
    private val lock = Any()
    private val _state = MutableStateFlow(load())
    val state: StateFlow<Map<String, ShareEntry>> = _state

    fun get(server: String): ShareEntry? = _state.value[server]

    fun all(): Map<String, ShareEntry> = _state.value

    /** Change [server]'s entry ([f] gets null when there is none). */
    fun update(server: String, f: (ShareEntry?) -> ShareEntry): ShareEntry = synchronized(lock) {
        val next = f(_state.value[server])
        val map = _state.value + (server to next)
        save(map)
        _state.value = map
        next
    }

    /** Drop [server]'s entry (the phone forgot that server). */
    fun forget(server: String) = synchronized(lock) {
        if (server !in _state.value) return@synchronized
        val map = _state.value - server
        save(map)
        _state.value = map
    }

    private fun load(): Map<String, ShareEntry> {
        if (!file.exists()) return emptyMap()
        return runCatching { JSON.decodeFromString(SERIALIZER, file.readText()) }.getOrDefault(emptyMap())
    }

    private fun save(map: Map<String, ShareEntry>) {
        file.parentFile?.mkdirs()
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(JSON.encodeToString(SERIALIZER, map))
        AtomicFiles.replace(tmp, file)
    }

    companion object {
        private val JSON = Json { ignoreUnknownKeys = true; explicitNulls = false; encodeDefaults = true }
        private val SERIALIZER = MapSerializer(String.serializer(), ShareEntry.serializer())
    }
}
