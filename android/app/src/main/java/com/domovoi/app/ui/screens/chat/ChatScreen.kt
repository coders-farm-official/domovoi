package com.domovoi.app.ui.screens.chat

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
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
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.Dispatchers
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
)

@Serializable
private data class MessageList(val messages: List<MessageRow> = emptyList())

/** Mutable transcript entry (the streaming assistant bubble updates live). */
private class LiveMessage(
    val role: String,
    content: String,
    val images: List<ImageRef> = emptyList(),
    val model: String? = null,
    error: String? = null,
    pending: Boolean = false,
) {
    var content by mutableStateOf(content)
    var error by mutableStateOf(error)
    var pending by mutableStateOf(pending)
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
) = withContext(Dispatchers.IO) {
    val body = buildJsonObject {
        put("content", content)
        put("images", buildJsonArray {
            images.forEach { img ->
                add(buildJsonObject { put("token", img.token); put("name", img.name) })
            }
        })
    }.toString().toRequestBody("application/json".toMediaType())
    val req = Request.Builder()
        .url(app.api.absolute("/api/chat/threads/$threadId/messages"))
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
    var openThread by remember { mutableStateOf<ThreadRow?>(null) }

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
            onDelete = { t ->
                app.scope.launch {
                    runCatching { app.api.delete("/api/chat/threads/${t.id}") }
                    threads.refresh()
                }
            },
        )
    } else {
        ConversationPane(current, onBack = { openThread = null; threads.refresh() })
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
                transcript.add(LiveMessage(it.role, it.content, it.images.orEmpty(), it.model, it.error))
            }
        }
    }
    LaunchedEffect(transcript.size) {
        if (transcript.isNotEmpty()) listState.animateScrollToItem(transcript.size - 1)
    }

    val picker = rememberLauncherForActivityResult(
        ActivityResultContracts.GetMultipleContents(),
    ) { uris ->
        uris.take(4 - attachments.size).forEach { uri ->
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
                    withContext(Dispatchers.Main) { attachments.add(up) }
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
        transcript.add(LiveMessage("user", content, images))
        val live = LiveMessage("assistant", "", pending = true)
        transcript.add(live)
        scope.launch {
            runCatching {
                sendStreaming(
                    app, thread.id, content, images,
                    onDelta = { live.content += it },
                    onError = { live.error = it },
                )
            }.onFailure { live.error = it.message ?: "send failed" }
            live.pending = false
            sending = false
        }
    }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
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

        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(transcript) { m -> MessageBubble(m) }
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
            IconButton(onClick = { picker.launch("image/*") }) {
                Icon(Icons.Outlined.AttachFile, contentDescription = "attach image", tint = Domovoi.colors.fgMuted)
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
    }
}

@Composable
private fun MessageBubble(m: LiveMessage) {
    val app = LocalApp.current
    val isUser = m.role == "user"
    Column(
        Modifier.fillMaxWidth(),
        horizontalAlignment = if (isUser) Alignment.End else Alignment.Start,
    ) {
        if (m.images.isNotEmpty()) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.padding(bottom = 4.dp)) {
                m.images.take(4).forEach { img ->
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
        Box(
            Modifier.widthIn(max = 480.dp)
                .clip(RoundedCornerShape(10.dp))
                .background(if (isUser) Domovoi.colors.card else Domovoi.colors.canvas)
                .then(
                    if (isUser) Modifier.border(1.dp, Domovoi.colors.border, RoundedCornerShape(10.dp))
                    else Modifier,
                )
                .padding(horizontal = if (isUser) 12.dp else 0.dp, vertical = if (isUser) 8.dp else 2.dp),
        ) {
            Column {
                Text(
                    m.content + if (m.pending) " ▍" else "",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fg,
                )
                m.error?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                        color = Domovoi.colors.err,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }
                if (!isUser && m.model != null && !m.pending) {
                    Text(
                        m.model,
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                        color = Domovoi.colors.fgFaint,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }
            }
        }
    }
}
