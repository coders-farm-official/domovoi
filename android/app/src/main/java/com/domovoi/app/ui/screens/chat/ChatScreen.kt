package com.domovoi.app.ui.screens.chat

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.core.content.ContextCompat
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.Flag
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.AnnotatedString
import java.time.Instant
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.gestures.ScrollableDefaults
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.overscroll
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.nestedscroll.NestedScrollConnection
import androidx.compose.ui.input.nestedscroll.NestedScrollSource
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.unit.Velocity
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.interaction.DragInteraction
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.outlined.AttachFile
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.domovoi.app.AppContainer
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.shell.keyboardCrowdsTheWindow
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import com.domovoi.app.ui.components.MarkdownText
import com.domovoi.app.ui.components.composeEmail
import com.domovoi.app.ui.components.parseInstant
import com.domovoi.app.ui.components.shareText
import com.domovoi.app.assistant.ReplyReport
import com.domovoi.app.assistant.ReportOutcome
import com.domovoi.app.assistant.ReportSinks
import com.domovoi.app.ui.screens.assistant.ACTION_REPORT
import com.domovoi.app.ui.screens.assistant.AssistantActions
import com.domovoi.app.ui.screens.assistant.ReportPane
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.takeWhile
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

// ---------------------------------------------------------------------------
// Models — /api/chat rows (web/static/chat.jsx).
// ---------------------------------------------------------------------------

@Serializable
internal data class ThreadRow(
    val id: Long = 0,
    val title: String? = null,
    val updated_at: String? = null,
    val message_count: Int = 0,
    val last_snippet: String? = null,
)

@Serializable
internal data class ThreadList(val threads: List<ThreadRow> = emptyList())

@Serializable
internal data class ImageRef(val token: String = "", val name: String = "")

@Serializable
internal data class MessageRow(
    val id: Long = 0,
    val role: String = "",
    val content: String = "",
    val images: List<ImageRef>? = null,
    val model: String? = null,
    val error: String? = null,
    val created_at: String? = null,
    val stats: ChatStats? = null,
    val device_id: String? = null,
    val device_name: String? = null,
    /** "typed", or "voice" for both rows of a turn said into the chat mic (V025). */
    val source: String? = null,
    /** Who voice identification heard (a voice user row); null when nobody. */
    val speaker_person_id: Long? = null,
    val speaker_name: String? = null,
    val speaker_confidence: Double? = null,
    /** The match was clear enough to draw the speaker marker at full strength. */
    val speaker_confident: Boolean = false,
    /** Documents attached with a user row (documents plan, D1). */
    val documents: List<ServerDocument>? = null,
    /** How a reply answered about the chat's documents: sources and notes. */
    val doc: ServerDocReply? = null,
)

@Serializable
private data class MessageList(
    val messages: List<MessageRow> = emptyList(),
    /** Long-document jobs (documents plan D2): running ones show their
     *  progress, a stopped one lets its reply carry on. */
    val jobs: List<ServerJobView> = emptyList(),
)

/** Documents per message the backend accepts (documents plan D1). */
internal const val MAX_CHAT_DOCUMENTS = 4

internal const val CHAT_DOC_CAP_TOAST = "up to $MAX_CHAT_DOCUMENTS documents per message"

/** Images per message the backend accepts (web/static/chat.jsx: "up to 4 images per message"). */
internal const val MAX_CHAT_IMAGES = 4

internal const val CHAT_IMAGE_CAP_TOAST = "up to $MAX_CHAT_IMAGES images per message"

/**
 * How a picker result fits beside the images already attached (F-A005):
 * `accepted` is how many of the [picked] URIs to upload, `refused` is true
 * when at least one was dropped for the cap — the caller must say so.
 */
internal data class AttachBudget(val accepted: Int, val refused: Boolean)

internal fun attachBudget(attached: Int, picked: Int, cap: Int = MAX_CHAT_IMAGES): AttachBudget {
    val room = (cap - attached).coerceAtLeast(0)
    return AttachBudget(accepted = minOf(picked, room), refused = picked > room)
}

/** Mutable transcript entry (the streaming assistant bubble updates live). */
/**
 * The server's kept row for a reply that streamed into [this]: its id,
 * time, model, figures, error and sources, and its TEXT. The kept text
 * replaces what streamed: for a reply about documents the server checks
 * the model's words first (citations of passages it never gave are taken
 * out, "couldn't find" is said exactly), so the row, not the stream, is
 * what the reply says.
 */
internal fun LiveMessage.finishWith(row: MessageRow) {
    id = row.id.takeIf { it > 0 }
    createdAt = row.created_at
    model = row.model ?: model
    stats = row.stats
    row.error?.let { error = it }
    row.stats?.error_code?.let { errorCode = it }
    doc = row.doc
    if (row.error == null) content = row.content
}

