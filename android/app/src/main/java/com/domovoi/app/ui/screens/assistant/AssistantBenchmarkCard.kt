package com.domovoi.app.ui.screens.assistant

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.assistant.AssistantBench
import com.domovoi.app.assistant.CatalogModel
import com.domovoi.app.assistant.LlmBackend
import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.assistant.ModelState
import com.domovoi.app.assistant.ModelStatus
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtBytes
import com.domovoi.app.ui.theme.Domovoi

/**
 * Settings > About > the on-device assistant benchmark (M0 of the phone
 * assistant): download the models, run the measured tests, copy the
 * results. No route or tab of its own; everything runs in
 * [AssistantBench], so a run carries on when this card leaves the screen.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun AssistantBenchmarkCard() {
    val bench = LocalApp.current.assistantBench
    val toast = LocalToast.current
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val ui by bench.state.collectAsState()
    var confirmDelete by remember { mutableStateOf<CatalogModel?>(null) }
    var afterMicGrant by remember { mutableStateOf<(() -> Unit)?>(null) }

    val askMic = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val then = afterMicGrant
        afterMicGrant = null
        if (granted) then?.invoke() else toast("microphone permission denied")
    }
    // The download runs as a foreground job with a progress notification;
    // Android 13+ shows it only with this permission. Asked once, on the
    // first download; the download runs either way.
    var afterNotifyAsk by remember { mutableStateOf<(() -> Unit)?>(null) }
    val askNotify = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        val then = afterNotifyAsk
        afterNotifyAsk = null
        then?.invoke()
    }
    fun withNotifications(action: () -> Unit) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            afterNotifyAsk = action
            askNotify.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            action()
        }
    }
    fun withMic(action: () -> Unit) {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            action()
        } else {
            afterMicGrant = action
            askMic.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    LaunchedEffect(bench) {
        bench.refresh()
        bench.messages.collect { toast(it) }
    }
    // A long run needs the screen on: a sleeping phone throttles it.
    val view = LocalView.current
    DisposableEffect(ui.running != null) {
        view.keepScreenOn = ui.running != null
        onDispose { view.keepScreenOn = false }
    }

    val idle = ui.running == null
    DomovoiCard(Modifier.fillMaxWidth()) {
        Text("On-device assistant benchmark", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
        Text(
            "Runs a language model and speech entirely on this phone and measures how fast they are " +
                "here. Nothing goes over the network except the model download you start, and the " +
                "results stay on the phone until you copy them.",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgMuted,
            modifier = Modifier.padding(top = 2.dp),
        )
        Spacer(Modifier.height(6.dp))
        val d = bench.device
        Mono("${d.manufacturer} ${d.model} · Android ${d.android} · ${fmtBytes(d.totalRamBytes)} RAM" +
            (d.soc?.let { " · $it" } ?: ""))

        Spacer(Modifier.height(12.dp))
        SectionLabel("models")
        Spacer(Modifier.height(4.dp))
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            ModelCatalog.all.forEach { m ->
                ModelRow(
                    model = m,
                    state = ui.models[m.id],
                    busy = ui.downloading || !idle,
                    onDownload = { withNotifications { bench.download(listOf(m)) } },
                    onDelete = { confirmDelete = m },
                )
            }
        }
        Spacer(Modifier.height(8.dp))
        val missing = ModelCatalog.all.filter { ui.models[it.id]?.status != ModelStatus.READY }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (ui.downloading) {
                OutlinedButton(onClick = { bench.pauseDownload() }) { Text("pause download") }
                TextButton(onClick = { bench.cancelDownload() }) { Text("cancel", color = Domovoi.colors.fgMuted) }
            } else if (missing.isNotEmpty()) {
                Button(onClick = { withNotifications { bench.download(missing) } }, enabled = idle) {
                    Text("download all (${fmtBytes(missing.sumOf { it.totalBytes - (ui.models[it.id]?.bytesDone ?: 0) })})")
                }
            }
        }
        ui.downloadNote?.let {
            Text(it, style = MaterialTheme.typography.bodySmall,
                color = if (ui.downloading) Domovoi.colors.fgMuted else Domovoi.colors.err,
                modifier = Modifier.padding(top = 4.dp))
        }
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 4.dp)) {
            Switch(checked = ui.allowMobileData, onCheckedChange = { bench.setAllowMobileData(it) })
            Text("  allow mobile data", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
        }
        ui.freeBytes?.let {
            Text("free space ${fmtBytes(it)} · the download carries on if you leave the app", style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgFaint, modifier = Modifier.padding(top = 4.dp))
        }

        Spacer(Modifier.height(12.dp))
        SectionLabel("run")
        Spacer(Modifier.height(4.dp))
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Text("model on", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
            LlmBackend.entries.forEach { b ->
                FilterChip(selected = ui.backend == b, enabled = idle, onClick = { bench.setBackend(b) },
                    label = { Text(b.name.lowercase(), style = MaterialTheme.typography.labelMedium) })
            }
        }
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Text("speech to text", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
            ModelCatalog.speechModels.forEach { m ->
                FilterChip(selected = ui.sttModelId == m.id, enabled = idle, onClick = { bench.setSttModel(m.id) },
                    label = { Text(m.id.removePrefix("moonshine-").removeSuffix("-en"), style = MaterialTheme.typography.labelMedium) })
            }
        }
        Spacer(Modifier.height(6.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedButton(enabled = idle, onClick = { bench.runText() }) { Text("text test") }
            OutlinedButton(enabled = idle, onClick = { withMic { bench.runVoice(fromMic = true) } }) { Text("voice test") }
            OutlinedButton(enabled = idle, onClick = { bench.runVoice(fromMic = false) }) { Text("voice self-test") }
            OutlinedButton(enabled = idle, onClick = { withMic { bench.runAudioIn(fromMic = true) } }) { Text("audio input") }
            OutlinedButton(enabled = idle, onClick = { bench.runAudioIn(fromMic = false) }) { Text("audio input self-test") }
            OutlinedButton(enabled = idle, onClick = { bench.runHeat() }) { Text("${AssistantBench.HEAT_MINUTES}-minute heat loop") }
        }
        Text(
            "Voice test: tap, then ask a question out loud. Self-tests have the phone speak a question to " +
                "itself instead. The heat loop keeps the model busy for ten minutes; leave the screen on. " +
                "Answers are spoken by this phone's own text-to-speech voice.",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgMuted,
            modifier = Modifier.padding(top = 6.dp),
        )


        if (!idle || ui.loaded != null) {
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (!idle) {
                    Pill(ui.running ?: "", if (ui.listening) Tone.Warn else Tone.Brand, live = true)
                    TextButton(onClick = { bench.stop() }) { Text("stop", color = Domovoi.colors.brand) }
                } else {
                    Pill("model loaded: ${ui.loaded}", Tone.Ok)
                    TextButton(onClick = { bench.unload() }) { Text("unload", color = Domovoi.colors.brand) }
                }
            }
            ui.step?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            }
        }
        if (ui.liveText.isNotBlank()) {
            Spacer(Modifier.height(6.dp))
            Text(
                ui.liveText,
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fg,
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Domovoi.colors.sunken, RoundedCornerShape(8.dp))
                    .padding(10.dp),
            )
        }

        Spacer(Modifier.height(12.dp))
        SectionLabel("results")
        Spacer(Modifier.height(4.dp))
        if (ui.results.isEmpty) {
            Text("No results yet.", style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted)
        } else {
            val report = remember(ui.results) { bench.report() }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = {
                    clipboard.setText(AnnotatedString(report))
                    toast("results copied")
                }) { Text("copy results") }
                TextButton(enabled = idle, onClick = {
                    bench.clearResults()
                    toast("results cleared")
                }) { Text("clear", color = Domovoi.colors.fgMuted) }
            }
            Spacer(Modifier.height(6.dp))
            SelectionContainer {
                Text(
                    report,
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                    color = Domovoi.colors.fgMuted,
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(max = 420.dp)
                        .background(Domovoi.colors.sunken, RoundedCornerShape(8.dp))
                        .verticalScroll(rememberScrollState())
                        .padding(10.dp),
                )
            }
        }
    }

    confirmDelete?.let { m ->
        ConfirmDialog(
            title = "delete ${m.name}?",
            body = "Removes its ${fmtBytes(m.totalBytes)} from this phone. You can download it again.",
            confirmLabel = "delete",
            destructive = true,
            onConfirm = { bench.delete(m) },
            onDismiss = { confirmDelete = null },
        )
    }
}

@Composable
private fun ModelRow(
    model: CatalogModel,
    state: ModelState?,
    busy: Boolean,
    onDownload: () -> Unit,
    onDelete: () -> Unit,
) {
    val status = state?.status ?: ModelStatus.MISSING
    Column(
        Modifier
            .fillMaxWidth()
            .background(Domovoi.colors.sunken, RoundedCornerShape(8.dp))
            .padding(10.dp),
        verticalArrangement = Arrangement.spacedBy(3.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(model.name, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg,
                modifier = Modifier.weight(1f, fill = false))
            when (status) {
                ModelStatus.READY -> Pill("ready", Tone.Ok)
                ModelStatus.DOWNLOADING -> Pill(percent(state, model), Tone.Brand, live = true)
                ModelStatus.PARTIAL -> Pill("paused ${percent(state, model)}", Tone.Idle)
                ModelStatus.FAILED -> Pill("failed", Tone.Err)
                ModelStatus.MISSING -> Pill("not downloaded", Tone.Idle)
            }
        }
        Text(
            "${model.kind.label} · ${fmtBytes(model.totalBytes)} · ${model.license}",
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgMuted,
        )
        model.note?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.warn) }
        state?.error?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.err) }
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            if (status != ModelStatus.READY && status != ModelStatus.DOWNLOADING) {
                TextButton(enabled = !busy, onClick = onDownload) {
                    Text(if (status == ModelStatus.PARTIAL) "resume" else "download", color = Domovoi.colors.brand)
                }
            }
            if (status == ModelStatus.READY || status == ModelStatus.PARTIAL || status == ModelStatus.FAILED) {
                TextButton(enabled = !busy, onClick = onDelete) { Text("delete", color = Domovoi.colors.fgMuted) }
            }
        }
    }
}

private fun percent(state: ModelState?, model: CatalogModel): String {
    val done = state?.bytesDone ?: 0
    return "${(done * 100 / model.totalBytes.coerceAtLeast(1)).coerceIn(0, 100)}%"
}

@Composable
private fun Mono(text: String) {
    Text(text, style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
        color = Domovoi.colors.fgFaint)
}
