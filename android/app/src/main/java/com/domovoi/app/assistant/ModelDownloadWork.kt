package com.domovoi.app.assistant

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
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import java.io.IOException
import java.util.concurrent.TimeUnit

/** How far the model download has got, as the job reports it. */
data class DownloadProgress(
    val modelId: String?,
    val modelDone: Long,
    val modelTotal: Long,
    val allDone: Long,
    val allTotal: Long,
) {
    fun toData(): Data = workDataOf(
        P_MODEL to modelId, P_MODEL_DONE to modelDone, P_MODEL_TOTAL to modelTotal,
        P_ALL_DONE to allDone, P_ALL_TOTAL to allTotal,
    )

    companion object {
        private const val P_MODEL = "model"
        private const val P_MODEL_DONE = "model_done"
        private const val P_MODEL_TOTAL = "model_total"
        private const val P_ALL_DONE = "all_done"
        private const val P_ALL_TOTAL = "all_total"

        /** Null for a job that has not reported yet. */
        fun from(data: Data): DownloadProgress? {
            if (!data.keyValueMap.containsKey(P_ALL_TOTAL)) return null
            return DownloadProgress(
                data.getString(P_MODEL), data.getLong(P_MODEL_DONE, 0), data.getLong(P_MODEL_TOTAL, 0),
                data.getLong(P_ALL_DONE, 0), data.getLong(P_ALL_TOTAL, 0),
            )
        }
    }
}

/** What one run of the download job came to. */
sealed interface FetchOutcome {
    data object Done : FetchOutcome

    /** The network failed: run again once it is back (WorkManager retries
     *  under the job's network constraint, and the files resume). */
    data class Retry(val reason: String) : FetchOutcome

    /** Retrying would not help: a checksum failed twice, or no space. */
    data class Failed(val reason: String) : FetchOutcome
}

/**
 * Installs [models] one after another through [install] (the downloader:
 * resume, hash, rename), reporting progress across all of them, and turns
 * what went wrong into a retry or a failure. Cancellation (the user's pause,
 * or WorkManager stopping the job when Wi-Fi goes) passes through: the
 * `.part` files stay for the next run.
 */
suspend fun fetchModels(
    models: List<CatalogModel>,
    install: suspend (CatalogModel, (done: Long, total: Long) -> Unit) -> Unit,
    onProgress: (DownloadProgress) -> Unit = {},
): FetchOutcome {
    val allTotal = models.sumOf { it.totalBytes }
    var before = 0L
    for (m in models) {
        try {
            install(m) { done, total -> onProgress(DownloadProgress(m.id, done, total, before + done, allTotal)) }
        } catch (e: CancellationException) {
            throw e
        } catch (e: ModelDownloader.HashMismatch) {
            return FetchOutcome.Failed("${m.name}: ${e.message}")
        } catch (e: ModelDownloader.NotEnoughSpace) {
            return FetchOutcome.Failed("not enough free space for ${m.name}")
        } catch (e: IOException) {
            val why = e.message ?: e.javaClass.simpleName
            if (why.contains("ENOSPC") || why.contains("No space left", ignoreCase = true)) {
                return FetchOutcome.Failed("the phone ran out of space during ${m.name}")
            }
            return FetchOutcome.Retry("${m.name}: $why")
        }
        before += m.totalBytes
    }
    onProgress(DownloadProgress(null, 0, 0, allTotal, allTotal))
    return FetchOutcome.Done
}

/**
 * The model download as one WorkManager job ("assistant-models"): it
 * survives leaving the app, an app restart and a dropped connection, and
 * runs in the foreground with a progress notification. Wi-Fi only unless
 * the user allows mobile data. Pausing cancels the job; the `.part` files
 * stay and the next start resumes them.
 *
 * The phone's voice downloads the same way as a job of its own
 * ("phone-voice"), so starting one never replaces the other.
 */
object ModelDownloads {
    const val UNIQUE_WORK = "assistant-models"
    const val UNIQUE_VOICE = "phone-voice"
    const val TAG_MOBILE_DATA = "assistant-models-mobile-data"
    internal const val KEY_MODELS = "models"
    internal const val KEY_ERROR = "error"

    fun constraints(allowMobileData: Boolean): Constraints = Constraints.Builder()
        .setRequiredNetworkType(if (allowMobileData) NetworkType.CONNECTED else NetworkType.UNMETERED)
        .build()

    fun request(models: List<CatalogModel>, allowMobileData: Boolean): OneTimeWorkRequest =
        OneTimeWorkRequestBuilder<ModelDownloadWorker>()
            .setConstraints(constraints(allowMobileData))
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .setInputData(workDataOf(KEY_MODELS to models.map { it.id }.toTypedArray()))
            .apply { if (allowMobileData) addTag(TAG_MOBILE_DATA) }
            .build()

    /** Starts (or restarts, with these models and this network rule) the job. */
    fun start(context: Context, models: List<CatalogModel>, allowMobileData: Boolean) {
        WorkManager.getInstance(context)
            .enqueueUniqueWork(UNIQUE_WORK, ExistingWorkPolicy.REPLACE, request(models, allowMobileData))
    }

    fun pause(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(UNIQUE_WORK)
    }

    /** The job's latest state, or null when there has been none. */
    fun watch(context: Context): Flow<WorkInfo?> =
        WorkManager.getInstance(context).getWorkInfosForUniqueWorkFlow(UNIQUE_WORK).map { it.lastOrNull() }

