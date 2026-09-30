package com.domovoi.app.alerts

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json

// ---------------------------------------------------------------------------
// What the alerts remember between runs, in their own app-private DataStore
// file `alerts` (the app opts out of backups, so none of it leaves the phone):
//
//   alerted         "<serverKey>|<timerId>|<epochMs>" for every timer this
//                   phone has posted, so the live path and the alarm path
//                   post one timer once between them
//   fires_seen      {serverKey: the highest fire id this phone has processed}
//   mirror          the local alarm mirror: which timers have an alarm set,
//                   and the words to show when one rings (TimerAlarmMirror)
//   hint_dismissed  Home's "Timer alerts are off on this phone" said "not now"
//
// The rules over those values are pure functions below, for the JVM tests.
// ---------------------------------------------------------------------------

/** An alerted entry older than this is forgotten... */
internal const val ALERTED_KEEP_MS = 24 * 60 * 60 * 1000L

/** ...and the book never holds more than this many. */
internal const val ALERTED_MAX = 500

/** A server's first catch-up alerts only fires younger than this... */
internal const val FIRST_RUN_WINDOW_MS = 2 * 60 * 1000L

/** ...and every later one only fires younger than this. */
internal const val CATCH_UP_WINDOW_MS = 30 * 60 * 1000L

private val alertedSerializer = ListSerializer(String.serializer())
private val seenSerializer = MapSerializer(String.serializer(), Long.serializer())

/** Never throws: a corrupt or absent blob is an empty book. */
internal fun decodeAlerted(raw: String?): List<String> =
    runCatching { Json.decodeFromString(alertedSerializer, raw ?: "[]") }.getOrDefault(emptyList())

internal fun encodeAlerted(entries: List<String>): String = Json.encodeToString(alertedSerializer, entries)

/**
 * Record that [timerId] on [serverKey] was posted at [nowMs]. Returns the
 * next book (pruned of entries older than [ALERTED_KEEP_MS] and malformed
 * ones, at most [ALERTED_MAX]) and whether this call added it — false means
 * some path already posted this timer, so this one must not.
 */
internal fun markInBook(entries: List<String>, serverKey: String, timerId: Long, nowMs: Long): Pair<List<String>, Boolean> {
    val kept = entries.filter { e ->
        val at = e.substringAfterLast('|', "").toLongOrNull()
        at != null && e.count { it == '|' } == 2 && nowMs - at < ALERTED_KEEP_MS
    }
    val prefix = "$serverKey|$timerId|"
    if (kept.any { it.startsWith(prefix) }) return kept to false
    return (kept + "$prefix$nowMs").takeLast(ALERTED_MAX) to true
}

internal fun decodeSeen(raw: String?): Map<String, Long> =
    runCatching { Json.decodeFromString(seenSerializer, raw ?: "{}") }.getOrDefault(emptyMap())

internal fun encodeSeen(seen: Map<String, Long>): String = Json.encodeToString(seenSerializer, seen)

/** What to post from a batch of fires, and the seen id afterwards. */
internal data class FirePlan(val post: List<TimerFire>, val seen: Long)

/**
 * Which of [fires] to post, given the highest id already processed for
 * this server ([seen], null on its first run) and the server's clock.
 *
 *  * First run: nothing older than [FIRST_RUN_WINDOW_MS] alerts — seen
 *    jumps to the newest fire older than that, and only the fires after it
 *    that are younger post. A phone meeting a house for the first time is
 *    not handed its whole afternoon.
 *  * Otherwise: every fire newer than [seen] younger than [maxAgeMs] posts;
 *    older ones only move seen on.
 *
 * A fire whose time is unreadable never posts. [serverNowMs] is the
 * server's clock (a phone's own may be minutes off).
 */
internal fun planFires(fires: List<TimerFire>, seen: Long?, serverNowMs: Long, maxAgeMs: Long): FirePlan {
    fun age(f: TimerFire): Long? = isoMs(f.fired_at)?.let { serverNowMs - it }
    val maxId = fires.maxOfOrNull { it.id } ?: 0L
    if (seen == null) {
        val base = fires.filter { f -> age(f).let { it == null || it > FIRST_RUN_WINDOW_MS } }
            .maxOfOrNull { it.id } ?: 0L
        val post = fires.filter { f -> f.id > base && age(f).let { it != null && it <= FIRST_RUN_WINDOW_MS } }
        return FirePlan(post.sortedBy { it.id }, maxOf(base, maxId))
    }
    val fresh = fires.filter { it.id > seen }
    val post = fresh.filter { f -> age(f).let { it != null && it <= maxAgeMs } }
    return FirePlan(post.sortedBy { it.id }, maxOf(seen, maxId))
}

/** The `alerts` DataStore: one file, app-private, beside the app's own. */
private val Context.alertsDataStore by preferencesDataStore(name = "alerts")

class AlertStore(private val ds: DataStore<Preferences>) {
    constructor(context: Context) : this(context.applicationContext.alertsDataStore)

    private val kAlerted = stringPreferencesKey("alerted")
    private val kSeen = stringPreferencesKey("fires_seen")
    private val kMirror = stringPreferencesKey("mirror")
    private val kHint = booleanPreferencesKey("hint_dismissed")

    /** Serialises every "has this been posted?" question: both paths ask it,
     *  from different threads, about the same timer at the same moment. */
    private val markLock = Mutex()

    /** True when this call is the one that gets to post [timerId]. */
    suspend fun markAlerted(serverKey: String, timerId: Long, nowMs: Long = System.currentTimeMillis()): Boolean =
        markLock.withLock {
            var added = false
            ds.edit { p ->
                val (next, ok) = markInBook(decodeAlerted(p[kAlerted]), serverKey, timerId, nowMs)
                added = ok
                p[kAlerted] = encodeAlerted(next)
            }
            added
        }

    suspend fun wasAlerted(serverKey: String, timerId: Long): Boolean =
        decodeAlerted(ds.data.first()[kAlerted]).any { it.startsWith("$serverKey|$timerId|") }

    suspend fun seen(serverKey: String): Long? = decodeSeen(ds.data.first()[kSeen])[serverKey]

    suspend fun setSeen(serverKey: String, id: Long) {
        ds.edit { p -> p[kSeen] = encodeSeen(decodeSeen(p[kSeen]) + (serverKey to id)) }
    }

    suspend fun mirror(): MirrorBook = decodeMirror(ds.data.first()[kMirror])

    suspend fun setMirror(book: MirrorBook) {
        ds.edit { p -> p[kMirror] = encodeMirror(book) }
    }

    val hintDismissed: Flow<Boolean> = ds.data.map { it[kHint] == true }

    suspend fun dismissHint() {
        ds.edit { it[kHint] = true }
    }
}
