package com.domovoi.app.ui.screens.audiobooks

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.audiobooks.BookDownloads
import com.domovoi.app.audiobooks.BookFetcher
import com.domovoi.app.audiobooks.BookItems
import com.domovoi.app.audiobooks.BookModels
import com.domovoi.app.audiobooks.IdentityAdmission
import com.domovoi.app.audiobooks.RemoveOutcome
import com.domovoi.app.audiobooks.SaveState
import com.domovoi.app.audiobooks.SavedBookMeta
import com.domovoi.app.audiobooks.ServerBook
import com.domovoi.app.audiobooks.ServerRemove
import com.domovoi.app.audiobooks.StartPlan
import com.domovoi.app.net.rememberApi
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.fmtBytes
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.shell.LocalServerChoice
import com.domovoi.app.ui.shell.title
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * One book on the server: cover, title, author and narrator, length and
 * size, the listener's place, the chapters (a tap plays from there), and
 * what can be done with it — play, save to this phone (its size shown
 * first), cancel or remove the saved copy, and for an admin, remove it from
 * the server.
 */
@Composable
internal fun BookPage(
    bookId: Long,
    saved: SavedBooksView,
    onBack: () -> Unit,
    onRemovedFromServer: () -> Unit,
) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val serverName = LocalServerChoice.current.title()
    val detail = rememberApi("audiobook", bookId) { BookModels.parse(it.api.get("/api/audiobooks/$bookId")) }
    val book = detail.data

    val server = app.activeServerScope()
    val copy = saved.books.firstOrNull { it.meta.bookId == bookId && it.meta.server.isSameServer(server) }
    val state = saved.stateOf(copy)

    var plan by remember { mutableStateOf<StartPlan?>(null) }
    var confirmSave by remember { mutableStateOf(false) }
    var confirmRemoveCopy by remember { mutableStateOf(false) }
    var removeServer by remember { mutableStateOf(false) }
    var removeNeedsPassword by remember { mutableStateOf(true) }
    var removeBusy by remember { mutableStateOf(false) }
    var removeError by remember { mutableStateOf<String?>(null) }

    val playing by app.player.isPlaying.collectAsState()
    val place by produceState<Double?>(null, bookId, playing, plan) {
        value = withContext(Dispatchers.IO) { runCatching { app.bookPositions.local(bookId, server)?.positionSec }.getOrNull() }
    }

    fun play(fromSec: Double? = null) {
        val b = book ?: return
        val item = copy?.let { BookItems.saved(it, app.books) } ?: BookItems.streaming(b)
        if (fromSec != null) {
            scope.launch {
                val (_, speed) = app.player.fetchPosition(item)
                app.player.playItems(listOf(item), 0, fromSec, speed)
            }
        } else {
            startBook(app, scope, item, b.id) { plan = it }
        }
    }

    fun save(b: ServerBook) {
        val meta = SavedBookMeta.fromServer(b, server, serverName, System.currentTimeMillis())
        scope.launch {
            val made = withContext(Dispatchers.IO) {
                runCatching { app.books.find(b.id, server) ?: app.books.create(meta) }
            }
            made.onSuccess {
                BookDownloads.start(context.applicationContext, it.key, now = true)
                saved.refresh()
                toast("saving \"${b.displayTitle}\" to this phone")
            }.onFailure { toast("couldn't start the save: ${it.message}") }
        }
    }

    fun removeCopy(alsoSay: String) {
        val key = copy?.key ?: return
        // A book playing from these files stops first.
        if (app.player.current?.savedKey == key) app.player.stop()
        BookDownloads.stop(context.applicationContext, key)
        scope.launch {
            withContext(Dispatchers.IO) { runCatching { app.books.remove(key) } }
            saved.refresh()
            toast(alsoSay)
        }
    }

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 4.dp, top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "back to audiobooks", tint = Domovoi.colors.fgMuted)
            }
            Text("audiobooks", style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.fgMuted)
        }
        when {
            book == null && detail.loading -> LoadingState()
            book == null -> ErrorState(detail.error ?: "request failed", detail.refresh)
            else -> LazyColumn(
                Modifier.fillMaxSize(),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(start = 16.dp, end = 16.dp, bottom = 24.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                item(key = "head") {
                    BookHead(
                        cover = BookModels.coverPath(book)?.let(app.api::absolute),
                        title = book.displayTitle,
                        author = book.author,
                        narrator = book.narrator,
                        meta = listOfNotNull(
                            book.duration_sec?.let { fmtDur(it) },
                            BookModels.sizeBytes(book)?.let { fmtBytes(it) },
                            BookModels.chapters(book).size.takeIf { it > 0 }?.let { "$it chapters" },
                        ).joinToString(" · "),
                        place = placeText(place, book.duration_sec),
                        state = state,
                        serverName = serverName,
                    )
                }
                item(key = "actions") {
                    Column(Modifier.padding(vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { play() }) { Text(if ((place ?: 0.0) > 5) "resume" else "play") }
                            when (state) {
                                SaveState.None -> OutlinedButton(onClick = { confirmSave = true }) {
                                    Icon(Icons.Outlined.Download, contentDescription = null, tint = Domovoi.colors.fg, modifier = Modifier.size(16.dp))
                                    Text(
                                        "  save to phone" + (BookModels.sizeBytes(book)?.let { " · " + fmtBytes(it) } ?: ""),
                                        color = Domovoi.colors.fg,
                                    )
                                }
                                SaveState.Saved -> OutlinedButton(onClick = { confirmRemoveCopy = true }) {
                                    Text("remove from phone", color = Domovoi.colors.fg)
                                }
                                is SaveState.Saving, is SaveState.Waiting -> OutlinedButton(onClick = { removeCopy("save cancelled") }) {
                                    Text("cancel save", color = Domovoi.colors.fg)
                                }
                                is SaveState.Stopped -> {
                                    OutlinedButton(onClick = { copy?.let { BookDownloads.start(context.applicationContext, it.key, now = true) } }) {
                                        Text("resume save", color = Domovoi.colors.fg)
                                    }
                                }
                            }
                        }
                        if (state is SaveState.Saving) SaveProgress(state, Modifier.fillMaxWidth())
                        if (state is SaveState.Waiting) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    "the save carries on by itself whenever $serverName is in reach",
                                    style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
                                    modifier = Modifier.weight(1f),
                                )
                                TextButton(onClick = { copy?.let { BookDownloads.start(context.applicationContext, it.key, now = true) } }) {
                                    Text("try now", color = Domovoi.colors.brand)
                                }
                            }
                        }
                        if (state is SaveState.Stopped) {
                            TextButton(onClick = { removeCopy("removed from this phone") }) {
                                Text("discard what was saved", color = Domovoi.colors.fgMuted)
                            }
                        }
                    }
                }
                val chapters = BookModels.chapters(book)
                if (chapters.isNotEmpty()) {
                    item(key = "chapters-label") { SectionLabel("chapters", Modifier.padding(top = 8.dp, bottom = 4.dp)) }
                    val current = place ?: 0.0
                    val at = chapters.indexOfLast { it.startSec <= current + 0.5 }.takeIf { (place ?: 0.0) > 5 } ?: -1
                    itemsIndexed(chapters, key = { i, _ -> "ch-$i" }) { i, ch ->
                        ChapterRow(i, ch.title, ch.startSec, current = i == at) { play(ch.startSec) }
                    }
                }
                // Only a path the phone may ask the server to delete
                // (BookModels.safeLibraryPath); none from an older server.
                if (BookModels.safeLibraryPath(book.library_path) != null) {
                    item(key = "remove-server") {
                        TextButton(
                            onClick = {
                                removeError = null
                                removeServer = true
                                scope.launch {
                                    removeNeedsPassword = runCatching { ServerRemove(app.api, IdentityAdmission.of(app.identity)).needsPassword() }.getOrDefault(true)
                                }
                            },
                            modifier = Modifier.padding(top = 16.dp),
                        ) { Text("remove from server (admin)", color = Domovoi.colors.err) }
                    }
                }
            }
        }
    }

    plan?.let { p ->
        BookResumeDialog(p, onDismiss = { plan = null }) { start ->
            playPlan(app, p, start)
            plan = null
        }
    }
    if (confirmSave && book != null) {
        val size = BookModels.sizeBytes(book)
        val free = saved.freeBytes
        val fits = size == null || free <= 0 || size + BookFetcher.SPACE_MARGIN <= free
        ConfirmDialog(
            title = "Save \"${book.displayTitle}\" to this phone?",
            body = (if (size != null) "${fmtBytes(size)}" else "Size unknown") +
                (if (free > 0) " · ${fmtBytes(free)} free on this phone." else ".") +
                if (fits) {
                    " It downloads from $serverName on your home network, plays with no server, and keeps your place in step." +
                        " If you leave home first, it carries on by itself when the server is back in reach."
                } else {
                    " There isn't enough free space for it."
                },
            confirm = if (fits) "save" else "ok",
            onDismiss = { confirmSave = false },
            onConfirm = {
                confirmSave = false
                if (fits) save(book)
            },
        )
    }
    if (confirmRemoveCopy && book != null) {
        ConfirmDialog(
            title = "Remove \"${book.displayTitle}\" from this phone?",
            body = "It stays on the server, and your place is kept.",
            confirm = "remove",
            danger = true,
            onDismiss = { confirmRemoveCopy = false },
            onConfirm = {
                confirmRemoveCopy = false
                removeCopy("removed from this phone")
            },
        )
    }
    if (removeServer && book != null) {
        val path = BookModels.safeLibraryPath(book.library_path)
        RemoveFromServerDialog(
            title = book.displayTitle,
            needsPassword = removeNeedsPassword,
            busy = removeBusy,
            error = removeError,
            onDismiss = { removeServer = false },
        ) { password ->
            if (path == null) return@RemoveFromServerDialog
            removeBusy = true
            scope.launch {
                when (val r = ServerRemove(app.api, IdentityAdmission.of(app.identity)).remove(path, book.is_folder, password)) {
                    is RemoveOutcome.Removed -> {
                        removeServer = false
                        toast("removed \"${book.displayTitle}\" from the server")
                        onRemovedFromServer()
                    }
                    is RemoveOutcome.Refused -> removeError = r.reason
                }
                removeBusy = false
            }
        }
    }
}

/** The top of a book page. */
@Composable
internal fun BookHead(
    cover: String?,
    title: String,
    author: String?,
    narrator: String?,
    meta: String,
    place: String?,
    state: SaveState,
    serverName: String?,
) {
    DomovoiCard(Modifier.fillMaxWidth(), padding = 14) {
        Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            BookCover(cover, 112.dp)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(title, style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
                author?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted) }
                narrator?.let { Text("read by $it", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle) }
                if (meta.isNotBlank()) {
                    Text(
                        meta,
                        style = MaterialTheme.typography.labelSmall.copy(fontFamily = com.domovoi.app.ui.theme.MonoFamily),
                        color = Domovoi.colors.fgFaint,
                        modifier = Modifier.padding(top = 2.dp),
                    )
                }
                place?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted, modifier = Modifier.padding(top = 2.dp))
                }
                SaveBadge(state, serverName, Modifier.padding(top = 6.dp))
            }
        }
    }
}
