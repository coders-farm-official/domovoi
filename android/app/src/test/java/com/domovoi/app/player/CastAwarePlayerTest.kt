package com.domovoi.app.player

import androidx.media3.common.C
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import com.domovoi.app.testing.CastRig
import com.domovoi.app.testing.awaitUntil
import com.domovoi.app.testing.libraryQueue
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The media notification, the lock screen and headset buttons drive the
 * session's player, [CastAwarePlayer]. Before 2026-10-01 that was the
 * ExoPlayer itself, so "play" or "next" there while casting started the
 * phone under a room that was playing. These tests drive it as the session
 * would and pin that, while casting, it acts on the ROOM and shows the room.
 *
 * (media3's Player.Commands is a FlagSet over android.util.SparseBooleanArray,
 * which is a stub on the JVM, so the command policy is pinned through
 * isCommandAvailable, which reads the same IN_A_ROOM / NOT_IN_A_ROOM sets
 * getAvailableCommands is built from.)
 */
class CastAwarePlayerTest {
    private val rig = CastRig()
    private val session = CastAwarePlayer(rig.exo.player, rig.player)

    @After fun tearDown() = rig.close()

    private fun casting(state: String = "play") {
        rig.player.playItems(libraryQueue("Stone Floor", "Old Barrels", "Quiet Jars"), 1)
        rig.exo.positionMs = 5_000
        rig.room("office", state, "Old Barrels", 5.0)
        runBlocking { rig.player.castTo("office") }
        rig.awaitRemote("office")
        rig.log.clear()
    }

    private fun roomCalls(): List<String> = rig.actions().map { it.substringBefore(" [") }

    @Test fun onThePhoneItIsTheExoPlayer() {
        rig.player.playItems(libraryQueue("Stone Floor", "Old Barrels"), 0)
        rig.log.clear()

        session.pause()
        assertFalse(session.playWhenReady)
        session.play()
        assertTrue(session.isPlaying)
        session.seekToNext()
        session.seekToPrevious()
        session.seekTo(1_000)

        assertEquals(
            listOf("exo.pause", "exo.play", "exo.seekToNext", "exo.seekToPrevious", "exo.seekTo(1000)"),
            roomCalls(),
        )
        assertEquals("phone item 0", session.mediaMetadata.title.toString())
    }

    @Test fun whileCastingPlayPauseAndNextGoToTheRoomNeverThePhone() {
        casting()

        session.pause()
        rig.awaitLog("POST /api/music/pause/office")
        session.play()
        rig.awaitLog("POST /api/music/resume/office")
        session.seekToNext()
        rig.awaitLog("POST /api/music/skip/office")
        session.seekToNextMediaItem()
        session.setPlayWhenReady(true)
        session.setPlayWhenReady(false)
        awaitUntil(what = "two skips, two resumes and two pauses") {
            listOf("skip", "resume", "pause").all { a ->
                rig.actions().count { it.startsWith("POST /api/music/$a/office") } == 2
            }
        }

        assertTrue("the phone was driven: ${rig.actions()}", rig.actions().none { it.startsWith("exo.") })
        assertFalse(rig.exo.playing)
    }

