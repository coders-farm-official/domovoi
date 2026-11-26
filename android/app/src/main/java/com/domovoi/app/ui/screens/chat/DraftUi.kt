package com.domovoi.app.ui.screens.chat

import android.content.Context
import android.speech.tts.TextToSpeech
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Print
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalToast
import com.domovoi.app.net.failureText
import com.domovoi.app.ui.components.MarkdownText
import com.domovoi.app.ui.screens.documents.MarkdownToolbar
import com.domovoi.app.ui.screens.documents.applyMdTool
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.launch

/**
 * Drafts in Chat on the phone (documents plan D4), for both places: the
 * draft card on a reply and the document panel ("canvas"). What differs
 * between a phone chat's draft and a home-server chat's (where versions
 * live, where a save goes, how it prints) is a [DraftDoc].
 *
 * The canvas opens full screen in the conversation's place with the message
 * box left at the bottom, so "shorter" works while looking at the draft.
 * Its bar: the version picker (v1 · v2 · v3), the changes switch (added
 * words in the soft amber wash, removed ones struck through, never green or
 * red; on for a version this phone hasn't seen yet), edit (the Documents
 * editor's markdown toolbar; saving makes a version "edited by you"), save
 * (.docx or PDF, the kind's default first, with the ATS self-check's line),
 * share, print and read aloud.
 */

/** One version as the canvas shows it. */
internal data class DraftVersionView(
    val version: Int,
    val body: String,
    /** The plain text the changes view diffs. */
    val display: String,
    /** Against the version before: (op, text), "=" "+" "-"; null for v1. */
    val changes: List<Pair<String, String>>?,
    /** "first", "revision" or "edited". */
    val source: String,
    val instruction: String?,
    /** The files saved from it, one line each. */
    val saved: List<String> = emptyList(),
    /** A revision written from an older version than the one before it. */
    val basedOn: Int? = null,
    /** A hand edit made on another device: its name ("edited on Sam's
     *  laptop"); null: made here ("edited by you"). */
    val editedOn: String? = null,
)

internal data class DraftView(val title: String, val kind: String, val format: String, val versions: List<DraftVersionView>)

/** What a save says: where it went and the self-check's line. */
internal data class DraftSaveResult(val where: String, val check: String, val ok: Boolean)

/** One draft, wherever it lives. */
internal interface DraftDoc {
    /** Stable for the draft (the "seen" key). */
    val key: String

    suspend fun load(): DraftView

    /** A hand edit as the next version; its number, or null when a newer
     *  version came in meanwhile. */
    suspend fun edit(text: String, base: Int): Int?

    suspend fun save(version: Int, format: String): DraftSaveResult

    /** The file for sharing or a copy: its name and bytes. */
    suspend fun file(version: Int, format: String): Pair<String, ByteArray>

    /** "Send to home" (a phone chat's draft, with the home server in
     *  reach); null where it doesn't apply. */
    val sendHome: (suspend (version: Int, format: String) -> DraftSaveResult)?
        get() = null

    /** Where a save lands, in words ("Chat documents on this phone"). */
    val saveWhere: String

    /** Asked before a save, when the save shares what wasn't shared (a
     *  private home-server chat's draft going to the household's
     *  Documents); null: saved at once. */
    val saveAsk: String? get() = null

    /** Whether this phone may write to it: save, hand edits and print are
     *  writes (1.5.0: an admin reading another device's private chat may
     *  not); false hides them. */
    val writable: Boolean get() = true

    /** A version's first lines, for its card; null when not known. */
    suspend fun firstLines(version: Int): String? = null
}

/** The first [n] lines a reader sees (the card's preview). */
internal fun firstLinesOf(markdown: String, n: Int = 3): String =
    com.domovoi.app.assistant.drafts.DraftBlocks.parse(markdown).flatMap { it.lines }.take(n).joinToString("\n")

internal const val CANVAS_CHANGES = "changes"
internal const val CANVAS_EDITED = "edited by you"
internal const val CANVAS_FIRST = "first draft"

