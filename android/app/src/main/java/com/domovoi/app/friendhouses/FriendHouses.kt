package com.domovoi.app.friendhouses

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
import com.domovoi.app.MainActivity
import com.domovoi.app.R
import com.domovoi.app.alerts.EXTRA_ROUTE
import com.domovoi.app.alerts.serverKey
import com.domovoi.app.data.Prefs
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.Discovery
import com.domovoi.app.net.HouseholdLock
import com.domovoi.app.net.StateBus
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json

/** The route a friend houses notification's tap asks MainActivity for. */
const val FRIEND_HOUSES_ROUTE = "friend-houses"

/** The gate as the shell reads it: the last verdict, for the server it
 *  came from (normalised). */
data class FhGateState(val server: String = "", val availability: FhAvailability = FhAvailability.Hidden) {
    /** The status to show for [activeServer], or null: hidden there (or the
     *  verdict is another server's). */
    fun shownFor(activeServer: String): FhStatus? =
        if (server.isNotEmpty() && server == ServerCredentials.normalize(activeServer)) {
            (availability as? FhAvailability.Shown)?.status
        } else {
            null
        }
}

/**
 * Friend Houses, process-wide like the alerts (AppContainer; started by
 * DomovoiApplication outside room mode): the availability gate the shell
 * reads ([state]: whether the entry is drawn at all, and its unread
 * badge), and the notifications the background sync's tick posts
 * ([onBackgroundTick], [FriendHouseAlerts]).
 *
 * The gate re-reads `GET /api/friend-houses` at start, on a server switch,
 * when the household token changes, when the live connection comes back,
 * on coming back to the app, every minute while it is on screen (the
 * shell), whenever the screen itself reads, and at every background tick.
 * Nothing pushes Friend Houses changes, so a read is the only way to know.
 */
