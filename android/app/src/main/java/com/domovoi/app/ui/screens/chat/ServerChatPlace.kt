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

/** A server thread as a row of Chat's list. */
internal fun ThreadRow.toChatRow(): ChatRow = ChatRow(
    place = ChatPlaceKind.Server,
    id = id.toString(),
    title = title ?: NEW_CHAT,
    snippet = last_snippet ?: "no messages yet",
    updatedMs = parseInstant(updated_at)?.toEpochMilli() ?: 0L,
    messages = message_count,
)

/**
 * The home server's chat as a Chat place: today's `/api/chat` threads, read
 * and deleted as the old thread list did, and [ConversationPane] (the REST
 * and SSE send, the server's voice view and photos) for a conversation.
 * Only offered while the server is in reach and allows chat
 * ([com.domovoi.app.net.Capabilities.aiEnabled]); the phone keeps no copy of these
 * threads.
 */
@Composable
internal fun rememberServerChatPlace(): ChatPlace {
    val app = LocalApp.current
    val toast = LocalToast.current
    val threads = rememberApi("chat-threads", eventTypes = setOf("chat.changed")) {
        it.api.get("/api/chat/threads").decode<ThreadList>().threads
    }
    // Whether the server's chat model is installed: the first default place.
    val models = rememberApi("chat-models") {
        it.api.get("/api/chat/models").decode<ChatModelsInfo>()
    }
    return ServerChatPlace(app, toast, threads, serverHasChatModel(models.data))
}

private class ServerChatPlace(
    private val app: AppContainer,
    private val toast: (String) -> Unit,
    private val threads: ApiState<List<ThreadRow>>,
    override val ready: Boolean?,
) : ChatPlace {
    override val kind = ChatPlaceKind.Server

    private val rows = threads.data.orEmpty()

    override val chats: List<ChatRow> = rows.map { it.toChatRow() }

    override fun refresh() = threads.refresh()

    // F-A010: destructive, so it is confirmed first (web chat.jsx parity).
    override fun deleteText(row: ChatRow): String = "Delete \"${row.title}\"? This can't be undone."

    override fun delete(row: ChatRow) {
        app.scope.launch {
            runCatching { app.api.delete("/api/chat/threads/${row.id}") }
                .onFailure { toast(failureText("delete", it)) }
            threads.refresh()
        }
    }

    @Composable
    override fun Conversation(row: ChatRow?, frame: ChatFrame) {
        val thread = row?.let { r ->
            rows.firstOrNull { it.id.toString() == r.id }
                ?: r.id.toLongOrNull()?.let { ThreadRow(id = it, title = r.title) }
        }
        ConversationPane(thread, frame, onCreated = { threads.refresh() })
    }
}
