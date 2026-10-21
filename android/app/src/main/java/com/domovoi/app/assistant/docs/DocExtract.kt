package com.domovoi.app.assistant.docs

import kotlinx.serialization.Serializable
import java.io.File
import java.util.Locale

/**
 * A document's text, read once and cached beside it (`<id>.parts.json`):
 * the same shape the server's `domovoi/doc_extract` returns, so a reply
 * cites the same labels in both places.
 */
@Serializable
data class DocExtraction(
    val version: Int = DocExtract.FORMAT_VERSION,
    val kind: String = "",
    val pages: Int? = null,
    val parts: List<DocPart> = emptyList(),
    val scannedPages: List<Int> = emptyList(),
    val garbledPages: List<Int> = emptyList(),
    val twoColumnPages: List<Int> = emptyList(),
    val sheets: Int? = null,
    val formulasWithoutValue: Int = 0,
    val encoding: String? = null,
    val words: Int = 0,
    val chars: Int = 0,
    val notes: List<DocNote> = emptyList(),
    /** Read by the home server ("Ask the home server"), not on the phone. */
    val readByServer: Boolean = false,
    /** A limit cut the document short (the text cap, a sheet's columns or
     *  cells): only the first part of it was read. */
    val truncated: Boolean = false,
)

/** A read that ran past its time: the reading loops call [check] as they
 *  go, so the thread the read runs on (runInterruptible) stops when the
 *  timeout interrupts it, instead of reading on unseen. */
object DocCancel {
    const val TOO_LONG = "Reading that document took too long, so Domovoi stopped."

    fun check() {
        if (Thread.currentThread().isInterrupted) throw DocRefused(TOO_LONG)
    }
}

/**
 * Text out of an attached document on the phone (documents plan part 1):
 * PDF through the phone's PDF text API ([PhonePdfText]), .docx and .odt
 * through our XML reader, .xlsx through the sheet code, CSV, text and
 * markdown. The file is somebody else's bytes: the formats are taken by
 * extension AND first bytes, zips are checked ([DocZip]), XML refuses
 * DOCTYPEs and entities ([DocXml]), and nothing here opens a connection.
 * Old formats get what to do instead. Call it off the main thread (the
 * caller also puts a time limit on it).
 */
object DocExtract {
    const val FORMAT_VERSION = 1
    const val MAX_FILE_BYTES = 100L * 1024 * 1024
    const val MAX_PAGES = 5000
    /** The most text kept from one document (review round 1): past it, the
     *  rest is left out and a note says so. A few MB keeps the cache, the
     *  index and every question's search small. */
    const val MAX_TEXT_CHARS = 4_000_000
    /** A text or markdown file is read up to this many bytes. */
    const val MAX_PLAIN_BYTES = 16 * 1024 * 1024

    val KIND_BY_EXT = mapOf(
        "pdf" to "pdf", "docx" to "docx", "odt" to "odt", "xlsx" to "xlsx", "csv" to "csv", "tsv" to "csv",
        "txt" to "txt", "text" to "txt", "md" to "md", "markdown" to "md",
    )
    const val ACCEPTS = "PDF, Word (.docx), OpenDocument (.odt), Excel (.xlsx), CSV, text or markdown"

    val OLD_FORMATS = mapOf(
        "doc" to "Save it as .docx or PDF, then attach it again.",
        "rtf" to "Save it as .docx or PDF, then attach it again.",
        "wpd" to "Save it as .docx or PDF, then attach it again.",
        "xls" to "Save it as .xlsx or .csv, then attach it again.",
        "ods" to "Save it as .xlsx or .csv, then attach it again.",
        "pages" to "Export it as .docx or PDF, then attach it again.",
    )

    /** The MIME types the paperclip's picker offers. */
    val PICKER_TYPES = arrayOf(
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv", "text/comma-separated-values", "text/plain", "text/markdown", "text/x-markdown",
        "application/octet-stream",
    )

    private fun ext(name: String) = name.substringAfterLast('.', "").lowercase(Locale.ROOT)

    fun kindFor(name: String): String? = KIND_BY_EXT[ext(name)]

    fun oldFormatAdvice(name: String): String? = OLD_FORMATS[ext(name)]

    /** Why [name] can't be attached at all, before it is copied; null: try it. */
    fun refusalFor(name: String): String? = when {
        kindFor(name) != null -> null
        oldFormatAdvice(name) != null -> "Domovoi can't read that older format. ${oldFormatAdvice(name)}"
        else -> "Chat reads $ACCEPTS files."
    }

