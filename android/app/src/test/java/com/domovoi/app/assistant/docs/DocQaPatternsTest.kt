package com.domovoi.app.assistant.docs

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The three citation patterns that used `(?U)` (which Android's ICU regex
 * rejects) now spell out Python's Unicode \b \w \d \s. They must still match
 * what they did: Unicode letters count as word characters (é, ñ, ü), any
 * decimal digit counts as a digit, and Unicode spaces count as spaces.
 */
class DocQaPatternsTest {
    private fun page(s: String): String? = DocQa.PAGE_RE.find(s)?.groupValues?.get(1)
    private fun paren(s: String): String? = DocQa.PAREN_PAGE_RE.find(s)?.groupValues?.get(1)
    private fun cite(s: String): String? = DocQa.CITATION_WORDS.find(s)?.value

    @Test
    fun pageNumbers() {
        assertEquals("7", page("page 7"))
        assertEquals("12", page("see pg. 12 for más"))
        assertEquals("3", page("p3"))
        assertEquals("40", page("pp 40"))
        assertEquals("2", page("pages 2"))
        assertEquals("5", page("page 5")) // no-break space
        assertEquals("5", page("page　5")) // ideographic space
        assertEquals("٣", page("page ٣")) // Arabic-Indic digit
        assertEquals("７", page("page ７")) // full-width digit
    }

    @Test
    fun pageNeedsWordBoundariesThatCountAccentedLetters() {
        assertNull(page("épage 7")) // é is a letter: no boundary before "page"
        assertNull(page("page 7ñ")) // ñ is a letter: no boundary after "7"
        assertNull(page("über7"))
        assertEquals("7", page("é page 7 ü"))
        assertNull(page("pagination"))
    }

    @Test
    fun normLabelFoldsPageForms() {
        assertEquals("p. 7", DocQa.normLabel("Page 7"))
        assertEquals("p. 12", DocQa.normLabel("pg. 12"))
        assertEquals("p. 3", DocQa.normLabel("p3"))
        assertEquals("p. ٣", DocQa.normLabel("page ٣"))
        assertEquals("añejo p. 4", DocQa.normLabel("añejo page 4"))
        assertEquals("épage 4", DocQa.normLabel("épage 4"))
    }

    @Test
    fun parenthesisedPages() {
        assertEquals("p. 4", paren("as said (p. 4)."))
        assertEquals("PAGE 12", paren("(PAGE 12)"))
        assertEquals("pg ٥", paren("(pg ٥)"))
        assertEquals("pp9", paren("(pp9)"))
        assertNull(paren("(pages 4)"))
        assertNull(paren("(page four)"))
    }

    @Test
    fun citationWords() {
        assertEquals("résumé.pdf", cite("from résumé.pdf"))
        assertEquals("naïve.txt", cite("naïve.txt"))
        assertEquals("año_2024.xlsx", cite("año_2024.xlsx, row 3"))
        assertEquals("über.docx", cite("über.docx"))
        assertEquals("notes٣.md", cite("notes٣.md"))
        assertEquals("p. 7", cite("p. 7"))
        assertEquals("section", cite("the section on ü"))
        assertEquals("footnotes", cite("footnotes"))
        assertTrue(DocQa.CITATION_WORDS.containsMatchIn("sheet"))
        assertFalse(DocQa.CITATION_WORDS.containsMatchIn("subsection"))
        assertFalse(DocQa.CITATION_WORDS.containsMatchIn("ésection"))
        assertFalse(DocQa.CITATION_WORDS.containsMatchIn("sectionñ"))
        assertFalse(DocQa.CITATION_WORDS.containsMatchIn("file.pdfé"))
    }
}
