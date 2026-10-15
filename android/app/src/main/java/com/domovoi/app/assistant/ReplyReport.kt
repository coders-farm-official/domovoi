package com.domovoi.app.assistant

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.time.Instant
import java.time.temporal.ChronoUnit
import kotlin.random.Random

/**
 * A report on one of the on-phone model's replies ("report this reply",
 * long-press a reply): Google Play's AI-generated content policy asks that a
 * person can flag an offensive or harmful answer without leaving the app.
 *
 * It holds exactly what the report sheet shows, nothing else: the question
 * the reply answered, the reply, the model that wrote it and where it ran,
 * when, the app's version, and the person's own note. No audio (none is ever
 * kept), no device details, no other messages of the conversation.
 *
 * Every field has a default, so a report written by an older (or newer) app
 * still reads (android/CONVENTIONS.md: tolerate missing fields).
 */
@Serializable
data class ReplyReport(
    val id: String = "",
    /** When the report was made, epoch ms. */
    val created: Long = 0,
    /** The question the reply answered (the person's message before it);
     *  null when the reply has none before it. */
    val question: String? = null,
    /** ...asked by voice (its text is the transcript). */
    val spoken: Boolean = false,
    val answer: String = "",
    /** When the reply was written, epoch ms. */
    val answeredAt: Long = 0,
    /** The model's name, as the model settings show it. */
    val model: String = "",
    /** The model's catalog id (ModelCatalog), which pins its exact file. */
    val modelId: String = "",
    /** Where it ran: "cpu" or "gpu"; null when not recorded. */
    val backend: String? = null,
    /** The person's note; empty when they wrote none. */
    val note: String = "",
    /** The app's versionName. */
    val appVersion: String = "",
) {
    /** The reply's first line, cut short: the saved reports list's title. */
    val title: String
        get() = answer.trim().lineSequence().firstOrNull().orEmpty()
            .let { if (it.length > TITLE_CHARS) it.take(TITLE_CHARS).trimEnd() + "…" else it }
            .ifBlank { "(empty reply)" }

    /**
     * The report as plain text: what "share as text" hands to the share
     * menu, and the order the sheet shows it in. Times in UTC, so a report
     * reads the same wherever it is opened.
     */
    fun asText(): String = buildString {
        appendLine("Domovoi on this phone: a reported reply")
        appendLine()
        appendLine("Reported: ${utc(created)}")
        appendLine("Model: ${modelLine()}")
        if (appVersion.isNotBlank()) appendLine("App: $appVersion")
        appendLine()
        appendLine(if (spoken) "Question (spoken):" else "Question:")
        appendLine(question?.trim()?.ifBlank { null } ?: "(none: the reply was the first message)")
        appendLine()
        appendLine("Reply (written ${utc(answeredAt)}):")
        appendLine(answer.trim().ifBlank { "(empty reply)" })
        if (note.isNotBlank()) {
            appendLine()
            appendLine("Note:")
            appendLine(note.trim())
        }
    }.trimEnd() + "\n"

    /** "Gemma 4 E2B (instruction-tuned), gemma-4-e2b-it, on the cpu". */
    fun modelLine(): String = listOfNotNull(
        model.ifBlank { null },
        modelId.ifBlank { null },
        backend?.let { "on the $it" },
    ).joinToString(", ").ifBlank { "unknown" }

    companion object {
        const val TITLE_CHARS = 60

        /** A note is a few sentences, not a document. */
        const val NOTE_MAX_CHARS = 2_000

        /** ISO-8601, UTC, to the second ("2026-10-15T18:04:05Z"). */
        fun utc(ms: Long): String = Instant.ofEpochMilli(ms).truncatedTo(ChronoUnit.SECONDS).toString()

        /**
         * The report for the reply at [index] of [conversation], before the
         * person adds a note: null when that message is not a reply with
         * words in it (a question, or a reply that failed before any).
         * The question is the nearest message from the person before it.
         */
        fun draft(
            conversation: StoredConversation,
            index: Int,
            model: CatalogModel,
            appVersion: String,
            now: Long,
        ): ReplyReport? {
            val reply = conversation.messages.getOrNull(index) ?: return null
            if (reply.fromUser || reply.text.isBlank()) return null
            val asked = conversation.messages.subList(0, index).lastOrNull { it.fromUser }
            return ReplyReport(
                id = ReplyReportStore.newId(now),
                created = now,
                question = asked?.text,
                spoken = asked?.spoken == true,
                answer = reply.text,
                answeredAt = reply.at,
                model = model.name,
                modelId = model.id,
                backend = reply.backend,
                appVersion = appVersion,
            )
        }
    }
}

