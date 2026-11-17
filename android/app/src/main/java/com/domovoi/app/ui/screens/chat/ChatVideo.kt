package com.domovoi.app.ui.screens.chat

import android.content.Context
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.provider.OpenableColumns
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Movie
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.StreamingBody
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import java.io.FileNotFoundException
import java.io.InputStream

/*
 * A video in a home-server chat (1.4.0; web/backend/api/chat_videos.py,
 * domovoi/chat_video/, the dashboard's web/static/chat_videos.jsx).
 *
 * The paperclip offers "a video" only in a home-server chat, and only when
 * the server says it can watch one (`GET /api/chat/models` → `video.available`:
 * AI features on and ffmpeg there; an older server has no `video` key, and
 * that is a no). The phone's own chats never offer it: this is the server's
 * feature. A clip over 3 minutes or 2 GB is refused here, before anything is
 * sent, in the server's own sentences; the server checks again with ffprobe.
 *
 * The file goes up as the request body (no multipart), streamed from the
 * phone's content uri with its length and type, with progress and a way to
 * stop it. Sent with a message (`videos: [{token}]`), the server queues a job
 * and answers at once; the person's message then carries the job's chip
 * ("Watching your video… about 4 minutes left"), read every 3 seconds while
 * queued or running, with stop; once watched, "what was kept" opens the
 * transcript and what each frame might show. The video itself is deleted on
 * the server when the job ends: only text is kept.
 *
 * The logic is pure and unit-tested on the JVM (ChatVideoTest); the
 * composables at the bottom draw it.
 */

// ---------------------------------------------------------------------------
// Limits and the server's words (domovoi/chat_video/limits.py)
// ---------------------------------------------------------------------------

/** The longest video chat takes, in seconds, when the server doesn't say. */
internal const val VIDEO_MAX_SECONDS = 180.0

/** Slack over the limit for a container's rounding (a "3:00" clip can probe as 180.02 s). */
internal const val VIDEO_SLACK_SECONDS = 0.5

/** The largest video file chat takes, in MiB (2 GiB), when the server doesn't say. */
internal const val VIDEO_MAX_MB = 2048L

/** File name endings chat takes. */
internal val VIDEO_EXTENSIONS = listOf(".mp4", ".m4v", ".mov", ".webm")

internal const val VIDEO_TOO_LONG = "Videos up to 3 minutes can be attached."
internal const val VIDEO_TOO_LARGE =
    "Videos up to 2 GB can be attached. Send a shorter clip, or record it at a lower quality."
internal const val VIDEO_UNSUPPORTED =
    "Chat takes MP4, MOV and WebM videos (H.264 or HEVC, the way phones record them). " +
        "Share the video from your phone as MP4 and try again."

/** The paperclip's item. */
internal const val VIDEO_MENU_ITEM = "a video (up to 3 minutes)"

/** A second video picked for the same message (the dashboard's words). */
internal const val VIDEO_ONE_A_MESSAGE = "one video a message"

/** The codes of the upload's refusals (chat_videos.py); each has a help-desk entry. */
internal val VIDEO_REFUSAL_CODES = setOf(
    "video_too_large", "video_unsupported", "video_too_long", "video_unreadable", "video_no_tools", "video_no_room",
)

/** How often a queued or running job is read again (the dashboard's pace). */
internal const val VIDEO_POLL_MS = 3_000L

// ---------------------------------------------------------------------------
// The server's shapes
// ---------------------------------------------------------------------------

/** `GET /api/chat/models` → `video`. */
@Serializable
internal data class ServerVideoInfo(
    val available: Boolean = false,
    /** "ai-off" or "no-ffmpeg" when not available. */
    val reason: String? = null,
    val max_seconds: Double = VIDEO_MAX_SECONDS,
    val max_mb: Long = VIDEO_MAX_MB,
    val accept: String = "",
    val extensions: List<String> = VIDEO_EXTENSIONS,
)

/** `POST /api/chat/videos`'s answer: a video staged for the next message. */
@Serializable
internal data class StagedVideo(
    val token: String = "",
    val name: String = "",
    val duration_s: Double = 0.0,
    val size: Long = 0,
    val width: Int? = null,
    val height: Int? = null,
    val has_audio: Boolean = false,
    val estimate_s: Double? = null,
    val estimate_text: String? = null,
)

