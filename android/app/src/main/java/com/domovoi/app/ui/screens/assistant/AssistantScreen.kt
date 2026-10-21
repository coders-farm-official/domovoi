package com.domovoi.app.ui.screens.assistant

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.assistant.AssistantController
import com.domovoi.app.assistant.AssistantUi
import com.domovoi.app.assistant.DeviceTier
import com.domovoi.app.assistant.DeviceTiers
import com.domovoi.app.assistant.LlmBackend
import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.assistant.ModelSetup
import com.domovoi.app.assistant.ReplyReport
import com.domovoi.app.assistant.ReportOutcome
import com.domovoi.app.assistant.ReportSink
import com.domovoi.app.assistant.ReportSinks
import com.domovoi.app.assistant.StoredConversation
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.composeEmail
import com.domovoi.app.ui.components.openAppSettings
import com.domovoi.app.ui.components.shareText
import com.domovoi.app.ui.screens.chat.CHIP_PHONE
import com.domovoi.app.ui.screens.chat.ChatFrame
import com.domovoi.app.ui.screens.chat.ChatHeader
import com.domovoi.app.ui.screens.chat.ChatPlaceKind
import com.domovoi.app.ui.screens.chat.NEW_CHAT
import com.domovoi.app.ui.screens.chat.rememberExportPhoneChats
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/** What the assistant's screens can ask of it ([AssistantController] in the
 *  app; plain lambdas in the tests). */
internal class AssistantActions(
    val download: () -> Unit = {},
    val pauseDownload: () -> Unit = {},
    val cancelDownload: () -> Unit = {},
    val setAllowMobileData: (Boolean) -> Unit = {},
    val deleteModel: () -> Unit = {},
    val setBackend: (LlmBackend) -> Unit = {},
    val retryLoad: () -> Unit = {},
    val send: (String) -> Unit = {},
    val stop: () -> Unit = {},
    val newConversation: () -> Unit = {},
    val open: (String) -> Unit = {},
    val delete: (String) -> Unit = {},
    val clearAll: () -> Unit = {},
    /** The mic button (asks for the permission first, in the app). */
    val talk: () -> Unit = {},
    val downloadVoice: () -> Unit = {},
    val setSpeakTyped: (Boolean) -> Unit = {},
    /** Lookups (part 5, W1): the card's answer for the message at an index,
     *  the chip, "Always allow internet", and "Use your own search". */
    val decideLookup: (Int, String) -> Unit = { _, _ -> },
    val setChatInternet: (Boolean) -> Unit = {},
    val setAlwaysAllowInternet: (Boolean) -> Unit = {},
    val setOwnSearch: (com.domovoi.app.assistant.lookup.OwnSearch?) -> Unit = {},
    val testOwnSearch: suspend (com.domovoi.app.assistant.lookup.OwnSearch?) -> String = { "" },
    /** "report this reply": the report for the reply at an index of a
     *  conversation, before the person's note; null when it is not one. */
    val draftReport: (StoredConversation, Int) -> ReplyReport? = { c, i ->
        ReplyReport.draft(c, i, ModelCatalog.chatModel, "", System.currentTimeMillis())
    },
    /** Keeps a report on the phone. */
    val saveReport: (ReplyReport) -> Unit = {},
    /** Where a report can go beyond the phone ([ReportSinks]); a button each. */
    val reportSinks: List<ReportSink> = emptyList(),
    /** Saves the report, then hands it to the sink. */
    val deliverReport: (ReportSink, ReplyReport) -> Unit = { _, _ -> },
    val deleteReport: (String) -> Unit = {},
    val clearReports: () -> Unit = {},
    /** "Try again": the last reply goes, the last question is answered again. */
    val retry: () -> Unit = {},
    /** One message's words to the share sheet. */
    val shareMessage: (String) -> Unit = {},
    /** The whole chat on screen to the share sheet, as text. */
    val shareChat: () -> Unit = {},
    /** "Export all phone chats": Markdown plus JSON, to a file the person picks. */
    val exportAll: () -> Unit = {},
    /** The paperclip: attach a document (its name, and how to read it);
     *  null where phone chats take no documents. */
    /** Attach (name, opener, the typed question). */
    val attachDocument: ((String, () -> java.io.InputStream, String) -> Unit)? = null,
    /** "Ask the home server" for a PDF this phone can't read; null when no
     *  paired home server is in reach. */
    val askServer: ((com.domovoi.app.assistant.PendingDocument) -> Unit)? = null,
    /** ...and it is reading one now. */
    val askingServer: Boolean = false,
    val dropPending: () -> Unit = {},
    val closeDocProblem: () -> Unit = {},
    /** The phone's own copy of a document of the conversation on screen, by id. */
    val docFile: (String) -> java.io.File? = { null },
    /** A draft of the conversation on screen, by id (documents plan D4);
     *  null where phone chats keep no drafts. */
    val draftDoc: ((String) -> com.domovoi.app.ui.screens.chat.DraftDoc)? = null,
    /** Prints a draft's version with Android's print dialog. */
    val printDraft: (String, Int) -> Unit = { _, _ -> },
    /** A draft card's Revise box: (draft id, what to change). */
    val reviseDraft: ((String, String) -> Unit)? = null,
    /** "carry on" a stopped long-document summary (documents plan D2). */
    val carryOn: () -> Unit = {},
    /** The home-server offer: its time, seconds, when the home server's
     *  measured speed beats this phone's (null: no offer), and taking it. */
    val serverOffer: Double? = null,
    val useServer: (() -> Unit)? = null,
    /** Stop a job handed to the home server (what it read is kept). */
    val stopServer: (() -> Unit)? = null,
)

