package com.domovoi.app.ui.screens.people

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.ScrollableTabRow
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.ui.components.AvatarBubble
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.Stat
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.isLive
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.launch

/**
 * The right pane (expanded) / full-screen overlay (compact) for a selected
 * person: Profile / Memory / Sessions / Conversations tabs.
 *
 * Note: person rename is intentionally absent — PATCH /api/people/{id}
 * doesn't exist yet (the web page stubs it with a toast).
 */
@Composable
internal fun PersonDetail(
    person: Person,
    compact: Boolean,
    onBack: () -> Unit,
    onForgotten: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val detail = rememberPersonDetail(person.id)
    var tab by remember(person.id) { mutableIntStateOf(0) }
    var confirmForget by remember(person.id) { mutableStateOf(false) }

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

        val titles = listOf(
            "profile",
            "memory (${detail.memories.size + detail.favorites.size})",
            "sessions (${detail.sessions.size})",
            "conversations (${detail.conversations.size})",
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
                    sessionCount = detail.sessions.size,
                    turnCount = detail.conversations.size,
                    onForget = { confirmForget = true },
                )
                1 -> MemoryTab(person, detail)
                2 -> SessionsTab(person, detail)
                else -> ConversationsTab(person, detail)
            }
        }
    }

    if (confirmForget) {
        ConfirmForgetDialog(
            person = person,
            sessionCount = detail.sessions.size,
            turnCount = detail.conversations.size,
            onConfirm = {
                confirmForget = false
                scope.launch {
                    runCatching { app.api.delete("/api/people/${person.id}") }
                        .onSuccess {
                            toast("forgot ${person.name} · cascade complete")
                            onForgotten()
                        }
                        .onFailure { toast("forget failed: ${it.message}") }
                }
            },
            onDismiss = { confirmForget = false },
        )
    }
}

// ---------------------------------------------------------------------------
// Profile tab
// ---------------------------------------------------------------------------

@Composable
private fun ProfileTab(
    person: Person,
    sessionCount: Int,
    turnCount: Int,
    onForget: () -> Unit,
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
                }
                Text(
                    "person · #${person.id} · added ${relTime(person.created_at)}",
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Stat(
                "last heard",
                relTime(person.last_seen_at),
                "presence: ${person.presence_tier ?: "—"}",
                Modifier.weight(1f),
            )
            Stat(
                "voice profiles",
                "${person.voice_profile_count}",
                "enrolled",
                Modifier.weight(1f),
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Stat("sessions", "$sessionCount", "recent", Modifier.weight(1f))
            Stat("turns", "$turnCount", "conversation_log rows", Modifier.weight(1f))
        }

        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            SectionLabel("notes")
            val notes = person.notes?.takeIf { it.isNotBlank() }
            Text(
                notes ?: "no notes yet",
                style = MaterialTheme.typography.bodyMedium,
                color = if (notes != null) Domovoi.colors.fg else Domovoi.colors.fgFaint,
            )
        }

        HorizontalDivider(color = Domovoi.colors.borderSoft)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            Button(
                onClick = onForget,
                colors = ButtonDefaults.buttonColors(
                    containerColor = Domovoi.colors.errSoft,
                    contentColor = Domovoi.colors.err,
                ),
            ) { Text("forget person") }
        }
    }
}

// ---------------------------------------------------------------------------
// Type-the-name-to-confirm cascade-delete dialog (web ConfirmForget).
// ---------------------------------------------------------------------------

@Composable
private fun ConfirmForgetDialog(
    person: Person,
    sessionCount: Int,
    turnCount: Int,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    var text by remember(person.id) { mutableStateOf("") }
    val ok = text.trim() == person.name

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Domovoi.colors.raised,
        title = {
            Text("Forget ${person.name}?", style = MaterialTheme.typography.titleMedium)
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    "This is a cascade delete. Domovoi will lose:",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fg,
                )
                Text(
                    "· ${person.voice_profile_count} " +
                        plural(person.voice_profile_count, "voice profile") + "\n" +
                        "· $sessionCount " + plural(sessionCount, "session") + "\n" +
                        "· $turnCount " + plural(turnCount, "conversation turn"),
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
                Text(
                    "Type ${person.name} to confirm.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it },
                    modifier = Modifier.fillMaxWidth(),
                    placeholder = { Text(person.name, color = Domovoi.colors.fgSubtle) },
                    singleLine = true,
                )
            }
        },
        confirmButton = {
            Button(
                enabled = ok,
                onClick = onConfirm,
                colors = ButtonDefaults.buttonColors(
                    containerColor = Domovoi.colors.err,
                    contentColor = Color.White,
                    disabledContainerColor = Domovoi.colors.errSoft,
                    disabledContentColor = Domovoi.colors.err,
                ),
            ) { Text("forget ${person.name}") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("cancel", color = Domovoi.colors.fgMuted) }
        },
    )
}
