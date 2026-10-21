package com.domovoi.app.ui.screens.audiobooks

import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Upload
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.audiobooks.BookDownloads
import com.domovoi.app.audiobooks.BookItems
import com.domovoi.app.audiobooks.BookModels
import com.domovoi.app.audiobooks.BookStart
import com.domovoi.app.audiobooks.BookUpload
import com.domovoi.app.audiobooks.BookUploadRules
import com.domovoi.app.audiobooks.PickedFile
import com.domovoi.app.audiobooks.ServerBook
import com.domovoi.app.audiobooks.StartPlan
import com.domovoi.app.net.Paging
import com.domovoi.app.net.decode
import com.domovoi.app.net.failureText
import com.domovoi.app.net.rememberApi
import com.domovoi.app.net.toPage
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.LibraryRefreshBox
import com.domovoi.app.ui.components.LoadMoreNearEnd
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.PagedFooter
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtBytes
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.components.rememberLibraryRefresh
import com.domovoi.app.ui.components.rememberPager
import com.domovoi.app.ui.screens.local.uploadFailureText
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.dropWhile
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/*
 * The server's side of Audiobooks (with "your device" beside it,
 * ScreenRouter): the books on the Domovoi, sorted by title, author or newest,
 * each opening its own page (BookPage.kt) with the cover, chapters, size, the
 * listener's place, "save to phone" and, for an admin, "remove from server".
 * "add" sends audiobook files or a folder from the phone to the server.
 */

@Serializable
private data class PersonRow(val id: Long = 0, val name: String? = null)

/** The server's sort keys (web/backend/api/audiobooks.py AudiobookSort). */
internal enum class BookSort(val key: String, val label: String) {
    Title("title", "title"),
    Author("author", "author"),
    Newest("newest", "newest"),
}

