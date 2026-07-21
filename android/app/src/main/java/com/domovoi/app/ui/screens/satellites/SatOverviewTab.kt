package com.domovoi.app.ui.screens.satellites

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.automirrored.filled.VolumeDown
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.Phone
import androidx.compose.material.icons.filled.RestartAlt
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.components.toneColor
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlin.math.roundToInt

/** Overview tab — web OverviewBody analog. */
@Composable
fun SatOverviewTab(s: Satellite, sats: List<Satellite>) {
    // The Domovoi server's current git SHA. A satellite whose last-synced SHA
    // differs is behind. A null s.version is UNKNOWN, not behind — no false
    // "needs upgrade" nagging, but the upgrade button stays enabled so a
    // hand-updated Pi can bootstrap its first UI upgrade.
    val versionState = rememberApi { it.api.get("/api/config/version").decode<VersionInfo>() }
    val coreSha = versionState.data?.sha
    val needsUpgrade = coreSha != null && s.version != null && s.version != coreSha
    val upToDate = coreSha != null && s.version != null && s.version == coreSha

    Column(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth()) {
            Column(Modifier.weight(1f).padding(horizontal = 16.dp, vertical = 14.dp)) {
                SectionLabel("last connected")
                Text(
                    if (s.online) "active now" else relTime(s.last_connected_at),
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = Domovoi.colors.fg,
                    modifier = Modifier.padding(top = 2.dp),
                )
                Text(
                    s.last_connected_at?.replace('T', ' ')?.take(16)?.plus(" UTC") ?: "—",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
            Column(Modifier.weight(1f).padding(horizontal = 16.dp, vertical = 14.dp)) {
                SectionLabel("wi-fi")
                val rx = s.wifi?.rx_mbits
                if (rx != null) {
                    val tx = s.wifi?.tx_mbits
                    Text(
                        "%.1f".format(rx) + (tx?.let { " / %.1f".format(it) } ?: "") + " Mbit/s",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.SemiBold,
                        color = toneColor(wifiTone(rx)),
                        modifier = Modifier.padding(top = 2.dp),
                    )
                    Text(
                        s.wifi?.ssid ?: "—",
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                    )
                } else {
                    Text(
                        "no signal",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgFaint,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }
            }
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)

        Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 14.dp)) {
            SectionLabel("now playing")
            val np = s.now_playing
            val playing = np?.state == "play" && np?.song != null
            if (playing) {
                val artist = np?.song?.artist
                val dur = np?.song?.duration_sec ?: 0.0
                Text(
                    songTitle(np) + (artist?.let { " · $it" } ?: ""),
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fg,
                    maxLines = 2, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(top = 4.dp),
                )
                Text(
                    fmtDur(np?.elapsed_sec) + (if (dur > 0) " / ${fmtDur(dur)}" else ""),
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgFaint,
                )
            } else {
                Text(
                    "nothing playing",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgFaint,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)

        Column(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            ReadRow("room id", s.room_id)
            ReadRow("voice", s.voice ?: "registry default", muted = s.voice == null)
            ReadRow("version", s.version ?: "—", muted = s.version == null)
            ReadRow(
                "mpd ports",
                "control :${s.mpd_ports?.control ?: "—"} · http :${s.mpd_ports?.http ?: "—"}",
            )
            ReadRow(
                "stream",
                s.now_playing?.stream_url ?: "(http :${s.mpd_ports?.http ?: "—"})",
                muted = true,
            )
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)

        VolumeSection(s)
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        AnnounceSection(s)
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        DropInSection(s, sats)
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        MaintenanceSection(s, coreSha, needsUpgrade, upToDate)
    }
}

@Composable
private fun ReadRow(label: String, value: String, muted: Boolean = false) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Top) {
        SectionLabel(label, Modifier.width(96.dp))
        Text(
            value,
            style = MaterialTheme.typography.bodySmall,
            color = if (muted) Domovoi.colors.fgMuted else Domovoi.colors.fg,
            modifier = Modifier.weight(1f),
        )
    }
}

/* ---- Volume (master output, debounced) ----------------------------------- */

