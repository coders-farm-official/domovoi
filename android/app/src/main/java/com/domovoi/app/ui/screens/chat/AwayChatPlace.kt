package com.domovoi.app.ui.screens.chat

import android.graphics.BitmapFactory
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.outlined.AttachFile
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.outlined.Stop
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.away.AwayDropped
import com.domovoi.app.away.AwayLink
import com.domovoi.app.away.AwayPicture
import com.domovoi.app.away.AwayRefused
import com.domovoi.app.away.AwaySession
import com.domovoi.app.away.AwayText
import com.domovoi.app.away.SendUpdate
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull

/*
 * Chat from away (1.5.0, away/): the home server's chats over the house's
 * away onion, while the phone is off the home network. The same list and
 * the same conversation view as at home, with a small "away" marker in the
 * header and one plain line when something is in the way. The requests
 * are the wire's five (AWAY-CHAT-WIRE §4): the list, a chat's history, a
 * send with its streamed reply, picking a reply up again, and stopping one.
 * Away there is no rename, archive, delete, search, voice or documents, and
 * pictures in a chat's history show as a placeholder unless this phone
 * still holds them (they aren't downloadable away in v1).
 *
 * Tor runs only while this is on screen (AwaySession: acquired on start,
 * let go on stop or when Chat closes, stopped shortly after).
 */

/** The header's marker. */
const val AWAY_MARKER = "away"

/** What a picture the phone doesn't hold says in its place. */
const val AWAY_PICTURE_PLACEHOLDER = "picture"

/** The list's line while away chat works: nothing to say. Otherwise what is in the way. */
internal fun awayListLine(link: AwayLink, note: String?): String? = when (link) {
    is AwayLink.Failed -> link.line
    AwayLink.Connecting -> AwayText.CONNECTING
    else -> note
}

/** A thread row from `chat.list` as a row of Chat's list: away there is no
 *  rename, archive or delete (the request set has none). */
internal fun awayRow(t: ThreadRow): ChatRow = t.toChatRow().copy(canWrite = false, canDelete = false)

/** How many pictures a message from `chat.history` had (`images: [{"kind": "picture"}]`). */
internal fun awayPictures(message: JsonObject): Int = (message["images"] as? JsonArray)?.size ?: 0

/** A message row from the house, as the home chat reads one (pictures apart). */
internal fun awayMessageRow(o: JsonObject): MessageRow =
    DomovoiJson.decodeFromJsonElement(MessageRow.serializer(), JsonObject(o - "images"))

/** Holds the away session while this is composed and the app is on screen. */
@Composable
internal fun HoldAwaySession(session: AwaySession) {
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle, session) {
        var held = false
        val obs = LifecycleEventObserver { _, e ->
            when (e) {
                Lifecycle.Event.ON_START -> if (!held) { session.acquire(); held = true }
                Lifecycle.Event.ON_STOP -> if (held) { session.release(); held = false }
                else -> Unit
            }
        }
        lifecycle.addObserver(obs)
        onDispose {
            lifecycle.removeObserver(obs)
            if (held) session.release()
        }
    }
}

@Composable
internal fun rememberAwayChatPlace(): ChatPlace {
    val app = LocalApp.current
    val session = app.away
    HoldAwaySession(session)
    var reads by remember { mutableIntStateOf(0) }
    var threads by remember { mutableStateOf<List<ThreadRow>>(emptyList()) }
    var loaded by remember { mutableStateOf(false) }
    val awayTick by app.prefs.awayChanged.collectAsState()
    val server by app.prefs.serverUrl.collectAsState()
    val enrolled = remember(server, awayTick) { app.prefs.holdsAway(server) }
    LaunchedEffect(reads, enrolled) {
        // Not set up (any more): nothing to ask; the list says why.
        if (!enrolled) {
            threads = emptyList()
            loaded = true
            return@LaunchedEffect
        }
        try {
            val open = session.client.listAll(archived = false)
            val archived = session.client.listAll(archived = true)
            threads = (open + archived).mapNotNull { runCatching { DomovoiJson.decodeFromJsonElement(ThreadRow.serializer(), it) }.getOrNull() }
            loaded = true
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // Not read: the line under the list says why (the session's
            // link), and Chat doesn't open a new chat as if there were none.
        }
    }
    return AwayChatPlace(app, session, threads, loaded) { reads++ }
}

