package com.domovoi.app.ui.screens.chat

import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.provider.OpenableColumns
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AttachFile
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.domovoi.app.assistant.docs.DocPdf
import com.domovoi.app.assistant.docs.PdfBlock
import com.domovoi.app.assistant.docs.PhonePdfText
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Documents in Chat, as both places draw them (documents plan parts 3 and
 * 10): the one-line marker an attachment leaves in the conversation, the
 * notes and sources under a document reply, and the page sheet a source
 * opens (the passage, its matched sentence marked, and for a PDF the page
 * itself with the passage boxed).
 */

/** One source under a reply, from either place. [pdf] gives the PDF the
 *  page is drawn from (null: not a PDF, or it can't be had). */
internal class SourceView(
    val label: String,
    val text: String,
    val match: String,
    val cited: Boolean,
    val page: Int?,
    val pdf: (suspend () -> File?)? = null,
)

/** Cited sources first, each group in the order the app gave them; one
 *  chip per label (two passages of one page, which overlap by a sentence,
 *  are one "p. 4": the cited one, else the best). */
internal fun orderedSources(list: List<SourceView>): List<SourceView> =
    (list.filter { it.cited } + list.filterNot { it.cited }).distinctBy { it.label }

/** Where [match] sits in [text] (the matched sentence to mark), or null. */
internal fun matchRange(text: String, match: String): IntRange? {
    if (match.isBlank()) return null
    val at = text.indexOf(match)
    return if (at < 0) null else at until at + match.length
}

/** A source chip's words: its label, and whether the reply cited it. */
internal fun sourceChipText(s: SourceView): String = if (s.cited) s.label else "${s.label} · read"

/** The name a picked document goes by (its display name, else the URI's last segment). */
internal fun pickedName(context: Context, uri: Uri): String = runCatching {
    context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
        if (c.moveToFirst()) c.getString(0) else null
    }
}.getOrNull()?.takeIf { it.isNotBlank() } ?: (uri.lastPathSegment?.substringAfterLast('/') ?: "document")

/**
 * The document picker (the system's, documents of the kinds Chat reads and
 * anything else, refused by name with what to do instead): [onPicked] gets
 * the file's name and how to read it. Kept here so the phone chat's code
 * never handles a content URI itself.
 */
@Composable
internal fun rememberDocPicker(onPicked: (String, () -> java.io.InputStream) -> Unit): () -> Unit {
    val context = androidx.compose.ui.platform.LocalContext.current
    val launcher = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.OpenDocument(),
    ) { uri ->
        if (uri != null) {
            val name = pickedName(context, uri)
            onPicked(name) { context.contentResolver.openInputStream(uri) ?: throw java.io.IOException("can't read $name") }
        }
    }
    return { launcher.launch(com.domovoi.app.assistant.docs.DocExtract.PICKER_TYPES + "*/*") }
}

/** The paperclip of a phone chat: the picker is made only where the
 *  button is shown (it needs the activity's result registry). */
@Composable
internal fun DocAttachButton(enabled: Boolean, onPicked: (String, () -> java.io.InputStream) -> Unit) {
    val pick = rememberDocPicker(onPicked)
    IconButton(onClick = pick, enabled = enabled) {
        Icon(androidx.compose.material.icons.Icons.Outlined.AttachFile, contentDescription = "attach a document", tint = Domovoi.colors.fgMuted)
    }
}

/** The marker an attachment leaves in the conversation: its summary line
 *  and its notes (scanned pages, formulas, a long document). */
@Composable
internal fun DocMarker(summary: String, notes: List<String>, modifier: Modifier = Modifier) {
    Column(
        modifier.widthIn(max = 480.dp)
            .clip(RoundedCornerShape(10.dp))
            .border(1.dp, Domovoi.colors.border, RoundedCornerShape(10.dp))
            .background(Domovoi.colors.card)
            .padding(horizontal = 10.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(Icons.Outlined.Description, contentDescription = null, tint = Domovoi.colors.fgMuted, modifier = Modifier.size(16.dp))
            Text("attached $summary", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg)
        }
        notes.forEach { Text(it, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted) }
    }
}

/** Under a document reply: its notes, then its sources as chips. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ReplyDocs(notes: List<String>, sources: List<SourceView>, onOpen: (SourceView) -> Unit) {
    if (notes.isEmpty() && sources.isEmpty()) return
    Column(Modifier.padding(top = 6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        notes.forEach { Text(it, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted) }
        if (sources.isNotEmpty()) {
            Text("sources", style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                orderedSources(sources).forEach { s ->
                    Text(
                        sourceChipText(s),
                        style = MaterialTheme.typography.labelSmall,
                        color = if (s.cited) Domovoi.colors.fg else Domovoi.colors.fgMuted,
                        modifier = Modifier
                            .clip(RoundedCornerShape(999.dp))
                            .background(if (s.cited) Domovoi.colors.brandSoft else Domovoi.colors.sunken)
                            .clickable(role = Role.Button, onClickLabel = "open ${s.label}") { onOpen(s) }
                            .semantics { contentDescription = "source ${s.label}" + if (s.cited) "" else ", read but not cited" }
                            .padding(horizontal = 10.dp, vertical = 5.dp),
                    )
                }
            }
        }
    }
}

/** The page sheet: the passage with its matched sentence marked; for a PDF,
 *  the page drawn with the passage boxed (from the text bounds, where this
 *  phone's PDF text API gives them). */
