package com.domovoi.app.ui.screens.assistant

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material3.Icon
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
import androidx.compose.ui.Alignment
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
import com.domovoi.app.assistant.AssistantSupport
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
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.openAppSettings
import com.domovoi.app.ui.components.composeEmail
import com.domovoi.app.ui.components.shareText
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
)

/**
 * The on-device assistant (Route.Assistant): Domovoi answering from a model
 * running on this phone, with no server and no internet once its model is
 * on the phone. A tab of the local shell (no server, or none in reach), and
 * reachable from Home's grid, the drawer, the rail and the phone bar's
 * choices with a server; never in Chat's place, and it always says "on this
 * phone", so nobody mistakes it for the server's chat. Its conversations
 * never go to a server and the server's never come here.
 *
 * - A 32-bit install has no engine ([AssistantSupport]): the route is on no
 *   launcher there; this screen says why if anything opens it anyway.
 * - A phone under 8 GB ([DeviceTier.Unsupported]) gets a plain explanation
 *   and no controls.
 * - Otherwise the setup ([AssistantSetupPanel]) until the model is on the
 *   phone, then the chat ([AssistantChat]).
 *
 * Showing it keeps the model in memory (and loads it); five minutes after it
 * leaves the screen the model is freed ([AssistantController.screenHidden]).
 */
@Composable
fun AssistantScreen() {
    if (!AssistantSupport.available) {
        NotInThisApp()
        return
    }
    val assistant = LocalApp.current.assistant
    val toast = LocalToast.current
    val context = LocalContext.current
    val ui by assistant.state.collectAsState()
    LaunchedEffect(assistant) { assistant.messages.collect { toast(it) } }

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
    AssistantContent(ui, actionsFor(assistant, download, talk, sinks, reportScope) { toast(it) })
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
    toast: (String) -> Unit,
) = AssistantActions(
    download = download,
    pauseDownload = a::pauseDownload,
    cancelDownload = a::cancelDownload,
    setAllowMobileData = a::setAllowMobileData,
    deleteModel = a::deleteModel,
    setBackend = a::setBackend,
    retryLoad = a::retryLoad,
    send = a::send,
    stop = a::stop,
    newConversation = a::newConversation,
    open = a::open,
    delete = a::delete,
    clearAll = a::clearAll,
    talk = talk,
    downloadVoice = a::downloadVoice,
    setSpeakTyped = a::setSpeakTyped,
    draftReport = a::draftReport,
    saveReport = { a.saveReport(it) },
    reportSinks = sinks,
    deliverReport = { sink, report ->
        a.saveReport(report)
        scope.launch { reportOutcomeToast(sink.deliver(report))?.let(toast) }
    },
    deleteReport = a::deleteReport,
    clearReports = a::clearReports,
)

/** The screen for [ui], with no controller behind it (the tests render it). */
@Composable
internal fun AssistantContent(ui: AssistantUi, actions: AssistantActions) {
    when {
        ui.tier == DeviceTier.Unsupported -> Page { UnsupportedPhone(ui) }
        ui.setup == ModelSetup.Checking -> Page { LoadingState() }
        ui.setup == ModelSetup.Installed -> AssistantChat(ui, actions)
        else -> Page { AssistantSetupPanel(ui, actions) }
    }
}

/** A scrolling page under the assistant's title. */
@Composable
private fun Page(content: @Composable () -> Unit) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        AssistantTitle()
        content()
    }
}

/** "Domovoi" with "on this phone": always, so it is never taken for Chat. */
@Composable
internal fun AssistantTitle(modifier: Modifier = Modifier, actions: (@Composable () -> Unit)? = null) {
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Icon(Icons.Filled.PhoneAndroid, contentDescription = null, tint = Domovoi.colors.brand, modifier = Modifier.size(20.dp))
        Text("Domovoi", style = MaterialTheme.typography.titleLarge, color = Domovoi.colors.fg, modifier = Modifier.asHeading())
        Pill("on this phone", Tone.Brand)
        Row(Modifier.weight(1f), horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.CenterVertically) {
            actions?.invoke()
        }
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

/** Opened on a 32-bit install (no launcher offers it there). */
@Composable
private fun NotInThisApp() {
    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        AssistantTitle()
        DomovoiCard(Modifier.fillMaxWidth()) {
            Text("Not in this version of the app", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg,
                modifier = Modifier.asHeading())
            Text(
                "The assistant on the phone needs the 64-bit Domovoi app (the arm64-v8a download). " +
                    "This one is the 32-bit app; everything else works the same in both.",
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
    }
}
