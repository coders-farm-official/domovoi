package com.domovoi.app.alerts

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.domovoi.app.MainActivity
import com.domovoi.app.R
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.Discovery
import com.domovoi.app.net.StateBus
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull

// ---------------------------------------------------------------------------
// A phone room's battery running low (domovoi/room_battery.py, 2026-10-25):
// the core warns at 20 % and again at 10 % while a phone room is off its
// charger, once per discharge; the web serves the open warnings of the rooms
// whose "Notify when the battery is low" is on. Every household phone posts
// one notification per room ("Pako is at 15%. Plug it in."), the 10 % one
// replacing the 20 % one, and takes it back once the room is charging (or
// its switch goes off). A shared screen posts it too: it is the house's
// news, not anybody's own.
//
// The shape of HelpDeliveryEngine: the engine is pure (a fake sink and book
// and a MockWebServer in the JVM tests); RoomBatteryAlerts wires it to the
// live socket and RoomBatteryNotifier to the notification shade.
// ---------------------------------------------------------------------------

/** The web backend's read and push, as the tests pin them. */
object RoomBatteryApi {
    const val PATH = "/api/satellites/battery-warnings"
    const val EVENT = "room_battery.changed"
}

/** One open warning: [roomId] is at [percent], under [threshold]. */
data class RoomBatteryWarning(
    val id: Long,
    val roomId: String,
    val name: String,
    val threshold: Int,
    val percent: Int,
    val text: String,
)

internal fun parseBatteryWarning(el: JsonElement?): RoomBatteryWarning? {
    val o = el as? JsonObject ?: return null
    fun str(k: String) = (o[k] as? JsonPrimitive)?.contentOrNull
    val id = (o["id"] as? JsonPrimitive)?.longOrNull ?: return null
    val room = str("room_id")?.takeIf { it.isNotBlank() } ?: return null
    val percent = (o["percent"] as? JsonPrimitive)?.intOrNull ?: return null
    val name = str("name")?.takeIf { it.isNotBlank() } ?: room
    val threshold = (o["threshold"] as? JsonPrimitive)?.intOrNull ?: percent
    val text = str("text")?.takeIf { it.isNotBlank() } ?: batteryText(name, percent)
    return RoomBatteryWarning(id, room, name, threshold, percent, text)
}

/** The read's payload or the push's (both `{"warnings": [...], ...}`; a
 *  bare list is taken too). Null when it is neither. */
fun parseBatteryWarnings(el: JsonElement?): List<RoomBatteryWarning>? {
    val list = when (el) {
        is JsonArray -> el
        is JsonObject -> el["warnings"] as? JsonArray ?: return null
        else -> return null
    }
    return list.mapNotNull { parseBatteryWarning(it) }
}

/** The core's words, when a row lacks them. */
fun batteryText(name: String, percent: Int): String = "$name is at $percent%. Plug it in."

/** What to post, what to take back, and the book to keep (room -> the
 *  warning id posted for it). */
data class BatteryPlan(
    val post: List<RoomBatteryWarning>,
    val withdraw: List<String>,
    val book: Map<String, Long>,
)

/**
 * The rule, pure. Per room the newest open warning is the one to show: post
 * it unless it is the one already posted; take back a room posted before
 * that has no open warning now (charging, or its switch went off). With
 * notifications off ([canPost] false) nothing posts and the book keeps only
 * what is still open, so a warning still open posts once they are back on.
 */
fun planBatteryNotifications(
    open: List<RoomBatteryWarning>,
    posted: Map<String, Long>,
    canPost: Boolean,
): BatteryPlan {
    val newest = open.groupBy { it.roomId }.mapValues { (_, ws) -> ws.maxBy { it.id } }
    val withdraw = posted.keys.filter { it !in newest }.sorted()
    val kept = posted.filterKeys { it in newest }
    if (!canPost) return BatteryPlan(emptyList(), withdraw, kept)
    val post = newest.values.filter { posted[it.roomId] != it.id }.sortedBy { it.id }
    return BatteryPlan(post, withdraw, kept + post.associate { it.roomId to it.id })
}

/** Where the notifications go. The Android one is [RoomBatteryNotifier]. */
interface RoomBatterySink {
    fun canPost(): Boolean
    fun post(serverKey: String, warning: RoomBatteryWarning)
    fun withdraw(serverKey: String, roomId: String)
}

