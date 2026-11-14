package com.domovoi.app.calendarshare

import android.content.Context
import android.provider.CalendarContract
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequest
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.domovoi.app.AppContainer
import com.domovoi.app.DomovoiApplication
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.HouseholdLock
import com.domovoi.app.net.encodePathSegment
import com.domovoi.app.net.suggestedDeviceName
import kotlinx.serialization.json.JsonObject
import java.io.File
import java.util.concurrent.TimeUnit

/*
 * When the phone's calendars go to the house. Three WorkManager jobs, no
 * foreground service, nothing running in between:
 *
 *  * "calendar-share-now": one send (or the delete). Queued a few seconds
 *    after sharing is turned on or a calendar is ticked (several ticks in a
 *    row make one upload), at once when it is turned off, and by the
 *    change watch below. Needs a network; a failure tries again after a
 *    minute, then two, and so on (linear back-off), and a 429 waits at
 *    least its Retry-After. Each is APPENDED to the one before, never
 *    replaces it (a cancelled send could reach the house after the
 *    delete); [ShareSync] also runs one pass at a time and finishes a call
 *    it started.
 *  * "calendar-share-change": waits for any change in the phone's calendar
 *    provider (a content-URI trigger on CalendarContract.CONTENT_URI, the
 *    platform's JobScheduler, so it costs nothing while nothing changes),
 *    then 90 seconds of quiet (at most 5 minutes) so a burst of edits or an
 *    account sync is one send. It queues the send and arms itself again.
 *  * "calendar-share-periodic": every 6 hours with a network, to keep the
 *    rolling window moving and to catch anything the watch missed (a
 *    reboot, a provider that didn't say). A snapshot the house already has
 *    is not sent again, except once a day ([ShareRules.unchanged]).
 *
 * All three run only while the ACTIVE, paired server has sharing on or a
 * copy to delete ([ShareRules.needsJob]); [schedule] cancels them
 * otherwise, and [serverChanged] brings them back when the app moves to a
 * server that needs them.
 *
 * One server at a time: the ACTIVE one, captured when the job starts (the
 * call is sent only to it, `onlyTo`, [ShareCalls]). A server switched away
 * from keeps its entry, and its send or pending delete goes when it is the
 * active one again. Forgetting a server or unpairing from it deletes the
 * house's copy first ([withdraw]).
 */
object CalendarShare {
    const val NOW = "calendar-share-now"
    const val CHANGE = "calendar-share-change"
    const val PERIODIC = "calendar-share-periodic"

    /** A tick, then another: one send. */
    const val SETTLE_MS = 8_000L

    /** What the forget flow says when the house still has this phone's
     *  calendars. */
    const val LEFT_ON_HOUSE = "this phone's calendars stay on the house until removed there"

    @Volatile
    private var storeInstance: ShareStore? = null

    fun store(context: Context): ShareStore =
        storeInstance ?: synchronized(this) {
            storeInstance ?: ShareStore(File(context.applicationContext.filesDir, "calendar-share.json"))
                .also { storeInstance = it }
        }

    private val network: Constraints
        get() = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    private fun changeRequest(): OneTimeWorkRequest =
        OneTimeWorkRequestBuilder<CalendarChangeWorker>()
            .setConstraints(
                Constraints.Builder()
                    .addContentUriTrigger(CalendarContract.CONTENT_URI, true)
                    .setTriggerContentUpdateDelay(90, TimeUnit.SECONDS)
                    .setTriggerContentMaxDelay(5, TimeUnit.MINUTES)
                    .build(),
            )
            .build()

    private fun container(context: Context): AppContainer? =
        (context.applicationContext as? DomovoiApplication)?.container

    /** The active server, normalised, and whether this phone holds its token. */
    private fun active(context: Context): Pair<String, Boolean> {
        val app = container(context) ?: return "" to false
        val server = ServerCredentials.normalize(app.prefs.serverUrl.value)
        return server to (server.isNotBlank() && app.prefs.tokenForServer(server) != null)
    }

    private fun needsJob(context: Context): Boolean {
        val (server, paired) = active(context)
        return ShareRules.needsJob(store(context).all(), server, paired)
    }

    /** Start or stop the background jobs to match the active server's entry. */
    fun schedule(context: Context) {
        val wm = WorkManager.getInstance(context)
        if (needsJob(context)) {
            wm.enqueueUniquePeriodicWork(
                PERIODIC, ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<CalendarShareWorker>(6, TimeUnit.HOURS)
                    .setConstraints(network)
                    .setBackoffCriteria(BackoffPolicy.LINEAR, 60, TimeUnit.SECONDS)
                    .build(),
            )
            wm.enqueueUniqueWork(CHANGE, ExistingWorkPolicy.KEEP, changeRequest())
        } else {
            wm.cancelUniqueWork(PERIODIC)
            wm.cancelUniqueWork(CHANGE)
        }
    }

    /** One send soon: after [delayMs], or later if the server asked this
     *  phone to wait. APPENDED to a send already queued or under way, never
     *  replacing it: a send cancelled half-way could reach the house after
     *  the delete that replaced it. Several ticks in a row still make one
     *  upload: the first pass sends what is ticked by then, and the ones
     *  after it find the house already has it ([ShareRules.unchanged]). */
    fun sendSoon(context: Context, delayMs: Long = SETTLE_MS) {
        val (server, _) = active(context)
        val wait = ShareRules.waitMs(store(context).get(server), System.currentTimeMillis())
        WorkManager.getInstance(context).enqueueUniqueWork(
            NOW, ExistingWorkPolicy.APPEND_OR_REPLACE,
            OneTimeWorkRequestBuilder<CalendarShareWorker>()
                .setConstraints(network)
                .setInitialDelay(maxOf(delayMs, wait), TimeUnit.MILLISECONDS)
                .setBackoffCriteria(BackoffPolicy.LINEAR, 60, TimeUnit.SECONDS)
                .build(),
        )
        schedule(context)
    }

