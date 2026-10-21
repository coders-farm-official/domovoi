package com.domovoi.app.ui.screens.assistant

import androidx.activity.compose.BackHandler
import com.domovoi.app.assistant.docs.PDF_NO_API
import com.domovoi.app.assistant.StoredConversation
import com.domovoi.app.ui.screens.chat.DocMarker
import com.domovoi.app.ui.screens.chat.DocProblemNote
import com.domovoi.app.ui.screens.chat.PendingPdfCard
import com.domovoi.app.ui.screens.chat.ReadingLine
import com.domovoi.app.ui.screens.chat.ReplyDocs
import com.domovoi.app.ui.screens.chat.SourceSheet
import com.domovoi.app.ui.screens.chat.SourceView
import com.domovoi.app.ui.screens.chat.DocAttachButton
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.foundation.selection.toggleable
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalToast
import com.domovoi.app.assistant.AssistantUi
import com.domovoi.app.assistant.EngineState
import com.domovoi.app.assistant.LlmBackend
import com.domovoi.app.assistant.ModelSetup
import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.assistant.ReplyReport
import com.domovoi.app.assistant.StoredMessage
import com.domovoi.app.assistant.VoicePhase
import com.domovoi.app.data.LocalDeveloperOptionsOn
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.MarkdownText
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtBytes
import com.domovoi.app.ui.screens.chat.COMPOSER_HINT
import com.domovoi.app.ui.screens.chat.ChatHeader
import com.domovoi.app.ui.screens.chat.ChatPlaceKind
import com.domovoi.app.ui.screens.chat.ACTION_SHARE
import com.domovoi.app.ui.screens.chat.ACTION_TRY_AGAIN
import com.domovoi.app.ui.screens.chat.NEW_CHAT
import com.domovoi.app.ui.screens.chat.ShareChatButton
import com.domovoi.app.ui.screens.chat.WindowDivider
import com.domovoi.app.assistant.retryBase
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.serialization.json.Json
import java.util.Locale

private enum class Pane { Chat, Model, Report, Reports }

/** A report being written survives a rotation as its JSON. */
private val reportSaver = Saver<ReplyReport?, String>(
    save = { r -> r?.let { Json.encodeToString(ReplyReport.serializer(), it) } ?: "" },
    restore = { s -> if (s.isEmpty()) null else runCatching { Json.decodeFromString(ReplyReport.serializer(), s) }.getOrNull() },
)

/** The composer's text field. A local so the JVM tests, which have no
 *  Android resources for Material's text field, can put a plain one in. */
internal val LocalComposerField = staticCompositionLocalOf<@Composable (String, (String) -> Unit, Modifier) -> Unit> {
    { value, onChange, modifier ->
        OutlinedTextField(
            value = value,
            onValueChange = onChange,
            placeholder = { Text(COMPOSER_HINT) },
            modifier = modifier,
            maxLines = 4,
        )
    }
}

/**
 * A chat on this phone (Chat's phone place): the conversation, newest at
 * the bottom, replies rendered with the server chat's MarkdownText and its
 * bubble look, a composer that turns into a stop button while a reply is
 * written. Chat's header leads back to the history list (where phone and
 * home-server chats are listed together, and deleted); its one action opens
 * the model's settings (CPU or GPU, delete the model, clear all phone
 * conversations, the saved reports).
 */
@Composable
internal fun AssistantChat(ui: AssistantUi, actions: AssistantActions, chrome: PhoneChatChrome = PhoneChatChrome()) {
    var pane by rememberSaveable { mutableStateOf(Pane.Chat) }
    var reporting by rememberSaveable(stateSaver = reportSaver) { mutableStateOf<ReplyReport?>(null) }
    val report = reporting
    when {
        pane == Pane.Model -> ModelPane(ui, actions, onClose = { pane = Pane.Chat }, onReports = { pane = Pane.Reports })
        pane == Pane.Reports -> ReportsPane(ui.reports, actions, onClose = { pane = Pane.Model })
        pane == Pane.Report && report != null -> ReportPane(report, actions, onClose = {
            reporting = null
            pane = Pane.Chat
        })
        else -> ChatPane(ui, actions, chrome, onModel = { pane = Pane.Model }, onReport = { index ->
            actions.draftReport(ui.conversation, index)?.let {
                reporting = it
                pane = Pane.Report
            }
        })
    }
}

