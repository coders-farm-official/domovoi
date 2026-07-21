package com.domovoi.app.ui.screens.satellites

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.toneForSlug
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.DomovoiGlyph
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.components.parseInstant
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.time.Duration

/* ---- Sessions -------------------------------------------------------------- */

@Composable
fun SatSessionsTab(room: String) {
    val sessionsState = rememberApi(room) {
        it.api.get("/api/satellites/$room/sessions?limit=100").decode<List<SatSession>>()
    }
    val convosState = rememberApi(room) {
        it.api.get("/api/satellites/$room/conversations?limit=300").decode<List<SatTurn>>()
    }
    val sessions = sessionsState.data
    val convos = convosState.data ?: emptyList()
    when {
        sessionsState.loading && sessions == null -> LoadingState()
        sessions == null -> ErrorState(sessionsState.error ?: "request failed", sessionsState.refresh)
        sessions.isEmpty() -> EmptyState("no sessions in this room")
        else -> Column(Modifier.fillMaxWidth()) {
            sessions.forEach { sess ->
                SessionRow(sess, convos.filter { it.session_id == sess.id })
            }
        }
    }
}

@Composable
private fun SessionRow(session: SatSession, turns: List<SatTurn>) {
    var open by remember(session.id) { mutableStateOf(false) }
    val durSec = run {
        val a = parseInstant(session.started_at)
        val b = parseInstant(session.last_activity)
        if (a != null && b != null) Duration.between(a, b).seconds.toDouble() else 0.0
    }
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().clickable { open = !open }
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Icon(
                if (open) Icons.Filled.KeyboardArrowDown else Icons.AutoMirrored.Filled.KeyboardArrowRight,
                contentDescription = null,
                tint = Domovoi.colors.fgMuted,
                modifier = Modifier.size(16.dp),
            )
            Column(Modifier.weight(1f)) {
                Text(
                    relTime(session.started_at),
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                    color = Domovoi.colors.fg,
                )
                Text(
                    "${(session.id ?: "").take(8)} · ${fmtDur(durSec)}" +
                        (session.person_id?.let { " · person #$it" } ?: ""),
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
            Text(
                "${session.intent_count} turn" + (if (session.intent_count == 1) "" else "s"),
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgMuted,
            )
            Text(
                "last ${relTime(session.last_activity)}",
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgFaint,
            )
        }
        if (open) {
            Column(
                Modifier.fillMaxWidth().background(Domovoi.colors.sunken)
                    .padding(start = 42.dp, end = 16.dp, bottom = 12.dp, top = 4.dp),
            ) {
                if (turns.isEmpty()) {
                    Text(
                        "no turns recorded",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgFaint,
                        modifier = Modifier.padding(vertical = 12.dp),
                    )
                } else {
                    turns.forEach { TurnCard(it, showId = false) }
                }
            }
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)
    }
}

/** One conversation turn — shared by the sessions expansion and the
 *  conversations tab (web SatConversationTurn analog, with more/less). */
