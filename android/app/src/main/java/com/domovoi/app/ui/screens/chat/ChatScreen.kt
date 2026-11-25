package com.domovoi.app.ui.screens.chat

import androidx.compose.foundation.clickable
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
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Share
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
    val archived: Boolean = false,
    /** A person named it (V034): the model's title never replaces it. */
    val title_by_user: Boolean = false,
    /** A search hit (`?q=`): the words around the match in a message. */
    val match_snippet: String? = null,
    /** "Allow in this chat" is on (V037): lookups go without asking. */
    val internet_allowed: Boolean = false,
    /** A lookup in it went online (V037): the list's globe. */
    val used_internet: Boolean = false,
    // Private chats (1.5.0, ChatPrivacy.kt): absent from an older server,
    // which then denies nothing and says nothing about sharing.
    /** Shared with the household (false: private to its device, or
     *  admin-only). */
    val shared: Boolean? = null,
    /** This phone owns it. */
    val mine: Boolean? = null,
    val can_write: Boolean? = null,
    val can_share: Boolean? = null,
    val can_delete: Boolean? = null,
    /** Visible only because the caller is an admin. */
    val admin_view: Boolean = false,
    /** The owning device's name, for an admin; else null. */
    val owner_name: String? = null,
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
    /** "Try again" may replace this reply (the chat model's own reply to
     *  a typed message, V034); false for a spoken turn's or the help
     *  desk's, and from a server that has no try again. */
    val retryable: Boolean = false,
    /** Documents attached with a user row (documents plan, D1). */
    val documents: List<ServerDocument>? = null,
    /** How a reply answered about the chat's documents: sources and notes. */
    val doc: ServerDocReply? = null,
    /** A draft reply (documents plan D4): the draft card's draft and version. */
    val draft: ServerDraftRef? = null,
    /** A reply offering to print a file attached in the chat (1.4.1). */
    val print_attachment: PrintAttachmentRef? = null,
)

