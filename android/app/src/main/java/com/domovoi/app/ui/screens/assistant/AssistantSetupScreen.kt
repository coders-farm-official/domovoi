package com.domovoi.app.ui.screens.assistant

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.selection.toggleable
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.domovoi.app.assistant.AssistantUi
import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.assistant.ModelSetup
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.DomovoiCard
import com.domovoi.app.ui.components.SectionLabel
import com.domovoi.app.ui.components.fmtBytes
import com.domovoi.app.ui.components.openWebLink
import com.domovoi.app.ui.theme.Domovoi

/**
 * The assistant's setup (AssistantSetupScreen in the plan): the one-time
 * model download, cost first. What will be downloaded, its size and
 * licence, the free space it needs; Wi-Fi only unless mobile data is
 * allowed; then the progress, with pause and cancel. The download is a
 * WorkManager job, so it carries on when the app leaves the screen, resumes
 * after a dropped connection or a restart, and the file is checked against
 * its SHA-256 before anything uses it.
 *
 * Only controls that can do what they say (owner rule): no download button
 * when there is not enough room, and nothing at all on a phone that cannot
 * run the model (AssistantScreen shows the reason instead).
 */
@Composable
internal fun AssistantSetupPanel(ui: AssistantUi, actions: AssistantActions) {
    val context = LocalContext.current
    var confirmCancel by remember { mutableStateOf(false) }
    var confirmDiscard by remember { mutableStateOf(false) }
    val setup = ui.setup
    val downloading = setup as? ModelSetup.Downloading
    val missing = setup as? ModelSetup.Missing
    val done = downloading?.bytesDone ?: missing?.bytesDone ?: 0L

    DomovoiCard(Modifier.fillMaxWidth()) {
        Text("Set up the assistant on this phone", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg,
            modifier = Modifier.asHeading())
        Text(
            "Domovoi can answer right here, with no server and no internet, once its language model is " +
                "on the phone. It is a one-time download; after that nothing you ask leaves the phone.",
            style = MaterialTheme.typography.bodyMedium,
            color = Domovoi.colors.fgMuted,
            modifier = Modifier.padding(top = 4.dp),
        )
        Spacer(Modifier.height(12.dp))
        SectionLabel("what it downloads")
        Spacer(Modifier.height(4.dp))
        ModelCatalog.chat.forEach { m ->
            Column(
                Modifier
                    .fillMaxWidth()
                    .background(Domovoi.colors.sunken, RoundedCornerShape(8.dp))
                    .padding(10.dp),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                Text(m.name, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
                Text("${m.kind.label} · ${fmtBytes(m.totalBytes)} · ${m.license}", style = MaterialTheme.typography.labelSmall,
                    color = Domovoi.colors.fgMuted)
                Text(
                    "licence",
                    style = MaterialTheme.typography.labelMedium,
                    color = Domovoi.colors.brand,
                    modifier = Modifier
                        .clickable(onClickLabel = "open in the browser", role = Role.Button) { openWebLink(context, m.licenseUrl) }
                        .semantics { contentDescription = "${m.name} licence" }
                        .padding(vertical = 2.dp),
                )
            }
        }
        Spacer(Modifier.height(8.dp))
        Text(spaceLine(ui), style = MaterialTheme.typography.bodySmall,
            color = if (ui.notEnoughSpace) Domovoi.colors.err else Domovoi.colors.fgMuted)

        if (downloading != null) {
            Spacer(Modifier.height(12.dp))
            SectionLabel("downloading")
            Spacer(Modifier.height(6.dp))
            LinearProgressIndicator(
                progress = { fraction(done, ui.totalBytes) },
                modifier = Modifier.fillMaxWidth().semantics { contentDescription = "download progress" },
                color = Domovoi.colors.brand,
                trackColor = Domovoi.colors.sunken,
            )
            Text(
                progressLine(done, ui.totalBytes),
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgMuted,
                modifier = Modifier.padding(top = 4.dp),
            )
            downloading.note?.let {
                Text(it, style = MaterialTheme.typography.bodySmall,
                    color = if (it == "starting") Domovoi.colors.fgMuted else Domovoi.colors.warn)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 6.dp)) {
                OutlinedButton(onClick = actions.pauseDownload) { Text("pause") }
                TextButton(onClick = { confirmCancel = true }) { Text("cancel", color = Domovoi.colors.fgMuted) }
            }
            Text(
                "The download carries on if you leave the app, and a notification shows how far it has got. " +
                    "When it finishes, the file is checked against its published SHA-256 before it is used.",
                style = MaterialTheme.typography.bodySmall,
                color = Domovoi.colors.fgSubtle,
                modifier = Modifier.padding(top = 6.dp),
            )
        } else if (missing != null) {
            missing.lastError?.let {
                Text("The last download failed: $it", style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.err,
                    modifier = Modifier.padding(top = 6.dp))
            }
            if (!ui.notEnoughSpace) {
                Spacer(Modifier.height(10.dp))
                Button(onClick = actions.download) { Text(downloadLabel(missing, ui.totalBytes)) }
            }
            if (missing.bytesDone > 0) {
                TextButton(onClick = { confirmDiscard = true }) {
                    Text("delete the partial download", color = Domovoi.colors.fgMuted)
                }
            }
        }

        Spacer(Modifier.height(8.dp))
        // One control for a screen reader: the row toggles, labelled by its text.
        Row(
            Modifier.toggleable(value = ui.allowMobileData, role = Role.Switch, onValueChange = actions.setAllowMobileData),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Switch(checked = ui.allowMobileData, onCheckedChange = null)
            Column(Modifier.padding(start = 10.dp)) {
                Text("allow mobile data", style = MaterialTheme.typography.labelLarge, color = Domovoi.colors.fg)
                Text(
                    if (ui.allowMobileData) "Downloads on any connection; it is a big file for a data plan."
                    else "Off: the download waits for Wi-Fi.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Domovoi.colors.fgMuted,
                )
            }
        }
    }

    if (confirmCancel) {
        ConfirmDialog(
            title = "cancel the download?",
            body = "Stops it and deletes the ${fmtBytes(done)} downloaded so far. Pause keeps it instead.",
            confirmLabel = "cancel download",
            destructive = true,
            onConfirm = actions.cancelDownload,
            onDismiss = { confirmCancel = false },
        )
    }
    if (confirmDiscard) {
        ConfirmDialog(
            title = "delete the partial download?",
            body = "Frees the ${fmtBytes(done)} downloaded so far; the next download starts from the beginning.",
            confirmLabel = "delete",
            destructive = true,
            onConfirm = actions.cancelDownload,
            onDismiss = { confirmDiscard = false },
        )
    }
}

