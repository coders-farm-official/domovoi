package com.domovoi.app.ui.screens.chat

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.PushPin
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.text.style.TextAlign
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.ui.components.shareText
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
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
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.snapshotFlow
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
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
    val pins by app.prefs.chatPins.collectAsState()
    val serverUrl by app.prefs.serverUrl.collectAsState()
    val context = LocalContext.current
    ChatHome(
        places = places,
        startable = startable,
        lastUsed = ChatPlaceKind.of(lastUsed),
        pins = pins,
        server = ServerCredentials.normalize(serverUrl),
        onPin = app.prefs::setChatPinned,
        onShare = { title, text -> shareText(context, title, text) },
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
 *  [place]; [visit] tells two openings of the same chat apart; [first]: a
 *  message from the list's "Message Domovoi…" box, sent as it opens. */
private data class OpenChat(val place: ChatPlaceKind, val row: ChatRow?, val visit: Int, val first: String? = null)

/** Chat's text fields (search, "Message Domovoi…"): (value, onChange,
 *  placeholder, modifier). A local so the JVM tests, which have no Android
 *  resources for Material's text field, can put a plain one in. */
internal val LocalChatField = staticCompositionLocalOf<@Composable (String, (String) -> Unit, String, Modifier) -> Unit> {
    { value, onChange, placeholder, modifier ->
        OutlinedTextField(
            value = value,
            onValueChange = onChange,
            placeholder = { Text(placeholder) },
            modifier = modifier,
            singleLine = placeholder == SEARCH_HINT,
            maxLines = 4,
        )
    }
}

/** How long typing pauses before a search runs. */
private const val SEARCH_DEBOUNCE_MS = 250L

/**
 * Chat with no Android behind it (the tests compose it with fake places):
 * the history list of every place's chats, pinned first and then by last
 * use, each with its chip, a search across them, the "archived" view at
 * the end, and the "Message Domovoi…" box pinned at the bottom, which
 * starts a new chat in [defaultPlace] (its picker moves it while nothing is
 * said; [startable]: the places that work right now). With no chats at all,
 * a new chat opens at once (assistant plan part 7). Each chat's long-press
 * menu (and its "more" button) renames, pins ([pins], [onPin]: this
 * phone's own), archives, shares and deletes it; a swipe archives it.
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
    pins: Set<String> = emptySet(),
    server: String = "",
    onPin: (String, Boolean) -> Unit = { _, _ -> },
    onShare: (String, String) -> Unit = { _, _ -> },
) {
    var open by remember { mutableStateOf<OpenChat?>(null) }
    var visits by remember { mutableIntStateOf(0) }
    var deleteTarget by remember { mutableStateOf<ChatRow?>(null) }
    var renameTarget by remember { mutableStateOf<ChatRow?>(null) }
    var showArchived by remember { mutableStateOf(false) }
    var query by remember { mutableStateOf("") }
    var hits by remember { mutableStateOf<List<ChatRow>?>(null) }
    var draft by remember { mutableStateOf("") }
    var autoOpened by remember { mutableStateOf(false) }
    val listState = rememberLazyListState()
    var listTouched by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val chip = { kind: ChatPlaceKind -> placeChip(kind, serverShareSeen) }
    val serverReady = places.firstOrNull { it.kind == ChatPlaceKind.Server }?.ready
    val fallbackPlace = defaultPlace(startable, lastUsed, serverReady)
    var boxPlace by remember { mutableStateOf<ChatPlaceKind?>(null) }
    val newPlace = boxPlace?.takeIf { it in startable } ?: fallbackPlace

    val all = withPins(places.flatMap { it.chats }, pins, server)
    val rows = orderChats(all)
    val archived = archivedChats(all)

    // No chats at all, once every place has said so: a new chat opens at
    // once, as it did before there was a list (once per visit to Chat).
    val allLoaded = places.all { it.loaded }
    LaunchedEffect(allLoaded, all.isEmpty(), fallbackPlace) {
        if (!autoOpened && allLoaded && all.isEmpty() && open == null && fallbackPlace != null) {
            autoOpened = true
            open = OpenChat(fallbackPlace, null, ++visits)
        }
    }
    // A search across every place, after a short pause in the typing.
    LaunchedEffect(query) {
        val q = query.trim()
        if (q.isEmpty()) {
            hits = null
            return@LaunchedEffect
        }
        delay(SEARCH_DEBOUNCE_MS)
        hits = withPins(places.flatMap { p -> p.search(q) }, pins, server).sortedByDescending { it.updatedMs }
    }

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
                        { PlacePicker(startable, current.place, chip) { k -> open = current.copy(place = k, first = null) } }
                    } else {
                        null
                    },
                    onSent = { onUsed(current.place) },
                    firstMessage = current.first,
                ),
            )
        }
        return
    }

    if (showArchived) BackHandler { showArchived = false }
    val searching = query.isNotBlank()
    val openRow = { row: ChatRow -> open = OpenChat(row.place, row, ++visits) }
    val act = { row: ChatRow, action: String ->
        val owner = places.firstOrNull { it.kind == row.place }
        when (action) {
            ACTION_RENAME -> renameTarget = row
            ACTION_PIN, ACTION_UNPIN -> onPin(pinKey(row, server), action == ACTION_PIN)
            ACTION_ARCHIVE -> owner?.setArchived(row, true)
            ACTION_UNARCHIVE -> owner?.setArchived(row, false)
            ACTION_SHARE -> scope.launch { owner?.shareText(row)?.let { onShare(row.title, it) } }
            ACTION_DELETE -> deleteTarget = row
        }
        Unit
    }
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Spacer(Modifier.height(16.dp))
        PageHeader(
            "Chat", subtitle,
            actions = if (startable.isEmpty()) null else {
                {
                    Button(onClick = {
                        if (newPlace != null) open = OpenChat(newPlace, null, ++visits)
                    }) { Text("new chat") }
                }
            },
        )
        Spacer(Modifier.height(12.dp))
        if (all.isNotEmpty() || searching) {
            LocalChatField.current(query, { query = it }, SEARCH_HINT, Modifier.fillMaxWidth())
            Spacer(Modifier.height(8.dp))
        }
        awayLine?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            Spacer(Modifier.height(8.dp))
        }
        if (showArchived && !searching) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = { showArchived = false }) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "back to chats", tint = Domovoi.colors.fg)
                }
                Text("archived", style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg,
                    modifier = Modifier.semantics { heading() })
            }
        }
        val shown = when {
            searching -> hits.orEmpty()
            showArchived -> archived
            else -> rows
        }
        // The places' lists arrive at different moments; a chat landing above
        // the first one shown would leave the list scrolled part-way into it.
        // Until the person scrolls it, the list stays at its top.
        val firstKey = shown.firstOrNull()?.let { "${it.place.key}:${it.id}" }
        LaunchedEffect(firstKey, showArchived, searching) {
            if (!listTouched) listState.scrollToItem(0)
        }
        LaunchedEffect(listState) {
            snapshotFlow { listState.isScrollInProgress }.collect { if (it) listTouched = true }
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            when {
                searching && hits == null -> Unit
                shown.isEmpty() -> EmptyState(
                    when {
                        searching -> "no chats match"
                        showArchived -> "no archived chats"
                        else -> "no chats yet"
                    },
                    if (searching || showArchived) null else emptyHint,
                )
                else -> LazyColumn(state = listState, verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxSize()) {
                    items(shown, key = { "${it.place.key}:${it.id}" }) { row ->
                        SwipeToArchive(row, onArchive = { act(row, if (row.archived) ACTION_UNARCHIVE else ACTION_ARCHIVE) }) {
                            ChatRowCard(
                                row, chip(row.place),
                                onOpen = { openRow(row) },
                                onAction = { a -> act(row, a) },
                            )
                        }
                    }
                    // The archived view sits at the end of the list.
                    if (!searching && !showArchived && archived.isNotEmpty()) {
                        item(key = "archived") {
                            ArchivedEntry(archived.size) { showArchived = true }
                        }
                    }
                }
            }
        }
        // "Message Domovoi…", pinned at the bottom: a new chat is one tap
        // or keystroke away. Its first message opens the chat and goes out.
        if (newPlace != null && !showArchived && !searching) {
            // The short chips here: the long household note would wrap the
            // picker onto a second line under a list that needs the room.
            if (startable.size > 1) PlacePicker(startable, newPlace, { placeChip(it, true) }) { boxPlace = it }
            NewChatBox(draft, { draft = it }) {
                val text = draft.trim()
                if (text.isNotEmpty()) {
                    draft = ""
                    open = OpenChat(newPlace, null, ++visits, first = text)
                }
            }
        }
        Spacer(Modifier.height(10.dp))
    }
    deleteTarget?.let { row ->
        val owner = places.firstOrNull { it.kind == row.place }
        if (owner != null) {
            ConfirmDialog(
                title = "delete chat",
                body = owner.deleteText(row),
                confirmLabel = "delete",
                destructive = true,
                onConfirm = {
                    owner.delete(row)
                    onPin(pinKey(row, server), false)
                },
                onDismiss = { deleteTarget = null },
            )
        }
    }
    renameTarget?.let { row ->
        RenameDialog(row.title, onDismiss = { renameTarget = null }) { name ->
            places.firstOrNull { it.kind == row.place }?.rename(row, name)
            renameTarget = null
        }
    }
}

/** The list's "Message Domovoi…" box: type, then send. */
@Composable
private fun NewChatBox(draft: String, onChange: (String) -> Unit, onSend: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(top = 6.dp),
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        LocalChatField.current(draft, onChange, COMPOSER_HINT, Modifier.weight(1f))
        IconButton(onClick = onSend, enabled = draft.isNotBlank()) {
            Icon(
                Icons.AutoMirrored.Filled.Send, contentDescription = "send",
                tint = if (draft.isNotBlank()) Domovoi.colors.brand else Domovoi.colors.fgFaint,
            )
        }
    }
}

