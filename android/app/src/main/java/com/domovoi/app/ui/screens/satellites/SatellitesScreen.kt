package com.domovoi.app.ui.screens.satellites

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Campaign
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.window.core.layout.WindowWidthSizeClass
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.components.toneColor
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Satellites page — per-room Pi grid + drill-in detail + broadcast composer.
 * Android analog of web/static/satellites.jsx.
 */
@Composable
fun SatellitesScreen() {
    var openRoom by remember { mutableStateOf<String?>(null) }

    val satsState = rememberApi(
        eventTypes = setOf(
            "satellites.presence.changed",
            "satellites.wifi.changed",
            "satellites.dropins.changed",
            "music.now_playing.changed",
        ),
    ) { it.api.get("/api/satellites").decode<List<Satellite>>() }
    val sats = satsState.data ?: emptyList()

    // 1s ticker extrapolating now-playing progress between fetches; the base
    // resets whenever a fresh payload lands so we follow the canonical
    // elapsed_sec (web: tick state reset on payload change).
    var nowMs by remember { mutableLongStateOf(System.currentTimeMillis()) }
    var tickBase by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(1000)
            nowMs = System.currentTimeMillis()
        }
    }
    val payloadKey = sats.joinToString("|") {
        "${it.room_id}:${it.now_playing?.elapsed_sec}:${it.now_playing?.song?.title}"
    }
    LaunchedEffect(payloadKey) { tickBase = System.currentTimeMillis() }
    val extraSec = ((nowMs - tickBase) / 1000.0).coerceAtLeast(0.0)

    val onlineCount = sats.count { it.online }
    val offlineCount = sats.size - onlineCount
    val open = sats.firstOrNull { it.room_id == openRoom }

    val compact = currentWindowAdaptiveInfo()
        .windowSizeClass.windowWidthSizeClass == WindowWidthSizeClass.COMPACT

    // Compact width: the detail takes over the whole screen with a back
    // affordance (web: right-side drawer overlay).
    if (compact && open != null) {
        BackHandler { openRoom = null }
        Box(Modifier.fillMaxSize().padding(12.dp)) {
            SatelliteDetail(
                s = open,
                sats = sats,
                onClose = { openRoom = null },
                modifier = Modifier.fillMaxSize(),
            )
        }
        return
    }

    Row(Modifier.fillMaxSize()) {
        Box(Modifier.weight(1f)) {
            LazyVerticalGrid(
                columns = GridCells.Adaptive(280.dp),
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(16.dp),
                horizontalArrangement = Arrangement.spacedBy(14.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                item(span = { GridItemSpan(maxLineSpan) }) {
                    PageHeader(
                        "Satellites",
                        "$onlineCount online · $offlineCount offline · ${sats.size} provisioned",
                    )
                }
                val err = satsState.error
                when {
                    satsState.loading && sats.isEmpty() ->
                        item(span = { GridItemSpan(maxLineSpan) }) { LoadingState() }
                    err != null && sats.isEmpty() ->
                        item(span = { GridItemSpan(maxLineSpan) }) { ErrorState(err, satsState.refresh) }
                    sats.isEmpty() ->
                        item(span = { GridItemSpan(maxLineSpan) }) {
                            EmptyState("no satellites provisioned yet", "connect a Pi to bring a room online")
                        }
                    else -> items(sats, key = { it.room_id }) { s ->
                        SatCard(s, extraSec) { openRoom = s.room_id }
                    }
                }
                item(span = { GridItemSpan(maxLineSpan) }) {
                    BroadcastComposer(onlineCount)
                }
            }
        }
        if (open != null) {
            SatelliteDetail(
                s = open,
                sats = sats,
                onClose = { openRoom = null },
                modifier = Modifier
                    .width(440.dp)
                    .fillMaxHeight()
                    .padding(top = 16.dp, end = 16.dp, bottom = 16.dp),
            )
        }
    }
}

/* ---- Satellite card ------------------------------------------------------ */

