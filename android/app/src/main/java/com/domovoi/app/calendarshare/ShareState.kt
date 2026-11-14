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
    /** The names of the ticked calendars, by id, so one that is gone from
     *  the phone can be named ("Work is no longer on this phone"). */
    val calendarNames: Map<Long, String> = emptyMap(),
    /** [SharePayload.hash] of the last send that went: the same again is
     *  not sent (except once a day, [ShareRules.RESEND_MS]). */
    val lastHash: String? = null,
    /** Something the person should know that is not a failed call: a
     *  ticked calendar gone from the phone, or the permission taken back
     *  (which turned sharing off). Cleared when they next change it. */
    val notice: String? = null,
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
        (e ?: ShareEntry()).copy(enabled = true, pendingDelete = false, lastError = null, notice = null, lastHash = null)

    /** Turned off: the house's copy must go. Only a phone that may have sent
     *  something has anything to delete; marking it anyway is harmless (the
     *  server answers 204 for a phone it has nothing from). */
    fun turnOff(e: ShareEntry?): ShareEntry =
        (e ?: ShareEntry()).copy(enabled = false, pendingDelete = true, lastError = null, notice = null, lastHash = null)

    /** The phone's calendar permission was taken back: as good as turned
     *  off (the house's copy is deleted), and said. */
    fun permissionLost(e: ShareEntry?): ShareEntry = turnOff(e).copy(notice = PERMISSION_LOST)

    /** Ticked calendars the phone no longer shows ([gone], by id): unticked,
     *  and named in [ShareEntry.notice]. */
    fun vanished(e: ShareEntry, gone: Set<Long>): ShareEntry {
        if (gone.isEmpty()) return e
        val names = gone.sorted().map { e.calendarNames[it] ?: "A calendar" }.distinct()
        val said = names.joinToString(", ") +
            if (names.size == 1) " is no longer on this phone, so it is no longer shared"
            else " are no longer on this phone, so they are no longer shared"
        return e.copy(
            calendarIds = e.calendarIds.filter { it !in gone },
            calendarNames = e.calendarNames - gone,
            notice = said,
        )
    }

    /** The calendars picked now. An unpicked one is simply left out of the
     *  next send, and the server deletes it. */
    fun pick(e: ShareEntry?, ids: Collection<Long>): ShareEntry {
        val base = e ?: ShareEntry()
        val keep = ids.distinct().sorted()
        return base.copy(calendarIds = keep, calendarNames = base.calendarNames.filterKeys { it in keep }, notice = null)
    }

    /** Ticked or unticked one calendar. Nothing is ticked until the person
     *  ticks it: turning sharing on sends nothing by itself. */
    fun toggle(e: ShareEntry?, id: Long, on: Boolean, name: String? = null): ShareEntry {
        val now = (e ?: ShareEntry()).calendarIds
        val picked = pick(e, if (on) now + id else now - id)
        return if (on && name != null) picked.copy(calendarNames = picked.calendarNames + (id to name)) else picked
    }

    /** A send went: when, what it said ([hash]), and how many events were
     *  left out. */
    fun sent(e: ShareEntry, nowMs: Long, dropped: Int, hash: String? = null): ShareEntry =
        e.copy(lastSentAtMs = nowMs, lastError = null, notBeforeMs = null, lastDropped = dropped, lastHash = hash)

    /** The delete went (or there was nothing to delete). */
    fun deleted(e: ShareEntry): ShareEntry =
        e.copy(pendingDelete = false, lastError = null, notBeforeMs = null, lastSentAtMs = null, lastDropped = 0, lastHash = null)

    /** An unchanged snapshot is sent again after this long anyway, so the
     *  server's window keeps moving. */
    const val RESEND_MS = 24L * 60 * 60 * 1000

    /** Whether a send with [hash] can be skipped: the house already has
     *  exactly that, sent less than [RESEND_MS] ago, and the last call
     *  did not fail. */
    fun unchanged(e: ShareEntry, hash: String, nowMs: Long): Boolean {
        val last = e.lastSentAtMs ?: return false
        return e.lastHash == hash && e.lastError == null && nowMs - last in 0 until RESEND_MS
    }

    /** Whether the house may hold a copy from this phone. */
    fun mayHoldCopy(e: ShareEntry?): Boolean = e != null && (e.enabled || e.pendingDelete || e.lastSentAtMs != null)

    /** A send or delete failed: [error] for the settings line, and a
     *  [retryAfterSec] from a 429 kept as a "not before". The pending
     *  delete stays set, so it is tried again. */
    fun failed(e: ShareEntry, error: String, nowMs: Long, retryAfterSec: Long? = null): ShareEntry =
        e.copy(lastError = error, notBeforeMs = retryAfterSec?.let { nowMs + it * 1000 } ?: e.notBeforeMs)

    /** How long to wait before the next call (0: go). */
    fun waitMs(e: ShareEntry?, nowMs: Long): Long =
        (e?.notBeforeMs?.minus(nowMs) ?: 0L).coerceAtLeast(0L)

    /** Whether anything needs the background job: the ACTIVE server, while
     *  this phone is [paired] with it, sharing or with a copy still to
     *  delete. A server switched away from keeps its entry, and the job
     *  comes back when it is the active one again
     *  ([CalendarShare.serverChanged]); a forgotten one has no entry. */
    fun needsJob(all: Map<String, ShareEntry>, active: String, paired: Boolean): Boolean {
        if (!paired || active.isBlank()) return false
        val e = all[active] ?: return false
        return e.enabled || e.pendingDelete
    }

    /** Plain words for a failed call, for the settings line. */
    fun errorText(status: Int?): String = when (status) {
        null -> "couldn't reach your Domovoi; trying again later"
        401, 403 -> "your Domovoi refused this phone; check it is still paired"
        404 -> "your Domovoi is too old to show phone calendars; update it"
        413 -> "too many events to send; pick fewer calendars"
        429 -> BUSY_TEXT
        else -> if (status >= 500) "your Domovoi had a problem; trying again later" else "your Domovoi turned it down ($status)"
    }

    /** A 429: the house asked this phone to wait (often while it is being
     *  set up, several ticks in a row). Not a fault, so not in the warning
     *  colour. */
    const val BUSY_TEXT = "sent a moment ago; trying again shortly"

    /** A PUT refused with 403: the house no longer takes this phone's
     *  calendar key. Nothing more is sent until sharing is turned off and
     *  on again. */
    const val KEY_REFUSED =
        "the house no longer accepts this phone's calendars: turn sharing off and on again"

    /** A DELETE refused with 403: the copy is the house's to remove now. */
    const val KEY_REFUSED_OFF =
        "the house no longer accepts this phone's calendar key, so it may still show them: an admin can " +
            "remove them in the dashboard (Settings → Household → Calendars)"

    /** Whether the settings line is in the warning colour. */
    fun warns(e: ShareEntry?): Boolean =
        e != null && (e.notice != null || (e.lastError != null && e.lastError != BUSY_TEXT))

    const val PERMISSION_LOST =
        "Domovoi can no longer read this phone's calendars, so sharing is off. To share again, allow " +
            "Calendars for Domovoi in the phone's settings, then turn this on."
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
        val text = runCatching { file.readText() }.getOrNull() ?: return emptyMap()
        runCatching { JSON.decodeFromString(SERIALIZER, text) }.onSuccess { return it }
        // Unreadable: moved aside rather than overwritten by the next save,
        // and every server it names is treated as turned off, so a pending
        // delete is never lost. A delete to a server holding nothing from
        // this phone is answered 204; sharing is turned on again in a tap.
        runCatching { AtomicFiles.replace(file, File(file.parentFile, file.name + ".corrupt")) }
        val salvaged = salvage(text)
        if (salvaged.isNotEmpty()) runCatching { save(salvaged) }
        return salvaged
    }

    private fun save(map: Map<String, ShareEntry>) {
        file.parentFile?.mkdirs()
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(JSON.encodeToString(SERIALIZER, map))
        AtomicFiles.replace(tmp, file)
    }

    companion object {
        /** The servers an unreadable file names (its keys are addresses),
         *  each marked turned off with its copy to delete. */
        internal fun salvage(text: String): Map<String, ShareEntry> =
            Regex(""""(https?://[^"\\]+)"\s*:""").findAll(text)
                .map { it.groupValues[1] }.distinct()
                .associateWith { ShareRules.turnOff(null) }

        private val JSON = Json { ignoreUnknownKeys = true; explicitNulls = false; encodeDefaults = true }
        private val SERIALIZER = MapSerializer(String.serializer(), ShareEntry.serializer())
    }
}
