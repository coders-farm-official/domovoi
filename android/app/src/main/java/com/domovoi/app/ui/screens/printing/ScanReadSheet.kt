package com.domovoi.app.ui.screens.printing

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalApp
import com.domovoi.app.ui.components.MarkdownText
import com.domovoi.app.ui.components.StatusDot
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.components.runAction
import com.domovoi.app.ui.components.toneColor
import com.domovoi.app.ui.components.toneSoft
import com.domovoi.app.ui.theme.Domovoi
import com.domovoi.app.ui.theme.MonoFamily
import kotlinx.coroutines.launch

/**
 * "What does it say?" for a scan that has been read or is being read
 * ([whatDoesItSayShown]): follows the reading while it runs, then shows the
 * short summary (markdown, the chat renderer) with the server's notes, and
 * "Show the full text" on request. The dashboard's ScanReadSheet.
 *
 * The summary and the text are the server's words about a letter: plain
 * text and markdown only, and a page the picture model guessed at already
 * says so in its own text ("might say"), shown as given.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun ScanReadSheet(scan: ScanJob, onDismiss: () -> Unit) {
    val app = LocalApp.current
    val io = remember(app) { PrintIo(app.api) }
    val scope = rememberCoroutineScope()
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var view by remember(scan.id) { mutableStateOf(ScanReadView(scan.read)) }
    var loadingText by remember { mutableStateOf(false) }

    // While it is queued or running: the reading again every 1.5 s.
    val readId = view.read?.id
    val polls = view.polls
    LaunchedEffect(readId, polls) {
        val start = view.read
        if (readId == null || !polls || start == null) return@LaunchedEffect
        follow(start, keepGoing = { ScanReadView(it).polls }, fetch = { io.scanRead(readId) }) {
            view = view.withRead(it)
        }
    }
    // Once done: the summary, asked for once.
    val wantsSummary = view.wantsSummary
    LaunchedEffect(readId, wantsSummary) {
        if (readId == null || !wantsSummary) return@LaunchedEffect
        runAction { askAgainOnTimeout { io.scanReadSummary(readId) } }
            .onSuccess { view = view.withSummary(it) }
            .onFailure { view = view.withError(it, SCAN_READ_SUMMARY_FAILED) }
    }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet, containerColor = Domovoi.colors.raised) {
        Column(
            Modifier.padding(horizontal = 20.dp).padding(bottom = 28.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(WHAT_DOES_IT_SAY, style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
            scan.name?.takeIf { it.isNotBlank() }?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            }
            view.progress?.let { line ->
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatusDot(Tone.Brand, live = true)
                    Text(line, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                }
                LinearProgressIndicator(Modifier.fillMaxWidth(), color = Domovoi.colors.brand, trackColor = Domovoi.colors.sunken)
            }
            view.endedLine?.let { Notice(it, Tone.Err) }
            if (view.summingUp) {
                Text(SCAN_READ_SUMMING, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
            }
            view.markdown?.let { MarkdownText(it) }
            view.plainSummary?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg) }
            view.aiOffNote?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted) }
            view.notes.forEach { Notice(it, Tone.Idle) }
            view.fullText?.let { text ->
                Box(
                    Modifier
                        .fillMaxWidth()
                        .heightIn(max = 360.dp)
                        .border(1.dp, Domovoi.colors.border, RoundedCornerShape(6.dp))
                        .verticalScroll(rememberScrollState())
                        .padding(10.dp),
                ) {
                    SelectionContainer {
                        Text(
                            text,
                            style = MaterialTheme.typography.bodySmall.copy(fontFamily = MonoFamily),
                            color = Domovoi.colors.fg,
                        )
                    }
                }
            }
            view.error?.let { Notice(it, Tone.Err) }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (view.offersFullText) {
                    val id = readId
                    OutlinedButton(enabled = !loadingText && id != null, onClick = {
                        if (id == null) return@OutlinedButton
                        loadingText = true
                        scope.launch {
                            runAction { io.scanReadText(id) }
                                .onSuccess { view = view.withFullText(it) }
                                .onFailure { view = view.withError(it, SCAN_READ_TEXT_FAILED) }
                            loadingText = false
                        }
                    }) { Text(SHOW_FULL_TEXT) }
                }
                TextButton(onClick = onDismiss) { Text("close", color = Domovoi.colors.fgMuted) }
            }
        }
    }
}

/** One of the server's notes, or an error, in a soft box of its tone. */
@Composable
private fun Notice(text: String, tone: Tone) {
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = if (tone == Tone.Idle) Domovoi.colors.fg else toneColor(tone),
        modifier = Modifier
            .fillMaxWidth()
            .background(toneSoft(tone), RoundedCornerShape(6.dp))
            .padding(horizontal = 10.dp, vertical = 8.dp),
    )
}
