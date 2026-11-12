package com.domovoi.app.player

import android.net.TestUri
import android.net.Uri
import androidx.media3.common.C
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import com.domovoi.app.testing.CastRig
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Every kind the phone plays shows in the media notification and on the lock
 * screen with its own title, the line under it, its artwork, and the
 * controls that fit it (mobile hand-off 2026-11-11, §3). The owner saw music
 * there and an audiobook not.
 *
 * Each kind is played the way its screen plays it ([PlayerController.playItems]),
 * and what the media session sees is read back from the session's player
 * ([CastAwarePlayer]): the media item the ExoPlayer was handed, and the
 * commands the notification draws its buttons from.
 */
class SessionMediaKindsTest {
    private val rig = CastRig()
    private val session = CastAwarePlayer(rig.exo.player, rig.player)

    @Before fun realUris() { SessionMedia.uriOf = { TestUri(it) } }

    @After fun tearDown() {
        SessionMedia.uriOf = { Uri.parse(it) }
        rig.close()
    }

    private val chapters = listOf(Chapter("Opening", 0.0), Chapter("The Road", 600.0), Chapter("The Inn", 1_500.0))

    private val song = PlayItem.fromTrack(7, "Stone Floor", "Kettle Band", "Cellar", 200.0)

    /** A folder book: two files played as one item (BookMediaSources). */
    private val book = PlayItem.fromBook(
        41, "The Long Road", "A. Writer", 2_400.0, "/api/audiobooks/41/cover", chapters,
        parts = listOf(
            MediaPart("/api/audiobooks/41/audio?file=01.mp3", 1_200.0),
            MediaPart("/api/audiobooks/41/audio?file=02.mp3", 1_200.0),
        ),
    )

    private val episode = PlayItem.fromEpisode(
        9, "Episode 9: Kettles", "The Kitchen Show", 1_800.0, "/api/podcasts/subscriptions/3/artwork", emptyList(),
    )

    private val station = PlayItem.fromStation(5, "Radio Cellar")

    private fun play(item: PlayItem, atSec: Double = 0.0) {
        rig.player.playItems(listOf(item), 0)
        rig.exo.positionMs = (atSec * 1000).toLong()
        rig.log.clear()
    }

    /** The media item the session sees now. */
    private fun seen(): MediaMetadata {
        val item = session.currentMediaItem
        assertNotNull("the session sees no media item", item)
        return item!!.mediaMetadata
    }

    private fun artwork(): String? = seen().artworkUri?.toString()

    private fun seeks(): List<String> = rig.actions().filter { it.startsWith("exo.seek") }.map { it.substringBefore(" [") }

    // ---- what each kind shows ------------------------------------------------

    @Test fun musicShowsTheSongTheArtistAndItsCover() {
        play(song)
        val m = seen()
        assertEquals("Stone Floor", m.title)
        assertEquals("Kettle Band", m.artist)
        assertEquals("Cellar", m.albumTitle)
        assertEquals(MediaMetadata.MEDIA_TYPE_MUSIC, m.mediaType)
        assertTrue(artwork().orEmpty().matches(Regex("http://.+/api/music/library/7/cover")))
    }

    @Test fun anAudiobookShowsItsTitleItsAuthorAndItsCover() {
        play(book)
        val m = seen()
        assertEquals("The Long Road", m.title)
        assertEquals("A. Writer", m.artist)
        assertEquals(MediaMetadata.MEDIA_TYPE_AUDIO_BOOK, m.mediaType)
        assertEquals(true, m.isPlayable)
        assertTrue(artwork().orEmpty().matches(Regex("http://.+/api/audiobooks/41/cover")))
    }

    @Test fun aSavedAudiobooksCoverIsItsOwnFile() {
        play(book.copy(uid = "saved-k", coverPath = "file:///data/books/k/cover.jpg", savedKey = "k"))
        assertEquals("file:///data/books/k/cover.jpg", artwork())
        assertEquals("A. Writer", seen().artist)
    }

    @Test fun aPodcastEpisodeShowsItsTitleItsShowAndTheShowsArtwork() {
        play(episode)
        val m = seen()
        assertEquals("Episode 9: Kettles", m.title)
        assertEquals("The Kitchen Show", m.artist)
        assertEquals(MediaMetadata.MEDIA_TYPE_PODCAST_EPISODE, m.mediaType)
        assertTrue(artwork().orEmpty().matches(Regex("http://.+/api/podcasts/subscriptions/3/artwork")))
    }

