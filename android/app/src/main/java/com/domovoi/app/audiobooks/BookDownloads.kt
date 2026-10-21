package com.domovoi.app.audiobooks

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequest
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.domovoi.app.DomovoiApplication
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.HouseholdLock
import com.domovoi.app.player.ServerScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import java.io.IOException
import java.util.concurrent.TimeUnit

/*
 * "Save to phone" as one WorkManager job per book ("audiobook-<folder>"): it
 * survives the app closing and a restart, shows its progress in a
 * notification, and can be cancelled from the app or the notification.
 *
 * There is no mobile-data setting (owner decision 2026-10-20): the server is
 * only reachable on the home network. The job needs a network, then checks
 * that the book's server answers; when it does not (the phone left home, the
 * server is off), the job ends in a retry and WorkManager runs it again
 * later, while the `.part` files wait to resume. The retries back off
 * linearly from a minute, so a phone that was away for a day tries about
 * hourly; and the app tries at once whenever the phone joins a network
 * ([kickWaiting]), which is when it most likely got home.
 */

/** A book's download job as the app sees it. */
data class BookJob(
    val key: String,
    val state: WorkInfo.State,
    /** Runs that ended in a retry (out of reach, a dropped connection). */
    val attempts: Int,
    val done: Long?,
    val total: Long?,
    val error: String?,
)

/** What the app says about a book and this phone. */
sealed interface SaveState {
    /** Not on the phone. */
    data object None : SaveState

    /** Saving, [done] of [total] bytes ([total] null: unknown). */
    data class Saving(val done: Long, val total: Long?) : SaveState {
        val percent: Int? get() = total?.takeIf { it > 0 }?.let { ((done * 100) / it).toInt().coerceIn(0, 100) }
    }

    /** Queued: waiting for the server to be in reach. [interrupted] when a
     *  run already started and lost the server. */
    data class Waiting(val interrupted: Boolean) : SaveState

    /** On the phone in full. */
    data object Saved : SaveState

    /** Started but not running: cancelled from the notification, or failed
     *  ([reason]). Resume or remove. */
    data class Stopped(val reason: String?) : SaveState
}

object SaveStates {
    /** Pure: what the book's record on disk ([meta], null: none) and its
     *  job ([job], null: none) add up to. */
    fun of(meta: SavedBookMeta?, job: BookJob?): SaveState {
        if (meta == null) return SaveState.None
        if (meta.complete) return SaveState.Saved
        return when (job?.state) {
            WorkInfo.State.RUNNING -> SaveState.Saving(job.done ?: 0L, job.total ?: meta.sizeBytes)
            WorkInfo.State.ENQUEUED, WorkInfo.State.BLOCKED -> SaveState.Waiting(interrupted = job.attempts > 0)
            WorkInfo.State.FAILED -> SaveState.Stopped(job.error)
            // Cancelled, or finished without the record saying complete (the
            // run that would have marked it was cut off), or no job at all
            // (the app's data was restored without WorkManager's): stopped.
            else -> SaveState.Stopped(null)
        }
    }

    /** Short lowercase text for the badge. */
    fun label(state: SaveState, serverName: String? = null): String = when (state) {
        SaveState.None -> ""
        SaveState.Saved -> "on this phone"
        is SaveState.Saving -> "saving" + (state.percent?.let { " $it%" } ?: "…")
        is SaveState.Waiting -> if (state.interrupted) {
            "paused · resumes when ${serverName ?: "the server"} is in reach"
        } else {
            "waiting for ${serverName ?: "the server"}"
        }
        is SaveState.Stopped -> state.reason?.let { "stopped: $it" } ?: "stopped"
    }
}

object BookDownloads {
    const val TAG = "audiobook-download"
    private const val TAG_KEY = "book:"
    internal const val KEY_BOOK = "book"
    internal const val KEY_ERROR = "error"
    internal const val P_DONE = "done"
    internal const val P_TOTAL = "total"

    fun uniqueName(key: String) = "audiobook-$key"