/** The phone chat's part of Chat's conversation frame: the way back to the
 *  list, the place chip, and the place picker while nothing is said yet. */
internal class PhoneChatChrome(
    val onBack: (() -> Unit)? = null,
    val chip: String = CHIP_PHONE,
    val picker: (@Composable () -> Unit)? = null,
)

/**
 * A chat "on this phone" (ui/screens/chat/ChatPlace.kt): Domovoi answering
 * from a model running on this phone, with no server and no internet once
 * its model is on the phone. Part of Chat since One Chat (2026-10-21): the
 * history list and the header are Chat's, and this is the phone place's
 * conversation. Its conversations never go to a server and the server's
 * never come here.
 *
 * - [openId]: the conversation to show (asked of the controller once);
 *   null starts a new one.
 * - A phone under 8 GB ([DeviceTier.Unsupported]) gets a plain explanation
 *   and no controls (Chat doesn't offer the phone there; this is for a chat
 *   opened anyway).
 * - Otherwise the setup ([AssistantSetupPanel]) until the model is on the
 *   phone, with the place picker above it, so someone who only wanted to
 *   chat can move to the home server instead; then the chat ([AssistantChat]).
 *
 * Showing it keeps the model in memory (and loads it); five minutes after it
 * leaves the screen the model is freed ([AssistantController.screenHidden]).
 * Chat's list and its home-server chats don't count as showing it, so they
 * never load the model.
 */