private class AwayChatPlace(
    private val app: AppContainer,
    private val session: AwaySession,
    private val threads: List<ThreadRow>,
    override val loaded: Boolean,
    private val reread: () -> Unit,
) : ChatPlace {
    override val kind = ChatPlaceKind.Server
    override val chats: List<ChatRow> = threads.map(::awayRow)
    override val ready: Boolean? = true
    override fun refresh() = reread()
    override fun deleteText(row: ChatRow): String = ""
    override fun delete(row: ChatRow) {}

    override suspend fun shareText(row: ChatRow): String? = runCatching {
        val id = row.id.toLong()
        val h = session.client.history(id)
        val messages = (h["messages"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonObject)?.let(::awayMessageRow) }
        serverChatText(row.title, messages)
    }.getOrNull()

    @Composable
    override fun Conversation(row: ChatRow?, frame: ChatFrame) {
        val thread = row?.let { r -> threads.firstOrNull { it.id.toString() == r.id } ?: r.id.toLongOrNull()?.let { ThreadRow(id = it, title = r.title) } }
        AwayConversation(session, thread, frame, onCreated = reread)
    }
}

/** A line of an away conversation: the home chat's message, and its pictures
 *  ([held]: the one this phone sent, still in memory). */
private class AwayEntry(val m: LiveMessage, val pictures: Int = 0, val held: ByteArray? = null)

