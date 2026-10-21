package com.domovoi.app.ui.screens.printing

import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.ui.screens.files.extOf
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Printing and scanning through the home server (web/backend/api/printers.py,
 * which proxies the core's /v1 printer routes). Every shape is tolerant:
 * missing keys default, unknown keys are ignored (DomovoiJson).
 *
 * Strings that come from a printer (model, location, status message, supply
 * names) are DATA: Compose Text renders them as plain text, never markup.
 */

@Serializable
data class PrintMedia(val id: String = "", val label: String = "")

@Serializable
data class PrintChoices(
    val sides: List<String> = emptyList(),
    val color: List<String> = emptyList(),
    val media: List<PrintMedia> = emptyList(),
    @SerialName("copies_max") val copiesMax: Int = 1,
)

/** The options a job is sent with; null = the printer's effective default. */
@Serializable
data class PrintOptions(
    val pages: String? = null,
    val copies: Int? = null,
    val sides: String? = null,
    val color: String? = null,
    val media: String? = null,
)

@Serializable
data class PrinterStatus(
    val state: String = "checking",
    val tone: String = "off",
    val words: List<String> = emptyList(),
    val message: String = "",
    @SerialName("checked_at") val checkedAt: String? = null,
)

@Serializable
data class PrinterSupply(
    val name: String = "",
    val level: Int? = null,
    val color: String? = null,
    val kind: String = "",
    val low: Boolean = false,
)

@Serializable
data class ScanCaps(
    val sources: List<String> = emptyList(),
    val color: List<String> = emptyList(),
    val resolutions: List<Int> = emptyList(),
)

@Serializable
data class PrintJob(
    val id: Long = 0,
    @SerialName("printer_id") val printerId: Long? = null,
    @SerialName("printer_name") val printerName: String = "",
    val title: String = "",
    val pages: Int = 0,
    val copies: Int = 1,
    val sheets: Int = 0,
    val options: PrintOptions? = null,
    val state: String = "queued",
    @SerialName("pages_done") val pagesDone: Int = 0,
    val progress: String = "",
    val error: String? = null,
    val who: String = "",
    val source: String = "",
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("finished_at") val finishedAt: String? = null,
    @SerialName("can_cancel") val canCancel: Boolean = false,
) {
    val finished: Boolean get() = state in JOB_END_STATES
}

internal val JOB_END_STATES = setOf("done", "failed", "canceled")

@Serializable
data class Printer(
    val id: Long = 0,
    val name: String = "",
    @SerialName("make_and_model") val makeAndModel: String = "",
    val location: String = "",
    val address: String = "",
    val uri: String = "",
    @SerialName("added_via") val addedVia: String = "",
    @SerialName("can_print") val canPrint: Boolean = false,
    @SerialName("can_scan") val canScan: Boolean = false,
    val color: Boolean = false,
    val duplex: Boolean = false,
    @SerialName("is_default") val isDefault: Boolean = false,
    val rooms: List<String> = emptyList(),
    @SerialName("two_sided_default") val twoSidedDefault: Boolean? = null,
    @SerialName("color_default") val colorDefault: Boolean? = null,
    val defaults: PrintOptions = PrintOptions(),
    val choices: PrintChoices = PrintChoices(),
    val status: PrinterStatus = PrinterStatus(),
    val supplies: List<PrinterSupply> = emptyList(),
    @SerialName("supplies_reported") val suppliesReported: Boolean = false,
    @SerialName("admin_url") val adminUrl: String? = null,
    @SerialName("needs_attention") val needsAttention: String? = null,
    val identify: Boolean = false,
    val scan: ScanCaps? = null,
    @SerialName("active_job") val activeJob: PrintJob? = null,
)

@Serializable
data class PrintRoom(val id: String = "", val label: String = "")

@Serializable
data class Printable(
    val exts: List<String> = emptyList(),
    @SerialName("pdf_first_exts") val pdfFirstExts: List<String> = emptyList(),
    @SerialName("pdf_first_message") val pdfFirstMessage: String = "",
)

@Serializable
data class DiscoveryDiagnostics(
    @SerialName("ran_at") val ranAt: String? = null,
    val found: Int = 0,
    @SerialName("by_multicast") val byMulticast: Int = 0,
    @SerialName("by_unicast") val byUnicast: Int = 0,
    @SerialName("by_sweep") val bySweep: Int = 0,
    val typed: Int = 0,
    val sweep: String = "skipped",
    @SerialName("sweep_retry_sec") val sweepRetrySec: Int = 0,
    val errors: List<String> = emptyList(),
)

@Serializable
data class PrintersResponse(
    val printers: List<Printer> = emptyList(),
    @SerialName("has_printer") val hasPrinter: Boolean = false,
    @SerialName("has_scanner") val hasScanner: Boolean = false,
    @SerialName("default_printer_id") val defaultPrinterId: Long? = null,
    val rooms: List<PrintRoom> = emptyList(),
    val printable: Printable = Printable(),
    val discovery: DiscoveryDiagnostics? = null,
)

@Serializable
data class FoundPrinter(
    val key: String = "",
    @SerialName("make_and_model") val makeAndModel: String = "",
    val location: String = "",
    val address: String = "",
    val prints: Boolean = false,
    val scans: Boolean = false,
    val color: Boolean = false,
    val duplex: Boolean = false,
    val via: String = "",
    @SerialName("already_added") val alreadyAdded: Long? = null,
)

@Serializable
data class DiscoverResponse(
    val found: List<FoundPrinter> = emptyList(),
    val diagnostics: DiscoveryDiagnostics? = null,
)

@Serializable
data class PageThumb(val page: Int = 0, val src: String = "")

@Serializable
data class PrintPreview(
    @SerialName("printer_id") val printerId: Long? = null,
    @SerialName("printer_name") val printerName: String = "",
    val title: String = "",
    val kind: String = "",
    @SerialName("pages_total") val pagesTotal: Int? = null,
    val pages: List<Int> = emptyList(),
    val copies: Int = 1,
    val sheets: Int = 0,
    val options: PrintOptions = PrintOptions(),
    val summary: String = "",
    val thumbs: List<PageThumb> = emptyList(),
    val notes: List<String> = emptyList(),
    val adjustments: List<String> = emptyList(),
    @SerialName("over_limit") val overLimit: String? = null,
    val choices: PrintChoices? = null,
)

@Serializable
data class PrintJobsResponse(val jobs: List<PrintJob> = emptyList())

@Serializable
data class ScanJob(
    val id: String = "",
    @SerialName("printer_id") val printerId: Long? = null,
    @SerialName("scanner_name") val scannerName: String = "",
    val state: String = "scanning",
    @SerialName("source_used") val sourceUsed: String? = null,
    val pages: Int = 0,
    val thumbs: List<PageThumb> = emptyList(),
    @SerialName("can_scan_more") val canScanMore: Boolean = false,
    val name: String? = null,
    @SerialName("library_id") val libraryId: String? = null,
    val path: String? = null,
    val incomplete: Boolean = false,
    val error: String? = null,
    val readable: Boolean = false,
) {
    /** The scanner is still working: the sheet polls until it isn't. */
    val busy: Boolean get() = state == "scanning" || state == "saving"
    val finished: Boolean get() = state in SCAN_END_STATES
}

internal val SCAN_END_STATES = setOf("done", "failed", "canceled")

// ---------------------------------------------------------------------------
// What a file's menu offers
// ---------------------------------------------------------------------------

/** What the file menu shows for a server file. */
sealed interface PrintOffer {
    /** A Print item. */
    data object Print : PrintOffer

    /** One line in place of Print ("save it as PDF first"). */
    data class PdfFirst(val message: String) : PrintOffer

    /** Nothing at all: no printer, a folder, or a kind that never prints. */
    data object None : PrintOffer
}

/**
 * Print only when a printer is saved AND the file's extension is printable;
 * for Word-like files, the server's "save it as PDF" line instead; otherwise
 * nothing (absent, never greyed). [printers] null = not known yet.
 */
internal fun printOffer(name: String, isDir: Boolean, printers: PrintersResponse?): PrintOffer {
    if (isDir || printers == null || !printers.hasPrinter) return PrintOffer.None
    val ext = extOf(name).takeIf { it.isNotEmpty() }?.let { ".$it" } ?: return PrintOffer.None
    val p = printers.printable
    return when {
        p.exts.any { it.equals(ext, ignoreCase = true) } -> PrintOffer.Print
        p.pdfFirstExts.any { it.equals(ext, ignoreCase = true) } ->
            PrintOffer.PdfFirst(p.pdfFirstMessage.ifBlank { "Save it as PDF first, then print the PDF." })
        else -> PrintOffer.None
    }
}

// ---------------------------------------------------------------------------
// Which controls the Print sheet shows
// ---------------------------------------------------------------------------

/** Each control is shown only when it can change something. */
data class PrintControls(
    val printer: Boolean,
    val pages: Boolean,
    val copies: Boolean,
    val sides: Boolean,
    val color: Boolean,
    val media: Boolean,
)

internal fun printControls(choices: PrintChoices?, printerCount: Int, pagesTotal: Int?): PrintControls {
    val c = choices ?: PrintChoices()
    return PrintControls(
        printer = printerCount > 1,
        pages = pagesTotal == null || pagesTotal > 1,
        copies = c.copiesMax > 1,
        sides = c.sides.size > 1,
        color = c.color.size > 1,
        media = c.media.size > 1,
    )
}

/** Which scan controls show: glass/feeder only when there is a real choice. */
data class ScanControls(
    val scanner: Boolean,
    /** "auto" first, then the scanner's own sources; empty = hidden. */
    val sources: List<String>,
    val colors: List<String>,
)

internal fun scanControls(caps: ScanCaps?, scannerCount: Int): ScanControls {
    val sources = caps?.sources.orEmpty()
    val colors = caps?.color.orEmpty()
    return ScanControls(
        scanner = scannerCount > 1,
        sources = if (sources.size > 1) listOf("auto") + sources else emptyList(),
        colors = if (colors.size > 1) colors else emptyList(),
    )
}

/** 300 dpi when the scanner offers it, else the nearest it does; null = the server's default. */
internal fun scanResolution(caps: ScanCaps?): Int? {
    val r = caps?.resolutions.orEmpty()
    if (r.isEmpty()) return null
    return r.minByOrNull { kotlin.math.abs(it - 300) }
}

// ---------------------------------------------------------------------------
// Words
// ---------------------------------------------------------------------------

internal fun sidesLabel(v: String): String = when (v) {
    "one-sided" -> "one-sided"
    "two-sided-long-edge" -> "two-sided (long edge)"
    "two-sided-short-edge" -> "two-sided (short edge)"
    else -> v
}

internal fun colorLabel(v: String): String = when (v) {
    "color" -> "colour"
    "monochrome" -> "black and white"
    "grey", "gray" -> "grey"
    else -> v
}

internal fun sourceLabel(v: String): String = when (v) {
    "auto" -> "automatic"
    "platen" -> "glass"
    "adf" -> "feeder"
    "adf_duplex" -> "feeder, both sides"
    else -> v
}

/** The household switches: null = follow the printer. */
internal fun twoSidedDefaultLabel(v: Boolean?): String = when (v) {
    null -> "follow the printer"
    true -> "two-sided"
    false -> "one-sided"
}

internal fun colorDefaultLabel(v: Boolean?): String = when (v) {
    null -> "follow the printer"
    true -> "colour"
    false -> "black and white"
}

private fun plural(n: Int, one: String, many: String = one + "s") = if (n == 1) "$n $one" else "$n $many"

/**
 * The discovery diagnostics line under "Found on your network": "last look:
 * found 2 printers — 2 by multicast, 0 by unicast, 0 by sweep; 1 added by
 * address". The errors and a rate-limited sweep are separate lines
 * ([diagnosticsNotes]).
 */
internal fun diagnosticsLine(d: DiscoveryDiagnostics): String {
    val base = "last look: found ${plural(d.found, "printer")} — " +
        "${d.byMulticast} by multicast, ${d.byUnicast} by unicast, ${d.bySweep} by sweep"
    return if (d.typed > 0) "$base; ${d.typed} added by address" else base
}

internal fun diagnosticsNotes(d: DiscoveryDiagnostics): List<String> {
    val notes = mutableListOf<String>()
    if (d.sweep == "rate_limited") {
        notes += if (d.sweepRetrySec > 0) {
            "the network sweep runs at most once a minute — try again in ${plural(d.sweepRetrySec, "second")}"
        } else {
            "the network sweep runs at most once a minute"
        }
    }
    notes += d.errors.filter { it.isNotBlank() }
    return notes
}

/** The toast when a watched job ends. */
internal fun jobEndMessage(job: PrintJob): String = when (job.state) {
    "done" -> "Printed: ${job.title.ifBlank { "your file" }}" +
        (if (job.pages > 0) " (${plural(job.pages, "page")})" else "")
    "failed" -> job.error?.takeIf { it.isNotBlank() } ?: "The print didn't finish."
    "canceled" -> "Canceled: ${job.title.ifBlank { "the print" }}"
    else -> job.progress.ifBlank { job.state }
}

/** The line a scan sheet ends on. */
internal fun scanEndMessage(scan: ScanJob): String = when (scan.state) {
    "done" -> "Saved to Documents as ${scan.name ?: scan.path?.substringAfterLast('/') ?: "a new scan"}" +
        (if (scan.incomplete) " (some pages may be missing)" else "")
    "failed" -> scan.error?.takeIf { it.isNotBlank() } ?: "The scan didn't finish; nothing was saved."
    "canceled" -> "Scan canceled; nothing was saved."
    else -> scan.state
}

/** "What does it say?" is offered only when the server can read the scan. */
internal fun offersWhatDoesItSay(scan: ScanJob): Boolean = scan.readable && scan.state == "done"

/** A supply bar's level, 0..1, or null when the printer gave none (or "unknown", -1..-3 in IPP). */
internal fun supplyFraction(s: PrinterSupply): Float? =
    s.level?.takeIf { it in 0..100 }?.let { it / 100f }

/** "#RRGGBB" (or "#RGB") → ARGB, or null; printer-supplied, so parsed defensively. */
internal fun supplyArgb(hex: String?): Long? {
    val h = hex?.trim()?.removePrefix("#") ?: return null
    val full = when {
        h.length == 3 && h.all { it.isLetterOrDigit() } -> h.map { "$it$it" }.joinToString("")
        h.length == 6 -> h
        else -> return null
    }
    val rgb = full.toLongOrNull(16) ?: return null
    return 0xFF000000L or rgb
}

/** The base64 payload of a `data:image/...;base64,` thumbnail, or null for anything else. */
internal fun dataUriBase64(src: String): String? {
    if (!src.startsWith("data:image/")) return null
    val comma = src.indexOf(',')
    if (comma < 0) return null
    if (!src.substring(0, comma).endsWith(";base64")) return null
    return src.substring(comma + 1).takeIf { it.isNotBlank() }
}

/** The server's plain-words `detail` from a failed call, when it gave one. */
internal fun serverDetail(e: Throwable): String? {
    val body = (e as? ApiException)?.body ?: return null
    return runCatching {
        ((DomovoiJson.parseToJsonElement(body) as? JsonObject)?.get("detail") as? JsonPrimitive)
            ?.takeIf { it.isString }?.content
    }.getOrNull()?.takeIf { it.isNotBlank() }
}

/** A failed printing call's toast: the server's words, else the usual text. */
internal fun printFailureText(action: String, e: Throwable): String =
    serverDetail(e) ?: com.domovoi.app.net.failureText(action, e)

/** The line for admin-only actions this app can't take. */
internal const val ADMIN_ONLY_LINE = "An admin can rename or remove printers on the web dashboard."

internal const val NETWORK_ONLY_LINE =
    "Works with network printers (AirPrint, IPP Everywhere, Mopria). USB printers aren't supported."