    /** Downloads [voice] (only that one) as the voice's own job. */
    fun startVoice(context: Context, voice: CatalogModel, allowMobileData: Boolean) {
        require(voice.kind == ModelKind.TTS) { "${voice.id} is not a voice" }
        WorkManager.getInstance(context)
            .enqueueUniqueWork(UNIQUE_VOICE, ExistingWorkPolicy.REPLACE, request(listOf(voice), allowMobileData))
    }

    fun pauseVoice(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(UNIQUE_VOICE)
    }

    fun watchVoice(context: Context): Flow<WorkInfo?> =
        WorkManager.getInstance(context).getWorkInfosForUniqueWorkFlow(UNIQUE_VOICE).map { it.lastOrNull() }

    fun errorOf(info: WorkInfo): String? = info.outputData.getString(KEY_ERROR)
}

/** What the card says about the download job. */
data class DownloadStatus(val active: Boolean, val note: String?)

/** The job's WorkManager state as the card shows it: queued or running
 *  counts as downloading, and a queued job says what it waits for. */
fun describeDownload(state: WorkInfo.State?, attempts: Int, mobileData: Boolean, error: String?): DownloadStatus {
    val network = if (mobileData) "a network" else "Wi-Fi"
    return when (state) {
        null, WorkInfo.State.SUCCEEDED, WorkInfo.State.CANCELLED -> DownloadStatus(false, null)
        WorkInfo.State.FAILED -> DownloadStatus(false, error ?: "download failed")
        WorkInfo.State.RUNNING -> DownloadStatus(true, null)
        WorkInfo.State.ENQUEUED, WorkInfo.State.BLOCKED -> DownloadStatus(
            true,
            if (attempts > 0) "connection lost; carries on when $network is back" else "waiting for $network",
        )
    }
}

/** The job itself: thin glue around [fetchModels]. */
class ModelDownloadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val ids = inputData.getStringArray(ModelDownloads.KEY_MODELS).orEmpty().toSet()
        val models = ModelCatalog.all.filter { it.id in ids }
        if (models.isEmpty()) return Result.success()
        val container = (applicationContext as DomovoiApplication).container
        val store = ModelStore.forApp(applicationContext)
        // A foreground job keeps running when the app leaves the screen. If
        // Android refuses (a start from the background on 12+), the job
        // still runs, as an ordinary one.
        runCatching { setForeground(foregroundInfo(null, models)) }
        var shownAt = 0L
        val outcome = fetchModels(
            models,
            install = { m, onBytes -> container.modelDownloader.install(m, store, onBytes) },
        ) { p ->
            val now = SystemClock.elapsedRealtime()
            if (now - shownAt >= PROGRESS_EVERY_MS || p.allDone == p.allTotal) {
                shownAt = now
                setProgressAsync(p.toData())
                runCatching { setForegroundAsync(foregroundInfo(p, models)) }
            }
        }
        return when (outcome) {
            FetchOutcome.Done -> Result.success()
            is FetchOutcome.Retry -> Result.retry()
            is FetchOutcome.Failed -> Result.failure(workDataOf(ModelDownloads.KEY_ERROR to outcome.reason))
        }
    }

    override suspend fun getForegroundInfo(): ForegroundInfo = foregroundInfo(null, emptyList())

    private fun foregroundInfo(p: DownloadProgress?, models: List<CatalogModel>): ForegroundInfo {
        val voice = models.isNotEmpty() && models.all { it.kind == ModelKind.TTS }
        val manager = applicationContext.getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && manager.getNotificationChannel(CHANNEL) == null) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL, "assistant model download", NotificationManager.IMPORTANCE_LOW),
            )
        }
        val current = models.firstOrNull { it.id == p?.modelId }
        val text = when {
            p == null -> "starting"
            p.allDone >= p.allTotal -> "verifying"
            voice -> "${current?.name ?: "voice"}: ${mb(p.allDone)} of ${mb(p.allTotal)}"
            else -> "${current?.name ?: "models"}: ${gb(p.allDone)} of ${gb(p.allTotal)}"
        }
        val percent = if (p == null || p.allTotal <= 0) 0 else (p.allDone * 100 / p.allTotal).toInt()
        val open = applicationContext.packageManager.getLaunchIntentForPackage(applicationContext.packageName)
            ?.let { PendingIntent.getActivity(applicationContext, 0, it, PendingIntent.FLAG_IMMUTABLE) }
        val notification = NotificationCompat.Builder(applicationContext, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle(if (voice) "Downloading the voice" else "Downloading the on-device assistant")
            .setContentText(text)
            .setProgress(100, percent, p == null)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(open)
            .addAction(0, "pause", WorkManager.getInstance(applicationContext).createCancelPendingIntent(id))
            .build()
        val id = if (voice) VOICE_NOTIFICATION_ID else NOTIFICATION_ID
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ForegroundInfo(id, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            ForegroundInfo(id, notification)
        }
    }

    private fun gb(bytes: Long) = String.format(java.util.Locale.US, "%.2f GB", bytes / 1e9)

    private fun mb(bytes: Long) = String.format(java.util.Locale.US, "%.0f MB", bytes / 1e6)

    companion object {
        const val CHANNEL = "assistant-models"
        private const val NOTIFICATION_ID = 0x0D0E
        private const val VOICE_NOTIFICATION_ID = 0x0D0F
        private const val PROGRESS_EVERY_MS = 500L
    }
}
