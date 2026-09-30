package com.domovoi.app.ui.screens.chat

import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.AnnotatedString
import java.time.Instant
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.interaction.DragInteraction
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.outlined.AttachFile
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.shell.keyboardCrowdsTheWindow
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import com.domovoi.app.ui.components.MarkdownText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
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
private data class ThreadRow(
    val id: Long = 0,
    val title: String? = null,
    val updated_at: String? = null,
    val message_count: Int = 0,
    val last_snippet: String? = null,
)

@Serializable
private data class ThreadList(val threads: List<ThreadRow> = emptyList())

@Serializable
private data class ImageRef(val token: String = "", val name: String = "")

@Serializable
private data class MessageRow(
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
)

@Serializable
private data class MessageList(val messages: List<MessageRow> = emptyList())

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
private class LiveMessage(
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
) {
    var content by mutableStateOf(content)
    var error by mutableStateOf(error)
    var pending by mutableStateOf(pending)
    var model by mutableStateOf(model)
    var id by mutableStateOf(id)
    var createdAt by mutableStateOf(createdAt)
    var stats by mutableStateOf(stats)
    var device by mutableStateOf(device)
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
    onDelta: (String) -> Unit,
    onError: (String) -> Unit,
    onDone: (MessageRow) -> Unit = {},
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
            onError("${resp.code} ${resp.message}")
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
                            "error" -> payload["detail"]?.jsonPrimitive?.content?.let {
                                withContext(Dispatchers.Main) { onError(it) }
                            }
                            // The persisted row: its id, timestamp and model.
                            "done" -> payload.decode<MessageRow>().let {
                                withContext(Dispatchers.Main) { onDone(it) }
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
// Screen — thread list when nothing selected; conversation otherwise.
// ---------------------------------------------------------------------------

@Composable
fun ChatScreen() {
    val app = LocalApp.current
    val threads = rememberApi("chat-threads", eventTypes = setOf("chat.changed")) {
        it.api.get("/api/chat/threads").decode<ThreadList>().threads
    }
    val toast = LocalToast.current
    var openThread by remember { mutableStateOf<ThreadRow?>(null) }
    var deleteTarget by remember { mutableStateOf<ThreadRow?>(null) }

    val current = openThread
    if (current == null) {
        ThreadListPane(
            threads.data.orEmpty(),
            onOpen = { openThread = it },
            onNew = {
                app.scope.launch {
                    runCatching {
                        app.api.post("/api/chat/threads").decode<ThreadRow>()
                    }.onSuccess { openThread = it; threads.refresh() }
                }
            },
            onDelete = { deleteTarget = it },
        )
        // F-A010: destructive, so confirm first (web chat.jsx window.confirm parity).
        deleteTarget?.let { t ->
            ConfirmDialog(
                title = "delete chat",
                body = "Delete \"${t.title ?: "new chat"}\"? This can't be undone.",
                confirmLabel = "delete",
                destructive = true,
                onConfirm = {
                    app.scope.launch {
                        runCatching { app.api.delete("/api/chat/threads/${t.id}") }
                            .onFailure { toast(failureText("delete", it)) }
                        threads.refresh()
                    }
                },
                onDismiss = { deleteTarget = null },
            )
        }
    } else {
        val closeThread = { openThread = null; threads.refresh() }
        // F-A006: the system back key closes the conversation like the in-app
        // chevron does (PeopleScreen shape); without this it fell through to app
        // nav and landed on Music.
        BackHandler(onBack = closeThread)
        ConversationPane(current, onBack = closeThread)
    }
}

@Composable
private fun ThreadListPane(
    threads: List<ThreadRow>,
    onOpen: (ThreadRow) -> Unit,
    onNew: () -> Unit,
    onDelete: (ThreadRow) -> Unit,
) {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        PageHeader(
            "Chat", "text chat with the domovoi — runs on your own hardware",
            actions = { Button(onClick = onNew) { Text("new chat") } },
        )
        Spacer(Modifier.height(12.dp))
        if (threads.isEmpty()) {
            EmptyState("no chats yet", "start one — attach an image and the vision model reads it")
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(threads, key = { it.id }) { t ->
                    DomovoiCard(Modifier.fillMaxWidth().clickable { onOpen(t) }) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(
                                    t.title ?: "new chat",
                                    style = MaterialTheme.typography.titleSmall,
                                    color = Domovoi.colors.fg,
                                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                                )
                                Text(
                                    t.last_snippet ?: "no messages yet",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = Domovoi.colors.fgFaint,
                                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                                )
                            }
                            IconButton(onClick = { onDelete(t) }) {
                                Icon(
                                    Icons.Outlined.DeleteOutline, contentDescription = "delete",
                                    tint = Domovoi.colors.fgMuted,
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ConversationPane(thread: ThreadRow, onBack: () -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()

    val transcript = remember { mutableStateListOf<LiveMessage>() }
    var draft by remember { mutableStateOf("") }
    val attachments = remember { mutableStateListOf<ImageRef>() }
    var sending by remember { mutableStateOf(false) }

    LaunchedEffect(thread.id) {
        runCatching {
            app.api.get("/api/chat/threads/${thread.id}/messages").decode<MessageList>().messages
        }.onSuccess { rows ->
            transcript.clear()
            rows.forEach {
                transcript.add(
                    LiveMessage(
                        it.role, it.content, it.images.orEmpty(), it.model, it.error,
                        id = it.id.takeIf { id -> id > 0 }, createdAt = it.created_at,
                        stats = it.stats,
                        device = when {
                            it.device_id != null && it.device_id == app.prefs.deviceId -> "this phone"
                            else -> it.device_name ?: it.device_id
                        },
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
        if (tail == null || tail.role == "user") return@LaunchedEffect
        snapshotFlow { tail.content.length to tail.error }
            .collect { if (following && tail.pending) listState.scrollBy(FOLLOW_STEP_PX) }
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
                    val mime = context.contentResolver.getType(uri) ?: "image/jpeg"
                    val ext = when {
                        mime.contains("png") -> "png"
                        mime.contains("webp") -> "webp"
                        mime.contains("gif") -> "gif"
                        else -> "jpg"
                    }
                    val form = MultipartBody.Builder().setType(MultipartBody.FORM)
                        .addFormDataPart(
                            "file", "photo.$ext",
                            bytes.toRequestBody(mime.toMediaType()),
                        )
                        .build()
                    val up = app.api.upload("/api/chat/uploads", form).decode<ImageRef>()
                    withContext(Dispatchers.Main) {
                        // Uploads run in parallel: two picker rounds can race past the cap.
                        if (attachments.size < MAX_CHAT_IMAGES) attachments.add(up) else toast(CHAT_IMAGE_CAP_TOAST)
                    }
                }.onFailure {
                    withContext(Dispatchers.Main) { toast("upload failed") }
                }
            }
        }
    }

    fun send() {
        val content = draft.trim()
        if (content.isBlank() || sending) return
        val images = attachments.toList()
        draft = ""
        attachments.clear()
        sending = true
        following = true
        // Stamped now; the server's own row (with its id) is not sent back
        // for the user turn, and the two clocks agree to the second on a LAN.
        transcript.add(
            LiveMessage("user", content, images, createdAt = Instant.now().toString(), device = "this phone"),
        )
        val live = LiveMessage("assistant", "", pending = true)
        live.sentAtMs = System.currentTimeMillis()
        transcript.add(live)
        scope.launch {
            runCatching {
                sendStreaming(
                    app, thread.id, content, images,
                    onDelta = {
                        if (live.firstWordsAtMs == null) live.firstWordsAtMs = System.currentTimeMillis()
                        live.content += it
                    },
                    onError = { live.error = it },
                    onDone = { row ->
                        live.id = row.id.takeIf { it > 0 }
                        live.createdAt = row.created_at
                        live.model = row.model ?: live.model
                        live.stats = row.stats
                    },
                )
            }.onFailure { live.error = it.message ?: "send failed" }
            live.doneAtMs = System.currentTimeMillis()
            if (live.createdAt == null) live.createdAt = Instant.now().toString()
            live.pending = false
            sending = false
        }
    }

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        ChatPaneGutter()
        ThreadTitleRow(thread, onBack)

        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(transcript) { m -> MessageBubble(m, thread.id) }
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

        Row(
            Modifier.fillMaxWidth().padding(top = 6.dp),
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            val atCap = attachments.size >= MAX_CHAT_IMAGES
            IconButton(onClick = { if (atCap) toast(CHAT_IMAGE_CAP_TOAST) else picker.launch("image/*") }) {
                Icon(
                    Icons.Outlined.AttachFile, contentDescription = "attach image",
                    tint = if (atCap) Domovoi.colors.fgFaint else Domovoi.colors.fgMuted,
                )
            }
            OutlinedTextField(
                value = draft, onValueChange = { draft = it },
                placeholder = { Text("message the domovoi…") },
                modifier = Modifier.weight(1f),
                maxLines = 4,
            )
            IconButton(onClick = { send() }, enabled = draft.isNotBlank() && !sending) {
                Icon(
                    Icons.AutoMirrored.Filled.Send, contentDescription = "send",
                    tint = if (draft.isNotBlank() && !sending) Domovoi.colors.brand else Domovoi.colors.fgFaint,
                )
            }
        }
        ChatPaneGutter()
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
private fun ThreadTitleRow(thread: ThreadRow, onBack: () -> Unit) {
    if (keyboardCrowdsTheWindow()) return
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        IconButton(onClick = onBack) {
            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "back", tint = Domovoi.colors.fg)
        }
        Text(
            thread.title ?: "new chat",
            style = MaterialTheme.typography.titleMedium,
            color = Domovoi.colors.fg,
            maxLines = 1, overflow = TextOverflow.Ellipsis,
        )
    }
    Spacer(Modifier.height(8.dp))
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

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun MessageBubble(m: LiveMessage, threadId: Long) {
    val app = LocalApp.current
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
                        MarkdownText(m.content, trailing = cursor)
                    }
                    m.error?.let {
                        Text(
                            it,
                            style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                            color = Domovoi.colors.err,
                            modifier = Modifier.padding(top = 4.dp),
                        )
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
            }
        }
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
