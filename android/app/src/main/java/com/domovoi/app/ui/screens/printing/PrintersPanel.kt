package com.domovoi.app.ui.screens.printing

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.components.runAction
import com.domovoi.app.ui.screens.settings.PanelCard
import com.domovoi.app.ui.screens.settings.SettingsDropdown
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

internal fun toneOf(tone: String): Tone = when (tone) {
    "ok" -> Tone.Ok
    "warn" -> Tone.Warn
    "err" -> Tone.Err
    else -> Tone.Idle
}

/**
 * Settings → Printers and scanners (the web's Settings → Printers): what the
 * home server has saved, what it found on the network, and each printer's
 * status, ink and household defaults. Status is re-read every 60 s while this
 * is on screen (GET /api/printers?refresh=1). Rename and Remove are
 * admin-only; this app has no admin session, so it says where they live.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun PrintersPanel() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val io = remember(app) { PrintIo(app.api) }
    val scope = rememberCoroutineScope()

    var data by remember { mutableStateOf<PrintersResponse?>(null) }
    var loadError by remember { mutableStateOf<String?>(null) }
    var found by remember { mutableStateOf<List<FoundPrinter>?>(null) }
    var diagnostics by remember { mutableStateOf<DiscoveryDiagnostics?>(null) }
    var looking by remember { mutableStateOf(false) }
    var reload by remember { mutableStateOf(0) }

    var addFound by remember { mutableStateOf<FoundPrinter?>(null) }
    var addByAddress by remember { mutableStateOf(false) }
    var offerTestPage by remember { mutableStateOf<Printer?>(null) }

    suspend fun load(refresh: Boolean) {
        runAction { io.printers(refresh) }
            .onSuccess { data = it; loadError = null; if (diagnostics == null) diagnostics = it.discovery }
            .onFailure { if (data == null) loadError = printFailureText("load printers", it) }
    }

    fun look(again: Boolean) {
        looking = true
        scope.launch {
            runAction { io.discover(again) }
                .onSuccess { found = it.found; diagnostics = it.diagnostics ?: diagnostics }
                .onFailure { toast(printFailureText("look for printers", it)) }
            looking = false
        }
    }

    /** Swap one saved row for the server's answer. */
    fun replace(p: Printer) {
        data = data?.let { d -> d.copy(printers = d.printers.map { if (it.id == p.id) p else it }) }
    }

    fun change(action: String, block: suspend () -> Printer, after: () -> Unit = {}) {
        scope.launch {
            runAction(block)
                .onSuccess { replace(it); after() }
                .onFailure { toast(printFailureText(action, it)) }
        }
    }

    // Open: the saved list, then a look (no sweep). Then every 60 s, a status refresh.
    LaunchedEffect(reload) {
        load(refresh = false)
    }
    LaunchedEffect(Unit) {
        look(again = false)
        while (true) {
            delay(60_000)
            load(refresh = true)
        }
    }

    val d = data
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Text(NETWORK_ONLY_LINE, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
        }
        when {
            d == null && loadError != null -> item { ErrorState(loadError ?: "", onRetry = { reload++ }) }
            d == null -> item { LoadingState() }
            d.printers.isEmpty() -> item {
                EmptyState("no printers yet", "Domovoi looks for printers on your home network.")
            }
            else -> {
                items(d.printers, key = { it.id }) { p ->
                    PrinterCard(
                        p = p,
                        rooms = d.rooms,
                        io = io,
                        onDefault = { on -> change("set default", { io.setDefault(p.id, on) }) { reload++ } },
                        onRooms = { rooms -> change("set rooms", { io.setRooms(p.id, rooms) }) { reload++ } },
                        onTwoSided = { v -> change("set two-sided", { io.setTwoSidedDefault(p.id, v) }) },
                        onColor = { v -> change("set colour", { io.setColorDefault(p.id, v) }) },
                    )
                }
                item {
                    Text(ADMIN_ONLY_LINE, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle)
                }
            }
        }

        item {
            PanelCard("Found on your network", "Printers and scanners Domovoi can see from the server.") {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(enabled = !looking, onClick = { look(again = found != null) }) {
                        Text(
                            when {
                                looking -> "looking…"
                                found == null -> "Look for printers"
                                else -> "Look again"
                            },
                        )
                    }
                    OutlinedButton(onClick = { addByAddress = true }) { Text("Add by address") }
                }
                if (looking) {
                    LinearProgressIndicator(
                        Modifier.fillMaxWidth().padding(top = 10.dp),
                        color = Domovoi.colors.brand,
                        trackColor = Domovoi.colors.sunken,
                    )
                }
                val list = found
                if (list != null) {
                    val fresh = list.filter { it.alreadyAdded == null }
                    if (fresh.isEmpty() && !looking) {
                        Text(
                            if (list.isEmpty()) "nothing found yet" else "everything found is already added",
                            style = MaterialTheme.typography.bodySmall,
                            color = Domovoi.colors.fgMuted,
                            modifier = Modifier.padding(top = 10.dp),
                        )
                    }
                    fresh.forEach { f ->
                        HorizontalDivider(Modifier.padding(vertical = 8.dp), color = Domovoi.colors.borderSoft)
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                Text(f.makeAndModel.ifBlank { f.address }, style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg)
                                if (f.location.isNotBlank()) {
                                    Text(f.location, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
                                }
                                FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                    if (f.prints) Pill("prints")
                                    if (f.scans) Pill("scans")
                                    if (f.color) Pill("colour")
                                    if (f.duplex) Pill("two-sided")
                                }
                            }
                            Button(onClick = { addFound = f }) { Text("Add") }
                        }
                    }
                }
                diagnostics?.let { dg ->
                    Text(
                        diagnosticsLine(dg),
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgFaint,
                        modifier = Modifier.padding(top = 10.dp),
                    )
                    diagnosticsNotes(dg).forEach {
                        Text(it, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.warn)
                    }
                }
            }
        }
    }

    addFound?.let { f ->
        AddPrinterDialog(
            title = "Add ${f.makeAndModel.ifBlank { "this printer" }}",
            askAddress = false,
            rooms = d?.rooms.orEmpty(),
            onDismiss = { addFound = null },
            onAdd = { _, name, room ->
                scope.launch {
                    runAction { io.add(key = f.key, name = name, roomId = room) }
                        .onSuccess {
                            addFound = null
                            toast("added ${it.name}")
                            reload++
                            look(again = false)
                            if (it.canPrint) offerTestPage = it
                        }
                        .onFailure { toast(printFailureText("add", it)) }
                }
            },
        )
    }
    if (addByAddress) {
        AddPrinterDialog(
            title = "Add by address",
            askAddress = true,
            rooms = d?.rooms.orEmpty(),
            onDismiss = { addByAddress = false },
            onAdd = { address, name, room ->
                scope.launch {
                    runAction { io.add(address = address.trim(), name = name, roomId = room) }
                        .onSuccess {
                            addByAddress = false
                            toast("added ${it.name}")
                            reload++
                            if (it.canPrint) offerTestPage = it
                        }
                        .onFailure { toast(printFailureText("add", it)) }
                }
            },
        )
    }
    offerTestPage?.let { p ->
        ConfirmDialog(
            title = "Print a test page?",
            body = "One page on ${p.name}, to check it works and which printer it is.",
            confirmLabel = "print a test page",
            onConfirm = {
                scope.launch {
                    runAction { io.testPage(p.id) }
                        .onSuccess { toast("test page sent to ${p.name}") }
                        .onFailure { toast(printFailureText("test page", it)) }
                }
            },
            onDismiss = { offerTestPage = null },
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PrinterCard(
    p: Printer,
    rooms: List<PrintRoom>,
    io: PrintIo,
    onDefault: (Boolean) -> Unit,
    onRooms: (List<String>) -> Unit,
    onTwoSided: (Boolean?) -> Unit,
    onColor: (Boolean?) -> Unit,
) {
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var showJobs by remember { mutableStateOf(false) }
    var jobs by remember { mutableStateOf<List<PrintJob>?>(null) }
    var jobsTick by remember { mutableStateOf(0) }

    PanelCard(p.name.ifBlank { p.makeAndModel }, listOf(p.makeAndModel, p.location).filter { it.isNotBlank() }.joinToString(" · ").ifBlank { null }) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                StatusDot(toneOf(p.status.tone), live = p.status.state == "printing")
                Text(p.status.state.lowercase(), style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                p.status.checkedAt?.let {
                    Text("· checked ${relTime(it)}", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint)
                }
            }
            p.status.words.forEach { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted) }
            if (p.status.message.isNotBlank()) {
                Text("printer says: ${p.status.message}", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgSubtle)
            }
            p.needsAttention?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.warn) }
            p.activeJob?.let { j ->
                Text("${j.title}: ${j.progress.ifBlank { j.state }.lowercase()}", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            }

            // Ink and toner.
            if (p.suppliesReported && p.supplies.isNotEmpty()) {
                p.supplies.forEach { s -> SupplyBar(s) }
            } else {
                Text("this printer doesn't report ink levels", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle)
            }

            FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (p.canPrint) Pill("prints")
                if (p.canScan) Pill("scans")
                if (p.color) Pill("colour")
                if (p.duplex) Pill("two-sided")
            }

            if (p.canPrint) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Default", style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                        Text("one per house", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgSubtle)
                    }
                    Switch(
                        checked = p.isDefault,
                        onCheckedChange = onDefault,
                        colors = SwitchDefaults.colors(checkedTrackColor = Domovoi.colors.brand),
                    )
                }
                if (rooms.isNotEmpty()) {
                    SectionLabel("default in rooms")
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        rooms.forEach { r ->
                            val on = r.id in p.rooms
                            FilterChip(
                                selected = on,
                                onClick = { onRooms(if (on) p.rooms - r.id else p.rooms + r.id) },
                                label = { Text(r.label.ifBlank { r.id }.lowercase()) },
                            )
                        }
                    }
                }
                if (p.duplex) {
                    SectionLabel("two-sided by default")
                    SettingsDropdown(
                        selected = p.twoSidedDefault,
                        options = listOf(null, true, false),
                        label = ::twoSidedDefaultLabel,
                        onSelect = onTwoSided,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                if (p.color) {
                    SectionLabel("colour by default")
                    SettingsDropdown(
                        selected = p.colorDefault,
                        options = listOf(null, true, false),
                        label = ::colorDefaultLabel,
                        onSelect = onColor,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }

            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (p.canPrint) {
                    OutlinedButton(onClick = {
                        scope.launch {
                            runAction { io.testPage(p.id) }
                                .onSuccess { toast("test page sent to ${p.name}"); jobsTick++ }
                                .onFailure { toast(printFailureText("test page", it)) }
                        }
                    }) { Text("Test page") }
                }
                if (p.identify) {
                    OutlinedButton(onClick = {
                        scope.launch {
                            runAction { io.identify(p.id) }
                                .onSuccess { toast("${p.name} should beep or flash now") }
                                .onFailure { toast(printFailureText("identify", it)) }
                        }
                    }) { Text("Which one is it?") }
                }
                p.adminUrl?.takeIf { it.startsWith("http://") || it.startsWith("https://") }?.let { url ->
                    TextButton(onClick = {
                        if (runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }.isFailure) {
                            toast("no browser found")
                        }
                    }) { Text("printer's own page", color = Domovoi.colors.brand) }
                }
            }

            if (p.canPrint) {
                TextButton(onClick = { showJobs = !showJobs }) {
                    Text(if (showJobs) "hide recent jobs" else "recent jobs", color = Domovoi.colors.fgMuted)
                }
                if (showJobs) {
                    LaunchedEffect(p.id, jobsTick, p.activeJob?.state) {
                        runAction { io.jobs(p.id) }
                            .onSuccess { jobs = it }
                            .onFailure { toast(printFailureText("recent jobs", it)) }
                    }
                    val list = jobs
                    when {
                        list == null -> Text("loading…", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle)
                        list.isEmpty() -> Text("nothing printed yet", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle)
                        else -> list.forEach { j ->
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Column(Modifier.weight(1f)) {
                                    Text(j.title.ifBlank { "untitled" }, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg)
                                    Text(
                                        listOf(
                                            (if (j.finished) j.state else j.progress.ifBlank { j.state }).lowercase(),
                                            j.who,
                                            j.createdAt?.let { relTime(it) }.orEmpty(),
                                        ).filter { it.isNotBlank() }.joinToString(" · "),
                                        style = MaterialTheme.typography.labelSmall,
                                        color = if (j.state == "failed") Domovoi.colors.err else Domovoi.colors.fgSubtle,
                                    )
                                    j.error?.takeIf { j.state == "failed" }?.let {
                                        Text(it, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.err)
                                    }
                                }
                                if (j.canCancel && !j.finished) {
                                    TextButton(onClick = {
                                        scope.launch {
                                            runAction { io.cancelJob(j.id) }
                                                .onSuccess { jobsTick++ }
                                                .onFailure { toast(printFailureText("cancel", it)) }
                                        }
                                    }) { Text("cancel", color = Domovoi.colors.err) }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SupplyBar(s: PrinterSupply) {
    val fraction = supplyFraction(s)
    val swatch = supplyArgb(s.color)?.let { Color(it) } ?: Domovoi.colors.fgMuted
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row {
            Text(s.name.ifBlank { s.kind }, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted, modifier = Modifier.weight(1f))
            Text(
                (fraction?.let { "${s.level}%" } ?: "unknown") + if (s.low) " · low" else "",
                style = MaterialTheme.typography.labelSmall,
                color = if (s.low) Domovoi.colors.warn else Domovoi.colors.fgSubtle,
            )
        }
        LinearProgressIndicator(
            progress = { fraction ?: 0f },
            modifier = Modifier.fillMaxWidth().height(6.dp),
            color = swatch,
            trackColor = Domovoi.colors.sunken,
            drawStopIndicator = {},
        )
    }
}

@Composable
private fun AddPrinterDialog(
    title: String,
    askAddress: Boolean,
    rooms: List<PrintRoom>,
    onDismiss: () -> Unit,
    onAdd: (address: String, name: String, roomId: String?) -> Unit,
) {
    var address by remember { mutableStateOf("") }
    var name by remember { mutableStateOf("") }
    var room by remember { mutableStateOf<PrintRoom?>(null) }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Domovoi.colors.raised,
        title = { Text(title, style = MaterialTheme.typography.titleMedium) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (askAddress) {
                    OutlinedTextField(
                        value = address,
                        onValueChange = { address = it.take(300) },
                        label = { Text("address") },
                        placeholder = { Text("192.168.1.50 or printer.local", color = Domovoi.colors.fgSubtle) },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it.take(80) },
                    label = { Text("name (optional)") },
                    placeholder = { Text("office printer", color = Domovoi.colors.fgSubtle) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                if (rooms.isNotEmpty()) {
                    SectionLabel("default in a room (optional)")
                    SettingsDropdown(
                        selected = room,
                        options = listOf<PrintRoom?>(null) + rooms,
                        label = { it?.label?.ifBlank { it.id } ?: "no room" },
                        onSelect = { room = it },
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        },
        confirmButton = {
            TextButton(enabled = !askAddress || address.isNotBlank(), onClick = { onAdd(address, name, room?.id) }) {
                Text("Add", color = Domovoi.colors.brand)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("cancel", color = Domovoi.colors.fgMuted) }
        },
    )
}