@Composable
internal fun PhoneChat(assistant: AssistantController, openId: String?, frame: ChatFrame) {
    val toast = LocalToast.current
    val context = LocalContext.current
    val ui by assistant.state.collectAsState()
    // The controller's one-line news is toasted by ChatScreen, which is
    // always around this view (and around the list, where a delete happens).

    // The conversation this view is for. The controller holds one at a time
    // and switches asynchronously, so until it has switched the view shows
    // nothing to type into, rather than the conversation shown before.
    val target = remember { phoneTarget(openId, assistant.state.value.conversation.id) }
    LaunchedEffect(target) {
        when (target) {
            is PhoneTarget.NewAfter -> assistant.newConversation()
            is PhoneTarget.Open -> assistant.open(target.id)
            PhoneTarget.Current -> Unit
        }
    }

    // On screen (and the app started): the model stays and loads; off it,
    // the unload timer runs.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(assistant, lifecycle) {
        var showing = false
        fun show(on: Boolean) {
            if (on == showing) return
            showing = on
            if (on) assistant.screenShown() else assistant.screenHidden()
        }
        val observer = LifecycleEventObserver { _, _ -> show(lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) }
        lifecycle.addObserver(observer)
        onDispose {
            lifecycle.removeObserver(observer)
            show(false)
        }
    }

    // The download runs as a foreground job with a progress notification;
    // Android 13+ shows it only with this permission. Asked on the first
    // download; the download runs either way.
    var afterNotifyAsk by remember { mutableStateOf<(() -> Unit)?>(null) }
    val askNotify = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        val then = afterNotifyAsk
        afterNotifyAsk = null
        then?.invoke()
    }
    val download = {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            afterNotifyAsk = { assistant.download() }
            askNotify.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            assistant.download()
        }
    }
    // The mic permission is asked on the first tap of the mic, after the
    // app's own one-line reason (MicPermission.kt); a refusal Android won't
    // ask about again points to the app's settings.
    var micAsk by remember { mutableStateOf<MicAsk?>(null) }
    val askMic = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            assistant.talk()
        } else {
            val again = context.activity()?.let {
                ActivityCompat.shouldShowRequestPermissionRationale(it, Manifest.permission.RECORD_AUDIO)
            } ?: true
            micAsk = afterRefusal(again)
            if (micAsk == null) toast(MIC_NOT_NOW)
        }
    }
    val talk = {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            assistant.talk()
        } else {
            micAsk = MicAsk.Reason
        }
    }
    MicPermissionDialog(
        ask = micAsk,
        onContinue = { askMic.launch(Manifest.permission.RECORD_AUDIO) },
        onOpenSettings = { if (!openAppSettings(context)) toast("could not open the app's settings") },
        onDismiss = { micAsk = null },
    )
    // "report this reply": saved on the phone; then the owner's destination
    // when one is set (REPORT_DESTINATION: an HTTPS address or an email
    // draft to Coders Farm), and the share menu.
    val app = LocalApp.current
    val sinks = remember(context) {
        ReportSinks.available(
            share = { subject, text -> shareText(context, subject, text) },
            email = { to, subject, body -> composeEmail(context, to, subject, body) },
            upload = { url, json -> app.reportUpload.post(url, json) },
        )
    }
    val reportScope = rememberCoroutineScope()
    val chrome = PhoneChatChrome(onBack = frame.onBack, chip = frame.chip, picker = frame.picker)
    // "Export all phone chats": the person picks where the zip goes.
    val exportAll = rememberExportPhoneChats(assistant)
    val share = { subject: String, text: String -> if (!shareText(context, subject, text)) toast("no app on this phone can share it") }
    val shareChat = {
        reportScope.launch {
            val c = assistant.state.value.conversation
            assistant.transcript(c.id)?.let { share(c.title, it) }
        }
        Unit
    }
    // A chat started from the list's "Message Domovoi…" box: its first
    // message goes out once this view has its conversation and the model
    // is on the phone.
    var firstSent by remember { mutableStateOf(frame.firstMessage.isNullOrBlank()) }
    val reached = target.reached(ui.conversation.id)
    LaunchedEffect(reached, ui.installed) {
        if (!firstSent && reached && ui.installed) {
            firstSent = true
            frame.firstMessage?.let { assistant.send(it) }
            frame.onSent()
        }
    }
    if (!target.reached(ui.conversation.id)) {
        Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            ChatHeader(NEW_CHAT, chrome.chip, ChatPlaceKind.Phone, chrome.onBack, Modifier.padding(top = 8.dp))
            LoadingState()
        }
        return
    }
    // Saying something here makes this phone the place last used.
    val used = frame.onSent
    // "Ask the home server" for a PDF this phone can't read the text of:
    // only with a paired home server in reach; it reads the text and keeps
    // nothing, and the answer is still written on this phone.
    val choice = com.domovoi.app.ui.shell.LocalServerChoice.current
    var asking by remember { mutableStateOf(false) }
    val askServer: ((com.domovoi.app.assistant.PendingDocument) -> Unit)? =
        if (choice is com.domovoi.app.ui.shell.ServerChoice.Available && !choice.needsPairing) {
            { p ->
                reportScope.launch {
                    asking = true
                    com.domovoi.app.ui.screens.chat.extractOnServer(app, assistant.pendingFile(p), p.name)
                        .onSuccess { assistant.attachRead(p, it) }
                        .onFailure { toast(com.domovoi.app.ui.screens.chat.docFailureText("read", it)) }
                    asking = false
                }
            }
        } else {
            null
        }
    // A long document read in parts (documents plan D2): the card's time
    // left ticks down while a part is read.
    androidx.compose.runtime.LaunchedEffect(ui.docJob?.id, ui.docJob?.state) {
        while (ui.docJob?.state == "reading" || ui.docJob?.state == "reducing") {
            kotlinx.coroutines.delay(2_000)
            assistant.tickJob()
        }
    }
    // "Use home server" (plan part 4): offered only when the home server's
    // MEASURED speed, plus sending the file, beats this phone's time left,
    // with AI features on there; the phone always works on its own.
    val serverOk = choice is com.domovoi.app.ui.shell.ServerChoice.Available && !choice.needsPairing
    val job = ui.docJob
    var offerS by remember { mutableStateOf<Double?>(null) }
    androidx.compose.runtime.LaunchedEffect(serverOk, job?.id, job?.read, job?.state) {
        offerS = null
        if (!serverOk || job == null || job.state != "reading" || !job.single) return@LaunchedEffect
        val bytes = assistant.handOverBytes() ?: return@LaunchedEffect
        val phone = assistant.phoneEtaS() ?: return@LaunchedEffect
        // The home server reads the whole document from the start, so its
        // time for ALL of it (plus sending) is what beats this phone's time
        // left, or not (review round 1).
        val speed = runCatching { com.domovoi.app.ui.screens.chat.fetchDocSpeed(app, job.totalChars) }.getOrNull()
            ?: return@LaunchedEffect
        offerS = com.domovoi.app.assistant.PhoneDocJobs.offer(phone, speed.estimate_s, bytes, speed.ai)
    }
    // A job with the home server: its stream while Chat is open, picked up
    // again when Chat comes back (the server keeps the summary for the
    // phone until it is taken, then deletes it).
    var streaming by remember { mutableStateOf(false) }
    val serverJob = ui.conversation.job?.takeIf { it.state == com.domovoi.app.assistant.PhoneDocJob.STATE_SERVER }
    fun onServerView(conversation: String, event: String, v: com.domovoi.app.ui.screens.chat.ServerJobView) {
        if (event == "job") assistant.serverJobStarted(conversation, v.id)
        // The error first; a "result" with no words is never filed as a
        // summary (review round 1).
        when (com.domovoi.app.assistant.PhoneDocJobs.serverOutcome(v.live, v.error != null, v.result?.content)) {
            "progress" -> if (event != "job") assistant.serverProgress(conversation, v.line, v.read, v.parts, v.eta_s)
            "failed" -> {
                assistant.serverFailed(conversation, (v.error?.detail?.takeIf { it.isNotBlank() }
                    ?: "the home server couldn't summarize it") + ". It can be read on this phone instead.")
                reportScope.launch { com.domovoi.app.ui.screens.chat.deleteServerJob(app, v.id) }
            }
            else -> {
                val r = v.result!!
                assistant.serverFinished(conversation, r.content, com.domovoi.app.ui.screens.chat.phoneSourcesOf(r.doc),
                    r.doc?.notes.orEmpty(), stopped = v.state == "stopped")
                reportScope.launch { com.domovoi.app.ui.screens.chat.deleteServerJob(app, v.id) }
            }
        }
    }
    androidx.compose.runtime.LaunchedEffect(serverJob?.serverJob, serverJob?.id, serverOk) {
        val sj = serverJob ?: return@LaunchedEffect
        if (streaming) return@LaunchedEffect
        val conv = ui.conversation.id
        val id = sj.serverJob
        if (id == null || !serverOk) {
            assistant.serverFailed(conv, "The home server isn't in reach. It can be read on this phone instead.")
            return@LaunchedEffect
        }
        val last = try {
            com.domovoi.app.ui.screens.chat.followServerJob(app, id) { ev, v -> onServerView(conv, ev, v) }
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Throwable) {
            null
        }
        // The stream ended with no summary (the server restarted, lost it):
        // this phone reads on from its own notes.
        if (last == null || (last.result == null && last.error == null)) {
            // Tell the server to drop it if it still can be told (it also
            // forgets a job no phone watches after 10 minutes).
            com.domovoi.app.ui.screens.chat.deleteServerJob(app, id)
            assistant.serverFailed(conv, "The home server stopped answering about it. It can be read on this phone instead.")
        }
    }
    val useServer: (() -> Unit)? = offerS?.let {
        {
            reportScope.launch {
                // Before the hand-over: the pick-up effect must not take the
                // job (no server id yet) for one this phone lost.
                streaming = true
                val h = assistant.handOverToServer()
                if (h == null) {
                    streaming = false
                    return@launch
                }
                try {
                    com.domovoi.app.ui.screens.chat.runPrivateJob(app, h.file, h.name, h.question) { ev, v -> onServerView(h.conversation, ev, v) }
                } catch (e: kotlinx.coroutines.CancellationException) {
                    throw e
                } catch (e: Throwable) {
                    assistant.serverJobOf(h.conversation)?.let { id -> com.domovoi.app.ui.screens.chat.deleteServerJob(app, id) }
                    assistant.serverFailed(h.conversation, com.domovoi.app.ui.screens.chat.docFailureText("the home server", e) +
                        ". It can be read on this phone instead.")
                } finally {
                    streaming = false
                }
            }
        }
    }
    val stopServer: (() -> Unit)? = serverJob?.serverJob?.let { id ->
        { reportScope.launch { runCatching { com.domovoi.app.ui.screens.chat.stopServerJob(app, id) } } }
    }
    val actions = actionsFor(assistant, download, talk, sinks, reportScope, used, askServer, asking) { toast(it) }
    // Drafts (documents plan D4): kept on this phone; saved to its "Chat
    // documents" library, and to the home server's From Chat by "Send to
    // home" when one is paired and in reach.
    val homeUpload: (suspend (String, ByteArray) -> String)? =
        if (choice is com.domovoi.app.ui.shell.ServerChoice.Available && !choice.needsPairing) {
            { name, bytes -> com.domovoi.app.ui.screens.chat.sendDraftHome(app, name, bytes) }
        } else {
            null
        }
    val draftStore = assistant.draftStore
    val draftDoc: ((String) -> com.domovoi.app.ui.screens.chat.DraftDoc)? = draftStore?.let { store ->
        { id ->
            PhoneDraftDoc(context, store, assistant.state.value.conversation.id, id,
                { text, base -> assistant.editDraft(assistant.state.value.conversation.id, id, text, base) }, homeUpload)
        }
    }
    val printDraft: (String, Int) -> Unit = { id, version ->
        val doc = draftDoc?.invoke(id)
        if (doc != null) {
            reportScope.launch {
                runCatching { doc.file(version, "pdf") }
                    .onSuccess { (name, bytes) -> if (!PhoneDraftFiles.print(context, name, bytes)) toast("this phone can't print") }
                    .onFailure { toast("couldn't make the PDF: ${it.message}") }
            }
        }
    }
    AssistantContent(
        ui,
        AssistantActions(
            download = actions.download, pauseDownload = actions.pauseDownload, cancelDownload = actions.cancelDownload,
            setAllowMobileData = actions.setAllowMobileData, deleteModel = actions.deleteModel,
            setBackend = actions.setBackend, retryLoad = actions.retryLoad, send = actions.send, stop = actions.stop,
            newConversation = actions.newConversation, open = actions.open, delete = actions.delete,
            clearAll = actions.clearAll, talk = actions.talk, downloadVoice = actions.downloadVoice,
            setSpeakTyped = actions.setSpeakTyped, draftReport = actions.draftReport, saveReport = actions.saveReport,
            decideLookup = actions.decideLookup, setChatInternet = actions.setChatInternet,
            setAlwaysAllowInternet = actions.setAlwaysAllowInternet, setOwnSearch = actions.setOwnSearch,
            testOwnSearch = actions.testOwnSearch,
            reportSinks = actions.reportSinks, deliverReport = actions.deliverReport,
            deleteReport = actions.deleteReport, clearReports = actions.clearReports,
            retry = assistant::retry,
            shareMessage = { share("Domovoi", it) },
            shareChat = shareChat,
            exportAll = exportAll,
            attachDocument = actions.attachDocument, askServer = actions.askServer,
            askingServer = actions.askingServer, dropPending = actions.dropPending,
            closeDocProblem = actions.closeDocProblem, docFile = actions.docFile,
            draftDoc = draftDoc, printDraft = printDraft, reviseDraft = assistant::reviseDraft,
            carryOn = actions.carryOn, serverOffer = offerS, useServer = useServer, stopServer = stopServer,
        ),
        chrome,
    )
}