@Composable
private fun ChatPane(
    ui: AssistantUi,
    actions: AssistantActions,
    chrome: PhoneChatChrome,
    onModel: () -> Unit,
    onReport: (Int) -> Unit,
) {
    val toast = LocalToast.current
    var draft by rememberSaveable { mutableStateOf("") }
    var offerSpeechFiles by remember { mutableStateOf(false) }
    var openSource by remember { mutableStateOf<SourceView?>(null) }
    val messages = ui.conversation.messages
    // The paperclip: a document for this phone chat (documents plan, D1),
    // copied into the app's storage and read on the phone. What's typed
    // goes with it as the question.
    val attach = actions.attachDocument
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        ChatHeader(
            if (messages.isEmpty()) NEW_CHAT else ui.conversation.title,
            chrome.chip, ChatPlaceKind.Phone, chrome.onBack, Modifier.padding(top = 8.dp),
        ) {
            if (messages.isNotEmpty() && !ui.replying) ShareChatButton(actions.shareChat)
            IconButton(onClick = onModel) {
                Icon(Icons.Outlined.Tune, contentDescription = "model settings", tint = Domovoi.colors.fgMuted)
            }
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            if (messages.isEmpty()) {
                Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
                    EmptyState(
                        "Ask Domovoi anything",
                        PHONE_EMPTY_HINT,
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
                    val canRetry = phoneCanRetry(ui)
                    itemsIndexed(reversed) { i, m ->
                        val writing = i == 0 && ui.replying && !m.fromUser
                        val index = messages.size - 1 - i
                        Column {
                            // Above the first message the model still sees.
                            if (index == ui.conversation.windowFrom && index > 0) WindowDivider()
                            MessageBubble(
                                m,
                                writing = writing,
                                onReport = if (!m.fromUser && !writing && m.text.isNotBlank()) ({ onReport(index) }) else null,
                                onRetry = if (i == 0 && canRetry) actions.retry else null,
                                onShare = if (!writing && m.text.isNotBlank()) ({ actions.shareMessage(m.text) }) else null,
                                conversation = ui.conversation,
                                docFile = actions.docFile,
                                onSource = { openSource = it },
                            )
                        }
                    }
                }
            }
        }
        // Nothing said yet: the place can still change (Chat's picker).
        if (messages.isEmpty() && ui.voicePhase == VoicePhase.Idle && !ui.replying) chrome.picker?.invoke()
        if (ui.voicePhase != VoicePhase.Idle) VoiceLine(ui) else EngineLine(ui, actions, onModel)
        ui.reading?.let { ReadingLine(it) }
        ui.docProblem?.let { DocProblemNote(it, onClose = actions.closeDocProblem) }
        ui.pendingPdf?.let { p ->
            PendingPdfCard(
                p.name, PDF_NO_API, canAskServer = actions.askServer != null, busy = actions.askingServer,
                onAsk = { actions.askServer?.invoke(p) }, onDrop = actions.dropPending,
            )
        }
        openSource?.let { SourceSheet(it, onClose = { openSource = null }) }
        if (offerSpeechFiles) SpeechFilesDialog(ui, actions) { offerSpeechFiles = false }
        Row(
            Modifier.fillMaxWidth().padding(top = 6.dp, bottom = 10.dp),
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            if (attach != null && ui.voicePhase == VoicePhase.Idle && !ui.replying) {
                DocAttachButton(enabled = ui.reading == null) { name, open ->
                    draft = attachWithDraft(attach, name, open, draft)
                }
            }
            LocalComposerField.current(draft, { draft = it }, Modifier.weight(1f))
            if (ui.voicePhase != VoicePhase.Idle) {
                // One button for the turn: while it listens and has heard
                // you, it sends what you said; otherwise it stops it all.
                val done = ui.voicePhase == VoicePhase.Listening && ui.heardSpeech
                IconButton(onClick = actions.talk) {
                    Icon(if (done) Icons.Filled.Check else Icons.Filled.Stop,
                        contentDescription = if (done) "done speaking" else "stop", tint = Domovoi.colors.brand)
                }
            } else if (ui.replying) {
                IconButton(onClick = actions.stop) {
                    Icon(Icons.Filled.Stop, contentDescription = "stop", tint = Domovoi.colors.brand)
                }
            } else if (draft.isBlank()) {
                // Nothing typed: the mic.
                IconButton(onClick = {
                    when (val v = ui.voiceSetup) {
                        ModelSetup.Installed -> actions.talk()
                        is ModelSetup.Downloading ->
                            toast("the speech files are downloading: ${progressLine(v.bytesDone, ui.voiceBytes)}")
                        else -> offerSpeechFiles = true
                    }
                }) {
                    Icon(Icons.Filled.Mic, contentDescription = "talk", tint = Domovoi.colors.brand)
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

/** Before the first spoken turn on a phone set up for text only: the
 *  speech files, cost first. */
@Composable
private fun SpeechFilesDialog(ui: AssistantUi, actions: AssistantActions, onDismiss: () -> Unit) {
    ConfirmDialog(
        title = "download the speech files?",
        body = "Talking to Domovoi on this phone needs Moonshine (speech to text) and Silero (end of speech), " +
            "${fmtBytes(ui.voiceBytes)}, downloaded once" +
            (if (ui.allowMobileData) "." else " over Wi-Fi.") +
            " Your voice is heard on the phone only; the audio is never kept.",
        confirmLabel = "download (${fmtBytes(ui.voiceBytes)})",
        onConfirm = actions.downloadVoice,
        onDismiss = onDismiss,
    )
}

/** Above the composer during a spoken turn: what it is doing now. */
@Composable
private fun VoiceLine(ui: AssistantUi) {
    val (pill, hint) = voiceLine(ui.voicePhase, ui.heardSpeech)
    // Read out as it changes (listening, transcribing, thinking, speaking):
    // a screen reader user hears where the turn is without looking.
    Row(
        Modifier.padding(top = 6.dp).semantics(mergeDescendants = true) { liveRegion = LiveRegionMode.Polite },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Pill(pill, if (ui.voicePhase == VoicePhase.Listening) Tone.Warn else Tone.Brand, live = true)
        hint?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted) }
    }
}

/** The pill and the hint for a phase. */
internal fun voiceLine(phase: VoicePhase, heardSpeech: Boolean): Pair<String, String?> = when (phase) {
    VoicePhase.Listening -> if (heardSpeech) "hearing you" to "stop talking, or tap done" else "listening" to "ask your question"
    VoicePhase.Transcribing -> "transcribing" to null
    VoicePhase.Thinking -> "thinking" to "tap stop to cancel"
    VoicePhase.Speaking -> "speaking" to "tap stop to silence it"
    VoicePhase.Idle -> "" to null
}

/** Above the composer: the model loading, failing, or thinking. */
@Composable
private fun EngineLine(ui: AssistantUi, actions: AssistantActions, onModel: () -> Unit) {
    when (val e = ui.engine) {
        is EngineState.Loading -> Row(Modifier.padding(top = 6.dp).semantics { liveRegion = LiveRegionMode.Polite }) {
            Pill("loading the model on the ${e.backend.name.lowercase()}", Tone.Brand, live = true)
        }
        is EngineState.Failed -> Column(Modifier.padding(top = 6.dp).semantics { liveRegion = LiveRegionMode.Polite }) {
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
        else -> if (ui.checking) {
            Row(Modifier.padding(top = 6.dp).semantics { liveRegion = LiveRegionMode.Polite }) {
                Pill("reading the passages and checking the answer", Tone.Brand, live = true)
            }
        } else if (ui.thinking) {
            Row(Modifier.padding(top = 6.dp).semantics { liveRegion = LiveRegionMode.Polite }) { Pill("thinking", Tone.Brand, live = true) }
        }
    }
}

/**
 * One message. Long-press opens its actions (copy; on a finished reply,
 * "report this reply") and, with developer options on, the reply's timings
 * ([replyDetails]); a screen reader gets the actions as custom actions, and
 * hears who said it ([bubbleState]).
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun MessageBubble(
    m: StoredMessage,
    writing: Boolean,
    onReport: (() -> Unit)? = null,
    onRetry: (() -> Unit)? = null,
    onShare: (() -> Unit)? = null,
    conversation: StoredConversation? = null,
    docFile: (String) -> java.io.File? = { null },
    onSource: (SourceView) -> Unit = {},
) {
    val toast = LocalToast.current
    val clipboard = LocalClipboardManager.current
    val haptics = LocalHapticFeedback.current
    var menu by remember { mutableStateOf(false) }
    val isUser = m.fromUser
    val copy = {
        clipboard.setText(AnnotatedString(m.text))
        toast("copied")
    }
    Column(Modifier.fillMaxWidth(), horizontalAlignment = if (isUser) Alignment.End else Alignment.Start) {
        // An attachment's marker: the document is not part of the
        // conversation's text, only this line (plan part 10).
        m.documents.mapNotNull { id -> conversation?.documents?.firstOrNull { it.id == id } }.forEach { d ->
            DocMarker(d.summary, d.notes.map { it.text }, Modifier.padding(bottom = 4.dp))
        }
        if (isUser && m.text.isBlank() && m.documents.isNotEmpty()) return@Column
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
                    .semantics {
                        stateDescription = bubbleState(isUser, writing)
                        customActions = bubbleActions(onReport != null, onShare != null, onRetry != null).map { label ->
                            CustomAccessibilityAction(label) {
                                when (label) {
                                    ACTION_COPY -> copy()
                                    ACTION_SHARE -> onShare?.invoke()
                                    ACTION_TRY_AGAIN -> onRetry?.invoke()
                                    else -> onReport?.invoke()
                                }
                                true
                            }
                        }
                    }
                    .padding(horizontal = if (isUser) 12.dp else 0.dp, vertical = if (isUser) 8.dp else 2.dp),
            ) {
                Column {
                    if (isUser && m.text.isNotBlank()) {
                        Text(m.text, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                        if (m.spoken) {
                            Text("spoken", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint,
                                modifier = Modifier.padding(top = 2.dp))
                        }
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
                    if (!isUser && !writing) {
                        ReplyDocs(m.notes.map { it.text }, phoneSources(m, conversation, docFile), onSource)
                    }
                }
            }
            DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                DropdownMenuItem(text = { Text(ACTION_COPY) }, onClick = {
                    copy()
                    menu = false
                })
                onShare?.let { share ->
                    DropdownMenuItem(text = { Text(ACTION_SHARE) }, onClick = {
                        menu = false
                        share()
                    })
                }
                onRetry?.let { again ->
                    DropdownMenuItem(text = { Text(ACTION_TRY_AGAIN) }, onClick = {
                        menu = false
                        again()
                    })
                }
                onReport?.let { report ->
                    DropdownMenuItem(text = { Text(ACTION_REPORT) }, onClick = {
                        menu = false
                        report()
                    })
                }
                if (LocalDeveloperOptionsOn.current) replyDetails(m)?.let { details ->
                    DropdownMenuItem(text = { Text(details, color = Domovoi.colors.fgMuted) }, onClick = { menu = false }, enabled = false)
                }
            }
        }
    }
}

/** A phone reply's sources as the shared chips show them; a PDF's page is
 *  drawn from the phone's own copy ([docFile], by document id). */
internal fun phoneSources(m: StoredMessage, conversation: StoredConversation?, docFile: (String) -> java.io.File?): List<SourceView> =
    m.sources.map { s ->
        val doc = conversation?.documents?.getOrNull(s.doc)
        SourceView(
            s.label, s.text, s.match, s.cited, s.page,
            pdf = if (doc?.kind == "pdf" && s.page != null) ({ docFile(doc.id) }) else null,
        )
    }

/** An empty phone chat: where the answers come from, and where the chat
 *  stays. Home-server chats are a different place, never mixed in. */
internal const val PHONE_EMPTY_HINT =
    "Type, or tap the mic and speak. Answers come from a model running on this phone, with no server and no " +
        "internet, and this chat stays on this phone."

internal const val ACTION_COPY = "copy"
internal const val ACTION_REPORT = "report this reply"

/** What a screen reader hears after a message's words: who said it. */
internal fun bubbleState(fromUser: Boolean, writing: Boolean): String = when {
    fromUser -> "your message"
    writing -> "Domovoi, writing"
    else -> "Domovoi's reply"
}

/** A message's actions, as its long-press menu and a screen reader's
 *  custom actions list them: report only on a finished reply with words;
 *  share on a message with words; try again on the last finished reply. */
internal fun bubbleActions(canReport: Boolean, canShare: Boolean = false, canRetry: Boolean = false): List<String> =
    listOfNotNull(
        ACTION_COPY,
        ACTION_SHARE.takeIf { canShare },
        ACTION_TRY_AGAIN.takeIf { canRetry },
        ACTION_REPORT.takeIf { canReport },
    )

/** "Try again" works on the phone chat now: it ends with a finished reply
 *  to a question, and nothing is being written or said. */
internal fun phoneCanRetry(ui: AssistantUi): Boolean =
    !ui.replying && ui.voicePhase == VoicePhase.Idle && ui.installed && retryBase(ui.conversation) != null

/** "0.45 s to first words · 30.2 tokens/s · cpu": how a reply went; a clock
 *  answer names the phone's clock instead, with no model numbers. */
internal fun replyDetails(m: StoredMessage): String? {
    if (m.fromUser) return null
    val parts = listOfNotNull(
        m.voiceMs?.let {
            String.format(Locale.US, "%.2f s from end of speech to first word (+%.1f s silence wait)", it / 1000.0,
                (m.silenceWaitMs ?: 0) / 1000.0)
        },
        m.sttMs?.let { "heard in $it ms" },
        m.firstTextMs?.let { String.format(Locale.US, "%.2f s to first words", it / 1000.0) },
        // The clock's answer: no model wrote it, so no model numbers.
        m.tokensPerS?.takeUnless { m.fromClock }?.let { String.format(Locale.US, "%.1f tokens/s", it) },
        if (m.fromClock) ReplyReport.CLOCK else m.backend,
        m.stopMs?.let { "silenced $it ms after stop" },
    )
    return parts.takeIf { it.isNotEmpty() }?.joinToString(" · ")
}

/** The model on this phone: where it runs, its size, delete it. */
@Composable
private fun ModelPane(ui: AssistantUi, actions: AssistantActions, onClose: () -> Unit, onReports: () -> Unit) {
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
            Text(model.name, style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg, modifier = Modifier.asHeading())
            Text("${model.kind.label} · ${fmtBytes(model.totalBytes)} on the phone · ${model.license}" +
                " (with the speech files: ${fmtBytes(ui.totalBytes)})",
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
            Text("Speech", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg, modifier = Modifier.asHeading())
            Text(speechSummary(ui), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            if (!ui.voiceReady && ui.voiceSetup !is ModelSetup.Downloading &&
                ui.voiceSetup != ModelSetup.Checking
            ) {
                TextButton(onClick = actions.downloadVoice) {
                    Text("download the speech files (${fmtBytes(ui.voiceBytes)})", color = Domovoi.colors.brand)
                }
            }
            Spacer(Modifier.height(6.dp))
            // One control for a screen reader: the row toggles, labelled by its text.
            Row(
                Modifier.toggleable(value = ui.speakTyped, role = Role.Switch, onValueChange = actions.setSpeakTyped),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Switch(checked = ui.speakTyped, onCheckedChange = null)
                Column(Modifier.padding(start = 10.dp)) {
                    Text("read typed replies aloud too", style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.fg)
                    Text(
                        "Replies to spoken questions are always spoken. Off: replies to typed ones stay silent.",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                    )
                }
            }
            // A measurement for developers (data/DeveloperOptions.kt).
            if (LocalDeveloperOptionsOn.current) ui.lastStopMs?.let {
                Text("Last stop: silent $it ms after the tap.", style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgSubtle, modifier = Modifier.padding(top = 4.dp))
            }
        }
        DomovoiCard(Modifier.fillMaxWidth()) {
            Text("Conversations", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg, modifier = Modifier.asHeading())
            Text(
                "${ui.history.size} kept on this phone, in the app's private storage and outside backups. The model " +
                    "sees the conversation you are in and nothing else on the phone: no files, media, contacts or location.",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
            )
            if (ui.history.isNotEmpty()) {
                // Markdown plus the JSON, in one zip, to a file the person
                // picks; nothing leaves the phone any other way.
                TextButton(onClick = actions.exportAll) { Text("export all phone chats", color = Domovoi.colors.brand) }
                TextButton(onClick = { confirmClear = true }) { Text("clear all conversations", color = Domovoi.colors.err) }
            }
        }
        DomovoiCard(Modifier.fillMaxWidth()) {
            Text("Reports", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg, modifier = Modifier.asHeading())
            Text(reportsSummary(ui.reports.size), style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            if (ui.reports.isNotEmpty()) {
                TextButton(onClick = onReports) { Text("show reports", color = Domovoi.colors.brand) }
            }
        }
    }
    if (confirmDelete) {
        ConfirmDialog(
            title = "delete the model?",
            body = "Frees ${fmtBytes(ui.totalBytes)} on this phone (the language model and the speech files). The " +
                "assistant can't answer without them; you can download them again. Your conversations stay.",
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
internal fun PaneTitle(title: String, onBack: () -> Unit) {
    Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        IconButton(onClick = onBack) {
            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "back", tint = Domovoi.colors.fg)
        }
        Text(title, style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg, modifier = Modifier.asHeading())
    }
}

/** The model settings' reports card: how many, and what they are. */
internal fun reportsSummary(count: Int): String = when (count) {
    0 -> "None saved. To report an offensive, harmful or wrong reply, long-press it and choose report this reply."
    1 -> "1 reply report saved on this phone, outside backups. It goes nowhere unless you share it."
    else -> "$count reply reports saved on this phone, outside backups. They go nowhere unless you share them."
}

/** What listens and what speaks, in a sentence or two. */
internal fun speechSummary(ui: AssistantUi): String {
    val listens = when (val v = ui.voiceSetup) {
        ModelSetup.Installed ->
            "Listens with Moonshine (speech to text) and Silero (end of speech), on the phone."
        is ModelSetup.Downloading ->
            "The speech files are downloading: ${progressLine(v.bytesDone, ui.voiceBytes)}."
        else -> "Talking needs the speech files (${fmtBytes(ui.voiceBytes)}), downloaded once."
    }
    val speaks = ui.voiceName?.let {
        if (it.startsWith("this phone")) " Speaks with $it; Domovoi's own voice is downloaded in Settings → Voices."
        else " Speaks with $it, the voice picked in Settings → Voices."
    } ?: " Speaks with the voice picked in Settings → Voices once it is downloaded, else the phone's own."
    return listens + speaks
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

/** The paperclip in a phone chat: the document goes with what is typed as
 *  its question (review round 1: the draft used to be dropped), and the
 *  box is cleared. Returns the new draft. */
internal fun attachWithDraft(
    attach: (String, () -> java.io.InputStream, String) -> Unit,
    name: String,
    open: () -> java.io.InputStream,
    draft: String,
): String {
    attach(name, open, draft.trim())
    return ""
}