internal class LiveMessage(
    val role: String,
    content: String,
    val images: List<ImageRef> = emptyList(),
    model: String? = null,
    error: String? = null,
    pending: Boolean = false,
    id: Long? = null,
    createdAt: String? = null,
    stats: ChatStats? = null,
    device: String? = null,
    errorCode: String? = null,
    /** "voice" for a turn said into the chat mic; null/"typed" otherwise. */
    val source: String? = null,
    speakerPersonId: Long? = null,
    speakerName: String? = null,
    speakerConfident: Boolean = false,
    /** Documents attached with a user message. */
    val documents: List<ServerDocument> = emptyList(),
    doc: ServerDocReply? = null,
) {
    var content by mutableStateOf(content)
    /** A reply's sources and notes about the chat's documents. */
    var doc by mutableStateOf(doc)
    /** A long document read in parts for this pending reply: how far. */
    var job by mutableStateOf<ServerJobView?>(null)
    var error by mutableStateOf(error)
    /** The server's code for [error] (`stats.error_code`, or the live
     *  stream's `error` event); null for an error this phone made. */
    var errorCode by mutableStateOf(errorCode)
    var pending by mutableStateOf(pending)
    var model by mutableStateOf(model)
    var id by mutableStateOf(id)
    var createdAt by mutableStateOf(createdAt)
    var stats by mutableStateOf(stats)
    var device by mutableStateOf(device)
    var speakerPersonId by mutableStateOf(speakerPersonId)
    var speakerName by mutableStateOf(speakerName)
    var speakerConfident by mutableStateOf(speakerConfident)
    /** Measured on this phone while a reply streams (details dialog only). */
    var sentAtMs: Long? = null
    var firstWordsAtMs: Long? = null
    var doneAtMs: Long? = null

    fun facts(threadId: Long) = MessageFacts(
        id = id, threadId = threadId, role = role, content = content,
        createdAt = createdAt, model = model, error = error,
        imageNames = images.map { it.name },
        stats = stats,
        device = device,
        source = source,
        speaker = speakerPersonId?.let { speakerName ?: "person #$it" },
        firstWordsMs = sentAtMs?.let { s -> firstWordsAtMs?.let { it - s } },
        totalMs = sentAtMs?.let { s -> doneAtMs?.let { it - s } },
    )
}

// ---------------------------------------------------------------------------
// SSE send — OkHttp streaming read of the reply.
// ---------------------------------------------------------------------------

