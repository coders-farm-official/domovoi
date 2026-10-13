package com.domovoi.app.assistant

import android.content.Context
import androidx.work.WorkInfo
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/** The model download job ("assistant-models") as the assistant sees it. */
data class DownloadJob(
    val id: String,
    val state: WorkInfo.State,
    /** Runs that ended in a retry (a dropped connection). */
    val attempts: Int,
    /** The job runs on any network, not only Wi-Fi. */
    val mobileData: Boolean,
    /** The catalog ids it fetches. */
    val models: Set<String>,
    val error: String?,
    val progress: DownloadProgress?,
) {
    val finished: Boolean get() = state.isFinished
    val active: Boolean get() = !state.isFinished
}

/**
 * Starting, stopping and watching the download job; WorkManager in the app
 * ([WorkDownloadJobs]), a fake in the tests. There is one job for every
 * model download the assistant and its benchmark start, so starting one
 * replaces the one before (whose `.part` files the new one resumes).
 */
interface DownloadJobs {
    /** The latest job; null when there has never been one. */
    val job: Flow<DownloadJob?>

    fun start(models: List<CatalogModel>, allowMobileData: Boolean)

    /** Stops the job; its `.part` files stay. */
    fun stop()
}

class WorkDownloadJobs(context: Context) : DownloadJobs {
    private val app = context.applicationContext

    override val job: Flow<DownloadJob?> = ModelDownloads.watch(app).map { it?.let(::of) }

    override fun start(models: List<CatalogModel>, allowMobileData: Boolean) =
        ModelDownloads.start(app, models, allowMobileData)

    override fun stop() = ModelDownloads.pause(app)

    companion object {
        fun of(info: WorkInfo): DownloadJob = DownloadJob(
            id = info.id.toString(),
            state = info.state,
            attempts = info.runAttemptCount,
            mobileData = ModelDownloads.TAG_MOBILE_DATA in info.tags,
            models = info.tags.filter { it.startsWith(ModelDownloads.TAG_MODEL) }
                .map { it.removePrefix(ModelDownloads.TAG_MODEL) }.toSet(),
            error = ModelDownloads.errorOf(info),
            progress = info.takeIf { it.state == WorkInfo.State.RUNNING }?.progress?.let(DownloadProgress::from),
        )
    }
}
