package com.domovoi.app.ui.shell.player

import androidx.compose.runtime.CompositionLocalProvider
import com.domovoi.app.LocalApp
import com.domovoi.app.player.Chapter
import com.domovoi.app.player.PlayItem
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.RecordingExoPlayer
import com.domovoi.app.testing.appWith
import com.domovoi.app.testing.button
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.testPlayer
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The mini player's next did nothing for a book (batch 18, 2026-10-20): a
 * book is one queue item. Now next is its next chapter, and where there is
 * none (a book of one chapter, nothing queued after it) the button is
 * greyed rather than left to do nothing — in the mini player and in the
 * queue sheet's transport row.
 */
class BookNextButtonTest {

    private fun book(chapters: List<Chapter>) = PlayItem.fromBook(41, "The Long Road", "Author", 2_400.0, null, chapters)

    /** Whether "next" takes a tap in the mini player and in the sheet, with [item] playing. */
    private fun nextEnabled(item: PlayItem): Pair<Boolean, Boolean> {
        val exo = RecordingExoPlayer()
        val player = testPlayer(exo)
        player.playItems(listOf(item), 0)
        val app = appWith(player)
        var mini = false
        var sheet = false
        withHeadlessWindow {
            val miniTree = NodeTree()
            composeTest(miniTree) {
                setContent {
                    HeadlessUi { CompositionLocalProvider(LocalApp provides app) { MiniPlayer(onOpenPlayer = {}) } }
                }
                settle()
                mini = miniTree.button("next").enabled
            }
            val sheetTree = NodeTree()
            composeTest(sheetTree) {
                setContent {
                    HeadlessUi { CompositionLocalProvider(LocalApp provides app) { SheetTransport(playing = true, onDismiss = {}) } }
                }
                settle()
                sheet = sheetTree.button("next").enabled
            }
        }
        return mini to sheet
    }

    @Test fun aBookOfOneChapterAloneHasNoNext() {
        assertEquals(false to false, nextEnabled(book(listOf(Chapter("", 0.0)))))
        assertEquals(false to false, nextEnabled(book(emptyList())))
    }

    @Test fun aBookWithChaptersHasANext() {
        assertEquals(true to true, nextEnabled(book(listOf(Chapter("One", 0.0), Chapter("Two", 600.0)))))
    }
}