private suspend fun sendStreaming(
    app: AppContainer,
    threadId: Long,
    content: String,
    images: List<ImageRef>,
    documents: List<ServerDocument>,
    onDelta: (String) -> Unit,
    /** (detail, code, model): the server's plain sentence and its code. */
    onError: (String, String?, String?) -> Unit,
    onDone: (MessageRow) -> Unit = {},
    /** A long document's job (documents plan D2): its `job` and `progress` views. */
    onJob: (ServerJobView) -> Unit = {},
) = withContext(Dispatchers.IO) {
    val body = buildJsonObject {
        put("content", content)
        // Which install sent it, for the message's details (V017).
        put("device_id", app.prefs.deviceId)
        put("images", buildJsonArray {
            images.forEach { img ->
                add(buildJsonObject { put("token", img.token); put("name", img.name) })
            }
        })
        if (documents.isNotEmpty()) {
            put("documents", buildJsonArray { documents.forEach { d -> add(buildJsonObject { put("token", d.token) }) } })
        }
    }.toString().toRequestBody("application/json".toMediaType())
    val req = Request.Builder()
        .url(app.api.absolute("/api/chat/threads/$threadId/messages"))
        // SSE, so it builds its own call instead of using ApiClient.raw()
        // — and sends the same preflight-forcing header (WEB-6).
        .header("X-Requested-With", "DomovoiApp")
        .post(body)
        .build()
    app.api.http.newCall(req).execute().use { resp ->
        if (!resp.isSuccessful) {
            onError("${resp.code} ${resp.message}", null, null)
            return@use
        }
        val source = resp.body?.source() ?: return@use
        var event = "message"
        val data = StringBuilder()
        while (true) {
            val line = source.readUtf8Line() ?: break
            when {
                line.startsWith("event: ") -> event = line.removePrefix("event: ").trim()
                line.startsWith("data: ") -> data.append(line.removePrefix("data: "))
                line.isEmpty() && data.isNotEmpty() -> {
                    runCatching {
                        val payload = Json.parseToJsonElement(data.toString()).jsonObject
                        when (event) {
                            "delta" -> payload["text"]?.jsonPrimitive?.content?.let {
                                withContext(Dispatchers.Main) { onDelta(it) }
                            }
                            "error" -> payload["detail"]?.jsonPrimitive?.content?.let { detail ->
                                val code = payload["code"]?.jsonPrimitive?.contentOrNull
                                val model = payload["model"]?.jsonPrimitive?.contentOrNull
                                withContext(Dispatchers.Main) { onError(detail, code, model) }
                            }
                            // The persisted row: its id, timestamp and model.
                            "done" -> payload.decode<MessageRow>().let {
                                withContext(Dispatchers.Main) { onDone(it) }
                            }
                            "job", "progress" -> payload.decode<ServerJobView>().let {
                                withContext(Dispatchers.Main) { onJob(it) }
                            }
                        }
                    }
                    event = "message"
                    data.setLength(0)
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// A home-server chat's conversation (Chat's server place, ServerChatPlace.kt).
// Chat's history list (ChatHome.kt) lists these threads with the phone's own
// chats; this is the conversation, with the server's voice and photos.
// ---------------------------------------------------------------------------

/**
 * One home-server chat. [thread] null is a new chat: nothing is made on the
 * server until the first message (or the first spoken turn) needs a thread,
 * so a new chat moved to the phone with the picker, or left empty, leaves no
 * empty thread in the household's list. [onCreated] hears of the thread made.
 */
@Composable
internal fun ConversationPane(thread: ThreadRow?, frame: ChatFrame, onCreated: (ThreadRow) -> Unit = {}) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    // The thread this chat writes to: the one opened, or the one the first
    // message made.
    var current by remember { mutableStateOf(thread) }
    suspend fun ensureThread(): ThreadRow = current
        ?: app.api.post("/api/chat/threads").decode<ThreadRow>().also {
            current = it
            onCreated(it)
        }

    val transcript = remember { mutableStateListOf<LiveMessage>() }
    var draft by remember { mutableStateOf("") }
    val attachments = remember { mutableStateListOf<ImageRef>() }
    // Documents for the next message (documents plan, D1): uploaded and
    // read on the home server as they are picked.
    val docAttachments = remember { mutableStateListOf<ServerDocument>() }
    var docUploading by remember { mutableStateOf<String?>(null) }
    var attachMenu by remember { mutableStateOf(false) }
    var openSource by remember { mutableStateOf<SourceView?>(null) }
    var sending by remember { mutableStateOf(false) }
    // What's installed: when the vision model isn't, the paperclip explains
    // that instead of opening the picker (web chat.jsx parity).
    val models = rememberApi("chat-models") {
        it.api.get("/api/chat/models").decode<ChatModelsInfo>()
    }
    var pictureNote by remember { mutableStateOf(false) }
    // Why a picked picture didn't reach the chat (photoFailureText), until
    // it is closed or the next pick.
    var uploadNote by remember { mutableStateOf<String?>(null) }
    // The hold-to-talk view (VoiceView.kt), over the conversation. It talks
    // to a thread, so a new chat makes its thread first.
    var voiceOpen by remember { mutableStateOf(false) }
    val startVoice: () -> Unit = {
        if (current != null) {
            voiceOpen = true
            frame.onSent()
        } else {
            scope.launch {
                runCatching { ensureThread() }
                    .onSuccess { voiceOpen = true; frame.onSent() }
                    .onFailure { toast(failureText("new chat", it)) }
            }
        }
    }
    val micPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) startVoice() else toast("the microphone permission is needed to talk to the domovoi")
    }
    val openVoice: () -> Unit = {
        val has = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (has) startVoice() else micPermission.launch(Manifest.permission.RECORD_AUDIO)
    }
    // "report this reply" (Play's AI-generated content policy covers the
    // server's replies too): the report sheet, over the conversation.
    var reporting by remember { mutableStateOf<ReplyReport?>(null) }

    // A thread opened from the list: its messages. A new chat has none.
    // The thread's long-document jobs (documents plan D2).
    val jobs = remember { mutableStateListOf<ServerJobView>() }
    var reloads by remember { mutableStateOf(0) }
    LaunchedEffect(thread?.id, reloads) {
        if (thread == null) return@LaunchedEffect
        runCatching {
            app.api.get("/api/chat/threads/${thread.id}/messages").decode<MessageList>()
        }.onSuccess { list ->
            val rows = list.messages
            jobs.clear()
            jobs.addAll(list.jobs)
            transcript.clear()
            rows.forEach {
                transcript.add(
                    LiveMessage(
                        it.role, it.content, it.images.orEmpty(), it.model, it.error,
                        id = it.id.takeIf { id -> id > 0 }, createdAt = it.created_at,
                        stats = it.stats,
                        errorCode = it.stats?.error_code,
                        device = when {
                            it.device_id != null && it.device_id == app.prefs.deviceId -> "this phone"
                            else -> it.device_name ?: it.device_id
                        },
                        source = it.source,
                        speakerPersonId = it.speaker_person_id,
                        speakerName = it.speaker_name,
                        speakerConfident = it.speaker_confident,
                        documents = it.documents.orEmpty(),
                        doc = it.doc,
                    ),
                )
            }
        }
    }
    // Keep the newest message pinned to the bottom. Two triggers, because the
    // list loses its anchor for two different reasons:
    //
    //  * the transcript GREW — animate, so the arrival reads as movement;
    //  * the VIEWPORT SHRANK — the shell hands this body the space above the
    //    keyboard, so opening the keyboard shortens the list without touching
    //    the transcript. LazyColumn keeps its first-visible-item anchor, and
    //    the tail (with 16 messages: 15 and 16 entirely, 14 down to 5px) slides
    //    out of the bottom and stays there.
    //
    // Keying the second case on a keyboard-up BOOLEAN does not work, and that
    // is the bug this replaces: WindowInsets.ime goes non-zero at the START of
    // the ~250ms IME animation, so the scroll ran while the body was still
    // full height and nothing re-ran after the shrink. viewportEndOffset is
    // the settled fact — it changes once per animation frame and the LAST
    // change is the one that re-pins.
    //
    // But re-pinning on EVERY viewport change throws away the reader's place:
    // scroll back through a thread to re-read something, tap the composer, and
    // the list yanks to the newest message; closing the keyboard yanked it
    // again, because growth emits the same way a shrink does. So two guards,
    // and they need each other:
    //
    //  * SHRINK ONLY. `end < lastEnd` is the keyboard taking space away. A
    //    growth (the keyboard leaving, a rotation) restores space the list can
    //    render into on its own and must not move the reader.
    //  * ONLY IF THEY WERE ALREADY AT THE NEWEST MESSAGE — decided BEFORE the
    //    shrink, never after. The tail slides out of view as part of the
    //    shrink, so asking "is the last item visible?" once the viewport has
    //    already changed always answers no and would make the pin inert. The
    //    emissions where viewportEndOffset did NOT change are the reader's own
    //    scrolling; those, and only those, update the flag.
    LaunchedEffect(listState) {
        var lastEnd = listState.layoutInfo.viewportEndOffset
        var readerAtNewest = true
        snapshotFlow {
            val info = listState.layoutInfo
            info.viewportEndOffset to (info.visibleItemsInfo.lastOrNull()?.index ?: -1)
        }
            .distinctUntilChanged()
            .collect { (end, lastVisible) ->
                if (end == lastEnd) {
                    if (lastVisible >= 0) readerAtNewest = lastVisible >= transcript.size - 1
                    return@collect
                }
                val shrank = end < lastEnd
                lastEnd = end
                if (shrank && readerAtNewest && transcript.isNotEmpty()) {
                    listState.scrollToItem(transcript.size - 1)
                }
            }
    }
    LaunchedEffect(transcript.size) {
        if (transcript.isNotEmpty()) listState.animateScrollToItem(transcript.size - 1)
    }
    // A job running in this chat that this screen didn't start (Chat was
    // left and opened again, another device asked): its progress every two
    // seconds until it ends, then the thread again (its reply is in).
    val liveIds = jobs.filter { it.live }.joinToString(",") { it.id }
    LaunchedEffect(liveIds, sending) {
        if (liveIds.isEmpty() || sending) return@LaunchedEffect
        while (true) {
            kotlinx.coroutines.delay(2_000)
            var ended = false
            jobs.filter { it.live }.forEach { j ->
                val v = runCatching { fetchJob(app, j.id) }.getOrNull()
                if (v == null || !v.live) ended = true
                else jobs.indexOfFirst { it.id == v.id }.takeIf { it >= 0 }?.let { jobs[it] = v }
            }
            if (ended) {
                reloads++
                return@LaunchedEffect
            }
        }
    }
    // Follow a streaming reply down as it grows — the size trigger above only
    // fires when a message is ADDED, and the reply grows inside one item.
    // A reader who drags up to re-read something is left alone; landing back
    // at the bottom (or sending) picks the follow up again.
    var following by remember { mutableStateOf(true) }
    LaunchedEffect(listState) {
        launch {
            listState.interactionSource.interactions.collect {
                if (it is DragInteraction.Start) following = false
            }
        }
        snapshotFlow { listState.isScrollInProgress }
            .filter { !it }
            .collect { if (!listState.canScrollForward) following = true }
    }
    val tail = transcript.lastOrNull()
    LaunchedEffect(tail) {
        if (tail == null || tail.role == "user" || !tail.pending) return@LaunchedEffect
        snapshotFlow { Triple(tail.content.length, tail.error, tail.pending) }
            .takeWhile { (_, _, pending) -> pending }
            .collect { if (following) listState.scrollBy(FOLLOW_STEP_PX) }
        // Done. The stamp line (time and model) appears under the reply only
        // now, a layout after `pending` drops, and nothing above followed it:
        // a reply taller than the list ended with that line under the fold
        // until the reader scrolled (2026-10-12, emulator, a streamed
        // three-screen reply). Follow it in over the next few frames.
        repeat(FINISH_FOLLOW_FRAMES) {
            withFrameNanos { }
            if (following && listState.canScrollForward) listState.scrollBy(FOLLOW_STEP_PX)
        }
    }

    val picker = rememberLauncherForActivityResult(
        ActivityResultContracts.GetMultipleContents(),
    ) { uris ->
        // F-A005: refuse the overflow out loud instead of dropping it in silence.
        val budget = attachBudget(attached = attachments.size, picked = uris.size)
        if (budget.refused) toast(CHAT_IMAGE_CAP_TOAST)
        uris.take(budget.accepted).forEach { uri ->
            scope.launch(Dispatchers.IO) {
                runCatching {
                    val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                        ?: return@launch
                    // By its bytes, not the gallery's type: a HEIC photo (and
                    // anything else the server can't read, or one over its
                    // limits) goes as a JPEG made here (ChatPhotoPlan.kt).
                    val photo = preparePhoto(bytes)
                    val form = MultipartBody.Builder().setType(MultipartBody.FORM)
                        .addFormDataPart(
                            "file", photo.name,
                            photo.bytes.toRequestBody(photo.mime.toMediaType()),
                        )
                        .build()
                    val up = app.api.upload("/api/chat/uploads", form).decode<ImageRef>()
                    withContext(Dispatchers.Main) {
                        // Uploads run in parallel: two picker rounds can race past the cap.
                        if (attachments.size < MAX_CHAT_IMAGES) attachments.add(up) else toast(CHAT_IMAGE_CAP_TOAST)
                    }
                }.onFailure { e ->
                    // Why, in words, where the photo would have been (a toast
                    // is gone before a sentence like this is read).
                    val text = photoFailureText(e, Build.VERSION.SDK_INT)
                    withContext(Dispatchers.Main) { uploadNote = text }
                }
            }
        }
    }

    val docPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        val name = pickedName(context, uri)
        com.domovoi.app.assistant.docs.DocExtract.refusalFor(name)?.let { uploadNote = it; return@rememberLauncherForActivityResult }
        if (docAttachments.size >= MAX_CHAT_DOCUMENTS) { toast(CHAT_DOC_CAP_TOAST); return@rememberLauncherForActivityResult }
        docUploading = name
        uploadNote = null
        scope.launch {
            runCatching {
                val tmp = withContext(Dispatchers.IO) {
                    val f = java.io.File(context.cacheDir, "chat-docs/upload-${System.nanoTime()}")
                    f.parentFile?.mkdirs()
                    context.contentResolver.openInputStream(uri)?.use { input -> f.outputStream().use { input.copyTo(it) } }
                        ?: throw java.io.IOException("can't read $name")
                    f
                }
                try {
                    uploadServerDocument(app, tmp, name)
                } finally {
                    withContext(Dispatchers.IO) { tmp.delete() }
                }
            }.onSuccess { docAttachments.add(it) }
                .onFailure { uploadNote = docFailureText("attach", it) }
            docUploading = null
        }
    }

    // The report for the reply at [i], or null when it is not a finished
    // reply with words (ReplyReport.draftServer).
    fun reportableAt(i: Int): ReplyReport? {
        val m = transcript.getOrNull(i) ?: return null
        if (m.role == "user" || m.pending) return null
        val asked = transcript.subList(0, i).lastOrNull { it.role == "user" }
        val now = System.currentTimeMillis()
        return ReplyReport.draftServer(
            question = asked?.content,
            spoken = asked?.source == "voice",
            answer = m.content,
            answeredAt = parseInstant(m.createdAt)?.toEpochMilli() ?: now,
            model = m.model,
            appVersion = appVersion(context),
            now = now,
        )
    }

    fun send() {
        val content = draft.trim()
        if ((content.isBlank() && docAttachments.isEmpty()) || sending || docUploading != null) return
        val images = attachments.toList()
        val docs = docAttachments.toList()
        draft = ""
        attachments.clear()
        docAttachments.clear()
        sending = true
        following = true
        frame.onSent()
        // Stamped now; the server's own row (with its id) is not sent back
        // for the user turn, and the two clocks agree to the second on a LAN.
        transcript.add(
            LiveMessage("user", content, images, createdAt = Instant.now().toString(), device = "this phone", documents = docs),
        )
        val live = LiveMessage("assistant", "", pending = true)
        live.sentAtMs = System.currentTimeMillis()
        transcript.add(live)
        scope.launch {
            runCatching {
                val target = ensureThread()
                sendStreaming(
                    app, target.id, content, images, docs,
                    onDelta = {
                        if (live.firstWordsAtMs == null) live.firstWordsAtMs = System.currentTimeMillis()
                        live.content += it
                    },
                    onError = { detail, code, model ->
                        live.error = detail
                        live.errorCode = code
                        if (model != null) live.model = model
                    },
                    onDone = { row -> live.finishWith(row) },
                    onJob = { live.job = it },
                )
            }.onFailure { live.error = failureText("send", it) }
            // Refused for want of the vision model: re-read what's installed,
            // so the paperclip explains it next time.
            if (live.errorCode == "vision_model_missing") models.refresh()
            live.doneAtMs = System.currentTimeMillis()
            if (live.createdAt == null) live.createdAt = Instant.now().toString()
            live.pending = false
            sending = false
            // A long summary that was stopped can carry on: the jobs again.
            if (live.job != null) reloads++
        }
    }

    val open = current
    if (voiceOpen && open != null) {
        VoiceView(open.id, transcript, onClose = { voiceOpen = false })
        return
    }
    reporting?.let { draftReport ->
        ReportPane(draftReport, serverReportActions(app.scope), canSave = false) { reporting = null }
        return
    }
    val threadId = open?.id ?: 0L

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        ChatPaneGutter()
        ThreadTitleRow(open?.title ?: NEW_CHAT, frame)

        Box(Modifier.weight(1f).fillMaxWidth().then(rememberFitStretch(listState))) {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                // A stopped summary: its newest reply offers "carry on".
                val stopped = jobs.filter { it.state == "stopped" || it.state == "failed" }
                val carryAt = stopped.associate { j -> j.id to transcript.indexOfLast { it.doc?.job?.id == j.id } }
                itemsIndexed(transcript) { i, m ->
                    // The draft is made at the tap, from the transcript then.
                    val stoppedJob = m.doc?.job?.id?.let { id -> stopped.firstOrNull { it.id == id && carryAt[id] == i } }
                    MessageBubble(
                        m, threadId, onReport = if (canReport(m)) ({ reporting = reportableAt(i) }) else null,
                        onSource = { openSource = it },
                        carryOnLeft = stoppedJob?.let { it.parts - it.read }.takeIf { !sending },
                        onCarryOn = {
                            stoppedJob?.let { j ->
                                scope.launch {
                                    runCatching { resumeServerJob(app, j.id) }
                                        .onSuccess { v -> jobs.indexOfFirst { it.id == v.id }.takeIf { it >= 0 }?.let { jobs[it] = v } }
                                        .onFailure { toast(docFailureText("carry on", it)) }
                                }
                            }
                        },
                    )
                }
                // A job this screen didn't start, while it runs.
                if (!sending) {
                    items(jobs.filter { it.live }, key = { "job-" + it.id }) { j ->
                        DocJobCard(j.line, j.read, j.parts, onStop = {
                            scope.launch {
                                runCatching { stopServerJob(app, j.id) }.onFailure { toast(docFailureText("stop", it)) }
                                reloads++
                            }
                        })
                    }
                }
            }
        }

        if (attachments.isNotEmpty()) {
            LazyRow(
                Modifier.padding(vertical = 6.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                items(attachments, key = { it.token }) { a ->
                    Box {
                        AsyncImage(
                            model = app.api.absolute("/api/chat/uploads/${a.token}"),
                            contentDescription = a.name,
                            contentScale = ContentScale.Crop,
                            modifier = Modifier.size(56.dp)
                                .clip(RoundedCornerShape(8.dp))
                                .border(1.dp, Domovoi.colors.border, RoundedCornerShape(8.dp)),
                        )
                        IconButton(
                            onClick = { attachments.remove(a) },
                            modifier = Modifier.size(20.dp).align(Alignment.TopEnd),
                        ) {
                            Icon(
                                Icons.Outlined.Close, contentDescription = "remove",
                                tint = Domovoi.colors.fg,
                            )
                        }
                    }
                }
            }
        }

        val visionMissing = visionMissing(models.data)
        if (visionMissing && (pictureNote || attachments.isNotEmpty())) {
            PictureNote(models.data?.vision_model, onClose = { pictureNote = false })
        }
        uploadNote?.let { UploadNote(it, onClose = { uploadNote = null }) }
        docUploading?.let { ReadingLine(it) }
        if (docAttachments.isNotEmpty()) {
            Column(Modifier.padding(top = 6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                docAttachments.forEach { d ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        DocMarker(d.summary.ifEmpty { d.name }, d.notes.map { it.text }, Modifier.weight(1f, fill = false))
                        IconButton(onClick = { docAttachments.remove(d) }) {
                            Icon(Icons.Outlined.Close, contentDescription = "remove ${d.name}", tint = Domovoi.colors.fgMuted)
                        }
                    }
                }
            }
        }
        openSource?.let { SourceSheet(it, onClose = { openSource = null }) }
        // A new chat with nothing said yet: the place can still change.
        if (open == null && transcript.isEmpty() && !sending) frame.picker?.invoke()

        Row(
            Modifier.fillMaxWidth().padding(top = 6.dp),
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            val action = attachAction(models.data, attachments.size)
            // The paperclip: a picture (the vision model reads it) or a
            // document (read on the home server, documents plan D1).
            Box {
                IconButton(onClick = { attachMenu = true }) {
                    Icon(Icons.Outlined.AttachFile, contentDescription = "attach", tint = Domovoi.colors.fgMuted)
                }
                DropdownMenu(expanded = attachMenu, onDismissRequest = { attachMenu = false }, containerColor = Domovoi.colors.raised) {
                    DropdownMenuItem(
                        text = {
                            Text(
                                if (visionMissing) "a picture (the vision model isn't installed)" else "a picture",
                                color = if (action == AttachAction.Pick) Domovoi.colors.fg else Domovoi.colors.fgMuted,
                            )
                        },
                        onClick = {
                            attachMenu = false
                            when (action) {
                                // Explain, and read again in case an admin has since
                                // installed it (the note goes once the read says so).
                                AttachAction.ExplainVision -> { pictureNote = true; models.refresh() }
                                AttachAction.AtCap -> toast(CHAT_IMAGE_CAP_TOAST)
                                AttachAction.Pick -> { pictureNote = false; uploadNote = null; picker.launch("image/*") }
                            }
                        },
                    )
                    DropdownMenuItem(
                        text = { Text("a document (PDF, Word, sheet, text)") },
                        enabled = docUploading == null,
                        onClick = {
                            attachMenu = false
                            docPicker.launch(com.domovoi.app.assistant.docs.DocExtract.PICKER_TYPES + "*/*")
                        },
                    )
                }
            }
            OutlinedTextField(
                value = draft, onValueChange = { draft = it },
                placeholder = { Text(COMPOSER_HINT) },
                modifier = Modifier.weight(1f),
                maxLines = 4,
            )
            // Nothing typed: the mic, which opens the hold-to-talk view.
            if (draft.isBlank() && attachments.isEmpty() && docAttachments.isEmpty()) {
                IconButton(onClick = openVoice, enabled = !sending) {
                    Icon(
                        Icons.Outlined.Mic, contentDescription = "talk",
                        tint = if (!sending) Domovoi.colors.brand else Domovoi.colors.fgFaint,
                    )
                }
            } else {
                val canSend = (draft.isNotBlank() || docAttachments.isNotEmpty()) && !sending && docUploading == null
                IconButton(onClick = { send() }, enabled = canSend) {
                    Icon(
                        Icons.AutoMirrored.Filled.Send, contentDescription = "send",
                        tint = if (canSend) Domovoi.colors.brand else Domovoi.colors.fgFaint,
                    )
                }
            }
        }
        ChatPaneGutter()
    }
}