@Composable
fun AudiobooksScreen() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    var openBook by rememberSaveable { mutableStateOf<Long?>(null) }
    var search by rememberSaveable { mutableStateOf("") }
    var sortName by rememberSaveable { mutableStateOf(BookSort.Title.name) }
    val sort = BookSort.entries.firstOrNull { it.name == sortName } ?: BookSort.Title
    var needle by remember { mutableStateOf(search.trim()) }
    LaunchedEffect(search) {
        kotlinx.coroutines.delay(250)
        needle = search.trim()
    }
    val booksPath = "/api/audiobooks?sort=${sort.key}" +
        (if (needle.isNotEmpty()) "&q=" + Uri.encode(needle) else "")
    val pager = rememberPager<ServerBook>(
        booksPath, keyOf = { it.id }, eventTypes = setOf("podcasts.changed"),
    ) { a, req -> a.api.get(Paging.path(booksPath, req)).toPage<ServerBook>(loadedBefore = req.offset) }
    val books by pager.state.collectAsState()
    val gridState = rememberLazyGridState()
    LoadMoreNearEnd(gridState, pager)
    // A new sort or search is a new list, and it opens at the top (batch 18:
    // it opened where the old list was scrolled to, the first book out of
    // view). Once the new list has loaded (the reset's load started, then
    // ended), jump to its first book; the jump also drops the old first
    // book's key, which a lazy grid otherwise follows into the new list. A
    // fresh grid state per list was not enough on the emulator: "author"
    // still opened one book down. [toppedFor] is saved, so coming back to the
    // page or turning the phone keeps the place in the same list.
    val listKey = "${sort.key}|$needle"
    var toppedFor by rememberSaveable { mutableStateOf(listKey) }
    LaunchedEffect(listKey) {
        if (toppedFor == listKey) return@LaunchedEffect
        pager.state.dropWhile { !it.loading }.first { !it.loading }
        gridState.scrollToItem(0)
        toppedFor = listKey
    }
    val saved = rememberSavedBooks()

    // Places set while away go up, and waiting saves try, once the page is
    // open on the server (it answered: the grid loaded).
    LaunchedEffect(books.items.isNotEmpty()) {
        if (books.items.isNotEmpty()) {
            withContext(Dispatchers.IO) {
                runCatching { app.bookPositions.pushAll(app.activeServerScope()) }
                runCatching { BookDownloads.kickWaiting(context.applicationContext, app.books) }
            }
        }
    }

    var plan by remember { mutableStateOf<StartPlan?>(null) }
    val upload = rememberBookUpload { pager.reload() }

    val refresh = rememberLibraryRefresh {
        runCatching { app.api.post("/api/audiobooks/reindex").jsonObject }
            .onSuccess { r ->
                val scanned = r["scanned"]?.jsonPrimitive?.intOrNull ?: 0
                toast("Indexed $scanned book(s)")
                pager.reload()
                pager.idle()
            }
            .onFailure { toast(failureText("Reindex", it)) }
    }

    openBook?.let { id ->
        BackHandler { openBook = null }
        BookPage(
            bookId = id,
            saved = saved,
            onBack = { openBook = null },
            onRemovedFromServer = {
                openBook = null
                pager.reload()
            },
        )
        return
    }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        PageHeader(
            "Audiobooks",
            "books with chapters and your place, played here, in a room, or saved to this phone",
            actions = {
                IconButton(onClick = { upload.choose() }, enabled = !upload.busy) {
                    Icon(Icons.Outlined.Upload, contentDescription = "add to server", tint = Domovoi.colors.fgMuted)
                }
                OutlinedButton(onClick = { refresh.refresh() }, enabled = !refresh.busy) {
                    Text(if (refresh.busy) "indexing…" else "reindex")
                }
            },
        )
        ListeningAsChip(Modifier.padding(top = 8.dp))
        OutlinedTextField(
            value = search, onValueChange = { search = it },
            placeholder = { Text("search books…") }, singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
        )
        Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            BookSort.entries.forEach { s ->
                FilterChip(
                    selected = s == sort,
                    onClick = { sortName = s.name },
                    label = { Text(s.label) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = Domovoi.colors.brandSoft,
                        selectedLabelColor = Domovoi.colors.fg,
                    ),
                )
            }
        }
        upload.status?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted, modifier = Modifier.padding(top = 4.dp))
        }
        Spacer(Modifier.height(8.dp))

        LibraryRefreshBox(refresh, Modifier.fillMaxWidth().weight(1f)) { when {
            books.items.isEmpty() && books.loading -> LoadingState()
            books.items.isEmpty() && books.error != null ->
                ErrorState(books.error ?: "request failed", pager::retry)
            books.items.isEmpty() && needle.isNotEmpty() -> EmptyState("no books match", "search = \"$needle\"")
            books.items.isEmpty() -> EmptyState(
                "no audiobooks yet",
                "add .m4b files or a folder per book from this phone, or drop them into the server's audiobooks folder and reindex",
                action = { Button(onClick = { upload.choose() }) { Text("add to server") } },
            )
            else -> LazyVerticalGrid(
                state = gridState,
                columns = GridCells.Adaptive(260.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                items(books.items, key = { it.id }) { book ->
                    val server = app.activeServerScope()
                    val copy = saved.books.firstOrNull { it.meta.bookId == book.id && it.meta.server.isSameServer(server) }
                    BookCard(
                        book,
                        copyState = saved.stateOf(copy),
                        onOpen = { openBook = book.id },
                        onPlay = {
                            val local = copy?.let { BookItems.saved(it, app.books) }
                            startBook(app, scope, local ?: BookItems.streaming(book), book.id) { plan = it }
                        },
                    )
                }
                item(key = "books-footer", span = { GridItemSpan(maxLineSpan) }) {
                    PagedFooter(books, "books", onRetry = pager::retry, onLoadMore = pager::loadMore)
                }
            }
        } }
    }

    plan?.let { p ->
        BookResumeDialog(p, onDismiss = { plan = null }) { start ->
            playPlan(app, p, start)
            plan = null
        }
    }
    upload.Dialogs()
}

/** Starts [item] (book [bookId]) here, asking first when there is a place to resume or a room further on. */
internal fun startBook(
    app: AppContainer,
    scope: CoroutineScope,
    item: com.domovoi.app.player.PlayItem,
    bookId: Long,
    onAsk: (StartPlan) -> Unit,
) {
    scope.launch {
        val server = item.origin ?: app.activeServerScope()
        val plan = BookStart.plan(app, item, bookId, server)
        if (plan.asks) onAsk(plan) else app.player.playItems(listOf(item), 0, 0.0, plan.speed)
    }
}

@Composable
private fun BookCard(
    book: ServerBook,
    copyState: com.domovoi.app.audiobooks.SaveState,
    onOpen: () -> Unit,
    onPlay: () -> Unit,
) {
    val app = LocalApp.current
    DomovoiCard(modifier = Modifier.fillMaxWidth().clickable(onClick = onOpen), padding = 14) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            BookCover(BookModels.coverPath(book)?.let(app.api::absolute), 56.dp)
            Column(Modifier.weight(1f)) {
                Text(
                    book.displayTitle,
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                book.author?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                val meta = buildString {
                    append(if (book.duration_sec != null) fmtDur(book.duration_sec) else "—")
                    if (book.chapters.isNotEmpty()) append(" · ${book.chapters.size} chapters")
                    BookModels.sizeBytes(book)?.let { append(" · " + fmtBytes(it)) }
                }
                Text(
                    meta,
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                    color = Domovoi.colors.fgFaint,
                    modifier = Modifier.padding(top = 2.dp),
                )
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    Button(onClick = onPlay) { Text("play") }
                    SaveBadge(copyState)
                }
            }
        }
    }
}