@Composable
private fun AwayConversation(session: AwaySession, thread: ThreadRow?, frame: ChatFrame, onCreated: () -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    val link by session.link.collectAsState()
    var chatId by remember { mutableStateOf(thread?.id) }
    var title by remember { mutableStateOf(thread?.title ?: NEW_CHAT) }
    var writable by remember { mutableStateOf(thread?.can_write != false) }
    val entries = remember { mutableStateListOf<AwayEntry>() }
    var more by remember { mutableStateOf(false) }
    var draft by remember { mutableStateOf("") }
    var picture by remember { mutableStateOf<ByteArray?>(null) }
    var preparing by remember { mutableStateOf(false) }
    var sending by remember { mutableStateOf(false) }
    var line by remember { mutableStateOf<String?>(null) }

    fun entryOf(o: JsonObject): AwayEntry {
        val r = awayMessageRow(o)
        val m = LiveMessage(
            r.role, r.content, emptyList(), r.model, r.error,
            id = r.id.takeIf { it > 0 }, createdAt = r.created_at, stats = r.stats, errorCode = r.stats?.error_code,
            device = if (r.device_id != null && r.device_id == app.prefs.deviceId) "this phone" else r.device_name ?: r.device_id,
            source = r.source, speakerPersonId = r.speaker_person_id, speakerName = r.speaker_name,
            speakerConfident = r.speaker_confident, documents = r.documents.orEmpty(), doc = r.doc,
        )
        return AwayEntry(m, awayPictures(o), r.id.takeIf { it > 0 }?.let { session.heldPicture(it) })
    }

    suspend fun loadHistory(id: Long, before: Long? = null): Boolean = try {
        val h = session.client.history(id, before)
        (h["thread"] as? JsonObject)?.let { t ->
            runCatching { DomovoiJson.decodeFromJsonElement(ThreadRow.serializer(), t) }.getOrNull()?.let {
                title = it.title ?: title
                writable = it.can_write != false
            }
        }
        val page = (h["messages"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonObject)?.let(::entryOf) }
        more = (h["more"] as? JsonPrimitive)?.booleanOrNull == true
        if (before == null) {
            entries.clear()
            entries.addAll(page)
        } else {
            entries.addAll(0, page)
        }
        line = null
        true
    } catch (e: AwayRefused) {
        if (e.code == "not_found") {
            toast(CHAT_GONE_LINE)
            frame.onBack()
        } else {
            line = e.line
        }
        false
    } catch (e: AwayDropped) {
        line = AwayText.forBye(e.byeCode)
        false
    }

    /** Follow a reply into [reply]'s bubble: a send's, or one picked up again. */
    suspend fun follow(updates: kotlinx.coroutines.flow.Flow<SendUpdate>, reply: () -> LiveMessage?, onStored: (SendUpdate.Stored) -> Unit = {}): SendUpdate? {
        var last: SendUpdate? = null
        updates.collect { u ->
            last = u
            when (u) {
                is SendUpdate.Stored -> onStored(u)
                is SendUpdate.Delta -> reply()?.appendStreamed(u.text)
                is SendUpdate.Done -> reply()?.let { r ->
                    r.pending = false
                    r.doneAtMs = System.currentTimeMillis()
                    u.message?.takeIf { !u.tooLarge }?.let { r.finishWith(awayMessageRow(it)) }
                }
                is SendUpdate.Failed -> reply()?.let { r ->
                    r.pending = false
                    r.error = u.detail.ifBlank { AwayText.WRONG }
                    r.errorCode = u.code
                    u.message?.let { r.finishWith(awayMessageRow(it)) }
                }
                else -> Unit
            }
        }
        return last
    }

    /** Pick up this phone's reply still being written in this chat. */
    suspend fun resume(id: Long) {
        var bubble: LiveMessage? = entries.lastOrNull()?.m?.takeIf { it.role != "user" && it.pending }
        val last = follow(session.client.stream(id), { bubble }) {
            if (bubble == null) {
                bubble = LiveMessage("assistant", "", pending = true).also { entries.add(AwayEntry(it)) }
            }
            // The stream's first delta is everything so far.
            bubble?.content = ""
        }
        when (last) {
            is SendUpdate.Done -> if (last.tooLarge) loadHistory(id)
            is SendUpdate.Failed -> if (last.detail.isBlank()) loadHistory(id)
            is SendUpdate.Dropped -> line = AwayText.forBye(last.byeCode)
            else -> Unit
        }
        bubble?.pending = false
    }

    // A chat opened from the list: its newest page, then any reply still running.
    LaunchedEffect(thread?.id) {
        val id = thread?.id ?: return@LaunchedEffect
        if (loadHistory(id)) resume(id)
    }
    LaunchedEffect(entries.size) { if (entries.isNotEmpty()) listState.animateScrollToItem(entries.lastIndex) }

    fun send(text: String, pic: ByteArray?) {
        if (sending || (text.isBlank() && pic == null)) return
        sending = true
        line = null
        val user = LiveMessage("user", text, device = "this phone")
        val reply = LiveMessage("assistant", "", pending = true).also { it.sentAtMs = System.currentTimeMillis() }
        entries.add(AwayEntry(user, if (pic != null) 1 else 0, pic))
        entries.add(AwayEntry(reply))
        frame.onSent()
        scope.launch {
            val clientId = com.domovoi.app.away.AwayWire.newClientId()
            var stored = false
            var last = follow(session.client.send(chatId, text, pic, clientId), { reply }) { s ->
                stored = true
                if (chatId == null && s.chatId != null) {
                    chatId = s.chatId
                    onCreated()
                }
                s.messageId?.let { id ->
                    user.id = id
                    pic?.let { session.holdPicture(id, it) }
                }
            }
            // Dropped mid-reply: the house goes on; reconnect once and pick it up.
            // (Only once the house had stored the message: one that never got
            // there is given back to the composer below.)
            if (last is SendUpdate.Dropped && stored && chatId != null && last.byeCode == null) {
                runCatching { resume(chatId!!) }
                last = null
            }
            when (val l = last) {
                is SendUpdate.Refused -> {
                    entries.remove(entries.lastOrNull { it.m === reply })
                    entries.remove(entries.lastOrNull { it.m === user })
                    if (draft.isEmpty()) draft = text
                    if (picture == null) picture = pic
                    line = if (l.code == "not_found") CHAT_GONE_LINE else if (l.code == "forbidden") l.detail.ifBlank { AwayText.WRONG } else AwayText.forErr(l.code)
                }
                is SendUpdate.Dropped -> {
                    reply.pending = false
                    if (reply.content.isEmpty()) entries.remove(entries.lastOrNull { it.m === reply })
                    if (!stored) {
                        // It never reached the house: the words go back to the composer.
                        entries.remove(entries.lastOrNull { it.m === user })
                        if (draft.isEmpty()) draft = text
                        if (picture == null) picture = pic
                    }
                    line = AwayText.forBye(l.byeCode)
                }
                is SendUpdate.Done -> if (l.tooLarge) chatId?.let { loadHistory(it) }
                // An error with no detail is one whose row didn't fit (too_large): history has it.
                is SendUpdate.Failed -> if (l.detail.isBlank()) chatId?.let { loadHistory(it) }
                else -> Unit
            }
            reply.pending = false
            sending = false
        }
    }

    // A new chat from the list's "Message Domovoi…" box: sent as it opens.
    LaunchedEffect(Unit) { frame.firstMessage?.let { send(it, null) } }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        uri ?: return@rememberLauncherForActivityResult
        preparing = true
        scope.launch {
            val made = withContext(Dispatchers.IO) {
                runCatching {
                    val size = context.contentResolver.openAssetFileDescriptor(uri, "r")?.use { it.length }?.takeIf { it >= 0 }
                    if (pickedTooBig(size)) null
                    else context.contentResolver.openInputStream(uri)?.use { it.readBytes() }?.let(AwayPicture::prepare)
                }.getOrNull()
            }
            preparing = false
            if (made == null) toast(AwayText.PICTURE) else picture = made
        }
    }

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        ChatHeader(title, frame.chip, ChatPlaceKind.Server, frame.onBack, actions = { Pill(AWAY_MARKER, Tone.Brand) })
        val shown = when (link) {
            AwayLink.Connecting -> AwayText.CONNECTING
            is AwayLink.Failed -> (link as AwayLink.Failed).line
            else -> null
        } ?: line
        shown?.let {
            Text(
                it,
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(bottom = 6.dp).semantics { liveRegion = LiveRegionMode.Polite },
            )
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            LazyColumn(state = listState, modifier = Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (more) item {
                    TextButton(onClick = {
                        val id = chatId ?: return@TextButton
                        val first = entries.firstOrNull()?.m?.id ?: return@TextButton
                        scope.launch { loadHistory(id, first) }
                    }) { Text("earlier messages") }
                }
                items(entries) { e ->
                    Column(Modifier.fillMaxWidth(), horizontalAlignment = if (e.m.role == "user") Alignment.End else Alignment.Start) {
                        if (e.pictures > 0) AwayPictures(e.pictures, e.held)
                        if (e.m.content.isNotEmpty() || e.m.pending || e.m.error != null || e.pictures == 0) {
                            MessageBubble(e.m, chatId ?: 0L)
                        }
                    }
                }
            }
        }
        if (thread == null && entries.isEmpty() && !sending) frame.picker?.invoke()
        picture?.let { p ->
            Row(Modifier.fillMaxWidth().padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                AwayPictures(1, p)
                Text(
                    "picture · ${p.size / 1024} KB",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                    modifier = Modifier.padding(start = 8.dp).weight(1f),
                )
                IconButton(onClick = { picture = null }) {
                    Icon(Icons.Outlined.Close, contentDescription = "remove the picture", tint = Domovoi.colors.fgMuted)
                }
            }
        }
        if (writable) Row(
            Modifier.fillMaxWidth().padding(top = 6.dp),
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            IconButton(onClick = { picker.launch("image/*") }, enabled = !sending && !preparing && picture == null) {
                Icon(Icons.Outlined.AttachFile, contentDescription = "attach a picture", tint = Domovoi.colors.fgMuted)
            }
            OutlinedTextField(
                value = draft, onValueChange = { draft = it },
                placeholder = { Text(COMPOSER_HINT) },
                modifier = Modifier.weight(1f),
                maxLines = 4,
            )
            if (sending && chatId != null) {
                IconButton(onClick = {
                    val id = chatId ?: return@IconButton
                    scope.launch { runCatching { session.client.cancel(id) } }
                }) {
                    Icon(Icons.Outlined.Stop, contentDescription = "stop the reply", tint = Domovoi.colors.brand)
                }
            } else {
                val canSend = (draft.isNotBlank() || picture != null) && !sending && !preparing
                IconButton(onClick = {
                    val text = draft.trim()
                    val pic = picture
                    draft = ""
                    picture = null
                    send(text, pic)
                }, enabled = canSend) {
                    Icon(
                        Icons.AutoMirrored.Filled.Send, contentDescription = "send",
                        tint = if (canSend) Domovoi.colors.brand else Domovoi.colors.fgFaint,
                    )
                }
            }
        }
        ChatPaneGutterSpace()
    }
}