@Composable
private fun TurnCard(c: SatTurn, showId: Boolean) {
    var more by remember(c.id) { mutableStateOf(false) }
    val txt = c.assistant_text ?: ""
    val long = txt.length > 140
    val shown = if (long && !more) txt.take(130).trimEnd() + "…" else txt

    Column(Modifier.fillMaxWidth().padding(vertical = 10.dp)) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(relTime(c.at), style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint)
            Pill(c.matched_handler ?: "qa", if (c.matched_handler != null) Tone.Brand else Tone.Idle)
            if (showId) {
                Spacer(Modifier.weight(1f))
                Text("#${c.id}", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint)
            } else if (c.matched_path != null) {
                Text(
                    c.matched_path,
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgFaint,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
        }
        Text(
            "“${c.user_text ?: ""}”",
            style = MaterialTheme.typography.bodyMedium,
            color = Domovoi.colors.fg,
            modifier = Modifier.padding(top = 4.dp),
        )
        Row(
            Modifier.padding(top = 3.dp),
            verticalAlignment = Alignment.Top,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            DomovoiGlyph(size = 12)
            Column {
                Text(shown, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
                if (long) {
                    Text(
                        if (more) "less" else "more",
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.brand,
                        modifier = Modifier.clickable { more = !more }.padding(top = 2.dp),
                    )
                }
            }
        }
    }
}

/* ---- Conversations ---------------------------------------------------------- */

@Composable
fun SatConversationsTab(room: String) {
    var q by remember(room) { mutableStateOf("") }
    val state = rememberApi(room) {
        it.api.get("/api/satellites/$room/conversations?limit=300").decode<List<SatTurn>>()
    }
    val all = state.data
    when {
        state.loading && all == null -> LoadingState()
        all == null -> ErrorState(state.error ?: "request failed", state.refresh)
        else -> {
            val filtered = all.filter {
                q.isBlank() ||
                    ("${it.user_text ?: ""} ${it.assistant_text ?: ""}").contains(q, ignoreCase = true)
            }
            Column(Modifier.fillMaxWidth()) {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    OutlinedTextField(
                        value = q,
                        onValueChange = { q = it },
                        placeholder = { Text("search this room…", color = Domovoi.colors.fgSubtle) },
                        leadingIcon = {
                            Icon(
                                Icons.Filled.Search, contentDescription = null,
                                tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(16.dp),
                            )
                        },
                        singleLine = true,
                        textStyle = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.weight(1f),
                    )
                    Text(
                        "${filtered.size} of ${all.size}",
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgFaint,
                    )
                }
                HorizontalDivider(color = Domovoi.colors.borderSoft)
                if (filtered.isEmpty()) {
                    EmptyState(
                        "nothing said in this room",
                        if (q.isNotBlank()) "q = “$q”" else "satellite has been quiet",
                    )
                } else {
                    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
                        filtered.forEach { c ->
                            TurnCard(c, showId = true)
                            HorizontalDivider(color = Domovoi.colors.borderSoft)
                        }
                    }
                }
            }
        }
    }
}

/* ---- Recently played --------------------------------------------------------- */

/** Source pill tone — driven by /api/capabilities handler_display (design
 *  §8): the server supplies a tone slug per handler/source name; unknown
 *  names render neutral. Core "library" keeps its brand accent. */
@Composable
private fun playSourceTone(source: String?): Tone {
    if (source == "library" || source == "playlist") return Tone.Brand
    return toneForSlug(LocalCapabilities.current.toneFor(source))
}

@Composable
fun SatRecentlyPlayedTab(room: String) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val state = rememberApi(room) {
        it.api.get("/api/satellites/$room/recently-played?limit=100").decode<List<SatPlayed>>()
    }
    // Rows added this session — flip to "queued" optimistically; the row only
    // truly drops off once the async download lands.
    var queued by remember(room) { mutableStateOf(setOf<Long>()) }

    // Generic acquisition enqueue (design §4.8): the exact stored URL goes
    // to the media-acquisition queue; whichever fulfiller plugin is
    // installed claims it. The server's `can_add` flag on each row already
    // reflects fulfiller availability.
    fun add(r: SatPlayed) {
        scope.launch {
            runCatching {
                app.api.post(
                    "/api/music/add-by-url",
                    buildJsonObject {
                        put("room_id", room)
                        put("url", r.url)
                        put("title", r.title)
                    },
                ).decode<AddByUrlResult>()
            }.onSuccess { res ->
                val label = r.title ?: "track"
                when {
                    res.already_in_library -> toast("already in library: $label")
                    res.already_downloading -> toast("already queued: $label")
                    res.message != null -> toast(res.message)
                    else -> toast("queued for download: $label")
                }
                queued = queued + r.id
                state.refresh()
            }.onFailure { toast("add failed: ${it.message}") }
        }
    }

    val rows = state.data
    when {
        state.loading && rows == null -> LoadingState()
        rows == null -> ErrorState(state.error ?: "request failed", state.refresh)
        rows.isEmpty() -> EmptyState(
            "nothing played in this room yet",
            "play some music and it'll show up here",
        )
        else -> Column(Modifier.fillMaxWidth()) {
            rows.forEach { r ->
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Column(Modifier.weight(1f)) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            Text(
                                relTime(r.started_at),
                                style = MaterialTheme.typography.labelSmall,
                                color = Domovoi.colors.fgMuted,
                            )
                            Pill(r.source ?: "—", playSourceTone(r.source))
                        }
                        Text(
                            r.title ?: "—",
                            style = MaterialTheme.typography.bodyMedium,
                            fontWeight = FontWeight.Medium,
                            color = Domovoi.colors.fg,
                            maxLines = 1, overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.padding(top = 2.dp),
                        )
                        val sub = r.artist ?: r.channel
                        if (sub != null) {
                            Text(
                                sub,
                                style = MaterialTheme.typography.labelSmall,
                                color = Domovoi.colors.fgFaint,
                                maxLines = 1, overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                    if (r.can_add) {
                        if (queued.contains(r.id)) {
                            Text(
                                "queued",
                                style = MaterialTheme.typography.labelSmall,
                                color = Domovoi.colors.fgFaint,
                            )
                        } else {
                            OutlinedButton(onClick = { add(r) }) {
                                Icon(Icons.Filled.Add, contentDescription = null, modifier = Modifier.size(14.dp))
                                Spacer(Modifier.width(4.dp))
                                Text("add")
                            }
                        }
                    }
                }
                HorizontalDivider(color = Domovoi.colors.borderSoft)
            }
        }
    }
}

