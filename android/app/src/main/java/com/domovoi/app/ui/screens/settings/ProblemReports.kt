package com.domovoi.app.ui.screens.settings

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.domovoi.app.LocalToast
import com.domovoi.app.diagnostics.Diagnostics
import com.domovoi.app.diagnostics.Problem
import com.domovoi.app.diagnostics.ProblemKind
import com.domovoi.app.diagnostics.formatTime
import com.domovoi.app.diagnostics.renderReport
import com.domovoi.app.ui.components.ConfirmDialog
import com.domovoi.app.ui.components.Pill
import com.domovoi.app.ui.components.Tone
import com.domovoi.app.ui.theme.Domovoi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Settings > About > Problem reports: the crashes, "not responding" closes and
 * freezes this phone recorded (diagnostics/Diagnostics.kt), with copy and
 * share so the owner can pass them on. Kept on the phone; this screen is the
 * only way out, and only when the owner taps copy or share.
 */
@Composable
internal fun ProblemReportsCard() {
    val context = LocalContext.current
    val toast = LocalToast.current
    val clipboard = LocalClipboardManager.current
    val problems by Diagnostics.problems.collectAsState()
    var header by remember { mutableStateOf<List<String>>(emptyList()) }
    var confirmClear by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        Diagnostics.refresh()
        header = withContext(Dispatchers.IO) { Diagnostics.reportHeader(context) }
    }

    fun report(): String = renderReport(problems, header).take(MAX_SHARE_CHARS)

    PanelCard(
        "Problem reports",
        "Crashes, freezes and \"not responding\" closes on this phone. They stay on the " +
            "phone: nothing is sent anywhere unless you copy or share it.",
    ) {
        if (problems.isEmpty()) {
            Text(
                "Nothing recorded. If the app freezes or closes unexpectedly, it shows up here.",
                style = MaterialTheme.typography.bodyMedium,
                color = Domovoi.colors.fgMuted,
            )
            return@PanelCard
        }
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedButton(onClick = {
                clipboard.setText(AnnotatedString(report()))
                toast("problem report copied")
            }) { Text("copy all") }
            OutlinedButton(onClick = {
                if (!share(context, report())) toast("no app found to share with")
            }) { Text("share") }
            TextButton(onClick = { confirmClear = true }) {
                Text("clear", color = Domovoi.colors.fgMuted)
            }
        }
        Spacer(Modifier.height(10.dp))
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            problems.forEach { p ->
                ProblemItem(p) {
                    clipboard.setText(AnnotatedString(renderReport(listOf(p), header).take(MAX_SHARE_CHARS)))
                    toast("copied")
                }
            }
        }
    }

    if (confirmClear) {
        ConfirmDialog(
            title = "clear problem reports?",
            body = "Removes every report saved on this phone.",
            confirmLabel = "clear",
            destructive = true,
            onConfirm = { Diagnostics.clear() },
            onDismiss = { confirmClear = false },
        )
    }
}

@Composable
private fun ProblemItem(p: Problem, onCopy: () -> Unit) {
    var open by remember(p.kind, p.atMs) { mutableStateOf(false) }
    Column(
        Modifier
            .fillMaxWidth()
            .background(Domovoi.colors.sunken, RoundedCornerShape(8.dp))
            .padding(10.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Pill(p.kind, if (p.kind == ProblemKind.FREEZE) Tone.Warn else Tone.Err)
            Text(
                formatTime(p.atMs),
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                color = Domovoi.colors.fgFaint,
            )
        }
        Text(p.summary, style = MaterialTheme.typography.bodyMedium, color = Domovoi.colors.fg)
        p.description?.takeIf { it.isNotBlank() }?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = Domovoi.colors.fgMuted)
        }
        if (open && !p.trace.isNullOrBlank()) {
            Text(
                p.trace,
                style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                color = Domovoi.colors.fgMuted,
                softWrap = false,
                modifier = Modifier.horizontalScroll(rememberScrollState()),
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            if (!p.trace.isNullOrBlank()) {
                TextButton(onClick = { open = !open }) {
                    Text(if (open) "hide details" else "show details", color = Domovoi.colors.brand)
                }
            }
            TextButton(onClick = onCopy) { Text("copy", color = Domovoi.colors.brand) }
        }
    }
}

/** Hand the report to any app that takes text; false when none does. */
private fun share(context: Context, text: String): Boolean {
    val send = Intent(Intent.ACTION_SEND).apply {
        type = "text/plain"
        putExtra(Intent.EXTRA_SUBJECT, "domovoi app problem report")
        putExtra(Intent.EXTRA_TEXT, text)
    }
    return try {
        context.startActivity(
            Intent.createChooser(send, "share problem report").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
        true
    } catch (_: ActivityNotFoundException) {
        false
    }
}

/** Kept well under the Binder limit an Intent extra travels through. */
private const val MAX_SHARE_CHARS = 100_000