/** A message's pictures: the one this phone holds, or a placeholder for each. */
@Composable
private fun AwayPictures(count: Int, held: ByteArray?) {
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.padding(bottom = 4.dp)) {
        val bmp = remember(held) { held?.let { runCatching { BitmapFactory.decodeByteArray(it, 0, it.size) }.getOrNull() } }
        if (bmp != null) {
            Image(
                bmp.asImageBitmap(), contentDescription = "a picture sent from this phone",
                contentScale = ContentScale.Crop,
                modifier = Modifier.size(84.dp).clip(RoundedCornerShape(8.dp)).border(1.dp, Domovoi.colors.border, RoundedCornerShape(8.dp)),
            )
        } else {
            repeat(count.coerceAtMost(MAX_CHAT_IMAGES)) {
                Box(
                    Modifier.size(84.dp).clip(RoundedCornerShape(8.dp)).background(Domovoi.colors.sunken)
                        .border(1.dp, Domovoi.colors.border, RoundedCornerShape(8.dp))
                        .semantics { contentDescription = "a picture, shown when you're home" },
                    contentAlignment = Alignment.Center,
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(Icons.Outlined.Image, contentDescription = null, tint = Domovoi.colors.fgMuted)
                        Text(AWAY_PICTURE_PLACEHOLDER, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
                    }
                }
            }
        }
    }
}

@Composable
private fun ChatPaneGutterSpace() {
    androidx.compose.foundation.layout.Spacer(Modifier.size(8.dp))
}