internal fun sourceLabel(v: DraftVersionView?): String = when {
    v == null -> ""
    v.source == "edited" -> v.editedOn?.let { "edited on $it" } ?: CANVAS_EDITED
    v.source == "first" -> CANVAS_FIRST
    else -> "“${v.instruction ?: "revised"}”" +
        (v.basedOn?.takeIf { it != v.version - 1 }?.let { " · based on v$it" } ?: "")
}

/** Changes are on for a version newer than the newest one seen here. */
internal fun changesOnFor(version: Int, seen: Int): Boolean = version > 1 && version > seen

internal object DraftSeen {
    private fun prefs(c: Context) = c.getSharedPreferences("draft_seen", Context.MODE_PRIVATE)
    fun get(c: Context, key: String): Int = runCatching { prefs(c).getInt(key, 0) }.getOrDefault(0)
    fun mark(c: Context, key: String, v: Int) {
        if (v > get(c, key)) runCatching { prefs(c).edit().putInt(key, v).apply() }
    }
}

/** A removed run long enough to stand as its own block: a line break in
 *  it, or [GAP_WORDS] words or more (the web's chat_drafts.jsx). */
internal const val GAP_WORDS = 8

internal fun longRun(t: String): Boolean =
    '\n' in t || t.trim().split(Regex("\\s+")).count { it.isNotEmpty() } >= GAP_WORDS

/** A paragraph gap after run [i]: a long removed run with an added run
 *  straight after it (the server's over-budget diff puts everything between
 *  the shared start and end there, old then new), unless the line already
 *  breaks at the join. Inline, the old last word ran into the new first one. */
internal fun gapAfter(ops: List<Pair<String, String>>, i: Int): Boolean {
    val (op, t) = ops[i]
    val next = ops.getOrNull(i + 1) ?: return false
    return op == "-" && next.first == "+" && longRun(t) && !t.endsWith('\n') && !next.second.startsWith('\n')
}

/** What a screen reader says for the changes: every run, the removed and
 *  added ones named as such (strike-through and the wash are not read out),
 *  and no gap ([gapAfter] is for the eye only). */
internal fun changesSpoken(ops: List<Pair<String, String>>): String = ops.mapNotNull { (op, t) ->
    val s = t.trim().takeIf { it.isNotEmpty() } ?: return@mapNotNull null
    val end = if (s.last() in ".!?,;:") "" else "."
    when (op) {
        "+" -> "added: $s$end"
        "-" -> "removed: $s$end"
        else -> s
    }
}.joinToString(" ")

/** The added and removed words, never green or red. */
@Composable
internal fun changesText(ops: List<Pair<String, String>>): AnnotatedString {
    val add = SpanStyle(background = Domovoi.colors.brandSoft, color = Domovoi.colors.fg)
    val del = SpanStyle(color = Domovoi.colors.fgSubtle, textDecoration = TextDecoration.LineThrough)
    return buildAnnotatedString {
        ops.forEachIndexed { i, (op, t) ->
            when (op) {
                "+" -> withStyle(add) { append(t) }
                "-" -> withStyle(del) { append(t) }
                else -> append(t)
            }
            if (gapAfter(ops, i)) append("\n\n")
        }
    }
}

/** What a card shows. [latest]: the draft's newest version when it is
 *  newer than the card's; [firstLines]: the version's first lines. */
internal data class DraftCardData(
    val id: String,
    val version: Int,
    val kind: String,
    val title: String,
    val action: String,
    val line: String,
    val latest: Int? = null,
    val firstLines: String? = null,
)

private fun kindLabel(kind: String) = when (kind) {
    "resume" -> "resume"
    "cover_letter" -> "cover letter"
    else -> "letter"
}

