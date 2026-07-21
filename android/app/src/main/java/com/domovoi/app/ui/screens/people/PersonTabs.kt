package com.domovoi.app.ui.screens.people

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
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.DomovoiGlyph
import com.domovoi.app.ui.components.RoomChip
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.components.parseInstant
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.time.Duration

// ---------------------------------------------------------------------------
// Memory tab — memories (pending / active / rejected), favorites, prefs.
// ---------------------------------------------------------------------------

@Composable
internal fun MemoryTab(person: Person, detail: PersonDetailData) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    var newBody by remember(person.id) { mutableStateOf("") }
    var newFavKind by remember(person.id) { mutableStateOf("") }
    var newFavValue by remember(person.id) { mutableStateOf("") }
    var showRejected by remember(person.id) { mutableStateOf(false) }
    var memToDelete by remember { mutableStateOf<PersonMemory?>(null) }
    var favToDelete by remember { mutableStateOf<PersonFavorite?>(null) }

    val pending = detail.memories.filter { it.status == "pending" }
    val active = detail.memories.filter { it.status == "active" }
    val rejected = detail.memories.filter { it.status == "rejected" }
    val favsByKind = detail.favorites.groupBy { it.kind }

    fun saveMemory() {
        val body = newBody.trim()
        if (body.isEmpty()) return
        scope.launch {
            runCatching {
                app.api.post(
                    "/api/people/${person.id}/memories",
                    buildJsonObject { put("body", body) },
                )
            }.onSuccess {
                newBody = ""
                toast("saved memory")
                detail.refresh()
            }.onFailure { toast("save failed: ${it.message}") }
        }
    }

    fun setStatus(m: PersonMemory, status: String) {
        scope.launch {
            runCatching {
                app.api.patch(
                    "/api/people/${person.id}/memories/${m.id}",
                    buildJsonObject { put("status", status) },
                )
            }.onSuccess {
                toast(if (status == "active") "approved" else "rejected")
                detail.refresh()
            }.onFailure { toast("patch failed: ${it.message}") }
        }
    }

    fun deleteMemory(m: PersonMemory) {
        scope.launch {
            runCatching { app.api.delete("/api/people/${person.id}/memories/${m.id}") }
                .onSuccess { toast("deleted"); detail.refresh() }
                .onFailure { toast("delete failed: ${it.message}") }
        }
    }

    fun saveFavorite() {
        val kind = newFavKind.trim().lowercase()
        val value = newFavValue.trim()
        if (kind.isEmpty() || value.isEmpty()) return
        scope.launch {
            runCatching {
                app.api.post(
                    "/api/people/${person.id}/favorites",
                    buildJsonObject { put("kind", kind); put("value", value); put("rank", 0) },
                )
            }.onSuccess {
                newFavKind = ""
                newFavValue = ""
                toast("saved favorite")
                detail.refresh()
            }.onFailure { toast("save failed: ${it.message}") }
        }
    }

    fun deleteFavorite(f: PersonFavorite) {
        scope.launch {
            runCatching { app.api.delete("/api/people/${person.id}/favorites/${f.id}") }
                .onSuccess { toast("deleted"); detail.refresh() }
                .onFailure { toast("delete failed: ${it.message}") }
        }
    }

    if (detail.loading && detail.memories.isEmpty() && detail.favorites.isEmpty() &&
        detail.preferences.isEmpty()
    ) {
        LoadingState()
        return
    }

    LazyColumn(Modifier.fillMaxSize()) {
        // ---- Memories ----
        item {
            SectionHeader(
                "memories",
                "${active.size} active" +
                    if (pending.isNotEmpty()) " · ${pending.size} pending" else "",
            )
        }
        item {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value = newBody,
                    onValueChange = { newBody = it },
                    modifier = Modifier.weight(1f),
                    placeholder = {
                        Text("add a memory…  e.g. allergic to peanuts", color = Domovoi.colors.fgSubtle)
                    },
                    singleLine = true,
                    textStyle = MaterialTheme.typography.bodyMedium,
                )
                Button(onClick = { saveMemory() }, enabled = newBody.isNotBlank()) {
                    Text("save")
                }
            }
        }

        if (pending.isNotEmpty()) {
            item {
                Text(
                    "pending — extracted from conversation",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Domovoi.colors.brandSoft)
                        .padding(start = 14.dp, end = 14.dp, top = 8.dp, bottom = 2.dp),
                )
            }
            items(pending, key = { "m${it.id}" }) { m ->
                MemoryRowView(
                    m = m,
                    highlight = true,
                    onApprove = { setStatus(m, "active") },
                    onReject = { setStatus(m, "rejected") },
                    onDelete = { memToDelete = m },
                )
            }
            item { HorizontalDivider(color = Domovoi.colors.borderSoft) }
        }

        if (active.isEmpty() && pending.isEmpty()) {
            item {
                CenterNote("no memories yet · say \"remember that ___\" or use the input above")
            }
        } else {
            items(active, key = { "m${it.id}" }) { m ->
                MemoryRowView(m = m, onDelete = { memToDelete = m })
                HorizontalDivider(color = Domovoi.colors.borderSoft)
            }
        }

        if (rejected.isNotEmpty()) {
            item {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .clickable { showRejected = !showRejected }
                        .padding(horizontal = 14.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Icon(
                        if (showRejected) Icons.Filled.ExpandMore else Icons.Filled.ChevronRight,
                        contentDescription = null,
                        modifier = Modifier.size(14.dp),
                        tint = Domovoi.colors.fgMuted,
                    )
                    Text(
                        "${rejected.size} rejected (hidden)",
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                    )
                }
            }
            if (showRejected) {
                items(rejected, key = { "m${it.id}" }) { m ->
                    MemoryRowView(m = m, dimmed = true, onDelete = { memToDelete = m })
                }
            }
        }

        // ---- Favorites ----
        item {
            SectionHeader(
                "favorites",
                "${detail.favorites.size} saved · ${favsByKind.size} " +
                    plural(favsByKind.size, "kind"),
            )
        }
        item {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value = newFavKind,
                    onValueChange = { newFavKind = it },
                    modifier = Modifier.width(110.dp),
                    placeholder = { Text("kind", color = Domovoi.colors.fgSubtle) },
                    singleLine = true,
                    textStyle = MaterialTheme.typography.bodyMedium,
                )
                OutlinedTextField(
                    value = newFavValue,
                    onValueChange = { newFavValue = it },
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("value  e.g. Mariners", color = Domovoi.colors.fgSubtle) },
                    singleLine = true,
                    textStyle = MaterialTheme.typography.bodyMedium,
                )
                Button(
                    onClick = { saveFavorite() },
                    enabled = newFavKind.isNotBlank() && newFavValue.isNotBlank(),
                ) { Text("save") }
            }
        }
        if (detail.favorites.isEmpty()) {
            item {
                CenterNote("no favorites yet · say \"my favorite ___ is ___\" or use the inputs above")
            }
        } else {
            favsByKind.forEach { (kind, favs) ->
                item(key = "k$kind") {
                    FavKindRow(kind, favs, onDelete = { favToDelete = it })
                    HorizontalDivider(color = Domovoi.colors.borderSoft)
                }
            }
        }

        // ---- Preferences (read-only) ----
        item {
            SectionHeader(
                "preferences",
                "${detail.preferences.size} " + plural(detail.preferences.size, "key"),
            )
        }
        if (detail.preferences.isEmpty()) {
            item { CenterNote("no preferences set yet") }
        } else {
            detail.preferences.forEach { (key, value) ->
                item(key = "p$key") {
                    Row(
                        Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Text(
                            key,
                            modifier = Modifier.width(120.dp),
                            style = MaterialTheme.typography.labelMedium,
                            color = Domovoi.colors.fgMuted,
                        )
                        Text(
                            prettyPref(value),
                            style = MaterialTheme.typography.bodySmall,
                            color = Domovoi.colors.fg,
                        )
                    }
                    HorizontalDivider(color = Domovoi.colors.borderSoft)
                }
            }
        }
    }

    memToDelete?.let { m ->
        ConfirmDialog(
            title = "forget memory",
            body = "Forget \"${m.body}\"?",
            confirmLabel = "forget",
            destructive = true,
            onConfirm = { deleteMemory(m) },
            onDismiss = { memToDelete = null },
        )
    }
    favToDelete?.let { f ->
        ConfirmDialog(
            title = "forget favorite",
            body = "Forget favorite ${f.kind} = ${f.value}?",
            confirmLabel = "forget",
            destructive = true,
            onConfirm = { deleteFavorite(f) },
            onDismiss = { favToDelete = null },
        )
    }
}

