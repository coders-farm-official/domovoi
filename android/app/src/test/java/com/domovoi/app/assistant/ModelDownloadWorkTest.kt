package com.domovoi.app.assistant

import androidx.work.NetworkType
import androidx.work.WorkInfo
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.io.IOException
import javax.xml.parsers.DocumentBuilderFactory

class ModelDownloadWorkTest {

    private fun model(id: String, size: Long) = CatalogModel(
        id, id, ModelKind.STT, listOf(ModelFile("f", "https://huggingface.co/x/y/resolve/${"0".repeat(40)}/f", size, "0".repeat(64))),
        "MIT", "https://example.org",
    )

    private val a = model("a", 100)
    private val b = model("b", 300)

    // ── One run of the job ──────────────────────────────────────────────

    @Test fun progressRunsAcrossEveryModel() = runBlocking {
        val seen = mutableListOf<DownloadProgress>()
        val outcome = fetchModels(listOf(a, b), install = { m, onBytes ->
            onBytes(m.totalBytes / 2, m.totalBytes)
            onBytes(m.totalBytes, m.totalBytes)
        }) { seen += it }
        assertEquals(FetchOutcome.Done, outcome)
        assertEquals(listOf(50L, 100L, 250L, 400L, 400L), seen.map { it.allDone })
        assertTrue(seen.all { it.allTotal == 400L })
        assertEquals(listOf("a", "a", "b", "b", null), seen.map { it.modelId })
    }

    @Test fun aNetworkFailureIsARetryThatKeepsWhatIsDone() = runBlocking {
        val installed = mutableListOf<String>()
        val outcome = fetchModels(listOf(a, b), install = { m, _ ->
            if (m == b) throw IOException("unexpected end of stream")
            installed += m.id
        })
        assertTrue(outcome is FetchOutcome.Retry)
        assertEquals(listOf("a"), installed)
    }

    @Test fun aChecksumFailureOrAFullDiskIsAFailureNotARetry() = runBlocking {
        fun failing(e: Exception): suspend (CatalogModel, (Long, Long) -> Unit) -> Unit = { _, _ -> throw e }
        val hash = fetchModels(listOf(a), failing(ModelDownloader.HashMismatch("f", "00", "ff")))
        assertTrue(hash is FetchOutcome.Failed && hash.reason.contains("checksum"))
        val space = fetchModels(listOf(a), failing(ModelDownloader.NotEnoughSpace(10, 1)))
        assertTrue(space is FetchOutcome.Failed && space.reason.contains("space"))
        val full = fetchModels(listOf(a), failing(IOException("write failed: ENOSPC (No space left on device)")))
        assertTrue(full is FetchOutcome.Failed && full.reason.contains("ran out of space"))
    }

    @Test fun aPauseIsNotSwallowed() {
        try {
            runBlocking { fetchModels(listOf(a), install = { _, _ -> throw CancellationException("paused") }) }
            fail("cancellation must pass through")
        } catch (e: CancellationException) {
            assertEquals("paused", e.message)
        }
    }

    @Test fun progressSurvivesWorkManagersDataRoundTrip() {
        val p = DownloadProgress("gemma-4-e2b-it", 10, 20, 30, 40)
        assertEquals(p, DownloadProgress.from(p.toData()))
        assertNull(DownloadProgress.from(androidx.work.Data.EMPTY))
    }

    // ── The job request ─────────────────────────────────────────────────

    @Test fun wifiOnlyByDefaultAnyNetworkWhenMobileDataIsAllowed() {
        val wifi = ModelDownloads.request(listOf(a, b), allowMobileData = false)
        assertEquals(NetworkType.UNMETERED, wifi.workSpec.constraints.requiredNetworkType)
        assertFalse(ModelDownloads.TAG_MOBILE_DATA in wifi.tags)
        assertEquals(listOf("a", "b"), wifi.workSpec.input.getStringArray(ModelDownloads.KEY_MODELS)!!.toList())
        // Each model is a tag too, so whoever watches the job knows what it fetches.
        assertTrue("model:a" in wifi.tags && "model:b" in wifi.tags)

        val mobile = ModelDownloads.request(listOf(a), allowMobileData = true)
        assertEquals(NetworkType.CONNECTED, mobile.workSpec.constraints.requiredNetworkType)
        assertTrue(ModelDownloads.TAG_MOBILE_DATA in mobile.tags)
        assertEquals(ModelDownloadWorker::class.java.name, mobile.workSpec.workerClassName)
        assertEquals("assistant-models", ModelDownloads.UNIQUE_WORK)
    }

