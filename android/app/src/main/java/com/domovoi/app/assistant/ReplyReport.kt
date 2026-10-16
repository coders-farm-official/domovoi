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
    /** The model's name, as the model settings show it; [CLOCK] for a
     *  reply from the phone's clock, which no model wrote. */
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
    /** The reply came from the phone's own clock, not a model. */
    val fromClock: Boolean get() = model == CLOCK && modelId.isEmpty()

    /** What [modelLine] is labelled: "source" for the clock, else "model". */
    val sourceLabel: String get() = if (fromClock) "source" else "model"

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
        appendLine("${sourceLabel.replaceFirstChar { it.uppercase() }}: ${modelLine()}")
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

    /** The report as the JSON an HTTPS destination receives: these fields
     *  and nothing else, under a kind and a version. */
    fun asJson(): String = REPORT_JSON.encodeToString(ReportEnvelope.serializer(), ReportEnvelope(report = this))

    /** "Gemma 4 E2B (instruction-tuned), gemma-4-e2b-it, on the cpu". */
    fun modelLine(): String = listOfNotNull(
        model.ifBlank { null },
        modelId.ifBlank { null },
        backend?.let { "on the $it" },
    ).joinToString(", ").ifBlank { "unknown" }

    companion object {
        const val TITLE_CHARS = 60

        /** The source of a reply the phone's clock gave (no model wrote it). */
        const val CLOCK = "the phone's clock"

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
                model = if (reply.fromClock) CLOCK else model.name,
                modelId = if (reply.fromClock) "" else model.id,
                backend = reply.backend.takeUnless { reply.fromClock },
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

/** What [ReplyReport.asJson] sends: the report, labelled. */
@Serializable
internal data class ReportEnvelope(
    val kind: String = "domovoi-reply-report",
    val version: Int = 1,
    val report: ReplyReport,
)

private val REPORT_JSON = Json { encodeDefaults = true; explicitNulls = true }

/**
 * Where a report can go from the phone, beyond being saved on it. One small
 * interface, so the owner's choice of where reports reach Coders Farm (open
 * decision 2 of the assistant plan) is the one line [REPORT_DESTINATION],
 * not a new flow; the report sheet shows a button per sink, labelled by it.
 *
 * Google Play's AI-generated content policy asks that a person can report
 * an offensive reply to the developer without leaving the app. The sinks:
 *
 *  - [HttpsReportSink]: sends the report to a Coders Farm HTTPS address,
 *    inside the app, and says "sent" only when that address said it got it.
 *    What the policy asks for, once such an address exists.
 *  - [EmailDraftSink]: opens the person's email app with the report
 *    addressed to Coders Farm; it is sent only when they press send there.
 *    Simpler (no server), but it leaves the app.
 *  - [ShareAsTextSink]: the Android share menu, to whoever the person picks.
 *    Always offered.
 *
 * [REPORT_DESTINATION] is the owner's pick (an email draft). With
 * [ReportDestination.None] the share menu is the only sink; no button
 * claims to send what it does not send (owner rule: no control that cannot
 * do what it says).
 */
interface ReportSink {
    /** The button, lowercase chrome ("share as text"). */
    val label: String

    /** One sentence the sheet shows under the buttons: what a tap does. */
    val explains: String

    /** Hands [report] on. A sink that sends does so off the main thread. */
    suspend fun deliver(report: ReplyReport): ReportOutcome
}

/** What became of a report handed to a [ReportSink]. */
enum class ReportOutcome {
    /** Another app has it (the share menu, an email draft): it goes only
     *  if the person sends it from there. */
    HANDED_ON,

    /** The Coders Farm address answered that it has it. */
    SENT,

    /** No app on the phone takes it (no share target, no email app). */
    NO_APP,

    /** The address did not take it (offline, refused, an error). */
    FAILED,
}

/** Where "report this reply" reaches Coders Farm: the owner's choice. */
sealed interface ReportDestination {
    /** Not chosen yet: the share menu only. */
    data object None : ReportDestination

    /** An email draft to [address], sent from the person's email app. */
    data class Email(val address: String) : ReportDestination

    /** A POST of the report as JSON ([ReplyReport.asJson]) to [url], https only. */
    data class Https(val url: String) : ReportDestination
}

/**
 * WHERE REPORTS GO: the owner's one line. Kamron's choice (2026-10-15): an
 * email draft to Coders Farm's address, the same one the Play listing gives
 * as its contact. The other shapes it can take:
 *   ReportDestination.Https("https://codersfarm.org/api/domovoi/reports")
 *   ReportDestination.None   (the share menu only)
 * A change is declared in Play's data safety form (android/play/DATA_SAFETY.md
 * has the answers for each) and the privacy policy (docs/PRIVACY_POLICY.md,
 * "Reports"). An address that is not a valid https URL or email address
 * offers nothing (ReplyReportTest holds this constant to that).
 */
val REPORT_DESTINATION: ReportDestination = ReportDestination.Email("codersfarm@gmail.com")

/** The Android share menu, with the report as plain text ([ReplyReport.asText]).
 *  [share] opens it (subject, text) from the screen's activity: false when
 *  no app on the phone takes plain text. */
class ShareAsTextSink(private val share: (subject: String, text: String) -> Boolean) : ReportSink {
    override val label = "share as text"
    override val explains =
        "Share as text opens Android's share menu with the report as plain text, so you can send it with an " +
            "email or messaging app to whoever you choose. Nothing leaves the phone until you do."

    override suspend fun deliver(report: ReplyReport): ReportOutcome =
        if (share(SUBJECT, report.asText())) ReportOutcome.HANDED_ON else ReportOutcome.NO_APP

    companion object {
        const val SUBJECT = "Domovoi: a reported reply"
    }
}

/** An email draft to Coders Farm with the report as its text. [compose]
 *  opens the person's email app (to, subject, body): false when there is
 *  none. The app itself sends nothing. */
class EmailDraftSink(
    val address: String,
    private val compose: (to: String, subject: String, body: String) -> Boolean,
) : ReportSink {
    override val label = "email draft to coders farm"
    override val explains =
        "Email draft opens your email app with the report addressed to Coders Farm, who make Domovoi " +
            "($address). It is sent only when you press send there."

    override suspend fun deliver(report: ReplyReport): ReportOutcome =
        if (compose(address, ShareAsTextSink.SUBJECT, report.asText())) ReportOutcome.HANDED_ON else ReportOutcome.NO_APP
}

/**
 * The report to Coders Farm over HTTPS: [ReplyReport.asJson] POSTed to
 * [url] by [upload] (net/ReportUpload.kt: the app's client without the
 * household token, no redirect followed; the assistant's own code never
 * touches the network, NoNetworkTest). [upload] answers true only for a
 * 2xx, so "sent" is said only when the address said it has the report.
 */
class HttpsReportSink(
    val url: String,
    private val upload: suspend (url: String, json: String) -> Boolean,
) : ReportSink {
    override val label = "send to coders farm"
    override val explains =
        "Send to Coders Farm sends exactly this report, and nothing else from this phone, over HTTPS to " +
            "Coders Farm, who make Domovoi, so they can look into the reply. It is sent only when you tap it."

    override suspend fun deliver(report: ReplyReport): ReportOutcome = try {
        if (upload(url, report.asJson())) ReportOutcome.SENT else ReportOutcome.FAILED
    } catch (e: kotlinx.coroutines.CancellationException) {
        throw e
    } catch (e: Exception) {
        ReportOutcome.FAILED
    }
}

object ReportSinks {
    private val EMAIL = Regex("^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\\.[A-Za-z0-9-]+)+$")

    /** https, a dotted host name, an optional port and path: no user info,
     *  no spaces. (A regex, not java.net.URI: the assistant's code names no
     *  networking class, NoNetworkTest.) */
    private val HTTPS = Regex("^https://([A-Za-z0-9-]+\\.)+[A-Za-z0-9-]+(:[0-9]{1,5})?(/[^\\s@]*)?$", RegexOption.IGNORE_CASE)

    /** [destination] can be offered as it is written. */
    fun valid(destination: ReportDestination): Boolean = when (destination) {
        ReportDestination.None -> true
        is ReportDestination.Email -> EMAIL.matches(destination.address.trim())
        is ReportDestination.Https -> HTTPS.matches(destination.url.trim())
    }

    /**
     * The sinks the report sheet offers, in order: the owner's destination
     * ([REPORT_DESTINATION]) first when it is set and valid, then the share
     * menu. A sink that sends must be declared in the Play data safety form.
     */
    fun available(
        share: (subject: String, text: String) -> Boolean,
        email: (to: String, subject: String, body: String) -> Boolean = { _, _, _ -> false },
        upload: suspend (url: String, json: String) -> Boolean = { _, _ -> false },
        destination: ReportDestination = REPORT_DESTINATION,
    ): List<ReportSink> {
        val chosen: ReportSink? = when {
            !valid(destination) -> null
            destination is ReportDestination.Email -> EmailDraftSink(destination.address.trim(), email)
            destination is ReportDestination.Https -> HttpsReportSink(destination.url.trim(), upload)
            else -> null
        }
        return listOfNotNull(chosen, ShareAsTextSink(share))
    }
}
