package com.domovoi.app.ui.screens.friendhouses

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import com.domovoi.app.LocalApp
import com.domovoi.app.friendhouses.FH_DESCRIPTION
import com.domovoi.app.friendhouses.FH_MESSAGE_MAX
import com.domovoi.app.friendhouses.FhConversation
import com.domovoi.app.friendhouses.FhFriend
import com.domovoi.app.friendhouses.FhMessage
import com.domovoi.app.friendhouses.FhPaths
import com.domovoi.app.friendhouses.FhSharedList
import com.domovoi.app.friendhouses.FhStatus
import com.domovoi.app.friendhouses.fhLength
import com.domovoi.app.friendhouses.fhProblem
import com.domovoi.app.friendhouses.fhSendable
import com.domovoi.app.friendhouses.fhStatusLine
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.screens.lists.ListsSelection
import com.domovoi.app.ui.shell.Route
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Friend houses: "Serverless messaging and friend system between
 * households". The house inbox (one for the whole house), a conversation
 * and its composer, the friends and the shared lists, mirroring the
 * dashboard's Settings → Household → Friend Houses at phone width.
 *
 * Reached only while the server says it is on (the shell's gate,
 * friendhouses/FhGate). What the household token may do is a control;
 * what only an admin may do is a line of words ([FhWords]).
 *
 * Every name, body, title and last_error came from another house: shown
 * only through [FhPlain], a plain-String Text (no links, no markup, line
 * breaks kept), and never logged.
 */
@Composable
fun FriendHousesScreen(navigate: (Route) -> Unit) {
    val app = LocalApp.current
    val server by app.prefs.serverUrl.collectAsState()
    val gate by app.friendHouses.state.collectAsState()
    val status = gate.shownFor(server)

    var tab by rememberSaveable { mutableStateOf(FhTab.Messages) }
    var open by rememberSaveable(server) { mutableStateOf<String?>(null) }
    var inbox by remember(server) { mutableStateOf<FhRead<List<FhConversation>>?>(null) }
    var friends by remember(server) { mutableStateOf<FhRead<List<FhFriend>>?>(null) }
    var shared by remember(server) { mutableStateOf<FhRead<List<FhSharedList>>?>(null) }
    var tick by remember { mutableStateOf(0) }

    // The inbox and the friends, read at once and every 15 s while the
    // screen is on screen (nothing pushes them); the shared lists with them.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(server, tick, lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                inbox = fhRead("load messages") { app.friendHouses.api.inbox().newestFirst() }
                friends = fhRead("load friends") { app.friendHouses.api.friends() }
                shared = fhRead("load shared lists") { app.friendHouses.api.sharedLists() }
                app.friendHouses.refresh()
                delay(INBOX_POLL_MS)
            }
        }
    }
    val reload: () -> Unit = { tick++ }

    val inboxList = (inbox as? FhRead.Ok)?.value.orEmpty()
    val friendList = (friends as? FhRead.Ok)?.value.orEmpty()
    val conv = fhOpenConversation(open, inboxList, friendList)
    BackHandler(enabled = open != null) { open = null }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        if (conv == null) {
            PageHeader(FhWords.TITLE, FH_DESCRIPTION)
            status?.let { StatusLine(it) }
            status?.let { ThisWeek(it) }
            Spacer(Modifier.height(12.dp))
            Tabs(tab, unread = status?.unread ?: 0) { tab = it }
            Spacer(Modifier.height(12.dp))
            Box(Modifier.weight(1f)) {
                when (tab) {
                    FhTab.Messages -> InboxTab(inbox) { open = it }
                    FhTab.Friends -> FriendsTab(friends) { house -> tab = FhTab.Messages; open = house }
                    FhTab.Shared -> SharedTab(shared, server, navigate)
                }
            }
        } else {
            ConversationView(
                conv = conv,
                onBack = { open = null },
                onChanged = reload,
                modifier = Modifier.weight(1f),
            )
        }
    }
}

enum class FhTab(val label: String) { Messages("messages"), Friends("friends"), Shared("shared lists") }

/** How often the inbox and an open conversation are read again. */
private const val INBOX_POLL_MS = 15_000L
private const val CONVERSATION_POLL_MS = 10_000L

/**
 * The ONE way this screen shows text from another house: a Text given a
 * plain String. A plain String has no spans and no link annotations, so
 * nothing in it is clickable, styled or fetched; Compose keeps its line
 * breaks. No autoLink, Linkify, Html.fromHtml, Markdown or previews here
 * (FriendHousesPlainTextTest holds the screen to it).
 */