@Serializable
internal data class MessageList(
    val messages: List<MessageRow> = emptyList(),
    /** Long-document jobs (documents plan D2): running ones show their
     *  progress, a stopped one lets its reply carry on. */
    val jobs: List<ServerJobView> = emptyList(),
    /** The thread's video jobs (1.4.0, ChatVideo.kt), each under the
     *  message it was sent with (`message_id`). */
    val videos: List<ServerVideoJob> = emptyList(),
    /** The thread itself with its access fields (1.5.0); null from an
     *  older server. */
    val thread: ThreadRow? = null,
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
    draft = row.draft
    printAttachment = row.print_attachment
    retryable = row.retryable
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
    retryable: Boolean = false,
    /** Documents attached with a user message. */
    val documents: List<ServerDocument> = emptyList(),
    doc: ServerDocReply? = null,
    draft: ServerDraftRef? = null,
    printAttachment: PrintAttachmentRef? = null,
    /** A video sent with this user message, until the thread is read
     *  back with its job (ChatVideo.kt). */
    val stagedVideo: StagedVideo? = null,
) {
    var content by mutableStateOf(content)
    /** A draft reply's draft (documents plan D4). */
    var draft by mutableStateOf(draft)
    /** A reply offering to print a file attached in the chat (1.4.1). */
    var printAttachment by mutableStateOf(printAttachment)
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
    var retryable by mutableStateOf(retryable)
    /** Measured on this phone while a reply streams (details dialog only). */
    var sentAtMs: Long? = null
    var firstWordsAtMs: Long? = null
    var doneAtMs: Long? = null

    /** A streamed `delta`: more of the reply. The first one marks when the
     *  first words came. */
    fun appendStreamed(text: String, now: Long = System.currentTimeMillis()) {
        if (firstWordsAtMs == null) firstWordsAtMs = now
        content += text
    }

    /** A streamed `replace`: the reply is [text] from now on. An empty one
     *  starts over (a web answer follows as new deltas), so the first words
     *  are the next delta's; a non-empty one is words shown now. */
    fun replaceStreamed(text: String, now: Long = System.currentTimeMillis()) {
        content = text
        firstWordsAtMs = if (text.isEmpty()) null else firstWordsAtMs ?: now
    }

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

/**
 * A message's `SendBody`: the words ([content] may be empty when a document
 * or a video goes with it; words sent with a video are its question), which
 * install sent it (V017), its pictures, documents and video (`videos:
 * [{token}]`, at most one), the draft it revises, and this phone's heads-up
 * switch and silenced kinds (the server's rules decide when a card shows).
 */
internal fun messageSendBody(
    content: String,
    deviceId: String,
    images: List<ImageRef>,
    documents: List<ServerDocument>,
    videos: List<String>,
    reviseDraft: Long?,
    headsUp: kotlinx.serialization.json.JsonObject,
): kotlinx.serialization.json.JsonObject = buildJsonObject {
    put("heads_up", headsUp)
    put("content", content)
    put("device_id", deviceId)
    put("images", buildJsonArray {
        images.forEach { img ->
            add(buildJsonObject { put("token", img.token); put("name", img.name) })
        }
    })
    if (documents.isNotEmpty()) {
        put("documents", buildJsonArray { documents.forEach { d -> add(buildJsonObject { put("token", d.token) }) } })
    }
    if (videos.isNotEmpty()) {
        put("videos", buildJsonArray { videos.take(1).forEach { t -> add(buildJsonObject { put("token", t) }) } })
    }
    if (reviseDraft != null) put("revise_draft", reviseDraft)
}

private suspend fun sendStreaming(
    app: AppContainer,
    threadId: Long,
    content: String,
    images: List<ImageRef>,
    documents: List<ServerDocument>,
    onDelta: (String) -> Unit,
    /** The reply streamed so far becomes exactly this text (`replace`). */
    onReplace: (String) -> Unit,
    /** (detail, code, model): the server's plain sentence and its code. */
    onError: (String, String?, String?) -> Unit,
    onDone: (MessageRow) -> Unit = {},
    /** A long document's job (documents plan D2): its `job` and `progress` views. */
    onJob: (ServerJobView) -> Unit = {},
    /** "Try again" (`POST .../retry`): no body, a new reply to the last
     *  question in place of the last reply. */
    retry: Boolean = false,
    /** A draft card's Revise box: the message revises this draft. */
    reviseDraft: Long? = null,
    /** The answer to a lookup card (`POST .../lookups/<message id>`). */
    decision: Pair<Long, String>? = null,
    /** A pick on a heads-up card (documents plan D3): (card row id, option),
     *  streamed from `POST .../heads-up`. */
    headsUpPick: Pair<Long, String>? = null,
    /** A staged video's token (ChatVideo.kt): at most one a message. */
    videos: List<String> = emptyList(),
) = withContext(Dispatchers.IO) {
    val prefs = app.headsUp.prefs
    val body = (if (headsUpPick != null) buildJsonObject {
        put("message_id", headsUpPick.first)
        put("option", headsUpPick.second)
        put("heads_up", headsUpBody(prefs))
    } else if (decision != null) buildJsonObject {
        put("decision", decision.second)
        put("device_id", app.prefs.deviceId)
    } else if (retry) buildJsonObject {} else {
        messageSendBody(content, app.prefs.deviceId, images, documents, videos, reviseDraft, headsUpBody(prefs))
    }).toString().toRequestBody("application/json".toMediaType())
    val path = when {
        headsUpPick != null -> "/api/chat/threads/$threadId/heads-up"
        decision != null -> "/api/chat/threads/$threadId/lookups/${decision.first}"
        retry -> "/api/chat/threads/$threadId/retry"
        else -> "/api/chat/threads/$threadId/messages"
    }
    val req = Request.Builder()
        .url(app.api.absolute(path))
        // SSE, so it builds its own call instead of using ApiClient.raw()
        // — and sends the same preflight-forcing header (WEB-6).
        .header("X-Requested-With", "DomovoiApp")
        .post(body)
        .build()
    app.api.http.newCall(req).execute().use { resp ->
        if (!resp.isSuccessful) {
            val text = resp.body?.string()
            // Unshared (or deleted) while it was open: the caller says so and
            // closes the chat (ChatPrivacy.kt).
            if (isThreadGone(resp.code, text)) throw ChatGoneException()
            // Plain words for the household's AI features being off (a 409
            // ai-off), not "409 Conflict" or an empty reply.
            val line = sendFailureLine(resp.code, resp.message, resp.header(com.domovoi.app.net.REFUSAL_HEADER), text)
            withContext(Dispatchers.Main) { onError(line, null, null) }
            return@use
        }
        val source = resp.body?.source() ?: return@use
        readChatStream(source) { ev ->
            withContext(Dispatchers.Main) {
                when (ev) {
                    is ChatStreamEvent.Delta -> onDelta(ev.text)
                    is ChatStreamEvent.Replace -> onReplace(ev.text)
                    is ChatStreamEvent.Failed -> onError(ev.detail, ev.code, ev.model)
                    is ChatStreamEvent.Done -> onDone(ev.row)
                    is ChatStreamEvent.Job -> onJob(ev.view)
                }
            }
        }
    }
}

/** One event of a chat answer's stream (`POST .../messages` and the others
 *  sendStreaming reads), as the app reads it. */
internal sealed interface ChatStreamEvent {
    /** More of the reply: appended to what streamed. */
    data class Delta(val text: String) : ChatStreamEvent
    /** The reply is [text] from now on, and later deltas append to it
     *  (1.4.1). Empty: start over, a web answer follows as new deltas;
     *  otherwise the corrected reply. Older servers never send it. */
    data class Replace(val text: String) : ChatStreamEvent
    /** The server's plain sentence, its code and model. */
    data class Failed(val detail: String, val code: String?, val model: String?) : ChatStreamEvent
    /** The persisted row: its id, timestamp, model and kept text. */
    data class Done(val row: MessageRow) : ChatStreamEvent
    /** A long document's job, `job` or `progress` (documents plan D2). */
    data class Job(val view: ServerJobView) : ChatStreamEvent
}

/** A chat stream frame's event, or null for one this app doesn't read (an
 *  unknown event, or one whose data doesn't parse). */
internal fun chatStreamEvent(event: String, data: String): ChatStreamEvent? = runCatching {
    val payload = Json.parseToJsonElement(data).jsonObject
    when (event) {
        "delta" -> payload["text"]?.jsonPrimitive?.content?.let { ChatStreamEvent.Delta(it) }
        "replace" -> payload["text"]?.jsonPrimitive?.content?.let { ChatStreamEvent.Replace(it) }
        "error" -> payload["detail"]?.jsonPrimitive?.content?.let { detail ->
            ChatStreamEvent.Failed(detail, payload["code"]?.jsonPrimitive?.contentOrNull, payload["model"]?.jsonPrimitive?.contentOrNull)
        }
        "done" -> ChatStreamEvent.Done(payload.decode<MessageRow>())
        "job", "progress" -> ChatStreamEvent.Job(payload.decode<ServerJobView>())
        else -> null
    }
}.getOrNull()

/** Reads a chat answer's SSE body to its end, handing [onEvent] each event
 *  it reads (chatStreamEvent); the rest are skipped. */
internal suspend fun readChatStream(source: okio.BufferedSource, onEvent: suspend (ChatStreamEvent) -> Unit) {
    var event = "message"
    val data = StringBuilder()
    while (true) {
        val line = source.readUtf8Line() ?: break
        when {
            line.startsWith("event: ") -> event = line.removePrefix("event: ").trim()
            line.startsWith("data: ") -> data.append(line.removePrefix("data: "))
            line.isEmpty() && data.isNotEmpty() -> {
                val ev = chatStreamEvent(event, data.toString())
                event = "message"
                data.setLength(0)
                ev?.let { onEvent(it) }
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
internal fun ConversationPane(
    thread: ThreadRow?,
    frame: ChatFrame,
    /** The thread's title as the list has it now (the model's title comes
     *  after the first reply); null: not known there yet. */
    titleOf: (Long?) -> String? = { null },
    /** The thread as the list has it now (its access fields after a share
     *  or unshare anywhere); null: not listed (yet). */
    rowOf: (Long?) -> ThreadRow? = { null },
    onCreated: (ThreadRow) -> Unit = {},
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    // The thread this chat writes to: the one opened, or the one the first
    // message made.
    var current by remember { mutableStateOf(thread) }
    // Private chats (1.5.0, ChatPrivacy.kt): whether this phone holds a
    // device key for the server (a new chat is then private unless the
    // switch says otherwise), and whether it is a shared screen there.
    val sharedScreen = com.domovoi.app.net.LocalSharedScreen.current
    val serverUrl by app.prefs.serverUrl.collectAsState()
    val keysTick by app.prefs.deviceKeysChanged.collectAsState()
    val hasKey = remember(serverUrl, keysTick) { app.prefs.holdsDeviceKey(serverUrl) }
    var shareNew by remember { mutableStateOf(false) }
    var unshareAsk by remember { mutableStateOf(false) }
    // Shown once on this phone, and for this whole visit once shown.
    var privateNote by remember {
        mutableStateOf(frame.truePrivateNote && hasKey && !sharedScreen && !app.prefs.chatPrivateNoteSeen.value)
    }
    LaunchedEffect(privateNote) { if (privateNote) app.prefs.setChatPrivateNoteSeen() }
    // This chat isn't this phone's to see any more: said once, and closed.
    var gone by remember { mutableStateOf(false) }
    fun goneNow() {
        if (gone) return
        gone = true
        toast(CHAT_GONE_LINE)
        frame.onBack()
    }
    suspend fun ensureThread(): ThreadRow = current
        ?: app.api.post("/api/chat/threads", createThreadBody(hasKey, sharedScreen, shareNew)).decode<ThreadRow>().also {
            current = it
            onCreated(it)
        }
    // A create the server refused with a code (ChatPrivacy.kt): a shared
    // screen it now is (the chip and switch follow), or a key it doesn't
    // know (register again, which starts over if the key is lost). True
    // when [e] was one of those.
    fun createRefused(e: Throwable): Boolean {
        when (chatRefusal(e)) {
            ChatRefusal.SharedScreen -> {
                app.prefs.setSharedScreen(serverUrl, true)
                toast(CHAT_SHARED_SCREEN_LINE)
            }
            ChatRefusal.KeyMissing -> {
                app.scope.launch { com.domovoi.app.net.registerDevice(app) }
                toast((e as? com.domovoi.app.net.ApiException)?.let { com.domovoi.app.net.apiDetail(it) } ?: failureText("new chat", e))
            }
            null -> return false
        }
        return true
    }

    val transcript = remember { mutableStateListOf<LiveMessage>() }
    var draft by remember { mutableStateOf("") }
    val attachments = remember { mutableStateListOf<ImageRef>() }
    // Documents for the next message (documents plan, D1): uploaded and
    // read on the home server as they are picked.
    val docAttachments = remember { mutableStateListOf<ServerDocument>() }
    var docUploading by remember { mutableStateOf<String?>(null) }
    // A video for the next message (ChatVideo.kt): going up, then staged on
    // the home server; one a message.
    var videoDraft by remember { mutableStateOf<VideoDraft?>(null) }
    var videoUpload by remember { mutableStateOf<kotlinx.coroutines.Job?>(null) }
    val caps = com.domovoi.app.net.LocalCapabilities.current
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
                    .onFailure { if (!createRefused(it)) toast(failureText("new chat", it)) }
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
    // The draft panel (documents plan D4), and the home's printers for its
    // Print (the server's Print sheet, as for a file in Documents).
    var canvas by remember { mutableStateOf<Long?>(null) }
    var canvasVersion by remember { mutableStateOf<Int?>(null) }
    var canvasKey by remember { mutableStateOf(0) }
    var printDraft by remember { mutableStateOf<Pair<Long, Int>?>(null) }
    var printAttachment by remember { mutableStateOf<PrintAttachmentRef?>(null) }
    val draftPrinters = rememberApi("printers-for-drafts") {
        com.domovoi.app.ui.screens.printing.PrintIo(it.api).printers()
    }.data
    val draftDocs = remember { HashMap<Long, ServerDraftDoc>() }
    fun draftDoc(id: Long) = draftDocs.getOrPut(id) {
        ServerDraftDoc(app, id, privateChat = { current?.shared == false }, canWrite = { current?.writable != false })
    }

    // The thread's long-document jobs (documents plan D2).
    val jobs = remember { mutableStateListOf<ServerJobView>() }
    // The thread's videos (ChatVideo.kt): each one's chip under its message.
    val videoJobs = remember { mutableStateListOf<ServerVideoJob>() }
    var reloads by remember { mutableStateOf(0) }
    // A thread's messages, read from the server (opening it, and after
    // "try again"), with its jobs. False when it couldn't be read.
    suspend fun loadMessages(threadId: Long): Boolean =
        runCatching {
            app.api.get("/api/chat/threads/$threadId/messages").decode<MessageList>()
        }.onFailure {
            if (isThreadGone(it)) goneNow()
        }.onSuccess { list ->
            // The thread's access as the server says it now (1.5.0).
            list.thread?.let { t -> if (current?.id == t.id) current = current?.withAccessOf(t) }
            val rows = list.messages
            jobs.clear()
            jobs.addAll(list.jobs)
            videoJobs.clear()
            videoJobs.addAll(list.videos)
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
                        retryable = it.retryable,
                        documents = it.documents.orEmpty(),
                        doc = it.doc,
                        draft = it.draft,
                        printAttachment = it.print_attachment,
                    ),
                )
            }
        }.isSuccess
    // A thread opened from the list: its messages. A new chat has none.
    LaunchedEffect(thread?.id, reloads) {
        if (thread != null) loadMessages(thread.id)
    }
    // The list is read again on every chat change: its row carries this
    // chat's access as it is now (shared or unshared on another device, by
    // an admin). A chat that was listed and no longer is may have been
    // unshared from this phone: asked once, and a 404 closes it.
    val listed = rowOf(current?.id)
    var seenInList by remember { mutableStateOf(false) }
    LaunchedEffect(listed) {
        val id = current?.id ?: return@LaunchedEffect
        if (listed != null) {
            seenInList = true
            if (listed.knowsSharing) current = current?.withAccessOf(listed)
        } else if (seenInList) {
            runCatching { app.api.get("/api/chat/threads/$id/messages").decode<MessageList>() }
                .onSuccess { l -> l.thread?.let { t -> current = current?.withAccessOf(t) } }
                .onFailure { if (isThreadGone(it)) goneNow() }
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
    // A video being watched: its view every three seconds while queued or
    // running (the dashboard's pace), then the thread again when one ends
    // (its summary, and the answer to a question sent with it, are in).
    val liveVideos = liveVideoKey(videoJobs)
    LaunchedEffect(liveVideos, sending) {
        if (liveVideos.isEmpty() || sending) return@LaunchedEffect
        val threadId = current?.id ?: return@LaunchedEffect
        while (true) {
            kotlinx.coroutines.delay(VIDEO_POLL_MS)
            val poll = pollVideos(videoJobs.toList()) { fetchVideoJob(app.api, it) }
            // Read back, the thread brings every job's view with it; if it
            // can't be read, the views read here still show how it ended.
            if (poll.ended && loadMessages(threadId)) return@LaunchedEffect
            val merged = mergeVideoViews(videoJobs.toList(), poll.views)
            merged.forEachIndexed { i, v -> if (videoJobs.getOrNull(i) != v) videoJobs[i] = v }
            if (poll.ended) return@LaunchedEffect
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

    // A video (ChatVideo.kt): its length and size checked here before
    // anything is sent, then streamed up with its progress; removing it
    // stops the upload.
    val videoPicker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        val info = models.data?.video ?: return@rememberLauncherForActivityResult
        if (videoDraft != null) { toast(VIDEO_ONE_A_MESSAGE); return@rememberLauncherForActivityResult }
        uploadNote = null
        val key = System.nanoTime()
        // Only this pick's draft changes: a removed one stays removed.
        fun update(f: (VideoDraft) -> VideoDraft?) {
            val cur = videoDraft
            if (cur != null && cur.key == key) videoDraft = f(cur)
        }
        videoUpload = scope.launch {
            val picked = withContext(Dispatchers.IO) { pickedVideo(context, uri) }
            val name = videoUploadName(picked.name, picked.mime, info.extensions)
            videoRefusalBeforeUpload(name, picked.durationS, picked.size, info)?.let {
                uploadNote = it
                return@launch
            }
            val upName = name ?: return@launch
            videoDraft = VideoDraft(key, upName)
            try {
                val staged = uploadChatVideo(
                    app.api, upName, picked.mime?.takeIf { it.startsWith("video/") } ?: "application/octet-stream",
                    picked.size, videoOpener(context, uri, upName),
                    onPercent = { p -> scope.launch { update { it.copy(percent = p) } } },
                )
                update { it.copy(staged = staged, percent = 100) }
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                update { null }
                uploadNote = videoFailureText(e)
            }
        }
    }
    val removeVideo: () -> Unit = {
        videoUpload?.cancel()
        videoUpload = null
        videoDraft = null
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

    /** [revise]: (draft, text) from a draft card's Revise box; else the
     *  message box's words and attachments. */
    fun send(revise: Pair<Long, String>? = null): Boolean {
        val content = revise?.second?.trim() ?: draft.trim()
        // A video still going up can't be sent yet; a staged one can be
        // sent alone (its question can come later, or with it).
        if (videoDraft?.uploading == true && revise == null) return false
        val video = if (revise != null) null else videoDraft?.staged
        if ((content.isBlank() && (revise != null || (docAttachments.isEmpty() && video == null))) || sending || docUploading != null) return false
        val images = if (revise != null) emptyList() else attachments.toList()
        val docs = if (revise != null) emptyList() else docAttachments.toList()
        if (revise == null) {
            draft = ""
            attachments.clear()
            docAttachments.clear()
            videoDraft = null
            videoUpload = null
        }
        sending = true
        following = true
        frame.onSent()
        // Stamped now; the server's own row (with its id) is not sent back
        // for the user turn, and the two clocks agree to the second on a LAN.
        val asked = LiveMessage(
            "user", content, images, createdAt = Instant.now().toString(), device = "this phone", documents = docs,
            stagedVideo = video,
        )
        transcript.add(asked)
        val live = LiveMessage("assistant", "", pending = true)
        live.sentAtMs = System.currentTimeMillis()
        transcript.add(live)
        scope.launch {
            runCatching {
                val target = ensureThread()
                sendStreaming(
                    app, target.id, content, images, docs,
                    onDelta = { live.appendStreamed(it) },
                    onReplace = { live.replaceStreamed(it) },
                    onError = { detail, code, model ->
                        live.error = detail
                        live.errorCode = code
                        if (model != null) live.model = model
                    },
                    onDone = { row -> live.finishWith(row) },
                    reviseDraft = revise?.first,
                    onJob = { live.job = it },
                    videos = listOfNotNull(video?.token),
                )
                // A video sent is being watched now: the thread again, for
                // its chip under this message (the job names the message).
                // A refused send keeps its error in place instead.
                if (video != null && live.error == null) loadMessages(target.id)
            }.onFailure { e ->
                when {
                    isThreadGone(e) -> { live.error = CHAT_GONE_LINE; goneNow() }
                    // The chat couldn't be made as the chip said: nothing was
                    // sent, so the words go back in the box.
                    current == null && createRefused(e) -> {
                        transcript.remove(live)
                        transcript.remove(asked)
                        if (revise == null) {
                            draft = content
                            attachments.addAll(images)
                            docAttachments.addAll(docs)
                        }
                    }
                    else -> live.error = failureText("send", e)
                }
            }
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
        return true
    }

    // A pick on a heads-up card (documents plan D3): the card keeps it and
    // the question before it is answered that way; "choose another" answers
    // nothing and opens the picture picker.
    fun pickHeadsUp(card: LiveMessage, option: String) {
        val target = current ?: return
        val id = card.id ?: return
        if (sending) return
        val hu = card.doc?.heads_up ?: return
        if (option == "choose_another") {
            scope.launch {
                runCatching {
                    withContext(Dispatchers.IO) {
                        app.api.post("/api/chat/threads/${target.id}/heads-up", buildJsonObject {
                            put("message_id", id)
                            put("option", option)
                        })
                    }
                }.onSuccess {
                    card.doc = card.doc?.copy(heads_up = hu.copy(chosen = option))
                    picker.launch("image/*")
                }.onFailure { toast(failureText("heads-up", it)) }
            }
            return
        }
        sending = true
        following = true
        card.doc = card.doc?.copy(heads_up = hu.copy(chosen = option))
        val live = LiveMessage("assistant", "", pending = true)
        live.sentAtMs = System.currentTimeMillis()
        transcript.add(live)
        scope.launch {
            runCatching {
                sendStreaming(
                    app, target.id, "", emptyList(), emptyList(),
                    onDelta = { live.appendStreamed(it) },
                    onReplace = { live.replaceStreamed(it) },
                    onError = { detail, code, model ->
                        live.error = detail
                        live.errorCode = code
                        if (model != null) live.model = model
                    },
                    onDone = { row -> live.finishWith(row) },
                    onJob = { live.job = it },
                    headsUpPick = id to option,
                )
            }.onFailure { if (isThreadGone(it)) goneNow() else live.error = failureText("heads-up", it) }
            live.doneAtMs = System.currentTimeMillis()
            if (live.createdAt == null) live.createdAt = Instant.now().toString()
            live.pending = false
            sending = false
            loadMessages(target.id)
        }
    }

    // "Try again": the last reply makes way for a new one to the same
    // question; the server swaps them once the new one is written, and the
    // thread is read back after (a failed retry writes nothing there, so the
    // old reply comes back, and why is said).
    fun retry() {
        val target = current ?: return
        if (sending || !canRetry(transcript)) return
        sending = true
        following = true
        val old = transcript.removeAt(transcript.lastIndex)
        val live = LiveMessage("assistant", "", pending = true)
        live.sentAtMs = System.currentTimeMillis()
        transcript.add(live)
        scope.launch {
            runCatching {
                sendStreaming(
                    app, target.id, "", emptyList(), emptyList(),
                    onDelta = { live.appendStreamed(it) },
                    onReplace = { live.replaceStreamed(it) },
                    onError = { detail, code, model ->
                        live.error = detail
                        live.errorCode = code
                        if (model != null) live.model = model
                    },
                    retry = true,
                )
            }.onFailure {
                // Nothing reached the server: the old reply is still the one.
                transcript.remove(live)
                transcript.add(old)
                if (isThreadGone(it)) goneNow() else toast(failureText("try again", it))
            }
            live.pending = false
            sending = false
            live.error?.let { toast("couldn't try again: $it") }
            loadMessages(target.id)
        }
    }

    // The answer to a lookup card (part 5, W1): the card shows it at once,
    // the reply streams in, and the thread is read back after.
    fun decideLookup(m: LiveMessage, decision: String) {
        val target = current ?: return
        val id = m.id ?: return
        if (sending) return
        sending = true
        following = true
        val state = when (decision) { "once" -> "allowed_once"; "chat" -> "allowed_chat"; else -> "declined" }
        m.stats = m.stats?.let { s -> s.copy(lookup_card = s.lookup_card?.copy(state = state)) }
        val live = LiveMessage("assistant", "", pending = true)
        live.sentAtMs = System.currentTimeMillis()
        transcript.add(live)
        scope.launch {
            runCatching {
                sendStreaming(
                    app, target.id, "", emptyList(), emptyList(),
                    onDelta = { live.appendStreamed(it) },
                    onReplace = { live.replaceStreamed(it) },
                    onError = { detail, code, model ->
                        live.error = detail
                        live.errorCode = code
                        if (model != null) live.model = model
                    },
                    onDone = { row -> live.finishWith(row) },
                    decision = id to decision,
                )
            }.onFailure { if (isThreadGone(it)) goneNow() else live.error = failureText("look it up", it) }
            live.pending = false
            sending = false
            if (decision == "chat") current = target.copy(internet_allowed = true)
            loadMessages(target.id)
        }
    }

    // A chat started from the list's "Message Domovoi…" box: its first
    // message goes out as the chat opens.
    LaunchedEffect(Unit) {
        val first = frame.firstMessage?.trim().orEmpty()
        if (first.isNotEmpty() && thread == null && transcript.isEmpty()) {
            draft = first
            send()
        }
    }

    // "Share with household" on or off (the owning phone, or an admin):
    // turning it off asks first ([CHAT_UNSHARE_ASK]).
    fun setShared(want: Boolean) {
        val t = current ?: return
        scope.launch {
            runCatching {
                app.api.patch("/api/chat/threads/${t.id}", buildJsonObject { put("shared", want) }).decode<ThreadRow>()
            }.onSuccess { r ->
                current = current?.withAccessOf(r)
                toast(if (want) CHAT_SHARED_TOAST else CHAT_UNSHARED_TOAST)
            }.onFailure { e ->
                when {
                    isThreadGone(e) -> goneNow()
                    chatRefusal(e) == ChatRefusal.SharedScreen -> {
                        app.prefs.setSharedScreen(serverUrl, true)
                        toast(CHAT_SHARED_SCREEN_LINE)
                    }
                    else -> toast(failureText("change it", e))
                }
            }
        }
    }
    if (unshareAsk) {
        com.domovoi.app.ui.components.ConfirmDialog(
            title = "stop sharing",
            body = CHAT_UNSHARE_ASK,
            confirmLabel = "stop sharing",
            onConfirm = { setShared(false) },
            onDismiss = { unshareAsk = false },
        )
    }

    val open = current
    // What the server lets this phone do in it: a control it would refuse
    // is not shown (an admin reading another device's private chat writes
    // nothing; a field an older server didn't send denies nothing).
    val writable = open?.writable != false
    if (voiceOpen && open != null) {
        VoiceView(open.id, transcript, onClose = { voiceOpen = false })
        return
    }
    reporting?.let { draftReport ->
        ReportPane(draftReport, serverReportActions(app.scope), canSave = false) { reporting = null }
        return
    }
    val threadId = open?.id ?: 0L
    val title = titleOf(open?.id) ?: open?.title ?: transcript.firstOrNull { it.role == "user" }
        ?.let { com.domovoi.app.assistant.ChatTitles.firstWords(it.content) } ?: NEW_CHAT
    // Where the newest reply's view of the thread began, when it didn't
    // see all of it: the quiet divider goes above that message.
    val windowAt = windowDividerIndex(transcript.map { it.id }, transcript.lastOrNull { it.role != "user" && it.stats != null }?.stats?.window_from_id)
    val shareChat: (() -> Unit)? = if (transcript.isEmpty() || sending) null else {
        { shareText(context, title, serverChatText(title, transcript.map { it.asRow() })) }
    }

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        ChatPaneGutter()
        ThreadTitleRow(title, frame, shareChat)
        when {
            open != null && open.knowsSharing -> {
                if (open.admin_view) ChatNote(adminBanner(open.owner_name))
                val shared = open.shared == true
                ChatPrivacyBar(privacyLine(shared, sharedScreen), shared || sharedScreen, showsShareSwitch(open, sharedScreen)) { want ->
                    if (want) setShared(true) else unshareAsk = true
                }
            }
            // A new chat: what it will be, and the switch before it exists.
            open == null -> newChatLine(hasKey, sharedScreen, shareNew)?.let { line ->
                ChatPrivacyBar(line, shareNew || sharedScreen, hasKey && !sharedScreen) { shareNew = it }
            }
        }
        if (privateNote) ChatNote(CHAT_TRULY_PRIVATE_NOTE) { privateNote = false }
        open?.takeIf { writable }?.let { t ->
            ServerInternetChip(t, onToggle = { allowed ->
                scope.launch {
                    runCatching {
                        app.api.patch("/api/chat/threads/${t.id}", kotlinx.serialization.json.buildJsonObject {
                            put("internet_allowed", allowed)
                        })
                    }.onSuccess { current = t.copy(internet_allowed = allowed) }
                        .onFailure { toast(failureText("change it", it)) }
                }
            })
        }

        val openDraft = canvas
        if (openDraft != null) Box(Modifier.weight(1f).fillMaxWidth()) {
            DraftCanvas(
                draftDoc(openDraft), canvasVersion, canvasKey, onClose = { canvas = null },
                onPrint = if (writable && draftPrinters?.hasPrinter == true) ({ v -> printDraft = openDraft to v }) else null,
            )
        } else Box(Modifier.weight(1f).fillMaxWidth().then(rememberFitStretch(listState))) {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                // A stopped summary: its newest reply offers "carry on".
                val stopped = jobs.filter { it.state == "stopped" || it.state == "failed" }
                val carryAt = stopped.associate { j -> j.id to transcript.indexOfLast { it.doc?.job?.id == j.id } }
                itemsIndexed(transcript) { i, m ->
                    if (i == windowAt) WindowDivider()
                    // A lookup's permission card: the exact words, where they
                    // go, and the three answers (part 5, W1).
                    val card = m.stats?.lookup_card
                    if (card != null && m.role != "user") {
                        com.domovoi.app.ui.screens.assistant.LookupCardView(card, enabled = !sending && writable) { d -> decideLookup(m, d) }
                        return@itemsIndexed
                    }
                    // The draft is made at the tap, from the transcript then.
                    val stoppedJob = m.doc?.job?.id?.let { id -> stopped.firstOrNull { it.id == id && carryAt[id] == i } }
                    MessageBubble(
                        m, threadId,
                        onReport = if (canReport(m)) ({ reporting = reportableAt(i) }) else null,
                        onSource = { openSource = it },
                        carryOnLeft = stoppedJob?.let { it.parts - it.read }.takeIf { !sending && writable },
                        onCarryOn = {
                            stoppedJob?.let { j ->
                                scope.launch {
                                    runCatching { resumeServerJob(app, j.id) }
                                        .onSuccess { v -> jobs.indexOfFirst { it.id == v.id }.takeIf { it >= 0 }?.let { jobs[it] = v } }
                                        .onFailure { toast(docFailureText("carry on", it)) }
                                }
                            }
                        },
                        onRetry = if (writable && i == transcript.lastIndex && !sending && canRetry(transcript)) ({ retry() }) else null,
                        // A video sent with this message: its job's chip.
                        videoJobs = m.id?.let { id -> videoJobs.filter { it.message_id == id } }.orEmpty(),
                        onStopVideo = { job ->
                            runCatching { cancelVideoJob(app.api, job.id) }
                                .onSuccess { v -> videoJobs.indexOfFirst { it.id == v.id }.takeIf { it >= 0 }?.let { videoJobs[it] = v } }
                                .onFailure { toast(failureText("stop", it)) }
                        },
                        // A card answers only while it ends the thread.
                        onHeadsUp = if (writable && i == transcript.lastIndex && !sending) ({ option -> pickHeadsUp(m, option) }) else null,
                        busy = sending,
                        draftCard = m.printAttachment?.takeIf { !m.pending && it.usable }?.let { att ->
                            {
                                AttachmentPrintCard(
                                    att,
                                    // Printing is a write (1.5.0): not in a chat this phone reads only.
                                    printers = draftPrinters.takeIf { writable },
                                    onPrint = { printAttachment = att },
                                )
                            }
                        } ?: m.draft?.takeIf { !m.pending }?.let { ref ->
                            {
                                ServerDraftCard(
                                    ref, m.content, transcript, draftDoc(ref.id), canvasKey,
                                    canPrint = writable && draftPrinters?.hasPrinter == true,
                                    onOpen = { canvas = ref.id; canvasVersion = ref.version; canvasKey++ },
                                    onPrint = { printDraft = ref.id to ref.version },
                                    onRevise = if (writable) ({ text -> send(ref.id to text) }) else null,
                                )
                            }
                        },
                    )
                }
                // A job a restart interrupted: carried on only when asked.
                if (!sending && writable) {
                    items(jobs.filter { it.state == "interrupted" }, key = { "int-" + it.id }) { j ->
                        DocJobCard(j.line.ifEmpty { "this summary was interrupted — carry on?" }, j.read, j.parts, onStop = null,
                            offer = carryOnText(j.parts - j.read), onOffer = {
                                scope.launch {
                                    runCatching { resumeServerJob(app, j.id) }
                                        .onSuccess { v -> jobs.indexOfFirst { it.id == v.id }.takeIf { it >= 0 }?.let { jobs[it] = v } }
                                        .onFailure { toast(docFailureText("carry on", it)) }
                                }
                            })
                    }
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
        videoDraft?.let { v ->
            Box(Modifier.padding(top = 6.dp)) {
                StagedVideoChip(v.name, v.staged, v.percent, onRemove = removeVideo)
            }
        }
        openSource?.let { SourceSheet(it, onClose = { openSource = null }) }
        val printing = printDraft
        if (printing != null && draftPrinters != null) {
            val title = transcript.firstNotNullOfOrNull { it.draft?.takeIf { d -> d.id == printing.first } }?.title ?: "draft"
            com.domovoi.app.ui.screens.printing.PrintSheet(
                libraryId = "", path = "", fileName = "$title v${printing.second}",
                printers = draftPrinters, onDismiss = { printDraft = null },
                target = com.domovoi.app.ui.screens.printing.PrintTarget.ChatDraft(printing.first, printing.second),
            )
        }
        val attaching = printAttachment
        if (attaching != null && draftPrinters != null) {
            com.domovoi.app.ui.screens.printing.PrintSheet(
                libraryId = "", path = "", fileName = attaching.name.ifBlank { "attachment" },
                printers = draftPrinters, onDismiss = { printAttachment = null },
                target = com.domovoi.app.ui.screens.printing.PrintTarget.ChatAttachment(attaching.token),
            )
        }
        // A new chat with nothing said yet: the place can still change.
        if (open == null && transcript.isEmpty() && !sending) frame.picker?.invoke()

        if (writable) Row(
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
                    // Only when the home server can watch one (AI on, ffmpeg there).
                    if (videoOffered(models.data, ChatPlaceKind.Server, caps.aiEnabled)) {
                        DropdownMenuItem(
                            text = { Text(VIDEO_MENU_ITEM) },
                            onClick = {
                                attachMenu = false
                                if (videoDraft != null) toast(VIDEO_ONE_A_MESSAGE) else videoPicker.launch("video/*")
                            },
                        )
                    }
                }
            }
            OutlinedTextField(
                value = draft, onValueChange = { draft = it },
                placeholder = { Text(COMPOSER_HINT) },
                modifier = Modifier.weight(1f),
                maxLines = 4,
            )
            // Nothing typed: the mic, which opens the hold-to-talk view.
            if (draft.isBlank() && attachments.isEmpty() && docAttachments.isEmpty() && videoDraft == null) {
                IconButton(onClick = openVoice, enabled = !sending) {
                    Icon(
                        Icons.Outlined.Mic, contentDescription = "talk",
                        tint = if (!sending) Domovoi.colors.brand else Domovoi.colors.fgFaint,
                    )
                }
            } else {
                val canSend = (draft.isNotBlank() || docAttachments.isNotEmpty() || videoDraft?.staged != null) &&
                    !sending && docUploading == null && videoDraft?.uploading != true
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
 * screen: the owner's "my scroll seems to be locked" (2026-10-12). Opening the
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
private fun ThreadTitleRow(title: String, frame: ChatFrame, onShare: (() -> Unit)?) {
    if (keyboardCrowdsTheWindow()) return
    // Chat's header in every place (ChatHome.kt): back, the title, the chip,
    // and sharing the whole chat as text.
    ChatHeader(title, frame.chip, ChatPlaceKind.Server, frame.onBack, actions = onShare?.let { share ->
        { ShareChatButton(share) }
    })
}

/** The conversation header's internet chip for a home-server chat: the
 *  household's answer (never / always allow) from `GET /api/chat/internet`
 *  and this thread's "Allow in this chat". */
@Composable
private fun ServerInternetChip(thread: ThreadRow, onToggle: (Boolean) -> Unit) {
    val app = LocalApp.current
    var info by remember { mutableStateOf<Pair<String, Boolean>?>(null) }
    LaunchedEffect(thread.id) {
        info = runCatching {
            val o = app.api.get("/api/chat/internet").jsonObject
            (o["internet"]?.jsonPrimitive?.contentOrNull.orEmpty()) to
                (o["always_allow"]?.jsonPrimitive?.contentOrNull == "true")
        }.getOrNull()
    }
    val (policy, always) = info ?: return
    val (label, on) = serverInternetChip(policy, always, thread.internet_allowed)
    val fixed = policy == "never" || always
    Row(
        Modifier.padding(bottom = 4.dp)
            .border(1.dp, if (on) Domovoi.colors.brand else Domovoi.colors.border, RoundedCornerShape(999.dp))
            .clickable(enabled = !fixed) { onToggle(!thread.internet_allowed) }
            .padding(horizontal = 10.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, style = MaterialTheme.typography.labelMedium, color = if (on) Domovoi.colors.fg else Domovoi.colors.fgMuted)
    }
}

/** The server chat chip's words: (label, on), as the dashboard says them.
 *  A chat the house asks first in, not allowed yet, says so ("internet:
 *  ask first"), not "internet off": it can still look things up once
 *  someone says yes, and a tap allows it in this chat. */
internal fun serverInternetChip(policy: String, alwaysAllow: Boolean, chatAllowed: Boolean): Pair<String, Boolean> = when {
    policy == "never" -> "internet off" to false
    alwaysAllow -> "internet always on" to true
    chatAllowed -> "internet on for this chat" to true
    else -> "internet: ask first" to false
}

/** A message as the share text reads it. */
private fun LiveMessage.asRow() = MessageRow(role = role, content = content, error = error, speaker_name = speakerName)

/** "Try again" is offered on the last message when it is a finished reply
 *  to a question. */
internal fun canRetry(transcript: List<LiveMessage>): Boolean {
    val last = transcript.lastOrNull() ?: return false
    val before = transcript.getOrNull(transcript.size - 2) ?: return false
    return last.role != "user" && !last.pending && before.role == "user" && last.retryable
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
    onRetry: (() -> Unit)? = null,
    onSource: (SourceView) -> Unit = {},
    /** A draft reply's card (documents plan D4), in place of its text. */
    draftCard: (@Composable () -> Unit)? = null,
    /** A stopped summary's parts still to read: "carry on" shows (null: no). */
    carryOnLeft: Int? = null,
    onCarryOn: () -> Unit = {},
    /** A pick on this heads-up card (null: it doesn't answer now). */
    onHeadsUp: ((String) -> Unit)? = null,
    busy: Boolean = false,
    /** Videos sent with this (user) message: their jobs' chips (ChatVideo.kt). */
    videoJobs: List<ServerVideoJob> = emptyList(),
    onStopVideo: (suspend (ServerVideoJob) -> Unit)? = null,
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
        // A video sent with it: as sent, then its job's chip once the thread is read back.
        if (videoJobs.isEmpty()) m.stagedVideo?.let { v -> StagedVideoChip(v.name, v, 100, onRemove = null) }
        videoJobs.forEach { j -> VideoJobChip(j, onStopVideo) }
        if (isUser && m.content.isBlank() &&
            (m.documents.isNotEmpty() || m.stagedVideo != null || videoJobs.isNotEmpty())
        ) return@Column
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
                    } else if (draftCard != null) {
                        draftCard()
                    } else {
                        val job = m.job
                        if (m.pending && job != null && m.content.isEmpty()) {
                            // A long document read in parts: how far, and stop.
                            DocJobCard(job.line.ifEmpty { "reading part ${job.part} of ${job.parts}" }, job.read, job.parts,
                                onStop = if (job.live) ({ app.scope.launch { runCatching { stopServerJob(app, job.id) } } }) else null)
                        } else if (m.doc?.mode == "heads_up" && m.doc?.heads_up != null) {
                            // A heads-up card is the reply until an option is picked (D3).
                            val hu = m.doc!!.heads_up!!
                            val skip by app.headsUp.state.collectAsState()
                            HeadsUpCard(hu.view(), silenced = hu.kind in skip.skip, busy = busy, onPick = onHeadsUp,
                                onNever = { app.headsUp.neverWarn(hu.kind) })
                        } else {
                            MarkdownText(m.content, trailing = cursor)
                        }
                        if (!m.pending && m.doc?.mode != "heads_up") {
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
                    text = { Text(ACTION_SHARE) },
                    leadingIcon = { Icon(Icons.Outlined.Share, contentDescription = null) },
                    enabled = m.content.isNotEmpty(),
                    onClick = { menu = false; shareText(context, "Domovoi", m.content) },
                )
                if (onRetry != null) {
                    DropdownMenuItem(
                        text = { Text(ACTION_TRY_AGAIN) },
                        leadingIcon = { Icon(Icons.Outlined.Refresh, contentDescription = null) },
                        onClick = { menu = false; onRetry() },
                    )
                }
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
