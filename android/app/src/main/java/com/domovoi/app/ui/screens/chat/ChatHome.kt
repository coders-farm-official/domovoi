package com.domovoi.app.ui.screens.chat

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.assistant.AssistantSupport
import com.domovoi.app.assistant.DeviceTier
import com.domovoi.app.net.LocalCapabilities
import com.domovoi.app.net.LocalSharedScreen
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.screens.assistant.rememberPhoneChatPlace
import com.domovoi.app.ui.screens.assistant.unsupportedText
import com.domovoi.app.ui.shell.keyboardCrowdsTheWindow
import com.domovoi.app.ui.theme.Domovoi
import java.time.Instant

/**
 * Chat (Route.Chat): one history list and one conversation view for the
 * chats on this phone and on the home server (assistant plan part 6,
 * ChatPlace.kt). [away]: the local shell, where the home server isn't in
 * reach (or the person chose this phone's media); its chats are not listed
 * then, and [awayLine] says when they come back.
 */
@Composable
fun ChatScreen(away: Boolean = false, awayLine: String? = null) {
    val app = LocalApp.current
    val caps = LocalCapabilities.current
    val shared = LocalSharedScreen.current
    val phoneEngine = AssistantSupport.available
    // The controller is built only where the phone place can exist: never
    // on a 32-bit install, nor on a shared screen.
    val assistant = if (phoneEngine && !shared) app.assistant else null
    val phoneTier = if (assistant != null) assistant.state.collectAsState().value.tier else null
    // The phone's one-line news (a delete that failed, say), wherever Chat
    // is showing: the list as much as a phone chat.
    if (assistant != null) {
        val toast = LocalToast.current
        LaunchedEffect(assistant) { assistant.messages.collect { toast(it) } }
    }
    val facts = PlaceFacts(
        away = away,
        serverChatOn = caps.aiEnabled,
        phoneEngine = phoneEngine,
        phoneCapable = phoneTier != DeviceTier.Unsupported,
        shared = shared,
    )
    val listed = listedPlaces(facts)
    val server = if (ChatPlaceKind.Server in listed) rememberServerChatPlace() else null
    val phone = if (ChatPlaceKind.Phone in listed && assistant != null) rememberPhoneChatPlace(assistant) else null
    val places = listOfNotNull(server, phone)
    val startable = startablePlaces(facts)
    val lastUsed by app.prefs.chatPlace.collectAsState()
    // The household note stays on the chip for this whole visit, so it is
    // still there while the first home-server message is being written.
    val serverShareSeen = remember { app.prefs.chatServerShareSeen.value }
    val unsupported = assistant?.state?.value?.memory
        ?.takeIf { phoneEngine && !facts.phoneCapable }
        ?.let { unsupportedText(it.marketedGb, it.lowRamDevice) }
    ChatHome(
        places = places,
        startable = startable,
        lastUsed = ChatPlaceKind.of(lastUsed),
        serverShareSeen = serverShareSeen,
        awayLine = awayLine?.takeIf { away },
        subtitle = chatSubtitle(startable),
        emptyHint = emptyHint(startable, unsupported),
        onUsed = { kind ->
            app.prefs.setChatPlace(kind.key)
            if (kind == ChatPlaceKind.Server) app.prefs.setChatServerShareSeen()
        },
    )
}

/** The list's subtitle, by the places a chat can start in now ([startable]):
 *  never a place that can't. */
internal fun chatSubtitle(startable: List<ChatPlaceKind>): String = when {
    ChatPlaceKind.Server in startable && ChatPlaceKind.Phone in startable ->
        "text chat with Domovoi — on this phone, or on your home server"
    ChatPlaceKind.Phone in startable -> "text chat with Domovoi on this phone — no server, no internet"
    ChatPlaceKind.Server in startable -> "text chat with Domovoi — runs on your own hardware"
    else -> "text chat with Domovoi"
}

/** The empty list's hint: how to start, or why nothing can start here. */
internal fun emptyHint(startable: List<ChatPlaceKind>, unsupported: String?): String? = when {
    ChatPlaceKind.Server in startable -> "start one — attach an image and the vision model reads it"
    ChatPlaceKind.Phone in startable -> "start one — it runs on this phone, with no internet"
    else -> unsupported
}

/** The chat open in Chat's conversation view: [row] null is a new chat in
 *  [place]; [visit] tells two openings of the same chat apart. */
private data class OpenChat(val place: ChatPlaceKind, val row: ChatRow?, val visit: Int)

/**
 * Chat with no Android behind it (the tests compose it with fake places):
 * the history list of every place's chats, newest first, each with its
 * chip; "new chat" opens a conversation in [defaultPlace]; a conversation
 * with nothing said yet carries the place picker ([startable]: the places
 * that work right now).
 */