/** The last row of the list: the archived chats, and how many. */
@Composable
private fun ArchivedEntry(count: Int, onOpen: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable(onClickLabel = "open", onClick = onOpen).padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Icon(Icons.Outlined.Archive, contentDescription = null, tint = Domovoi.colors.fgMuted)
        Text(archivedLabel(count), style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted)
    }
}

/** "archived · 3". */
internal fun archivedLabel(count: Int): String = "archived · $count"

/** A chat that swipes away to the archive (or back out of it). The swipe
 *  is a shortcut: the long-press menu does the same. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SwipeToArchive(row: ChatRow, onArchive: () -> Unit, content: @Composable () -> Unit) {
    val state = rememberSwipeToDismissBoxState(
        confirmValueChange = { value ->
            if (value != SwipeToDismissBoxValue.Settled) onArchive()
            false
        },
    )
    SwipeToDismissBox(
        state = state,
        backgroundContent = {
            Row(
                Modifier.fillMaxSize().padding(horizontal = 16.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = if (state.dismissDirection == SwipeToDismissBoxValue.StartToEnd) Arrangement.Start else Arrangement.End,
            ) {
                Icon(Icons.Outlined.Archive, contentDescription = null, tint = Domovoi.colors.fgMuted)
                Spacer(Modifier.size(6.dp))
                Text(if (row.archived) "move back" else ACTION_ARCHIVE, style = MaterialTheme.typography.labelMedium,
                    color = Domovoi.colors.fgMuted)
            }
        },
        content = { content() },
    )
}

/** Renaming a chat: its title to edit; a blank name can't be saved. */
@Composable
private fun RenameDialog(title: String, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var name by remember { mutableStateOf(title.removeSuffix("…")) }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Domovoi.colors.raised,
        title = { Text("rename chat") },
        text = { LocalChatField.current(name, { name = it.take(200) }, "name", Modifier.fillMaxWidth()) },
        confirmButton = {
            TextButton(onClick = { onSave(name.trim()) }, enabled = name.isNotBlank()) {
                Text("save", color = if (name.isNotBlank()) Domovoi.colors.brand else Domovoi.colors.fgFaint)
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("cancel", color = Domovoi.colors.fgMuted) } },
    )
}

