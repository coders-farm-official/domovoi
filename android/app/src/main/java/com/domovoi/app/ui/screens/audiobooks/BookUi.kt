package com.domovoi.app.ui.screens.audiobooks

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.MenuBook
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.audiobooks.BookDownloads
import com.domovoi.app.audiobooks.BookJob
import com.domovoi.app.audiobooks.ContinueRules
import com.domovoi.app.audiobooks.SaveState
import com.domovoi.app.audiobooks.SaveStates
import com.domovoi.app.audiobooks.SavedBook
import com.domovoi.app.audiobooks.StartPlan
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.fmtBytes
import com.domovoi.app.ui.components.fmtDur
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** The books on this phone and their download jobs, as one view. */
internal class SavedBooksView(
    val books: List<SavedBook>,
    val jobs: Map<String, BookJob>,
    val usedBytes: Long,
    /** Bytes each book takes now, by folder key. */
    val bytes: Map<String, Long>,
    val freeBytes: Long,
    val loaded: Boolean,
    val refresh: () -> Unit,
) {
    fun stateOf(book: SavedBook?): SaveState = SaveStates.of(book?.meta, book?.let { jobs[it.key] })
}

/**
 * The saved books, read from disk off the main thread, again whenever a
 * download job moves (a book finishing, a cancel) and on [SavedBooksView.refresh].
 */
@Composable
internal fun rememberSavedBooks(): SavedBooksView {
    val app = LocalApp.current
    val context = LocalContext.current
    val jobsFlow = remember(context) { BookDownloads.watch(context.applicationContext) }
    val jobs by jobsFlow.collectAsState(initial = emptyMap())
    var tick by remember { mutableIntStateOf(0) }
    var books by remember { mutableStateOf<List<SavedBook>>(emptyList()) }
    var used by remember { mutableStateOf(0L) }
    var sizes by remember { mutableStateOf<Map<String, Long>>(emptyMap()) }
    var free by remember { mutableStateOf(0L) }
    var loaded by remember { mutableStateOf(false) }
    // The states that change what is on disk; progress ticks don't.
    val shape = jobs.map { (k, j) -> k to j.state }.toSet()
    LaunchedEffect(tick, shape) {
        withContext(Dispatchers.IO) {
            val list = runCatching { app.books.list() }.getOrDefault(emptyList())
            books = list
            sizes = list.associate { it.key to runCatching { app.books.bytesOf(it.key) }.getOrDefault(0L) }
            used = runCatching { app.books.totalBytes() }.getOrDefault(0L)
            free = runCatching { app.books.usableBytes() }.getOrDefault(0L)
        }
        loaded = true
    }
    return SavedBooksView(books, jobs, used, sizes, free, loaded) { tick++ }
}

/** A book's cover at [size]: the picture when there is one, else the book glyph. */
@Composable
internal fun BookCover(model: String?, size: Dp, modifier: Modifier = Modifier) {
    val shape = RoundedCornerShape(8.dp)
    var failed by remember(model) { mutableStateOf(false) }
    Box(
        modifier.size(size).clip(shape)
            .background(Domovoi.colors.sunken, shape)
            .border(1.dp, Domovoi.colors.border, shape),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            Icons.AutoMirrored.Outlined.MenuBook,
            contentDescription = null,
            tint = Domovoi.colors.fgSubtle,
            modifier = Modifier.size(size * 0.46f),
        )
        if (model != null && !failed) {
            AsyncImage(
                model = model,
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier.matchParentSize(),
                onError = { failed = true },
            )
        }
    }
}

/** The save state as a pill (nothing when the book is not on the phone). */
@Composable
internal fun SaveBadge(state: SaveState, serverName: String? = null, modifier: Modifier = Modifier) {
    if (state == SaveState.None) return
    val tone = when (state) {
        SaveState.Saved -> Tone.Ok
        is SaveState.Saving -> Tone.Brand
        is SaveState.Waiting -> Tone.Idle
        is SaveState.Stopped -> Tone.Warn
        SaveState.None -> Tone.Idle
    }
    Pill(SaveStates.label(state, serverName), tone, live = state is SaveState.Saving, modifier = modifier)
}

/** The bar under a book that is saving. */
@Composable
internal fun SaveProgress(state: SaveState.Saving, modifier: Modifier = Modifier) {
    Column(modifier) {
        val pct = state.percent
        if (pct != null) {
            LinearProgressIndicator(
                progress = { pct / 100f },
                modifier = Modifier.fillMaxWidth(),
                color = Domovoi.colors.brand,
                trackColor = Domovoi.colors.sunken,
            )
        } else {
            LinearProgressIndicator(Modifier.fillMaxWidth(), color = Domovoi.colors.brand, trackColor = Domovoi.colors.sunken)
        }
        Text(
            fmtBytes(state.done) + (state.total?.let { " of " + fmtBytes(it) } ?: ""),
            style = MaterialTheme.typography.labelSmall,
            color = Domovoi.colors.fgMuted,
            modifier = Modifier.padding(top = 4.dp),
        )
    }
}