@Composable
private fun VolumeSection(s: Satellite) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val online = s.online
    val serverVol = s.volume?.roundToInt()
    val known = serverVol != null

    var vol by remember(s.room_id) { mutableIntStateOf(serverVol ?: 50) }
    var touched by remember(s.room_id) { mutableStateOf(false) }
    var dirty by remember(s.room_id) { mutableStateOf(false) }
    var debounce by remember(s.room_id) { mutableStateOf<Job?>(null) }

    // Follow the server-reported level (spoken "turn it up", another
    // dashboard) whenever the user isn't mid-adjustment.
    LaunchedEffect(serverVol) {
        if (serverVol != null && !dirty) vol = serverVol
    }

    fun commit(level: Int) {
        scope.launch {
            runCatching {
                app.api.post(
                    "/api/satellites/${s.room_id}/volume",
                    buildJsonObject { put("level", level) },
                )
            }.onSuccess { toast("${s.room_id} volume set to $level%") }
                .onFailure { toast("volume failed: ${it.message}") }
            dirty = false
        }
    }

    fun slide(v: Int) {
        vol = v
        touched = true
        dirty = true
        debounce?.cancel()
        debounce = scope.launch {
            delay(300)
            commit(vol)
        }
    }

    fun nudge(delta: Int) {
        val v = (vol + delta).coerceIn(0, 100)
        vol = v
        touched = true
        dirty = true
        debounce?.cancel()
        commit(v)
    }

    Column(Modifier.fillMaxWidth().background(Domovoi.colors.sunken).padding(16.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            SectionLabel("volume")
            Spacer(Modifier.weight(1f))
            Text(
                if (known || touched) "$vol%" else "unknown",
                style = MaterialTheme.typography.labelMedium,
                color = if (known || touched) Domovoi.colors.fg else Domovoi.colors.fgFaint,
            )
        }
        Row(
            Modifier.fillMaxWidth().padding(top = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            IconButton(onClick = { nudge(-5) }, enabled = online) {
                Icon(
                    Icons.AutoMirrored.Filled.VolumeDown, contentDescription = "quieter",
                    tint = Domovoi.colors.fgMuted, modifier = Modifier.size(18.dp),
                )
            }
            Slider(
                value = vol.toFloat(),
                onValueChange = { slide(it.roundToInt()) },
                valueRange = 0f..100f,
                enabled = online,
                modifier = Modifier.weight(1f),
            )
            IconButton(onClick = { nudge(5) }, enabled = online) {
                Icon(
                    Icons.AutoMirrored.Filled.VolumeUp, contentDescription = "louder",
                    tint = Domovoi.colors.fgMuted, modifier = Modifier.size(18.dp),
                )
            }
        }
        Text(
            "master output — scales speech and music on ${s.room_id}" +
                (if (!known && online) " · move to set (this board may not report its level)" else ""),
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgFaint,
            modifier = Modifier.padding(top = 6.dp),
        )
    }
}

/* ---- Announce ------------------------------------------------------------- */

@Composable
private fun AnnounceSection(s: Satellite) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var msg by remember(s.room_id) { mutableStateOf("") }

    fun send() {
        val m = msg.trim()
        if (m.isEmpty()) {
            toast("type a message first")
            return
        }
        if (!s.online) {
            toast("${s.room_id} is offline")
            return
        }
        scope.launch {
            runCatching {
                app.api.post(
                    "/api/satellites/${s.room_id}/announce",
                    buildJsonObject { put("message", m) },
                ).decode<AnnounceResult>()
            }.onSuccess { res ->
                // 200 with an empty announced_to means the WS to this room is
                // dead underneath the active-sessions map — say so honestly.
                if (res.announced_to.contains(s.room_id)) {
                    toast("announced to ${s.room_id}")
                } else {
                    toast("${s.room_id} accepted the request but didn't play — connection may be dead")
                }
                msg = ""
            }.onFailure { toast("announce failed: ${it.message}") }
        }
    }

    Column(Modifier.fillMaxWidth().background(Domovoi.colors.sunken).padding(16.dp)) {
        SectionLabel("announce to ${s.room_id}")
        Row(
            Modifier.fillMaxWidth().padding(top = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedTextField(
                value = msg,
                onValueChange = { msg = it },
                placeholder = {
                    Text("speak through the ${s.room_id} satellite…", color = Domovoi.colors.fgSubtle)
                },
                enabled = s.online,
                singleLine = true,
                textStyle = MaterialTheme.typography.bodySmall,
                modifier = Modifier.weight(1f),
            )
            Button(onClick = { send() }, enabled = msg.isNotBlank() && s.online) {
                Icon(Icons.AutoMirrored.Filled.Send, contentDescription = null, modifier = Modifier.size(14.dp))
                Spacer(Modifier.width(6.dp))
                Text("send")
            }
        }
    }
}

/* ---- Drop-in --------------------------------------------------------------- */

