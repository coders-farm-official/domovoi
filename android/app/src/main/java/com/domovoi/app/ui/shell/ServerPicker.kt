package com.domovoi.app.ui.shell

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.WifiOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import com.domovoi.app.LocalApp
import com.domovoi.app.net.Discovery
import com.domovoi.app.net.FoundDomovoi
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.DomovoiGlyph
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch

/**
 * Shared domovoi picker: wifi check, /24 auto-scan, saved servers,
 * and manual ip:port entry. Used by the first-run StartupScreen and by
 * the topbar server-switcher dialog. Mirrors the web ServerSwitcher.
 */
@Composable
fun ServerPickerPanel(onSelected: () -> Unit) {
    val app = LocalApp.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val currentUrl by app.prefs.serverUrl.collectAsState()
    val known by app.prefs.knownServers.collectAsState()

    var onLan by remember { mutableStateOf(Discovery.onLan(context)) }
    var scanning by remember { mutableStateOf(false) }
    var scanned by remember { mutableStateOf(false) }
    var progress by remember { mutableStateOf(0 to 0) }
    var found by remember { mutableStateOf<List<FoundDomovoi>>(emptyList()) }
    var manual by remember { mutableStateOf("") }
    var manualBusy by remember { mutableStateOf(false) }
    var manualError by remember { mutableStateOf<String?>(null) }

    fun select(url: String, name: String?) {
        app.prefs.upsertKnownServer(url, name)
        app.prefs.setServerUrl(url)
        onSelected()
    }

    fun rescan() {
        onLan = Discovery.onLan(context)
        if (scanning) return
        scanning = true
        scanned = false
        found = emptyList()
        scope.launch {
            found = Discovery.scan(app.api.http) { done, total, _ ->
                progress = done to total
            }
            scanning = false
            scanned = true
        }
    }

    fun addManual() {
        var url = manual.trim().trimEnd('/')
        if (url.isBlank()) return
        if (!url.contains("://")) url = "http://$url"
        if (!Regex(":\\d+$").containsMatchIn(url.substringAfter("://"))) {
            url = "$url:${Discovery.DEFAULT_PORT}"
        }
        manualBusy = true
        manualError = null
        scope.launch {
            val hit = Discovery.probe(app.api.http, url, timeoutMs = 3000)
            manualBusy = false
            if (hit != null) select(hit.url, hit.name)
            else manualError = "couldn't reach a dashboard at $url"
        }
    }

    // Auto-scan once when the panel opens on a LAN.
    LaunchedEffect(Unit) {
        if (onLan && !scanned) rescan()
    }

    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (!onLan) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(Icons.Filled.WifiOff, contentDescription = null, tint = Domovoi.colors.warn, modifier = Modifier.size(16.dp))
                Text(
                    "not on wifi — join your home network to scan, or add an address manually",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
        }

        // ── Scan status ───────────────────────────────────────────────
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            SectionLabel(
                when {
                    scanning -> "scanning ${progress.first}/${progress.second}…"
                    scanned && found.isEmpty() && known.isEmpty() -> "no domovois found"
                    else -> "domovois"
                }
            )
            Spacer(Modifier.weight(1f))
            OutlinedButton(onClick = { rescan() }, enabled = !scanning) {
                if (scanning) {
                    CircularProgressIndicator(modifier = Modifier.size(13.dp), strokeWidth = 2.dp, color = Domovoi.colors.brand)
                } else {
                    Icon(Icons.Filled.Refresh, contentDescription = null, modifier = Modifier.size(13.dp))
                }
                Spacer(Modifier.width(6.dp))
                Text("rescan")
            }
        }
        if (scanning) {
            LinearProgressIndicator(
                progress = {
                    if (progress.second == 0) 0f
                    else progress.first.toFloat() / progress.second
                },
                color = Domovoi.colors.brand,
                trackColor = Domovoi.colors.border,
                modifier = Modifier.fillMaxWidth().height(3.dp),
            )
        }

        // ── Known + found servers ─────────────────────────────────────
        val foundUrls = found.map { it.url }.toSet()
        val rows = known.map { Triple(it.url, it.name, true) } +
            found.filter { f -> known.none { it.url == f.url } }
                .map { Triple(it.url, it.name, false) }

        if (rows.isEmpty() && scanned && !scanning) {
            Text(
                "nothing answered on port ${Discovery.DEFAULT_PORT} — is the web backend running? you can still add an address below",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgSubtle,
            )
        }

        rows.forEach { (url, name, saved) ->
            val active = url == currentUrl
            val online = url in foundUrls
            Row(
                Modifier
                    .fillMaxWidth()
                    .background(
                        if (active) Domovoi.colors.brandSoft else Domovoi.colors.sunken,
                        RoundedCornerShape(8.dp),
                    )
                    .clickable(enabled = !active) { select(url, name) }
                    .padding(horizontal = 12.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                StatusDot(
                    tone = when {
                        active -> Tone.Brand
                        online -> Tone.Ok
                        else -> Tone.Idle
                    },
                    live = active,
                )
                Column(Modifier.weight(1f)) {
                    Text(
                        name ?: "domovoi",
                        style = MaterialTheme.typography.titleSmall,
                        color = Domovoi.colors.fg,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        url.removePrefix("http://").removePrefix("https://"),
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
                if (active) {
                    Pill("connected", Tone.Brand, live = true)
                } else {
                    Text("use", style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.brand)
                }
                if (saved && !active) {
                    IconButton(onClick = { app.prefs.removeKnownServer(url) }, modifier = Modifier.size(26.dp)) {
                        Icon(Icons.Filled.Close, "forget", tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(14.dp))
                    }
                }
            }
        }

        // ── Manual add ────────────────────────────────────────────────
        SectionLabel("add manually", Modifier.padding(top = 4.dp))
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = manual,
                onValueChange = { manual = it; manualError = null },
                placeholder = { Text("192.168.1.30:6369", color = Domovoi.colors.fgSubtle) },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            Button(
                onClick = { addManual() },
                enabled = manual.isNotBlank() && !manualBusy,
                colors = ButtonDefaults.buttonColors(
                    containerColor = Domovoi.colors.brand,
                    contentColor = Domovoi.colors.brandFg,
                ),
            ) {
                Text(if (manualBusy) "checking…" else "add")
            }
        }
        if (manualError != null) {
            Text(manualError!!, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.err)
        }
    }
}