    @Test fun aStationShowsItsNameAsLiveRadioWithNoArtwork() {
        play(station)
        val m = seen()
        assertEquals("Radio Cellar", m.title)
        assertEquals("live radio", m.artist)
        assertEquals("Radio Cellar", m.station)
        assertEquals(MediaMetadata.MEDIA_TYPE_RADIO_STATION, m.mediaType)
        assertNull("a station has no artwork to show", m.artworkUri)
    }

    // ---- the controls each kind gets -----------------------------------------

    @Test fun aBookSkipsBack15AndForward30AndMovesByChapter() {
        play(book, atSec = 100.0)
        assertEquals(listOf(SessionMedia.Button.SkipBack, SessionMedia.Button.SkipForward), buttons())
        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_BACK))
        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_FORWARD))
        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM))
        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_TO_NEXT)) // the next chapter
        assertEquals(15_000L, session.seekBackIncrement)
        assertEquals(30_000L, session.seekForwardIncrement)

        // The notification's skip buttons, as the session runs them.
        assertTrue(SessionMedia.perform(SessionMedia.ACTION_SKIP_BACK, session))
        assertTrue(SessionMedia.perform(SessionMedia.ACTION_SKIP_FORWARD, session))
        assertEquals(listOf("exo.seekTo(85000)", "exo.seekTo(115000)"), seeks())
    }

    @Test fun aSkipBackNearTheStartGoesToTheStart() {
        play(book, atSec = 4.0)
        session.seekBack()
        assertEquals(listOf("exo.seekTo(0)"), seeks())
    }

    @Test fun aPodcastEpisodeSkipsBack15AndForward30() {
        play(episode, atSec = 60.0)
        assertEquals(listOf(SessionMedia.Button.SkipBack, SessionMedia.Button.SkipForward), buttons())
        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_BACK))
        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_FORWARD))
        session.seekBack()
        session.seekForward()
        assertEquals(listOf("exo.seekTo(45000)", "exo.seekTo(75000)"), seeks())
    }

    @Test fun aStationIsLiveAndCannotSeek() {
        play(station, atSec = 30.0)
        assertEquals(emptyList<SessionMedia.Button>(), buttons())
        for (c in listOf(
            Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM, Player.COMMAND_SEEK_BACK, Player.COMMAND_SEEK_FORWARD,
            Player.COMMAND_SEEK_TO_DEFAULT_POSITION, Player.COMMAND_SET_SPEED_AND_PITCH,
            Player.COMMAND_SEEK_TO_PREVIOUS, // would only restart the stream
        )) {
            assertFalse("a station offers command $c", session.isCommandAvailable(c))
            // The same set leaves availableCommands (its FlagSet can't be
            // read on the JVM: SparseBooleanArray is a stub here).
            assertTrue("a station's commands hold $c", c in SessionMedia.withheld(rig.player.current, false))
        }
        assertTrue(session.isCommandAvailable(Player.COMMAND_PLAY_PAUSE))
        assertFalse(Player.COMMAND_PLAY_PAUSE in SessionMedia.withheld(rig.player.current, false))
        assertEquals("a live stream has no length", C.TIME_UNSET, session.duration)

        // And a controller that seeks anyway moves nothing.
        session.seekTo(5_000)
        session.seekBack()
        session.seekForward()
        session.seekToDefaultPosition()
        SessionMedia.perform(SessionMedia.ACTION_SKIP_FORWARD, session)
        assertEquals(emptyList<String>(), seeks())
    }

    @Test fun aStationAfterSomethingElseKeepsPrevious() {
        rig.player.playItems(listOf(song, station), 1)
        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_TO_PREVIOUS))
        assertFalse(session.isCommandAvailable(Player.COMMAND_SEEK_BACK))
    }

    @Test fun musicKeepsItsControlsAndGetsNoSkipButtons() {
        play(song, atSec = 50.0)
        assertEquals(emptyList<SessionMedia.Button>(), buttons())
        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM))
        assertTrue(session.isCommandAvailable(Player.COMMAND_SEEK_TO_PREVIOUS))
        assertTrue(session.isCommandAvailable(Player.COMMAND_PLAY_PAUSE))
    }

    @Test fun whileCastingNothingSkips() {
        assertEquals(emptyList<SessionMedia.Button>(), SessionMedia.buttons(book, casting = true))
        assertEquals(emptyList<SessionMedia.Button>(), SessionMedia.buttons(episode, casting = true))
    }

    private fun buttons(): List<SessionMedia.Button> =
        SessionMedia.buttons(rig.player.current, casting = rig.player.target.value is PlayTarget.Room)
}