    /** Any network; the job itself checks the server is in reach. */
    val constraints: Constraints = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    fun request(key: String): OneTimeWorkRequest =
        OneTimeWorkRequestBuilder<BookDownloadWorker>()
            .setConstraints(constraints)
            .setBackoffCriteria(BackoffPolicy.LINEAR, 60, TimeUnit.SECONDS)
            .setInputData(workDataOf(KEY_BOOK to key))
            .addTag(TAG)
            .addTag(TAG_KEY + key)
            .build()

    /** Starts the book's job; [now] restarts one that is waiting (resets
     *  its back-off). A running one is left to run. */
    fun start(context: Context, key: String, now: Boolean = false) {
        WorkManager.getInstance(context).enqueueUniqueWork(
            uniqueName(key),
            if (now) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.KEEP,
            request(key),
        )
    }

    /** Stops the job; the `.part` files stay (resume picks them up). */
    fun stop(context: Context, key: String) {
        WorkManager.getInstance(context).cancelUniqueWork(uniqueName(key))
    }

    /** Every book's latest job, by folder key. */
    fun watch(context: Context): Flow<Map<String, BookJob>> =
        WorkManager.getInstance(context).getWorkInfosByTagFlow(TAG).map { infos ->
            infos.mapNotNull(::of).groupBy { it.key }.mapValues { (_, jobs) ->
                // A replaced job's old row can linger: the unfinished one wins.
                jobs.firstOrNull { !it.state.isFinished } ?: jobs.last()
            }
        }

    fun of(info: WorkInfo): BookJob? {
        val key = info.tags.firstOrNull { it.startsWith(TAG_KEY) }?.removePrefix(TAG_KEY) ?: return null
        val p = info.progress
        return BookJob(
            key = key,
            state = info.state,
            attempts = info.runAttemptCount,
            done = p.keyValueMap[P_DONE] as? Long,
            total = (p.keyValueMap[P_TOTAL] as? Long)?.takeIf { it > 0 },
            error = info.outputData.getString(KEY_ERROR),
        )
    }

    /**
     * The phone joined a network, or the app found the server: every book
     * waiting on a back-off tries now. Only waiting ones — a running job is
     * left alone, and a stopped one stays stopped until the person resumes it.
     */
    fun kickWaiting(context: Context, store: BookStore) {
        val wm = WorkManager.getInstance(context)
        val incomplete = runCatching { store.list().filterNot { it.meta.complete } }.getOrDefault(emptyList())
        for (b in incomplete) {
            val infos = runCatching { wm.getWorkInfosForUniqueWork(uniqueName(b.key)).get(2, TimeUnit.SECONDS) }
                .getOrNull() ?: continue
            val last = infos.lastOrNull() ?: continue
            if (last.state == WorkInfo.State.ENQUEUED && last.runAttemptCount > 0) start(context, b.key, now = true)
        }
    }
}

