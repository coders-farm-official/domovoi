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
import com.domovoi.app.DomovoiApplication
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.net.HouseholdLock
import com.domovoi.app.net.encodePathSegment
import com.domovoi.app.net.suggestedDeviceName
import java.io.File
import java.util.concurrent.TimeUnit

/*
 * When the phone's calendars go to the house. Three WorkManager jobs, no
 * foreground service, nothing running in between:
 *
 *  * "calendar-share-now": one send (or the delete). Queued a few seconds
 *    after sharing is turned on or off or a calendar is ticked (several
 *    ticks in a row make one send), and by the change watch below. Needs a
 *    network; a failure tries again after a minute, then two, and so on
 *    (linear back-off), and a 429 waits at least its Retry-After.
 *  * "calendar-share-change": waits for any change in the phone's calendar
 *    provider (a content-URI trigger on CalendarContract.CONTENT_URI, the
 *    platform's JobScheduler, so it costs nothing while nothing changes),
 *    then 90 seconds of quiet (at most 5 minutes) so a burst of edits or an
 *    account sync is one send. It queues the send and arms itself again.
 *  * "calendar-share-periodic": every 6 hours with a network, to keep the
 *    rolling window moving and to catch anything the watch missed (a
 *    reboot, a provider that didn't say).
 *
 * All three run only while some server has sharing on or a copy to delete
 * ([ShareRules.needsJob]); [schedule] cancels them otherwise.
 *
 * One server at a time: the ACTIVE one (the call is sent only to it,
 * `onlyTo`). A server switched away from keeps its entry, and its send or
 * pending delete goes when it is the active one again.
 */
object CalendarShare {
    const val NOW = "calendar-share-now"
    const val CHANGE = "calendar-share-change"
    const val PERIODIC = "calendar-share-periodic"

    /** A tick, then another: one send. */
    const val SETTLE_MS = 8_000L

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

    /** Start or stop the background jobs to match the stored entries. */
    fun schedule(context: Context) {
        val wm = WorkManager.getInstance(context)
        if (ShareRules.needsJob(store(context).all())) {
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
     *  phone to wait. Replaces a send already waiting. */
    fun sendSoon(context: Context, delayMs: Long = SETTLE_MS) {
        val app = (context.applicationContext as? DomovoiApplication)?.container
        val server = app?.let { ServerCredentials.normalize(it.prefs.serverUrl.value) }.orEmpty()
        val wait = ShareRules.waitMs(store(context).get(server), System.currentTimeMillis())
        WorkManager.getInstance(context).enqueueUniqueWork(
            NOW, ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<CalendarShareWorker>()
                .setConstraints(network)
                .setInitialDelay(maxOf(delayMs, wait), TimeUnit.MILLISECONDS)
                .setBackoffCriteria(BackoffPolicy.LINEAR, 60, TimeUnit.SECONDS)
                .build(),
        )
        schedule(context)
    }

    /** Arm the change watch again (from inside its own run: appended, so it
     *  waits for the current run to finish rather than cancelling it). */
    internal fun rearmChange(context: Context) {
        if (!ShareRules.needsJob(store(context).all())) return
        WorkManager.getInstance(context).enqueueUniqueWork(CHANGE, ExistingWorkPolicy.APPEND_OR_REPLACE, changeRequest())
    }
}

/** The send (or delete) for the active server. */
class CalendarShareWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val app = (applicationContext as DomovoiApplication).container
        // Room mode keeps the household side off (net/HouseholdLock.kt).
        if (HouseholdLock.roomModeActive) return Result.retry()
        val server = ServerCredentials.normalize(app.prefs.serverUrl.value)
        if (server.isBlank() || app.prefs.tokenForServer(server) == null) return Result.success()
        val store = CalendarShare.store(applicationContext)
        // A shared screen (the kitchen tablet) is offered no sharing; one
        // marked shared after it was turned on sends no more, but may still
        // delete the copy it sent.
        if (app.prefs.sharedHere(server) && ShareRules.action(store.get(server)) == ShareAction.Put) {
            return Result.success()
        }
        val reader = PhoneCalendarReader(applicationContext)
        val path = "/api/calendar/phones/${encodePathSegment(app.prefs.deviceId)}"
        val sync = ShareSync(
            store = store,
            calendars = reader::calendars,
            instances = reader::instances,
            put = { to, body -> app.api.put(path, body, onlyTo = to) },
            delete = { to -> app.api.delete(path, onlyTo = to) },
            phoneName = { suggestedDeviceName() },
        )
        val out = sync.run(server)
        CalendarShare.schedule(applicationContext)
        return if (out == ShareSync.Outcome.Retry) Result.retry() else Result.success()
    }
}

/** The calendar provider changed: queue a send and watch again. */
class CalendarChangeWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val store = CalendarShare.store(applicationContext)
        if (ShareRules.needsJob(store.all())) {
            CalendarShare.sendSoon(applicationContext, delayMs = 0)
            CalendarShare.rearmChange(applicationContext)
        }
        return Result.success()
    }
}