/**
 * The reports kept on this phone: one JSON file each in [dir]
 * (`filesDir/assistant-reports/`), app-private, outside every backup and
 * transfer (the app's backup rules exclude all domains), removed on
 * uninstall. Apart from the conversations (`filesDir/assistant/`), so
 * clearing conversations leaves a report the person chose to keep; the
 * reports list deletes them, one or all.
 *
 * Written like [ConversationStore]: a temporary file moved into place, and a
 * file that cannot be read is left out of the list instead of breaking it.
 */
class ReplyReportStore(val dir: File) {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true; explicitNulls = false; coerceInputValues = true }

    private fun file(id: String): File {
        require(isValidId(id)) { "not a report id: $id" }
        return File(dir, "$id.json")
    }

    /** Every readable report, newest first. */
    fun list(): List<ReplyReport> =
        dir.listFiles().orEmpty()
            .filter { it.isFile && it.name.endsWith(".json") && isValidId(it.name.removeSuffix(".json")) }
            .mapNotNull { f ->
                runCatching { json.decodeFromString(ReplyReport.serializer(), f.readText()) }.getOrNull()
                    ?.let { if (it.id == f.name.removeSuffix(".json")) it else it.copy(id = f.name.removeSuffix(".json")) }
            }
            .sortedWith(compareByDescending<ReplyReport> { it.created }.thenByDescending { it.id })

    fun save(report: ReplyReport) {
        dir.mkdirs()
        val target = file(report.id)
        val tmp = File(dir, "${report.id}.json.tmp")
        tmp.writeText(json.encodeToString(ReplyReport.serializer(), report))
        Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
    }

    fun delete(id: String): Boolean = !isValidId(id) || file(id).let { !it.exists() || it.delete() }

    /** Deletes every report (and any leftover temporary file). */
    fun clearAll(): Boolean =
        dir.listFiles().orEmpty().filter { it.isFile && (it.name.endsWith(".json") || it.name.endsWith(".json.tmp")) }
            .map { it.delete() }.all { it }

    companion object {
        private val ID = Regex("^[a-z0-9-]{1,64}$")

        fun isValidId(id: String): Boolean = ID.matches(id)

        /** `r-<ms>-<random>`: sortable by time, unique enough for one phone. */
        fun newId(now: Long): String = "r-$now-${Random.nextInt(0, 1 shl 30).toString(36)}"
    }
}

/**
 * Where a report can go from the phone, beyond being saved on it. One small
 * interface, so the owner's choice of where reports are sent (open decision
 * 2 of the assistant plan: an HTTPS endpoint on codersfarm.org, or an email
 * draft) is one more implementation listed in [ReportSinks.available], not
 * a new flow; the report sheet shows a button per sink, labelled by it.
 *
 * Until that choice is made the only sink is [ShareAsTextSink], the
 * Android share menu: nothing leaves the phone until the person picks an
 * app there and sends it themselves, and no button claims to send anything
 * (owner rule: no control that cannot do what it says).
 */
interface ReportSink {
    /** The button, lowercase chrome ("share as text"). */
    val label: String

    /** One sentence the sheet shows under the buttons: what a tap does. */
    val explains: String

    /** Hands [report] on; false when it could not (no app to take it). */
    fun deliver(report: ReplyReport): Boolean
}

/** The Android share menu, with the report as plain text ([ReplyReport.asText]).
 *  [share] opens it (subject, text) from the screen's activity: false when
 *  no app on the phone takes plain text. */
class ShareAsTextSink(private val share: (subject: String, text: String) -> Boolean) : ReportSink {
    override val label = "share as text"
    override val explains =
        "Share as text opens Android's share menu with the report as plain text, so you can send it with an " +
            "email or messaging app to whoever you choose. Nothing leaves the phone until you do."

    override fun deliver(report: ReplyReport): Boolean = share(SUBJECT, report.asText())

    companion object {
        const val SUBJECT = "Domovoi: a reported reply"
    }
}

object ReportSinks {
    /**
     * The sinks the report sheet offers, in order. The owner's destination
     * (see [ReportSink]) goes here once chosen, ahead of the share menu; it
     * must then be declared in the Play data safety form, as it sends user
     * data, and pass the assistant's no-network test (an HTTP sink lives
     * outside assistant/ or is listed there as the downloader is).
     */
    fun available(share: (subject: String, text: String) -> Boolean): List<ReportSink> = listOf(ShareAsTextSink(share))
}