@Serializable
internal data class VideoJobProgress(
    val pieces_done: Int = 0,
    val pieces: Int = 0,
    val frames_done: Int = 0,
    val frames: Int = 0,
)

/** A video job's view (`GET /api/chat/video-jobs/{id}`, and a thread's `videos`). */
@Serializable
internal data class ServerVideoJob(
    val id: String = "",
    val thread_id: Long? = null,
    /** The person's message it was sent with: the chip goes under it. */
    val message_id: Long? = null,
    val name: String = "",
    val question: String = "",
    val duration_s: Double = 0.0,
    val size: Long = 0,
    /** queued | running | done | failed | cancelled */
    val state: String = "",
    /** scan | transcribe | frames | combine, while running. */
    val stage: String? = null,
    /** The server's words for the attachment. */
    val line: String = "",
    val waiting_for_voice: Boolean = false,
    val estimate_s: Double? = null,
    val eta_s: Double? = null,
    val progress: VideoJobProgress = VideoJobProgress(),
    val summary: String? = null,
    val error: String? = null,
    val error_code: String? = null,
    val cancel_requested: Boolean = false,
) {
    val live: Boolean get() = state == "queued" || state == "running"
}

@Serializable
internal data class VideoTranscriptPiece(val start_s: Double = 0.0, val end_s: Double = 0.0, val text: String = "")

@Serializable
internal data class VideoFrameGuess(val at_s: Double = 0.0, val might_show: String = "")

/** `GET /api/chat/video-jobs/{id}/results`: what was kept. */
@Serializable
internal data class VideoResults(
    val job: ServerVideoJob? = null,
    val summary: String? = null,
    val transcript: List<VideoTranscriptPiece> = emptyList(),
    val frames: List<VideoFrameGuess> = emptyList(),
)

// ---------------------------------------------------------------------------
// The gate and the checks before anything is sent
// ---------------------------------------------------------------------------

/**
 * Whether the paperclip offers a video: a home-server chat, the household's
 * AI features on, and the server saying it can watch one. Anything else
 * (a phone chat, AI off, no ffmpeg, an older server with no `video` key, the
 * models not read yet) offers nothing: a control that can't work isn't shown.
 */
internal fun videoOffered(info: ChatModelsInfo?, place: ChatPlaceKind, aiOn: Boolean): Boolean =
    place == ChatPlaceKind.Server && aiOn && info?.video?.available == true

/** `2:05` for 125 s (`1:02:05` past an hour), as the server writes it. */
internal fun videoClock(seconds: Double?): String {
    val s = Math.round(maxOf(0.0, seconds ?: 0.0))
    val h = s / 3600
    val m = (s % 3600) / 60
    val sec = s % 60
    val root = java.util.Locale.ROOT
    return if (h > 0) String.format(root, "%d:%02d:%02d", h, m, sec) else String.format(root, "%d:%02d", m, sec)
}

/** The server's sentence for a clip that is too long. */
internal fun videoTooLongText(seconds: Double): String = "$VIDEO_TOO_LONG This one is ${videoClock(seconds)} long."

/** "412 MB" / "1.9 GB", as the dashboard's chip says it. */
internal fun videoSizeText(bytes: Long): String {
    val mb = bytes.coerceAtLeast(0) / (1024.0 * 1024.0)
    return if (mb >= 1024) String.format(java.util.Locale.ROOT, "%.1f GB", mb / 1024) else "${maxOf(1L, Math.round(mb))} MB"
}

/** The ending a video's type stands for, when its name has none chat takes. */
private val VIDEO_EXT_BY_TYPE = mapOf(
    "video/mp4" to ".mp4",
    "video/x-m4v" to ".m4v",
    "video/quicktime" to ".mov",
    "video/webm" to ".webm",
)

/**
 * The name a video goes up under: its own, when it ends the way chat takes
 * ([extensions]); else its own with the ending its [mime] type stands for (a
 * gallery that names a clip without one); null when neither (the server would
 * refuse it as a format chat doesn't take).
 */