/** True when a drag on the conversation gets [rememberFitStretch]'s stretch:
 *  only while the list can scroll neither way. A list that can scroll draws
 *  its own stretch at its ends and leaves nothing over for a second one. */
internal fun fitStretches(canScrollBackward: Boolean, canScrollForward: Boolean): Boolean =
    !canScrollBackward && !canScrollForward

/**
 * A conversation shorter than its pane still answers a drag.
 *
 * Compose foundation 1.7 draws no overscroll on a list that cannot scroll:
 * ScrollingLogic dispatches it only when canScrollForward or
 * canScrollBackward. So a thread that fits on screen (one picture and one
 * multi-paragraph reply on a 1080x2520 phone) gave a drag no response at all.
 * On the emulator a held 600px drag screenshots pixel-identical to the still
 * frame, where a list that can scroll stretches. That reads as a locked
 * screen: Kamron's "my scroll seems to be locked" (2026-10-12). Opening the
 * keyboard shortens the pane until the same thread overflows by ~400px, which
 * was his "when I click into the chat I can scroll a little bit". Nothing was
 * locked; there was nothing to scroll and nothing said so.
 *
 * The list still hands an unusable drag to its nested-scroll parents, so this
 * connection, on the box around the list, feeds what it leaves over to a
 * stretch of the platform's own kind, and lets it spring back on release,
 * while [fitStretches] says the list cannot scroll itself.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun rememberFitStretch(listState: LazyListState): Modifier {
    val effect = ScrollableDefaults.overscrollEffect()
    return remember(listState, effect) {
        val fits = { fitStretches(listState.canScrollBackward, listState.canScrollForward) }
        val connection = object : NestedScrollConnection {
            override fun onPostScroll(consumed: Offset, available: Offset, source: NestedScrollSource): Offset {
                if (!fits()) return Offset.Zero
                // Nothing scrolls here: the effect pulls (or relaxes) its stretch.
                return effect.applyToScroll(available, source) { Offset.Zero }
            }

            override suspend fun onPostFling(consumed: Velocity, available: Velocity): Velocity {
                if (!fits() && !effect.isInProgress) return Velocity.Zero
                effect.applyToFling(available) { Velocity.Zero }
                return available
            }
        }
        Modifier.nestedScroll(connection).overscroll(effect)
    }
}

/**
 * The thread's own title row — and nothing at all in a window the keyboard
 * has left too short for it.
 *
 * Measured on a landscape phone (1080px tall, IME top 394, so the shell hands
 * this pane 320px): this row is 126px, its spacer 21px and the composer row
 * 163px, against 236px of usable height once the pane's own 16dp gutters are
 * paid. Column measures its non-weighted children in order with whatever
 * main-axis space is left, so the composer — last, and not weighted — was
 * handed 89px, below an OutlinedTextField's own minimum. The box still drew
 * its outline at [793,253][2216,379] and uiautomator still reported the typed
 * text, but the inner text field had no room to render it: you typed BLIND
 * into a field that looked like it was working. The message list got 0px.
 *
 * Dropping this row hands those 147px to the composer, which then measures at
 * its natural height and draws the text, and leaves the list a real band.
 * Nothing is stranded: ChatScreen installs a BackHandler for the same
 * `onBack`, so system back still leaves the thread, and the row returns the
 * instant the keyboard closes. In portrait (578dp left) it never goes.
 *
 * Its own composable so the `keyboardCrowdsTheWindow()` read — snapshot state
 * that changes on every frame of the IME animation — sits in a leaf rather
 * than invalidating the pane, composer and caret included.
 */
