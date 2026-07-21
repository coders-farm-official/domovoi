package com.domovoi.app.ui.screens.people

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.window.core.layout.WindowWidthSizeClass
import com.domovoi.app.net.ApiState
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.AvatarBubble
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.isLive
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi

/**
 * People page — household roster + per-person profile / memory / sessions /
 * conversations, mirroring web/static/people.jsx. Two-pane on expanded
 * widths; roster-then-full-screen-detail on compact.
 */
@Composable
fun PeopleScreen() {
    val peopleState = rememberApi(eventTypes = setOf("people.last_seen.changed")) { app ->
        app.api.get("/api/people").decode<List<Person>>()
    }
    val denylistState = rememberApi { app ->
        app.api.get("/api/denylist").decode<List<DenylistEntry>>()
    }

    val people = peopleState.data ?: emptyList()
    val denylistCount = denylistState.data?.size ?: 0
    val liveCount = people.count { isLive(it.last_seen_at) }

    var selectedId by remember { mutableStateOf<Long?>(null) }
    var denylistOpen by remember { mutableStateOf(false) }
    var search by remember { mutableStateOf("") }
    val selected = people.firstOrNull { it.id == selectedId }

    val compact = currentWindowAdaptiveInfo().windowSizeClass.windowWidthSizeClass ==
        WindowWidthSizeClass.COMPACT
    val sub = "${people.size} enrolled · $liveCount heard in the last 5 min"

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        if (compact) {
            when {
                denylistOpen -> {
                    BackHandler { denylistOpen = false }
                    DenylistView(denylistCount, onBack = { denylistOpen = false })
                }
                selected != null -> {
                    BackHandler { selectedId = null }
                    PersonDetail(
                        person = selected,
                        compact = true,
                        onBack = { selectedId = null },
                        onForgotten = { selectedId = null; peopleState.refresh() },
                        modifier = Modifier.fillMaxSize(),
                    )
                }
                else -> {
                    PageHeader("People", sub)
                    Spacer(Modifier.height(12.dp))
                    RosterPane(
                        state = peopleState,
                        search = search,
                        onSearch = { search = it },
                        selectedId = null,
                        denylistOpen = false,
                        denylistCount = denylistCount,
                        onSelect = { selectedId = it },
                        onDenylist = { denylistOpen = true },
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        } else {
            PageHeader("People", sub)
            Spacer(Modifier.height(12.dp))
            Row(Modifier.weight(1f), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                RosterPane(
                    state = peopleState,
                    search = search,
                    onSearch = { search = it },
                    selectedId = selectedId,
                    denylistOpen = denylistOpen,
                    denylistCount = denylistCount,
                    onSelect = { selectedId = it; denylistOpen = false },
                    onDenylist = { denylistOpen = true; selectedId = null },
                    modifier = Modifier.width(300.dp),
                )
                Box(Modifier.weight(1f)) {
                    when {
                        denylistOpen -> DenylistView(denylistCount, onBack = { denylistOpen = false })
                        selected != null -> PersonDetail(
                            person = selected,
                            compact = false,
                            onBack = {},
                            onForgotten = { selectedId = null; peopleState.refresh() },
                            modifier = Modifier.fillMaxSize(),
                        )
                        else -> DomovoiCard(Modifier.fillMaxSize()) {
                            EmptyState(
                                "pick someone from the list",
                                "profiles, sessions, and the full conversation log live here",
                            )
                        }
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Roster rail — filter input, roster rows, denylist entry point.
// ---------------------------------------------------------------------------

@Composable
private fun RosterPane(
    state: ApiState<List<Person>>,
    search: String,
    onSearch: (String) -> Unit,
    selectedId: Long?,
    denylistOpen: Boolean,
    denylistCount: Int,
    onSelect: (Long) -> Unit,
    onDenylist: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val people = state.data ?: emptyList()
    val filtered = people.filter { search.isBlank() || it.name.contains(search, ignoreCase = true) }

    Column(modifier, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        DomovoiCard(Modifier.weight(1f), padding = 0) {
            Box(Modifier.padding(10.dp)) {
                OutlinedTextField(
                    value = search,
                    onValueChange = onSearch,
                    modifier = Modifier.fillMaxWidth(),
                    placeholder = { Text("filter by name…", color = Domovoi.colors.fgSubtle) },
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
            }
            HorizontalDivider(color = Domovoi.colors.borderSoft)
            when {
                state.loading && state.data == null -> LoadingState()
                state.error != null && state.data == null ->
                    ErrorState(state.error ?: "request failed", state.refresh)
                filtered.isEmpty() -> Box(
                    Modifier.fillMaxWidth().padding(18.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        if (people.isEmpty()) "nobody enrolled yet" else "no match",
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                    )
                }
                else -> LazyColumn(Modifier.weight(1f)) {
                    items(filtered, key = { it.id }) { p ->
                        RosterRow(
                            p = p,
                            active = !denylistOpen && selectedId == p.id,
                            onClick = { onSelect(p.id) },
                        )
                        HorizontalDivider(color = Domovoi.colors.borderSoft)
                    }
                }
            }
        }

        DomovoiCard(Modifier.fillMaxWidth().clickable(onClick = onDenylist), padding = 12) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Icon(
                    Icons.Filled.MicOff,
                    contentDescription = null,
                    modifier = Modifier.size(16.dp),
                    tint = if (denylistOpen) Domovoi.colors.brand else Domovoi.colors.fgMuted,
                )
                Column(Modifier.weight(1f)) {
                    Text(
                        "Voice denylist",
                        style = MaterialTheme.typography.bodyMedium,
                        color = if (denylistOpen) Domovoi.colors.brand else Domovoi.colors.fg,
                    )
                    Text(
                        "opted-out voices · names redacted",
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                    )
                }
                Text(
                    "$denylistCount",
                    style = MaterialTheme.typography.labelMedium,
                    color = Domovoi.colors.fgFaint,
                )
            }
        }
    }
}

@Composable
private fun RosterRow(p: Person, active: Boolean, onClick: () -> Unit) {
    val live = isLive(p.last_seen_at)
    Row(
        Modifier
            .fillMaxWidth()
            .background(if (active) Domovoi.colors.brandSoft else Color.Transparent)
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        AvatarBubble(p.name, 36)
        Column(Modifier.weight(1f)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    p.name,
                    style = MaterialTheme.typography.bodyLarge,
                    color = if (active) Domovoi.colors.brandPress else Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (live) StatusDot(Tone.Ok, live = true)
            }
            Text(
                "${relTime(p.last_seen_at)} · ${p.presence_tier ?: "high"}",
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgMuted,
            )
        }
        Text(
            "${p.voice_profile_count}p",
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgFaint,
        )
    }
}

// ---------------------------------------------------------------------------
// Denylist view — informational, names are never persisted.
// ---------------------------------------------------------------------------

@Composable
private fun DenylistView(count: Int, onBack: () -> Unit, modifier: Modifier = Modifier) {
    DomovoiCard(modifier.fillMaxWidth(), padding = 0) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            TextButton(onClick = onBack) { Text("back", color = Domovoi.colors.fgMuted) }
            Text(
                "Voice denylist",
                style = MaterialTheme.typography.titleSmall,
                color = Domovoi.colors.fg,
            )
            Spacer(Modifier.weight(1f))
            Pill("$count opted out", Tone.Idle)
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        Column(
            Modifier.fillMaxWidth().padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            Text(
                "Opted-out voices are never persisted, never named, and never appear in this " +
                    "list. Domovoi hashes their embedding so it can ignore them on future " +
                    "utterances — but the hash isn't reversible to anything human-readable.",
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgMuted,
                textAlign = TextAlign.Center,
                modifier = Modifier.widthIn(max = 440.dp),
            )
            Row(
                Modifier
                    .background(Domovoi.colors.sunken, RoundedCornerShape(10.dp))
                    .border(1.dp, Domovoi.colors.borderSoft, RoundedCornerShape(10.dp))
                    .padding(horizontal = 18.dp, vertical = 14.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Icon(
                    Icons.Filled.MicOff,
                    contentDescription = null,
                    modifier = Modifier.size(18.dp),
                    tint = Domovoi.colors.fgMuted,
                )
                Column {
                    Text(
                        "$count",
                        style = MaterialTheme.typography.headlineMedium,
                        color = Domovoi.colors.fg,
                    )
                    Text(
                        "hashes on file",
                        style = MaterialTheme.typography.labelSmall,
                        color = Domovoi.colors.fgMuted,
                    )
                }
            }
        }
    }
}