/** room -> the warning id posted, per server. */
interface RoomBatteryBook {
    suspend fun posted(serverKey: String): Map<String, Long>
    suspend fun setPosted(serverKey: String, book: Map<String, Long>)
}

private val Context.roomBatteryDataStore by preferencesDataStore(name = "room_battery")

/** [RoomBatteryBook] in its own app-private DataStore (`room=id,room=id`). */
class RoomBatteryStore(private val ds: DataStore<Preferences>) : RoomBatteryBook {
    constructor(context: Context) : this(context.applicationContext.roomBatteryDataStore)

    private fun key(serverKey: String) = stringPreferencesKey("posted_$serverKey")

    override suspend fun posted(serverKey: String): Map<String, Long> =
        decodeBook(ds.data.first()[key(serverKey)])

    override suspend fun setPosted(serverKey: String, book: Map<String, Long>) {
        ds.edit { it[key(serverKey)] = encodeBook(book) }
    }
}

internal fun encodeBook(book: Map<String, Long>): String =
    book.entries.sortedBy { it.key }.joinToString(",") { "${it.key}=${it.value}" }

internal fun decodeBook(raw: String?): Map<String, Long> =
    raw.orEmpty().split(',').mapNotNull { part ->
        val i = part.lastIndexOf('=')
        if (i <= 0) return@mapNotNull null
        val id = part.substring(i + 1).toLongOrNull() ?: return@mapNotNull null
        part.substring(0, i) to id
    }.toMap()

class RoomBatteryEngine(
    private val api: ApiClient,
    private val book: RoomBatteryBook,
    private val sink: RoomBatterySink,
    private val serverUrl: () -> String,
    private val log: (String) -> Unit = {},
) {
    /** One payload at a time: a push and a read landing together post once. */
    private val lock = Mutex()

    private fun activeKey(): String? = serverUrl().takeIf { it.isNotBlank() }?.let { serverKey(it) }

    /** A `room_battery.changed` push: the open warnings, whole. */
    suspend fun onEvent(payload: JsonElement?) {
        val rows = parseBatteryWarnings(payload) ?: return
        val key = activeKey() ?: return
        apply(key, rows)
    }

    /** The catch-up read (start, reconnect, coming back to the app, the
     *  background tick). Never throws; an older server (404) is "none". */
    suspend fun refresh() {
        val key = activeKey() ?: return
        try {
            val rows = parseBatteryWarnings(api.getUntracked(RoomBatteryApi.PATH)) ?: return
            apply(key, rows)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            log("battery warnings read failed: ${e.message}")
        }
    }

    private suspend fun apply(key: String, rows: List<RoomBatteryWarning>) {
        lock.withLock {
            if (activeKey() != key) return
            val posted = book.posted(key)
            val plan = planBatteryNotifications(rows, posted, sink.canPost())
            // Remembered first: a crash in between loses a post rather than
            // doubling one.
            if (plan.book != posted) book.setPosted(key, plan.book)
            plan.withdraw.forEach { sink.withdraw(key, it) }
            plan.post.forEach { sink.post(key, it) }
        }
    }
}

/** The notification tag a room's battery warning posts under. */
fun batteryTag(serverKey: String, roomId: String): String = "room_battery:$serverKey:$roomId"

/**
 * The notification side: a "Rooms" channel of its own. The lock screen's
 * public version says only "A room's battery is low".
 */
class RoomBatteryNotifier(context: Context) : RoomBatterySink {
    private val ctx = context.applicationContext

    companion object {
        const val CHANNEL_ID = "room_battery"
        const val CHANNEL_NAME = "Room batteries"
        const val CHANNEL_DESCRIPTION = "A phone used as a room is running low and needs its charger"
        const val ACTION_OPEN = "com.domovoi.app.action.OPEN_ROOM_BATTERY"
        private const val NOTIFICATION_ID = 1
        private const val TAG = "RoomBatteryNotifier"

        fun createChannel(context: Context) {
            val nm = context.getSystemService(NotificationManager::class.java) ?: return
            val channel = NotificationChannel(CHANNEL_ID, CHANNEL_NAME, NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = CHANNEL_DESCRIPTION
                lockscreenVisibility = Notification.VISIBILITY_PRIVATE
            }
            nm.createNotificationChannel(channel)
        }

        fun canPost(context: Context): Boolean {
            val compat = NotificationManagerCompat.from(context)
            if (!compat.areNotificationsEnabled()) return false
            val channel = context.getSystemService(NotificationManager::class.java)
                ?.getNotificationChannel(CHANNEL_ID)
            return channel == null || channel.importance != NotificationManager.IMPORTANCE_NONE
        }
    }

