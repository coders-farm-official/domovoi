package com.domovoi.app.ui.screens.chat

import androidx.compose.runtime.Composable
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.ApiState
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.parseInstant
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.net.URLEncoder

/** A server thread as a row of Chat's list. */
internal fun ThreadRow.toChatRow(): ChatRow = ChatRow(
    place = ChatPlaceKind.Server,
    id = id.toString(),
    title = title ?: NEW_CHAT,
    snippet = last_snippet ?: "no messages yet",
    updatedMs = parseInstant(updated_at)?.toEpochMilli() ?: 0L,
    messages = message_count,
    archived = archived,
    match = match_snippet,
)

/** The search route for [query]: `GET /api/chat/threads?q=` (archived
 *  threads included). */
internal fun threadSearchPath(query: String): String =
    "/api/chat/threads?q=" + URLEncoder.encode(query.trim().take(SEARCH_MAX_CHARS), "UTF-8").replace("+", "%20")

/** The longest search the server takes. */
internal const val SEARCH_MAX_CHARS = 200

/** A home-server thread as plain text, for the share sheet. */
internal fun serverChatText(title: String, messages: List<MessageRow>): String = buildString {
    append(title).append("\n")
    append("Chat with Domovoi on the home server\n")
    messages.forEach { m ->
        val who = if (m.role == "user") (m.speaker_name ?: "You") else "Domovoi"
        val words = buildString {
            append(m.content.trim())
            m.error?.let { if (isNotEmpty()) append("\n"); append("[").append(it).append("]") }
        }
        append("\n").append(who).append(": ").append(words).append("\n")
    }
}

/**
 * The home server's chat as a Chat place: today's `/api/chat` threads, read
 * and deleted as the old thread list did, and [ConversationPane] (the REST
 * and SSE send, the server's voice view and photos) for a conversation.
 * Only offered while the server is in reach and allows chat
 * ([com.domovoi.app.net.Capabilities.aiEnabled]); the phone keeps no copy of these
 * threads. Rename and archive are the server's (`PATCH`), so they show on
 * every paired device; pins are this phone's own (ChatHome).
 */
@Composable
internal fun rememberServerChatPlace(): ChatPlace {
    val app = LocalApp.current
    val toast = LocalToast.current
    val threads = rememberApi("chat-threads", eventTypes = setOf("chat.changed")) {
        it.api.get("/api/chat/threads").decode<ThreadList>().threads
    }
    val archived = rememberApi("chat-threads-archived", eventTypes = setOf("chat.changed")) {
        it.api.get("/api/chat/threads?archived=true").decode<ThreadList>().threads
    }
    // Whether the server's chat model is installed: the first default place.
    val models = rememberApi("chat-models") {
        it.api.get("/api/chat/models").decode<ChatModelsInfo>()
    }
    return ServerChatPlace(app, toast, threads, archived, serverHasChatModel(models.data))
}

private class ServerChatPlace(
    private val app: AppContainer,
    private val toast: (String) -> Unit,
    private val threads: ApiState<List<ThreadRow>>,
    private val archivedThreads: ApiState<List<ThreadRow>>,
    override val ready: Boolean?,
) : ChatPlace {
    override val kind = ChatPlaceKind.Server

    private val rows = threads.data.orEmpty() + archivedThreads.data.orEmpty()

    override val chats: List<ChatRow> = rows.map { it.toChatRow() }

    // Loaded once the list answered (or failed: an empty list is still an
    // answer, and a new chat starts).
    override val loaded: Boolean = !threads.loading

    override fun refresh() {
        threads.refresh()
        archivedThreads.refresh()
    }

    // F-A010: destructive, so it is confirmed first (web chat.jsx parity);
    // the household's, so it says whose it is.
    override fun deleteText(row: ChatRow): String = "Delete \"${row.title}\"? $SERVER_DELETE_NOTE This can't be undone."

    override fun delete(row: ChatRow) {
        app.scope.launch {
            runCatching { app.api.delete("/api/chat/threads/${row.id}") }
                .onFailure { toast(failureText("delete", it)) }
            refresh()
        }
    }

    override fun rename(row: ChatRow, title: String) {
        val name = title.trim()
        if (name.isEmpty()) return
        app.scope.launch {
            runCatching { app.api.patch("/api/chat/threads/${row.id}", buildJsonObject { put("title", name) }) }
                .onFailure { toast(failureText("rename", it)) }
            refresh()
        }
    }

    override fun setArchived(row: ChatRow, archived: Boolean) {
        app.scope.launch {
            runCatching { app.api.patch("/api/chat/threads/${row.id}", buildJsonObject { put("archived", archived) }) }
                .onSuccess { toast(if (archived) "archived" else "moved back to chats") }
                .onFailure { toast(failureText(if (archived) "archive" else "move back", it)) }
            refresh()
        }
    }

    // A failure is thrown: the list says it couldn't search, not "no chats
    // match".
    override suspend fun search(query: String): List<ChatRow> =
        app.api.get(threadSearchPath(query)).decode<ThreadList>().threads.map { it.toChatRow() }

    override suspend fun shareText(row: ChatRow): String? = runCatching {
        val messages = app.api.get("/api/chat/threads/${row.id}/messages").decode<MessageList>().messages
        serverChatText(row.title, messages)
    }.getOrNull()

    @Composable
    override fun Conversation(row: ChatRow?, frame: ChatFrame) {
        val thread = row?.let { r ->
            rows.firstOrNull { it.id.toString() == r.id }
                ?: r.id.toLongOrNull()?.let { ThreadRow(id = it, title = r.title) }
        }
        // The title as the list has it now (the model's title arrives after
        // the first reply; a rename on another device shows too).
        val titleOf = { id: Long? -> id?.let { i -> rows.firstOrNull { it.id == i }?.title } }
        ConversationPane(thread, frame, titleOf = titleOf, onCreated = { threads.refresh() })
    }
}
