package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.PlaylistAdd
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Save
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.SubdirectoryArrowRight
import androidx.compose.material3.Button
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.ui.components.runAction
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/*
 * The queue's search box (the web's PlayerQueueAdd), drawn wherever the app
 * shows a queue: the full player's Queue tab, Music's player and room-queue
 * tabs, and a room's queue sheet. Primary: it sits at the queue's top, a
 * friendly prompt in it. "Save as playlist" is a quiet button under it that
 * asks for a name (the phone's queue here; a room's queue has its own in
 * the room queue's header, saved by the server from the room's list).
 *
 * Hidden on a phone that isn't paired: it searches the household's library
 * and writes to a queue, and both need the pairing.
 */

/** [target]: where an add goes. [saveIds]: the phone queue's library songs
 *  for "save as playlist", or null where there is no such button. */
@Composable
internal fun QueueAddBox(
    target: QueueTarget,
    saveIds: List<Long>? = null,
    modifier: Modifier = Modifier,
) {
    val app = LocalApp.current
    val token by app.prefs.deviceToken.collectAsState()
    if (token.isNullOrBlank()) return
    val server by app.prefs.serverUrl.collectAsState()

    val prompt = remember { queueAskPrompt() }
    var q by remember { mutableStateOf("") }
    var finds by remember { mutableStateOf<List<QueueFind>?>(null) }   // null = nothing asked yet
    var asked by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }
    val term = queueSearchTerm(q)

    LaunchedEffect(term, server) {
        if (term == null) { finds = null; busy = false; asked = null; return@LaunchedEffect }
        delay(QUEUE_SEARCH_DEBOUNCE_MS)
        busy = true
        // Keyed by the server too: a change of server cancels this read,
        // so one house's songs never land under another's box.
        runAction { queueSearch(app.api, term) }.onSuccess { finds = it; status = "" }
            .onFailure { finds = emptyList(); status = queueSearchFailed(apiErrorText(it)) }
        asked = term
        busy = false
    }

    val adder = remember(app) {
        QueueAdder.forApp(app)
    }
    fun add(row: QueueFind, next: Boolean) {
        // The app's scope: the sheet can close before the room answers.
        app.scope.launch { status = adder.addFind(target, row, next) }
    }

    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        OutlinedTextField(
            value = q,
            onValueChange = { q = it; status = "" },
            placeholder = { Text(prompt, color = Domovoi.colors.fgSubtle, maxLines = 1, overflow = TextOverflow.Ellipsis) },
            leadingIcon = { Icon(Icons.Filled.Search, null, tint = Domovoi.colors.fgSubtle, modifier = Modifier.size(20.dp)) },
            trailingIcon = if (q.isEmpty()) null else {
                {
                    IconButton(onClick = { q = ""; status = "" }) {
                        Icon(Icons.Filled.Close, "clear the search", tint = Domovoi.colors.fgMuted)
                    }
                }
            },
            singleLine = true,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            modifier = Modifier.fillMaxWidth().semantics { contentDescription = QUEUE_SEARCH_LABEL },
        )
        val rows = finds
        if (rows != null && term != null && asked == term) {
            rows.forEach { row -> QueueFindRow(row, onAdd = { add(row, false) }, onNext = { add(row, true) }) }
            if (rows.isEmpty() && !busy && status.isEmpty()) {
                Text(
                    queueNothingMatches(term),
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                    modifier = Modifier.padding(vertical = 6.dp),
                )
            }
        }
        val line = status.ifEmpty { if (busy && term != null) QUEUE_SEARCHING else "" }
        Text(
            line,
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgMuted,
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
        )
        if (saveIds != null) QueueSaveRow(saveIds)
    }
}

/**
 * Play next / Add to queue for a song row, anywhere in the app: to the room
 * this phone casts to, else to this phone's own queue ([queueTargetOf]),
 * read at the tap. One toast says where it went.
 */
@Composable
internal fun rememberSongQueue(): (LibraryTrack, Boolean) -> Unit {
    val app = LocalApp.current
    val to = rememberSongQueueTo()
    return remember(app, to) { { t, next -> to(queueTargetOf(app.player.target.value), t, next) } }
}

/** Play next / Add to queue to a target the caller names (the track drawer's
 *  "this device" and "in <room>" buttons). */
@Composable
internal fun rememberSongQueueTo(): (QueueTarget, LibraryTrack, Boolean) -> Unit {
    val app = LocalApp.current
    val toast = LocalToast.current
    val adder = remember(app) {
        QueueAdder.forApp(app)
    }
    return remember(app, adder, toast) {
        { target, t, next -> songToQueue(app.scope, adder, target, t, next, toast) }
    }
}

