package com.domovoi.app.ui.screens.printing

import com.domovoi.app.net.encodePathSegment
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * "What does it say?" for a scan (the server's scan reader, 1.4.0): a scan
 * saved to Documents/Scans is read in the background, a page at a time, and
 * then answers with a short summary and, on request, its full text. The
 * shapes, gate, words and flow follow the dashboard (web/static/printers.jsx:
 * scanReadShown, ScanReadButton, ScanReadSheet) and docs/API_REFERENCE.md.
 *
 * Everything here is pure, so the JVM tests pin it; ScanReadSheet draws it.
 */

/** A scan's reading: its state and progress. `reading` = queued or running. */
@Serializable
data class ScanRead(
    val id: String = "",
    val path: String = "",
    val state: String = "queued",
    val pages: Int = 0,
    @SerialName("pages_done") val pagesDone: Int = 0,
    @SerialName("pages_guessed") val pagesGuessed: Int = 0,
    @SerialName("pages_unread") val pagesUnread: Int = 0,
    @SerialName("waiting_for_voice") val waitingForVoice: Boolean = false,
    val readable: Boolean = false,
    val reading: Boolean = false,
    val error: String? = null,
    @SerialName("error_code") val errorCode: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("finished_at") val finishedAt: String? = null,
)

/** `GET /api/scan/reads?path=`: the newest reading of a scan (null: never read). */
@Serializable
data class ScanReadLookup(
    val read: ScanRead? = null,
    @SerialName("can_read") val canRead: Boolean = false,
)

@Serializable
data class ScanReadNote(val code: String = "", val text: String = "")

/** `GET /api/scan/reads/{id}/summary`: [content] is markdown (null with AI
 *  features off, or when the model failed: then [said] is shown). */
@Serializable
data class ScanReadSummary(
    val read: ScanRead? = null,
    val ai: Boolean = false,
    val content: String? = null,
    val said: String = "",
    val notes: List<ScanReadNote> = emptyList(),
    val failed: Boolean = false,
)

@Serializable
data class ScanReadPage(
    val page: Int = 0,
    val method: String = "",
    val confidence: Double? = null,
    val text: String = "",
)

/** `GET /api/scan/reads/{id}/text`: the whole text, page by page. */
@Serializable
data class ScanReadText(
    val read: ScanRead? = null,
    val pages: List<ScanReadPage> = emptyList(),
    val text: String = "",
)

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

/** How often a reading (or a saved scan that is being read) is asked again: the dashboard's pace. */
internal const val SCAN_READ_POLL_MS = 1500L

/** The newest reading of a scan by its Documents path ("Scans/Scan 2026-10-21 15.40.pdf"):
 *  one query value, so the slash and the spaces are escaped (%2F, %20). */
internal fun scanReadLookupPath(documentsPath: String): String =
    "/api/scan/reads?path=${encodePathSegment(documentsPath)}"

/** `POST` here with [scanReadStartBody] reads a scan (or answers with the reading already under way). */
internal const val SCAN_READ_START_PATH = "/api/scan/reads"

internal fun scanReadStartBody(documentsPath: String): JsonObject = buildJsonObject { put("path", documentsPath) }

internal fun scanReadPath(id: String): String = "/api/scan/reads/${encodePathSegment(id)}"
internal fun scanReadSummaryPath(id: String): String = scanReadPath(id) + "/summary"
internal fun scanReadTextPath(id: String): String = scanReadPath(id) + "/text"

// ---------------------------------------------------------------------------
// The button: when it shows, and what it says
// ---------------------------------------------------------------------------

/**
 * "What does it say?" shows for a scan that has been read and found words
 * (`readable`) or is being read (`read.reading`, with its progress). Never
 * for a scan that was never read (`read` null: an old scan, or a server that
 * can't read scans), nor for a failed or wordless read: no misleading
 * controls. The dashboard's scanReadShown, one to one.
 */
internal fun whatDoesItSayShown(scan: ScanJob): Boolean = scan.readable || scan.read?.reading == true

/** The progress words: "reading page 2 of 3", "waiting to be read", "paused while someone talks to Domovoi". */
internal fun scanReadProgress(read: ScanRead?): String = when {
    read == null -> ""
    read.waitingForVoice -> "paused while someone talks to Domovoi"
    read.state == "queued" -> "waiting to be read"
    read.pages > 0 -> "reading page ${minOf(read.pagesDone + 1, read.pages)} of ${read.pages}"
    else -> "reading…"
}

internal const val WHAT_DOES_IT_SAY = "What does it say?"

/** The button's label: with the progress in brackets while the scan is still being read. */
internal fun whatDoesItSayLabel(scan: ScanJob): String =
    if (scan.readable) WHAT_DOES_IT_SAY else "$WHAT_DOES_IT_SAY (${scanReadProgress(scan.read)})"

// ---------------------------------------------------------------------------
// Following: the scan sheet's ScanRow, and the reading itself
// ---------------------------------------------------------------------------

/** The scan sheet asks for the scan again while the scanner works, and,
 *  once saved, while the saved scan is being read (so the button's progress moves). */
internal fun scanSheetPolls(scan: ScanJob?): Boolean =
    scan != null && (scan.busy || (scan.state == "done" && scan.read?.reading == true))

/**
 * Ask for [start] again with [fetch] after each [pause] while [keepGoing]
 * says so, handing each answer to [onEach]; a failed ask is skipped (the
 * next one tries again), so a dropped connection never stops the following.
 * Returns the last answer.
 */
internal suspend fun <T> follow(
    start: T,
    keepGoing: (T) -> Boolean,
    fetch: suspend () -> T,
    pause: suspend () -> Unit = { delay(SCAN_READ_POLL_MS) },
    onEach: (T) -> Unit,
): T {
    var cur = start
    while (keepGoing(cur)) {
        pause()
        cur = try {
            fetch()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            continue
        }
        onEach(cur)
    }
    return cur
}

// ---------------------------------------------------------------------------
// The sheet: progress, then the summary, then the full text on request
// ---------------------------------------------------------------------------

internal const val SCAN_READ_FAILED = "This scan couldn't be read."
internal const val SCAN_READ_STOPPED = "This scan isn't being read any more."
internal const val SCAN_READ_SUMMING = "summing it up…"
internal const val SCAN_READ_AI_OFF =
    "AI features are off, so there's no summary: this is the start of the text."
internal const val SCAN_READ_SUMMARY_FAILED = "Domovoi couldn't sum it up just now."
internal const val SCAN_READ_TEXT_FAILED = "The text couldn't be loaded."
internal const val SHOW_FULL_TEXT = "Show the full text"

/**
 * What the "What does it say?" sheet holds: the reading (followed while it
 * runs), the summary once it is done, the full text when asked for, and the
 * last error in the server's own words.
 */
internal data class ScanReadView(
    val read: ScanRead?,
    val summary: ScanReadSummary? = null,
    val fullText: String? = null,
    val error: String? = null,
) {
    val done: Boolean get() = read?.state == "done"
    private val ended: Boolean get() = read?.state == "failed" || read?.state == "cancelled"

    /** Ask for the reading again (every [SCAN_READ_POLL_MS]) while it is queued or running. */
    val polls: Boolean get() = read != null && read.id.isNotEmpty() && !done && !ended

    /** The progress line while reading; null otherwise. */
    val progress: String? get() = if (polls) scanReadProgress(read) else null

    /** Ask for the summary once: the read is done and nothing is in yet. */
    val wantsSummary: Boolean get() = done && summary == null && error == null

    val summingUp: Boolean get() = wantsSummary

    /** The line for a reading that ended without words to show. */
    val endedLine: String? get() = when (read?.state) {
        "failed" -> read.error?.takeIf { it.isNotBlank() } ?: SCAN_READ_FAILED
        "cancelled" -> SCAN_READ_STOPPED
        else -> null
    }

    /** The summary as markdown (the chat renderer), or null: then [plainSummary]. */
    val markdown: String? get() = summary?.content?.takeIf { it.isNotBlank() }

    /** The spoken words, shown as plain text when there is no markdown summary. */
    val plainSummary: String? get() = summary?.takeIf { markdown == null }?.said?.takeIf { it.isNotBlank() }

    /** Said under the start of the text when AI features are off (not when the model failed). */
    val aiOffNote: String? get() =
        summary?.takeIf { !it.ai && !it.failed && it.content == null }?.let { SCAN_READ_AI_OFF }

    val notes: List<String> get() = summary?.notes.orEmpty().map { it.text }.filter { it.isNotBlank() }

    val offersFullText: Boolean get() = done && fullText == null

    fun withRead(r: ScanRead) = copy(read = r)
    fun withSummary(s: ScanReadSummary) = copy(summary = s, error = null)
    fun withFullText(t: ScanReadText) = copy(fullText = t.text, error = null)
    fun withError(e: Throwable, fallback: String) = copy(error = serverDetail(e) ?: fallback)
}

/**
 * The first summary can take the chat model longer than the phone waits
 * for one answer (the dashboard's call waits up to three minutes). The
 * server keeps writing it and keeps it once made, so a timed-out ask is
 * simply asked again, up to [times] in all; any other failure is the
 * answer.
 */
internal suspend fun <T> askAgainOnTimeout(times: Int = 3, block: suspend () -> T): T {
    var left = times
    while (true) {
        try {
            return block()
        } catch (e: java.net.SocketTimeoutException) {
            if (--left <= 0) throw e
        }
    }
}

/** A scan row in the job log, as on the dashboard: "scan · done · 2 pages · Scan 2026-10-21 15.40 · Alex". */
internal fun scanLogLine(scan: ScanJob): String =
    listOf(
        "scan",
        scan.state,
        if (scan.pages == 1) "1 page" else "${scan.pages} pages",
        scan.name.orEmpty(),
        scan.who,
    ).filter { it.isNotBlank() }.joinToString(" · ")
