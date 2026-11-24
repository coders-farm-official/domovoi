package com.domovoi.app.ui.screens.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The draft changes view (1.4.1, as the web's chat_drafts.jsx): a
 *  paragraph gap between a long removed run and the added run straight
 *  after it, and a screen reader told which words went and which came. */
class DraftChangesGapTest {
    private val long = "one two three four five six seven eight"

    @Test fun aLongRemovedRunBeforeAnAddedRunGetsAGap() {
        val ops = listOf("=" to "Dear Sam, ", "-" to long, "+" to "new words", "=" to " end")
        assertTrue(gapAfter(ops, 1))
        assertFalse(gapAfter(ops, 0))
        assertFalse(gapAfter(ops, 2))
        assertFalse(gapAfter(ops, 3))
    }

    @Test fun aLineBreakMakesARunLong() {
        assertTrue(gapAfter(listOf("-" to "two\nlines", "+" to "x"), 0))
    }

    @Test fun noGapForShortRunsOrWhereTheLineAlreadyBreaks() {
        assertFalse(gapAfter(listOf("-" to "seven words are not quite enough here", "+" to "x"), 0))
        assertFalse(gapAfter(listOf("-" to "$long\n", "+" to "x"), 0))
        assertFalse(gapAfter(listOf("-" to long, "+" to "\nx"), 0))
        assertFalse(gapAfter(listOf("-" to long, "=" to "x"), 0))
        assertFalse(gapAfter(listOf("-" to long), 0))
        assertFalse(gapAfter(listOf("+" to long, "+" to "x"), 0))
    }

    @Test fun theScreenReaderHearsWhatWentAndWhatCame() {
        val ops = listOf("=" to "Dear Sam, ", "-" to "I am writing ", "+" to "I write", "=" to " to say hello.", "+" to "  ")
        assertEquals("Dear Sam, removed: I am writing. added: I write. to say hello.", changesSpoken(ops))
        assertEquals("removed: Thanks!", changesSpoken(listOf("-" to "Thanks!")))
    }
}
