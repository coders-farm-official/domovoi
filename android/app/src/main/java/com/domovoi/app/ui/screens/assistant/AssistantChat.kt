package com.domovoi.app.ui.screens.assistant

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalToast
import com.domovoi.app.assistant.AssistantUi
import com.domovoi.app.assistant.ConversationSummary
import com.domovoi.app.assistant.EngineState
import com.domovoi.app.assistant.LlmBackend
import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.assistant.StoredMessage
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.MarkdownText
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtBytes
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import java.time.Instant
import java.util.Locale

private enum class Pane { Chat, History, Model }

/** The composer's text field. A local so the JVM tests, which have no
 *  Android resources for Material's text field, can put a plain one in. */
internal val LocalComposerField = staticCompositionLocalOf<@Composable (String, (String) -> Unit, Modifier) -> Unit> {
    { value, onChange, modifier ->
        OutlinedTextField(
            value = value,
            onValueChange = onChange,
            placeholder = { Text("ask domovoi on this phone…") },
            modifier = modifier,
            maxLines = 4,
        )
    }
}

/**
 * The text chat with the model on the phone (M1 is text only; voice is M2):
 * the conversation, newest at the bottom, replies rendered with the server
 * chat's MarkdownText and its bubble look, a composer that turns into a stop
 * button while a reply is written. The title bar opens the conversations on
 * this phone (open one, delete one, clear all) and the model's settings
 * (CPU or GPU, delete the model).
 */
@Composable
internal fun AssistantChat(ui: AssistantUi, actions: AssistantActions) {
    var pane by rememberSaveable { mutableStateOf(Pane.Chat) }
    when (pane) {
        Pane.History -> HistoryPane(ui.history, ui.conversation.id, actions, onClose = { pane = Pane.Chat })
        Pane.Model -> ModelPane(ui, actions, onClose = { pane = Pane.Chat })
        Pane.Chat -> ChatPane(ui, actions, onHistory = { pane = Pane.History }, onModel = { pane = Pane.Model })
    }
}

@Composable
private fun ChatPane(ui: AssistantUi, actions: AssistantActions, onHistory: () -> Unit, onModel: () -> Unit) {
    val toast = LocalToast.current
    var draft by rememberSaveable { mutableStateOf("") }
    val messages = ui.conversation.messages
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        AssistantTitle(Modifier.padding(top = 8.dp)) {
            IconButton(onClick = onHistory) {
                Icon(Icons.Outlined.History, contentDescription = "conversations", tint = Domovoi.colors.fgMuted)
            }
            IconButton(onClick = {
                actions.newConversation()
                toast("new conversation")
            }, enabled = messages.isNotEmpty()) {
                Icon(Icons.Outlined.Add, contentDescription = "new conversation",
                    tint = if (messages.isNotEmpty()) Domovoi.colors.fgMuted else Domovoi.colors.fgFaint)
            }
            IconButton(onClick = onModel) {
                Icon(Icons.Outlined.Tune, contentDescription = "model settings", tint = Domovoi.colors.fgMuted)
            }
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            if (messages.isEmpty()) {
                Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
                    EmptyState(
                        "Ask Domovoi anything",
                        "Answers come from a model running on this phone, with no server and no internet. " +
                            "Conversations stay on this phone; Chat, which talks to your Domovoi server, is separate.",
                    )
                }
            } else {
                // Newest at the bottom, laid out from the bottom: a reply
                // growing as it streams, or the keyboard opening, keeps the
                // newest words in view, and a reader scrolled up stays put.
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    reverseLayout = true,
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    val reversed = messages.asReversed()
                    itemsIndexed(reversed) { i, m ->
                        MessageBubble(m, writing = i == 0 && ui.replying && !m.fromUser)
                    }
                }
            }
        }
        EngineLine(ui, actions, onModel)
        Row(
            Modifier.fillMaxWidth().padding(top = 6.dp, bottom = 10.dp),
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            LocalComposerField.current(draft, { draft = it }, Modifier.weight(1f))
            if (ui.replying) {
                IconButton(onClick = actions.stop) {
                    Icon(Icons.Filled.Stop, contentDescription = "stop", tint = Domovoi.colors.brand)
                }
            } else {
                val canSend = draft.isNotBlank()
                IconButton(onClick = {
                    actions.send(draft)
                    draft = ""
                }, enabled = canSend) {
                    Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "send",
                        tint = if (canSend) Domovoi.colors.brand else Domovoi.colors.fgFaint)
                }
            }
        }
    }
}

