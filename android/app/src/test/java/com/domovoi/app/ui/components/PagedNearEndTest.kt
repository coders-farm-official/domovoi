package com.domovoi.app.ui.components

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** When a paged list asks for its next page (ui/components/Paged.kt). */
class PagedNearEndTest {
    @Test fun nearTheEndOfALoadedPageAsks() {
        // 55 items (a header, 50 rows, a footer...), the 47th on screen.
        assertTrue(nearEnd(totalItems = 55, lastVisible = 46, threshold = 8))
        assertFalse(nearEnd(totalItems = 55, lastVisible = 30, threshold = 8))
    }

    @Test fun aScreenStillShowingItsHeaderAndSpinnerDoesNotAsk() {
        // The Music page before its first page lands: header, now playing,
        // tabs, filters, spinner — all on screen, and not a list end.
        assertFalse(nearEnd(totalItems = 5, lastVisible = 4, threshold = 8))
        assertFalse(nearEnd(totalItems = 0, lastVisible = -1, threshold = 8))
    }
}
