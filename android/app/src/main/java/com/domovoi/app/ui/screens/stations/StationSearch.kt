package com.domovoi.app.ui.screens.stations

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.StarBorder
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.decode
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.net.URLEncoder

private val ONLINE_TAG_PRESETS = listOf("indie", "jazz", "classical", "news", "electronic", "rock")
private const val PAGE_SIZE = 30

private fun enc(s: String): String = URLEncoder.encode(s, "UTF-8")

/**
 * Two scopes, one search surface (web StationSearch analog):
 *   - "online" → /api/plugins/radio/search (radio-browser catalog)
 *   - "fm"     → /api/plugins/radio/stations?source=fm against the FCC import,
 *                numeric input becomes an exact frequency_mhz match.
 * Pagination is "full-page-looks-like-there's-more" — no total count.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun StationSearchCard(onFavorite: suspend (Station) -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    var scopeSel by remember { mutableStateOf("online") } // "online" | "fm"
    var q by remember { mutableStateOf("") }
    var country by remember { mutableStateOf("US") }
    var results by remember { mutableStateOf<List<Station>>(emptyList()) }
    var offset by remember { mutableIntStateOf(0) }
    var loading by remember { mutableStateOf(false) }
    var submitted by remember { mutableStateOf(false) }

    suspend fun doSearch(newOffset: Int, queryText: String) {
        if (scopeSel == "online" && queryText.isEmpty() && country.trim().isEmpty()) {
            results = emptyList()
            submitted = false
            return
        }
        loading = true
        try {
            val out: List<Station> = if (scopeSel == "online") {
                val cc = country.trim().uppercase()
                val p = StringBuilder()
                if (queryText.isNotEmpty()) p.append("q=${enc(queryText)}&")
                if (cc.isNotEmpty()) p.append("country_code=${enc(cc)}&")
                p.append("limit=$PAGE_SIZE&offset=$newOffset")
                app.api.get("/api/plugins/radio/search?$p").decode()
            } else {
                val p = StringBuilder("source=fm&limit=$PAGE_SIZE&offset=$newOffset")
                if (queryText.isNotEmpty()) {
                    val asFreq = queryText.toDoubleOrNull()
                    if (asFreq != null) p.append("&frequency_mhz=$asFreq")
                    else p.append("&q=${enc(queryText)}")
                }
                app.api.get("/api/plugins/radio/stations?$p").decode()
            }
            results = out
            offset = newOffset
            submitted = true
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            toast("search failed: ${e.message}")
            results = emptyList()
        } finally {
            loading = false
        }
    }

    fun run(newOffset: Int, qOverride: String? = null) {
        scope.launch { doSearch(newOffset, (qOverride ?: q).trim()) }
    }

    fun switchScope(next: String) {
        if (scopeSel == next) return
        // Reset paging + results whenever scope flips — stale results from
        // the previous scope confuse the row layout below.
        scopeSel = next
        results = emptyList()
        offset = 0
        submitted = false
    }

    val page = offset / PAGE_SIZE + 1
    val hasPrev = offset > 0
    val hasNext = results.size == PAGE_SIZE

    DomovoiCard(Modifier.fillMaxWidth(), padding = 0) {
        // Scope selector
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            ScopeButton("online stations", "radio-browser catalog", scopeSel == "online") { switchScope("online") }
            ScopeButton("local fm", "fcc-imported, near you", scopeSel == "fm") { switchScope("fm") }
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)

        // Query row
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            OutlinedTextField(
                value = q,
                onValueChange = { q = it },
                placeholder = {
                    Text(
                        if (scopeSel == "online") "search stations — name, callsign, network…"
                        else "name, call sign, city, or frequency (e.g. 97.5)",
                        color = Domovoi.colors.fgSubtle,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                },
                leadingIcon = {
                    Icon(
                        Icons.Filled.Search, contentDescription = null,
                        tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(16.dp),
                    )
                },
                singleLine = true,
                textStyle = MaterialTheme.typography.bodySmall,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { run(0) }),
                modifier = Modifier.weight(1f),
            )
            if (scopeSel == "online") {
                OutlinedTextField(
                    value = country,
                    onValueChange = { country = it.take(2).uppercase() },
                    singleLine = true,
                    textStyle = MaterialTheme.typography.bodySmall,
                    placeholder = { Text("US", color = Domovoi.colors.fgSubtle) },
                    modifier = Modifier.width(72.dp),
                )
            }
            Button(onClick = { run(0) }, enabled = !loading) {
                Text(
                    when {
                        loading -> "searching…"
                        scopeSel == "fm" -> "browse"
                        else -> "search"
                    },
                )
            }
        }

        // Tag chip row — only meaningful for online (radio-browser tags).
        if (scopeSel == "online" && !loading && !submitted) {
            FlowRow(
                Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, bottom = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    "tags:",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgFaint,
                    modifier = Modifier.align(Alignment.CenterVertically),
                )
                ONLINE_TAG_PRESETS.forEach { t ->
                    Box(
                        Modifier
                            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(999.dp))
                            .clickable {
                                q = t
                                run(0, t)
                            }
                            .padding(horizontal = 10.dp, vertical = 4.dp),
                    ) {
                        Text(t, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
                    }
                }
            }
        }

        // FM hint to seed the empty state
        if (scopeSel == "fm" && !loading && !submitted) {
            Text(
                "Tip: leave the box blank and hit browse to paginate the full FCC import. " +
                    "Type a number (97.5) to filter to that frequency, or a fragment (WJIM, Lansing) " +
                    "for a name / call-sign / city match.",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(start = 16.dp, end = 16.dp, bottom = 14.dp),
            )
        }

        if (submitted && !loading && results.isEmpty()) {
            EmptyState(
                "no stations match",
                if (scopeSel == "online") {
                    if (q.isNotBlank()) "nothing in ${country.ifBlank { "all countries" }} called “$q”"
                    else "nothing for $country"
                } else {
                    if (q.isNotBlank()) "no FCC FM rows match “$q”"
                    else "no FCC FM rows imported yet — try import fcc fm"
                },
            )
        }

        if (results.isNotEmpty()) {
            HorizontalDivider(color = Domovoi.colors.borderSoft)
            Column(Modifier.fillMaxWidth()) {
                results.forEach { hit ->
                    SearchResultRow(hit, scopeSel, onFavorite)
                    HorizontalDivider(color = Domovoi.colors.borderSoft)
                }
            }
        }

        // Pagination footer
        if (submitted && (hasPrev || hasNext)) {
            Row(
                Modifier.fillMaxWidth().background(Domovoi.colors.sunken)
                    .padding(horizontal = 16.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                OutlinedButton(
                    onClick = { run((offset - PAGE_SIZE).coerceAtLeast(0)) },
                    enabled = hasPrev && !loading,
                ) { Text("prev") }
                Text(
                    "page $page · ${results.size} shown",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                )
                Spacer(Modifier.weight(1f))
                OutlinedButton(
                    onClick = { run(offset + PAGE_SIZE) },
                    enabled = hasNext && !loading,
                ) { Text("next") }
            }
        }
    }
}

@Composable
private fun ScopeButton(label: String, sub: String, selected: Boolean, onClick: () -> Unit) {
    Row(
        Modifier
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(8.dp))
            .background(
                if (selected) Domovoi.colors.brandSoft else Domovoi.colors.card,
                RoundedCornerShape(8.dp),
            )
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(
            label,
            style = MaterialTheme.typography.labelMedium,
            fontWeight = if (selected) FontWeight.Medium else FontWeight.Normal,
            color = if (selected) Domovoi.colors.brandPress else Domovoi.colors.fg,
        )
        Text(
            sub,
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgFaint,
            maxLines = 1, overflow = TextOverflow.Ellipsis,
        )
    }
}

/* ---- Search result row -------------------------------------------------------- */

