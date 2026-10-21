package com.domovoi.app.alerts

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.domovoi.app.net.ApiClient
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull

// ---------------------------------------------------------------------------
// Help steps sent by voice (assistant plan part 9): someone asks a room
// "how do I…", Domovoi answers in a sentence and offers "Want me to send the
// steps to your phone?". A yes opens a home-server chat ("Help: …") and
// lists a delivery for the asker. This phone posts a notification for a
// delivery only when it is for the person this phone belongs to on that
// server (Settings → Connection, "This phone belongs to"), once, and never
// on a shared screen. Tapping it opens Chat on that thread.
//
// The shape of IncidentEngine: the engine is pure (fake sink, fake book and
// a MockWebServer in the JVM tests); HelpAlerts wires it to the live socket
// and HelpNotifier to the notification shade.
// ---------------------------------------------------------------------------

/** The web backend's read and push, as the tests pin them. */
object HelpDeliveriesApi {
    const val PATH = "/api/help/deliveries"
    const val EVENT = "help_deliveries.changed"

    /** The catch-up read for [personId]; [sinceId] null reads the last
     *  hour, newest first. */
    fun path(personId: Long, sinceId: Long?): String =
        "$PATH?person_id=$personId" + (sinceId?.let { "&since_id=$it" } ?: "")
}

/** One delivery: the steps for [personId], in server chat thread [threadId]. */
data class HelpDelivery(
    val id: Long,
    val personId: Long,
    val threadId: Long,
    val title: String,
    val createdAt: String?,
)

/** A row as the read and the push give it; null when it isn't one. */
internal fun parseDelivery(el: JsonElement?): HelpDelivery? {
    val o = el as? JsonObject ?: return null
    fun long(k: String): Long? = (o[k] as? JsonPrimitive)?.longOrNull
    val id = long("id") ?: return null
    val person = long("person_id") ?: return null
    val thread = long("thread_id") ?: return null
    val title = (o["title"] as? JsonPrimitive)?.contentOrNull.orEmpty()
    return HelpDelivery(id, person, thread, title, (o["created_at"] as? JsonPrimitive)?.contentOrNull)
}

/** The push's payload (a list) or the read's (`{"deliveries": [...]}`);
 *  null when it is neither. */
fun parseDeliveries(el: JsonElement?): List<HelpDelivery>? {
    val list = when (el) {
        is JsonArray -> el
        is JsonObject -> el["deliveries"] as? JsonArray ?: return null
        else -> return null
    }
    return list.mapNotNull { parseDelivery(it) }
}

/** The notification's title: the row's own ("Help: add a room"), with the
 *  "Help: " the plan names added when the row lacks it. */
fun helpTitle(row: HelpDelivery): String {
    val t = row.title.trim()
    return when {
        t.isEmpty() -> "Help from Domovoi"
        t.startsWith("Help:", ignoreCase = true) -> t
        else -> "Help: $t"
    }
}

/** What to post and what to remember, from [rows] (any order). */
data class HelpPlan(val post: List<HelpDelivery>, val seen: Long?)

/**
 * The rule, pure: post the rows for [owner] newer than [seen], oldest
 * first. [seen] null is this server's first run: nothing posts (an hour of
 * old steps must not flood a phone just set up), only the newest id is
 * remembered. Every row moves the mark, whoever it is for, so a later
 * change of owner never posts what was already there. [shared] (the
 * kitchen tablet) and no [owner] post nothing either.
 */
fun planHelp(rows: List<HelpDelivery>, owner: Long?, seen: Long?, shared: Boolean): HelpPlan {
    val newest = rows.maxOfOrNull { it.id }
    val mark = listOfNotNull(seen, newest).maxOrNull()
    if (seen == null || owner == null || shared) return HelpPlan(emptyList(), mark)
    val post = rows.filter { it.personId == owner && it.id > seen }.distinctBy { it.id }.sortedBy { it.id }
    return HelpPlan(post, mark)
}

/** Where help notifications go. The Android one is [HelpNotifier]. */
interface HelpSink {
    /** Notifications are on for this app and the help channel. */
    fun canPost(): Boolean

    /** This install is a shared screen for the active server. */
    fun shared(): Boolean

    fun post(serverKey: String, row: HelpDelivery)
}

/** The last delivery id seen, per server. */
interface HelpSeenBook {
    suspend fun seen(serverKey: String): Long?
    suspend fun setSeen(serverKey: String, id: Long)
}

private val Context.helpDataStore by preferencesDataStore(name = "help_deliveries")

/** [HelpSeenBook] in its own app-private DataStore. */
class HelpSeenStore(private val ds: DataStore<Preferences>) : HelpSeenBook {
    constructor(context: Context) : this(context.applicationContext.helpDataStore)

    private fun key(serverKey: String) = longPreferencesKey("seen_$serverKey")

    override suspend fun seen(serverKey: String): Long? = ds.data.first()[key(serverKey)]

    override suspend fun setSeen(serverKey: String, id: Long) {
        ds.edit { it[key(serverKey)] = id }
    }
}

class HelpDeliveryEngine(
    private val api: ApiClient,
    private val book: HelpSeenBook,
    private val sink: HelpSink,
    private val serverUrl: () -> String,
    /** This phone's owner on the active server (a `people.id` as text). */
    private val owner: () -> String?,
    private val log: (String) -> Unit = {},
) {
    /** One payload at a time: a push and a read landing together must not
     *  both find a row unposted and post it twice. */
    private val lock = Mutex()

    private fun activeKey(): String? = serverUrl().takeIf { it.isNotBlank() }?.let { serverKey(it) }

    private fun ownerId(): Long? = owner()?.trim()?.toLongOrNull()

    /** A `help_deliveries.changed` push: the last hour's rows, newest first. */
    suspend fun onEvent(payload: JsonElement?) {
        val rows = parseDeliveries(payload) ?: return
        val key = activeKey() ?: return
        apply(key, rows)
    }

    /**
     * The catch-up read, for what was sent while the socket was down: this
     * phone's owner's rows since the last one seen (the last hour on a
     * first run, which only sets the mark). Without an owner there is
     * nothing to ask for. Never throws.
     */
    suspend fun refresh() {
        val key = activeKey() ?: return
        val person = ownerId() ?: return
        try {
            val seen = book.seen(key)
            val rows = parseDeliveries(api.getUntracked(HelpDeliveriesApi.path(person, seen))) ?: return
            apply(key, rows)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            log("help deliveries read failed: ${e.message}")
        }
    }

    private suspend fun apply(key: String, rows: List<HelpDelivery>) {
        lock.withLock {
            if (activeKey() != key) return
            val seen = book.seen(key)
            // Notifications off: nothing posts and the mark stays, so what is
            // still in the last hour posts once they come back on — except on
            // the first run, which only ever sets the mark.
            if (seen != null && !sink.canPost()) return
            val plan = planHelp(rows, ownerId(), seen, sink.shared())
            // Remembered first: a crash in between loses a post rather than
            // doubling one.
            plan.seen?.let { if (it != seen) book.setSeen(key, it) }
            plan.post.forEach { sink.post(key, it) }
        }
    }
}