    @Test fun whileCastingSeekingIsNotOfferedAndDoesNothing() {
        casting()

        session.seekTo(30_000)
        session.seekTo(0, 0)
        session.seekBack()
        session.seekForward()
        session.seekToDefaultPosition()
        Thread.sleep(100)

        assertEquals(emptyList<String>(), rig.actions())
        assertTrue(session.isCommandAvailable(Player.COMMAND_PLAY_PAUSE))
        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_TO_NEXT))
        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM))
        for (c in listOf(
            Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM, Player.COMMAND_SEEK_BACK,
            Player.COMMAND_SEEK_FORWARD, Player.COMMAND_SET_SPEED_AND_PITCH,
        )) assertFalse("command $c offered while casting", session.isCommandAvailable(c))
    }

    @Test fun noSessionControllerMayChooseWhatPlaysInEitherMode() {
        // The rig's player offers every command; the session's player hides
        // the two that would let a controller hand it a media item — and so
        // a URL for the authenticated data source to open (A6-01) — on the
        // phone and while casting alike. Transport stays.
        rig.player.playItems(libraryQueue("Stone Floor", "Old Barrels"), 0)
        for (c in SessionAccess.MEDIA_ITEM_COMMANDS) {
            assertFalse("command $c offered on the phone", session.isCommandAvailable(c))
        }
        assertTrue(session.isCommandAvailable(Player.COMMAND_PLAY_PAUSE))

        casting()
        for (c in SessionAccess.MEDIA_ITEM_COMMANDS) {
            assertFalse("command $c offered while casting", session.isCommandAvailable(c))
        }
        assertTrue(session.isCommandAvailable(Player.COMMAND_PLAY_PAUSE))
        assertEquals(
            setOf(Player.COMMAND_SET_MEDIA_ITEM, Player.COMMAND_CHANGE_MEDIA_ITEMS),
            CastAwarePlayer.NEVER_FROM_THE_SESSION,
        )
    }

    @Test fun whileCastingPreviousIsOfferedAndGoesToTheRoom() {
        // Since 2026-10-01 the core's previous follows the room's queue, so
        // the lock screen's previous is the room's (it was withheld).
        casting()
        // Whatever the phone's own player could do from where it stopped.
        rig.exo.unavailable += listOf(Player.COMMAND_SEEK_TO_PREVIOUS, Player.COMMAND_SEEK_TO_PREVIOUS_MEDIA_ITEM)

        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_TO_PREVIOUS))
        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_TO_PREVIOUS_MEDIA_ITEM))
        session.seekToPrevious()
        session.seekToPreviousMediaItem()
        awaitUntil(what = "two previous") {
            rig.actions().count { it.startsWith("POST /api/music/previous/office") } == 2
        }
        assertTrue("the phone was moved: ${rig.actions()}", rig.actions().none { it.startsWith("exo.") })
    }

    @Test fun whileCastingItShowsTheRoom() {
        casting("play")
        rig.room("office", "play", "Quiet Jars", 42.5)
        rig.awaitRemoteTitle("Quiet Jars")

        assertTrue(session.isPlaying)
        assertTrue(session.playWhenReady)
        assertEquals(Player.STATE_READY, session.playbackState)
        assertEquals("Quiet Jars", session.mediaMetadata.title.toString())
        assertEquals("Kettle Band · in office", session.mediaMetadata.artist.toString())
        assertEquals(42_500L, session.currentPosition)
        assertEquals(200_000L, session.duration)

        rig.room("office", "pause", "Quiet Jars", 43.0)
        rig.awaitRemoteState("pause")
        assertFalse("a paused room shown as playing", session.isPlaying)
        assertFalse(session.playWhenReady)
    }

    @Test fun beforeTheRoomAnswersItSaysWhereItIsCasting() {
        rig.player.playItems(libraryQueue("Stone Floor"), 0)
        rig.failing["/api/music/now-playing"] = 503
        runBlocking { rig.player.castTo("office") }

        assertEquals("casting to office", session.mediaMetadata.title.toString())
        assertEquals("in office", session.mediaMetadata.artist.toString())
        assertFalse(session.isPlaying)
        assertEquals(C.TIME_UNSET, session.duration)
    }

    @Test fun refreshTellsTheSessionWhatChanged() {
        val heard = mutableListOf<String>()
        session.addListener(object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) { heard += "isPlaying=$isPlaying" }
            override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
                heard += "playWhenReady=$playWhenReady/$reason"
            }
            override fun onMediaMetadataChanged(mediaMetadata: MediaMetadata) {
                heard += "title=${mediaMetadata.title}"
            }
        })
        casting("play")

        session.refresh()

        assertEquals(
            listOf(
                "playWhenReady=true/${Player.PLAY_WHEN_READY_CHANGE_REASON_REMOTE}",
                "isPlaying=true",
                "title=Old Barrels",
            ),
            heard,
        )
    }
}

private fun CastRig.awaitRemoteTitle(title: String) =
    awaitUntil(what = "office read as $title") { player.remote.value?.title == title }

private fun CastRig.awaitRemoteState(state: String) =
    awaitUntil(what = "office read as $state") { player.remote.value?.state == state }
