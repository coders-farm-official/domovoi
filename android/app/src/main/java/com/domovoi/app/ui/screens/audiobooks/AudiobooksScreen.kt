package com.domovoi.app.ui.screens.audiobooks

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.MenuBook
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.DeviceDownloads
import com.domovoi.app.net.decode
import com.domovoi.app.net.rememberApi
import com.domovoi.app.player.Chapter
import com.domovoi.app.player.PlayItem
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.ErrorState
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.LoadingState
import com.domovoi.app.ui.components.PageHeader
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

// ---------------------------------------------------------------------------
// Models — /api/audiobooks rows (web/static/audiobooks.jsx).
// ---------------------------------------------------------------------------

@Serializable
private data class BookChapterRow(
    val title: String? = null,
    val start_sec: Double = 0.0,
)

@Serializable
private data class AudiobookRow(
    val id: Long = 0,
    val title: String? = null,
    val author: String? = null,
    val artwork: String? = null,
    val duration_sec: Double? = null,
    val is_folder: Boolean = false,
    val file_ext: String? = null,   // single-file books; folder books zip
    val chapters: List<BookChapterRow> = emptyList(),
)

@Serializable
private data class PersonRow(
    val id: Long = 0,
    val name: String? = null,
)

private class ResumeRequest(val item: PlayItem, val positionSec: Double, val speed: Float)

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

@Composable
fun AudiobooksScreen() {
    val app = LocalApp.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()

    val books = rememberApi("audiobooks", eventTypes = setOf("podcasts.changed")) {
        it.api.get("/api/audiobooks").decode<List<AudiobookRow>>()
    }

    val context = LocalContext.current
    var busy by remember { mutableStateOf(false) }
    var resume by remember { mutableStateOf<ResumeRequest?>(null) }

    fun saveToDevice(book: AudiobookRow) {
        // Single-file books save as the file; folder books arrive as a zip
        // of the chapter files (the server builds it, so allow a pause
        // before DownloadManager shows progress).
        val ext = if (book.is_folder) ".zip" else (book.file_ext ?: ".m4b")
        val name = DeviceDownloads.safeName(
            book.title ?: "audiobook-${book.id}", fallback = "audiobook",
        ) + ext
        val err = DeviceDownloads.enqueue(
            context,
            app.api.absolute("/api/audiobooks/${book.id}/download"),
            name,
            mimeType = if (book.is_folder) "application/zip" else null,
        )
        toast(err ?: "saving \"$name\" to Downloads/Domovoi")
    }

    fun reindex() {
        if (busy) return
        busy = true
        scope.launch {
            runCatching { app.api.post("/api/audiobooks/reindex").jsonObject }
                .onSuccess { r ->
                    val scanned = r["scanned"]?.jsonPrimitive?.intOrNull ?: 0
                    toast("Indexed $scanned book(s)")
                    books.refresh()
                }
                .onFailure { toast("Reindex failed") }
            busy = false
        }
    }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        PageHeader(
            "Audiobooks",
            "local books with chapters + resume, played here or cast to a room",
            actions = {
                OutlinedButton(onClick = { reindex() }, enabled = !busy) {
                    Text(if (busy) "indexing…" else "reindex")
                }
            },
        )
        ListeningAsChip(Modifier.padding(top = 8.dp))
        Spacer(Modifier.height(12.dp))

        when {
            books.data == null && books.loading -> LoadingState()
            books.data == null && books.error != null ->
                ErrorState(books.error ?: "request failed", books.refresh)
            books.data.isNullOrEmpty() -> EmptyState(
                "no audiobooks yet",
                "drop .m4b files or per-chapter folders into your audiobooks dir, then reindex",
                action = {
                    Button(onClick = { reindex() }, enabled = !busy) { Text("reindex") }
                },
            )
            else -> LazyVerticalGrid(
                columns = GridCells.Adaptive(260.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.weight(1f),
            ) {
                items(books.data.orEmpty(), key = { it.id }) { book ->
                    BookCard(
                        book,
                        onSave = { saveToDevice(book) },
                    ) { playBook(app, scope, book) { resume = it } }
                }
            }
        }
    }

    resume?.let { req ->
        ResumeDialog(req, onDismiss = { resume = null }) { startSec ->
            app.player.playItems(listOf(req.item), 0, startSec, req.speed)
            resume = null
        }
    }
}

