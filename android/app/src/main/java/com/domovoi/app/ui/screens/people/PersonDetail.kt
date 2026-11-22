package com.domovoi.app.ui.screens.people

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ScrollableTabRow
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.components.AvatarBubble
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Stat
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.isLive
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi

/**
 * The right pane (expanded) / full-screen overlay (compact) for a selected
 * person: Profile / Memory / Sessions / Conversations tabs.
 *
 * Renaming and forgetting someone are admin on the server (PATCH and DELETE
 * /api/people/{id}), and a phone has no admin session, so neither is offered:
 * both 401'd from every phone (the phone admin audit, 22 Nov 2026). The
 * profile says where they are ([PEOPLE_EDIT_ON_DASHBOARD]). Someone who
 * shares a name with another person is flagged ([namesakeNote]).
 */
@Composable
internal fun PersonDetail(
    person: Person,
    compact: Boolean,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
    onRenamed: () -> Unit = {},
    /** Voice ID's state, read with the People list; null when unknown. */
    voiceId: VoiceIdStatus? = null,
) {
    val detail = rememberPersonDetail(person.id, person.voice_profile_count)
    var tab by remember(person.id) { mutableIntStateOf(0) }

    DomovoiCard(modifier, padding = 0) {
        if (compact) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                IconButton(onClick = onBack) {
                    Icon(
                        Icons.AutoMirrored.Filled.ArrowBack,
                        contentDescription = "back",
                        tint = Domovoi.colors.fgMuted,
                    )
                }
                AvatarBubble(person.name, 28)
                Text(
                    person.name,
                    style = MaterialTheme.typography.titleMedium,
                    color = Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (isLive(person.last_seen_at)) Pill("live", Tone.Ok, live = true)
            }
            HorizontalDivider(color = Domovoi.colors.borderSoft)
        }

        // F-A008: a list that failed to load counts as "?", not 0.
        val titles = listOf(
            "profile",
            "memory (${countLabel(
                detail.memories.size + detail.favorites.size,
                detail.errors.memories ?: detail.errors.favorites,
            )})",
            "sessions (${countLabel(detail.sessions.size, detail.errors.sessions)})",
            "conversations (${countLabel(detail.conversations.size, detail.errors.conversations)})",
        )
        ScrollableTabRow(
            selectedTabIndex = tab,
            containerColor = Color.Transparent,
            edgePadding = 8.dp,
        ) {
            titles.forEachIndexed { i, title ->
                Tab(
                    selected = tab == i,
                    onClick = { tab = i },
                    text = { Text(title, style = MaterialTheme.typography.labelMedium) },
                    selectedContentColor = Domovoi.colors.brand,
                    unselectedContentColor = Domovoi.colors.fgMuted,
                )
            }
        }
        HorizontalDivider(color = Domovoi.colors.borderSoft)

        Box(Modifier.weight(1f)) {
            when (tab) {
                0 -> ProfileTab(
                    person = person,
                    heardIn = lastHeardIn(
                        detail.conversations,
                        failed = detail.errors.conversations != null,
                        loading = detail.loading,
                    ),
                    voiceRooms = detail.voiceRooms,
                    voiceId = voiceId,
                    onPeopleChanged = onRenamed,
                )
                1 -> MemoryTab(person, detail)
                2 -> SessionsTab(person, detail)
                else -> ConversationsTab(person, detail)
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Profile tab
// ---------------------------------------------------------------------------

@Composable
private fun ProfileTab(
    person: Person,
    heardIn: String,
    voiceRooms: List<VoiceRoom>,
    voiceId: VoiceIdStatus?,
    onPeopleChanged: () -> Unit,
) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            AvatarBubble(person.name, 56)
            Column {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        person.name,
                        style = MaterialTheme.typography.headlineSmall,
                        color = Domovoi.colors.fg,
                    )
                    if (isLive(person.last_seen_at)) Pill("live", Tone.Ok, live = true)
                    if (person.namesakes > 0) Pill("same name", Tone.Warn)
                }
                Text(
                    "person · #${person.id} · added ${relTime(person.created_at)}",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
        }

        namesakeNote(person)?.let { note ->
            Text(
                note,
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fg,
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(MaterialTheme.shapes.small)
                    .background(Domovoi.colors.warnSoft)
                    .padding(12.dp),
            )
        }

        // Web order: namesakes, then this, then where and when they were
        // last heard. No voice profile / session / turn counts: the tabs
        // and the samples already say them (owner, 1.3.0 device check).
        personVoiceRelearnNotice(person, voiceId)?.let { VoiceNoticeBox(it, Domovoi.colors.warnSoft) }

        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Stat("last heard in", heardIn, modifier = Modifier.weight(1f))
            Stat(
                "last heard",
                relTime(person.last_seen_at),
                "presence: ${person.presence_tier ?: "—"}",
                Modifier.weight(1f),
            )
        }

        VoiceRoomsSection(person, voiceRooms, voiceId)

        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            SectionLabel("notes")
            val notes = person.notes?.takeIf { it.isNotBlank() }
            Text(
                notes ?: "no notes yet",
                style = MaterialTheme.typography.bodyMedium,
                color = if (notes != null) Domovoi.colors.fg else Domovoi.colors.fgFaint,
            )
        }

        // Whose this phone is, and their listening history (2026-11-12).
        HorizontalDivider(color = Domovoi.colors.borderSoft)
        ThisPhoneSection(person, onPeopleChanged)
        ListeningHistorySection(person)
        // Their own music mix (1.3.0): theirs to change from their phone.
        com.domovoi.app.ui.screens.settings.PersonMusicMixSection(person.id, person.name)

        HorizontalDivider(color = Domovoi.colors.borderSoft)
        Text(
            PEOPLE_EDIT_ON_DASHBOARD,
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgMuted,
        )
    }
}

// ---------------------------------------------------------------------------
// Rooms that have learned this voice (web PersonVoiceRooms) — read-only.
// ---------------------------------------------------------------------------

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun VoiceRoomsSection(person: Person, rooms: List<VoiceRoom>, voiceId: VoiceIdStatus?) {
    if (!showVoiceRooms(person, rooms, voiceId)) return
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SectionLabel(VOICE_ROOMS_LABEL)
            Text(
                voiceRoomsCount(rooms),
                style = MaterialTheme.typography.labelSmall,
                color = Domovoi.colors.fgFaint,
            )
        }
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            rooms.forEach { room ->
                Pill(voiceRoomChip(room), if (room.known) Tone.Ok else Tone.Idle)
            }
        }
        voiceRoomsHint(person.name, rooms)?.let { hint ->
            Text(hint, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
        }
    }
}

// ---------------------------------------------------------------------------
// Voice ID notices (web VoiceIdNotice / PersonVoiceRelearn): a mic icon and a
// bold line, the details muted under it. Announced as a whole to TalkBack.
// ---------------------------------------------------------------------------

@Composable
internal fun VoiceNoticeBox(notice: VoiceNotice, background: Color, modifier: Modifier = Modifier) {
    Column(
        modifier
            .fillMaxWidth()
            .clip(MaterialTheme.shapes.small)
            .background(background)
            .padding(horizontal = 14.dp, vertical = 10.dp)
            .semantics(mergeDescendants = true) {},
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(
                if (notice.title == VOICE_ID_OFF_TITLE) Icons.Filled.MicOff else Icons.Filled.Mic,
                contentDescription = null,
                modifier = Modifier.size(14.dp),
                tint = Domovoi.colors.fg,
            )
            Text(
                notice.title,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold,
                color = Domovoi.colors.fg,
            )
        }
        notice.body?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
        }
    }
}
