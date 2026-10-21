package com.domovoi.app.ui.screens.assistant

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.domovoi.app.assistant.ReplyReport
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.EmptyState
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import java.time.Instant

/** The report's note field. A local so the JVM tests, which have no Android
 *  resources for Material's text field, can put a plain one in. */
internal val LocalNoteField = staticCompositionLocalOf<@Composable (String, (String) -> Unit, Modifier) -> Unit> {
    { value, onChange, modifier ->
        OutlinedTextField(
            value = value,
            onValueChange = onChange,
            placeholder = { Text("what is wrong with it? (optional)") },
            modifier = modifier,
            minLines = 2,
            maxLines = 6,
        )
    }
}

/** What the report says when the reply has no question before it. */
internal const val NO_QUESTION = "(none: the reply was the first message)"

/**
 * "Report this reply" (long-press a reply): exactly what the report holds,
 * a note, and what can be done with it. Save keeps it on this phone (the
 * reports list under model settings); each [com.domovoi.app.assistant.ReportSink]
 * adds a button (today only the share menu, "share as text"). Nothing is
 * sent anywhere by itself, and no button says it sends.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ReportPane(draft: ReplyReport, actions: AssistantActions, canSave: Boolean = true, onClose: () -> Unit) {
    BackHandler(onBack = onClose)
    var note by rememberSaveable(draft.id) { mutableStateOf(draft.note) }
    val report = draft.copy(note = note.take(ReplyReport.NOTE_MAX_CHARS))
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        PaneTitle("Report this reply", onClose)
        Text(
            "If this answer is offensive, harmful or wrong, report it. The report holds exactly what is below " +
                "and nothing else; nothing leaves the phone by itself.",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgMuted,
        )
        ReportField(if (draft.spoken) "your question (spoken)" else "your question", draft.question?.ifBlank { null } ?: NO_QUESTION)
        ReportField("the reply", draft.answer)
        ReportField(draft.sourceLabel, draft.modelLine())
        ReportField("written", "${relTime(Instant.ofEpochMilli(draft.answeredAt).toString())} (${ReplyReport.utc(draft.answeredAt)})")
        if (draft.appVersion.isNotBlank()) ReportField("app", draft.appVersion)
        SectionLabel("your note")
        LocalNoteField.current(note, { note = it.take(ReplyReport.NOTE_MAX_CHARS) }, Modifier.fillMaxWidth())
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            // Saved reports are listed under the phone model's settings, so a
            // home-server reply is only handed on (canSave false).
            if (canSave) {
                Button(onClick = {
                    actions.saveReport(report)
                    onClose()
                }) { Text("save on this phone") }
            }
            actions.reportSinks.forEach { sink ->
                OutlinedButton(onClick = {
                    actions.deliverReport(sink, report)
                    onClose()
                }) { Text(sink.label) }
            }
        }
        if (canSave) {
            Text(
                "Save keeps the report on this phone, outside backups, under model settings → reports, until you delete it.",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgSubtle,
            )
        }
        actions.reportSinks.forEach { sink ->
            Text(sink.explains, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgSubtle)
        }
    }
}

/** One part of the report: its label and its words, as they will be kept. */
@Composable
private fun ReportField(label: String, text: String) {
    Column(Modifier.fillMaxWidth()) {
        SectionLabel(label)
        Text(
            text,
            style = MaterialTheme.typography.bodyMedium,
            color = Domovoi.colors.fg,
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 2.dp)
                .background(Domovoi.colors.sunken, RoundedCornerShape(8.dp))
                .padding(10.dp),
        )
    }
}

/** The reports saved on this phone: share one on, delete one, or all. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ReportsPane(reports: List<ReplyReport>, actions: AssistantActions, onClose: () -> Unit) {
    BackHandler(onBack = onClose)
    var confirmDelete by remember { mutableStateOf<ReplyReport?>(null) }
    var confirmClear by remember { mutableStateOf(false) }
    // A few reports at most: a plain scrolling column, not a lazy list.
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        PaneTitle("Reports on this phone", onClose)
        Text(
            "Replies you reported, kept only on this phone and outside backups. They go nowhere unless you share one.",
            style = MaterialTheme.typography.bodySmall,
            color = Domovoi.colors.fgMuted,
        )
        if (reports.isEmpty()) {
            EmptyState("No reports", "To report a reply, long-press it and choose report this reply.")
        }
        reports.forEach { r ->
            DomovoiCard(Modifier.fillMaxWidth()) {
                Text(r.title, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                Text(
                    "reported ${relTime(Instant.ofEpochMilli(r.created).toString())}" +
                        (r.question?.ifBlank { null }?.let { " · asked: ${it.trim().take(60)}" } ?: ""),
                    style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted,
                )
                if (r.note.isNotBlank()) {
                    Text("note: ${r.note}", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted,
                        modifier = Modifier.padding(top = 2.dp))
                }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    actions.reportSinks.forEach { sink ->
                        TextButton(
                            onClick = { actions.deliverReport(sink, r) },
                            modifier = Modifier.semantics { contentDescription = "${sink.label}: ${r.title}" },
                        ) { Text(sink.label, color = Domovoi.colors.brand) }
                    }
                    TextButton(
                        onClick = { confirmDelete = r },
                        modifier = Modifier.semantics { contentDescription = "delete report: ${r.title}" },
                    ) { Text("delete", color = Domovoi.colors.err) }
                }
            }
        }
        if (reports.isNotEmpty()) {
            TextButton(onClick = { confirmClear = true }) { Text("delete all reports", color = Domovoi.colors.err) }
        }
    }
    confirmDelete?.let { r ->
        ConfirmDialog(
            title = "delete this report?",
            body = "\"${r.title}\" is deleted from this phone. The conversation it came from stays.",
            confirmLabel = "delete",
            destructive = true,
            onConfirm = { actions.deleteReport(r.id) },
            onDismiss = { confirmDelete = null },
        )
    }
    if (confirmClear) {
        ConfirmDialog(
            title = "delete all reports?",
            body = "Deletes every report kept on this phone (${reports.size}). Your conversations stay.",
            confirmLabel = "delete all",
            destructive = true,
            onConfirm = actions.clearReports,
            onDismiss = { confirmClear = false },
        )
    }
}

/** A title the screen reader announces as a heading. */
internal fun Modifier.asHeading(): Modifier = semantics { heading() }