/** Which conversation a phone chat's view waits for the controller to show. */
internal sealed interface PhoneTarget {
    /** A new one: any conversation other than the one showing before. */
    data class NewAfter(val before: String) : PhoneTarget

    /** One from the list, by its id. */
    data class Open(val id: String) : PhoneTarget

    /** The one already showing. */
    data object Current : PhoneTarget

    fun reached(showing: String): Boolean = when (this) {
        is NewAfter -> showing != before
        is Open -> showing == id
        Current -> true
    }
}

/** What a phone chat's view asks for: [openId] null is a new chat; the
 *  conversation already showing ([showing]) needs nothing. */
internal fun phoneTarget(openId: String?, showing: String): PhoneTarget = when (openId) {
    null -> PhoneTarget.NewAfter(showing)
    showing -> PhoneTarget.Current
    else -> PhoneTarget.Open(openId)
}

/** What the person is told once a report has been handed on (nothing when
 *  another app opened with it: that app is what they see). */
internal fun reportOutcomeToast(outcome: ReportOutcome): String? = when (outcome) {
    ReportOutcome.HANDED_ON -> null
    ReportOutcome.SENT -> "report sent to coders farm; it is kept on this phone too"
    ReportOutcome.NO_APP -> "no app on this phone can take the report; it is saved here"
    ReportOutcome.FAILED -> "couldn't send the report; it is saved here, try again from model settings, reports"
}

