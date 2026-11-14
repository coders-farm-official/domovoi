package com.domovoi.app.ui.screens.music

import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.QueueOrigin
import com.domovoi.app.player.UpNextState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What the queue panels draw of the phone's Up next (PhoneUpNextUi.kt), and
 * the lists a library song is played from (the web's music.jsx contexts).
 */
class PhoneUpNextUiTest {
    private fun song(id: Long) = PlayItem.fromTrack(id, "Song $id", "Band", null, 100.0)
    private fun pick(seq: Int) = song(900L + seq).copy(queueOrigin = QueueOrigin.Auto, upNextSeq = seq, sessionId = "s1")
    private val queue = listOf(song(1), song(2), pick(1), pick(2))

    @Test fun theRowsAboveTheSectionAndTheCountLeaveOutTheWaitingPicks() {
        val v = PhoneUpNextView.of(paired = true, queue = queue, state = UpNextState("srv", 1, id = "s1"), casting = false, autoplayPref = true)
        assertTrue(v.shown)
        assertEquals(listOf(0, 1), v.explicitRows(queue, 0))
        assertEquals(listOf(2, 3), v.pickRows(queue, 0))
        assertEquals(2, v.count(queue, 0))
    }

    @Test fun withTheSectionHiddenEveryRowShowsSoNoPickPlaysUnseen() {
        val v = PhoneUpNextView.of(paired = false, queue = queue, state = UpNextState("srv", 1, id = "s1"), casting = false, autoplayPref = true)
        assertFalse(v.shown)
        assertEquals(listOf(0, 1, 2, 3), v.explicitRows(queue, 0))
        assertTrue(v.pickRows(queue, 0).isEmpty())
        assertEquals(4, v.count(queue, 0))
    }

    @Test fun theSwitchSaysTheSessionsAutoplayElseThePreference() {
        assertFalse(PhoneUpNextView.of(true, queue, UpNextState("srv", 1, autoplay = false), false, autoplayPref = true).autoplay)
        assertFalse(PhoneUpNextView.of(true, queue, null, false, autoplayPref = false).autoplay)
        assertTrue(PhoneUpNextView.of(true, queue, null, false, autoplayPref = true).autoplay)
    }

    @Test fun aLibraryRowGoesOnWithWhatIsShownOnlyWhenSearchedOrFiltered() {
        assertNull("the whole library: library mode", libraryRowContext("", "all", favoritedOnly = false))
        assertEquals("results for \"neil\"", libraryRowContext(" neil ", "all", false)!!.name)
        assertEquals("favorites", libraryRowContext("", "all", true)!!.name)
        assertEquals("this list", libraryRowContext("", "upload", false)!!.name)
        assertEquals("ids", libraryRowContext("", "upload", false)!!.kind)
    }

    @Test fun aPlaylistByItsIdAndFavoritesByItsSongs() {
        val real = playlistContext(Playlist(id = 12, name = "Road trip"))
        assertEquals("playlist", real.kind)
        assertEquals("12", real.ref)
        val fav = playlistContext(Playlist(id = -1, name = "Favorites", isVirtual = true))
        assertEquals("ids", fav.kind)
        assertNull(fav.ref)
    }

    @Test fun aGenreByItsKeyAndTheNoGenreRowByItsSongs() {
        assertEquals("genre", genreContext(GenreRow(key = "soul", name = "Soul")).kind)
        assertEquals("soul", genreContext(GenreRow(key = "soul", name = "Soul")).ref)
        assertEquals("ids", genreContext(GenreRow(key = "", name = "no genre")).kind)
    }

    @Test fun aRoomPlaylistCarriesThisPhonesAutoplayOff() {
        val off = roomPlayPlaylistBody("office", 3, false, "d", null, false, autoplay = false)
        assertEquals("false", off["autoplay"].toString())
        assertFalse("on is the server's default", "autoplay" in roomPlayPlaylistBody("office", 3, false, "d", null, false))
    }
}
