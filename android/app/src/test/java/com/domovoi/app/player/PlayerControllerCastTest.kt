package com.domovoi.app.player

import com.domovoi.app.net.ApiException
import com.domovoi.app.testing.CastRig
import com.domovoi.app.testing.field
import com.domovoi.app.testing.libraryQueue
import com.domovoi.app.testing.phoneSong
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * The cast wiring in [PlayerController], end to end on the JVM: a real
 * controller, a [com.domovoi.app.testing.StatefulExoPlayer] and the web's
 * music routes on a MockWebServer ([CastRig]). Every request and every call
 * on the phone's player lands in one log, tagged with where the controls
 * pointed at that moment, so these tests pin ORDER as well as outcome:
 *
 *  - a cast that sends nothing is refused before anything changes;
 *  - a cast tells the room first; only once the room has taken it does the
 *    phone pause, move its queue to the room's start, and switch target;
 *  - castFrom re-casts from a tapped row; jumpTo never plays the phone
 *    under a room;
 *  - the hand-offs (2026-10-01): room to room starts where the first room
 *    had got to and pauses it after the new one took the queue; back to
 *    this phone pauses the room, follows its track and time, and plays only
 *    if the room was playing and took the pause; "play here" while casting
 *    ends the cast and pauses the room.
 */
class PlayerControllerCastTest {
    private val rig = CastRig()
    private val player get() = rig.player
    private val queue = libraryQueue("Stone Floor", "Old Barrels", "Quiet Jars", "Lantern Hum")

    @After fun tearDown() = rig.close()

    /** The phone playing [items] from [index], [sec] seconds in. */
    private fun playingOnThePhone(items: List<PlayItem> = queue, index: Int = 1, sec: Double = 7.6) {
        player.playItems(items, index)
        rig.exo.positionMs = (sec * 1000).toLong()
        rig.log.clear()
    }

    /** Casting [items] to office, the room now reported at [title]. */
    private fun castingToOffice(state: String = "play", title: String? = "Old Barrels", elapsed: Double = 7.0) {
        playingOnThePhone()
        rig.room("office", state, title, elapsed)
        runBlocking { player.castTo("office") }
        rig.awaitRemote("office")
        rig.log.clear()
    }

    private fun indexOf(prefix: String): Int {
        val i = rig.log.indexOfFirst { it.startsWith(prefix) }
        assertTrue("no \"$prefix\" in ${rig.log}", i >= 0)
        return i
    }

    // ---- this phone -> a room ------------------------------------------------

    @Test fun aQueueWithNothingARoomCanPlayIsRefusedBeforeAnythingChanges() {
        playingOnThePhone(listOf(phoneSong(1, "Pocket Tune"), phoneSong(2, "Desk Hum")), index = 0)

        try {
            runBlocking { player.castTo("office") }
            fail("a phone-only queue was cast")
        } catch (e: PlayerController.NothingToCast) {
            assertTrue(e.message!!, e.message!!.contains("saved on this phone"))
        }

        assertEquals("nothing may be sent or paused", emptyList<String>(), rig.log.toList())
        assertTrue("the phone stopped playing", rig.exo.playing)
        assertEquals(PlayTarget.Local, player.target.value)
    }

    @Test fun aCastTellsTheRoomFirstThenPausesThePhoneMovesItAndSwitches() {
        // A phone-only song is playing, library songs follow: the room
        // starts on the first library song, from its top.
        playingOnThePhone(listOf(phoneSong(9, "Pocket Tune")) + queue.take(2), index = 0, sec = 16.0)

        val outcome = runBlocking { player.castTo("office") } as CastOutcome.ToRoom

        assertEquals(
            listOf(
                """POST /api/music/play-tracks {"room_id":"office","track_ids":[101,102]} [target=phone]""",
                "exo.pause [target=phone]",
                "exo.seekTo(1, 0) [target=phone]",
            ),
            rig.actions(),
        )
        assertEquals("office", rig.roomTarget)
        assertEquals(1, player.index.value)
        assertFalse(rig.exo.playing)
        assertNull(outcome.left)
        // The room is watched from now on.
        rig.awaitLog("GET /api/music/now-playing")
    }