/** Topbar-launched switcher: the picker in a dialog. */
@Composable
fun ServerSwitcherDialog(onDismiss: () -> Unit) {
    Dialog(onDismissRequest = onDismiss) {
        DomovoiCard(Modifier.fillMaxWidth(), padding = 20) {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.Dns, contentDescription = null, tint = Domovoi.colors.brand, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("domovois", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
                    Spacer(Modifier.weight(1f))
                    IconButton(onClick = onDismiss, modifier = Modifier.size(28.dp)) {
                        Icon(Icons.Filled.Close, "close", tint = Domovoi.colors.fgMuted, modifier = Modifier.size(16.dp))
                    }
                }
                Spacer(Modifier.height(12.dp))
                ServerPickerPanel(onSelected = onDismiss)
            }
        }
    }
}

/**
 * First-run / no-server screen: wifi check + auto-scan + pick or add
 * manually. Replaces the old single-URL connect form.
 */
@Composable
fun StartupScreen() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(
            Modifier
                .widthIn(max = 480.dp)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            DomovoiGlyph(48)
            Text("domovoi", style = MaterialTheme.typography.displayLarge, color = Domovoi.colors.fg)
            Text(
                "looking for domovois on your network — pick one to connect",
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgMuted,
            )
            Spacer(Modifier.height(4.dp))
            ServerPickerPanel(onSelected = {})
        }
    }
}
