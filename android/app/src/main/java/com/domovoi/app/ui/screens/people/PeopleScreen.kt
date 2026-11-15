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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.window.core.layout.WindowWidthSizeClass
import com.domovoi.app.LocalApp
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
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope

/**
 * People page — household roster + per-person profile / memory / sessions /
 * conversations, mirroring web/static/people.jsx. Two-pane on expanded
 * widths; roster-then-full-screen-detail on compact. Since the nav regroup
 * it is Settings → Household → People, [embedded] under the Settings
 * header (which names the page), so its own header shrinks to the counts.
 */
@Composable
fun PeopleScreen(embedded: Boolean = false) {
    // Voice ID's state is read with the list (alongside it) and refreshed
    // with it; its own failures never fail the list (see [loadVoiceId]).
    val peopleState = rememberApi(eventTypes = setOf("people.last_seen.changed")) { app ->
        coroutineScope {
            val voiceId = async { loadVoiceId { app.api.get("/api/people/voice-id") } }
            PeoplePage(app.api.get("/api/people").decode<List<Person>>(), voiceId.await())
        }
    }
    val denylistState = rememberApi { app ->
        app.api.get("/api/denylist").decode<List<DenylistEntry>>()
    }

    val people = peopleState.data?.people ?: emptyList()
    // What the notices go by: the last answer, kept across a failed read and
    // dropped when the server changes (one server's state never shows as
    // another's).
    val serverUrl = LocalApp.current.prefs.serverUrl.value
    var voiceId by remember(serverUrl) { mutableStateOf<VoiceIdStatus?>(null) }
    LaunchedEffect(peopleState.data) {
        peopleState.data?.let { voiceId = nextVoiceId(voiceId, it.voiceId) }
    }
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
                        onRenamed = { peopleState.refresh() },
                        voiceId = voiceId,
                    )
                }
                else -> {
                    PeopleHeader(embedded, sub)
                    Spacer(Modifier.height(12.dp))
                    VoiceIdNoticeBox(voiceId)
                    RosterPane(
                        state = peopleState,
                        search = search,
                        onSearch = { search = it },
                        voiceId = voiceId,
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
            PeopleHeader(embedded, sub)
            Spacer(Modifier.height(12.dp))
            VoiceIdNoticeBox(voiceId)
            Row(Modifier.weight(1f), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                RosterPane(
                    state = peopleState,
                    search = search,
                    onSearch = { search = it },
                    voiceId = voiceId,
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
                            onRenamed = { peopleState.refresh() },
                            voiceId = voiceId,
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

/** Voice ID's household notice (web VoiceIdNotice) above the list, with the
 *  gap the web gives it; nothing when there is nothing to say. */
@Composable
private fun VoiceIdNoticeBox(voiceId: VoiceIdStatus?) {
    val notice = voiceIdNotice(voiceId) ?: return
    VoiceNoticeBox(
        notice,
        if (voiceIdOff(voiceId)) Domovoi.colors.warnSoft else Domovoi.colors.sunken,
        Modifier.semantics { liveRegion = LiveRegionMode.Polite },
    )
    Spacer(Modifier.height(12.dp))
}

/** The page's header; under Settings' own ([embedded]) only the counts. */
@Composable
private fun PeopleHeader(embedded: Boolean, sub: String) {
    if (embedded) {
        Text(sub, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
    } else {
        PageHeader("People", sub)
    }
}

// ---------------------------------------------------------------------------
// Roster rail — filter input, roster rows, denylist entry point.
// ---------------------------------------------------------------------------

@Composable
private fun RosterPane(
    state: ApiState<PeoplePage>,
    voiceId: VoiceIdStatus?,
    search: String,
    onSearch: (String) -> Unit,
    selectedId: Long?,
    denylistOpen: Boolean,
    denylistCount: Int,
    onSelect: (Long) -> Unit,
    onDenylist: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val people = state.data?.people ?: emptyList()
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
                            relearnDot = showRelearnDot(p, voiceId),
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
private fun RosterRow(p: Person, active: Boolean, relearnDot: Boolean, onClick: () -> Unit) {
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
                if (p.namesakes > 0) StatusDot(Tone.Warn)
                if (relearnDot) {
                    StatusDot(
                        Tone.Idle,
                        modifier = Modifier.semantics { contentDescription = RELEARN_DOT_DESCRIPTION },
                    )
                }
            }
            Text(
                "${rosterNameTag(p)}${relTime(p.last_seen_at)} · ${p.presence_tier ?: "high"}",
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
