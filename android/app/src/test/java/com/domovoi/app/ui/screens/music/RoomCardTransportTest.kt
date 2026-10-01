package com.domovoi.app.ui.screens.music

import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.button
import com.domovoi.app.testing.buttons
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.withHeadlessWindow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Music page's room cards (NowPlayingStrip): each transport button is
 * that room's own control, `POST /api/music/{action}/{room}`. Previous was
 * greyed out and did nothing (emulator, verifier 2026-10-01: a tap sent no
 * request) after rooms had gained a previous and the player tab's previous
 * already drove the room.
 *
 * The strip is composed for real on the JVM ([HeadlessUi]); its buttons are
 * read off their nodes and tapped ([buttons]).
 */
class RoomCardTransportTest {

    private val office = NowPlayingRoom(
        roomId = "office", state = "play", elapsedSec = 42.0,
        song = NowPlayingSong(title = "Long Road", artist = "Handoff Band", durationSec = 240.0),
    )

    /** The strip over [rooms]; the controls its buttons sent, as "action/room". */
    private fun strip(vararg rooms: NowPlayingRoom, taps: NodeTree.() -> Unit): List<String> {
        val sent = mutableListOf<String>()
        val tree = NodeTree()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        NowPlayingStrip(
                            rooms.toList(), tick = 0,
                            onPlayRandom = { sent += "play-random/$it" },
                            transport = RoomTransport { action, room -> sent += "$action/$room" },
                            onFavorite = { sent += "favorite/$it" },
                        )
                    }
                }
                // A playing room's "live" pill pulses for as long as it shows.
                settle(untilQuiet = false, frames = 20)
                tree.taps()
            }
        }
        return sent
    }

    @Test fun previousOnARoomCardIsThatRoomsPrevious() {
        var enabled = false
        val sent = strip(office) {
            val previous = button("previous")
            enabled = previous.enabled
            previous.click()
        }
        assertTrue("the room card's previous is greyed out", enabled)
        assertEquals(listOf("previous/office"), sent)
    }

    @Test fun everyTransportButtonOnACardIsItsRoomsControl() {
        val paused = office.copy(roomId = "den", state = "pause")
        val sent = strip(office, paused) {
            val all = buttons().filter { it.label in setOf("previous", "pause", "resume", "skip", "stop") }
            assertTrue(all.toString(), all.all { it.enabled })
            all.forEach { it.click() }
        }
        assertEquals(
            listOf(
                "previous/office", "pause/office", "skip/office", "stop/office",
                "previous/den", "resume/den", "skip/den", "stop/den",
            ),
            sent,
        )
    }
}