internal fun videoUploadName(name: String?, mime: String?, extensions: List<String> = VIDEO_EXTENSIONS): String? {
    val n = name?.trim().orEmpty().ifEmpty { "video" }
    val exts = extensions.map { it.lowercase() }
    val ext = n.substringAfterLast('.', "").lowercase().let { if (it.isEmpty()) "" else ".$it" }
    if (ext in exts) return n
    val byType = VIDEO_EXT_BY_TYPE[mime?.substringBefore(';')?.trim()?.lowercase()] ?: return null
    return if (byType in exts) n + byType else null
}

/**
 * The sentence that refuses a picked video before anything is sent, or null
 * to send it. [uploadName] null: not a format chat takes. [durationS] null or
 * [sizeBytes] < 0: the phone couldn't tell, and the server's own check decides.
 */
internal fun videoRefusalBeforeUpload(
    uploadName: String?,
    durationS: Double?,
    sizeBytes: Long,
    info: ServerVideoInfo = ServerVideoInfo(available = true),
): String? {
    if (uploadName == null) return VIDEO_UNSUPPORTED
    if (sizeBytes >= 0 && sizeBytes > info.max_mb * 1024L * 1024L) return VIDEO_TOO_LARGE
    if (durationS != null && durationS > info.max_seconds + VIDEO_SLACK_SECONDS) return videoTooLongText(durationS)
    return null
}

// ---------------------------------------------------------------------------
// The upload, its refusals, and the job's routes
// ---------------------------------------------------------------------------

/** `POST /api/chat/videos?name=<file name>`, the name percent-encoded. */
internal fun videoUploadPath(name: String): String =
    "/api/chat/videos?name=" + java.net.URLEncoder.encode(name, "UTF-8").replace("+", "%20")

/**
 * Stage one video: the raw bytes from [open] as the request body (no
 * multipart), with its [mime] type and [length] (Content-Length; -1 when the
 * phone can't tell, sent chunked then), reporting whole percent to
 * [onPercent]. Cancelling the coroutine cancels the request. Throws
 * [ApiException] for a refusal ([videoFailureText] words it).
 */
internal suspend fun uploadChatVideo(
    api: ApiClient,
    name: String,
    mime: String,
    length: Long,
    open: () -> InputStream,
    onPercent: (Int) -> Unit,
): StagedVideo {
    val body = StreamingBody(mime.toMediaTypeOrNull(), length, open, onPercent)
    val text = api.raw("POST", videoUploadPath(name), body)
    return DomovoiJson.decodeFromString(StagedVideo.serializer(), text)
}

/**
 * What a person reads when the server refused a video: its own sentence for
 * a coded refusal, the same words for the transport guard's plain-text 413,
 * null for anything else (the caller says "attach failed" with the reason).
 */
internal fun videoRefusalText(status: Int, body: String): String? {
    val obj = runCatching { DomovoiJson.parseToJsonElement(body) as? JsonObject }.getOrNull()
    fun str(key: String) = (obj?.get(key) as? JsonPrimitive)?.takeIf { it.isString }?.content?.trim()
    val detail = str("detail")
    if (!detail.isNullOrEmpty() && (str("code") in VIDEO_REFUSAL_CODES || status in setOf(413, 415, 422, 503, 507))) {
        return detail
    }
    return if (status == 413) VIDEO_TOO_LARGE else null
}

/** A failed video upload in words: AI features off, the server's refusal, or the failure. */
internal fun videoFailureText(e: Throwable): String = when {
    e is ApiException && isAiOffRefusal(e) -> AI_CHAT_OFF_TEXT
    e is ApiException -> videoRefusalText(e.status, e.body) ?: failureText("attach", e)
    else -> failureText("attach", e)
}

internal suspend fun fetchVideoJob(api: ApiClient, id: String): ServerVideoJob =
    api.get("/api/chat/video-jobs/$id").decode<ServerVideoJob>()

/** Stop watching: the server deletes the video and anything understood so far (works with AI off). */
internal suspend fun cancelVideoJob(api: ApiClient, id: String): ServerVideoJob =
    api.post("/api/chat/video-jobs/$id/cancel").decode<ServerVideoJob>()

