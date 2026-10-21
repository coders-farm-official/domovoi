package com.domovoi.app.ui.screens.printing

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.Modifier
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
 * Scan into Documents through a scanner the home server has saved: glass or
 * feeder (automatic by default), colour or grey, Scan; then Scan another page
 * (glass) or Done, with thumbnails as pages arrive; then "Saved to Documents
 * as …" with Open. "What does it say?" appears only when the server says it
 * can read the scan ([onWhatDoesItSay] given and `readable` true).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun ScanSheet(
    printers: PrintersResponse,
    onOpen: (ScanJob) -> Unit,
    onSaved: () -> Unit,
    onDismiss: () -> Unit,
    onWhatDoesItSay: ((ScanJob) -> Unit)? = null,
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val io = remember(app) { PrintIo(app.api) }
    val scope = rememberCoroutineScope()
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    val scanners = printers.printers.filter { it.canScan }
    var scannerId by remember {
        mutableStateOf(
            scanners.firstOrNull { it.id == printers.defaultPrinterId }?.id ?: scanners.firstOrNull()?.id,
        )
    }
    val scanner = scanners.firstOrNull { it.id == scannerId }
    val controls = scanControls(scanner?.scan, scanners.size)
    var source by remember(scannerId) { mutableStateOf("auto") }
    var color by remember(scannerId) { mutableStateOf(controls.colors.firstOrNull()) }

    var scan by remember { mutableStateOf<ScanJob?>(null) }
    var busy by remember { mutableStateOf(false) }

    fun call(action: String, block: suspend () -> ScanJob) {
        busy = true
        scope.launch {
            runAction(block)
                .onSuccess {
                    scan = it
                    if (it.state == "done") onSaved()
                }
                .onFailure { toast(printFailureText(action, it)) }
            busy = false
        }
    }

    // While the scanner works (scanning, saving), ask again every 1.5 s.
    val scanId = scan?.id
    val scanBusy = scan?.busy == true
    LaunchedEffect(scanId, scanBusy) {
        if (scanId == null || !scanBusy) return@LaunchedEffect
        while (scan?.busy == true) {
            delay(1500)
            runAction { io.scan(scanId) }.onSuccess {
                scan = it
                if (it.state == "done") onSaved()
            }
        }
    }

    ModalBottomSheet(
        onDismissRequest = {
            // Leaving mid-scan cancels it, so nothing is half-saved behind the person's back.
            scan?.takeIf { !it.finished }?.let { s -> app.scope.launch { runAction { io.scanCancel(s.id) } } }
            onDismiss()
        },
        sheetState = sheet,
        containerColor = Domovoi.colors.raised,
    ) {
        Column(
            Modifier.padding(horizontal = 20.dp).padding(bottom = 28.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("Scan", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
            val s = scan
            if (s == null) {
                if (controls.scanner) {
                    SectionLabel("scanner")
                    SettingsDropdown(
                        selected = scanner ?: scanners.first(),
                        options = scanners,
                        label = { it.name.ifBlank { it.makeAndModel } },
                        onSelect = { scannerId = it.id },
                        modifier = Modifier.fillMaxWidth(),
                    )
                } else if (scanner != null) {
                    Text(scanner.name, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted)
                }
                if (controls.sources.isNotEmpty()) {
                    SectionLabel("glass or feeder")
                    SettingsDropdown(
                        selected = source,
                        options = controls.sources,
                        label = ::sourceLabel,
                        onSelect = { source = it },
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                if (controls.colors.isNotEmpty()) {
                    SectionLabel("colour")
                    SettingsDropdown(
                        selected = color ?: controls.colors.first(),
                        options = controls.colors,
                        label = ::colorLabel,
                        onSelect = { color = it },
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        enabled = scanner != null && !busy,
                        onClick = { call("scan") { io.startScan(scannerId, source, color, scanResolution(scanner?.scan)) } },
                    ) { Text(if (busy) "scanning…" else "Scan") }
                    TextButton(onClick = onDismiss) { Text("cancel", color = Domovoi.colors.fgMuted) }
                }
                return@Column
            }

            Text(
                if (s.finished) s.state else "${s.state} · ${s.pages} page${if (s.pages == 1) "" else "s"}",
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgMuted,
            )
            if (s.busy || busy) {
                LinearProgressIndicator(Modifier.fillMaxWidth(), color = Domovoi.colors.brand, trackColor = Domovoi.colors.sunken)
            }
            ThumbStrip(s.thumbs)

            if (s.finished) {
                Text(
                    scanEndMessage(s),
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (s.state == "failed") Domovoi.colors.err else Domovoi.colors.fg,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (s.state == "done" && s.path != null) {
                        Button(onClick = { onOpen(s) }) { Text("Open") }
                    }
                    if (onWhatDoesItSay != null && offersWhatDoesItSay(s)) {
                        OutlinedButton(onClick = { onWhatDoesItSay(s) }) { Text("What does it say?") }
                    }
                    TextButton(onClick = onDismiss) { Text("close", color = Domovoi.colors.fgMuted) }
                }
                return@Column
            }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (s.canScanMore) {
                    OutlinedButton(enabled = !busy && !s.busy, onClick = { call("scan") { io.scanNext(s.id) } }) {
                        Text("Scan another page")
                    }
                }
                Button(enabled = !busy && !s.busy && s.pages > 0, onClick = { call("save") { io.scanFinish(s.id) } }) {
                    Text("Done")
                }
                TextButton(enabled = !busy, onClick = { call("cancel") { io.scanCancel(s.id) } }) {
                    Text("cancel", color = Domovoi.colors.err)
                }
            }
        }
    }
}