internal fun fraction(done: Long, total: Long): Float = if (total <= 0) 0f else (done.toFloat() / total).coerceIn(0f, 1f)

internal fun progressLine(done: Long, total: Long): String =
    "${fmtBytes(done)} of ${fmtBytes(total)} · ${(fraction(done, total) * 100).toInt()}%"

internal fun downloadLabel(missing: ModelSetup.Missing, total: Long): String = when {
    missing.bytesDone > 0 -> "resume (${fmtBytes((total - missing.bytesDone).coerceAtLeast(0))} left)"
    missing.lastError != null -> "try again (${fmtBytes(total)})"
    else -> "download (${fmtBytes(total)})"
}

/** What the download needs free, against what the phone has. */
internal fun spaceLine(ui: AssistantUi): String {
    val free = ui.freeBytes ?: return "Needs ${fmtBytes(ui.bytesNeeded)} of free space."
    return if (ui.notEnoughSpace) {
        "Not enough free space: the download needs ${fmtBytes(ui.bytesNeeded)} and this phone has " +
            "${fmtBytes(free)} free. Free up ${fmtBytes(ui.bytesNeeded - free)}, then come back."
    } else {
        "Needs ${fmtBytes(ui.bytesNeeded)} of free space; this phone has ${fmtBytes(free)} free."
    }
}