/** The draft card on the reply that wrote a version (or asked to print). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun DraftCard(
    card: DraftCardData,
    canPrint: Boolean,
    onOpen: () -> Unit,
    /** Null: no save (the chat is read only here). */
    onSave: (() -> Unit)?,
    onShare: () -> Unit,
    onPrint: () -> Unit,
    /** The Revise box: its text revises this draft, whatever it says;
     *  false when the send was refused (a reply still being written), and
     *  the box keeps its words. */
    onRevise: ((String) -> Boolean)? = null,
) {
    val isPrint = card.action == "print"
    Column(
        Modifier.widthIn(max = 480.dp).fillMaxWidth()
            .background(Domovoi.colors.card, RoundedCornerShape(10.dp))
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(10.dp))
            .padding(12.dp)
            .semantics { contentDescription = "draft: ${card.title}, version ${card.version}" },
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(if (isPrint) Icons.Outlined.Print else Icons.Outlined.Description, null, tint = Domovoi.colors.fgMuted)
            Text("${kindLabel(card.kind)} ·", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
            Text(card.title, style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.fg, maxLines = 1,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
            Text("v${card.version}", style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily),
                color = Domovoi.colors.fg,
                modifier = Modifier.border(1.dp, Domovoi.colors.borderStrong, RoundedCornerShape(999.dp))
                    .padding(horizontal = 6.dp, vertical = 1.dp))
            card.latest?.let {
                Text("latest v$it", style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily), color = Domovoi.colors.fgMuted)
            }
        }
        card.firstLines?.takeIf { it.isNotBlank() }?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted, maxLines = 3, overflow = TextOverflow.Ellipsis)
        }
        if (card.line.isNotBlank()) Text(card.line, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            if (isPrint) {
                if (canPrint) Button(onClick = onPrint) { Text("Print") }
                OutlinedButton(onClick = onOpen) { Text("open") }
            } else {
                OutlinedButton(onClick = onOpen) { Text("open") }
                onSave?.let { OutlinedButton(onClick = it) { Text("save") } }
                OutlinedButton(onClick = onShare) { Text("share") }
                if (canPrint) OutlinedButton(onClick = onPrint) { Text("print") }
            }
        }
        if (!isPrint && onRevise != null) {
            var ask by remember(card.id) { mutableStateOf("") }
            fun go() {
                val t = ask.trim()
                if (t.isNotEmpty() && onRevise(t)) ask = ""
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedTextField(
                    value = ask, onValueChange = { ask = it.take(500) }, singleLine = true,
                    placeholder = { Text("change this draft…") },
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { go() }),
                    modifier = Modifier.weight(1f).semantics { contentDescription = "revise ${card.title}" },
                )
                OutlinedButton(onClick = { go() }, enabled = ask.isNotBlank()) { Text("revise") }
            }
        }
    }
}

/**
 * The document panel for [doc]: full screen in the conversation's place
 * (the caller leaves its message box below). [reloadKey] changes when the
 * chat wrote a version of this draft: the panel reads it again and shows
 * the newest. [onPrint] prints a version (the phone's print dialog for a
 * phone draft, the home server's Print sheet for a home-server one).
 */