class FriendHouses(
    context: Context,
    apiClient: ApiClient,
    private val bus: StateBus,
    private val prefs: Prefs,
) {
    private val ctx = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    val api = FriendHousesApi(apiClient)
    private val notifier = FriendHouseNotifier(ctx)

    private val _state = MutableStateFlow(FhGateState())
    val state: StateFlow<FhGateState> = _state

    private val alerts = FriendHouseAlerts(
        api = api,
        store = PrefsFhSeenStore(ctx),
        sink = notifier,
        serverUrl = { prefs.serverUrl.value },
        serverKey = ::serverKey,
        onStatus = ::note,
        roomMode = { HouseholdLock.roomModeActive },
        log = { Log.i(TAG, it) },
    )

    /** Record a status answer for [server] (the gate's [FhGate.settle]). */
    fun note(server: String, availability: FhAvailability) {
        val key = ServerCredentials.normalize(server)
        if (key.isEmpty()) return
        synchronized(this) {
            val previous = _state.value.takeIf { it.server == key }?.availability
            _state.value = FhGateState(key, FhGate.settle(previous, availability))
        }
    }

    private val requests = Channel<Unit>(Channel.CONFLATED)
    private var started = false

    /** Start (or, after room mode, wake) the gate: the watchers start once,
     *  and the status is read again either way. */
    @Synchronized
    fun start() {
        if (started) {
            refresh()
            return
        }
        started = true
        scope.launch {
            while (true) {
                requests.receive()
                try {
                    read()
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    Log.w(TAG, "friend houses status read failed: ${e.javaClass.simpleName}")
                }
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
            prefs.serverUrl.collect { url -> if (url != last) { last = url; refresh() } }
        }
        scope.launch {
            var last = prefs.deviceToken.value
            prefs.deviceToken.collect { token -> if (token != last) { last = token; refresh() } }
        }
        refresh()
    }

    private suspend fun read() {
        val server = prefs.serverUrl.value
        if (server.isBlank()) return
        // Not paired (the household routes would refuse it) or a room (no
        // household request at all): nothing to show, nothing asked.
        val availability =
            if (prefs.isPaired() && !HouseholdLock.roomModeActive) api.status() else FhAvailability.Hidden
        note(server, availability)
    }

    /** Re-read the status soon. */
    fun refresh() {
        requests.trySend(Unit)
    }

    fun onAppResumed() = refresh()

    /**
     * Room mode begins (RoomSetupActivity.begin): the phone is a room
     * anyone nearby may pick up. Every Friend Houses notification already in
     * the shade goes (at the "read" level they carry message text), the
     * gate shows nothing, and nothing posts while it runs (the tick stands
     * down, [FriendHouseAlerts] and [FriendHouseNotifier.post] check too).
     */
    fun pauseForRoomMode() {
        notifier.withdrawAll()
        note(prefs.serverUrl.value, FhAvailability.Hidden)
    }

    /** A conversation was opened here (and marked read): its notification goes. */
    fun conversationOpened(house: String) {
        val server = prefs.serverUrl.value
        if (server.isNotBlank()) notifier.withdraw(serverKey(server), house)
    }

    /** The background sync's tick (TimerSyncReceiver). Never throws. */
    suspend fun onBackgroundTick(): FhTick =
        alerts.backgroundTick(onLan = runCatching { Discovery.onLan(ctx) }.getOrDefault(false))

    private companion object {
        const val TAG = "FriendHouses"
    }
}

/**
 * The notification side: a "Friend houses" channel of its own (so it can
 * be silenced apart from the timers, help and the container guard), one
 * notification per conversation (tag `friend_house:<serverKey>:<house>`),
 * replaced as more arrive. Default importance, as Help and Room batteries:
 * a message is not an alarm.
 *
 * Privacy as for the other alerts: VISIBILITY_PRIVATE, with a public
 * version that says only "A message from a friend house", which Android
 * shows on a lock screen that hides sensitive content.
 */
class FriendHouseNotifier(context: Context) : FhSink {
    private val ctx = context.applicationContext

    companion object {
        const val CHANNEL_ID = "friend_houses"
        const val CHANNEL_NAME = "Friend houses"
        const val CHANNEL_DESCRIPTION = "Messages from friend houses: $FH_DESCRIPTION"
        const val GROUP = "friend_houses"
        const val PUBLIC_TITLE = "A message from a friend house"
        const val ACTION_OPEN = "com.domovoi.app.action.OPEN_FRIEND_HOUSES"
        private const val TAG = "FriendHouseNotifier"

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

        const val TAG_PREFIX = "friend_house:"

        fun tag(serverKey: String, house: String) = "$TAG_PREFIX$serverKey:$house"

        /** Of the tags in the shade, those that are ours (any server). */
        fun ours(tags: List<String?>): List<String> = tags.filterNotNull().filter { it.startsWith(TAG_PREFIX) }.distinct()
    }

    override fun canPost(): Boolean = canPost(ctx)

    private fun openIntent(house: String): PendingIntent = PendingIntent.getActivity(
        ctx,
        house.hashCode(),
        Intent(ctx, MainActivity::class.java)
            .setAction(ACTION_OPEN)
            .putExtra(EXTRA_ROUTE, FRIEND_HOUSES_ROUTE)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun base(): NotificationCompat.Builder =
        NotificationCompat.Builder(ctx, CHANNEL_ID)
            // The app's one status-bar glyph; no new artwork.
            .setSmallIcon(R.drawable.ic_stat_timer)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setGroup(GROUP)

    @SuppressLint("MissingPermission") // canPost() is checked first; a revoke in between is caught
    override fun post(serverKey: String, note: FhNote) {
        // A room shows no household message, whoever asks.
        if (HouseholdLock.roomModeActive || !canPost()) return
        val public = base().setContentTitle(PUBLIC_TITLE).build()
        // Plain strings only: no Html, no links, no styled spans.
        val builder = base()
            .setContentTitle(note.title)
            .setAutoCancel(true)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(public)
            .setContentIntent(openIntent(note.house))
        if (note.count > 1) builder.setNumber(note.count)
        note.text?.let {
            builder.setContentText(it)
            builder.setStyle(NotificationCompat.BigTextStyle().bigText(it))
        }
        try {
            NotificationManagerCompat.from(ctx).notify(tag(serverKey, note.house), 0, builder.build())
        } catch (e: SecurityException) {
            Log.i(TAG, "friend house message not posted (no permission)")
        }
    }

    /** Take back every Friend Houses notification in the shade, for any server. */
    fun withdrawAll() {
        try {
            val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
            val shown = nm.activeNotifications.filter { it.tag != null }
            val ours = ours(shown.map { it.tag }).toSet()
            shown.filter { it.tag in ours }.forEach { nm.cancel(it.tag, it.id) }
        } catch (e: RuntimeException) {
            Log.i(TAG, "friend house notifications not withdrawn: ${e.javaClass.simpleName}")
        }
    }

    override fun withdraw(serverKey: String, house: String) {
        try {
            NotificationManagerCompat.from(ctx).cancel(tag(serverKey, house), 0)
        } catch (e: RuntimeException) {
            Log.i(TAG, "friend house notification not withdrawn: ${e.javaClass.simpleName}")
        }
    }
}

@Serializable
private data class SeenRecord(val unread: Int = 0, val lastIds: Map<String, Long> = emptyMap())

/** [FhSeen] per server, in the app's private preferences (the app opts
 *  out of backups). House keys and numbers only: no text. */
class PrefsFhSeenStore(context: Context) : FhSeenStore {
    private val prefs = context.applicationContext.getSharedPreferences("friend_houses", Context.MODE_PRIVATE)
    private val codec = MapSerializer(String.serializer(), SeenRecord.serializer())
    private val json = Json { ignoreUnknownKeys = true }

    private fun all(): Map<String, SeenRecord> =
        runCatching { json.decodeFromString(codec, prefs.getString(KEY, null) ?: "{}") }.getOrDefault(emptyMap())

    override suspend fun read(serverKey: String): FhSeen? =
        all()[serverKey]?.let { FhSeen(it.unread, it.lastIds) }

    override suspend fun write(serverKey: String, seen: FhSeen) {
        val ids = seen.lastIds.filterKeys { FhPaths.isHouseKey(it) }
        val next = all() + (serverKey to SeenRecord(seen.unread, ids))
        prefs.edit().putString(KEY, json.encodeToString(codec, next)).apply()
    }

    private companion object {
        const val KEY = "seen"
    }
}