    /** Why [head] (the file's first bytes) isn't a [kind] file, or null. */
    fun sniff(head: ByteArray, kind: String): String? {
        fun startsWith(vararg b: Int) = head.size >= b.size && b.indices.all { head[it] == b[it].toByte() }
        return when (kind) {
            "pdf" -> if (String(head.copyOf(minOf(1024, head.size)), Charsets.ISO_8859_1).contains("%PDF-")) null
            else "That file isn't a PDF, though its name ends in .pdf."
            "docx", "odt", "xlsx" -> if (startsWith(0x50, 0x4B, 3, 4)) null else "That file is damaged or isn't the kind its name says."
            else -> when {
                startsWith(0xFF, 0xFE) || startsWith(0xFE, 0xFF) -> null
                head.take(8192).any { it == 0.toByte() } -> "That file isn't text, though its name says it is."
                else -> null
            }
        }
    }

    /**
     * The parts of [file] (named [name]). [pdf] reads PDF text; null on a
     * phone without the API, which makes a PDF throw [NoPdfTextApi]. Throws
     * [DocRefused] with the sentence to show.
     */
    fun extract(file: File, name: String, pdf: PdfTextReader?, singlePass: Int = DocQa.PHONE_SINGLE_PASS_CHARS): DocExtraction {
        val kind = kindFor(name) ?: throw DocRefused(refusalFor(name) ?: "Chat reads $ACCEPTS files.")
        if (file.length() > MAX_FILE_BYTES) {
            throw DocRefused("That file is too large for the chat: more than ${MAX_FILE_BYTES / (1024 * 1024)} MB.")
        }
        val head = file.inputStream().use { s -> ByteArray(8192).let { b -> val n = s.read(b); if (n <= 0) ByteArray(0) else b.copyOf(n) } }
        sniff(head, kind)?.let { throw DocRefused(it) }
        var base = DocExtraction(kind = kind)
        base = when (kind) {
            "pdf" -> {
                val reader = pdf ?: throw NoPdfTextApi()
                val r = DocPdf.parts(reader.read(file, MAX_PAGES))
                base.copy(pages = r.pages, parts = r.parts, scannedPages = r.scanned, garbledPages = r.garbled, twoColumnPages = r.twoColumn)
            }
            "docx" -> base.copy(parts = DocWord.extract(file))
            "odt" -> base.copy(parts = DocOdt.extract(file))
            "xlsx" -> DocSheets.xlsx(file).let {
                base.copy(parts = it.parts, sheets = it.sheets, formulasWithoutValue = it.formulasWithoutValue, truncated = it.truncated)
            }
            "csv" -> DocSheets.csv(file).let { base.copy(parts = it.parts, encoding = it.encoding, truncated = it.truncated) }
            else -> {
                val cut = file.length() > MAX_PLAIN_BYTES
                val bytes = file.inputStream().use { s -> s.readNBytes(MAX_PLAIN_BYTES) }
                DocPlain.extract(bytes, markdown = kind == "md").let { (p, enc) -> base.copy(parts = p, encoding = enc, truncated = cut) }
            }
        }
        return finish(base, singlePass)
    }

    /** Counts, empties dropped, the text cap, notes; refuses a document
     *  with no text. */
    fun finish(e: DocExtraction, singlePass: Int = DocQa.PHONE_SINGLE_PASS_CHARS, maxChars: Int = MAX_TEXT_CHARS): DocExtraction {
        val all = e.parts.filter { it.text.isNotBlank() }
        if (all.isEmpty()) throw DocRefused(if (e.scannedPages.isNotEmpty()) DocNotes.NO_TEXT_SCANNED else DocNotes.NO_TEXT)
        // The text cap: parts are kept whole until it; the one that crosses
        // it is cut; the rest is left out.
        val parts = ArrayList<DocPart>()
        var used = 0
        var cut = e.truncated
        for (p in all) {
            val size = p.text.length + p.head.length
            if (used + size <= maxChars) {
                parts += p
                used += size
                continue
            }
            cut = true
            val room = maxChars - used - p.head.length
            if (room > 0 && parts.isEmpty()) parts += p.copy(text = p.text.take(room))
            break
        }
        val words = DocNotes.countWords(parts)
        val chars = parts.sumOf { it.text.length + it.head.length }
        val notes = DocNotes.attachNotes(e.scannedPages, e.garbledPages, e.formulasWithoutValue, chars, words, singlePass).toMutableList()
        if (cut) {
            val pagesRead = if (e.kind == "pdf") parts.mapNotNull { it.page }.maxOrNull() else null
            notes += DocNotes.truncatedNote(pagesRead, words)
        }
        return e.copy(parts = parts, words = words, chars = chars, notes = notes, truncated = cut)
    }
}
