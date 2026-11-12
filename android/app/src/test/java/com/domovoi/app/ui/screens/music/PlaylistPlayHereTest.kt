package com.domovoi.app.ui.screens.music

import com.domovoi.app.testing.Bytecode
import com.domovoi.app.testing.CastRig
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Batch 2 §5: a playlist (Favorites included) plays and shuffles on this
 * phone, not only in a room. "play here" / "shuffle here" make the WHOLE
 * list this phone's queue — driven against a real PlayerController on
 * [CastRig].
 */
class PlaylistPlayHereTest {
    private val rig = CastRig()
    private val toasts = mutableListOf<String>()

    @After fun tearDown() = rig.close()

    private fun tracks(n: Int) = (1..n).map {
        LibraryTrack(id = 400L + it, title = "song $it", artist = "Kettle Band", album = "Cellar", durationSec = 180.0)
    }

    private fun here(list: List<LibraryTrack>, shuffle: Boolean, shuffler: (List<LibraryTrack>) -> List<LibraryTrack> = { it }) =
        playTracksHereAndSay(rig.player, list, "Favorites", shuffle, { toasts += it }, { it() }, shuffler)

    @Test fun playHereQueuesTheWholeListInOrder() {
        val list = tracks(37)

        assertTrue(here(list, shuffle = false))

        assertEquals(list.map { it.id }, rig.player.queue.value.map { it.id })
        assertEquals(0, rig.player.index.value)
        assertEquals(listOf("playing Favorites on this device"), toasts)
    }

    @Test fun shuffleHereQueuesEveryTrackInTheShuffledOrder() {
        val list = tracks(37)

        here(list, shuffle = true, shuffler = { it.reversed() })

        assertEquals(list.reversed().map { it.id }, rig.player.queue.value.map { it.id })
    }

    @Test fun shuffleHereByDefaultStillQueuesEveryTrackOnce() {
        val list = tracks(60)

        playTracksHereAndSay(rig.player, list, "Road trip", true, { toasts += it }, { it() })

        assertEquals(list.map { it.id }.sorted(), rig.player.queue.value.map { it.id }.sorted())
    }

    @Test fun anEmptyListPlaysNothing() {
        assertFalse(here(emptyList(), shuffle = false))
        assertTrue(rig.player.queue.value.isEmpty())
        assertTrue(toasts.isEmpty())
    }

    @Test fun thePlaylistDrawerOffersPlayHere() {
        // The drawer's buttons are what call it; a drawer that only plays in
        // rooms (as before) has no caller.
        val calls = Bytecode.callers(
            fileFacade = "com.domovoi.app.ui.screens.music.PlaylistDrawerKt",
            owner = "com/domovoi/app/ui/screens/music/TrackSetsKt",
            names = setOf("playTracksHereAndSay", "playTracksHereAndSay\$default"),
        )
        assertTrue("PlaylistDrawer never plays a playlist on this phone", calls.isNotEmpty())
    }
}