private fun playBook(
    app: AppContainer,
    scope: CoroutineScope,
    book: AudiobookRow,
    onResumePrompt: (ResumeRequest) -> Unit,
) {
    val item = PlayItem.fromBook(
        id = book.id,
        title = book.title ?: "audiobook",
        author = book.author,
        durationSec = book.duration_sec,
        artwork = book.artwork,
        chapters = book.chapters.map { Chapter(it.title ?: "", it.start_sec) },
    )
    scope.launch {
        val (pos, speed) = app.player.fetchPosition(item)
        if (pos > 5) onResumePrompt(ResumeRequest(item, pos, speed))
        else app.player.playItems(listOf(item), 0, 0.0, speed)
    }
}

@Composable
private fun BookCard(book: AudiobookRow, onSave: () -> Unit, onPlay: () -> Unit) {
    DomovoiCard(modifier = Modifier.fillMaxWidth(), padding = 14) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            BookCover(book.artwork)
            Column(Modifier.weight(1f)) {
                Text(
                    book.title ?: "audiobook",
                    style = MaterialTheme.typography.titleSmall,
                    color = Domovoi.colors.fg,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                book.author?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                val meta = buildString {
                    append(if (book.duration_sec != null) fmtDur(book.duration_sec) else "—")
                    if (book.chapters.isNotEmpty()) append(" · ${book.chapters.size} chapters")
                }
                Text(
                    meta,
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                    color = Domovoi.colors.fgFaint,
                    modifier = Modifier.padding(top = 2.dp),
                )
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = onPlay) { Text("play") }
                    OutlinedButton(onClick = onSave) {
                        Icon(
                            Icons.Outlined.Download, contentDescription = null,
                            tint = Domovoi.colors.fg, modifier = Modifier.size(16.dp),
                        )
                        Spacer(Modifier.size(6.dp))
                        Text(if (book.is_folder) "save zip" else "save", color = Domovoi.colors.fg)
                    }
                }
            }
        }
    }
}

@Composable
private fun BookCover(artwork: String?) {
    val app = LocalApp.current
    val shape = RoundedCornerShape(8.dp)
    if (artwork.isNullOrBlank()) {
        Box(
            Modifier.size(56.dp)
                .background(Domovoi.colors.sunken, shape)
                .border(1.dp, Domovoi.colors.border, shape),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                Icons.AutoMirrored.Outlined.MenuBook,
                contentDescription = null,
                tint = Domovoi.colors.fgSubtle,
                modifier = Modifier.size(26.dp),
            )
        }
    } else {
        AsyncImage(
            model = app.api.absolute(artwork),
            contentDescription = null,
            contentScale = ContentScale.Crop,
            modifier = Modifier.size(56.dp)
                .clip(shape)
                .border(1.dp, Domovoi.colors.border, shape),
        )
    }
}

/** "listening as" hint chip — the full selector lives in Settings > Connection. */
@Composable
private fun ListeningAsChip(modifier: Modifier = Modifier) {
    val app = LocalApp.current
    val listenerId by app.prefs.listenerPersonId.collectAsState()
    val id = listenerId ?: return
    val people = rememberApi("people-hint") { it.api.get("/api/people").decode<List<PersonRow>>() }
    val name = people.data?.firstOrNull { it.id.toString() == id }?.name
    Box(modifier) { Pill("listening as ${name ?: "…"}", Tone.Brand) }
}

@Composable
private fun ResumeDialog(
    req: ResumeRequest,
    onDismiss: () -> Unit,
    onPlay: (resumeSec: Double) -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Domovoi.colors.raised,
        title = { Text(req.item.title, style = MaterialTheme.typography.titleMedium) },
        text = {
            Text(
                "You were at ${fmtDur(req.positionSec)}.",
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgMuted,
            )
        },
        confirmButton = {
            TextButton(onClick = { onPlay(req.positionSec) }) {
                Text("resume", color = Domovoi.colors.brand)
            }
        },
        dismissButton = {
            TextButton(onClick = { onPlay(0.0) }) {
                Text("start over", color = Domovoi.colors.fgMuted)
            }
        },
    )
}