internal suspend fun fetchVideoResults(api: ApiClient, id: String): VideoResults =
    api.get("/api/chat/video-jobs/$id/results").decode<VideoResults>()

// ---------------------------------------------------------------------------
// Following a job
// ---------------------------------------------------------------------------

/** The jobs to read again (queued or running), as a key: empty stops the polling. */
internal fun liveVideoKey(jobs: List<ServerVideoJob>): String = jobs.filter { it.live }.joinToString(",") { it.id }

/** One round of reading: the new views, and whether one has ended (read the thread again: its summary is in). */
internal data class VideoPoll(val views: List<ServerVideoJob>, val ended: Boolean)

/**
 * Read each live job in [jobs] again with [fetch]. A read that fails is
 * skipped (the next round tries again), so a dropped connection never stops
 * the following; [VideoPoll.ended] is true only when a job really left
 * queued/running.
 */
internal suspend fun pollVideos(jobs: List<ServerVideoJob>, fetch: suspend (String) -> ServerVideoJob): VideoPoll {
    val views = jobs.filter { it.live }.mapNotNull { j ->
        try {
            fetch(j.id)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            null
        }
    }
    return VideoPoll(views, views.any { !it.live })
}

/** [jobs] with each of [views] in its job's place. */
internal fun mergeVideoViews(jobs: List<ServerVideoJob>, views: List<ServerVideoJob>): List<ServerVideoJob> =
    jobs.map { j -> views.firstOrNull { it.id == j.id } ?: j }

// ---------------------------------------------------------------------------
// What the chips say
// ---------------------------------------------------------------------------

/** The composer's video: going up ([percent]) until [staged] says the server
 *  has it. [key] tells one pick from the next, so a late report of a removed
 *  upload never lands on a new one. */
internal data class VideoDraft(val key: Long, val name: String, val percent: Int = 0, val staged: StagedVideo? = null) {
    val uploading: Boolean get() = staged == null
}

/** The composer's video while it goes up ([percent]) or once it is staged. */
internal fun stagedVideoLine(staged: StagedVideo?, percent: Int): String =
    if (staged == null) {
        "sending… ${percent.coerceIn(0, 100)}%"
    } else {
        listOfNotNull(
            videoClock(staged.duration_s),
            videoSizeText(staged.size),
            staged.estimate_text?.takeIf { it.isNotBlank() }?.let { "watching takes $it" },
        ).joinToString(" · ")
    }

/** A sent video's chip: its name and length, the server's line, a bar while
 *  it runs, and what can be done. */
internal data class VideoChipView(
    val name: String,
    val length: String,
    val line: String,
    /** 0..1 while running; null: no bar. */
    val progress: Float?,
    val canStop: Boolean,
    val canOpenKept: Boolean,
    /** Why it failed, in the server's words. */
    val error: String?,
)

/** The server always sends `line`; these stand in for one that is missing. */
private fun fallbackLine(state: String): String = when (state) {
    "queued" -> "Waiting to start…"
    "running" -> "Watching your video…"
    "done" -> "Watched"
    "failed" -> "Couldn't watch it"
    "cancelled" -> "Stopped"
    else -> ""
}

internal fun videoChipView(job: ServerVideoJob): VideoChipView {
    val p = job.progress
    val units = p.pieces + p.frames + 1
    val done = p.pieces_done + p.frames_done
    return VideoChipView(
        name = job.name,
        length = videoClock(job.duration_s),
        line = job.line.ifBlank { fallbackLine(job.state) },
        // The dashboard's bar: never full while it still runs.
        progress = if (job.state == "running") minOf(0.95f, done.toFloat() / units.coerceAtLeast(1)) else null,
        canStop = job.live && !job.cancel_requested,
        canOpenKept = job.state == "done",
        error = job.error?.takeIf { job.state == "failed" && it.isNotBlank() },
    )
}

