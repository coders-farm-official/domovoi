package com.domovoi.app.ui.screens.stations

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CellTower
import androidx.compose.material.icons.filled.DeleteOutline
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Headset
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.Radio
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.window.core.layout.WindowWidthSizeClass
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.player.PlayItem
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Stations page — radio search + favorites + detail w/ detection feed.
 * Android analog of web/static/stations.jsx.
 */
@Composable
fun StationsScreen() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var selectedId by remember { mutableStateOf<Long?>(null) }

    val favState = rememberApi(eventTypes = setOf("radio.stations.changed")) {
        it.api.get("/api/plugins/radio/stations?favorited_only=true&limit=500").decode<List<Station>>()
    }
    val favorites = favState.data ?: emptyList()
    val selected = favorites.firstOrNull { it.id == selectedId }

    val compact = currentWindowAdaptiveInfo()
        .windowSizeClass.windowWidthSizeClass == WindowWidthSizeClass.COMPACT

    // POSTing the search-hit shape — server idempotent on external_id.
    val onFavorite: suspend (Station) -> Unit = { hit ->
        app.api.post(
            "/api/plugins/radio/stations",
            buildJsonObject {
                put("name", hit.name)
                put("source", hit.source ?: "online")
                put("stream_url", hit.stream_url)
                put("external_id", hit.external_id)
                put("country_code", hit.country_code)
                put("language", hit.language)
                put("tags", buildJsonArray { hit.tags.forEach { add(JsonPrimitive(it)) } })
            },
        )
        toast("favorited ${hit.name}")
        favState.refresh()
    }

    fun forget(st: Station) {
        scope.launch {
            runCatching { app.api.delete("/api/plugins/radio/stations/${st.id}") }
                .onSuccess {
                    toast("forgot ${st.name}")
                    if (selectedId == st.id) selectedId = null
                    favState.refresh()
                }
                .onFailure { toast("forget failed: ${it.message}") }
        }
    }

    fun fccImport() {
        // No state arg — the radio plugin uses its RADIO_MARKET_STATE setting.
        toast("fcc import started…")
        scope.launch {
            runCatching {
                app.api.post("/api/plugins/radio/fcc-import").decode<FccImportResult>()
            }.onSuccess { out ->
                if (out.state.isNullOrBlank()) {
                    toast("fcc import: no market state configured (set RADIO_MARKET_STATE)")
                } else {
                    toast("fcc ${out.state}: ${out.inserted} new, ${out.updated} updated")
                }
            }.onFailure { toast("fcc import failed: ${it.message}") }
        }
    }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item {
            PageHeader(
                "Stations",
                "${favorites.size} favorited · sampler runs in the background",
            ) {
                OutlinedButton(onClick = { fccImport() }) {
                    Icon(Icons.Filled.Download, contentDescription = null, modifier = Modifier.size(14.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("import fcc fm")
                }
            }
        }

        item { StationSearchCard(onFavorite) }

        item {
            if (compact) {
                Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    FavoritesCard(
                        modifier = Modifier.fillMaxWidth(),
                        favorites = favorites,
                        loading = favState.loading,
                        error = favState.error,
                        refresh = favState.refresh,
                        selectedId = selectedId,
                        onSelect = { selectedId = it },
                        onForget = { forget(it) },
                    )
                    if (selected != null) {
                        StationDetailCard(selected, favState.refresh, Modifier.fillMaxWidth())
                    }
                }
            } else {
                Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                    FavoritesCard(
                        modifier = Modifier.weight(0.42f),
                        favorites = favorites,
                        loading = favState.loading,
                        error = favState.error,
                        refresh = favState.refresh,
                        selectedId = selectedId,
                        onSelect = { selectedId = it },
                        onForget = { forget(it) },
                    )
                    Box(Modifier.weight(0.58f)) {
                        if (selected != null) {
                            StationDetailCard(selected, favState.refresh, Modifier.fillMaxWidth())
                        } else {
                            DomovoiCard(Modifier.fillMaxWidth()) {
                                EmptyState(
                                    "pick a favorite",
                                    "or search above to find stations",
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

/* ---- Favorites card ---------------------------------------------------------- */

@Composable
private fun FavoritesCard(
    modifier: Modifier,
    favorites: List<Station>,
    loading: Boolean,
    error: String?,
    refresh: () -> Unit,
    selectedId: Long?,
    onSelect: (Long) -> Unit,
    onForget: (Station) -> Unit,
) {
    DomovoiCard(modifier, padding = 0) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(
                Icons.Filled.Star, contentDescription = null,
                tint = Domovoi.colors.brand, modifier = Modifier.size(13.dp),
            )
            Text(
                "Favorites",
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
                color = Domovoi.colors.fg,
            )
            Spacer(Modifier.weight(1f))
            Text(
                "${favorites.size}",
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgFaint,
            )
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        when {
            loading && favorites.isEmpty() -> LoadingState()
            error != null && favorites.isEmpty() -> ErrorState(error, refresh)
            favorites.isEmpty() -> EmptyState(
                "no favorites yet",
                "search and tap the star to start collecting",
            )
            else -> Column(Modifier.fillMaxWidth()) {
                favorites.forEach { s ->
                    FavoriteRow(
                        s = s,
                        active = selectedId == s.id,
                        onSelect = { onSelect(s.id) },
                        onForget = { onForget(s) },
                        refresh = refresh,
                    )
                }
            }
        }
    }
}

/* ---- Favorite row + inline controls -------------------------------------------- */

@Composable
private fun FavoriteRow(
    s: Station,
    active: Boolean,
    onSelect: () -> Unit,
    onForget: () -> Unit,
    refresh: () -> Unit,
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var editing by remember(s.id) { mutableStateOf(false) }
    var draft by remember(s.id, s.sample_interval_sec) {
        mutableStateOf((s.sample_interval_sec ?: 300).toString())
    }
    var confirmForget by remember(s.id) { mutableStateOf(false) }

    fun playHere() {
        // FM/SDR rows without a resolved simulcast have nothing the app can
        // stream — the radio proxy would 409. Be honest up front.
        if ((s.source == "fm" || s.source == "sdr") && s.stream_url.isNullOrBlank()) {
            toast("${s.name} is FM/SDR with no stream URL — play it through a room instead")
            return
        }
        app.player.playItems(listOf(PlayItem.fromStation(s.id, s.name)))
        toast("streaming ${s.name} here")
    }

    fun saveInterval() {
        val v = draft.trim().toIntOrNull()
        if (v == null || v < 30 || v > 86400) {
            toast("interval must be between 30 and 86400 s")
            return
        }
        scope.launch {
            runCatching {
                app.api.patch(
                    "/api/plugins/radio/stations/${s.id}",
                    buildJsonObject { put("sample_interval_sec", v) },
                )
            }.onSuccess {
                toast("${s.name}: sampling every ${v}s")
                editing = false
                refresh()
            }.onFailure { toast("save failed: ${it.message}") }
        }
    }

    Column(
        Modifier.fillMaxWidth()
            .background(if (active) Domovoi.colors.brandSoft else Color.Transparent),
    ) {
        Row(
            Modifier.fillMaxWidth().clickable(onClick = onSelect)
                .padding(horizontal = 14.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Column(Modifier.weight(1f)) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Icon(
                        if (s.source == "fm") Icons.Filled.CellTower else Icons.Filled.Radio,
                        contentDescription = null,
                        tint = Domovoi.colors.fgMuted,
                        modifier = Modifier.size(12.dp),
                    )
                    Text(
                        s.name,
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.Medium,
                        color = Domovoi.colors.fg,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
                NowPlayingLine(s)
                val head = if (s.source == "fm" && s.frequency_mhz != null) {
                    "${fmtFreq(s.frequency_mhz)} FM"
                } else {
                    s.country_code ?: "online"
                }
                Text(
                    "$head · sampling every ${s.sample_interval_sec ?: "—"}s",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
            StatusDot(
                if (s.last_sampled_at != null) Tone.Ok else Tone.Idle,
                live = s.last_sampled_at != null,
            )
        }

        if (active) {
            Row(
                Modifier.fillMaxWidth().padding(start = 14.dp, end = 14.dp, bottom = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                if (editing) {
                    OutlinedTextField(
                        value = draft,
                        onValueChange = { draft = it },
                        singleLine = true,
                        textStyle = MaterialTheme.typography.bodySmall,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        suffix = {
                            Text("s", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
                        },
                        modifier = Modifier.width(110.dp),
                    )
                    Button(onClick = { saveInterval() }) { Text("save") }
                    OutlinedButton(onClick = { editing = false }) { Text("cancel") }
                } else {
                    OutlinedButton(onClick = { playHere() }) {
                        Icon(Icons.Filled.Headset, contentDescription = null, modifier = Modifier.size(14.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("play here")
                    }
                    OutlinedButton(onClick = { editing = true }) {
                        Icon(Icons.Filled.Edit, contentDescription = null, modifier = Modifier.size(14.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("interval")
                    }
                    Spacer(Modifier.weight(1f))
                    OutlinedButton(onClick = { confirmForget = true }) {
                        Icon(
                            Icons.Filled.DeleteOutline, contentDescription = null,
                            tint = Domovoi.colors.err, modifier = Modifier.size(14.dp),
                        )
                        Spacer(Modifier.width(4.dp))
                        Text("forget", color = Domovoi.colors.err)
                    }
                }
            }
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)
    }

    if (confirmForget) {
        ConfirmDialog(
            title = "forget ${s.name}?",
            body = "Removes the station and stops sampling it.",
            confirmLabel = "forget",
            destructive = true,
            onConfirm = { onForget() },
            onDismiss = { confirmForget = false },
        )
    }
}

/** Now-playing line for a favorited station — driven by the ICY poller cache.
 *  icy_supported == false means the audio sampler is the only detection
 *  source for this station; show that quietly. */
@Composable
private fun NowPlayingLine(s: Station) {
    val np = s.now_playing
    when {
        np != null -> Row(
            Modifier.padding(top = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Icon(
                Icons.Filled.MusicNote, contentDescription = null,
                tint = Domovoi.colors.fg, modifier = Modifier.size(10.dp),
            )
            Text(
                np,
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f, fill = false),
            )
            if (s.now_playing_updated_at != null) {
                Text(
                    "· ${relTime(s.now_playing_updated_at)}",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgFaint,
                )
            }
        }
        s.icy_supported == false -> Text(
            "no live metadata",
            style = MaterialTheme.typography.labelSmall,
            fontStyle = FontStyle.Italic,
            color = Domovoi.colors.fgFaint,
            modifier = Modifier.padding(top = 2.dp),
        )
        else -> Text(
            "listening…",
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgMuted,
            modifier = Modifier.padding(top = 2.dp),
        )
    }
}
