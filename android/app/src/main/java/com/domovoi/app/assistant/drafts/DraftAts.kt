package com.domovoi.app.assistant.drafts

import com.domovoi.app.assistant.docs.DocWord
import com.domovoi.app.assistant.docs.DocXml
import java.io.ByteArrayInputStream
import java.text.Normalizer
import java.util.zip.ZipInputStream

/** The ATS self-check's answer: [line] is what the save line says. */
data class DraftCheck(val ok: Boolean, val line: String, val read: Int, val expected: Int)

/**
 * The ATS self-check that runs after every save (documents plan part 2), the
 * twin of the server's `domovoi/drafts/ats.py`: the saved file's text read
 * back (a PDF with the phone's own PDF text API, a .docx from its paragraph
 * XML) and compared, word by word and in order, with the draft. A match reads
 * `text readable · single column`; a mismatch says where.
 */
object DraftAts {
    const val PASSED = "text readable · single column"

    /** Said when this phone has no PDF text API to read the file back. */
    const val NO_PDF_API = "saved · this phone can't read PDF text back to check it (that needs Android 15)"

    private val WORD = DraftSpace.WORDS
    private val BULLETS = setOf("•", "-", "*", "·")

    fun words(text: String): List<String> =
        WORD.findAll(Normalizer.normalize(text, Normalizer.Form.NFKC)).map { it.value }.filter { it !in BULLETS }.toList()

    fun expectedWords(markdown: String): List<String> = words(DraftBlocks.plainText(DraftBlocks.parse(markdown)))

    fun compare(read: List<String>, want: List<String>): DraftCheck {
        if (read == want) return DraftCheck(true, PASSED, read.size, want.size)
        var i = 0
        while (i < minOf(read.size, want.size) && read[i] == want[i]) i++
        val line = if (i < want.size) {
            val got = if (i < read.size) read[i] else "nothing"
            "text check: “${want[i]}” reads as “$got” (word ${i + 1} of ${want.size})"
        } else {
            "text check: ${read.size - want.size} extra words after the end"
        }
        return DraftCheck(false, line, read.size, want.size)
    }

    /** The text of a .docx we wrote: every paragraph's text, in order. */
    fun docxText(data: ByteArray): String {
        var xml: String? = null
        ZipInputStream(ByteArrayInputStream(data)).use { z ->
            while (true) {
                val e = z.nextEntry ?: break
                if (e.name == "word/document.xml") {
                    xml = z.readBytes().toString(Charsets.UTF_8)
                    break
                }
            }
        }
        val root = DocXml.parse(xml ?: error("no document part"), "Word document")
        return root.find("p").joinToString("\n") { DocWord.paraText(it) }
    }

    fun checkDocx(data: ByteArray, markdown: String): DraftCheck = runCatching {
        compare(words(docxText(data)), expectedWords(markdown))
    }.getOrElse { DraftCheck(false, "text check: the saved file couldn't be read back", 0, expectedWords(markdown).size) }

    fun checkText(text: String, markdown: String): DraftCheck = compare(words(text), expectedWords(markdown))
}