internal const val VIDEO_STOP = "stop"
internal const val VIDEO_STOPPING = "stopping…"
internal const val VIDEO_STOP_HINT = "stop watching; the video is deleted"
internal const val VIDEO_KEPT = "what was kept"
internal const val KEPT_SAID = "what was said"
internal const val KEPT_NO_SPEECH = "no speech was heard"
internal const val KEPT_FRAMES = "what the pictures might show"
internal const val KEPT_GUESSES = "these are a small model's guesses, and can be wrong"
internal const val KEPT_DELETED = "the video itself was deleted after it was watched"
internal const val KEPT_LOADING = "loading…"
internal const val KEPT_FAILED = "couldn't load what was kept"

/** One kept line: its time in the video, and the words. */
internal data class KeptLine(val at: String, val text: String)

/** What was kept from a watched video, as the dashboard lays it out. */
internal data class VideoKeptView(
    /** The transcript; empty with [noSpeech]. */
    val said: List<KeptLine>,
    val noSpeech: Boolean,
    /** Each frame only as what it "might show" (a small model's guess). */
    val frames: List<KeptLine>,
    /** Under the frames: they are guesses (null when there are none). */
    val framesNote: String?,
    /** Always: the video itself is gone. */
    val deleted: String = KEPT_DELETED,
)

internal fun videoKeptView(r: VideoResults): VideoKeptView = VideoKeptView(
    said = r.transcript.map { KeptLine(videoClock(it.start_s), it.text) },
    noSpeech = r.transcript.isEmpty(),
    frames = r.frames.map { KeptLine(videoClock(it.at_s), "might show: ${it.might_show}") },
    framesNote = KEPT_GUESSES.takeIf { r.frames.isNotEmpty() },
)

// ---------------------------------------------------------------------------
// The phone's side: what a picked video is
// ---------------------------------------------------------------------------

/** A picked video as the phone sees it: its name, type, size (-1: unknown)
 *  and length in seconds (null: unknown). */
internal data class PickedVideo(val name: String?, val mime: String?, val size: Long, val durationS: Double?)

/** Reads [uri]'s name, type, size and length (MediaMetadataRetriever), without reading the file. */
internal fun pickedVideo(context: Context, uri: Uri): PickedVideo {
    val resolver = context.contentResolver
    var name: String? = null
    var size = -1L
    runCatching {
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                if (!c.isNull(0)) name = c.getString(0)
                if (!c.isNull(1)) size = c.getLong(1)
            }
        }
    }
    if (size < 0) {
        size = runCatching { resolver.openAssetFileDescriptor(uri, "r")?.use { it.length } }.getOrNull() ?: -1L
    }
    val duration = runCatching {
        val r = MediaMetadataRetriever()
        try {
            r.setDataSource(context, uri)
            r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull()?.let { it / 1000.0 }
        } finally {
            r.release()
        }
    }.getOrNull()
    return PickedVideo(name, resolver.getType(uri), size, duration)
}

/** Opens [uri] for the upload (reopened on a retry). */
internal fun videoOpener(context: Context, uri: Uri, name: String): () -> InputStream = {
    context.contentResolver.openInputStream(uri) ?: throw FileNotFoundException("can't read \"$name\" on this phone")
}

// ---------------------------------------------------------------------------
// UI
// ---------------------------------------------------------------------------

@Composable
private fun VideoChipFrame(content: @Composable () -> Unit) {
    Column(
        Modifier.widthIn(max = 480.dp).padding(bottom = 4.dp)
            .clip(RoundedCornerShape(10.dp))
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(10.dp))
            .background(Domovoi.colors.card)
            .padding(horizontal = 10.dp, vertical = 8.dp)
            .semantics { liveRegion = LiveRegionMode.Polite },
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) { content() }
}

@Composable
private fun VideoNameRow(name: String, length: String?, trailing: @Composable () -> Unit = {}) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Icon(Icons.Outlined.Movie, contentDescription = null, tint = Domovoi.colors.fgMuted, modifier = Modifier.size(16.dp))
        Text(
            name, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg, maxLines = 1,
            modifier = Modifier.weight(1f, fill = false),
        )
        length?.let { Text(it, style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily), color = Domovoi.colors.fgMuted) }
        trailing()
    }
}

/**
 * A video on the composer (or on a message just sent): going up, with its
 * bar, or staged, with its length, size and how long watching takes.
 * [onRemove] null: no remove (a sent message's).
 */