@Composable
private fun DropInSection(s: Satellite, sats: List<Satellite>) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var peer by remember(s.room_id) { mutableStateOf("") }

    // Eligible peers: OTHER online rooms that are AEC-capable (full_duplex)
    // and not already in a call. The Domovoi server re-checks all of this.
    val peers = sats.filter {
        it.room_id != s.room_id && it.online && it.full_duplex && it.in_call_with == null
    }

    fun start() {
        if (peer.isBlank()) {
            toast("pick a room to drop in on")
            return
        }
        if (!s.online) {
            toast("${s.room_id} is offline")
            return
        }
        scope.launch {
            runCatching {
                app.api.post(
                    "/api/satellites/${s.room_id}/dropin/start",
                    buildJsonObject { put("target_room", peer) },
                )
            }.onSuccess {
                toast("dropping in: ${s.room_id} → $peer")
                peer = ""
            }.onFailure { toast("drop-in failed: ${it.message}") }
        }
    }

    fun hangUp() {
        scope.launch {
            runCatching { app.api.post("/api/satellites/${s.room_id}/dropin/end") }
                .onSuccess { toast("hung up ${s.room_id}") }
                .onFailure { toast("hang up failed: ${it.message}") }
        }
    }

    Column(Modifier.fillMaxWidth().background(Domovoi.colors.sunken).padding(16.dp)) {
        SectionLabel("drop in")
        val inCallWith = s.in_call_with
        when {
            inCallWith != null -> Row(
                Modifier.fillMaxWidth().padding(top = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Pill("in call with $inCallWith", Tone.Brand, live = true)
                OutlinedButton(onClick = { hangUp() }) {
                    Icon(Icons.Filled.Close, contentDescription = null, modifier = Modifier.size(14.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("hang up")
                }
            }
            !s.full_duplex -> Text(
                "this room's mic can't do drop-in — it needs an echo-cancelling array (XVF3800)",
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgFaint,
                modifier = Modifier.padding(top = 8.dp),
            )
            else -> Row(
                Modifier.fillMaxWidth().padding(top = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                SatDropdown(
                    value = peer,
                    options = peers.map { it.room_id },
                    placeholder = if (peers.isEmpty()) "no eligible rooms" else "choose a room…",
                    enabled = s.online && peers.isNotEmpty(),
                    modifier = Modifier.weight(1f),
                    onSelect = { peer = it },
                )
                Button(onClick = { start() }, enabled = peer.isNotBlank() && s.online) {
                    Icon(Icons.Filled.Phone, contentDescription = null, modifier = Modifier.size(14.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("start")
                }
            }
        }
        Text(
            "live two-way audio — say \"hang up\" or use the button to end",
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgFaint,
            modifier = Modifier.padding(top = 6.dp),
        )
        // Phone ↔ room: this device joins the intercom bridge directly.
        // Always composed — gating on in_call_with would unmount the active
        // call dialog the instant the room reports it's in a call (which is
        // this very call), and the dialog's onDispose hangs up, killing the
        // call on connect. The button self-disables during any call; the
        // dialog stays up until the user hangs up.
        PhoneDropinRow(s)
    }
}

/* ---- Maintenance ------------------------------------------------------------ */

@Composable
private fun MaintenanceSection(
    s: Satellite,
    coreSha: String?,
    needsUpgrade: Boolean,
    upToDate: Boolean,
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var confirmRestart by remember { mutableStateOf(false) }
    var confirmUpgrade by remember { mutableStateOf(false) }

    fun restart() {
        scope.launch {
            runCatching { app.api.post("/api/satellites/${s.room_id}/restart") }
                .onSuccess { toast("restarting ${s.room_id}…") }
                .onFailure { toast("restart failed: ${it.message}") }
        }
    }

    fun upgrade() {
        scope.launch {
            runCatching { app.api.post("/api/satellites/${s.room_id}/upgrade") }
                .onSuccess { toast("upgrading ${s.room_id}…") }
                .onFailure { toast("upgrade failed: ${it.message}") }
        }
    }

    Column(Modifier.fillMaxWidth().background(Domovoi.colors.sunken).padding(16.dp)) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SectionLabel("maintenance")
            if (needsUpgrade) Pill("needs upgrade", Tone.Warn)
        }
        Row(
            Modifier.fillMaxWidth().padding(top = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedButton(onClick = { confirmRestart = true }, enabled = s.online) {
                Icon(Icons.Filled.RestartAlt, contentDescription = null, modifier = Modifier.size(14.dp))
                Spacer(Modifier.width(6.dp))
                Text("restart")
            }
            if (needsUpgrade) {
                Button(onClick = { confirmUpgrade = true }, enabled = s.online && !upToDate) {
                    Icon(Icons.Filled.CloudDownload, contentDescription = null, modifier = Modifier.size(14.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("upgrade")
                }
            } else {
                OutlinedButton(onClick = { confirmUpgrade = true }, enabled = s.online && !upToDate) {
                    Icon(Icons.Filled.CloudDownload, contentDescription = null, modifier = Modifier.size(14.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("upgrade")
                }
            }
        }
        Text(
            "restart bounces domovoi-satellite.service · upgrade syncs satellite code to " +
                "${coreSha ?: "the server's tree"} then restarts",
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgFaint,
            modifier = Modifier.padding(top = 6.dp),
        )
    }

    if (confirmRestart) {
        ConfirmDialog(
            title = "restart ${s.room_id}?",
            body = "It'll drop offline for a few seconds while the service bounces.",
            confirmLabel = "restart",
            onConfirm = { restart() },
            onDismiss = { confirmRestart = false },
        )
    }
    if (confirmUpgrade) {
        ConfirmDialog(
            title = "upgrade ${s.room_id}?",
            body = "Upgrades to ${coreSha ?: "the latest Domovoi code"}. It pulls the new " +
                "satellite code from the server, backs up its current tree, and restarts — " +
                "dropping offline for a few seconds. If the new code won't start, it rolls back automatically.",
            confirmLabel = "upgrade",
            onConfirm = { upgrade() },
            onDismiss = { confirmUpgrade = false },
        )
    }
}
