package com.domovoi.app.ui.screens.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.domovoi.app.ui.components.parseInstant
import com.domovoi.app.ui.components.relTime
import com.domovoi.app.ui.theme.Domovoi
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale
import kotlin.math.roundToInt

/*
 * Long-press on a chat message: copy it, or open its details. Formatting is
 * pure (unit-tested on the JVM); the dialog only lays it out.
 */

/** What the details dialog knows about one message. Timings are measured on
 *  this phone while the reply streamed, so they exist only for replies
 *  that arrived with the thread open — the server does not keep them. */
internal data class MessageFacts(
    val id: Long?,
    val threadId: Long,
    val role: String,
    val content: String,
    val createdAt: String?,
    val model: String?,
    val error: String?,
    val imageNames: List<String>,
    val firstWordsMs: Long? = null,
    val totalMs: Long? = null,
)

/**
 * The small stamp under a message: "12:37" today, "yesterday 12:37",
 * "wed 12:37" within the week, "3 sep 12:37" this year, else with the year.
 * Lowercase, like the rest of the chrome.
 */
internal fun chatStamp(iso: String?, now: Instant = Instant.now(), zone: ZoneId = ZoneId.systemDefault()): String? {
    val t = parseInstant(iso) ?: return null
    val at = t.atZone(zone)
    val today = now.atZone(zone).toLocalDate()
    val days = ChronoUnit.DAYS.between(at.toLocalDate(), today)
    val time = at.format(DateTimeFormatter.ofPattern("H:mm", Locale.ENGLISH))
    val stamp = when {
        days == 0L -> time
        days == 1L -> "yesterday $time"
        days in 2..6 -> at.format(DateTimeFormatter.ofPattern("EEE", Locale.ENGLISH)) + " $time"
        at.year == today.year -> at.format(DateTimeFormatter.ofPattern("d MMM", Locale.ENGLISH)) + " $time"
        else -> at.format(DateTimeFormatter.ofPattern("d MMM yyyy", Locale.ENGLISH)) + " $time"
    }
    return stamp.lowercase(Locale.ENGLISH)
}

/** The details dialog's rows, in order; a fact the message lacks is left out. */
internal fun detailRows(f: MessageFacts, zone: ZoneId = ZoneId.systemDefault()): List<Pair<String, String>> {
    val rows = mutableListOf<Pair<String, String>>()
    rows += "from" to if (f.role == "user") "you" else "domovoi"
    parseInstant(f.createdAt)?.let { t ->
        val full = DateTimeFormatter.ofPattern("EEE d MMM yyyy, H:mm:ss", Locale.ENGLISH).format(t.atZone(zone))
        rows += "sent" to "$full (${relTime(f.createdAt)})"
    }
    if (f.role != "user") f.model?.takeIf { it.isNotBlank() }?.let { rows += "model" to it }
    if (f.firstWordsMs != null || f.totalMs != null) {
        rows += "reply time" to listOfNotNull(
            f.firstWordsMs?.let { "first words after ${secs(it)}" },
            f.totalMs?.let { "finished in ${secs(it)}" },
        ).joinToString(" · ")
    }
    rows += "length" to lengthLine(f.content)
    if (f.imageNames.isNotEmpty()) {
        val names = f.imageNames.map { it.ifBlank { "image" } }
        rows += "images" to "${names.size} · ${names.joinToString(", ")}"
    }
    f.error?.takeIf { it.isNotBlank() }?.let { rows += "error" to it }
    rows += "message" to (f.id?.let { "#$it in thread #${f.threadId}" } ?: "thread #${f.threadId} (not saved yet)")
    return rows
}

internal fun lengthLine(text: String): String {
    val words = text.split(Regex("\\s+")).count { it.isNotBlank() }
    val lines = if (text.isEmpty()) 0 else text.trimEnd().count { it == '\n' } + 1
    return "%,d %s · %,d %s · %,d %s".format(
        Locale.ENGLISH,
        text.length, if (text.length == 1) "character" else "characters",
        words, if (words == 1) "word" else "words",
        lines, if (lines == 1) "line" else "lines",
    )
}

private fun secs(ms: Long): String =
    if (ms < 10_000) "${(ms / 100.0).roundToInt() / 10.0}s" else "${(ms / 1000.0).roundToInt()}s"

@Composable
internal fun MessageDetailsDialog(facts: MessageFacts, onCopy: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Domovoi.colors.raised,
        title = {
            Text("message details", style = MaterialTheme.typography.titleMedium, color = Domovoi.colors.fg)
        },
        text = {
            Column(
                Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                detailRows(facts).forEach { (label, value) ->
                    Row(Modifier.fillMaxWidth()) {
                        Text(
                            label,
                            style = MaterialTheme.typography.labelMedium,
                            color = Domovoi.colors.fgMuted,
                            modifier = Modifier.width(88.dp).padding(top = 1.dp),
                        )
                        Text(
                            value,
                            style = MaterialTheme.typography.bodyMedium,
                            color = if (label == "error") Domovoi.colors.err else Domovoi.colors.fg,
                        )
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("close") } },
        dismissButton = { TextButton(onClick = onCopy) { Text("copy text") } },
    )
}