/** "listening as" hint chip — the full selector lives in Settings > Phones & browsers. */
@Composable
private fun ListeningAsChip(modifier: Modifier = Modifier) {
    val app = LocalApp.current
    val listenerId by app.prefs.listenerPersonId.collectAsState()
    val id = listenerId ?: return
    val people = rememberApi("people-hint") { it.api.get("/api/people").decode<List<PersonRow>>() }
    val name = people.data?.firstOrNull { it.id.toString() == id }?.name
    Box(modifier) { Pill("listening as ${name ?: "…"}", Tone.Brand) }
}

// ---------------------------------------------------------------------------
// "add to server": audiobook files (a book each) or a folder (one book).
// ---------------------------------------------------------------------------

internal class BookUploadUi(
    val choose: () -> Unit,
    val busy: Boolean,
    val status: String?,
    val Dialogs: @Composable () -> Unit,
)

@Composable
internal fun rememberBookUpload(onDone: () -> Unit): BookUploadUi {
    val app = LocalApp.current
    val toast = LocalToast.current
    val context = LocalContext.current
    var choosing by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }

    fun run(files: List<PickedFile>, folder: String?) {
        if (files.isEmpty()) {
            toast(if (folder != null) "no audio files in that folder" else "nothing picked")
            return
        }
        busy = true
        val label = folder ?: if (files.size == 1) files[0].name else "${files.size} files"
        status = "adding \"$label\" to the server…"
        app.scope.launch {
            runCatching {
                BookUpload.send(context.applicationContext, app, files, folder) { i, pct ->
                    app.scope.launch {
                        status = "adding \"$label\" · file ${i + 1} of ${files.size} · $pct%"
                    }
                }
            }.onSuccess { r ->
                toast(
                    "added \"$label\" to the server" +
                        (if (r.skipped > 0) " · ${r.skipped} skipped" else "") +
                        (if (r.reindexTriggered) " · indexing…" else ""),
                )
                onDone()
            }.onFailure { e ->
                if (e is kotlinx.coroutines.CancellationException) return@onFailure
                toast(
                    if (BookUploadRules.isOldServerRefusal(e)) {
                        "this server can't take a folder yet (it needs an update); add the files one by one instead"
                    } else {
                        uploadFailureText(label, e)
                    },
                )
            }
            busy = false
            status = null
        }
    }

    val pickFiles = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isEmpty()) return@rememberLauncherForActivityResult
        app.scope.launch {
            val picked = withContext(Dispatchers.IO) {
                uris.map { u -> PickedFile(u, displayName(context, u) ?: "audiobook", null) }
            }
            run(picked.filter { BookUploadRules.isAudio(it.name, context.contentResolver.getType(it.uri)) }, null)
        }
    }
    val pickFolder = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { tree ->
        if (tree == null) return@rememberLauncherForActivityResult
        app.scope.launch {
            val folder = runCatching { BookUpload.readFolder(context.applicationContext, tree) }.getOrNull()
            if (folder == null) {
                toast("couldn't read that folder")
                return@launch
            }
            run(folder.files, BookUploadRules.folderName(folder.name))
        }
    }

    return BookUploadUi(
        choose = { if (!busy) choosing = true },
        busy = busy,
        status = status,
        Dialogs = {
            if (choosing) {
                AlertDialog(
                    onDismissRequest = { choosing = false },
                    containerColor = Domovoi.colors.raised,
                    title = { Text("Add audiobooks to the server", style = MaterialTheme.typography.titleMedium) },
                    text = {
                        Text(
                            "Pick audio files (each one is a book, like an .m4b), or a folder that holds one book " +
                                "as a file per chapter.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = Domovoi.colors.fgMuted,
                        )
                    },
                    confirmButton = {
                        Column(horizontalAlignment = androidx.compose.ui.Alignment.End) {
                            TextButton(onClick = { choosing = false; pickFiles.launch(arrayOf("audio/*", "application/octet-stream")) }) {
                                Text("pick files", color = Domovoi.colors.brand)
                            }
                            TextButton(onClick = { choosing = false; pickFolder.launch(null) }) {
                                Text("pick a folder", color = Domovoi.colors.brand)
                            }
                            TextButton(onClick = { choosing = false }) { Text("cancel", color = Domovoi.colors.fgMuted) }
                        }
                    },
                )
            }
        },
    )
}

private fun displayName(context: android.content.Context, uri: Uri): String? = runCatching {
    context.contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
        if (c.moveToFirst()) c.getString(0) else null
    }
}.getOrNull()
