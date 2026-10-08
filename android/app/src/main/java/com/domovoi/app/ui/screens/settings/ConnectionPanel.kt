package com.domovoi.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.net.Discovery
import com.domovoi.app.net.IdentityGate
import com.domovoi.app.net.IdentityVerdict
import com.domovoi.app.net.ServerAddress
import com.domovoi.app.net.ServerIdentity
import com.domovoi.app.net.decode
import com.domovoi.app.net.registerDevice
import com.domovoi.app.net.rememberApi
import com.domovoi.app.net.renameDevice
import com.domovoi.app.net.suggestedDeviceName
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.shell.ForgetActiveServerDialog
import com.domovoi.app.ui.shell.forgetActiveServer
import com.domovoi.app.ui.shell.provenFingerprint
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.ThemeMode
import kotlinx.coroutines.launch

/**
 * Connection tab — Android-only. The browser gets the server URL for free
 * from location.origin and the theme from localStorage; this app has to ask.
 * Also picks who "listening as" resumes podcasts/audiobooks for.
 */
@Composable
internal fun ConnectionPanel() {
    val app = LocalApp.current
    val toast = LocalToast.current

    val serverUrl by app.prefs.serverUrl.collectAsState()
    val themeMode by app.prefs.themeMode.collectAsState()
    val listenerId by app.prefs.listenerPersonId.collectAsState()
    val connected by app.bus.connected.collectAsState()
    val deviceToken by app.prefs.deviceToken.collectAsState()
    val trusted by app.prefs.trustedServers.collectAsState()
    val known by app.prefs.knownServers.collectAsState()
    val pins by app.prefs.identityPins.collectAsState()

    var url by remember(serverUrl) { mutableStateOf(serverUrl) }
    var tokenDraft by remember(serverUrl) { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    var confirmForgetActive by remember { mutableStateOf(false) }
    // The active server's latest identity verdict (net/IdentityGate.kt).
    val identityStatus by app.identity.status.collectAsState()
    val activeVerdict = identityStatus?.takeIf { it.base == IdentityGate.pinKey(serverUrl) }?.verdict

    // The server is the source of truth for this device's name (it may have
    // been renamed from the dashboard), so read it back from the idempotent
    // register rather than keeping a local copy.
    val scope = rememberCoroutineScope()
    var deviceName by remember { mutableStateOf("") }
    var deviceNameDraft by remember { mutableStateOf("") }
    LaunchedEffect(serverUrl) {
        registerDevice(app)?.let { deviceName = it.name; deviceNameDraft = it.name }
    }

    val peopleState = rememberApi(fetch = { it.api.get("/api/people").decode<List<SettingsPerson>>() })
    val people = peopleState.data ?: emptyList()
    // null == "me (this device)" — resume positions stay per-device.
    val listenerOptions: List<SettingsPerson?> = listOf(null) + people
    val selectedListener: SettingsPerson? = people.firstOrNull { it.id.toString() == listenerId }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            PanelCard(
                "Server",
                "The Domovoi web backend this app talks to — usually http://<domovoi-ip>:6369 on your LAN.",
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(
                        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        StatusDot(if (connected) Tone.Ok else Tone.Err, live = connected)
                        Text(
                            if (connected) "connected" else "not connected",
                            style = MaterialTheme.typography.labelMedium,
                            color = if (connected) Domovoi.colors.ok else Domovoi.colors.err,
                        )
                    }
                    OutlinedTextField(
                        value = url,
                        onValueChange = { url = it },
                        placeholder = { Text("http://192.168.1.10:6369", color = Domovoi.colors.fgSubtle) },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                        textStyle = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Button(
                        enabled = url.isNotBlank() && !saving,
                        onClick = {
                            // The same rule as the picker's "add manually":
                            // default scheme and port, and a plain-http address
                            // outside the home network is refused here, before
                            // anything is saved (A6-05). A hand-typed address is
                            // a choice, so typing it IS the trust decision — and
                            // it is listed as a known server, with a forget
                            // button, like every other trusted one (P2-at-01).
                            // The address is probed first, on the token-less
                            // discovery client, as the picker probes it: the
                            // identity it advertises is what the trust decision
                            // pins, and an address nothing answers at is not
                            // saved (A6-03 review).
                            when (val typed = ServerAddress.fromTyped(url)) {
                                null -> Unit
                                is ServerAddress.Result.Refused -> toast(typed.message)
                                is ServerAddress.Result.Ok -> {
                                    saving = true
                                    scope.launch {
                                        val hit = Discovery.probe(app.api.http, typed.url, timeoutMs = 3000)
                                        saving = false
                                        if (hit == null) {
                                            toast("couldn't reach a dashboard at ${typed.url}")
                                            return@launch
                                        }
                                        app.prefs.trustServer(hit.url, hit.identity)
                                        app.prefs.upsertKnownServer(hit.url, hit.name)
                                        if (app.prefs.setServerUrl(hit.url)) toast("server saved — reconnecting")
                                        else toast("couldn't switch to that server")
                                    }
                                }
                            }
                        },
                    ) { Text(if (saving) "checking…" else "Save & reconnect") }
                    if (serverUrl.isNotBlank()) {
                        IdentitySection(
                            pin = app.prefs.pinForServer(serverUrl),
                            verdict = activeVerdict,
                            onPin = { offered ->
                                IdentityGate.pinKey(serverUrl)?.let { key ->
                                    app.prefs.pin(key, offered)
                                    toast("pinned ${offered.fingerprint}")
                                }
                            },
                        )
                    }
                }
            }
        }

        item {
            PanelCard(
                "Household token",
                "What this phone presents so the server knows it belongs here. " +
                    "An admin finds it on the dashboard under Settings → Devices.",
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        StatusDot(if (deviceToken != null) Tone.Ok else Tone.Idle, live = false)
                        Text(
                            if (deviceToken != null) "paired" else "not paired",
                            style = MaterialTheme.typography.labelMedium,
                            color = if (deviceToken != null) Domovoi.colors.ok else Domovoi.colors.fgMuted,
                        )
                    }
                    if (deviceToken != null) {
                        Text(
                            "This phone sends the household token with every request to this server, " +
                                "and to nothing else. Paste a new one here after an admin rotates it.",
                            style = MaterialTheme.typography.bodySmall,
                            color = Domovoi.colors.fgMuted,
                        )
                    }
                    PairingField(
                        token = tokenDraft,
                        onTokenChange = { tokenDraft = it },
                        label = if (deviceToken != null) "replace" else "pair",
                        onPair = {
                            app.prefs.setDeviceToken(tokenDraft.trim())
                            app.api.clearPairingRequired()
                            tokenDraft = ""
                            toast("this phone is paired")
                        },
                    )
                    if (deviceToken != null) {
                        Button(
                            onClick = {
                                app.prefs.setDeviceToken(null)
                                toast("this phone is no longer paired")
                            },
                        ) { Text("forget the token") }
                    }
                }
            }
        }

        item {
            TrustedServersCard(
                trusted = trusted,
                known = known.associate { ServerCredentials.normalize(it.url) to it.name },
                active = serverUrl,
                fingerprintOf = { app.prefs.pinForServer(it)?.fingerprint },
                paired = { app.prefs.isPaired() && ServerCredentials.normalize(it) == serverUrl },
                onForget = { app.prefs.removeKnownServer(it); toast("forgot ${ServerCredentials.address(it)}") },
                onForgetActive = { confirmForgetActive = true },
            )
        }

        item {
            PanelCard("Appearance", "Theme for this app. System follows Android's dark mode.") {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ThemeMode.entries.forEach { mode ->
                        FilterChip(
                            selected = themeMode == mode,
                            onClick = { app.prefs.setThemeMode(mode) },
                            label = { Text(mode.name.lowercase()) },
                        )
                    }
                }
            }
        }

        item {
            PanelCard(
                "This device",
                "The name a room queue shows next to anything you add from this phone.",
            ) {
                SectionLabel("name")
                Spacer(Modifier.height(2.dp))
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    OutlinedTextField(
                        value = deviceNameDraft,
                        onValueChange = { deviceNameDraft = it.take(60) },
                        singleLine = true,
                        placeholder = {
                            Text(suggestedDeviceName(), color = Domovoi.colors.fgSubtle)
                        },
                        textStyle = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.weight(1f),
                    )
                    Button(
                        onClick = {
                            val next = deviceNameDraft.trim()
                            if (next.isEmpty()) {
                                toast("name can't be blank")
                            } else {
                                scope.launch {
                                    runCatching { renameDevice(app, next) }
                                        .onSuccess {
                                            deviceName = it.name
                                            deviceNameDraft = it.name
                                            toast("this device is now \"${it.name}\"")
                                        }
                                        .onFailure { toast("rename failed: ${it.message}") }
                                }
                            }
                        },
                        enabled = deviceNameDraft.trim().isNotEmpty()
                            && deviceNameDraft.trim() != deviceName,
                    ) { Text("rename") }
                }
                Spacer(Modifier.height(8.dp))
                SectionLabel("device id")
                Spacer(Modifier.height(2.dp))
                Text(
                    app.prefs.deviceId,
                    style = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
                    color = Domovoi.colors.fg,
                )
            }
        }

        item {
            PanelCard(
                "Listening as",
                "Podcast and audiobook resume positions sync to this person — pick yourself to " +
                    "share progress with the satellites; leave it on this device to keep it local.",
            ) {
                if (peopleState.error != null && peopleState.data == null) {
                    Text(
                        "couldn't load people: ${peopleState.error}",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.err,
                    )
                } else {
                    SettingsDropdown(
                        selected = selectedListener,
                        options = listenerOptions,
                        label = { it?.name ?: "me (this device)" },
                        onSelect = { person ->
                            app.prefs.setListenerPersonId(person?.id?.toString())
                            toast(
                                if (person == null) "listening as this device"
                                else "listening as ${person.name}",
                            )
                        },
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }
    }

    if (confirmForgetActive && serverUrl.isNotBlank()) {
        ForgetActiveServerDialog(
            address = ServerCredentials.address(serverUrl),
            pinned = app.prefs.pinForServer(serverUrl)?.fingerprint,
            proven = provenFingerprint(identityStatus, serverUrl),
            onDismiss = { confirmForgetActive = false },
            onConfirm = {
                confirmForgetActive = false
                forgetActiveServer(app, toast)
            },
        )
    }
}

/**
 * What the phone holds the active server to: its pinned identity, or why
 * there is none. A server that was trusted while it offered no identity
 * and has grown one since is never pinned behind the user's back
 * (net/IdentityGate.kt, Legacy with an offered key): the key it proved is
 * shown here with a button to pin it, after comparing it with the
 * dashboard's Settings → About.
 */
@Composable
internal fun IdentitySection(
    pin: ServerIdentity.Pin?,
    verdict: IdentityVerdict?,
    onPin: (ServerIdentity.Pin) -> Unit,
) {
    val offered = (verdict as? IdentityVerdict.Legacy)?.offered
    SectionLabel("identity")
    when {
        pin != null -> {
            Text(
                pin.fingerprint,
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                color = Domovoi.colors.fg,
            )
            Text(
                "Compare with Settings → About on the dashboard. The household token is " +
                    "sent only after the server proves this identity: on every network, " +
                    "every ten minutes, and whenever the app comes back to the foreground.",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
            )
        }
        offered != null -> {
            Text(
                offered.fingerprint,
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                color = Domovoi.colors.fg,
            )
            Text(
                "Not pinned: this server was trusted while it offered no identity, and it " +
                    "proves this one now. Compare it with Settings → About on the dashboard, " +
                    "then pin it — from then on the household token goes out only after a " +
                    "proof of this key. Until then it is sent as before (unverified).",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
            )
            Button(onClick = { onPin(offered) }) { Text("pin this identity") }
        }
        verdict is IdentityVerdict.Legacy -> Text(
            "none — this server offers no identity (an older web backend), so the household " +
                "token is sent as before, unverified: the app cannot tell this server from " +
                "another one at the same address.",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.warn,
        )
        else -> Text(
            "not pinned yet — the identity the trust dialog showed is pinned when a server is " +
                "trusted; a server trusted before this build is pinned the first time it proves one",
            style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
            color = Domovoi.colors.fgSubtle,
        )
    }
}

/**
 * Every server this phone trusts, with what it remembers about each, and a
 * way to forget it. Until 2026-10-08 the trust list was write-only: an
 * address trusted here stayed trusted through every later switch with
 * nothing in the UI showing it (P2-at-01). The rows are [trustedServersRows]
 * — pure, so the listing rule is unit-tested.
 */
@Composable
internal fun TrustedServersCard(
    trusted: Set<String>,
    known: Map<String, String?>,
    active: String,
    fingerprintOf: (String) -> String?,
    paired: (String) -> Boolean,
    onForget: (String) -> Unit,
    /** The active row's forget: confirmed first, then the app returns to
     *  the server list (ui/shell/ForgetServer.kt). */
    onForgetActive: () -> Unit,
) {
    val rows = trustedServersRows(trusted, known, active)
    PanelCard(
        "Trusted servers",
        "The Domovois this phone will connect to without asking again. Forgetting one also " +
            "forgets its household token and its pinned identity; forgetting the one you are " +
            "connected to returns you to the server list — the way to re-pair after a server " +
            "was reinstalled or replaced.",
    ) {
        if (rows.isEmpty()) {
            Text("none yet", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle)
        }
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            rows.forEach { row ->
                Row(
                    Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            row.name ?: "domovoi",
                            style = MaterialTheme.typography.titleSmall,
                            color = Domovoi.colors.fg,
                            maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            ServerCredentials.address(row.url),
                            style = MaterialTheme.typography.bodySmall,
                            color = Domovoi.colors.fgMuted,
                            maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            fingerprintOf(row.url) ?: "identity not pinned yet",
                            style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                            color = Domovoi.colors.fgSubtle,
                            maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                    }
                    if (row.active) {
                        Pill("connected", Tone.Brand, live = true)
                        IconButton(onClick = onForgetActive, modifier = Modifier.size(26.dp)) {
                            Icon(Icons.Filled.Close, "forget", tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(14.dp))
                        }
                    } else {
                        if (paired(row.url)) Pill("paired", Tone.Ok)
                        IconButton(onClick = { onForget(row.url) }, modifier = Modifier.size(26.dp)) {
                            Icon(Icons.Filled.Close, "forget", tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(14.dp))
                        }
                    }
                }
            }
        }
    }
}

/** One row of the trusted-servers list. */
internal data class TrustedServerRow(val url: String, val name: String?, val active: Boolean)

/**
 * What Settings → Connection lists: every trusted server and every known
 * one (a known server is trusted by construction, but the two lists can
 * drift — a pre-trust-list install, a harness write), the active one
 * first, then by address. The active server is listed even when it is
 * known to nothing else, so there is never a server the phone talks to
 * that the list does not show.
 */
internal fun trustedServersRows(
    trusted: Set<String>,
    known: Map<String, String?>,
    active: String,
): List<TrustedServerRow> {
    val activeClean = ServerCredentials.normalize(active)
    val urls = (trusted.map(ServerCredentials::normalize) + known.keys.map(ServerCredentials::normalize) +
        listOfNotNull(activeClean.takeIf { it.isNotBlank() })).filter { it.isNotBlank() }.toSet()
    return urls.map { TrustedServerRow(it, known[it], it == activeClean) }
        .sortedWith(compareByDescending<TrustedServerRow> { it.active }.thenBy { ServerCredentials.address(it.url) })
}