@Composable
internal fun DraftCanvas(
    doc: DraftDoc,
    startVersion: Int?,
    reloadKey: Int,
    onClose: () -> Unit,
    onPrint: ((Int) -> Unit)?,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var view by remember(doc.key) { mutableStateOf<DraftView?>(null) }
    var error by remember(doc.key) { mutableStateOf<String?>(null) }
    var sel by remember(doc.key) { mutableStateOf(startVersion ?: 0) }
    var showChanges by remember(doc.key) { mutableStateOf(false) }
    var editing by remember(doc.key) { mutableStateOf<TextFieldValue?>(null) }
    var busy by remember { mutableStateOf(false) }
    var saved by remember(doc.key) { mutableStateOf<DraftSaveResult?>(null) }
    var menu by remember { mutableStateOf(false) }
    var saveAsking by remember { mutableStateOf<String?>(null) }

    suspend fun load(jumpTo: Int?) {
        runCatching { doc.load() }
            .onSuccess { v ->
                view = v
                error = null
                val last = v.versions.lastOrNull()?.version ?: 1
                val pick = jumpTo?.takeIf { j -> v.versions.any { it.version == j } } ?: last
                sel = pick
                showChanges = changesOnFor(pick, DraftSeen.get(context, doc.key))
            }
            .onFailure {
                android.util.Log.w("DraftCanvas", "couldn't read ${doc.key}", it)
                error = "couldn't read the draft: ${it.message ?: it.javaClass.simpleName}"
            }
    }
    LaunchedEffect(doc.key, reloadKey) {
        editing = null
        load(if (reloadKey > 0) null else startVersion)
    }
    LaunchedEffect(doc.key, sel) { if (sel > 0) DraftSeen.mark(context, doc.key, sel) }

    // Read aloud: the phone's own text-to-speech, stopped when the panel goes.
    var tts by remember { mutableStateOf<TextToSpeech?>(null) }
    DisposableEffect(Unit) { onDispose { tts?.stop(); tts?.shutdown() } }
    fun readAloud(text: String) {
        val t = tts
        if (t != null) {
            t.speak(text, TextToSpeech.QUEUE_FLUSH, null, "draft")
            return
        }
        var made: TextToSpeech? = null
        made = TextToSpeech(context.applicationContext) { status ->
            if (status == TextToSpeech.SUCCESS) made?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "draft")
            else toast("this phone has no text-to-speech voice")
        }
        tts = made
    }

    val v = view?.versions?.firstOrNull { it.version == sel } ?: view?.versions?.lastOrNull()
    val latest = view?.versions?.lastOrNull()
    val fmt = view?.format ?: "docx"
    val other = if (fmt == "docx") "pdf" else "docx"
    fun label(f: String) = if (f == "pdf") "PDF" else ".docx"

    // "Save a copy to…": the system's file picker names the place.
    var copyOf by remember { mutableStateOf<Pair<Int, String>?>(null) }
    val saveCopy = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("*/*")) { uri ->
        val job = copyOf
        copyOf = null
        if (uri == null || job == null) return@rememberLauncherForActivityResult
        scope.launch {
            runCatching {
                val (_, bytes) = doc.file(job.first, job.second)
                context.contentResolver.openOutputStream(uri, "w")?.use { it.write(bytes) } ?: error("can't write there")
            }.onSuccess { toast("copy saved") }.onFailure { toast("couldn't save the copy: ${it.message}") }
        }
    }

    BackHandler { if (editing != null) editing = null else onClose() }

    Column(modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { if (editing != null) editing = null else onClose() }) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "back to the chat", tint = Domovoi.colors.fgMuted)
            }
            Text(view?.title ?: "draft", style = MaterialTheme.typography.titleSmall, color = Domovoi.colors.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            if (editing == null && v != null) {
                if (doc.writable) IconButton(onClick = { editing = TextFieldValue(v.body) }, enabled = !busy) {
                    Icon(Icons.Outlined.Edit, contentDescription = "edit", tint = Domovoi.colors.fgMuted)
                }
                IconButton(onClick = {
                    scope.launch {
                        runCatching { doc.file(v.version, fmt) }
                            .onSuccess { (name, bytes) -> if (!shareDraftFile(context, name, bytes)) toast("no app on this phone can share it") }
                            .onFailure { toast("couldn't share it: ${it.message}") }
                    }
                }) { Icon(Icons.Outlined.Share, contentDescription = "share", tint = Domovoi.colors.fgMuted) }
                Box {
                    IconButton(onClick = { menu = true }) {
                        Icon(Icons.Outlined.MoreVert, contentDescription = "more", tint = Domovoi.colors.fgMuted)
                    }
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }, containerColor = Domovoi.colors.raised) {
                        DropdownMenuItem(text = { Text("save a copy to…") }, onClick = {
                            menu = false
                            copyOf = v.version to fmt
                            scope.launch {
                                val name = runCatching { doc.file(v.version, fmt).first }.getOrDefault("draft.$fmt")
                                saveCopy.launch(name)
                            }
                        })
                        doc.sendHome?.let { send ->
                            DropdownMenuItem(text = { Text("send to home") }, onClick = {
                                menu = false
                                busy = true
                                scope.launch {
                                    runCatching { send(v.version, fmt) }
                                        .onSuccess { saved = it; toast("sent to your home server's Documents → From Chat") }
                                        .onFailure { toast(failureText("send to home", it)) }
                                    busy = false
                                }
                            })
                        }
                        onPrint?.let { print ->
                            DropdownMenuItem(text = { Text("print") }, onClick = { menu = false; print(v.version) })
                        }
                        DropdownMenuItem(text = { Text("read aloud") }, onClick = { menu = false; readAloud(v.display) })
                    }
                }
            }
        }
        val vs = view?.versions.orEmpty()
        if (vs.isNotEmpty()) {
            Row(
                Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                vs.forEach { x ->
                    FilterChip(
                        selected = x.version == sel,
                        onClick = {
                            editing = null
                            sel = x.version
                            showChanges = changesOnFor(x.version, DraftSeen.get(context, doc.key))
                        },
                        label = { Text("v${x.version}", fontFamily = MonoFamily) },
                    )
                }
                Text(sourceLabel(v), style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(horizontal = 4.dp))
                Text(CANVAS_CHANGES, style = MaterialTheme.typography.labelMedium,
                    color = if (v?.changes != null) Domovoi.colors.fg else Domovoi.colors.fgSubtle)
                Switch(checked = showChanges && v?.changes != null, enabled = v?.changes != null && editing == null,
                    onCheckedChange = { showChanges = it })
            }
        }
        if (editing == null && v != null && doc.writable) {
            fun saveAs(format: String) {
                busy = true
                scope.launch {
                    runCatching { doc.save(v.version, format) }
                        .onSuccess { saved = it; load(v.version) }
                        .onFailure { toast(failureText("save", it)) }
                    busy = false
                }
            }
            // A save from a private chat asks first ([DraftDoc.saveAsk]).
            fun ask(format: String) { if (doc.saveAsk != null) saveAsking = format else saveAs(format) }
            saveAsking?.let { f -> SaveAsk(doc.saveAsk, onConfirm = { saveAs(f) }, onDismiss = { saveAsking = null }) }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                Button(enabled = !busy, onClick = { ask(fmt) }) { Text("save ${label(fmt)}") }
                OutlinedButton(enabled = !busy, onClick = { ask(other) }) { Text(label(other)) }
                onPrint?.let { print ->
                    IconButton(onClick = { print(v.version) }) {
                        Icon(Icons.Outlined.Print, contentDescription = "print", tint = Domovoi.colors.fgMuted)
                    }
                }
            }
        }
        saved?.let { s ->
            Column(Modifier.padding(vertical = 4.dp)) {
                Text(s.where, style = MaterialTheme.typography.labelSmall.copy(fontFamily = MonoFamily), color = Domovoi.colors.fg)
                Text(s.check, style = MaterialTheme.typography.labelSmall, color = if (s.ok) Domovoi.colors.fgMuted else Domovoi.colors.warn)
            }
        }
        error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.err) }
        val edit = editing
        if (edit != null) {
            MarkdownToolbar { tool -> editing = applyMdTool(edit, tool) }
            TextField(
                value = edit,
                onValueChange = { editing = it },
                modifier = Modifier.fillMaxWidth().weight(1f),
                textStyle = MaterialTheme.typography.bodyMedium.copy(fontFamily = MonoFamily),
                colors = TextFieldDefaults.colors(
                    focusedContainerColor = Domovoi.colors.canvas, unfocusedContainerColor = Domovoi.colors.canvas,
                    focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent,
                    cursorColor = Domovoi.colors.brand, focusedTextColor = Domovoi.colors.fg, unfocusedTextColor = Domovoi.colors.fg,
                ),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(enabled = !busy && edit.text.isNotBlank() && latest != null, onClick = {
                    busy = true
                    scope.launch {
                        runCatching { doc.edit(edit.text, latest!!.version) }
                            .onSuccess { n ->
                                if (n == null) {
                                    toast("a newer version came in meanwhile; your text is still here")
                                    load(null)
                                } else {
                                    editing = null
                                    toast("saved as v$n · $CANVAS_EDITED")
                                    load(n)
                                }
                            }
                            .onFailure { toast(failureText("save", it)) }
                        busy = false
                    }
                }) { Text("save as v${(latest?.version ?: 0) + 1}") }
                TextButton(onClick = { editing = null }) { Text("cancel", color = Domovoi.colors.fgMuted) }
            }
        } else {
            Column(Modifier.fillMaxWidth().weight(1f).verticalScroll(rememberScrollState()).padding(vertical = 6.dp)) {
                when {
                    v == null -> Text(if (error == null) "reading the draft…" else "", style = MaterialTheme.typography.bodySmall,
                        color = Domovoi.colors.fgMuted)
                    showChanges && v.changes != null -> Text(changesText(v.changes), style = MaterialTheme.typography.bodyMedium,
                        color = Domovoi.colors.fg,
                        modifier = Modifier.semantics { contentDescription = changesSpoken(v.changes) })
                    else -> MarkdownText(v.body)
                }
                v?.saved?.takeIf { it.isNotEmpty() }?.let { files ->
                    Column(Modifier.padding(top = 12.dp)) {
                        files.forEach {
                            Text(it, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted,
                                modifier = Modifier.heightIn(min = 18.dp))
                        }
                    }
                }
            }
        }
    }
}