    @Test fun theRoomStartsAtTheTrackAndSecondThePhoneIsAt() {
        playingOnThePhone(index = 1, sec = 7.6)

        runBlocking { player.castTo("office") }

        assertEquals(
            """POST /api/music/play-tracks {"room_id":"office","track_ids":[102,103,104],"start_sec":7} [target=phone]""",
            rig.actions().first(),
        )
        // Already on the room's start: the phone pauses where it is.
        assertEquals(listOf("exo.pause [target=phone]"), rig.actions().drop(1))
    }

    @Test fun aCastTheRoomRefusesLeavesThePhonePlayingAndTheTargetAlone() {
        playingOnThePhone()
        rig.failing["/api/music/play-tracks"] = 502

        try {
            runBlocking { player.castTo("office") }
            fail("a refused cast reported success")
        } catch (e: ApiException) {
            assertEquals(502, e.status)
        }

        assertEquals(1, rig.actions().size) // the POST, and nothing after it
        assertTrue(rig.exo.playing)
        assertEquals(PlayTarget.Local, player.target.value)
        assertNull("a room that refused is being watched", field(player, "remotePollJob"))
    }

    // ---- while casting ---------------------------------------------------------

    @Test fun aTappedQueueRowRestartsTheRoomThereAndPausesNothing() {
        castingToOffice()

        val plan = runBlocking { player.castFrom(3) }

        assertEquals(listOf(104L), plan.trackIds)
        assertEquals(
            listOf(
                """POST /api/music/play-tracks {"room_id":"office","track_ids":[104]} [target=office]""",
                "exo.pause [target=office]",
                "exo.seekTo(3, 0) [target=office]",
            ),
            rig.actions(),
        )
        assertEquals("office", rig.roomTarget)
        assertFalse(rig.exo.playing)
    }

    @Test fun aTappedPhoneOnlyRowIsRefusedByName() {
        playingOnThePhone(queue.take(2) + phoneSong(9, "Pocket Tune"), index = 0)
        rig.room("office", "play", "Stone Floor", 3.0)
        runBlocking { player.castTo("office") }
        rig.log.clear()

        try {
            runBlocking { player.castFrom(2) }
            fail("a phone-only row was cast")
        } catch (e: PlayerController.NothingToCast) {
            assertTrue(e.message!!, e.message!!.contains("Pocket Tune"))
        }
        assertEquals(emptyList<String>(), rig.actions())
    }

    @Test fun jumpToWhileCastingTouchesNeitherPlayer() {
        castingToOffice()

        player.jumpTo(2)

        assertEquals(emptyList<String>(), rig.actions())
        assertFalse(rig.exo.playing)
    }

    // ---- room -> room ------------------------------------------------------------

    @Test fun castingOnToAnotherRoomStartsWhereTheFirstHadGotToThenPausesIt() {
        castingToOffice()
        // office has moved on to Quiet Jars, 42 s in.
        rig.room("office", "play", "Quiet Jars", 42.4)

        val outcome = runBlocking { player.castTo("den") } as CastOutcome.ToRoom

        assertEquals(
            listOf(
                """POST /api/music/play-tracks {"room_id":"den","track_ids":[103,104],"start_sec":42} [target=office]""",
                "exo.pause [target=office]",
                "exo.seekTo(2, 42000) [target=office]",
                "POST /api/music/pause/office [target=den]",
            ),
            rig.actions(),
        )
        assertEquals("den", rig.roomTarget)
        assertEquals("office", outcome.left)
        assertTrue(outcome.leftPaused)
        assertEquals("casting to den · paused office", outcome.note)
        // The plan read office afresh, not the poll's last word.
        assertTrue(rig.log.first().startsWith("GET /api/music/now-playing"))
    }

    @Test fun aRoomThatRefusesTheCastLeavesTheFirstRoomPlaying() {
        castingToOffice()
        rig.failing["/api/music/play-tracks"] = 502

        try {
            runBlocking { player.castTo("den") }
            fail("a refused cast reported success")
        } catch (e: ApiException) {
            assertEquals(502, e.status)
        }

        assertTrue("office was paused for a cast den refused", rig.actions().none { "pause/office" in it })
        assertEquals("office", rig.roomTarget)
    }

