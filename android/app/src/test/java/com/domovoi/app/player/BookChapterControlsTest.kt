package com.domovoi.app.player

import androidx.media3.common.Player
import com.domovoi.app.testing.CastRig
import com.domovoi.app.testing.libraryQueue
import com.domovoi.app.testing.setField
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A book's next and previous on this phone (batch 18, 2026-10-20): the mini
 * player's next did nothing for a book (one queue item), and the
 * notification's previous sent a book back to its very start. They move by
 * chapter now — from the app's buttons ([PlayerController.next] / prev) and
 * from the notification, the lock screen and a headset ([CastAwarePlayer]),
 * which offers next only while there is a next chapter.
 */
class BookChapterControlsTest {
    private val rig = CastRig()
    private val session = CastAwarePlayer(rig.exo.player, rig.player)

    @After fun tearDown() = rig.close()

    private val chapters = listOf(Chapter("Opening", 0.0), Chapter("The Road", 600.0), Chapter("The Inn", 1_500.0))

    private fun book(id: Long = 41, chapters: List<Chapter> = this.chapters) =
        PlayItem.fromBook(id, "Book $id", "Author", 2_400.0, null, chapters)

    private fun playBook(vararg items: PlayItem, atSec: Double) {
        rig.player.playItems(items.toList(), 0)
        rig.exo.positionMs = (atSec * 1000).toLong()
        rig.log.clear()
    }

    private fun calls(): List<String> = rig.actions().map { it.substringBefore(" [") }

    @Test fun theAppsNextAndPreviousMoveByChapter() {
        playBook(book(), atSec = 100.0)

        rig.player.next()
        assertEquals(listOf("exo.seekTo(600000)"), calls())

        rig.log.clear()
        rig.exo.positionMs = 620_000
        rig.player.prev() // 20 s into The Road: its start again
        assertEquals(listOf("exo.seekTo(600000)"), calls())

        rig.log.clear()
        rig.exo.positionMs = 601_000
        rig.player.prev() // a second in: the chapter before
        assertEquals(listOf("exo.seekTo(0)"), calls())
    }

    @Test fun fromTheLastChapterNextDoesNothingAndIsNotOffered() {
        playBook(book(), atSec = 1_600.0)

        assertFalse(rig.player.canNextHere())
        rig.player.next()
        assertEquals(emptyList<String>(), calls())
        assertFalse(session.isCommandAvailable(Player.COMMAND_SEEK_TO_NEXT))
        assertFalse(session.isCommandAvailable(Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM))
        // Previous is always there: this chapter's start at least.
        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_TO_PREVIOUS))
    }

    @Test fun aBookOfOneChapterHasNoNextUnlessSomethingIsQueuedAfterIt() {
        playBook(book(chapters = listOf(Chapter("", 0.0))), atSec = 30.0)
        assertFalse(rig.player.canNextHere())
        assertFalse(session.isCommandAvailable(Player.COMMAND_SEEK_TO_NEXT))

        playBook(book(41, listOf(Chapter("", 0.0))), book(42), atSec = 30.0)
        assertTrue(rig.player.canNextHere())
        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_TO_NEXT))
        session.seekToNext()
        assertEquals(listOf("exo.seekToNextMediaItem"), calls())
    }

    @Test fun theNotificationsNextAndPreviousMoveByChapterToo() {
        // The ExoPlayer has no next item, but the book has a next chapter.
        playBook(book(), atSec = 100.0)
        rig.exo.unavailable += Player.COMMAND_SEEK_TO_NEXT
        rig.exo.unavailable += Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM

        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_TO_NEXT))
        session.seekToNext()
        rig.exo.positionMs = 600_000
        session.seekToNextMediaItem()
        rig.exo.positionMs = 1_510_000
        session.seekToPrevious() // ten seconds into The Inn: its start
        rig.exo.positionMs = 1_500_000
        session.seekToPreviousMediaItem()

        // Never the ExoPlayer's own: on a book, its previous was the book's start.
        assertEquals(
            listOf("exo.seekTo(600000)", "exo.seekTo(1500000)", "exo.seekTo(1500000)", "exo.seekTo(600000)"),
            calls(),
        )
    }

    @Test fun songsKeepTheExoPlayersOwnNextAndPrevious() {
        rig.player.playItems(libraryQueue("Stone Floor", "Old Barrels"), 0)
        rig.log.clear()
        session.seekToNext()
        session.seekToPrevious()
        assertEquals(listOf("exo.seekToNext", "exo.seekToPrevious"), calls())
    }

    @Test fun theSessionHearsTheBooksCommandsNotTheExoPlayers() {
        playBook(book(), atSec = 100.0)
        var heard: Player.Commands? = null
        var playing: Boolean? = null
        var state: Int? = null
        val listener = object : Player.Listener {
            override fun onAvailableCommandsChanged(availableCommands: Player.Commands) { heard = availableCommands }
            override fun onIsPlayingChanged(isPlaying: Boolean) { playing = isPlaying }
            override fun onPlaybackStateChanged(playbackState: Int) { state = playbackState }
        }
        val before = rig.exo.listeners.size
        session.addListener(listener)
        assertEquals(before + 1, rig.exo.listeners.size)
        val fromExo = Player.Commands.Builder().build()
        // What the ExoPlayer tells its listeners (the session's, wrapped).
        rig.exo.listeners.forEach {
            it.onAvailableCommandsChanged(fromExo)
            it.onIsPlayingChanged(true)
            it.onPlaybackStateChanged(Player.STATE_BUFFERING)
        }
        assertNotNull("the session heard no change of commands", heard)
        assertNotSame("the ExoPlayer's commands went to the session as they were", fromExo, heard)
        // Everything else passes through as it was (Kotlin's `by` passed
        // none of it: Player.Listener's methods are Java defaults).
        assertEquals(true, playing)
        assertEquals(Player.STATE_BUFFERING, state)
        // And the session can let go of it again.
        session.removeListener(listener)
        assertEquals(before, rig.exo.listeners.size)
    }

    @Test fun crossingIntoTheLastChapterTellsTheSessionOnce() {
        playBook(book(), atSec = 1_400.0)
        setField(rig.player, "session", session)
        var told = 0
        session.addListener(object : Player.Listener {
            override fun onAvailableCommandsChanged(availableCommands: Player.Commands) { told++ }
        })

        rig.player.bookCommandsTick() // first look: next is there
        rig.player.bookCommandsTick()
        assertEquals(1, told)
        rig.exo.positionMs = 1_550_000
        rig.player.bookCommandsTick() // into The Inn: no next any more
        rig.player.bookCommandsTick()
        assertEquals(2, told)
        assertFalse(session.isCommandAvailable(Player.COMMAND_SEEK_TO_NEXT))
    }
}
