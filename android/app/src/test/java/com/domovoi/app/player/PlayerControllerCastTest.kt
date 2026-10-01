package com.domovoi.app.player

import com.domovoi.app.net.ApiException
import com.domovoi.app.testing.CastRig
import com.domovoi.app.testing.field
import com.domovoi.app.testing.libraryQueue
import com.domovoi.app.testing.phoneSong
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
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
 *  - 2026-10-01 (wf/music-remote): a "play here" while a change of target
 *    is still on its way wins (the change stops; a room that took the queue
 *    is paused again); a cast outlives the menu that started it; a cast
 *    from a paused phone or room starts the room paused; a pause the room
 *    says didn't happen (non-2xx or ok:false) is "couldn't pause"; previous
 *    is the room's.
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

    /** Suspend (not block: the cast runs on this same loop) until [prefix] is logged. */
    private suspend fun onTheWire(prefix: String) {
        val until = System.currentTimeMillis() + 5_000
        while (rig.log.none { it.startsWith(prefix) }) {
            assertTrue("nothing starting with \"$prefix\" in ${rig.log}", System.currentTimeMillis() < until)
            delay(10)
        }
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

        val plan = (runBlocking { player.castFrom(3) } as CastOutcome.ToRoom).plan

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

    @Test fun pickingTheRoomAlreadyCastToRestartsItThereAndPausesNothing() {
        castingToOffice()
        rig.room("office", "play", "Quiet Jars", 20.0)

        val outcome = runBlocking { player.castTo("office") } as CastOutcome.ToRoom

        assertEquals(
            listOf(
                """POST /api/music/play-tracks {"room_id":"office","track_ids":[103,104],"start_sec":20} [target=office]""",
                "exo.pause [target=office]",
                "exo.seekTo(2, 20000) [target=office]",
            ),
            rig.actions(),
        )
        assertEquals("office", rig.roomTarget)
        assertNull("the room just started was named as left", outcome.left)
        assertEquals("casting to office", outcome.note)
    }

    // ---- a second pick while a cast is on its way ---------------------------------

    @Test fun aSecondRoomPickedWhileTheFirstCastIsOnItsWayWaitsThenPausesTheFirst() {
        // Picked office, then den before office had answered: before
        // 2026-10-01 both casts started from the phone, so both rooms played
        // and office was never paused or watched again.
        playingOnThePhone()
        rig.room("office", "play", "Old Barrels", 9.0)
        rig.delays["/api/music/play-tracks"] = 400

        val (first, second) = runBlocking {
            val office = async { player.castTo("office") }
            onTheWire("POST /api/music/play-tracks")
            val den = async { player.castTo("den") }
            office.await() as CastOutcome.ToRoom to den.await() as CastOutcome.ToRoom
        }

        assertEquals(
            listOf(
                """POST /api/music/play-tracks {"room_id":"office","track_ids":[102,103,104],"start_sec":7} [target=phone]""",
                "exo.pause [target=phone]",
                """POST /api/music/play-tracks {"room_id":"den","track_ids":[102,103,104],"start_sec":9} [target=office]""",
                "exo.pause [target=office]",
                "POST /api/music/pause/office [target=den]",
            ),
            rig.actions(),
        )
        assertEquals("den", rig.roomTarget)
        assertNull(first.left)
        assertEquals("casting to den · paused office", second.note)
    }

    @Test fun thisDevicePickedWhileACastIsOnItsWayComesBackFromThatRoom() {
        // Before 2026-10-01 "this device" found the target still the phone,
        // said "playing on this device", and the cast then landed anyway.
        playingOnThePhone()
        rig.room("office", "play", "Old Barrels", 9.0)
        rig.delays["/api/music/play-tracks"] = 400

        val back = runBlocking {
            val office = async { player.castTo("office") }
            onTheWire("POST /api/music/play-tracks")
            val here = async { player.castTo(null) }
            office.await()
            here.await() as CastOutcome.Here
        }

        assertEquals(PlayTarget.Local, player.target.value)
        assertEquals("office", back.left)
        assertTrue(rig.actions().any { it.startsWith("POST /api/music/pause/office") })
        assertTrue(rig.exo.playing)
        assertEquals("playing on this device · paused office", back.note)
    }

    @Test fun thisDevicePickedWhileATappedRowIsOnItsWayStillEndsHere() {
        castingToOffice()
        rig.delays["/api/music/play-tracks"] = 400

        runBlocking {
            val row = async { player.castFrom(3) }
            onTheWire("POST /api/music/play-tracks")
            val here = async { player.castTo(null) }
            row.await()
            here.await()
        }

        // The row's cast landed first, then the hand-back: the phone plays
        // and the room is paused, rather than the row's cast landing last
        // and pausing the phone the person had just picked.
        assertEquals(PlayTarget.Local, player.target.value)
        assertTrue(rig.exo.playing)
        assertTrue(indexOf("POST /api/music/pause/office") > indexOf("POST /api/music/play-tracks"))
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
        assertEquals("back on this device, paused · office wasn't playing", outcome.note)
        assertEquals(PlayTarget.Local, player.target.value)
    }

    @Test fun backWhenTheRoomCantBeReadNowFollowsTheLastPoll() {
        castingToOffice(title = "Quiet Jars", elapsed = 30.0)
        rig.failing["/api/music/now-playing"] = 502

        val outcome = runBlocking { player.castTo(null) } as CastOutcome.Here

        assertEquals(
            listOf(
                "POST /api/music/pause/office [target=office]",
                "exo.seekTo(2, 30000) [target=office]",
                "exo.play [target=phone]",
            ),
            rig.actions(),
        )
        assertTrue(outcome.playing)
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

    @Test fun previousWhileCastingIsTheRoomsAndSeekLeavesThePhoneAlone() {
        castingToOffice()

        player.prev()
        rig.awaitLog("POST /api/music/previous/office")
        player.seekTo(30.0)
        Thread.sleep(100)

        assertEquals(listOf("POST /api/music/previous/office [target=office]"), rig.actions())
    }

    // ---- a "play here" while a change of target is on its way (2026-10-01) --------
    // Before, a play here in the 3-5 s a room takes to ready its stream played
    // here, then the cast landed: it paused the phone and moved the target to
    // the room, and the play here was lost.

    @Test fun aPlayHereDuringACastWinsAndTheRoomIsPausedAgain() {
        playingOnThePhone()
        rig.delays["/api/music/play-tracks"] = 400

        val (left, outcome) = runBlocking {
            val cast = async { player.castTo("office") }
            onTheWire("POST /api/music/play-tracks")
            val left = player.playItems(listOf(phoneSong(9, "Pocket Tune")))
            left to cast.await()
        }

        assertNull("nothing was being cast to yet", left)
        assertEquals(CastOutcome.Superseded("office", sent = true, undone = true), outcome)
        assertEquals("cast to office cancelled · playing on this device instead", outcome.note)
        assertEquals(PlayTarget.Local, player.target.value)
        assertTrue("the play here was silenced", rig.exo.playing)
        assertNull("a room nobody casts to is watched", field(player, "remotePollJob"))
        val playHere = indexOf("exo.setMediaItems")
        assertTrue(rig.log.drop(playHere).none { it.startsWith("exo.pause") || it.startsWith("exo.seekTo") })
        assertTrue(indexOf("POST /api/music/pause/office") > playHere)
    }

    @Test fun aPlayHereDuringARoomToRoomCastPausesBothRooms() {
        castingToOffice()
        rig.room("office", "play", "Quiet Jars", 42.0)
        rig.delays["/api/music/play-tracks"] = 400

        val (left, outcome) = runBlocking {
            val cast = async { player.castTo("den") }
            onTheWire("POST /api/music/play-tracks")
            val left = player.playItems(listOf(phoneSong(9, "Pocket Tune")))
            left to cast.await()
        }

        assertEquals("office", left)
        assertEquals(CastOutcome.Superseded("den", sent = true, undone = true), outcome)
        rig.awaitLog("POST /api/music/pause/office")
        assertTrue(indexOf("POST /api/music/pause/den") > indexOf("POST /api/music/play-tracks"))
        assertEquals(PlayTarget.Local, player.target.value)
        assertTrue(rig.exo.playing)
    }

    @Test fun aPlayHereWhileTheOldRoomIsReadSendsTheNewRoomNothing() {
        castingToOffice()
        rig.delays["/api/music/now-playing"] = 400

        val outcome = runBlocking {
            val cast = async { player.castTo("den") }
            delay(100) // den's cast is reading where office is
            player.playItems(queue) // a library queue: a cast that went on would send it
            cast.await()
        }

        assertEquals(CastOutcome.Superseded("den"), outcome)
        assertTrue(rig.actions().toString(), rig.actions().none { it.startsWith("POST /api/music/play-tracks") })
        assertEquals(PlayTarget.Local, player.target.value)
    }

    @Test fun aPickWaitingItsTurnIsNeverSentAfterAPlayHere() {
        playingOnThePhone()
        rig.delays["/api/music/play-tracks"] = 400

        val (office, den) = runBlocking {
            val office = async { player.castTo("office") }
            onTheWire("POST /api/music/play-tracks")
            val den = async { player.castTo("den") }
            delay(50) // den is picked (and waits for the lock) before the play here
            player.playItems(listOf(phoneSong(9, "Pocket Tune")))
            office.await() to den.await()
        }

        assertEquals(CastOutcome.Superseded("office", sent = true, undone = true), office)
        assertEquals(CastOutcome.Superseded("den"), den)
        assertEquals("didn't cast to den · playing on this device instead", den.note)
        assertTrue(rig.actions().none { "\"room_id\":\"den\"" in it })
        assertEquals(PlayTarget.Local, player.target.value)
        assertTrue(rig.exo.playing)
    }

    @Test fun aPlayHereDuringAHandBackIsLeftAlone() {
        castingToOffice()
        rig.room("office", "play", "Quiet Jars", 30.0)
        rig.delays["/api/music/pause/office"] = 400

        val outcome = runBlocking {
            val back = async { player.castTo(null) }
            onTheWire("POST /api/music/pause/office")
            player.playItems(listOf(phoneSong(9, "Pocket Tune")))
            back.await()
        }

        assertEquals(CastOutcome.Superseded(null), outcome)
        // The hand-back would have moved the phone to Quiet Jars, 30 s in.
        val playHere = indexOf("exo.setMediaItems")
        assertTrue(rig.log.drop(playHere).none { it.startsWith("exo.seekTo") || it.startsWith("exo.pause") })
        assertEquals(PlayTarget.Local, player.target.value)
        assertEquals(1, player.queue.value.size)
        assertTrue(rig.exo.playing)
    }

    @Test fun aTappedRowsRecastLosesToAPlayHere() {
        castingToOffice()
        rig.delays["/api/music/play-tracks"] = 400

        val outcome = runBlocking {
            val row = async { player.castFrom(3) }
            onTheWire("POST /api/music/play-tracks")
            player.playItems(listOf(phoneSong(9, "Pocket Tune")))
            row.await()
        }

        assertEquals(CastOutcome.Superseded("office", sent = true, undone = true), outcome)
        assertEquals(PlayTarget.Local, player.target.value)
        assertTrue(rig.exo.playing)
    }

    @Test fun aTappedRowWaitingItsTurnDoesNothingAfterAPlayHere() {
        castingToOffice()
        rig.delays["/api/music/play-tracks"] = 400

        val (cast, row) = runBlocking {
            val cast = async { player.castTo("den") }
            onTheWire("POST /api/music/play-tracks")
            val row = async { player.castFrom(3) }
            delay(50) // the row is tapped (and waits for the lock) before the play here
            player.playItems(queue)
            cast.await() to row.await()
        }

        assertEquals(CastOutcome.Superseded("den", sent = true, undone = true), cast)
        assertEquals(CastOutcome.Superseded(null), row)
        assertEquals(1, rig.actions().count { it.startsWith("POST /api/music/play-tracks") })
        assertEquals(PlayTarget.Local, player.target.value)
    }

    @Test fun aRoomThatTookTheQueueAndWontPauseAgainIsSaidSo() {
        playingOnThePhone()
        rig.delays["/api/music/play-tracks"] = 400
        rig.failing["/api/music/pause/office"] = 502

        val outcome = runBlocking {
            val cast = async { player.castTo("office") }
            onTheWire("POST /api/music/play-tracks")
            player.playItems(listOf(phoneSong(9, "Pocket Tune")))
            cast.await()
        }

        assertEquals(CastOutcome.Superseded("office", sent = true, undone = false), outcome)
        assertEquals("cast to office cancelled, but office couldn't be paused, it may be playing", outcome.note)
    }

    @Test fun aCastOutlivesTheMenuThatStartedIt() {
        // The cast menu's scope ends when the person leaves the player tab —
        // often to "play here" from the library. A cast cut off mid-POST
        // left the room playing with nothing watching or pausing it.
        playingOnThePhone()
        rig.delays["/api/music/play-tracks"] = 400

        runBlocking {
            val menu = launch { player.castTo("office") }
            onTheWire("POST /api/music/play-tracks")
            menu.cancel()
            menu.join()
        }

        assertEquals("office", rig.roomTarget)
        assertFalse(rig.exo.playing)
        rig.awaitLog("GET /api/music/now-playing")
    }

    // ---- a cast from a paused player waits paused ----------------------------------

    @Test fun aCastFromAPausedPhoneStartsTheRoomPausedThere() {
        playingOnThePhone(sec = 151.5)
        player.pause()
        rig.log.clear()

        val outcome = runBlocking { player.castTo("office") } as CastOutcome.ToRoom

        assertEquals(
            """POST /api/music/play-tracks {"room_id":"office","track_ids":[102,103,104],"start_sec":151,"start_paused":true} [target=phone]""",
            rig.actions().first(),
        )
        assertTrue(outcome.paused)
        assertEquals("casting to office, paused", outcome.note)
        assertEquals("office", rig.roomTarget)
    }

    @Test fun aCastFromAPlayingPhoneSaysNothingOfPausing() {
        playingOnThePhone()

        val outcome = runBlocking { player.castTo("office") } as CastOutcome.ToRoom

        assertFalse(rig.actions().first().contains("start_paused"))
        assertFalse(outcome.paused)
    }

    @Test fun aCastFromAPausedRoomStartsTheNextRoomPaused() {
        castingToOffice()
        rig.room("office", "pause", "Quiet Jars", 42.4)

        val outcome = runBlocking { player.castTo("den") } as CastOutcome.ToRoom

        assertTrue(
            rig.actions().toString(),
            rig.actions().contains(
                """POST /api/music/play-tracks {"room_id":"den","track_ids":[103,104],"start_sec":42,"start_paused":true} [target=office]""",
            ),
        )
        assertEquals("casting to den, paused · paused office", outcome.note)
    }

    // ---- a control the room says didn't happen --------------------------------------

    @Test fun aHandBackWhosePauseTheRoomSaysDidntHappenKeepsThePhoneSilent() {
        castingToOffice()
        rig.room("office", "play", "Quiet Jars", 30.0)
        rig.answers["/api/music/pause/office"] = """{"ok":false}"""

        val outcome = runBlocking { player.castTo(null) } as CastOutcome.Here

        assertFalse(outcome.leftPaused)
        assertTrue(rig.actions().none { it.startsWith("exo.play") })
        assertTrue(outcome.note, outcome.note.contains("couldn't pause office"))
    }

    @Test fun aPlayHereHearsWhetherTheRoomItLeftPaused() {
        val heard = java.util.concurrent.CopyOnWriteArrayList<Pair<String, Boolean>>()
        castingToOffice()
        player.playItems(queue) { room, paused -> heard += room to paused }
        com.domovoi.app.testing.awaitUntil(what = "the first pause answered") { heard.size == 1 }

        rig.room("office", "play", "Old Barrels", 7.0)
        runBlocking { player.castTo("office") }
        rig.failing["/api/music/pause/office"] = 502
        player.playItems(listOf(phoneSong(8, "Desk Hum"))) { room, paused -> heard += room to paused }
        com.domovoi.app.testing.awaitUntil(what = "the second pause answered") { heard.size == 2 }

        assertEquals(listOf("office" to true, "office" to false), heard.toList())
    }
}
