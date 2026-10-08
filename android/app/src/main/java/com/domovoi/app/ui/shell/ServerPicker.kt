package com.domovoi.app.ui.shell

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.ui.draw.alpha
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import com.domovoi.app.LocalApp
import com.domovoi.app.net.Discovery
import com.domovoi.app.net.FoundDomovoi
import com.domovoi.app.net.ServerAddress
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
 * and manual ip:port entry. Lives inside [ConnectionDialog], which both
 * shells open from their server pill. Mirrors the web ServerSwitcher.
 *
 * Every probe here — the sweep's and the typed address's — is made on a
 * client with no household token on it (Discovery.client): the hosts
 * probed are not the server, and the trust dialog has not been answered
 * yet (A6-02).
 */
@Composable
fun ServerPickerPanel(onSelected: () -> Unit) {
    val app = LocalApp.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val currentUrl by app.prefs.serverUrl.collectAsState()
    val known by app.prefs.knownServers.collectAsState()
    val preferLocal by app.prefs.preferLocal.collectAsState()
    // The saved server is "connected" only while it is the one in use; on the
    // phone, or with it out of reach, it is just the selected server.
    val inUse = !preferLocal && LocalServerChoice.current.enabled

    var onLan by remember { mutableStateOf(Discovery.onLan(context)) }
    var scanning by remember { mutableStateOf(false) }
    var scanned by remember { mutableStateOf(false) }
    var progress by remember { mutableStateOf(0 to 0) }
    var found by remember { mutableStateOf<List<FoundDomovoi>>(emptyList()) }
    var manual by remember { mutableStateOf("") }
    var manualBusy by remember { mutableStateOf(false) }
    var manualError by remember { mutableStateOf<String?>(null) }

    // Trust before connect (FE-2): a LAN sweep finds whatever answers
    // /api/health, and connecting means loading that server's capability
    // manifest and plugin-backed screens. The gate shows the address and
    // waits for a yes; nothing is written or fetched before that.
    val gate = remember {
        ServerConnectGate(
            isTrusted = { app.prefs.isTrusted(it) },
            onTrust = { app.prefs.trustServer(it) },
            onConnect = { url, name ->
                app.prefs.upsertKnownServer(url, name)
                app.prefs.setServerUrl(url)
                app.prefs.setPreferLocal(false)
            },
        )
    }
    val pendingTrust by gate.pending.collectAsState()

    fun select(url: String, name: String?, fingerprint: String? = null) {
        if (gate.select(url, name, fingerprint)) onSelected()
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
        // One rule with Settings → Connection (net/ServerAddress.kt): default
        // scheme and port, and a plain-http address outside the home network
        // is refused up front with the reason, rather than "couldn't reach"
        // after the policy refuses it.
        val url = when (val typed = ServerAddress.fromTyped(manual)) {
            null -> return
            is ServerAddress.Result.Refused -> { manualError = typed.message; return }
            is ServerAddress.Result.Ok -> typed.url
        }
        manualBusy = true
        manualError = null
        scope.launch {
            val hit = Discovery.probe(app.api.http, url, timeoutMs = 3000)
            manualBusy = false
            if (hit != null) select(hit.url, hit.name, hit.fingerprint)
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
        val fingerprints = found.associate { it.url to it.fingerprint }
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
                    .clickable(enabled = !active) { select(url, name, fingerprints[url]) }
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
                    if (inUse) Pill("connected", Tone.Brand, live = true) else Pill("selected", Tone.Idle)
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

    pendingTrust?.let { server ->
        TrustServerDialog(
            server = server,
            onDismiss = { gate.cancel() },
            onConfirm = { if (gate.confirm()) onSelected() },
        )
    }
}

/**
 * "Is this your Domovoi?" — the address first, because that is what a person
 * can check against the box, and the identity it advertises, which is what
 * the dashboard's Settings → About shows. Nothing has been saved at this
 * point; cancelling leaves the app connected to whatever it was connected to.
 */
@Composable
private fun TrustServerDialog(
    server: PendingServer,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
) {
    Dialog(onDismissRequest = onDismiss) {
        DomovoiCard(Modifier.fillMaxWidth(), padding = 20) {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        Icons.Filled.Dns, contentDescription = null,
                        tint = Domovoi.colors.warn, modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        "trust this server?",
                        style = MaterialTheme.typography.titleMedium,
                        color = Domovoi.colors.fg,
                    )
                }
                Text(
                    server.address,
                    style = MaterialTheme.typography.titleLarge,
                    color = Domovoi.colors.fg,
                )
                server.fingerprint?.let { fingerprint ->
                    Text(
                        fingerprint,
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                        color = Domovoi.colors.fgMuted,
                    )
                }
                Text(
                    buildString {
                        server.name?.takeIf { it.isNotBlank() }?.let { append("It calls itself \"$it\". ") }
                        append(
                            "Check that this address is your Domovoi before you continue: the app " +
                                "will load the screens this server advertises and send it your " +
                                "requests. Nothing is saved until you confirm.",
                        )
                        if (server.fingerprint != null) {
                            append(
                                " The identity above should match Settings → About on the dashboard; " +
                                    "the app pins it once the server proves it.",
                            )
                        }
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
                ) {
                    OutlinedButton(onClick = onDismiss) { Text("cancel") }
                    Button(
                        onClick = onConfirm,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = Domovoi.colors.brand,
                            contentColor = Domovoi.colors.brandFg,
                        ),
                    ) { Text("trust this server") }
                }
            }
        }
    }
}

