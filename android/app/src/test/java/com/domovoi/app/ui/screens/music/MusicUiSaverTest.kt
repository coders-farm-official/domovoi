package com.domovoi.app.ui.screens.music

import androidx.compose.runtime.saveable.SaverScope
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Music page keeps where it was (tab, search, filters) through
 * the workspace going away and coming back — a spell in the background
 * drops to local media and back — which is what the "your device" switch
 * needs to bring the server's side back as it was left.
 */
class MusicUiSaverTest {
    private val scope = SaverScope { true }

    @Test fun whereThePageIsRoundTrips() {
        val ui = MusicUi().apply {
            tab = 3
            q = "hearth"
            source = "library"
            sort = "artist"
            favoritedOnly = true
            selectedIds.add(7)
        }
        val saved = with(MusicUi.Saver) { scope.save(ui) }!!
        val back = MusicUi.Saver.restore(saved)!!
        assertEquals(3, back.tab)
        assertEquals("hearth", back.q)
        assertEquals("library", back.source)
        assertEquals("artist", back.sort)
        assertEquals(true, back.favoritedOnly)
        assertTrue("a selection is not kept", back.selectedIds.isEmpty())
    }

    @Test fun aFreshPageRoundTripsToItsDefaults() {
        val fresh = MusicUi()
        val back = MusicUi.Saver.restore(with(MusicUi.Saver) { scope.save(fresh) }!!)!!
        assertEquals(fresh.tab, back.tab)
        assertEquals(fresh.sort, back.sort)
        assertEquals(fresh.source, back.source)
        assertEquals(fresh.favoritedOnly, back.favoritedOnly)
    }
}