/**
 * "You were at 1:02:33", and when a room of the same person got further,
 * "continue from the kitchen (2 h 13 m)" (owner decision 2026-10-20).
 */
@Composable
internal fun BookResumeDialog(plan: StartPlan, onDismiss: () -> Unit, onPlay: (startSec: Double) -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Domovoi.colors.raised,
        title = { Text(plan.item.title, style = MaterialTheme.typography.titleMedium) },
        text = {
            Text(
                if (plan.positionSec > 5) "You were at ${fmtDur(plan.positionSec)} on this phone." else "You haven't started this on this phone.",
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgMuted,
            )
        },
        confirmButton = {
            Column(horizontalAlignment = Alignment.End) {
                plan.offer?.let { offer ->
                    TextButton(onClick = { onPlay(offer.positionSec) }) {
                        Text(ContinueRules.text(offer), color = Domovoi.colors.brand)
                    }
                }
                if (plan.positionSec > 5) {
                    TextButton(onClick = { onPlay(plan.positionSec) }) {
                        Text("resume at ${fmtDur(plan.positionSec)}", color = if (plan.offer == null) Domovoi.colors.brand else Domovoi.colors.fg)
                    }
                }
                TextButton(onClick = { onPlay(0.0) }) { Text("start over", color = Domovoi.colors.fgMuted) }
            }
        },
    )
}

/** Plays [plan] from [startSec] on this phone. */
internal fun playPlan(app: AppContainer, plan: StartPlan, startSec: Double) {
    app.player.playItems(listOf(plan.item), 0, startSec, plan.speed)
}

/** A plain yes/no for something that can't be undone. */
@Composable
internal fun ConfirmDialog(
    title: String,
    body: String,
    confirm: String,
    danger: Boolean = false,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Domovoi.colors.raised,
        title = { Text(title, style = MaterialTheme.typography.titleMedium) },
        text = { Text(body, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fgMuted) },
        confirmButton = {
            TextButton(onClick = onConfirm) {
                Text(confirm, color = if (danger) Domovoi.colors.err else Domovoi.colors.brand)
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("cancel", color = Domovoi.colors.fgMuted) } },
    )
}

/**
 * "Remove from server": admins only. Asks for the admin password when the
 * server has one ([needsPassword]); the password is used for this one
 * delete and not kept.
 */
@Composable
internal fun RemoveFromServerDialog(
    title: String,
    needsPassword: Boolean,
    busy: Boolean,
    error: String?,
    onDismiss: () -> Unit,
    onConfirm: (password: String?) -> Unit,
) {
    var password by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        containerColor = Domovoi.colors.raised,
        title = { Text("Remove \"$title\" from the server?", style = MaterialTheme.typography.titleMedium) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    "This deletes the book's files from the Domovoi server for everyone in the house. " +
                        "Copies saved on phones stay on those phones.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Domovoi.colors.fgMuted,
                )
                if (needsPassword) {
                    OutlinedTextField(
                        value = password,
                        onValueChange = { password = it },
                        label = { Text("admin password") },
                        singleLine = true,
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                        enabled = !busy,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.err) }
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onConfirm(password.takeIf { needsPassword }) },
                enabled = !busy && (!needsPassword || password.isNotEmpty()),
            ) { Text(if (busy) "removing…" else "remove", color = Domovoi.colors.err) }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, enabled = !busy) { Text("cancel", color = Domovoi.colors.fgMuted) }
        },
    )
}

/** One chapter row: number, title, where it starts. */
@Composable
internal fun ChapterRow(index: Int, title: String, startSec: Double, current: Boolean, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(6.dp))
            .background(if (current) Domovoi.colors.brandSoft else androidx.compose.ui.graphics.Color.Transparent)
            .clickable(onClick = onClick)
            .padding(horizontal = 8.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            "${index + 1}",
            style = MaterialTheme.typography.labelSmall.copy(fontFamily = com.domovoi.app.ui.theme.MonoFamily),
            color = Domovoi.colors.fgFaint,
            modifier = Modifier.padding(end = 2.dp),
        )
        Text(
            title.ifBlank { "chapter ${index + 1}" },
            style = MaterialTheme.typography.bodyMedium,
            color = Domovoi.colors.fg,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        Text(
            fmtDur(startSec),
            style = MaterialTheme.typography.labelSmall.copy(fontFamily = com.domovoi.app.ui.theme.MonoFamily),
            color = Domovoi.colors.fgMuted,
        )
    }
}

/** "your place: 1:02:33 of 10:12:00 · 10%"; null before the first listen. */
internal fun placeText(positionSec: Double?, durationSec: Double?): String? {
    val p = positionSec?.takeIf { it > 5 } ?: return null
    val d = durationSec?.takeIf { it > 0 }
    return "your place: " + fmtDur(p) + (d?.let { " of " + fmtDur(it) + " · ${((p / it) * 100).toInt().coerceIn(0, 100)}%" } ?: "")
}
