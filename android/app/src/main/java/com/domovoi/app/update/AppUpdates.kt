package com.domovoi.app.update

import android.content.Context
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.util.concurrent.ConcurrentHashMap

/**
 * Where [AppUpdate] gets its inputs: the server's answer (read at most every
 * [FRESH_MS] per server, so Home asks once, not on every visit), and the
 * dismissal, kept on the phone ([UpdateDismissals]).
 */
object AppUpdates {
    /** The dashboard's open read (web/backend/api/devices.py). */
    const val WEB_PATH = "/api/devices/android-app"

    /** The core's public read (domovoi/main.py), for a room-mode phone. */
    const val CORE_PATH = "/v1/android-app"

    const val FRESH_MS = 30 * 60_000L
    const val RETRY_MS = 5 * 60_000L

    private val answers = ConcurrentHashMap<String, Pair<Long, AppOffer?>>()

    /** What the server at [server] offers, from the last [maxAgeMs], else
     *  asked now; null when it didn't answer (an older server, the core
     *  down, offline). Never throws but for cancellation. */
    suspend fun offer(
        api: ApiClient,
        server: String,
        maxAgeMs: Long = FRESH_MS,
        now: () -> Long = System::currentTimeMillis,
    ): AppOffer? {
        if (server.isBlank()) return null
        answers[server]?.let { (at, offer) ->
            // No answer is asked again sooner: the server may just have been restarting.
            if (now() - at < if (offer == null) minOf(maxAgeMs, RETRY_MS) else maxAgeMs) return offer
        }
        val offer = try {
            // Untracked: an open read says nothing about this phone's pairing.
            api.getUntracked(WEB_PATH).decode<AppOffer>()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            null
        }
        answers[server] = now() to offer
        return offer
    }

    /** The core's `/v1/android-app` answer [body] as an offer, its join page
     *  being [coreUrl]'s (the core the room proved). Null if unreadable. */
    fun fromCore(coreUrl: String, body: String): AppOffer? = runCatching {
        DomovoiJson.decodeFromString(AppOffer.serializer(), body)
            .copy(joinUrl = coreUrl.trimEnd('/') + AppUpdate.JOIN_PATH)
    }.getOrNull()

    /** Forget every answer (tests). */
    internal fun clear() = answers.clear()

    @Volatile
    private var dismissals: UpdateDismissals? = null

    /** The phone's one dismissal record, shared by Home, About and the room
     *  screen. */
    fun dismissals(context: Context): UpdateDismissals = dismissals ?: synchronized(this) {
        dismissals ?: UpdateDismissals(PrefsDismissalStore(context)).also { dismissals = it }
    }
}

/** Where a dismissal is kept. */
interface DismissalStore {
    fun load(): Dismissal?
    fun save(dismissal: Dismissal)
}

/** The dismissed notice, read once and kept current. "Dismiss" hides a
 *  notice until a newer release ([AppUpdate.shows]). */
class UpdateDismissals(private val store: DismissalStore) {
    private val _dismissed = MutableStateFlow(runCatching { store.load() }.getOrNull())
    val dismissed: StateFlow<Dismissal?> = _dismissed

    fun shows(state: UpdateState): Boolean = AppUpdate.shows(state, _dismissed.value)

    fun dismiss(state: UpdateState) {
        val d = AppUpdate.dismissalFor(state) ?: return
        _dismissed.value = d
        runCatching { store.save(d) }
    }
}

/** The dismissal in its own small preferences file (`app_update`). */
class PrefsDismissalStore(context: Context) : DismissalStore {
    // Opened on first use: a failure there is UpdateDismissals' to absorb.
    private val prefs by lazy { context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE) }

    override fun load(): Dismissal? {
        val code = prefs.getLong(K_CODE, 0L)
        return if (code > 0) Dismissal(code, prefs.getBoolean(K_READY, true)) else null
    }

    override fun save(dismissal: Dismissal) {
        prefs.edit().putLong(K_CODE, dismissal.versionCode).putBoolean(K_READY, dismissal.ready).apply()
    }

    private companion object {
        const val FILE = "app_update"
        const val K_CODE = "dismissed_version_code"
        const val K_READY = "dismissed_ready"
    }
}