@Composable
internal fun ChatHome(
    places: List<ChatPlace>,
    startable: List<ChatPlaceKind>,
    lastUsed: ChatPlaceKind?,
    serverShareSeen: Boolean,
    awayLine: String?,
    subtitle: String,
    emptyHint: String?,
    onUsed: (ChatPlaceKind) -> Unit,
) {
    var open by remember { mutableStateOf<OpenChat?>(null) }
    var visits by remember { mutableIntStateOf(0) }
    var deleteTarget by remember { mutableStateOf<ChatRow?>(null) }
    val chip = { kind: ChatPlaceKind -> placeChip(kind, serverShareSeen) }

    val current = open
    val place = current?.let { o -> places.firstOrNull { it.kind == o.place } }
    // The place went (the server's AI switched off, say): back to the list.
    LaunchedEffect(current, place) { if (current != null && place == null) open = null }
    if (current != null && place != null) {
        val close = {
            open = null
            places.forEach { it.refresh() }
        }
        // F-A006: the system back key closes the conversation like the
        // in-app chevron does.
        BackHandler(onBack = close)
        key(current.visit, current.place) {
            place.Conversation(
                current.row,
                ChatFrame(
                    chip = chip(current.place),
                    onBack = close,
                    picker = if (current.row == null && startable.isNotEmpty()) {
                        { PlacePicker(startable, current.place, chip) { k -> open = current.copy(place = k) } }
                    } else {
                        null
                    },
                    onSent = { onUsed(current.place) },
                ),
            )
        }
        return
    }

    val rows = mergeChats(places.map { it.chats })
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        PageHeader(
            "Chat", subtitle,
            actions = if (startable.isEmpty()) null else {
                {
                    Button(onClick = {
                        val kind = defaultPlace(startable, lastUsed, places.firstOrNull { it.kind == ChatPlaceKind.Server }?.ready)
                        if (kind != null) open = OpenChat(kind, null, ++visits)
                    }) { Text("new chat") }
                }
            },
        )
        Spacer(Modifier.height(12.dp))
        awayLine?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            Spacer(Modifier.height(8.dp))
        }
        if (rows.isEmpty()) {
            EmptyState("no chats yet", emptyHint)
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(rows, key = { "${it.place.key}:${it.id}" }) { row ->
                    ChatRowCard(
                        row, chip(row.place),
                        onOpen = { open = OpenChat(row.place, row, ++visits) },
                        onDelete = { deleteTarget = row },
                    )
                }
            }
        }
    }
    deleteTarget?.let { row ->
        val owner = places.firstOrNull { it.kind == row.place }
        if (owner != null) {
            ConfirmDialog(
                title = "delete chat",
                body = owner.deleteText(row),
                confirmLabel = "delete",
                destructive = true,
                onConfirm = { owner.delete(row) },
                onDismiss = { deleteTarget = null },
            )
        }
    }
}

/** One chat in the list: its title, where it runs, when, and its last words
 *  where the place keeps them. */
@Composable
internal fun ChatRowCard(row: ChatRow, chip: String, onOpen: () -> Unit, onDelete: () -> Unit) {
    DomovoiCard(Modifier.fillMaxWidth().clickable(onClickLabel = "open", onClick = onOpen)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text(
                    row.title,
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.fg,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    PlaceChip(row.place, chip)
                    if (row.updatedMs > 0) {
                        Text(
                            relTime(Instant.ofEpochMilli(row.updatedMs).toString()),
                            style = MaterialTheme.typography.labelSmall,
                            color = Domovoi.colors.fgMuted,
                        )
                    }
                }
                // A reply that failed leaves the server's snippet blank: no
                // empty line under the chip.
                row.snippet?.takeIf { it.isNotBlank() }?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgFaint,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            IconButton(onClick = onDelete) {
                Icon(Icons.Outlined.DeleteOutline, contentDescription = "delete", tint = Domovoi.colors.fgMuted)
            }
        }
    }
}

/** Where a chat runs, as a chip: the phone's in the brand tone, the home
 *  server's neutral. */
@Composable
internal fun PlaceChip(kind: ChatPlaceKind, text: String, modifier: Modifier = Modifier) {
    Pill(text, if (kind == ChatPlaceKind.Phone) Tone.Brand else Tone.Idle, modifier = modifier)
}

/**
 * The conversation's header in every place: back to the list, the chat's
 * title, its place chip, and the place's own [actions]. Nothing at all in a
 * window the keyboard has left too short (ChatScreen.kt ThreadTitleRow says
 * why); system back still leaves the chat.
 */
@Composable
internal fun ChatHeader(
    title: String,
    chip: String,
    place: ChatPlaceKind,
    onBack: (() -> Unit)?,
    modifier: Modifier = Modifier,
    actions: (@Composable () -> Unit)? = null,
) {
    if (keyboardCrowdsTheWindow()) return
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        onBack?.let { back ->
            IconButton(onClick = back) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "back", tint = Domovoi.colors.fg)
            }
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(
                title,
                style = MaterialTheme.typography.titleMedium,
                color = Domovoi.colors.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.semantics { heading() },
            )
            PlaceChip(place, chip)
        }
        actions?.invoke()
    }
    Spacer(Modifier.height(8.dp))
}

/**
 * The place picker on the "Message Domovoi…" box of a chat with nothing
 * said yet: the places that work right now ([choices]), the chosen one
 * marked. With one place it is that place's chip alone, so it still says
 * where the chat runs.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun PlacePicker(
    choices: List<ChatPlaceKind>,
    selected: ChatPlaceKind,
    chip: (ChatPlaceKind) -> String,
    onPick: (ChatPlaceKind) -> Unit,
) {
    FlowRow(
        Modifier.fillMaxWidth().padding(top = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(
            "runs",
            style = MaterialTheme.typography.labelMedium,
            color = Domovoi.colors.fgMuted,
            modifier = Modifier.align(Alignment.CenterVertically),
        )
        if (choices.size <= 1) {
            PlaceChip(selected, chip(selected), Modifier.align(Alignment.CenterVertically))
        } else {
            choices.forEach { k ->
                FilterChip(
                    selected = k == selected,
                    onClick = { if (k != selected) onPick(k) },
                    label = { Text(chip(k)) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = Domovoi.colors.brandSoft,
                        selectedLabelColor = Domovoi.colors.fg,
                        labelColor = Domovoi.colors.fgMuted,
                    ),
                )
            }
        }
    }
}