/** One chat in the list: its title, where it runs, when, its last words
 *  (or a search's match) where the place keeps them, and its actions
 *  ([rowActions]) on a long press or the "more" button. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun ChatRowCard(row: ChatRow, chip: String, onOpen: () -> Unit, onAction: (String) -> Unit) {
    var menu by remember { mutableStateOf(false) }
    DomovoiCard(
        Modifier.fillMaxWidth()
            .combinedClickable(
                onClickLabel = "open",
                onLongClickLabel = "chat actions",
                onClick = onOpen,
                // The long press's own haptic tick is combinedClickable's.
                onLongClick = { menu = true },
            )
            .semantics {
                customActions = rowActions(row).map { a -> CustomAccessibilityAction(a) { onAction(a); true } }
            },
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    if (row.pinned) {
                        Icon(Icons.Outlined.PushPin, contentDescription = "pinned", tint = Domovoi.colors.fgMuted,
                            modifier = Modifier.size(14.dp))
                    }
                    Text(
                        row.title,
                        style = MaterialTheme.typography.titleSmall,
                        color = Domovoi.colors.fg,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    PlaceChip(row.place, chip)
                    if (row.updatedMs > 0) {
                        Text(
                            relTime(Instant.ofEpochMilli(row.updatedMs).toString()),
                            style = MaterialTheme.typography.labelSmall,
                            color = Domovoi.colors.fgMuted,
                        )
                    }
                    if (row.archived) {
                        Text("archived", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
                    }
                }
                // A reply that failed leaves the server's snippet blank: no
                // empty line under the chip.
                (row.match ?: row.snippet)?.takeIf { it.isNotBlank() }?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgFaint,
                        maxLines = if (row.match != null) 2 else 1, overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            Box {
                IconButton(onClick = { menu = true }) {
                    Icon(Icons.Outlined.MoreVert, contentDescription = "chat actions", tint = Domovoi.colors.fgMuted)
                }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }, containerColor = Domovoi.colors.raised) {
                    rowActions(row).forEach { a ->
                        DropdownMenuItem(
                            text = { Text(a, color = if (a == ACTION_DELETE) Domovoi.colors.err else Color.Unspecified) },
                            onClick = { menu = false; onAction(a) },
                        )
                    }
                }
            }
        }
    }
}

/** The quiet divider above the first message the model still sees
 *  ([WINDOW_NOTE]). */
@Composable
internal fun WindowDivider() {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 6.dp).semantics(mergeDescendants = true) {},
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        HorizontalDivider(Modifier.weight(1f), color = Domovoi.colors.border)
        Text(WINDOW_NOTE, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgSubtle,
            textAlign = TextAlign.Center, modifier = Modifier.widthIn(max = 260.dp))
        HorizontalDivider(Modifier.weight(1f), color = Domovoi.colors.border)
    }
}

/** Where the window divider goes: the index of the message with [fromId]
 *  in [ids] (the transcript's ids, null for one not saved yet); null when
 *  there is none, or it is the first message (nothing earlier fell out). */
internal fun windowDividerIndex(ids: List<Long?>, fromId: Long?): Int? {
    fromId ?: return null
    val at = ids.indexOf(fromId)
    return at.takeIf { it > 0 }
}

/** The header's "share this chat" button (the share sheet, as text). */
@Composable
internal fun ShareChatButton(onShare: () -> Unit) {
    IconButton(onClick = onShare) {
        Icon(Icons.Outlined.Share, contentDescription = "share chat", tint = Domovoi.colors.fgMuted)
    }
}

/** "try again", in a reply's long-press menu. */
internal const val ACTION_TRY_AGAIN = "try again"

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
