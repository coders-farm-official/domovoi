package com.domovoi.app.ui.screens.audiobooks

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
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
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.audiobooks.BookDownloads
import com.domovoi.app.audiobooks.BookItems
import com.domovoi.app.audiobooks.BookStore
import com.domovoi.app.audiobooks.SaveState
import com.domovoi.app.audiobooks.SavedBook
import com.domovoi.app.audiobooks.StartPlan
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.fmtBytes
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/*
 * The books saved on this phone: the "your device" side of Audiobooks, and
 * the Audiobooks tab of the local shell (no server, or none in reach). Each
 * plays from the phone's own files with its chapters, keeps the one place
 * per book, and can be removed; the header says how much space they take.
 */

/** The cover of a saved book, from its folder (named in the record only
 *  once written; no disk check, as this runs while drawing). */
internal fun savedCover(book: SavedBook, store: BookStore): String? =
    book.meta.cover?.let { store.fileOf(book.key, it) }?.toURI()?.toString()

@Composable
fun SavedBooksScreen(offline: Boolean = false) {
    val app = LocalApp.current
    val saved = rememberSavedBooks()
    var open by rememberSaveable { mutableStateOf<String?>(null) }

    open?.let { key ->
        val book = saved.books.firstOrNull { it.key == key }
        if (book == null && saved.loaded) {
            open = null
        } else if (book != null) {
            BackHandler { open = null }
            SavedBookPage(book, saved, onBack = { open = null })
            return
        }
    }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        PageHeader(
            "Audiobooks",
            if (offline) "saved on this phone — they play with no server" else "on this phone — saved books play with no server",
        )
        if (saved.books.isNotEmpty()) {
            Text(
                "${fmtBytes(saved.usedBytes)} used · ${fmtBytes(saved.freeBytes)} free",
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(top = 6.dp),
            )
        }
        when {
            !saved.loaded -> LoadingState()
            saved.books.isEmpty() -> EmptyState(
                "no books on this phone",
                if (offline) "connect to your Domovoi, open a book and choose save to phone"
                else "open a book on your Domovoi and choose save to phone",
            )
            else -> LazyColumn(
                Modifier.fillMaxSize().padding(top = 12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                items(saved.books, key = { it.key }) { b ->
                    SavedBookRow(b, saved.stateOf(b), savedCover(b, app.books), saved.bytes[b.key] ?: 0L) { open = b.key }
                }
            }
        }
    }
}

@Composable
private fun SavedBookRow(book: SavedBook, state: SaveState, cover: String?, bytes: Long, onOpen: () -> Unit) {
    DomovoiCard(Modifier.fillMaxWidth().clickable(onClick = onOpen), padding = 12) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            BookCover(cover, 56.dp)
            Column(Modifier.weight(1f)) {
                Text(book.meta.title, style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                book.meta.author?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                Text(
                    listOfNotNull(
                        book.meta.durationSec?.let { fmtDur(it) },
                        book.meta.chapters.size.takeIf { it > 0 }?.let { "$it chapters" },
                        fmtBytes(bytes),
                    ).joinToString(" · "),
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                    color = Domovoi.colors.fgFaint,
                )
                if (state is SaveState.Saving) SaveProgress(state, Modifier.fillMaxWidth().padding(top = 4.dp))
                else if (state != SaveState.Saved) SaveBadge(state, book.meta.serverName, Modifier.padding(top = 4.dp))
            }
        }
    }
}

@Composable
private fun SavedBookPage(book: SavedBook, saved: SavedBooksView, onBack: () -> Unit) {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val state = saved.stateOf(book)
    var plan by remember { mutableStateOf<StartPlan?>(null) }
    var confirmRemove by remember { mutableStateOf(false) }
    val playing by app.player.isPlaying.collectAsState()
    val place by produceState<Double?>(null, book.key, playing, plan) {
        value = withContext(Dispatchers.IO) {
            runCatching { app.bookPositions.local(book.meta.bookId, book.meta.server)?.positionSec }.getOrNull()
        }
    }
    val item = remember(book) { BookItems.saved(book, app.books) }

    fun play(fromSec: Double? = null) {
        val it = item ?: return
        if (fromSec != null) {
            scope.launch {
                val (_, speed) = app.player.fetchPosition(it)
                app.player.playItems(listOf(it), 0, fromSec, speed)
            }
        } else {
            startBook(app, scope, it, book.meta.bookId) { p -> plan = p }
        }
    }

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 4.dp, top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "back to saved books", tint = Domovoi.colors.fgMuted)
            }
            Text("saved books", style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.fgMuted)
        }
        LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(start = 16.dp, end = 16.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            item(key = "head") {
                BookHead(
                    cover = savedCover(book, app.books),
                    title = book.meta.title,
                    author = book.meta.author,
                    narrator = book.meta.narrator,
                    meta = listOfNotNull(
                        book.meta.durationSec?.let { fmtDur(it) },
                        fmtBytes(saved.bytes[book.key] ?: 0L),
                        book.meta.serverName?.let { "from $it" },
                    ).joinToString(" · "),
                    place = placeText(place, book.meta.durationSec),
                    state = state,
                    serverName = book.meta.serverName,
                )
            }
            item(key = "actions") {
                Column(Modifier.padding(vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (item != null) Button(onClick = { play() }) { Text(if ((place ?: 0.0) > 5) "resume" else "play") }
                        when (state) {
                            is SaveState.Stopped -> OutlinedButton(onClick = { BookDownloads.start(context.applicationContext, book.key, now = true) }) {
                                Text("resume save", color = Domovoi.colors.fg)
                            }
                            is SaveState.Waiting -> OutlinedButton(onClick = { BookDownloads.start(context.applicationContext, book.key, now = true) }) {
                                Text("try now", color = Domovoi.colors.fg)
                            }
                            else -> {}
                        }
                        OutlinedButton(onClick = { confirmRemove = true }) {
                            Text(if (state == SaveState.Saved) "remove from phone" else "cancel save", color = Domovoi.colors.fg)
                        }
                    }
                    if (state is SaveState.Saving) SaveProgress(state, Modifier.fillMaxWidth())
                }
            }
            if (book.meta.chapters.isNotEmpty()) {
                item(key = "chapters-label") { SectionLabel("chapters", Modifier.padding(top = 8.dp, bottom = 4.dp)) }
                val current = place ?: 0.0
                val at = book.meta.chapters.indexOfLast { it.startSec <= current + 0.5 }.takeIf { (place ?: 0.0) > 5 } ?: -1
                itemsIndexed(book.meta.chapters, key = { i, _ -> "ch-$i" }) { i, ch ->
                    ChapterRow(i, ch.title, ch.startSec, current = i == at) { if (item != null) play(ch.startSec) }
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
    if (confirmRemove) {
        ConfirmDialog(
            title = "Remove \"${book.meta.title}\" from this phone?",
            body = if (state == SaveState.Saved) "It stays on the server, and your place is kept." else "What was saved so far is deleted.",
            confirm = "remove",
            danger = true,
            onDismiss = { confirmRemove = false },
            onConfirm = {
                confirmRemove = false
                // A book playing from these files stops first.
                if (app.player.current?.savedKey == book.key) app.player.stop()
                BookDownloads.stop(context.applicationContext, book.key)
                scope.launch {
                    withContext(Dispatchers.IO) { runCatching { app.books.remove(book.key) } }
                    saved.refresh()
                    toast("removed \"${book.meta.title}\" from this phone")
                    onBack()
                }
            },
        )
    }
}