@Composable
private fun MemoryRowView(
    m: PersonMemory,
    dimmed: Boolean = false,
    highlight: Boolean = false,
    onApprove: (() -> Unit)? = null,
    onReject: (() -> Unit)? = null,
    onDelete: () -> Unit,
) {
    Column(
        Modifier
            .fillMaxWidth()
            .background(if (highlight) Domovoi.colors.brandSoft else Color.Transparent)
            .alpha(if (dimmed) 0.55f else 1f)
            .padding(horizontal = 14.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(m.body, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
        Text(
            listOfNotNull(m.source, m.status, m.topic).joinToString(" · ") +
                " · " + relTime(m.created_at),
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgMuted,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            if (onApprove != null) {
                TextButton(onClick = onApprove) { Text("approve", color = Domovoi.colors.ok) }
            }
            if (onReject != null) {
                TextButton(onClick = onReject) { Text("reject", color = Domovoi.colors.fgMuted) }
            }
            TextButton(onClick = onDelete) { Text("forget", color = Domovoi.colors.err) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun FavKindRow(
    kind: String,
    favs: List<PersonFavorite>,
    onDelete: (PersonFavorite) -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            kind,
            modifier = Modifier.width(90.dp).padding(top = 6.dp),
            style = MaterialTheme.typography.labelMedium,
            color = Domovoi.colors.fgMuted,
        )
        FlowRow(
            Modifier.weight(1f),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            favs.forEach { f ->
                Row(
                    Modifier
                        .background(Domovoi.colors.sunken, RoundedCornerShape(999.dp))
                        .border(1.dp, Domovoi.colors.border, RoundedCornerShape(999.dp))
                        .padding(horizontal = 10.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text(f.value, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg)
                    Icon(
                        Icons.Filled.Close,
                        contentDescription = "forget",
                        modifier = Modifier.size(12.dp).clickable { onDelete(f) },
                        tint = Domovoi.colors.fgFaint,
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Sessions tab — expandable session rows with that session's turns.
// ---------------------------------------------------------------------------

@Composable
internal fun SessionsTab(person: Person, detail: PersonDetailData) {
    if (detail.loading && detail.sessions.isEmpty()) {
        LoadingState()
        return
    }
    if (detail.sessions.isEmpty()) {
        EmptyState(
            "${person.name} hasn't had a session yet",
            "sessions appear once they've spoken at least once",
        )
        return
    }
    LazyColumn(Modifier.fillMaxSize()) {
        items(detail.sessions, key = { it.id }) { s ->
            SessionRowView(s, detail.conversations.filter { it.session_id == s.id })
            HorizontalDivider(color = Domovoi.colors.borderSoft)
        }
    }
}

@Composable
private fun SessionRowView(s: PersonSession, turns: List<PersonTurn>) {
    var open by remember(s.id) { mutableStateOf(false) }
    val durSec = run {
        val a = parseInstant(s.started_at)
        val b = parseInstant(s.last_activity)
        if (a != null && b != null) Duration.between(a, b).seconds.toDouble() else 0.0
    }
    Column {
        Row(
            Modifier
                .fillMaxWidth()
                .clickable { open = !open }
                .padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Icon(
                if (open) Icons.Filled.ExpandMore else Icons.Filled.ChevronRight,
                contentDescription = null,
                modifier = Modifier.size(16.dp),
                tint = Domovoi.colors.fgMuted,
            )
            Column(Modifier.weight(1f)) {
                Text(
                    relTime(s.started_at),
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fg,
                )
                Text(
                    "${s.id.take(8).ifEmpty { "—" }} · ${fmtDur(durSec)}",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
            RoomChip(s.room_id)
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    "${s.intent_count} " + plural(s.intent_count, "turn"),
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                )
                Text(
                    "last ${relTime(s.last_activity)}",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgFaint,
                )
            }
        }
        if (open) {
            Column(
                Modifier
                    .fillMaxWidth()
                    .background(Domovoi.colors.sunken)
                    .padding(start = 38.dp, end = 12.dp, top = 4.dp, bottom = 12.dp),
            ) {
                if (turns.isEmpty()) {
                    Text(
                        "no conversation rows in this session",
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgFaint,
                        modifier = Modifier.padding(vertical = 10.dp),
                    )
                } else {
                    turns.forEach { c ->
                        HorizontalDivider(color = Domovoi.colors.borderSoft)
                        TurnBrief(c)
                    }
                }
            }
        }
    }
}

@Composable
private fun TurnBrief(c: PersonTurn) {
    Column(
        Modifier.fillMaxWidth().padding(vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(3.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                relTime(c.at),
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgFaint,
            )
            Pill(
                c.matched_handler ?: "qa",
                if (c.matched_handler != null) Tone.Ok else Tone.Idle,
            )
            Text(
                c.matched_path ?: "",
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgFaint,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Text(
            "“${c.user_text ?: ""}”",
            style = MaterialTheme.typography.bodyMedium,
            color = Domovoi.colors.fg,
        )
        Row(
            verticalAlignment = Alignment.Top,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            DomovoiGlyph(12)
            Text(
                c.assistant_text ?: "",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
            )
        }
    }
}

// ---------------------------------------------------------------------------
// Conversations tab — client-side search + turn cards with more/less.
// ---------------------------------------------------------------------------

@Composable
internal fun ConversationsTab(person: Person, detail: PersonDetailData) {
    var q by remember(person.id) { mutableStateOf("") }
    val filtered = detail.conversations.filter { c ->
        q.isBlank() ||
            ((c.user_text ?: "") + " " + (c.assistant_text ?: "")).contains(q, ignoreCase = true)
    }

    if (detail.loading && detail.conversations.isEmpty()) {
        LoadingState()
        return
    }

    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            OutlinedTextField(
                value = q,
                onValueChange = { q = it },
                modifier = Modifier.weight(1f),
                placeholder = {
                    Text(
                        "search what they said or what domovoi said…",
                        color = Domovoi.colors.fgSubtle,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                leadingIcon = {
                    Icon(
                        Icons.Filled.Search,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                        tint = Domovoi.colors.fgSubtle,
                    )
                },
                singleLine = true,
                textStyle = MaterialTheme.typography.bodyMedium,
            )
            Text(
                "${filtered.size} of ${detail.conversations.size}",
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgFaint,
            )
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        if (filtered.isEmpty()) {
            EmptyState(
                "nothing matches",
                if (q.isNotBlank()) "q = “$q”" else "${person.name} hasn't said anything yet",
            )
        } else {
            LazyColumn(Modifier.weight(1f)) {
                items(filtered, key = { it.id }) { c ->
                    TurnCard(c)
                    HorizontalDivider(color = Domovoi.colors.borderSoft)
                }
            }
        }
    }
}

@Composable
private fun TurnCard(c: PersonTurn) {
    val txt = c.assistant_text ?: ""
    val isLong = txt.length > 140
    var more by remember(c.id) { mutableStateOf(false) }
    Column(
        Modifier.fillMaxWidth().padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                relTime(c.at),
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgFaint,
            )
            RoomChip(c.room_id)
            Pill(
                c.matched_handler ?: "qa",
                if (c.matched_handler != null) Tone.Ok else Tone.Idle,
            )
            Spacer(Modifier.weight(1f))
            Text(
                "#${c.id}",
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgFaint,
            )
        }
        Text(
            "“${c.user_text ?: ""}”",
            style = MaterialTheme.typography.bodyMedium,
            color = Domovoi.colors.fg,
        )
        Row(
            verticalAlignment = Alignment.Top,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            DomovoiGlyph(12)
            Column(Modifier.weight(1f)) {
                Text(
                    if (isLong && !more) txt.take(130).trimEnd() + "…" else txt,
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
                if (isLong) {
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

// ---------------------------------------------------------------------------
// Small shared pieces
// ---------------------------------------------------------------------------

@Composable
private fun SectionHeader(title: String, meta: String) {
    Column {
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(title, style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg)
            Text(meta, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint)
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)
    }
}

@Composable
private fun CenterNote(text: String) {
    Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) {
        Text(
            text,
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgFaint,
            textAlign = TextAlign.Center,
        )
    }
}
