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
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.components.toneColor
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlin.math.roundToInt

/** Overview tab — web OverviewBody analog. [onRefresh] re-reads the roster
 *  after a change only it can show (the timer-scope switch). */
@Composable
fun SatOverviewTab(s: Satellite, sats: List<Satellite>, onRefresh: () -> Unit = {}) {
    // The Domovoi server's current git SHA. A satellite whose last-synced SHA
    // differs is behind. A null s.version is UNKNOWN, not behind — no false
    // "needs upgrade" nagging, but the upgrade button stays enabled so a
    // hand-updated Pi can bootstrap its first UI upgrade.
    val versionState = rememberApi { it.api.get("/api/config/version").decode<VersionInfo>() }
    val coreSha = versionState.data?.sha
    val needsUpgrade = coreSha != null && s.version != null && s.version != coreSha
    val upToDate = coreSha != null && s.version != null && s.version == coreSha
    // Whether it was upgraded but not rebooted (the health sample says so).
    // A read the caller may not make (or an older server) is just "no".
    val healthState = rememberApi(s.room_id) {
        runCatching { it.api.get("/api/satellites/${s.room_id}/health").decode<SatHealthDoc>() }.getOrNull()
    }
    val rebootReason = rebootNeeded(healthState.data)
    val netStep = netStepLine(healthState.data)

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
                val dbm = s.wifi?.signal_dbm
                if (s.wifi?.mode == "ap") {
                    Text(
                        "setup hotspot",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.SemiBold,
                        color = toneColor(Tone.Err),
                        modifier = Modifier.padding(top = 2.dp),
                    )
                    Text(
                        "hosting its setup network, not on the house Wi-Fi",
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                    )
                } else if (dbm != null) {
                    Text(
                        "%d dBm %s".format(Math.round(dbm), wifiSignalWord(dbm)),
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.SemiBold,
                        color = toneColor(wifiSignalTone(dbm)),
                        modifier = Modifier.padding(top = 2.dp),
                    )
                    Text(
                        (s.wifi?.ssid ?: "—") + (rx?.let { " · %.1f Mbit/s".format(it) } ?: ""),
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                    )
                } else if (rx != null) {
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
        TimerScopeSection(s, onRefresh)
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        DropInSection(s, sats)
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        MaintenanceSection(s, coreSha, needsUpgrade, upToDate, rebootReason, netStep, onRefresh)
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
                    Text("speak in the ${s.room_id} room…", color = Domovoi.colors.fgSubtle)
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

/* ---- Timers & reminders ------------------------------------------------------ */

/**
 * "Only reminders for this device" — web SatTimerScopeControl. Off (the
 * default): this room also announces the timers and reminders set in other
 * rooms. On: only its own. Device tier (the household token), so it works
 * from the phone; DB state, so it works while the room is offline.
 */
@Composable
private fun TimerScopeSection(s: Satellite, onChanged: () -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    // The switch moves at once; the roster catches up after the PUT.
    var override by remember(s.room_id) { mutableStateOf<Boolean?>(null) }
    var busy by remember(s.room_id) { mutableStateOf(false) }
    val on = override ?: s.timers_own_only
    LaunchedEffect(s.timers_own_only) {
        if (!busy && override == s.timers_own_only) override = null
    }

    fun set(next: Boolean) {
        if (busy) return
        val prev = on
        override = next
        busy = true
        scope.launch {
            try {
                putTimerScope(app.api, s.room_id, next)
                toast(timerScopeToast(s.room_id, next))
                onChanged()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                override = if (prev == s.timers_own_only) null else prev
                toast(failureText("change timer announcements", e))
            } finally {
                busy = false
            }
        }
    }

    Column(Modifier.fillMaxWidth().background(Domovoi.colors.sunken).padding(16.dp)) {
        SectionLabel("timers & reminders")
        Row(
            Modifier.fillMaxWidth().padding(top = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                TIMER_SCOPE_LABEL,
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fg,
                modifier = Modifier.weight(1f),
            )
            Switch(checked = on, onCheckedChange = { set(it) }, enabled = !busy)
        }
        Text(
            TIMER_SCOPE_HELP,
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgFaint,
            modifier = Modifier.padding(top = 6.dp),
        )
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
    rebootReason: String? = null,
    netStep: NetStepLine? = null,
    onRefresh: () -> Unit = {},
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var confirmRestart by remember { mutableStateOf(false) }
    var confirmUpgrade by remember { mutableStateOf(false) }

    // The upgrade under way, if any (SatUpgrade.kt): this app's press, or
    // the server's record — what reopening this screen, or a dashboard, sees.
    // The button stays locked from the server's yes until the satellite is
    // back (web: satellites.jsx OverviewBody, the same states).
    var press by remember(s.room_id) { mutableStateOf(SatUpgradePresses.get(s.room_id)) }
    // The press in flight: the server hasn't answered yet.
    var asking by remember(s.room_id) { mutableStateOf(false) }
    var upgradeErr by remember(s.room_id) { mutableStateOf<String?>(null) }
    var nowMs by remember { mutableLongStateOf(System.currentTimeMillis()) }
    val upView = satUpgradeView(s, press, nowMs)
    val upgrading = upView.running
    // While it runs: the clock, and a fresh roster every few seconds — the
    // satellite drops off and comes back, and that answer ends the wait.
    val refresh by rememberUpdatedState(onRefresh)
    LaunchedEffect(upgrading) {
        if (!upgrading) return@LaunchedEffect
        var ticks = 0
        while (true) {
            delay(1000)
            nowMs = System.currentTimeMillis()
            if (++ticks % 3 == 0) refresh()
        }
    }
    // The wait is over: say how it went, once, and forget the press (the
    // server's record, when there is one, keeps saying it for a while).
    var lastUpState by remember(s.room_id) { mutableStateOf(upView.state) }
    LaunchedEffect(upView.state) {
        val was = lastUpState
        lastUpState = upView.state
        if (was != "running" || !upView.finished) return@LaunchedEffect
        toast(satUpgradeOutcome(s.room_id, upView))
        val p = press ?: return@LaunchedEffect
        if (upView.rec != null) {
            SatUpgradePresses.drop(s.room_id)
            press = null
        } else {
            val settled = p.copy(outcome = upView.state)
            SatUpgradePresses.put(s.room_id, settled)
            press = settled
        }
    }

    fun restart() {
        if (upgrading) {
            toast("${s.room_id} is upgrading — it restarts itself when the new code is in")
            return
        }
        scope.launch {
            runCatching { app.api.post("/api/satellites/${s.room_id}/restart") }
                .onSuccess { toast("restarting ${s.room_id}…") }
                .onFailure { toast("restart failed: ${it.message}") }
        }
    }

    fun upgrade() {
        if (upgrading || asking) return
        asking = true
        upgradeErr = null
        scope.launch {
            runCatching { requestSatUpgrade(app.api, s.room_id) }
                .onSuccess { answered ->
                    // Locked from here until the satellite is back.
                    val p = if (answered.expected == null) answered.copy(expected = coreSha) else answered
                    SatUpgradePresses.put(s.room_id, p)
                    press = p
                    nowMs = System.currentTimeMillis()
                    toast("upgrading ${s.room_id}…")
                    onRefresh()
                }
                .onFailure {
                    if (it is CancellationException) throw it
                    // Refused, failed or cancelled: the button stays offered,
                    // and says why.
                    val msg = failureText("upgrade", it)
                    upgradeErr = msg
                    toast(msg)
                    // 409: one is already under way (a dashboard): the roster says so.
                    if ((it as? ApiException)?.status == 409) onRefresh()
                }
            asking = false
        }
    }

    Column(Modifier.fillMaxWidth().background(Domovoi.colors.sunken).padding(16.dp)) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SectionLabel("maintenance")
            if (needsUpgrade) Pill("needs upgrade", Tone.Warn)
            if (rebootReason != null) Pill("reboot needed", Tone.Warn)
        }
        if (rebootReason != null) {
            Text(
                (if (netStep?.word == "failed") "Its network settings did not all apply at this boot; a reboot tries again. "
                else "Upgraded, but not rebooted yet: its network settings are applied at boot. ") +
                    "Unplug it for a few seconds and plug it back in. ($rebootReason)",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(top = 6.dp),
            )
        }
        // What root's boot-time network step did this boot, and the host
        // name the router should show (absent from an older satellite).
        if (netStep != null) {
            Row(
                Modifier.padding(top = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                SectionLabel("boot network step")
                if (netStep.word.isNotEmpty()) Pill(netStep.word, netStep.tone)
            }
            if (netStep.hostname != null) {
                Text(
                    "host name ${netStep.hostname}",
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
            if (netStep.detail != null) {
                Text(
                    netStep.detail,
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
            if (netStep.early != null) {
                Text(
                    netStep.early,
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
        }
        Row(
            Modifier.fillMaxWidth().padding(top = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // Nothing else restarts it mid-upgrade: a restart during the sync
            // would leave a half-copied tree with no rollback armed.
            OutlinedButton(onClick = { confirmRestart = true }, enabled = s.online && !upgrading) {
                Icon(Icons.Filled.RestartAlt, contentDescription = null, modifier = Modifier.size(14.dp))
                Spacer(Modifier.width(6.dp))
                Text("restart")
            }
            val upgradeEnabled = s.online && !upToDate && !upgrading && !asking
            val upgradeLabel = if (upgrading || asking) "upgrading…" else "upgrade"
            if (needsUpgrade) {
                Button(onClick = { confirmUpgrade = true }, enabled = upgradeEnabled) {
                    Icon(Icons.Filled.CloudDownload, contentDescription = null, modifier = Modifier.size(14.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(upgradeLabel)
                }
            } else {
                OutlinedButton(onClick = { confirmUpgrade = true }, enabled = upgradeEnabled) {
                    Icon(Icons.Filled.CloudDownload, contentDescription = null, modifier = Modifier.size(14.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(upgradeLabel)
                }
            }
        }
        if (upgrading) {
            Text(
                (if (s.online) "Syncing its code${upView.expected?.let { " to $it" } ?: ""} from the Domovoi server…"
                else "Restarting into the new code…") +
                    (upView.startedMs?.let { "  " + satUpgradeClock(nowMs - it) } ?: ""),
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(top = 8.dp),
            )
            Text(
                "It drops offline while it restarts. This unlocks when it is back on the new code, " +
                    "usually within a couple of minutes.",
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgFaint,
                modifier = Modifier.padding(top = 2.dp),
            )
        } else if (upView.finished) {
            Text(
                satUpgradeOutcome(s.room_id, upView, line = true),
                style = MaterialTheme.typography.bodySmall,
                color = toneColor(if (upView.state == "done") Tone.Ok else Tone.Warn),
                modifier = Modifier.padding(top = 8.dp),
            )
        }
        val err = upgradeErr
        if (err != null && !upgrading) {
            Text(
                err,
                style = MaterialTheme.typography.bodySmall,
                color = toneColor(Tone.Err),
                modifier = Modifier.padding(top = 8.dp),
            )
        }
        Text(
            "restart bounces domovoi-satellite.service · upgrade syncs the room software to " +
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
                "room software from the server, backs up its current tree, and restarts — " +
                "dropping offline for a few seconds. If the new code won't start, it rolls back automatically. " +
                "Some updates finish at the next boot: if it then says \"reboot needed\", unplug it " +
                "for a few seconds and plug it back in.",
            confirmLabel = "upgrade",
            onConfirm = { upgrade() },
            onDismiss = { confirmUpgrade = false },
        )
    }
}