@Composable
private fun SearchResultRow(
    hit: Station,
    scopeSel: String,
    onFavorite: suspend (Station) -> Unit,
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    // Local favorited flag so the toggle feels snappy — the WS push refreshes
    // the canonical favorites list, but this row should react immediately.
    var favorited by remember(hit.id, hit.external_id) { mutableStateOf(hit.favorited) }
    var busy by remember(hit.id, hit.external_id) { mutableStateOf(false) }

    fun toggle() {
        if (busy) return
        scope.launch {
            busy = true
            runCatching {
                if (hit.id != 0L) {
                    // Existing DB row (local-FM rows always have a real id).
                    val newFav = !favorited
                    app.api.patch(
                        "/api/plugins/radio/stations/${hit.id}",
                        buildJsonObject { put("favorited", newFav) },
                    )
                    favorited = newFav
                    toast(if (newFav) "favorited ${hit.name}" else "unfavorited ${hit.name}")
                    // FCC FM rows have no stream_url — right after favoriting,
                    // fire the radio-browser simulcast resolver so the poller
                    // has something to hit. Split from the PATCH so a slow or
                    // failing lookup doesn't bounce the favorite itself.
                    if (newFav && hit.source == "fm" && hit.stream_url.isNullOrBlank()) {
                        runCatching {
                            app.api.post("/api/plugins/radio/stations/${hit.id}/resolve-simulcast")
                                .decode<SimulcastResult>()
                        }.onSuccess { res ->
                            if (res.resolved) toast("simulcast found for ${hit.name}")
                            else res.message?.let { toast("no simulcast: $it") }
                        }.onFailure { toast("simulcast lookup failed: ${it.message}") }
                    }
                } else if (!favorited) {
                    // Online hit not yet persisted — POST the search-hit shape.
                    onFavorite(hit)
                    favorited = true
                }
            }.onFailure { toast("favorite failed: ${it.message}") }
            busy = false
        }
    }

    Row(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        IconButton(onClick = { toggle() }, enabled = !busy) {
            Icon(
                if (favorited) Icons.Filled.Star else Icons.Filled.StarBorder,
                contentDescription = "favorite",
                tint = if (favorited) Domovoi.colors.brand else Domovoi.colors.fgSubtle,
            )
        }
        Column(Modifier.weight(1f)) {
            if (scopeSel == "online") {
                Text(
                    hit.name,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                    color = Domovoi.colors.fg,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                if (!hit.stream_url.isNullOrBlank()) {
                    Text(
                        hit.stream_url,
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgFaint,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
                Row(
                    Modifier.padding(top = 2.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Text(
                        hit.country_code ?: "—",
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                    )
                    hit.tags.take(3).forEach { t -> SearchTagChip(t) }
                }
            } else {
                Text(
                    listOfNotNull(
                        hit.call_sign,
                        hit.frequency_mhz?.let { "${fmtFreq(it)} FM" },
                    ).joinToString(" · ").ifBlank { "—" },
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                    color = Domovoi.colors.fg,
                )
                Text(
                    hit.name.ifBlank { "—" },
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fg,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                Text(
                    listOfNotNull(hit.market_city, hit.market_state)
                        .joinToString(", ").ifBlank { "—" },
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
        }
        Pill(if (favorited) "saved" else "tap star", if (favorited) Tone.Brand else Tone.Idle)
    }
}

@Composable
private fun SearchTagChip(t: String) {
    Box(
        Modifier
            .background(Domovoi.colors.sunken, RoundedCornerShape(999.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    ) {
        Text(t, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
    }
}