    /** The app moved to another server, or its token came or went: the
     *  jobs follow the active server, and a send or delete it was owed
     *  goes now. */
    fun serverChanged(context: Context) {
        val (server, paired) = active(context)
        schedule(context)
        if (paired && ShareRules.action(store(context).get(server)) != ShareAction.Nothing) {
            sendSoon(context, delayMs = 0)
        }
    }

    /** Arm the change watch again (from inside its own run: appended, so it
     *  waits for the current run to finish rather than cancelling it). */
    internal fun rearmChange(context: Context) {
        if (!needsJob(context)) return
        WorkManager.getInstance(context).enqueueUniqueWork(CHANGE, ExistingWorkPolicy.APPEND_OR_REPLACE, changeRequest())
    }

    /** The pass for [server] with this phone's calendars and the app's
     *  client; every call goes only to [server]. */
    internal fun sync(context: Context, app: AppContainer, server: String): ShareSync {
        val reader = PhoneCalendarReader(context.applicationContext)
        val calls = ShareCalls(app.prefs.deviceId, ApiShareHttp(app.api))
        return ShareSync(
            store = store(context),
            calendars = reader::calendars,
            instances = reader::instances,
            put = calls::put,
            delete = calls::delete,
            phoneName = { suggestedDeviceName() },
        )
    }

    /**
     * Before the phone forgets the ACTIVE server or drops its token: delete
     * the house's copy while the token still works, then forget the entry.
     * False when the house still has this phone's calendars ([LEFT_ON_HOUSE]).
     * Call it BEFORE the token goes.
     */
    suspend fun withdraw(context: Context, app: AppContainer): Boolean {
        val server = ServerCredentials.normalize(app.prefs.serverUrl.value)
        if (server.isBlank()) return true
        val ok = if (HouseholdLock.roomModeActive || app.prefs.tokenForServer(server) == null) {
            // No token to send it with: whatever the house has stays.
            val had = ShareRules.mayHoldCopy(store(context).get(server))
            store(context).forget(server)
            !had
        } else {
            sync(context, app, server).withdraw(server)
        }
        schedule(context)
        return ok
    }

    /** A server that is NOT the active one is being forgotten: nothing can
     *  be sent to it (the app's client talks to the active server only), so
     *  its entry goes and false says the house may keep a copy. */
    fun forgetInactive(context: Context, server: String): Boolean {
        val key = ServerCredentials.normalize(server)
        val had = ShareRules.mayHoldCopy(store(context).get(key))
        store(context).forget(key)
        return !had
    }
}

/** The two calls, as the app's client makes them ([onlyTo] null: to
 *  whichever server is active by then, which [ShareCalls] never does). */
internal interface ShareHttp {
    suspend fun put(path: String, body: JsonObject, onlyTo: String?)
    suspend fun delete(path: String, onlyTo: String?)
}

private class ApiShareHttp(private val api: ApiClient) : ShareHttp {
    override suspend fun put(path: String, body: JsonObject, onlyTo: String?) { api.put(path, body, onlyTo = onlyTo) }
    override suspend fun delete(path: String, onlyTo: String?) { api.delete(path, onlyTo = onlyTo) }
}

/**
 * This phone's PUT and DELETE, each sent ONLY to the server the pass was
 * started for ([ApiClient.raw] `onlyTo`): if the app has moved to another
 * server meanwhile, nothing is sent (a ServerChangedException, tried again
 * later), so one household's calendars never reach another.
 */
internal class ShareCalls(deviceId: String, private val http: ShareHttp) {
    val path = "/api/calendar/phones/${encodePathSegment(deviceId)}"
    suspend fun put(server: String, body: JsonObject) = http.put(path, body, onlyTo = server)
    suspend fun delete(server: String) = http.delete(path, onlyTo = server)
}

/** The send (or delete) for the active server. */
class CalendarShareWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val app = (applicationContext as DomovoiApplication).container
        // Room mode keeps the household side off (net/HouseholdLock.kt).
        if (HouseholdLock.roomModeActive) return Result.retry()
        // The server is read ONCE: every call of this pass goes to it only.
        val server = ServerCredentials.normalize(app.prefs.serverUrl.value)
        if (server.isBlank() || app.prefs.tokenForServer(server) == null) return Result.success()
        val store = CalendarShare.store(applicationContext)
        // A shared screen (the kitchen tablet) is offered no sharing; one
        // marked shared after it was turned on sends no more, but may still
        // delete the copy it sent.
        if (app.prefs.sharedHere(server) && ShareRules.action(store.get(server)) == ShareAction.Put) {
            return Result.success()
        }
        val out = CalendarShare.sync(applicationContext, app, server).run(server)
        CalendarShare.schedule(applicationContext)
        return if (out == ShareSync.Outcome.Retry) Result.retry() else Result.success()
    }
}

/** The calendar provider changed: queue a send and watch again. */
class CalendarChangeWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val app = (applicationContext as DomovoiApplication).container
        val server = ServerCredentials.normalize(app.prefs.serverUrl.value)
        val paired = server.isNotBlank() && app.prefs.tokenForServer(server) != null
        if (ShareRules.needsJob(CalendarShare.store(applicationContext).all(), server, paired)) {
            CalendarShare.sendSoon(applicationContext, delayMs = 0)
            CalendarShare.rearmChange(applicationContext)
        }
        return Result.success()
    }
}
