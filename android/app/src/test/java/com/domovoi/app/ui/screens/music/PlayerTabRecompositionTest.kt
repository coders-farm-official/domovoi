package com.domovoi.app.ui.screens.music

import androidx.compose.runtime.CompositionLocalProvider
import com.domovoi.app.LocalApp
import com.domovoi.app.player.Chapter
import com.domovoi.app.player.PlayItem
import com.domovoi.app.testing.appWith
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.testPlayer
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The player tab's state ([rememberPlayerTabModel]) is collected at the top
 * of the Music page, and the tab's item list — a row per queue entry — is
 * built from it. While something plays the position ticks every 500 ms; it
 * used to be read right there, so every tick rebuilt the page and the whole
 * queue (at 1,500 tracks, 50-70 ms of main thread a second). Only the seek
 * row and the current-chapter state may follow it.
 *
 * Composed for real on the JVM (no UI): counts how often the caller of
 * rememberPlayerTabModel runs.
 */
class PlayerTabRecompositionTest {

    private val book = PlayItem.fromBook(
        id = 4, title = "The Long Book", author = "someone", durationSec = 9_000.0,
        artwork = null,
        chapters = listOf(Chapter("one", 0.0), Chapter("two", 60.0), Chapter("three", 125.5)),
    )

    /** PlayerController's public flows are its own MutableStateFlows. */
    @Suppress("UNCHECKED_CAST")
    private fun <T> writable(flow: StateFlow<T>) = flow as MutableStateFlow<T>

    @Test fun thePositionTickDoesNotRecomposeTheTab() = composeTest {
        val player = testPlayer()
        val app = appWith(player)
        writable(player.queue).value = listOf(book)
        var runs = 0
        var model: PlayerTabModel? = null
        setContent {
            CompositionLocalProvider(LocalApp provides app) {
                runs++
                model = rememberPlayerTabModel()
            }
        }
        settle()
        val composed = runs
        assertNotNull(model)
        assertEquals(book.chapters, model!!.chapters)

        val position = writable(player.positionSec)
        for (sec in listOf(0.5, 1.0, 1.5, 2.0, 60.5, 61.0, 126.0, 126.5)) {
            position.value = sec
            settle()
        }

        assertEquals("a position tick recomposed the player tab", composed, runs)
        // The chapter still follows the position, through its own state.
        assertEquals(2, model!!.currentChapter.value)
        position.value = 61.0
        settle()
        assertEquals(1, model!!.currentChapter.value)
        assertEquals(composed, runs)
    }

    @Test fun whatTheItemListIsBuiltFromDoesRecomposeIt() {
        // The harness sees recompositions: the queue and the index are the
        // tab's own inputs.
        composeTest {
            val player = testPlayer()
            val app = appWith(player)
            val other = PlayItem.fromTrack(9, "other", "artist", null, 120.0)
            writable(player.queue).value = listOf(book)
            var runs = 0
            var model: PlayerTabModel? = null
            setContent {
                CompositionLocalProvider(LocalApp provides app) {
                    runs++
                    model = rememberPlayerTabModel()
                }
            }
            settle()
            val composed = runs

            writable(player.queue).value = listOf(book, other)
            settle()
            assertTrue(runs > composed)
            assertEquals(2, model!!.queue.size)

            val queued = runs
            writable(player.index).value = 1
            settle()
            assertTrue(runs > queued)
            assertEquals(other, model!!.current)
            assertEquals(emptyList<Chapter>(), model!!.chapters)
        }
    }
}
