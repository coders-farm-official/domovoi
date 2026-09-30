package com.domovoi.app.ui.components

import org.junit.Assert.assertEquals
import org.junit.Test

class MarkdownTest {

    @Test fun blocksFromATypicalReply() {
        val md = """
            # Raw chicken

            Short answer: **no**.

            1. **Food safety:** bacteria
            2. **Bones:** splinter
            * Cooked chicken breast
              - no skin
            > ask your vet

            ---
            ```
            val x = 1
            ```
        """.trimIndent()
        assertEquals(
            listOf(
                MdBlock.Heading(1, "Raw chicken"),
                MdBlock.Paragraph("Short answer: **no**."),
                MdBlock.ListItem("1.", "**Food safety:** bacteria", 0),
                MdBlock.ListItem("2.", "**Bones:** splinter", 0),
                MdBlock.ListItem("•", "Cooked chicken breast", 0),
                MdBlock.ListItem("•", "no skin", 1),
                MdBlock.Quote("ask your vet"),
                MdBlock.Rule,
                MdBlock.Code("val x = 1"),
            ),
            parseMarkdown(md),
        )
    }

    @Test fun anUnclosedFenceMidStreamRunsToTheEnd() {
        assertEquals(
            listOf(MdBlock.Paragraph("here:"), MdBlock.Code("line one\nline two")),
            parseMarkdown("here:\n```kotlin\nline one\nline two"),
        )
    }

    @Test fun inlineEmphasisCodeAndLinks() {
        assertEquals(
            listOf(
                MdSpan("a "), MdSpan("bold", bold = true), MdSpan(" and "),
                MdSpan("it", italic = true), MdSpan(" "), MdSpan("x()", code = true),
            ),
            parseInline("a **bold** and *it* `x()`"),
        )
        assertEquals(
            listOf(MdSpan("see "), MdSpan("docs", link = "https://example.org/d")),
            parseInline("see [docs](https://example.org/d)"),
        )
        // Only web links become clickable.
        assertEquals(listOf(MdSpan("x", link = null)), parseInline("[x](javascript:alert(1))").take(1))
    }

    @Test fun halfWrittenAndLiteralMarkersStayLiteral() {
        assertEquals(listOf(MdSpan("so **far")), parseInline("so **far"))
        assertEquals(listOf(MdSpan("snake_case_name")), parseInline("snake_case_name"))
        assertEquals(listOf(MdSpan("2 * 3 * 4")), parseInline("2 * 3 * 4"))
        assertEquals(listOf(MdSpan("*not*")), parseInline("\\*not\\*"))
    }
}