/** Above the composer: the model loading, failing, or thinking. */
@Composable
private fun EngineLine(ui: AssistantUi, actions: AssistantActions, onModel: () -> Unit) {
    when (val e = ui.engine) {
        is EngineState.Loading -> Row(Modifier.padding(top = 6.dp)) {
            Pill("loading the model on the ${e.backend.name.lowercase()}", Tone.Brand, live = true)
        }
        is EngineState.Failed -> Column(Modifier.padding(top = 6.dp)) {
            Text(
                "The model could not start on the ${e.backend.name.lowercase()}: ${e.reason}" +
                    if (e.backend == LlmBackend.GPU) ". This phone's graphics driver may not run it; the CPU does." else "",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.err,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TextButton(onClick = actions.retryLoad) { Text("retry", color = Domovoi.colors.brand) }
                if (e.backend == LlmBackend.GPU) {
                    TextButton(onClick = { actions.setBackend(LlmBackend.CPU) }) { Text("use the cpu", color = Domovoi.colors.brand) }
                }
                TextButton(onClick = onModel) { Text("model settings", color = Domovoi.colors.fgMuted) }
            }
        }
        else -> if (ui.thinking) {
            Row(Modifier.padding(top = 6.dp)) { Pill("thinking", Tone.Brand, live = true) }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun MessageBubble(m: StoredMessage, writing: Boolean) {
    val toast = LocalToast.current
    val clipboard = LocalClipboardManager.current
    val haptics = LocalHapticFeedback.current
    var menu by remember { mutableStateOf(false) }
    val isUser = m.fromUser
    Column(Modifier.fillMaxWidth(), horizontalAlignment = if (isUser) Alignment.End else Alignment.Start) {
        Box {
            Box(
                Modifier.widthIn(max = 480.dp)
                    .background(if (isUser) Domovoi.colors.card else Domovoi.colors.canvas, RoundedCornerShape(10.dp))
                    .then(if (isUser) Modifier.border(1.dp, Domovoi.colors.border, RoundedCornerShape(10.dp)) else Modifier)
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
                    if (isUser) {
                        Text(m.text, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                    } else if (m.text.isNotEmpty() || writing) {
                        MarkdownText(m.text, trailing = if (writing) " ▍" else "")
                    }
                    m.error?.let {
                        Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.err,
                            modifier = Modifier.padding(top = 4.dp))
                    }
                    if (m.stopped) {
                        Text("stopped", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint,
                            modifier = Modifier.padding(top = 2.dp))
                    }
                }
            }
            DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                DropdownMenuItem(text = { Text("copy") }, onClick = {
                    clipboard.setText(AnnotatedString(m.text))
                    toast("copied")
                    menu = false
                })
                replyDetails(m)?.let { details ->
                    DropdownMenuItem(text = { Text(details, color = Domovoi.colors.fgMuted) }, onClick = { menu = false }, enabled = false)
                }
            }
        }
    }
}

/** "0.45 s to first words · 30.2 tokens/s · cpu": how a reply went. */
internal fun replyDetails(m: StoredMessage): String? {
    if (m.fromUser) return null
    val parts = listOfNotNull(
        m.firstTextMs?.let { String.format(Locale.US, "%.2f s to first words", it / 1000.0) },
        m.tokensPerS?.let { String.format(Locale.US, "%.1f tokens/s", it) },
        m.backend,
    )
    return parts.takeIf { it.isNotEmpty() }?.joinToString(" · ")
}

/** The conversations on this phone: open one, delete one, clear all. */
@Composable
private fun HistoryPane(history: List<ConversationSummary>, currentId: String, actions: AssistantActions, onClose: () -> Unit) {
    BackHandler(onBack = onClose)
    var confirmClear by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf<ConversationSummary?>(null) }
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        PaneTitle("Conversations on this phone", onClose)
        Text(
            "Kept only on this phone, outside backups. They never go to a Domovoi server.",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgMuted,
        )
        Spacer(Modifier.height(8.dp))
        if (history.isEmpty()) {
            EmptyState("No conversations yet", "Ask something and it is kept here.")
        } else {
            LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                items(history, key = { it.id }) { c ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .background(
                                if (c.id == currentId) Domovoi.colors.brandSoft else Domovoi.colors.card,
                                RoundedCornerShape(8.dp),
                            )
                            .clickable {
                                actions.open(c.id)
                                onClose()
                            }
                            .padding(start = 12.dp, top = 4.dp, bottom = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(c.title, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg,
                                maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text("${c.messages} messages · ${relTime(Instant.ofEpochMilli(c.updated).toString())}",
                                style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
                        }
                        IconButton(onClick = { confirmDelete = c }) {
                            Icon(Icons.Outlined.DeleteOutline, contentDescription = "delete conversation", tint = Domovoi.colors.fgMuted)
                        }
                    }
                }
            }
            TextButton(onClick = { confirmClear = true }, modifier = Modifier.padding(vertical = 6.dp)) {
                Text("clear all conversations", color = Domovoi.colors.err)
            }
        }
    }
    if (confirmClear) {
        ConfirmDialog(
            title = "clear all conversations?",
            body = "Deletes every conversation kept on this phone (${history.size}). This can't be undone.",
            confirmLabel = "clear all",
            destructive = true,
            onConfirm = actions.clearAll,
            onDismiss = { confirmClear = false },
        )
    }
    confirmDelete?.let { c ->
        ConfirmDialog(
            title = "delete this conversation?",
            body = "\"${c.title}\" is deleted from this phone. This can't be undone.",
            confirmLabel = "delete",
            destructive = true,
            onConfirm = { actions.delete(c.id) },
            onDismiss = { confirmDelete = null },
        )
    }
}