private fun actionsFor(
    a: AssistantController,
    download: () -> Unit,
    talk: () -> Unit,
    sinks: List<ReportSink>,
    scope: CoroutineScope,
    used: () -> Unit,
    askServer: ((com.domovoi.app.assistant.PendingDocument) -> Unit)? = null,
    askingServer: Boolean = false,
    toast: (String) -> Unit,
) = AssistantActions(
    download = download,
    pauseDownload = a::pauseDownload,
    cancelDownload = a::cancelDownload,
    setAllowMobileData = a::setAllowMobileData,
    deleteModel = a::deleteModel,
    setBackend = a::setBackend,
    retryLoad = a::retryLoad,
    send = { text ->
        a.send(text)
        used()
    },
    stop = a::stop,
    newConversation = a::newConversation,
    open = a::open,
    delete = a::delete,
    clearAll = a::clearAll,
    talk = {
        talk()
        used()
    },
    downloadVoice = a::downloadVoice,
    setSpeakTyped = a::setSpeakTyped,
    decideLookup = a::decideLookup,
    setChatInternet = a::setChatInternet,
    setAlwaysAllowInternet = a::setAlwaysAllowInternet,
    setOwnSearch = a::setOwnSearch,
    testOwnSearch = { a.testOwnSearch(it) },
    draftReport = a::draftReport,
    saveReport = { a.saveReport(it) },
    reportSinks = sinks,
    deliverReport = { sink, report ->
        a.saveReport(report)
        scope.launch { reportOutcomeToast(sink.deliver(report))?.let(toast) }
    },
    deleteReport = a::deleteReport,
    clearReports = a::clearReports,
    attachDocument = if (a.canAttach) { name, open, question ->
        a.attach(name, open, question)
        used()
    } else {
        null
    },
    askServer = askServer,
    askingServer = askingServer,
    dropPending = a::dropPending,
    closeDocProblem = a::closeDocProblem,
    docFile = { id -> a.docFile(id) },
    carryOn = a::carryOnJob,
)