@Composable
internal fun StagedVideoChip(name: String, staged: StagedVideo?, percent: Int, onRemove: (() -> Unit)?) {
    VideoChipFrame {
        VideoNameRow(name, null) {
            if (onRemove != null) {
                IconButton(onClick = onRemove, modifier = Modifier.size(28.dp)) {
                    Icon(Icons.Outlined.Close, contentDescription = "remove $name", tint = Domovoi.colors.fgMuted)
                }
            }
        }
        Text(
            stagedVideoLine(staged, percent),
            style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily), color = Domovoi.colors.fgMuted,
        )
        if (staged == null) {
            LinearProgressIndicator(
                progress = { percent.coerceIn(0, 100) / 100f },
                modifier = Modifier.fillMaxWidth(),
                color = Domovoi.colors.brand,
                trackColor = Domovoi.colors.border,
            )
        }
    }
}

/** A sent video under its message: the server's line, a bar while it runs,
 *  stop, then how it ended and "what was kept". */
@Composable
internal fun VideoJobChip(job: ServerVideoJob, onStop: (suspend (ServerVideoJob) -> Unit)?) {
    val view = videoChipView(job)
    val scope = rememberCoroutineScope()
    var stopping by remember(job.id) { mutableStateOf(false) }
    var open by remember(job.id) { mutableStateOf(false) }
    VideoChipFrame {
        VideoNameRow(view.name, view.length)
        Text(
            view.line, style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
            color = Domovoi.colors.fg,
        )
        view.progress?.let { p ->
            LinearProgressIndicator(
                progress = { p },
                modifier = Modifier.fillMaxWidth(),
                color = Domovoi.colors.brand,
                trackColor = Domovoi.colors.border,
            )
        }
        view.error?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted) }
        if ((view.canStop && onStop != null) || view.canOpenKept) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (view.canStop && onStop != null) {
                    TextButton(
                        enabled = !stopping,
                        onClick = {
                            stopping = true
                            scope.launch {
                                try { onStop(job) } finally { stopping = false }
                            }
                        },
                        modifier = Modifier.semantics { contentDescription = VIDEO_STOP_HINT },
                    ) { Text(if (stopping) VIDEO_STOPPING else VIDEO_STOP, color = Domovoi.colors.brand) }
                }
                if (view.canOpenKept) {
                    TextButton(onClick = { open = !open }) { Text(VIDEO_KEPT, color = Domovoi.colors.brand) }
                }
            }
        }
        if (open) VideoKept(job.id)
    }
}

/** What was kept from a watched video, read when opened. */
@Composable
private fun VideoKept(jobId: String) {
    val app = LocalApp.current
    var data by remember(jobId) { mutableStateOf<VideoResults?>(null) }
    var failed by remember(jobId) { mutableStateOf(false) }
    LaunchedEffect(jobId) {
        runCatching { withContext(Dispatchers.IO) { fetchVideoResults(app.api, jobId) } }
            .onSuccess { data = it }
            .onFailure { if (it is CancellationException) throw it else failed = true }
    }
    val note = @Composable { text: String ->
        Text(text, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
    }
    val r = data
    when {
        failed -> note(KEPT_FAILED)
        r == null -> note(KEPT_LOADING)
        else -> {
            val kept = videoKeptView(r)
            Column(Modifier.padding(top = 2.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (kept.noSpeech) {
                    note(KEPT_NO_SPEECH)
                } else {
                    KeptHead(KEPT_SAID)
                    kept.said.forEach { KeptRow(it) }
                }
                if (kept.frames.isNotEmpty()) {
                    KeptHead(KEPT_FRAMES)
                    kept.frames.forEach { KeptRow(it) }
                    kept.framesNote?.let { note(it) }
                }
                note(kept.deleted)
            }
        }
    }
}

@Composable
private fun KeptHead(text: String) {
    Text(text, style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fg, modifier = Modifier.padding(top = 4.dp))
}

@Composable
private fun KeptRow(line: KeptLine) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(line.at, style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily), color = Domovoi.colors.fgMuted)
        Text(line.text, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg)
    }
}