    @Test fun aFirstRoomThatWontPauseIsSaidSo() {
        castingToOffice()
        rig.failing["/api/music/pause/office"] = 500

        val outcome = runBlocking { player.castTo("den") }

        assertEquals("den", rig.roomTarget)
        assertTrue(outcome.note, outcome.note.endsWith("couldn't pause office, it may still be playing"))
    }

    // ---- room -> this phone ---------------------------------------------------------

    @Test fun backFromAPlayingRoomPausesItThenPlaysThePhoneWhereTheRoomWas() {
        castingToOffice()
        rig.room("office", "play", "Lantern Hum", 12.5)

        val outcome = runBlocking { player.castTo(null) } as CastOutcome.Here

        assertEquals(
            listOf(
                "POST /api/music/pause/office [target=office]",
                "exo.seekTo(3, 12500) [target=office]",
                "exo.play [target=phone]",
            ),
            rig.actions(),
        )
        assertEquals(PlayTarget.Local, player.target.value)
        assertNull(player.remote.value)
        assertNull("the room is still being watched", field(player, "remotePollJob"))
        assertEquals(3, player.index.value)
        assertTrue(rig.exo.playing)
        assertTrue(outcome.playing)
        assertEquals("playing on this device · paused office", outcome.note)
    }

    @Test fun backFromAPausedRoomLeavesThePhonePausedAndSaysSo() {
        castingToOffice()
        rig.room("office", "pause", "Quiet Jars", 30.0)

        val outcome = runBlocking { player.castTo(null) } as CastOutcome.Here

        assertEquals(
            listOf(
                "POST /api/music/pause/office [target=office]",
                "exo.seekTo(2, 30000) [target=office]",
            ),
            rig.actions(),
        )
        assertFalse(rig.exo.playing)
        assertFalse(outcome.playing)
        assertFalse(outcome.note, outcome.note.contains("playing on"))
        assertEquals(PlayTarget.Local, player.target.value)
    }

    @Test fun aRoomThatWontPauseKeepsThePhoneSilent() {
        castingToOffice()
        rig.room("office", "play", "Quiet Jars", 30.0)
        rig.failing["/api/music/pause/office"] = 500

        val outcome = runBlocking { player.castTo(null) } as CastOutcome.Here

        assertTrue("the phone played over a room still playing", rig.actions().none { it.startsWith("exo.play") })
        assertFalse(outcome.playing)
        assertTrue(outcome.note, outcome.note.contains("couldn't pause office"))
        assertEquals(PlayTarget.Local, player.target.value)
    }

    @Test fun aRoomOnASongNotInTheQueueHandsBackWhereTheCastStarted() {
        castingToOffice()
        rig.room("office", "play", "Somebody Else's Song", 50.0)

        runBlocking { player.castTo(null) }

        // No seek: the phone sits where the cast left it (Old Barrels, 7.6 s).
        assertEquals(
            listOf("POST /api/music/pause/office [target=office]", "exo.play [target=phone]"),
            rig.actions(),
        )
        assertEquals(1, player.index.value)
        assertEquals(7_600L, rig.exo.positionMs)
    }

    @Test fun backToThisDeviceWhenAlreadyHereChangesNothingAndClaimsNothing() {
        playingOnThePhone()
        player.pause()
        rig.log.clear()

        val outcome = runBlocking { player.castTo(null) } as CastOutcome.Here

        assertEquals(emptyList<String>(), rig.log.toList())
        // The old toast said "playing on this device" whatever was true.
        assertFalse(outcome.playing)
        assertEquals("on this device", outcome.note)
    }

    // ---- "play here" while casting ---------------------------------------------------

    @Test fun playHereWhileCastingEndsTheCastAndPausesTheRoom() {
        castingToOffice()

        val left = player.playItems(listOf(phoneSong(9, "Pocket Tune")))

        assertEquals("office", left)
        assertEquals(PlayTarget.Local, player.target.value)
        assertNull(player.remote.value)
        assertNull("the room is still being watched", field(player, "remotePollJob"))
        assertTrue(rig.exo.playing)
        rig.awaitLog("POST /api/music/pause/office")
    }

    // ---- the in-app transport while casting -----------------------------------------

    @Test fun previousAndSeekWhileCastingLeaveThePhoneAlone() {
        castingToOffice()

        player.prev()
        player.seekTo(30.0)

        assertEquals(emptyList<String>(), rig.actions())
    }
}