/** The job: checks the server, refreshes the book's record, fetches the files. */
class BookDownloadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val key = inputData.getString(BookDownloads.KEY_BOOK) ?: return Result.success()
        val container = (applicationContext as DomovoiApplication).container
        val store = container.books
        val saved = store.read(key) ?: return Result.success()          // removed meanwhile
        if (saved.meta.complete) return Result.success()
        // Room mode keeps the household side off (net/HouseholdLock.kt).
        if (HouseholdLock.roomModeActive) return Result.retry()

        // The book belongs to the server it came from: another server's
        // book 12 is another book. Wait for that one.
        val url = container.prefs.serverUrl.value
        val active = ServerScope.of(url, container.prefs.pinForServer(url))
        if (!saved.meta.server.isSameServer(active)) return Result.retry()
        if (!container.api.answers()) return Result.retry()

        runCatching { setForeground(foregroundInfo(saved.meta.title, null, null)) }

        // What the server says the book is now: a book re-encoded since the
        // save started gets its new files; a book that is gone fails.
        val fresh = try {
            BookModels.parse(container.api.get("/api/audiobooks/${saved.meta.bookId}"))
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiException) {
            if (e.status == 404) return fail("this book is no longer on the server")
            return Result.retry()
        } catch (e: IOException) {
            return Result.retry()
        }
        val book = refreshed(store, saved, fresh, active) ?: return Result.success()

        var shownAt = 0L
        val outcome = BookFetcher(container.api.http, store).fetch(book, container.api::absolute) { done, total ->
            val now = SystemClock.elapsedRealtime()
            if (now - shownAt >= PROGRESS_EVERY_MS) {
                shownAt = now
                setProgressAsync(workDataOf(BookDownloads.P_DONE to done, BookDownloads.P_TOTAL to (total ?: 0L)))
                runCatching { setForegroundAsync(foregroundInfo(book.meta.title, done, total)) }
            }
        }
        return when (outcome) {
            BookFetchOutcome.Done -> {
                val now = store.read(key) ?: return Result.success()
                runCatching { store.write(key, now.meta.copy(complete = true)) }
                Result.success()
            }
            is BookFetchOutcome.Retry -> Result.retry()
            is BookFetchOutcome.Failed -> fail(outcome.reason)
        }
    }

    private fun fail(reason: String) = Result.failure(workDataOf(BookDownloads.KEY_ERROR to reason))

    /** The record, with the server's current files when they changed (old
     *  files and partial files that no longer belong are deleted). */
    private fun refreshed(store: BookStore, saved: SavedBook, fresh: ServerBook, server: ServerScope): SavedBook? {
        val next = SavedBookMeta.fromServer(fresh, server, saved.meta.serverName, saved.meta.savedAtMs)
        val same = next.files.map { it.name to it.sizeBytes } == saved.meta.files.map { it.name to it.sizeBytes }
        val meta = if (same) {
            saved.meta.copy(
                title = next.title, author = next.author, narrator = next.narrator,
                durationSec = next.durationSec, chapters = next.chapters,
                coverPath = next.coverPath ?: saved.meta.coverPath, sizeBytes = next.sizeBytes ?: saved.meta.sizeBytes,
            )
        } else {
            val keep = next.files.map { it.local }.toSet()
            store.dirOf(saved.key).listFiles()?.forEach { f ->
                val base = f.name.removeSuffix(".part")
                if (base !in keep && f.name != BookStore.META && !f.name.startsWith("cover")) f.delete()
            }
            next.copy(cover = saved.meta.cover)
        }
        return try {
            store.write(saved.key, meta)
            SavedBook(saved.key, meta)
        } catch (e: IOException) {
            null // the folder went: the book was removed meanwhile
        }
    }

    override suspend fun getForegroundInfo(): ForegroundInfo = foregroundInfo("audiobook", null, null)

    private fun foregroundInfo(title: String, done: Long?, total: Long?): ForegroundInfo {
        val manager = applicationContext.getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && manager.getNotificationChannel(CHANNEL) == null) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL, "audiobook downloads", NotificationManager.IMPORTANCE_LOW),
            )
        }
        val percent = if (done == null || total == null || total <= 0) null else ((done * 100) / total).toInt().coerceIn(0, 100)
        val text = when {
            done == null -> "starting"
            total != null -> "${mb(done)} of ${mb(total)}"
            else -> mb(done)
        }
        val open = applicationContext.packageManager.getLaunchIntentForPackage(applicationContext.packageName)
            ?.let { PendingIntent.getActivity(applicationContext, 0, it, PendingIntent.FLAG_IMMUTABLE) }
        val notification = NotificationCompat.Builder(applicationContext, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle("Saving \"$title\" to this phone")
            .setContentText(text)
            .setProgress(100, percent ?: 0, percent == null)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(open)
            .addAction(0, "cancel", WorkManager.getInstance(applicationContext).createCancelPendingIntent(id))
            .build()
        val nid = NOTIFICATION_BASE + (id.hashCode() and 0xFFF)
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ForegroundInfo(nid, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            ForegroundInfo(nid, notification)
        }
    }

    private fun mb(bytes: Long) = String.format(java.util.Locale.US, "%.0f MB", bytes / 1e6)

    companion object {
        const val CHANNEL = "audiobook-downloads"
        private const val NOTIFICATION_BASE = 0x0B00_0000
        private const val PROGRESS_EVERY_MS = 500L
    }
}