/** Shares a draft's file through the system's share sheet (a copy in the
 *  app's cache, handed over by the app's file provider). */
internal fun shareDraftFile(context: Context, name: String, bytes: ByteArray): Boolean = runCatching {
    val dir = java.io.File(context.cacheDir, "drafts-share").apply { mkdirs() }
    dir.listFiles()?.forEach { it.delete() }
    val f = java.io.File(dir, name)
    f.writeBytes(bytes)
    val uri = androidx.core.content.FileProvider.getUriForFile(context, "${context.packageName}.files", f)
    val mime = if (name.endsWith(".pdf")) "application/pdf"
    else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    val send = android.content.Intent(android.content.Intent.ACTION_SEND)
        .setType(mime)
        .putExtra(android.content.Intent.EXTRA_STREAM, uri)
        .addFlags(android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION)
    context.startActivity(android.content.Intent.createChooser(send, name).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
}.isSuccess

/** Under a chat attachment's print card when the home has no printer yet. */
internal const val ATTACHMENT_NO_PRINTER = "No printer is set up yet. Add one in Settings → Printers & scanners."

/**
 * The card on a reply that offers to print a file attached in the chat
 * ("print this", 1.4.1): the file's name and kind, and Print, which opens
 * the home's Print sheet for it. Nothing to open, save or share here: the
 * attachment is the chat's, not a draft. With no printer at the home, the
 * card says so instead of offering a Print that can't work; while the
 * printers are still being read ([printers] null), it offers nothing yet.
 */
@Composable
internal fun AttachmentPrintCard(
    att: PrintAttachmentRef,
    printers: com.domovoi.app.ui.screens.printing.PrintersResponse?,
    onPrint: () -> Unit,
) {
    val kind = if (att.kind == "image") "image" else "document"
    val name = att.name.ifBlank { "attachment" }
    Column(
        Modifier.widthIn(max = 480.dp).fillMaxWidth()
            .background(Domovoi.colors.card, RoundedCornerShape(10.dp))
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(10.dp))
            .padding(12.dp)
            .semantics(mergeDescendants = true) { contentDescription = "$kind to print: $name" },
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(Icons.Outlined.Print, null, tint = Domovoi.colors.fgMuted)
            Text("$kind ·", style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fgMuted)
            Text(name, style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.fg, maxLines = 1,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
        }
        when {
            printers == null -> Unit
            printers.hasPrinter -> Button(onClick = onPrint) {
                Icon(Icons.Outlined.Print, null, modifier = Modifier.padding(end = 6.dp))
                Text("Print")
            }
            else -> Text(ATTACHMENT_NO_PRINTER, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
        }
    }
}