/**
 * The one connection pop-up, opened from the server pill in either shell:
 * a switch between "this phone" (local media) and the saved Domovoi on top,
 * the server picker below. The server side is greyed out, with the reason,
 * when there is no server yet or it is out of reach; picking a server in the
 * list below also means "use the server".
 */
@Composable
fun ConnectionDialog(onDismiss: () -> Unit) {
    val app = LocalApp.current
    val choice = LocalServerChoice.current
    val preferLocal by app.prefs.preferLocal.collectAsState()
    val serverUrl by app.prefs.serverUrl.collectAsState()
    // Which side is lit: the phone when chosen or when the server can't be used.
    val onPhone = preferLocal || serverUrl.isBlank() || !choice.enabled
    Dialog(onDismissRequest = onDismiss) {
        DomovoiCard(Modifier.fillMaxWidth(), padding = 20) {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.Dns, contentDescription = null, tint = Domovoi.colors.brand, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("connection", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
                    Spacer(Modifier.weight(1f))
                    IconButton(onClick = onDismiss, modifier = Modifier.size(28.dp)) {
                        Icon(Icons.Filled.Close, "close", tint = Domovoi.colors.fgMuted, modifier = Modifier.size(16.dp))
                    }
                }
                Spacer(Modifier.height(12.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                    ModeOption(
                        icon = Icons.Filled.PhoneAndroid,
                        title = "this phone",
                        sub = "music, videos and files on the phone",
                        selected = onPhone,
                        enabled = true,
                        modifier = Modifier.weight(1f),
                    ) {
                        app.prefs.setPreferLocal(true)
                        onDismiss()
                    }
                    ModeOption(
                        icon = Icons.Filled.Dns,
                        title = choice.title(),
                        sub = choice.reason(),
                        selected = !onPhone,
                        enabled = choice.enabled,
                        modifier = Modifier.weight(1f),
                    ) {
                        app.prefs.setPreferLocal(false)
                        onDismiss()
                    }
                }
                Spacer(Modifier.height(16.dp))
                ServerPickerPanel(onSelected = onDismiss)
            }
        }
    }
}

/** One side of the phone / server switch: a card that is lit when chosen and greyed when it can't be. */
@Composable
private fun ModeOption(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    title: String,
    sub: String,
    selected: Boolean,
    enabled: Boolean,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val shape = RoundedCornerShape(10.dp)
    val alpha = if (enabled) 1f else 0.45f
    Column(
        modifier
            .background(if (selected) Domovoi.colors.brandSoft else Domovoi.colors.sunken, shape)
            .border(1.dp, if (selected) Domovoi.colors.brand else Domovoi.colors.border, shape)
            .clickable(enabled = enabled && !selected, onClickLabel = "use $title", onClick = onClick)
            .padding(12.dp)
            .alpha(alpha),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(icon, contentDescription = null, tint = if (selected) Domovoi.colors.brand else Domovoi.colors.fgMuted,
                modifier = Modifier.size(16.dp))
            Text(title, style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Text(sub, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted, maxLines = 2,
            overflow = TextOverflow.Ellipsis)
        if (selected) Pill("in use", Tone.Brand)
    }
}