/** The model on this phone: where it runs, its size, delete it. */
@Composable
private fun ModelPane(ui: AssistantUi, actions: AssistantActions, onClose: () -> Unit) {
    BackHandler(onBack = onClose)
    var confirmDelete by remember { mutableStateOf(false) }
    var confirmClear by remember { mutableStateOf(false) }
    val model = ModelCatalog.chatModel
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        PaneTitle("The model on this phone", onClose)
        DomovoiCard(Modifier.fillMaxWidth()) {
            Text(model.name, style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
            Text("${model.kind.label} · ${fmtBytes(model.totalBytes)} on the phone · ${model.license}",
                style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
            Text(engineSummary(ui.engine), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(top = 6.dp))
            ui.lastDownloadMs?.let {
                Text("Downloaded and checked in ${durationText(it)} (from the last start or resume).", style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted)
            }
            Spacer(Modifier.height(10.dp))
            SectionLabel("runs on")
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                LlmBackend.entries.forEach { b ->
                    FilterChip(
                        selected = ui.backend == b,
                        onClick = { actions.setBackend(b) },
                        label = { Text(if (b == LlmBackend.CPU) "cpu (default)" else "gpu") },
                    )
                }
            }
            Text(
                "The CPU works on every phone. The GPU answers faster on many phones but takes longer to load, " +
                    "uses about 1.5 GB more memory, and doesn't work on every phone's graphics driver.",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgSubtle,
            )
            Spacer(Modifier.height(10.dp))
            OutlinedButton(onClick = { confirmDelete = true }) { Text("delete model", color = Domovoi.colors.err) }
        }
        DomovoiCard(Modifier.fillMaxWidth()) {
            Text("Conversations", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
            Text(
                "${ui.history.size} kept on this phone, in the app's private storage and outside backups. The model " +
                    "sees the conversation you are in and nothing else on the phone: no files, media, contacts or location.",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
            )
            if (ui.history.isNotEmpty()) {
                TextButton(onClick = { confirmClear = true }) { Text("clear all conversations", color = Domovoi.colors.err) }
            }
        }
    }
    if (confirmDelete) {
        ConfirmDialog(
            title = "delete the model?",
            body = "Frees ${fmtBytes(model.totalBytes)} on this phone. The assistant can't answer without it; you can " +
                "download it again. Your conversations stay.",
            confirmLabel = "delete model",
            destructive = true,
            onConfirm = actions.deleteModel,
            onDismiss = { confirmDelete = false },
        )
    }
    if (confirmClear) {
        ConfirmDialog(
            title = "clear all conversations?",
            body = "Deletes every conversation kept on this phone (${ui.history.size}). This can't be undone.",
            confirmLabel = "clear all",
            destructive = true,
            onConfirm = actions.clearAll,
            onDismiss = { confirmClear = false },
        )
    }
}

@Composable
private fun PaneTitle(title: String, onBack: () -> Unit) {
    Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        IconButton(onClick = onBack) {
            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "back", tint = Domovoi.colors.fg)
        }
        Text(title, style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
    }
}

internal fun engineSummary(engine: EngineState): String = when (engine) {
    EngineState.Unloaded -> "Not in memory now; it loads when you open the assistant."
    is EngineState.Loading -> "Loading on the ${engine.backend.name}…"
    is EngineState.Ready -> String.format(Locale.US, "In memory, on the %s; loaded in %.1f s.", engine.backend.name, engine.loadMs / 1000.0)
    is EngineState.Failed -> "Could not start on the ${engine.backend.name}: ${engine.reason}"
}

internal fun durationText(ms: Long): String {
    val s = ms / 1000
    return when {
        s < 60 -> "$s s"
        s < 3600 -> "${s / 60} min ${s % 60} s"
        else -> "${s / 3600} h ${(s % 3600) / 60} min"
    }
}