@Composable
private fun ThreadTitleRow(title: String, frame: ChatFrame) {
    if (keyboardCrowdsTheWindow()) return
    // Chat's header in every place (ChatHome.kt): back, the title, the chip.
    ChatHeader(title, frame.chip, ChatPlaceKind.Server, frame.onBack)
}

/**
 * The pane's vertical breathing room: 16dp, or 2dp in a crowded window.
 *
 * The pane used to pay this as `padding(16.dp)` on its Column. That is 84px of
 * a 320px landscape window spent on whitespace around a composer that had no
 * room to draw. It is a leaf for the same reason [ThreadTitleRow] is: the
 * inset read must not invalidate the caret's own node. Horizontal padding
 * stays on the Column — the window is 2400px wide, width was never scarce.
 */
@Composable
private fun ChatPaneGutter() {
    Spacer(Modifier.height(if (keyboardCrowdsTheWindow()) 2.dp else 16.dp))
}

/** The report sheet's actions for a home-server reply: no save (saved
 *  reports live under the phone model's settings), only the sinks that hand
 *  it on (the owner's destination, the share menu). */
@Composable
private fun serverReportActions(scope: CoroutineScope): AssistantActions {
    val context = LocalContext.current
    val app = LocalApp.current
    val toast = LocalToast.current
    val sinks = remember(context) {
        ReportSinks.available(
            share = { subject, text -> shareText(context, subject, text) },
            email = { to, subject, body -> composeEmail(context, to, subject, body) },
            upload = { url, json -> app.reportUpload.post(url, json) },
        )
    }
    return AssistantActions(
        reportSinks = sinks,
        deliverReport = { sink, report ->
            scope.launch { serverReportToast(sink.deliver(report))?.let(toast) }
        },
    )
}

