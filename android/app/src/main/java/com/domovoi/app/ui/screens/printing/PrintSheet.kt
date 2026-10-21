package com.domovoi.app.ui.screens.printing

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.runAction
import com.domovoi.app.ui.screens.settings.SettingsDropdown
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * The server's Print sheet for a file in Documents or Files (the web's Print
 * dialog): preview strip, the options that can change, Print, then the job's
 * progress every 2 s with Cancel. Phone-local files never come here; they keep
 * Android's own print dialog.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun PrintSheet(
    libraryId: String,
    path: String,
    fileName: String,
    printers: PrintersResponse,
    onDismiss: () -> Unit,
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val io = remember(app) { PrintIo(app.api) }
    val scope = rememberCoroutineScope()
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    val printing = printers.printers.filter { it.canPrint }
    var printerId by remember { mutableStateOf(printers.defaultPrinterId?.takeIf { id -> printing.any { it.id == id } }) }
    var pagesText by remember { mutableStateOf("") }
    var copies by remember { mutableStateOf(1) }
    var sides by remember { mutableStateOf<String?>(null) }
    var color by remember { mutableStateOf<String?>(null) }
    var media by remember { mutableStateOf<String?>(null) }

    var preview by remember { mutableStateOf<PrintPreview?>(null) }
    var previewError by remember { mutableStateOf<String?>(null) }
    var previewing by remember { mutableStateOf(true) }
    var job by remember { mutableStateOf<PrintJob?>(null) }
    var sending by remember { mutableStateOf(false) }

    val options = PrintOptions(pagesText.takeIf { it.isNotBlank() }, copies, sides, color, media)

    // Re-preview when an option changes (a short pause first, for typing).
    LaunchedEffect(printerId, pagesText, copies, sides, color, media) {
        if (job != null) return@LaunchedEffect
        delay(if (preview == null) 0 else 400)
        previewing = true
        runAction { io.preview(libraryId, path, printerId, options) }
            .onSuccess {
                preview = it
                previewError = null
            }
            .onFailure { previewError = printFailureText("preview", it) }
        previewing = false
    }

    // Watch the job every 2 s while the sheet shows it.
    val jobId = job?.id
    LaunchedEffect(jobId) {
        if (jobId == null) return@LaunchedEffect
        while (job?.finished == false) {
            delay(2000)
            runAction { io.job(jobId) }.onSuccess { job = it }
        }
        job?.let { toast(jobEndMessage(it)) }
    }

    val p = preview
    val printer = printing.firstOrNull { it.id == (printerId ?: p?.printerId) }
    val choices = p?.choices ?: printer?.choices
    val controls = printControls(choices, printing.size, p?.pagesTotal)

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet, containerColor = Domovoi.colors.raised) {
        Column(
            // The sheet is its own window (ADJUST_NOTHING): it lifts itself over the keyboard for "pages".
            Modifier.imePadding().padding(horizontal = 20.dp).padding(bottom = 28.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("Print \"${p?.title?.ifBlank { null } ?: fileName}\"", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)

            val j = job
            if (j != null) {
                JobProgress(j, onCancel = {
                    scope.launch {
                        runAction { io.cancelJob(j.id) }
                            .onSuccess { job = it }
                            .onFailure { toast(printFailureText("cancel", it)) }
                    }
                })
                if (j.finished) {
                    Text(jobEndMessage(j), style = MaterialTheme.typography.bodyMedium, color = if (j.state == "failed") Domovoi.colors.err else Domovoi.colors.fg)
                    TextButton(onClick = onDismiss) { Text("close", color = Domovoi.colors.brand) }
                }
                return@Column
            }

            ThumbStrip(p?.thumbs.orEmpty(), loading = previewing && p == null)

            if (controls.printer) {
                SectionLabel("printer")
                SettingsDropdown(
                    selected = printer ?: printing.first(),
                    options = printing,
                    label = { it.name.ifBlank { it.makeAndModel } },
                    onSelect = {
                        if (it.id != printerId) {
                            printerId = it.id
                            sides = null; color = null; media = null
                            copies = copies.coerceAtMost(it.choices.copiesMax.coerceAtLeast(1))
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                )
            } else if (p != null && p.printerName.isNotBlank()) {
                Text(p.printerName, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted)
            }

            if (controls.pages) {
                OutlinedTextField(
                    value = pagesText,
                    onValueChange = { pagesText = it.take(60) },
                    label = { Text("pages") },
                    placeholder = { Text("all — or e.g. 1-3, 5", color = Domovoi.colors.fgSubtle) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            if (controls.copies) {
                val max = choices?.copiesMax ?: 1
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("copies", style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted, modifier = Modifier.weight(1f))
                    OutlinedButton(onClick = { copies = (copies - 1).coerceAtLeast(1) }, enabled = copies > 1) { Text("−") }
                    Text("$copies", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
                    OutlinedButton(onClick = { copies = (copies + 1).coerceAtMost(max) }, enabled = copies < max) { Text("+") }
                }
            }
            if (controls.sides && choices != null) {
                SectionLabel("two-sided")
                SettingsDropdown(
                    selected = sides ?: p?.options?.sides ?: choices.sides.first(),
                    options = choices.sides,
                    label = ::sidesLabel,
                    onSelect = { sides = it },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            if (controls.color && choices != null) {
                SectionLabel("colour")
                SettingsDropdown(
                    selected = color ?: p?.options?.color ?: choices.color.first(),
                    options = choices.color,
                    label = ::colorLabel,
                    onSelect = { color = it },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            if (controls.media && choices != null) {
                SectionLabel("paper")
                val current = media ?: p?.options?.media
                SettingsDropdown(
                    selected = choices.media.firstOrNull { it.id == current } ?: choices.media.first(),
                    options = choices.media,
                    label = { it.label.ifBlank { it.id } },
                    onSelect = { media = it.id },
                    modifier = Modifier.fillMaxWidth(),
                )
            }

            if (p != null && p.summary.isNotBlank()) {
                Text(p.summary, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
            }
            p?.notes?.forEach { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted) }
            p?.adjustments?.forEach { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.warn) }
            p?.overLimit?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.err) }
            previewError?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.err) }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Button(
                    enabled = p != null && p.overLimit == null && previewError == null && !sending && !previewing,
                    onClick = {
                        sending = true
                        scope.launch {
                            runAction { io.print(libraryId, path, printerId, options) }
                                .onSuccess { job = it }
                                .onFailure { toast(printFailureText("print", it)) }
                            sending = false
                        }
                    },
                ) { Text(if (sending) "sending…" else "Print") }
                TextButton(onClick = onDismiss) { Text("cancel", color = Domovoi.colors.fgMuted) }
                if (previewing && p != null) CircularProgressIndicator(Modifier.width(18.dp).height(18.dp), color = Domovoi.colors.brand, strokeWidth = 2.dp)
            }
        }
    }
}

/** A job's lowercase progress line, a bar while printing, and Cancel while it can. */
@Composable
internal fun JobProgress(job: PrintJob, onCancel: () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                job.progress.ifBlank { job.state }.lowercase(),
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.weight(1f),
            )
            if (job.canCancel && !job.finished) {
                TextButton(onClick = onCancel) { Text("cancel", color = Domovoi.colors.err) }
            }
        }
        if (!job.finished) {
            val total = (job.pages * job.copies.coerceAtLeast(1)).coerceAtLeast(1)
            if (job.state == "printing" && job.pagesDone > 0) {
                LinearProgressIndicator(
                    progress = { (job.pagesDone.toFloat() / total).coerceIn(0f, 1f) },
                    modifier = Modifier.fillMaxWidth(),
                    color = Domovoi.colors.brand,
                    trackColor = Domovoi.colors.sunken,
                )
            } else {
                LinearProgressIndicator(Modifier.fillMaxWidth(), color = Domovoi.colors.brand, trackColor = Domovoi.colors.sunken)
            }
        }
    }
}

/** The preview / scan thumbnails (data: URIs only; nothing is fetched). */
@Composable
internal fun ThumbStrip(thumbs: List<PageThumb>, loading: Boolean = false) {
    if (thumbs.isEmpty()) {
        if (loading) {
            Box(Modifier.fillMaxWidth().height(120.dp), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = Domovoi.colors.brand)
            }
        }
        return
    }
    LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        items(thumbs, key = { it.page }) { t ->
            val bmp = remember(t.src) { decodeThumb(t.src) }
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Box(
                    Modifier.width(90.dp).height(120.dp)
                        .background(Domovoi.colors.card, RoundedCornerShape(4.dp))
                        .border(1.dp, Domovoi.colors.border, RoundedCornerShape(4.dp)),
                    contentAlignment = Alignment.Center,
                ) {
                    if (bmp != null) {
                        Image(bmp, contentDescription = "page ${t.page}", contentScale = ContentScale.Fit, modifier = Modifier.padding(2.dp))
                    }
                }
                Text("${t.page}", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint)
            }
        }
    }
}