/* ---- Notes --------------------------------------------------------------------- */

@Composable
fun SatNotesTab(room: String) {
    val state = rememberApi(room) {
        it.api.get("/api/satellites/$room/notes").decode<List<SatNote>>()
    }
    val notes = state.data
    when {
        state.loading && notes == null -> LoadingState()
        notes == null -> ErrorState(state.error ?: "request failed", state.refresh)
        notes.isEmpty() -> EmptyState(
            "no notes from this room",
            "say: domovoi, jot this down — …",
        )
        else -> Column(Modifier.fillMaxWidth()) {
            notes.forEach { n ->
                Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 14.dp)) {
                    Text(
                        "${relTime(n.captured_at)} · #${n.id}",
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgFaint,
                    )
                    Text(
                        n.body ?: "",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Domovoi.colors.fg,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }
                HorizontalDivider(color = Domovoi.colors.borderSoft)
            }
        }
    }
}

/* ---- Timers ---------------------------------------------------------------------- */

/** Web fmtRemaining analog: "1h 2m", "3m 04s", "45s". */
private fun satFmtRemaining(sec: Long?): String {
    if (sec == null) return "—"
    val h = sec / 3600
    val m = (sec % 3600) / 60
    val s = sec % 60
    return when {
        h > 0 -> "${h}h ${m}m"
        m > 0 -> "${m}m ${s.toString().padStart(2, '0')}s"
        else -> "${s}s"
    }
}

@Composable
fun SatTimersTab(room: String) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val state = rememberApi(room) {
        it.api.get("/api/satellites/$room/timers").decode<List<SatTimer>>()
    }

    // 1s tick so the remaining column counts down between fetches.
    var nowMs by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(1000)
            nowMs = System.currentTimeMillis()
        }
    }

    fun cancel(t: SatTimer) {
        scope.launch {
            runCatching { app.api.delete("/api/satellites/$room/timers/${t.id}") }
                .onSuccess {
                    toast("cancelled ${if (t.is_reminder) "reminder" else "timer"} #${t.id}")
                    state.refresh()
                }
                .onFailure { toast("cancel failed: ${it.message}") }
        }
    }

    val rows = state.data
    when {
        state.loading && rows == null -> LoadingState()
        rows == null -> ErrorState(state.error ?: "request failed", state.refresh)
        rows.isEmpty() -> EmptyState("no active timers or reminders")
        else -> Column(Modifier.fillMaxWidth()) {
            rows.forEach { t ->
                val remaining = parseInstant(t.expires_at)
                    ?.let { ((it.toEpochMilli() - nowMs) / 1000).coerceAtLeast(0) }
                val kind = if (t.is_reminder) "reminder" else "timer"
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Pill(kind, if (t.is_reminder) Tone.Idle else Tone.Brand)
                    Column(Modifier.weight(1f)) {
                        Text(
                            t.label ?: (if (t.is_reminder) t.message else null) ?: "—",
                            style = MaterialTheme.typography.bodyMedium,
                            fontWeight = FontWeight.Medium,
                            color = Domovoi.colors.fg,
                            maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            "fires ${relTime(t.expires_at)}",
                            style = MaterialTheme.typography.labelSmall,
                            color = Domovoi.colors.fgMuted,
                        )
                    }
                    Text(
                        satFmtRemaining(remaining),
                        style = MaterialTheme.typography.bodySmall,
                        color = if (remaining != null && remaining < 600) Domovoi.colors.warn else Domovoi.colors.fg,
                    )
                    OutlinedButton(onClick = { cancel(t) }) { Text("cancel") }
                }
                HorizontalDivider(color = Domovoi.colors.borderSoft)
            }
        }
    }
}