/** A home-server message "report this reply" is offered on: a finished
 *  reply with words. */
internal fun canReport(m: LiveMessage): Boolean = m.role != "user" && !m.pending && m.content.isNotBlank()

/** What the person is told once a home-server reply's report is handed on;
 *  it is not kept on the phone, so nothing says it is. */
internal fun serverReportToast(outcome: ReportOutcome): String? = when (outcome) {
    ReportOutcome.HANDED_ON -> null
    ReportOutcome.SENT -> "report sent to coders farm"
    ReportOutcome.NO_APP -> "no app on this phone can take the report"
    ReportOutcome.FAILED -> "couldn't send the report; try again"
}

/** The app's versionName, for a report. */
private fun appVersion(context: android.content.Context): String =
    runCatching { context.packageManager.getPackageInfo(context.packageName, 0).versionName }.getOrNull().orEmpty()

@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun MessageBubble(
    m: LiveMessage,
    threadId: Long,
    onReport: (() -> Unit)? = null,
    onSource: (SourceView) -> Unit = {},
    /** A stopped summary's parts still to read: "carry on" shows (null: no). */
    carryOnLeft: Int? = null,
    onCarryOn: () -> Unit = {},
) {
    val app = LocalApp.current
    val context = LocalContext.current
    val toast = LocalToast.current
    val clipboard = LocalClipboardManager.current
    val haptics = LocalHapticFeedback.current
    val isUser = m.role == "user"
    var menu by remember { mutableStateOf(false) }
    var details by remember { mutableStateOf(false) }
    val copy = {
        clipboard.setText(AnnotatedString(m.content))
        toast("copied")
    }
    Column(
        Modifier.fillMaxWidth(),
        horizontalAlignment = if (isUser) Alignment.End else Alignment.Start,
    ) {
        if (m.images.isNotEmpty()) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.padding(bottom = 4.dp)) {
                m.images.take(MAX_CHAT_IMAGES).forEach { img ->
                    AsyncImage(
                        model = app.api.absolute("/api/chat/uploads/${img.token}"),
                        contentDescription = img.name,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.size(84.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(8.dp)),
                    )
                }
            }
        }
        // A document attached with this message: its marker line.
        m.documents.forEach { d -> DocMarker(d.summary.ifEmpty { d.name }, d.notes.map { it.text }, Modifier.padding(bottom = 4.dp)) }
        if (isUser && m.content.isBlank() && m.documents.isNotEmpty()) return@Column
        // Long-press: copy / details. The menu anchors to the bubble.
        Box {
            Box(
                Modifier.widthIn(max = 480.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(if (isUser) Domovoi.colors.card else Domovoi.colors.canvas)
                    .then(
                        if (isUser) Modifier.border(1.dp, Domovoi.colors.border, RoundedCornerShape(10.dp))
                        else Modifier,
                    )
                    .combinedClickable(
                        onClickLabel = null,
                        onLongClickLabel = "message actions",
                        onClick = {},
                        onLongClick = {
                            haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                            menu = true
                        },
                    )
                    .padding(horizontal = if (isUser) 12.dp else 0.dp, vertical = if (isUser) 8.dp else 2.dp),
            ) {
                Column {
                    val cursor = if (m.pending) " ▍" else ""
                    if (isUser) {
                        Text(
                            m.content + cursor,
                            style = MaterialTheme.typography.bodyMedium,
                            color = Domovoi.colors.fg,
                        )
                    } else {
                        val job = m.job
                        if (m.pending && job != null && m.content.isEmpty()) {
                            // A long document read in parts: how far, and stop.
                            DocJobCard(job.line.ifEmpty { "reading part ${job.part} of ${job.parts}" }, job.read, job.parts,
                                onStop = if (job.live) ({ app.scope.launch { runCatching { stopServerJob(app, job.id) } } }) else null)
                        } else {
                            MarkdownText(m.content, trailing = cursor)
                        }
                        if (!m.pending) {
                            val sources = serverSources(m.doc) { token -> { serverDocumentFile(app, context.cacheDir, token) } }
                            ReplyDocs(m.doc?.notes.orEmpty().map { it.text }, sources, onSource)
                            carryOnLeft?.let { left -> CarryOnButton(left, onCarryOn) }
                        }
                    }
                    chatErrorView(m.error, m.errorCode, m.model)?.let { err ->
                        if (err.code != null) {
                            ChatErrorNote(err)
                        } else {
                            Text(
                                err.text,
                                style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                                color = Domovoi.colors.err,
                                modifier = Modifier.padding(top = 4.dp),
                            )
                        }
                    }
                }
            }
            DropdownMenu(
                expanded = menu,
                onDismissRequest = { menu = false },
                containerColor = Domovoi.colors.raised,
            ) {
                DropdownMenuItem(
                    text = { Text("copy") },
                    leadingIcon = { Icon(Icons.Outlined.ContentCopy, contentDescription = null) },
                    enabled = m.content.isNotEmpty(),
                    onClick = { menu = false; copy() },
                )
                DropdownMenuItem(
                    text = { Text("details") },
                    leadingIcon = { Icon(Icons.Outlined.Info, contentDescription = null) },
                    onClick = { menu = false; details = true },
                )
                // On every finished reply with words (owner, 2026-10-20).
                if (onReport != null) {
                    DropdownMenuItem(
                        text = { Text(ACTION_REPORT) },
                        leadingIcon = { Icon(Icons.Outlined.Flag, contentDescription = null) },
                        onClick = { menu = false; onReport() },
                    )
                }
            }
        }
        // Who Domovoi heard, on a message said into the chat mic.
        val owners by app.prefs.owners.collectAsState()
        val server by app.prefs.serverUrl.collectAsState()
        val owner = ServerCredentials.ownerFor(owners, server)
        speakerMark(m.source, m.role, m.speakerPersonId, m.speakerName, m.speakerConfident, owner)
            ?.takeIf { !m.pending }
            ?.let { SpeakerMarker(it, Modifier.padding(end = 2.dp)) }
        // Stamp line: when, and (for a reply) which model answered.
        val stamp = chatStamp(m.createdAt)
        val model = m.model.takeIf { !isUser && !m.pending }
        val line = listOfNotNull(stamp, model).joinToString(" · ")
        if (line.isNotEmpty()) {
            Text(
                line,
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                color = Domovoi.colors.fgFaint,
                modifier = Modifier.padding(top = 3.dp, start = if (isUser) 0.dp else 2.dp, end = if (isUser) 2.dp else 0.dp),
            )
        }
    }
    if (details) {
        MessageDetailsDialog(
            facts = m.facts(threadId),
            onCopy = { copy(); details = false },
            onDismiss = { details = false },
        )
    }
}

/** One follow step: more than any reply grows between two deltas, so a
 *  scrollBy of it always lands on the list's end (scrolling clamps there). */
private const val FOLLOW_STEP_PX = 100_000f

/** Frames the follow keeps watching after a reply is done, for its stamp
 *  line to be laid out (about 160ms at 60Hz). */
private const val FINISH_FOLLOW_FRAMES = 10
