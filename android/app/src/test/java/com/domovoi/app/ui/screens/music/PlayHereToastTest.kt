package com.domovoi.app.ui.screens.music

import com.domovoi.app.net.ApiException
import com.domovoi.app.player.PlayItem
import com.domovoi.app.testing.CastRig
import com.domovoi.app.testing.awaitUntil
import com.domovoi.app.testing.libraryQueue
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

/**
 * What the Music screen says (2026-10-01): its library "play here" toast
 * names the room it left as paused only once that room's pause happened —
 * the core answers a pause its player never got with a 502, and the toast
 * used to say "paused office" regardless — and a refused cast reads as words,
 * not the server's reply ('cast failed: 502 Bad Gateway: {"detail":...}').
 * Driven against a real PlayerController on [CastRig].
 */
class PlayHereToastTest {
    private val rig = CastRig()
    private val toasts = CopyOnWriteArrayList<String>()
    private val track = PlayItem.fromTrack(301L, "Far Hills", "Handoff Band", "Roads", 240.0)

    @After fun tearDown() = rig.close()

    private fun castingToOffice() {
        rig.player.playItems(libraryQueue("Stone Floor", "Old Barrels"), 0)
        rig.room("office", "play", "Stone Floor", 4.0)
        runBlocking { rig.player.castTo("office") }
    }

    private fun playHere() = playHereAndSay(rig.player, track, "Far Hills", { toasts += it }) { it() }

    /** The first toast, and long enough after it for a second to show. */
    private fun toastsSaid(): List<String> {
        awaitUntil(what = "the toast") { toasts.isNotEmpty() }
        Thread.sleep(400)
        return toasts.toList()
    }

    @Test fun onThePhoneItSaysSoAtOnce() {
        rig.player.playItems(libraryQueue("Stone Floor"), 0)

        playHere()

        assertEquals(listOf("playing \"Far Hills\" on this device"), toastsSaid())
    }

    @Test fun whileCastingItNamesTheRoomOnceItPaused() {
        castingToOffice()

        playHere()

        assertEquals(listOf("playing \"Far Hills\" on this device · paused office"), toastsSaid())
    }

    @Test fun aRoomWhosePauseDidntHappenIsNotCalledPaused() {
        castingToOffice()
        rig.failing["/api/music/pause/office"] = 502

        playHere()

        assertEquals(
            listOf("playing \"Far Hills\" on this device · couldn't pause office, it may still be playing"),
            toastsSaid(),
        )
    }

    @Test fun aRefusedCastIsToastedInWords() {
        val note = castFailure(
            ApiException(502, "502 Bad Gateway: {\"detail\":\"MPD error: No response from server while reading MPD hello\"}"),
            "office",
        )
        assertEquals("couldn't cast to office: its speaker isn't answering (is the office satellite online?)", note)
        assertFalse(note.contains("detail"))
    }
}