/** The screen for [ui], with no controller behind it (the tests render it). */
@Composable
internal fun AssistantContent(ui: AssistantUi, actions: AssistantActions, chrome: PhoneChatChrome = PhoneChatChrome()) {
    when {
        ui.tier == DeviceTier.Unsupported -> Page(chrome) { UnsupportedPhone(ui) }
        ui.setup == ModelSetup.Checking -> Page(chrome) { LoadingState() }
        ui.setup == ModelSetup.Installed -> AssistantChat(ui, actions, chrome)
        // A phone chat started before the model is on the phone: its setup,
        // inside Chat, with the picker to chat on the home server instead.
        else -> Page(chrome, withPicker = true) { AssistantSetupPanel(ui, actions) }
    }
}

/** A scrolling page under Chat's header, for a phone chat that can't chat
 *  yet. */
@Composable
private fun Page(chrome: PhoneChatChrome, withPicker: Boolean = false, content: @Composable () -> Unit) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ChatHeader(NEW_CHAT, chrome.chip, ChatPlaceKind.Phone, chrome.onBack)
        if (withPicker) chrome.picker?.invoke()
        content()
    }
}

/** A phone under the floor: what it has, what it needs, and no controls. */
@Composable
private fun UnsupportedPhone(ui: AssistantUi) {
    DomovoiCard(Modifier.fillMaxWidth()) {
        Text("This phone can't run the assistant", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg,
            modifier = Modifier.asHeading())
        Text(
            unsupportedText(ui.memory.marketedGb, ui.memory.lowRamDevice),
            style = MaterialTheme.typography.bodyMedium,
            color = Domovoi.colors.fgMuted,
            modifier = Modifier.padding(top = 4.dp),
        )
    }
}

internal fun unsupportedText(gb: Int, lowRam: Boolean): String =
    "Domovoi on this phone runs a language model on the phone itself, which needs at least " +
        "${DeviceTiers.MIN_GB} GB of memory. " +
        (if (lowRam) "Android marks this phone as a low-memory device" else "This phone has $gb GB") +
        ", so the assistant isn't available here. Everything else in the app works as usual, " +
        "and Chat still talks to your Domovoi server when one is connected."