@Composable
internal fun SourceSheet(source: SourceView, onClose: () -> Unit) {
    var page by remember { mutableStateOf<Pair<Bitmap, Float>?>(null) }
    var boxes by remember { mutableStateOf<List<PdfBlock>>(emptyList()) }
    LaunchedEffect(source) {
        val index = (source.page ?: return@LaunchedEffect) - 1
        val file = source.pdf?.invoke() ?: return@LaunchedEffect
        withContext(Dispatchers.IO) {
            page = PhonePdfText.render(file, index, 1200)
            boxes = PhonePdfText.page(file, index)?.let { DocPdf.boxesFor(it, source.match) }.orEmpty()
        }
    }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = Domovoi.colors.canvas) {
            Column(Modifier.fillMaxSize().padding(16.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(source.label, style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg, modifier = Modifier.weight(1f))
                    IconButton(onClick = onClose) { Icon(Icons.Outlined.Close, contentDescription = "close", tint = Domovoi.colors.fgMuted) }
                }
                Column(Modifier.weight(1f).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    page?.let { (bmp, widthPts) -> PageImage(bmp, widthPts, boxes) }
                    val range = matchRange(source.text, source.match)
                    val shown = buildAnnotatedString {
                        if (range == null) {
                            append(source.text)
                        } else {
                            append(source.text.substring(0, range.first))
                            withStyle(SpanStyle(background = Domovoi.colors.brandSoft)) { append(source.text.substring(range)) }
                            append(source.text.substring(range.last + 1))
                        }
                    }
                    Text(shown, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                    Text(
                        if (source.cited) "the reply cited this passage" else "Domovoi read this passage; the reply didn't cite it",
                        style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgFaint,
                    )
                }
            }
        }
    }
}

@Composable
private fun PageImage(bmp: Bitmap, widthPts: Float, boxes: List<PdfBlock>) {
    val brand = Domovoi.colors.brand
    BoxWithConstraints(Modifier.fillMaxWidth().aspectRatio(bmp.width.toFloat() / bmp.height.coerceAtLeast(1))) {
        Image(bmp.asImageBitmap(), contentDescription = "the page", modifier = Modifier.fillMaxSize())
        if (boxes.isNotEmpty() && widthPts > 0) {
            Canvas(Modifier.fillMaxSize()) {
                val k = size.width / widthPts
                boxes.forEach { b ->
                    drawRect(
                        brand, topLeft = Offset(b.left * k - 3, b.top * k - 3),
                        size = Size((b.right - b.left) * k + 6, (b.bottom - b.top) * k + 6), style = Stroke(width = 2.dp.toPx()),
                    )
                }
            }
        }
    }
}

/** A line about the last attachment (why it wasn't taken), closable. */
@Composable
internal fun DocProblemNote(text: String, onClose: () -> Unit) = UploadNote(text, onClose)

/** A PDF this phone can't read the text of: what it means, and "Ask the
 *  home server" only where one is in reach and paired. */
@Composable
internal fun PendingPdfCard(name: String, message: String, canAskServer: Boolean, busy: Boolean, onAsk: () -> Unit, onDrop: () -> Unit) {
    Column(
        Modifier.fillMaxWidth().padding(top = 6.dp).clip(RoundedCornerShape(8.dp)).background(Domovoi.colors.sunken)
            .padding(10.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(message, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fg)
        if (canAskServer) {
            Text(askServerText(name), style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (canAskServer) {
                OutlinedButton(onClick = onAsk, enabled = !busy) { Text(if (busy) "reading…" else "ask the home server") }
            }
            TextButton(onClick = onDrop, enabled = !busy) { Text("remove", color = Domovoi.colors.fgMuted) }
        }
        Spacer(Modifier.height(0.dp))
    }
}

/** What "Ask the home server" does, said before it is tapped. */
internal fun askServerText(name: String) = "Sends $name to your home server to read its text. Nothing is kept there."

/** "reading lease.pdf…", above the composer while a document is read. */
@Composable
internal fun ReadingLine(name: String) {
    Box(Modifier.padding(top = 6.dp)) {
        com.domovoi.app.ui.components.Pill("reading $name…", com.domovoi.app.ui.components.Tone.Brand, live = true)
    }
}

/**
 * A long document read in parts (documents plan D2): the line ("reading
 * part 3 of 8 · about 1 min left"), a bar of the parts read, stop, and,
 * where the home server's measured speed beats this phone's, the offer
 * with what it does said first.
 */
@Composable
internal fun DocJobCard(
    line: String,
    read: Int,
    parts: Int,
    onStop: (() -> Unit)?,
    offer: String? = null,
    offerNote: String? = null,
    onOffer: (() -> Unit)? = null,
) {
    Column(
        Modifier.fillMaxWidth().padding(top = 6.dp).clip(RoundedCornerShape(8.dp)).background(Domovoi.colors.sunken)
            .padding(10.dp)
            .semantics { liveRegion = androidx.compose.ui.semantics.LiveRegionMode.Polite },
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(line, style = MaterialTheme.typography.labelMedium, color = Domovoi.colors.fg)
        androidx.compose.material3.LinearProgressIndicator(
            progress = { if (parts > 0) read.toFloat() / parts else 0f },
            modifier = Modifier.fillMaxWidth(),
            color = Domovoi.colors.brand,
            trackColor = Domovoi.colors.border,
        )
        if (offer != null && onOffer != null) {
            offerNote?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = Domovoi.colors.fgMuted) }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (onStop != null) {
                TextButton(onClick = onStop) { Text("stop", color = Domovoi.colors.brand) }
            }
            if (offer != null && onOffer != null) {
                OutlinedButton(onClick = onOffer) { Text(offer) }
            }
        }
    }
}

/** "carry on" under a stopped summary: [left] parts still to read. */
@Composable
internal fun CarryOnButton(left: Int, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, modifier = Modifier.padding(top = 6.dp)) {
        Text(carryOnText(left))
    }
}

internal fun carryOnText(left: Int) = "carry on ($left part${if (left == 1) "" else "s"} left)"