/** One song to [target]'s queue, said in a toast; a room's refusal is said too. */
internal fun songToQueue(
    scope: kotlinx.coroutines.CoroutineScope,
    adder: QueueAdder,
    target: QueueTarget,
    t: LibraryTrack,
    next: Boolean,
    toast: (String) -> Unit,
): kotlinx.coroutines.Job = scope.launch {
    val title = t.title ?: "track"
    runAction { adder.add(target, listOf(t), next) }
        .onSuccess { toast(songQueuedToast(target, title, next)) }
        .onFailure { toast(queueAddFailed("\"$title\"", apiErrorText(it))) }
}

/** `GET /api/music/genres`'s rows (the paged answer's items). */
@kotlinx.serialization.Serializable
internal data class GenrePage(val items: List<GenreRow> = emptyList())

/** One find: tap to add it, or the two 48dp buttons (the web's play next / add to queue). */
@Composable
private fun QueueFindRow(row: QueueFind, onAdd: () -> Unit, onNext: () -> Unit) {
    val label = queueFindLabel(row)
    Row(
        Modifier.fillMaxWidth().heightIn(min = 56.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(
            Modifier.weight(1f).clickable(onClickLabel = addToQueueLabel(label), onClick = onAdd)
                .padding(vertical = 6.dp, horizontal = 4.dp),
        ) {
            Text(
                queueFindTitle(row),
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
            Text(
                queueFindSub(row),
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = Domovoi.colors.fgMuted,
                maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
        }
        IconButton(onClick = onNext, modifier = Modifier.size(48.dp)) {
            Icon(Icons.Filled.SubdirectoryArrowRight, playNextLabel(label), tint = Domovoi.colors.fgMuted)
        }
        IconButton(onClick = onAdd, modifier = Modifier.size(48.dp)) {
            Icon(Icons.AutoMirrored.Filled.PlaylistAdd, addToQueueLabel(label), tint = Domovoi.colors.brand)
        }
    }
}

/** "save as playlist": a quiet button that opens a name field (the web's PlayerQueueSave). */
@Composable
private fun QueueSaveRow(trackIds: List<Long>) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val flow = remember { SaveAsPlaylistFlow() }

    fun save() {
        if (!flow.canSave(trackIds)) return
        // The app's scope: the sheet can close before the server answers.
        app.scope.launch { if (flow.save(app.api, trackIds)) toast(flow.note) }
    }

    SaveAsPlaylistRow(
        flow = flow,
        enabled = trackIds.isNotEmpty(),
        hint = if (trackIds.isNotEmpty()) SAVE_AS_PLAYLIST_HINT else SAVE_AS_PLAYLIST_NONE,
        canSave = flow.canSave(trackIds),
        onSave = ::save,
    )
}

/**
 * The save button and, once pressed, its name field: the phone's queue
 * ([QueueSaveRow]) and a room's (the room queue's header). [hint]: the
 * button's TalkBack name; [onSave] sends.
 */
@Composable
internal fun SaveAsPlaylistRow(
    flow: SaveAsPlaylistFlow,
    enabled: Boolean,
    hint: String,
    canSave: Boolean,
    onSave: () -> Unit,
) {
    if (!flow.open) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(
                onClick = flow::ask,
                enabled = enabled,
                modifier = Modifier.heightIn(min = 48.dp).semantics { contentDescription = hint },
            ) {
                Icon(Icons.Filled.Save, null, Modifier.size(16.dp))
                Spacer(Modifier.size(6.dp))
                Text(SAVE_AS_PLAYLIST)
            }
            if (flow.note.isNotEmpty()) NoteText(flow.note)
        }
        return
    }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        OutlinedTextField(
            value = flow.name,
            onValueChange = { flow.name = it },
            placeholder = { Text(PLAYLIST_NAME_PROMPT, color = Domovoi.colors.fgSubtle) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { onSave() }),
            modifier = Modifier.fillMaxWidth().semantics { contentDescription = "playlist name" },
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Button(onClick = onSave, enabled = canSave, modifier = Modifier.heightIn(min = 48.dp)) {
                Icon(Icons.Filled.Save, null, Modifier.size(16.dp))
                Spacer(Modifier.size(6.dp))
                Text("save")
            }
            TextButton(onClick = flow::cancel, modifier = Modifier.heightIn(min = 48.dp)) { Text("cancel") }
        }
        if (flow.note.isNotEmpty()) NoteText(flow.note)
    }
}

@Composable
private fun NoteText(note: String) {
    Text(
        note,
        style = MaterialTheme.typography.bodySmall,
        color = Domovoi.colors.fgMuted,
        modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
    )
}
