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
import kotlinx.serialization.Serializable

/*
 * Long-press on a chat message: copy it, or open its details. Formatting is
 * pure (unit-tested on the JVM); the dialog only lays it out.
 */

/**
 * A reply's figures as the server keeps them (`chat_messages.stats`, V017).
 * Every field is optional: older rows have none, and a failed reply has
 * only the server's own timings.
 */
@Serializable
internal data class ChatStats(
    val prompt_tokens: Long? = null,
    val output_tokens: Long? = null,
    val tokens_per_sec: Double? = null,
    val total_ms: Double? = null,
    val load_ms: Double? = null,
    val prompt_ms: Double? = null,
    val generate_ms: Double? = null,
    val first_token_ms: Double? = null,
    val wall_ms: Double? = null,
    val done_reason: String? = null,
    val context_sent: Int? = null,
    val context_in_thread: Int? = null,
    val context_limit: Int? = null,
    val num_ctx: Long? = null,
    val model_role: String? = null,
)

/** What the details dialog knows about one message. [firstWordsMs] and
 *  [totalMs] are measured on this phone while a reply streams — the
 *  fallback for a reply whose row has no server timings. */
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
    val stats: ChatStats? = null,
    /** The sending device's name; for a message sent from here, "this phone". */
    val device: String? = null,
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
    if (f.role == "user") f.device?.takeIf { it.isNotBlank() }?.let { rows += "device" to it }
    val st = f.stats
    if (f.role != "user") f.model?.takeIf { it.isNotBlank() }?.let { model ->
        val why = when (st?.model_role) {
            "chat" -> " (chat model)"
            "vision" -> " (vision model: images attached)"
            "override" -> " (chosen for this message)"
            else -> ""
        }
        rows += "model" to model + why
    }
    // Reply time: the server's own measurement when it kept one, else the
    // one this phone took while the reply streamed.
    val firstMs = st?.first_token_ms?.toLong() ?: f.firstWordsMs
    val doneMs = st?.wall_ms?.toLong() ?: f.totalMs
    if (firstMs != null || doneMs != null) {
        rows += "reply time" to listOfNotNull(
            firstMs?.let { "first words after ${secs(it)}" },
            doneMs?.let { "finished in ${secs(it)}" },
        ).joinToString(" · ")
    }
    if (st != null) {
        val load = st.load_ms?.toLong()
        val parts = listOfNotNull(
            load?.takeIf { it >= COLD_LOAD_MS }?.let { "loading the model ${secs(it)}" },
            st.prompt_ms?.let { "reading ${secs(it.toLong())}" },
            st.generate_ms?.let { "writing ${secs(it.toLong())}" },
        )
        if (parts.isNotEmpty()) rows += "time spent" to parts.joinToString(" · ")
        if (st.prompt_tokens != null || st.output_tokens != null) {
            rows += "tokens" to listOfNotNull(
                st.prompt_tokens?.let { "%,d in".format(Locale.ENGLISH, it) },
                st.output_tokens?.let { "%,d out".format(Locale.ENGLISH, it) },
            ).joinToString(" · ")
        }
        st.tokens_per_sec?.let { rows += "speed" to "%.1f tokens/s".format(Locale.ENGLISH, it) }
        st.done_reason?.let {
            rows += "stopped" to when (it) {
                "stop" -> "finished normally"
                "length" -> "cut off at the length limit"
                else -> it
            }
        }
        contextLine(st)?.let { rows += "context" to it }
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

/** Below this, "loading the model" is just Ollama's bookkeeping, not a cold start. */
private const val COLD_LOAD_MS = 250L

/** How much of the thread the model was given with this reply. */
internal fun contextLine(st: ChatStats): String? {
    val sent = st.context_sent ?: return null
    val inThread = st.context_in_thread
    val base = if (inThread != null && inThread > sent) {
        "saw the last $sent of $inThread messages; older ones were left out"
    } else {
        "saw all $sent ${if (sent == 1) "message" else "messages"} in the thread"
    }
    val window = st.num_ctx?.let { " · %,d-token window".format(Locale.ENGLISH, it) }.orEmpty()
    return base + window
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