@Composable
internal fun FhPlain(
    text: String,
    modifier: Modifier = Modifier,
    style: TextStyle = MaterialTheme.typography.bodyMedium,
    color: Color = Domovoi.colors.fg,
    maxLines: Int = Int.MAX_VALUE,
    fontWeight: FontWeight? = null,
) {
    Text(
        text = text,
        modifier = modifier,
        style = style,
        color = color,
        maxLines = maxLines,
        overflow = if (maxLines == Int.MAX_VALUE) TextOverflow.Clip else TextOverflow.Ellipsis,
        fontWeight = fontWeight,
    )
}

/** A muted line of words (the admin-only sentences, notes). */
@Composable
private fun Note(text: String, modifier: Modifier = Modifier) {
    FhPlain(text, modifier, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
}

/** A problem a read or a send ran into, as the server or [fhProblem] said it. */
@Composable
private fun Problem(text: String) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        StatusDot(Tone.Warn)
        FhPlain(text, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg)
    }
}

/** Stopped, error or starting: the server's own sentence. The inbox still
 *  reads and a send queues, so nothing else changes. */
@Composable
private fun StatusLine(status: FhStatus) {
    val line = fhStatusLine(status) ?: return
    Row(
        Modifier.fillMaxWidth().padding(top = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        StatusDot(if (status.status == "starting") Tone.Idle else Tone.Warn, live = status.status == "starting")
        FhPlain(line, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
    }
}

/** The week's trust changes (new friend houses, lists shared with one):
 *  said aloud too, but quiet hours hold that back, so they stay written
 *  here. Nothing when there are none. Names and titles are another
 *  house's words: [FhPlain]. */
@Composable
internal fun ThisWeek(status: FhStatus) {
    val lines = FhWords.changes(status)
    if (lines.isEmpty()) return
    Column(Modifier.fillMaxWidth().padding(top = 10.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(FhWords.THIS_WEEK, style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.fg)
        lines.forEach { FhPlain(it, style = MaterialTheme.typography.bodySmall) }
        Note(FhWords.NOT_YOU)
    }
}

@Composable
private fun Tabs(tab: FhTab, unread: Int, onTab: (FhTab) -> Unit) {
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        FhTab.entries.forEach { t ->
            val on = t == tab
            val shape = RoundedCornerShape(999.dp)
            Row(
                Modifier
                    .heightIn(min = 48.dp)
                    .clip(shape)
                    .border(1.dp, if (on) Domovoi.colors.brand else Domovoi.colors.border, shape)
                    .background(if (on) Domovoi.colors.brandSoft else Color.Transparent, shape)
                    .clickable { onTab(t) }
                    .semantics {
                        role = Role.Tab
                        selected = on
                    }
                    .padding(horizontal = 14.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(t.label, style = MaterialTheme.typography.labelLarge, color = if (on) Domovoi.colors.fg else Domovoi.colors.fgMuted)
                if (t == FhTab.Messages && unread > 0) {
                    Text("$unread", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgSubtle)
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// The inbox.
// ---------------------------------------------------------------------------

@Composable
private fun InboxTab(read: FhRead<List<FhConversation>>?, onOpen: (String) -> Unit) {
    when (read) {
        null -> LoadingState()
        is FhRead.Problem -> Problem(read.text)
        is FhRead.Ok -> if (read.value.isEmpty()) {
            EmptyState(FhWords.NO_MESSAGES, FhWords.NO_MESSAGES_SUB)
        } else {
            LazyColumn(Modifier.fillMaxSize()) {
                items(read.value, key = { it.house }) { c -> ConversationRow(c) { onOpen(c.house) } }
            }
        }
    }
}

@Composable
private fun ConversationRow(c: FhConversation, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .clickable(onClick = onClick)
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Column(Modifier.weight(1f)) {
            FhPlain(
                c.name.orEmpty(),
                maxLines = 2,
                fontWeight = if (c.unread > 0) FontWeight.SemiBold else FontWeight.Normal,
            )
            if (c.friendId == null) Note("no longer a friend")
        }
        if (c.notDelivered > 0) Pill(FhWords.notDelivered(c.notDelivered), Tone.Err)
        if (c.unread > 0) Pill(FhWords.unread(c.unread), Tone.Brand)
        Icon(Icons.Filled.ChevronRight, contentDescription = null, tint = Domovoi.colors.fgSubtle)
    }
}

// ---------------------------------------------------------------------------
// One conversation: newest at the bottom, earlier pages above, the
// composer under it.
// ---------------------------------------------------------------------------

@Composable
private fun ConversationView(
    conv: FhConversation,
    onBack: () -> Unit,
    onChanged: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val app = LocalApp.current
    val scope = rememberCoroutineScope()
    val loader = remember(conv.house) { FhThreadLoader(app.friendHouses.api, conv.house) }
    var thread by remember(conv.house) { mutableStateOf(FhThread()) }
    var problem by remember(conv.house) { mutableStateOf<String?>(null) }
    var text by rememberSaveable(conv.house) { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var sendProblem by remember(conv.house) { mutableStateOf<String?>(null) }
    val list = rememberLazyListState()

    suspend fun markRead() {
        try {
            loader.markRead()
            app.friendHouses.conversationOpened(conv.house)
            app.friendHouses.refresh()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // The next read tries again.
        }
    }

    suspend fun latest(first: Boolean) {
        when (val r = fhRead("load messages") { loader.latest(thread, first) }) {
            is FhRead.Ok -> {
                thread = r.value.first
                problem = null
                if (!first && r.value.second) markRead()
            }
            is FhRead.Problem -> {
                problem = r.text
                thread = thread.copy(loaded = true)
            }
        }
    }

    // Opening it reads it and marks it read; then every 10 s while it is
    // on screen, marking read again when something new came in.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(conv.house) {
        latest(first = true)
        markRead()
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                delay(CONVERSATION_POLL_MS)
                latest(first = false)
            }
        }
    }
    // The newest at the bottom, in view whenever a newer one arrives.
    val newest = thread.messages.lastOrNull()?.id
    LaunchedEffect(newest) {
        if (newest != null) list.scrollToItem(thread.messages.size + (if (thread.more) 1 else 0) - 1)
    }

    val name = conv.name?.takeIf { it.isNotBlank() } ?: "this house"
    val friendId = conv.friendId
    val former = friendId == null

    Column(modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "all conversations")
            }
            FhPlain(name, style = MaterialTheme.typography.titleMedium, maxLines = 2, modifier = Modifier.weight(1f))
        }
        Note(if (former) FhWords.FORMER_FRIEND else FhWords.CONVERSATION_ADMIN, Modifier.padding(bottom = 8.dp))
        problem?.let { Problem(it) }
        LazyColumn(
            Modifier.weight(1f).fillMaxWidth(),
            state = list,
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (thread.more) {
                item(key = "earlier") {
                    TextButton(onClick = {
                        scope.launch {
                            when (val r = fhRead("load earlier") { loader.earlier(thread) }) {
                                is FhRead.Ok -> thread = r.value
                                is FhRead.Problem -> problem = r.text
                            }
                        }
                    }) { Text(FhWords.LOAD_EARLIER) }
                }
            }
            if (thread.loaded && thread.messages.isEmpty() && problem == null) {
                item(key = "none") { Note(FhWords.NO_MESSAGES) }
            }
            items(thread.messages, key = { it.id }) { m -> MessageBubble(m) }
        }
        if (!thread.loaded) LoadingState()
        if (friendId != null) {
            val length = fhLength(text)
            val over = length > FH_MESSAGE_MAX
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = text,
                onValueChange = { text = it; sendProblem = null },
                enabled = !busy,
                placeholder = { Text("Message $name…", color = Domovoi.colors.fgSubtle, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                minLines = 2,
                maxLines = 6,
                modifier = Modifier.fillMaxWidth(),
            )
            sendProblem?.let { Box(Modifier.padding(top = 6.dp)) { Problem(it) } }
            Row(
                Modifier.fillMaxWidth().padding(top = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "$length / $FH_MESSAGE_MAX",
                    style = MaterialTheme.typography.labelMedium,
                    color = if (over) Domovoi.colors.err else Domovoi.colors.fgSubtle,
                    modifier = Modifier.weight(1f),
                )
                Button(
                    onClick = {
                        val body = text
                        if (!fhSendable(body) || busy) return@Button
                        busy = true
                        scope.launch {
                            try {
                                app.friendHouses.api.send(friendId, body)
                                text = ""
                                sendProblem = null
                                latest(first = false)
                                onChanged()
                            } catch (e: CancellationException) {
                                throw e
                            } catch (e: Exception) {
                                // 400 / 404: the server's sentence, as written;
                                // 403: only an admin. Never the text itself.
                                sendProblem = fhProblem("send", e)
                            } finally {
                                busy = false
                            }
                        }
                    },
                    enabled = !busy && fhSendable(text),
                    colors = ButtonDefaults.buttonColors(containerColor = Domovoi.colors.brand, contentColor = Domovoi.colors.brandFg),
                ) { Text(if (busy) "Sending…" else "Send") }
            }
        }
    }
}

/** One message: its text exactly as sent (plain, line breaks kept), when
 *  it arrived, the sender's clock if it disagrees, and an outgoing one's
 *  state in words. */
@Composable
internal fun MessageBubble(m: FhMessage) {
    val out = !m.incoming
    Column(
        Modifier.fillMaxWidth(),
        horizontalAlignment = if (out) Alignment.End else Alignment.Start,
    ) {
        Box(
            Modifier
                .widthIn(max = 520.dp)
                .clip(RoundedCornerShape(10.dp))
                .background(if (out) Domovoi.colors.sunken else Domovoi.colors.card)
                .border(1.dp, Domovoi.colors.border, RoundedCornerShape(10.dp))
                .padding(horizontal = 11.dp, vertical = 8.dp),
        ) {
            FhPlain(m.body.orEmpty())
        }
        val meta = buildList {
            add(fhRelTime(m.createdAt))
            fhTheirClock(m)?.let { add(it) }
        }.joinToString(" · ")
        Row(
            Modifier.padding(top = 3.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(meta, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgSubtle)
            fhOutState(m)?.let { (tone, words) ->
                StatusDot(tone)
                FhPlain(words, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Friends (read only).
// ---------------------------------------------------------------------------

@Composable
private fun FriendsTab(read: FhRead<List<FhFriend>>?, onMessage: (String) -> Unit) {
    when (read) {
        null -> LoadingState()
        is FhRead.Problem -> Problem(read.text)
        is FhRead.Ok -> LazyColumn(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            if (read.value.isEmpty()) item(key = "none") { EmptyState(FhWords.NO_FRIENDS, FhWords.NO_FRIENDS_SUB) }
            items(read.value, key = { it.id }) { f -> FriendRow(f, onMessage) }
            item(key = "admin") {
                Column(Modifier.padding(top = 12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Note(FhWords.PHONE_FOLLOWS)
                    Note(FhWords.FRIENDS_ADMIN)
                }
            }
        }
    }
}

@Composable
internal fun FriendRow(f: FhFriend, onMessage: (String) -> Unit) {
    Column(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FhPlain(f.name, fontWeight = FontWeight.SemiBold, maxLines = 2, modifier = Modifier.weight(1f))
            if (f.verified) Pill(FhWords.VERIFIED, Tone.Ok) else Pill(FhWords.NOT_VERIFIED, Tone.Warn)
        }
        FhWords.presence(f)?.let { Note(it) }
        Note(FhWords.alert(f.alert))
        if (FhPaths.isHouseKey(f.house)) {
            TextButton(onClick = { onMessage(f.house) }) { Text("message") }
        }
    }
}

// ---------------------------------------------------------------------------
// Shared lists (read only here; the list itself is an ordinary list).
// ---------------------------------------------------------------------------

@Composable
private fun SharedTab(read: FhRead<List<FhSharedList>>?, server: String, navigate: (Route) -> Unit) {
    val context = LocalContext.current
    when (read) {
        null -> LoadingState()
        is FhRead.Problem -> Problem(read.text)
        is FhRead.Ok -> LazyColumn(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            val lists = fhSharedOrder(read.value)
            if (lists.isEmpty()) item(key = "none") { EmptyState(FhWords.NO_SHARED) }
            items(lists, key = { it.id }) { s ->
                SharedRow(s) { listId ->
                    ListsSelection.save(context, server, listId)
                    navigate(Route.Lists)
                }
            }
            item(key = "admin") { Note(FhWords.LISTS_ADMIN, Modifier.padding(top = 12.dp)) }
        }
    }
}

@Composable
internal fun SharedRow(s: FhSharedList, onOpen: (Long) -> Unit) {
    val friend = FhWords.friendName(s.friend)
    Column(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        if (s.state == "invited") {
            FhPlain(FhWords.invited(friend, s.title.orEmpty()))
            return@Column
        }
        FhPlain(s.listName ?: s.title.orEmpty(), fontWeight = FontWeight.SemiBold, maxLines = 2)
        Note(if (s.origin == "us") "Our list, shared with $friend" else "$friend's list, shared with this house")
        if (s.waitingToSend > 0) Note(FhWords.waiting(s.waitingToSend))
        val listId = s.listId
        if (listId != null && listId > 0) {
            TextButton(onClick = { onOpen(listId) }) { Text("open in lists") }
        }
    }
}