    override fun canPost(): Boolean = canPost(ctx)

    private fun openIntent(roomId: String): PendingIntent = PendingIntent.getActivity(
        ctx,
        roomId.hashCode(),
        Intent(ctx, MainActivity::class.java)
            .setAction(ACTION_OPEN)
            .putExtra(EXTRA_ROUTE, "home")
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    @SuppressLint("MissingPermission") // canPost() is checked first; a revoke in between is caught
    override fun post(serverKey: String, warning: RoomBatteryWarning) {
        if (!canPost()) return
        fun base() = NotificationCompat.Builder(ctx, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_timer)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
        val public = base().setContentTitle("A room's battery is low").build()
        val notification = base()
            .setContentTitle("${warning.name}'s battery is low")
            .setContentText(warning.text)
            .setAutoCancel(true)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(public)
            .setContentIntent(openIntent(warning.roomId))
            .build()
        try {
            NotificationManagerCompat.from(ctx).notify(batteryTag(serverKey, warning.roomId), NOTIFICATION_ID, notification)
        } catch (e: SecurityException) {
            Log.i(TAG, "battery warning not posted: ${e.message}")
        }
    }

    override fun withdraw(serverKey: String, roomId: String) {
        try {
            NotificationManagerCompat.from(ctx).cancel(batteryTag(serverKey, roomId), NOTIFICATION_ID)
        } catch (e: RuntimeException) {
            Log.i(TAG, "battery warning not withdrawn: ${e.message}")
        }
    }
}

/**
 * Process-wide, like [HelpAlerts]: created in AppContainer, started by
 * DomovoiApplication beside it (never on a phone in room mode). Live path:
 * `room_battery.changed` on /ws/state. Catch-up reads at start, on every
 * reconnect, on a server switch, on coming back to the app, and on the
 * background sync's tick (TimerSyncReceiver), so a warning that opens while
 * the app is off screen still notifies.
 */
class RoomBatteryAlerts(
    context: Context,
    api: ApiClient,
    private val bus: StateBus,
    private val prefs: Prefs,
) {
    private val ctx = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    val engine = RoomBatteryEngine(
        api = api,
        book = RoomBatteryStore(ctx),
        sink = RoomBatteryNotifier(ctx),
        serverUrl = { prefs.serverUrl.value },
        log = { Log.d(TAG, it) },
    )

    private val requests = Channel<Unit>(Channel.CONFLATED)
    private var started = false

    @Synchronized
    fun start() {
        if (started) return
        started = true
        scope.launch {
            while (true) {
                requests.receive()
                safely("battery warnings read") { engine.refresh() }
            }
        }
        scope.launch {
            bus.events.collect { ev ->
                if (ev.type == RoomBatteryApi.EVENT) safely("battery warnings push") { engine.onEvent(ev.payload) }
            }
        }
        scope.launch {
            var was = bus.connected.value
            bus.connected.collect { now ->
                if (now && !was) refresh()
                was = now
            }
        }
        scope.launch {
            var last = prefs.serverUrl.value
            prefs.serverUrl.collect { url ->
                if (url != last) {
                    last = url
                    refresh()
                }
            }
        }
        refresh()
    }

    fun refresh() {
        requests.trySend(Unit)
    }

    /** MainActivity.onResume. */
    fun onAppResumed() = refresh()

    /** The background sync's tick: one read when on the home network. Never throws. */
    suspend fun onBackgroundTick() {
        if (!started) return
        if (!runCatching { Discovery.onLan(ctx) }.getOrDefault(false)) return
        safely("battery warnings tick") { engine.refresh() }
    }

    private suspend fun safely(what: String, block: suspend () -> Unit) {
        try {
            block()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w(TAG, "$what failed: ${e.message}")
        }
    }

    private companion object {
        const val TAG = "RoomBatteryAlerts"
    }
}