    @Test fun theCardSaysWhatAQueuedJobWaitsFor() {
        assertEquals(DownloadStatus(true, "waiting for Wi-Fi"), describeDownload(WorkInfo.State.ENQUEUED, 0, false, null))
        assertEquals(DownloadStatus(true, "waiting for a network"), describeDownload(WorkInfo.State.ENQUEUED, 0, true, null))
        assertEquals(DownloadStatus(true, "connection lost; carries on when Wi-Fi is back"),
            describeDownload(WorkInfo.State.ENQUEUED, 2, false, null))
        assertEquals(DownloadStatus(true, null), describeDownload(WorkInfo.State.RUNNING, 0, false, null))
        assertEquals(DownloadStatus(false, "a.bin failed its checksum"),
            describeDownload(WorkInfo.State.FAILED, 1, false, "a.bin failed its checksum"))
        assertEquals(DownloadStatus(false, null), describeDownload(WorkInfo.State.SUCCEEDED, 0, false, null))
        assertEquals(DownloadStatus(false, null), describeDownload(WorkInfo.State.CANCELLED, 0, false, null))
        assertEquals(DownloadStatus(false, null), describeDownload(null, 0, false, null))
    }

    /** The job as the assistant's screens see it (assistant/DownloadJobs.kt). */
    @Test fun theJobAsTheAssistantSeesIt() {
        val info = WorkInfo(
            id = java.util.UUID.randomUUID(),
            state = WorkInfo.State.RUNNING,
            tags = setOf(ModelDownloadWorker::class.java.name, "model:gemma-4-e2b-it", "model:silero-vad-v4", ModelDownloads.TAG_MOBILE_DATA),
            progress = DownloadProgress("gemma-4-e2b-it", 1, 2, 3, 4).toData(),
            runAttemptCount = 2,
        )
        val job = WorkDownloadJobs.of(info)
        assertEquals(setOf("gemma-4-e2b-it", "silero-vad-v4"), job.models)
        assertTrue(job.mobileData)
        assertEquals(2, job.attempts)
        assertEquals(DownloadProgress("gemma-4-e2b-it", 1, 2, 3, 4), job.progress)
        assertTrue(job.active)

        val failed = WorkDownloadJobs.of(WorkInfo(
            id = java.util.UUID.randomUUID(), state = WorkInfo.State.FAILED, tags = setOf("model:gemma-4-e2b-it"),
            outputData = androidx.work.workDataOf(ModelDownloads.KEY_ERROR to "not enough free space for Gemma"),
        ))
        assertTrue(failed.finished)
        assertFalse(failed.mobileData)
        assertNull(failed.progress)
        assertEquals("not enough free space for Gemma", failed.error)
    }

    // ── The manifest the foreground job needs ───────────────────────────

    @Test fun theForegroundJobIsDeclaredAsDataSync() {
        val manifest = listOf(File("src/main/AndroidManifest.xml"), File("app/src/main/AndroidManifest.xml"))
            .first { it.isFile }
        val doc = DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }
            .newDocumentBuilder().parse(manifest)
        val ns = "http://schemas.android.com/apk/res/android"
        fun all(tag: String) = doc.getElementsByTagName(tag).let { n -> (0 until n.length).map { n.item(it) as org.w3c.dom.Element } }
        assertTrue(all("uses-permission").any { it.getAttributeNS(ns, "name") == "android.permission.FOREGROUND_SERVICE_DATA_SYNC" })
        assertTrue(all("uses-permission").any { it.getAttributeNS(ns, "name") == "android.permission.POST_NOTIFICATIONS" })
        val service = all("service").single { it.getAttributeNS(ns, "name") == "androidx.work.impl.foreground.SystemForegroundService" }
        assertEquals("dataSync", service.getAttributeNS(ns, "foregroundServiceType"))
    }
}
