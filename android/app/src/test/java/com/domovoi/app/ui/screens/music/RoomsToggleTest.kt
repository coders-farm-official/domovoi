package com.domovoi.app.ui.screens.music

import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsModifier
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.buttons
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.elementsOf
import com.domovoi.app.testing.withHeadlessWindow
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Music page's rooms strip is folded away behind a text link (owner
 * 2026-11-11, handoff §2; web/static/music.jsx `domovoi.music.roomsOpen`):
 * closed until opened, the choice kept on this phone, the link worded as
 * the web words it, and announced as a button that is expanded or not.
 */
class RoomsToggleTest {
    private class MapFlags : RoomsOpenStore.Flags {
        val map = mutableMapOf<String, Boolean>()
        override fun get(key: String): Boolean? = map[key]
        override fun put(key: String, value: Boolean) { map[key] = value }
    }

    @Test fun roomsStartFoldedAwayForEveryone() {
        assertFalse(RoomsOpenStore(MapFlags()).open)
    }

    @Test fun theChoiceIsReadBackFromThePhone() {
        val flags = MapFlags()
        RoomsOpenStore(flags).open = true
        // A new screen (a new store over the same storage) reads it back.
        assertTrue(RoomsOpenStore(flags).open)
        RoomsOpenStore(flags).open = false
        assertFalse(RoomsOpenStore(flags).open)
    }

    @Test fun theLinkIsWordedAsTheWebWordsIt() {
        assertEquals("Show rooms", RoomsToggle.label(open = false, playingCount = 0))
        assertEquals("Show rooms (2 playing)", RoomsToggle.label(open = false, playingCount = 2))
        assertEquals("Show rooms (1 playing)", RoomsToggle.label(open = false, playingCount = 1))
        assertEquals("Hide rooms", RoomsToggle.label(open = true, playingCount = 0))
        assertEquals("Hide rooms", RoomsToggle.label(open = true, playingCount = 3))
    }

    @Test fun thePlayingCountIsTheRoomsTheNowPlayingReadSaysArePlaying() {
        // now-playing rows as the core sends them (domovoi music routes).
        val rows = Json { ignoreUnknownKeys = true }.decodeFromString<List<NowPlayingRoom>>(
            """[
              {"room_id": "kitchen", "state": "play", "elapsed_sec": 12.0, "song": {"title": "A", "artist": "B", "duration_sec": 200.0}},
              {"room_id": "office", "state": "pause", "elapsed_sec": 3.0, "song": {"title": "C", "artist": "D", "duration_sec": 100.0}},
              {"room_id": "den", "state": "play", "elapsed_sec": 1.0, "song": {"title": "E", "artist": "F", "duration_sec": 90.0}},
              {"room_id": "hall", "state": "stop", "elapsed_sec": null, "song": null}
            ]""",
        )
        assertEquals(2, RoomsToggle.playingCount(rows))
        assertEquals("Show rooms (2 playing)", RoomsToggle.label(false, RoomsToggle.playingCount(rows)))
    }

    /** The link as a screen reader meets it: a button, its words, its state. */
    private fun linkAsComposed(open: Boolean, playing: Int): Triple<Role?, String?, String> {
        var role: Role? = null
        var state: String? = null
        var taps = 0
        withHeadlessWindow {
            val tree = NodeTree()
            composeTest(tree) {
                setContent { HeadlessUi { RoomsToggleLink(open, playing, onToggle = { taps++ }) } }
                settle()
                val semantics = tree.nodes().flatMap { elementsOf(it) }.filterIsInstance<SemanticsModifier>()
                    .map { it.semanticsConfiguration }
                role = semantics.firstNotNullOfOrNull { it.getOrNull(SemanticsProperties.Role) }
                state = semantics.firstNotNullOfOrNull { it.getOrNull(SemanticsProperties.StateDescription) }
                tree.buttons().single().click()
            }
        }
        assertEquals("one tap, one toggle", 1, taps)
        return Triple(role, state, RoomsToggle.label(open, playing))
    }

    @Test fun theLinkIsAButtonThatSaysWhetherTheRoomsAreShown() {
        val (closedRole, closedState) = linkAsComposed(open = false, playing = 2)
        assertEquals(Role.Button, closedRole)
        assertEquals("collapsed", closedState)
        val (openRole, openState) = linkAsComposed(open = true, playing = 2)
        assertEquals(Role.Button, openRole)
        assertEquals("expanded", openState)
    }
}
