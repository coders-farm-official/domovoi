package com.domovoi.app.ui.shell.player

import com.domovoi.app.player.LyricsDoc
import com.domovoi.app.player.LyricsResult
import com.domovoi.app.ui.shell.player.SheetTab.Hide
import com.domovoi.app.ui.shell.player.SheetTab.Lyrics
import com.domovoi.app.ui.shell.player.SheetTab.Queue
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Batch 2 §6: the full player's Lyrics · Queue · Hide tabs follow the web's
 * default rule (player.jsx playerSheetTab), without the Equalizer the phone
 * doesn't have: Lyrics when the song has them, else the Queue; the pick
 * sticks for the session.
 */
class PlayerSheetTabsTest {

    private fun tab(
        here: SheetTab? = null,
        picked: SheetTab? = null,
        lyricsTab: Boolean = true,
        found: Boolean = false,
        loading: Boolean = false,
    ) = playerSheetTab(here, picked, lyricsTab, found, loading)

    @Test fun lyricsByDefaultWhenTheSongHasThem() {
        assertEquals(Lyrics, tab(found = true))
        // Still loading: Lyrics, so it doesn't flick to the queue and back.
        assertEquals(Lyrics, tab(loading = true))
    }

    @Test fun theQueueByDefaultWhenItHasNone() {
        assertEquals(Queue, tab(found = false))
        // Radio, a podcast, a song on the phone: no lyrics tab at all.
        assertEquals(Queue, tab(lyricsTab = false, found = false))
    }

    @Test fun thePickSticksAcrossSongs() {
        assertEquals(Hide, tab(picked = Hide, found = true))
        assertEquals(Queue, tab(picked = Queue, found = true))
        assertEquals(Lyrics, tab(picked = Lyrics, found = true))
    }

    @Test fun aLyricsPickGivesWayOnASongWithout() {
        assertEquals(Queue, tab(picked = Lyrics, found = false))
        assertEquals(Queue, tab(picked = Lyrics, lyricsTab = false))
    }

    @Test fun aTabPickedForThisSongStaysEvenLyricsWithNone() {
        assertEquals(Lyrics, tab(here = Lyrics, found = false))
        assertEquals(Queue, tab(here = Queue, picked = Hide, found = true))
        // ...but not a Lyrics tab the song can't have.
        assertEquals(Queue, tab(here = Lyrics, lyricsTab = false))
    }

    @Test fun theTabsOffered() {
        assertEquals(listOf(Lyrics, Queue, Hide), sheetTabs(lyricsTab = true))
        assertEquals(listOf(Queue, Hide), sheetTabs(lyricsTab = false))
    }

    @Test fun onlyWordsCountAsFoundLyrics() {
        assertTrue(lyricsFound(LyricsResult.Loaded(LyricsDoc(status = "plain", text = "la la"))))
        assertFalse(lyricsFound(LyricsResult.Loaded(LyricsDoc(status = "instrumental"))))
        assertFalse(lyricsFound(LyricsResult.Loaded(LyricsDoc(status = "none", checking = true))))
        assertFalse(lyricsFound(LyricsResult.Hidden))
        assertFalse(lyricsFound(null))
    }
}