@Composable
private fun SatCard(s: Satellite, extraSec: Double, onOpen: () -> Unit) {
    val online = s.online
    val np = s.now_playing
    val playing = np?.state == "play" && np?.song != null
    val songDur = np?.song?.duration_sec ?: 0.0
    val elapsed = if (playing) (np?.elapsed_sec ?: 0.0) + extraSec else 0.0
    val progress = if (playing && songDur > 0) (elapsed / songDur).coerceIn(0.0, 1.0) else 0.0
    val wTone = wifiTone(s.wifi?.rx_mbits)

    DomovoiCard(
        modifier = Modifier
            .fillMaxWidth()
            .alpha(if (online) 1f else 0.78f)
            .clickable(onClick = onOpen),
        padding = 0,
    ) {
        Row(
            Modifier.fillMaxWidth().padding(start = 16.dp, top = 14.dp, end = 16.dp, bottom = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            StatusDot(if (online) Tone.Ok else Tone.Idle, live = online)
            Text(
                s.room_id,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                color = if (online) Domovoi.colors.fg else Domovoi.colors.fgMuted,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Pill(if (online) "online" else "offline", if (online) Tone.Brand else Tone.Idle, live = online)
        }

        Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp).heightIn(min = 48.dp)) {
            if (playing) {
                val artist = np?.song?.artist
                Text(
                    songTitle(np) + (artist?.let { " · $it" } ?: ""),
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fg,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                Row(
                    Modifier.fillMaxWidth().padding(top = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Box(
                        Modifier
                            .weight(1f)
                            .height(3.dp)
                            .clip(RoundedCornerShape(2.dp))
                            .background(Domovoi.colors.sunken),
                    ) {
                        Box(
                            Modifier
                                .fillMaxWidth(progress.toFloat())
                                .fillMaxHeight()
                                .background(Domovoi.colors.brand),
                        )
                    }
                    Text(
                        fmtDur(elapsed) + (if (songDur > 0) " / ${fmtDur(songDur)}" else ""),
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                    )
                }
            } else {
                Text("no music", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgFaint)
            }
        }

        Spacer(Modifier.height(12.dp))
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        Row(
            Modifier.fillMaxWidth().background(Domovoi.colors.sunken)
                .padding(horizontal = 16.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                Icons.Filled.Wifi, contentDescription = null,
                tint = toneColor(wTone), modifier = Modifier.size(13.dp),
            )
            Spacer(Modifier.width(6.dp))
            Text(
                s.wifi?.rx_mbits?.let { "%.0f Mbit/s".format(it) } ?: "—",
                style = MaterialTheme.typography.labelMedium,
                color = Domovoi.colors.fg,
            )
            Spacer(Modifier.weight(1f))
            Text(
                if (online) "active now" else relTime(s.last_connected_at),
                style = MaterialTheme.typography.labelSmall,
                color = if (online) Domovoi.colors.ok else Domovoi.colors.fgFaint,
            )
        }
    }
}

/* ---- Broadcast composer -------------------------------------------------- */

@Composable
private fun BroadcastComposer(onlineCount: Int) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var msg by remember { mutableStateOf("") }
    var sending by remember { mutableStateOf(false) }

    fun send() {
        val m = msg.trim()
        if (m.isEmpty()) {
            toast("type a message first")
            return
        }
        if (onlineCount == 0) {
            toast("no satellites connected — nothing to broadcast to")
            return
        }
        scope.launch {
            sending = true
            runCatching {
                app.api.post(
                    "/api/satellites/announce-all",
                    buildJsonObject { put("message", m) },
                ).decode<AnnounceResult>()
            }.onSuccess { res ->
                // Inspect announced_to for an honest toast — a 200 can still
                // mean dead WSes underneath the active-sessions map.
                val delivered = res.announced_to
                when {
                    delivered.isEmpty() ->
                        toast("broadcast queued but no satellites accepted it (dead connections?)")
                    delivered.size < onlineCount ->
                        toast("broadcast partial — ${delivered.size}/$onlineCount reached (${delivered.joinToString(", ")})")
                    else ->
                        toast("broadcasted to ${delivered.size} satellite" + if (delivered.size == 1) "" else "s")
                }
                msg = ""
            }.onFailure { toast("broadcast failed: ${it.message}") }
            sending = false
        }
    }

    DomovoiCard(Modifier.fillMaxWidth(), padding = 0) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Icon(
                Icons.Filled.Campaign, contentDescription = null,
                tint = Domovoi.colors.fgMuted, modifier = Modifier.size(16.dp),
            )
            Text(
                "Broadcast",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = Domovoi.colors.fg,
            )
            Pill("$onlineCount online", if (onlineCount > 0) Tone.Brand else Tone.Idle, live = onlineCount > 0)
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        Column(Modifier.fillMaxWidth().background(Domovoi.colors.sunken).padding(16.dp)) {
            Text(
                "Speaks through every connected satellite simultaneously.",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
            )
            Row(
                Modifier.fillMaxWidth().padding(top = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value = msg,
                    onValueChange = { msg = it },
                    placeholder = { Text("dinner's ready", color = Domovoi.colors.fgSubtle) },
                    enabled = onlineCount > 0,
                    singleLine = true,
                    textStyle = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.weight(1f),
                )
                Button(
                    onClick = { send() },
                    enabled = msg.isNotBlank() && onlineCount > 0 && !sending,
                ) {
                    Icon(Icons.Filled.Campaign, contentDescription = null, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("broadcast")
                }
            }
        }
    }
}
