package com.domovoi.app.player

import com.domovoi.app.net.ApiException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CastPlanTest {

    private fun lib(id: Long, title: String = "lib $id") =
        PlayItem.fromTrack(id, title, "artist", "album", 200.0)

    private fun phone(id: Long, title: String = "phone $id") =
        PlayItem.fromDeviceAudio(id, title, "artist", null, 180.0, "content://media/external/audio/media/$id", null)

    private fun radio(id: Long) = PlayItem.fromStation(id, "station $id")

    // ── the owner's case: a queue of songs saved on the phone ───────────────

    @Test fun phoneOnlyQueueSendsNothingAndSaysWhy() {
        // 2026-09-30: a song from the on-device list, cast to a room. The old
        // castTo sent nothing and still switched to "casting to <room>".
        val q = listOf(phone(11), phone(12), phone(13))
        val plan = CastPlanner.plan(q, 1, 42.0)
        assertFalse(plan.castable)
        assertEquals(-1, plan.startIndex)
        assertEquals(0, plan.startSec)
        assertEquals(2, plan.phoneOnly)
        val why = CastPlanner.refusal(plan)
        assertNotNull(why)
        assertTrue(why!!, why.contains("this phone"))
        assertTrue(why, why.contains("can't"))
    }

    @Test fun emptyQueueSendsNothing() {
        val plan = CastPlanner.plan(emptyList(), 0, 0.0)
        assertFalse(plan.castable)
        assertNotNull(CastPlanner.refusal(plan))
    }

    @Test fun radioOnlyQueueIsRefusedAsNotInTheLibrary() {
        val plan = CastPlanner.plan(listOf(radio(1)), 0, 0.0)
        assertFalse(plan.castable)
        assertEquals("only library songs can be cast to a room.", CastPlanner.refusal(plan))
    }

    // ── library queues start where the phone is ────────────────────────────

    @Test fun libraryQueueStartsAtTheCurrentTrackAndPosition() {
        // The other half of the bug: the room always started at track one.
        val q = listOf(lib(1), lib(2), lib(3), lib(4))
        val plan = CastPlanner.plan(q, 2, 73.6)
        assertEquals(listOf(3L, 4L), plan.trackIds)
        assertEquals(2, plan.startIndex)
        assertEquals(73, plan.startSec)
        assertNull(CastPlanner.refusal(plan))
        assertEquals("casting to office", CastPlanner.sentNote(plan, "office"))
    }

    @Test fun theFirstSecondsStartTheTrackFromTheTop() {
        val q = listOf(lib(1), lib(2))
        assertEquals(0, CastPlanner.plan(q, 0, 1.9).startSec)
        assertEquals(2, CastPlanner.plan(q, 0, 2.0).startSec)
        assertEquals(0, CastPlanner.plan(q, 0, Double.NaN).startSec)
        assertEquals(0, CastPlanner.plan(q, 0, -5.0).startSec)
    }

    @Test fun outOfRangeIndexIsClamped() {
        val q = listOf(lib(1), lib(2))
        assertEquals(listOf(2L), CastPlanner.plan(q, 9, 0.0).trackIds)
        assertEquals(listOf(1L, 2L), CastPlanner.plan(q, -3, 0.0).trackIds)
    }

    // ── mixed queues: the subset the server has ────────────────────────────

    @Test fun mixedQueueSendsTheLibrarySongsAndCountsWhatStays() {
        val q = listOf(lib(1), phone(50), lib(2), radio(7), phone(51), lib(3))
        val plan = CastPlanner.plan(q, 0, 30.0)
        assertEquals(listOf(1L, 2L, 3L), plan.trackIds)
        assertEquals(0, plan.startIndex)
        assertEquals(30, plan.startSec)
        assertEquals(2, plan.phoneOnly)
        assertEquals(1, plan.notInLibrary)
        assertEquals(
            "casting 3 songs to den · left out 2 only on this phone, 1 not in the library",
            CastPlanner.sentNote(plan, "den"),
        )
    }

    @Test fun aPhoneSongPlayingNowStartsTheRoomOnTheNextLibrarySongFromTheTop() {
        // The position belongs to the phone song, not to the song the room
        // starts on.
        val q = listOf(lib(1), phone(50), lib(2), lib(3))
        val plan = CastPlanner.plan(q, 1, 95.0)
        assertEquals(listOf(2L, 3L), plan.trackIds)
        assertEquals(2, plan.startIndex)
        assertEquals(0, plan.startSec)
        assertEquals(1, plan.phoneOnly)
        assertEquals("casting 2 songs to den · left out 1 only on this phone", CastPlanner.sentNote(plan, "den"))
    }

    @Test fun librarySongsBeforeTheCurrentOneAreNotSent() {
        // Already heard: the room does not replay them, and a phone song
        // with only history behind it has nothing to send.
        val q = listOf(lib(1), lib(2), phone(50))
        val plan = CastPlanner.plan(q, 2, 10.0)
        assertFalse(plan.castable)
        assertTrue(CastPlanner.refusal(plan)!!.contains("this phone"))
    }

    @Test fun phoneAndRadioLeftWithNothingCastableExplainBoth() {
        val q = listOf(phone(50), radio(1))
        val why = CastPlanner.refusal(CastPlanner.plan(q, 0, 0.0))!!
        assertTrue(why, why.contains("this phone"))
        assertTrue(why, why.contains("library"))
    }

    @Test fun neverMoreIdsThanPlayTracksTakes() {
        val q = (1L..(CastPlanner.MAX_IDS + 40L)).map { lib(it) }
        val plan = CastPlanner.plan(q, 0, 0.0)
        assertEquals(CastPlanner.MAX_IDS, plan.trackIds.size)
        assertEquals(1L, plan.trackIds.first())
    }

    @Test fun oneSongNoteIsSingular() {
        val plan = CastPlanner.plan(listOf(phone(50), lib(9)), 0, 0.0)
        assertEquals("casting 1 song to den · left out 1 only on this phone", CastPlanner.sentNote(plan, "den"))
    }

    // ── a tapped row while casting ──────────────────────────────────────────

    @Test fun aTappedPhoneRowIsRefusedByName() {
        assertNull(CastPlanner.refusal(lib(1, "Alpha")))
        val why = CastPlanner.refusal(phone(50, "Voice memo"))!!
        assertTrue(why, why.contains("\"Voice memo\""))
        assertTrue(why, why.contains("this phone"))
        assertTrue(CastPlanner.refusal(radio(3))!!.contains("library"))
    }

    // ── casting from one room to another ───────────────────────────────────

    @Test fun followRoomFindsWhereTheRoomHasGot() {
        val q = listOf(lib(1, "One"), lib(2, "Two"), lib(3, "Three"), lib(4, "Two"))
        // The phone stopped at index 0; the room has moved on to "Two".
        assertEquals(1, CastPlanner.followRoom(q, 0, "Two"))
        // Never backwards past where the phone is.
        assertEquals(3, CastPlanner.followRoom(q, 2, "Two"))
        // Unknown or missing titles leave the phone's own index.
        assertEquals(2, CastPlanner.followRoom(q, 2, null))
        assertEquals(2, CastPlanner.followRoom(q, 2, "Elsewhere"))
        assertEquals(0, CastPlanner.followRoom(emptyList(), 0, "Two"))
    }

    // ── what the player would send (PlayerController.castPlan) ─────────────

    private val office = PlayTarget.Room("office")

    private fun playing(room: String, title: String?, elapsed: Double) =
        RemoteNowPlaying(room, "play", title, "artist", elapsed, 200.0)

    @Test fun onThisDeviceTheCastStartsAtThePhonesItemAndPosition() {
        val q = listOf(lib(1, "One"), lib(2, "Two"), lib(3, "Three"))
        val plan = CastPlanner.planFor(q, 1, 42.7, PlayTarget.Local, playing("office", "Three", 9.0))
        assertEquals(listOf(2L, 3L), plan.trackIds)
        assertEquals(42, plan.startSec)
    }

    @Test fun roomToRoomStartsWhereTheRoomHasGotAtItsElapsedTime() {
        // 2026-09-30 verify: cast to den, den moved on to "Three" and sits
        // 37 s in; casting on to office starts office there, not at "One"
        // where the phone stopped, and not at the phone's stale position.
        val q = listOf(lib(1, "One"), lib(2, "Two"), lib(3, "Three"), lib(4, "Four"))
        val plan = CastPlanner.planFor(q, 0, 120.0, office, playing("office", "Three", 37.4))
        assertEquals(listOf(3L, 4L), plan.trackIds)
        assertEquals(2, plan.startIndex)
        assertEquals(37, plan.startSec)
    }

    @Test fun aRoomStillOnThePhonesItemCarriesTheRoomsPositionNotThePhones() {
        val q = listOf(lib(1, "One"), lib(2, "Two"))
        val plan = CastPlanner.planFor(q, 0, 120.0, office, playing("office", "One", 12.0))
        assertEquals(listOf(1L, 2L), plan.trackIds)
        assertEquals(12, plan.startSec)
    }

    @Test fun whileCastingAnUnknownRoomTitleStartsThePhonesItemFromTheTop() {
        val q = listOf(lib(1, "One"), lib(2, "Two"))
        // Nothing polled yet, a title not in the queue, or a reading for
        // another room: the phone's index, and never its stale position.
        for (remote in listOf(null, playing("office", "Elsewhere", 50.0), playing("den", "Two", 50.0))) {
            val plan = CastPlanner.planFor(q, 0, 120.0, office, remote)
            assertEquals(listOf(1L, 2L), plan.trackIds)
            assertEquals(0, plan.startSec)
        }
    }

    @Test fun followRoomSkipsPhoneSongsWithTheSameTitle() {
        val q = listOf(phone(50, "Same"), lib(2, "Same"))
        assertEquals(1, CastPlanner.followRoom(q, 0, "Same"))
    }

    // ── coming back to this phone (PlayerController.castTo(null)) ──────────

    @Test fun handBackPicksUpOnTheRoomsTrackAtTheRoomsTime() {
        val q = listOf(lib(1, "One"), lib(2, "Two"), lib(3, "Three"))
        assertEquals(HandBack(2, 61.5), CastPlanner.handBack(q, 0, playing("office", "Three", 61.5)))
        // Still on the track the cast started.
        assertEquals(HandBack(0, 4.0), CastPlanner.handBack(q, 0, playing("office", "One", 4.0)))
    }

    @Test fun handBackWithoutAUsableReadingStaysWhereThePhoneWas() {
        val q = listOf(lib(1, "One"), lib(2, "Two"))
        // Nothing read, a song that isn't in the queue, a stopped room (no
        // song), or a title only a phone song carries.
        for (remote in listOf(
            null,
            playing("office", "Elsewhere", 50.0),
            RemoteNowPlaying("office", "stop", null, null, 0.0, null),
        )) {
            assertEquals(HandBack(1, null), CastPlanner.handBack(q, 1, remote))
        }
        assertEquals(
            HandBack(0, null),
            CastPlanner.handBack(listOf(phone(9, "Two"), lib(2, "One")), 0, playing("office", "Two", 9.0)),
        )
        assertEquals(HandBack(3, null), CastPlanner.handBack(emptyList(), 3, playing("office", "One", 9.0)))
    }

    // ── what the toast says (CastOutcome.note) ─────────────────────────────

    @Test fun theToastClaimsPlaybackOnlyWhenThePhoneIsPlaying() {
        val playing = CastOutcome.Here(left = "office", playing = true, leftPaused = true, leftWasPlaying = true)
        assertEquals("playing on this device · paused office", playing.note)
        for (silent in listOf(
            CastOutcome.Here(left = "office", playing = false, leftPaused = true, leftWasPlaying = false),
            CastOutcome.Here(left = "office", playing = false, leftPaused = false, leftWasPlaying = true),
            CastOutcome.Here(left = "office", playing = false, leftPaused = true, leftWasPlaying = true, queued = false),
            CastOutcome.Here(left = null, playing = false),
        )) {
            assertFalse(silent.note, silent.note.contains("playing on"))
        }
        assertTrue(
            CastOutcome.Here(left = "office", playing = false, leftPaused = false).note
                .contains("couldn't pause office"),
        )
    }

    @Test fun aRoomToRoomToastNamesTheRoomItLeft() {
        val plan = CastPlanner.plan(listOf(lib(1)), 0, 0.0)
        assertEquals("casting to den", CastOutcome.ToRoom(plan, "den").note)
        assertEquals("casting to den · paused office", CastOutcome.ToRoom(plan, "den", "office", true).note)
        assertEquals(
            "casting to den · couldn't pause office, it may still be playing",
            CastOutcome.ToRoom(plan, "den", "office", false).note,
        )
    }

    @Test fun aRoomThatWaitsPausedIsSaidToBePaused() {
        val plan = CastPlanner.plan(listOf(lib(1)), 0, 0.0)
        assertEquals("casting to den, paused", CastOutcome.ToRoom(plan, "den", paused = true).note)
        val leftOut = CastPlanner.plan(listOf(lib(1), lib(2), phone(3)), 0, 0.0)
        assertEquals(
            "casting 2 songs to den, paused · left out 1 only on this phone",
            CastPlanner.sentNote(leftOut, "den", paused = true),
        )
    }

    @Test fun aCastAPlayHereBeatSaysWhatBecameOfTheRoom() {
        assertEquals("playing on this device", CastOutcome.Superseded(null).note)
        assertEquals("didn't cast to den · playing on this device instead", CastOutcome.Superseded("den").note)
        assertEquals(
            "cast to den cancelled · playing on this device instead",
            CastOutcome.Superseded("den", sent = true, undone = true).note,
        )
        assertEquals(
            "cast to den cancelled, but den couldn't be paused, it may be playing",
            CastOutcome.Superseded("den", sent = true, undone = false).note,
        )
    }

    @Test fun aPlayHereToastNamesTheRoomOnlyOnceItPaused() {
        assertEquals("playing \"X\" on this device · paused office",
            CastPlanner.playHereNote("playing \"X\" on this device", "office", true))
        assertEquals("playing \"X\" on this device · couldn't pause office, it may still be playing",
            CastPlanner.playHereNote("playing \"X\" on this device", "office", false))
    }

    // ── a refused cast in words, never the server's reply ─────────────────

    @Test fun aRefusedCastIsSaidPlainly() {
        // 2026-10-01: the toast read 'cast failed: 502 Bad Gateway:
        // {"detail":"MPD error: No response from server while reading MPD hello"}'.
        val body = "{\"failed\":\"music_player\",\"detail\":\"the music player for office on the domovoi server isn't answering\"}"
        val mpdDown = ApiException(502, "502 Bad Gateway: $body", body = body)
        val note = CastPlanner.failureNote(mpdDown, "office")
        assertEquals(
            "couldn't cast to office: its music player on the domovoi server isn't answering " +
                "(try again in a minute; if it keeps failing, restart the domovoi)",
            note,
        )
        for (raw in listOf("{", "detail", "502", "MPD", "Bad Gateway", "failed")) assertFalse(note, note.contains(raw))

        assertEquals(
            "couldn't cast to den: none of these songs are in the library now (try a library rescan)",
            CastPlanner.failureNote(ApiException(404, "404 Not Found: {}"), "den"),
        )
        assertEquals(
            "couldn't cast to den: this phone needs pairing with the domovoi again",
            CastPlanner.failureNote(ApiException(401, "401", deviceTokenRequired = true), "den"),
        )
        assertEquals("couldn't cast to den (the domovoi said 409)",
            CastPlanner.failureNote(ApiException(409, "409 Conflict: {\"detail\":\"x\"}"), "den"))
        assertEquals("couldn't cast to den: the domovoi can't be reached (offline?)",
            CastPlanner.failureNote(java.io.IOException("timeout"), "den"))
        assertEquals("couldn't cast to den", CastPlanner.failureNote(IllegalStateException("boom"), "den"))
        assertEquals("couldn't switch back to this device", CastPlanner.failureNote(mpdDown, null))
        // A refusal already says why, in words.
        assertEquals("only library songs", CastPlanner.failureNote(PlayerController.NothingToCast("only library songs"), "den"))
    }

    // ── which part failed: the server's music player, or the satellite ─────

    @Test fun aCastTheServersMusicPlayerFailedNeverBlamesTheSatellite() {
        // ft, 2026-10-01: office's MPD (on the server) was frozen while the
        // office satellite was connected; the toast asked whether the
        // satellite was online.
        val note = CastPlanner.failureNote(
            ApiException(502, "502 Bad Gateway: {\"failed\":\"music_player\",\"detail\":\"x\"}"), "office",
        )
        assertTrue(note, note.contains("its music player on the domovoi server isn't answering"))
        assertFalse(note, note.contains("satellite"))
    }

    @Test fun aCastWithNoSatelliteYetNamesTheSatellite() {
        val body = "{\"failed\":\"satellite\",\"detail\":\"no satellite has connected to the domovoi yet\"}"
        assertEquals(
            "couldn't cast to den: no satellite has connected to the domovoi yet, so there's no speaker to play on",
            CastPlanner.failureNote(ApiException(503, "503 Service Unavailable: $body", body = body), "den"),
        )
    }

    @Test fun aServerThatNamesNoPartGetsNoGuess() {
        // An older core ("MPD error: ..."), or the web without its core
        // ("domovoi unreachable"): no part named, so no hint at one.
        for (detail in listOf("MPD error: No response from server while reading MPD hello", "domovoi unreachable")) {
            val body = "{\"detail\":\"$detail\"}"
            val note = CastPlanner.failureNote(ApiException(502, "502 Bad Gateway: $body", body = body), "office")
            assertEquals("couldn't cast to office: the domovoi couldn't start it (it said 502)", note)
        }
    }

    @Test fun thePartIsReadFromTheWholeBodyOrTheMessage() {
        // The message keeps 200 characters of the body; the part is read
        // from the whole body when there is one.
        val long = "{\"detail\":\"${"x".repeat(300)}\",\"failed\":\"music_player\"}"
        assertEquals("music_player", ApiException(502, "502 Bad Gateway: ${long.take(200)}", body = long).failedPart)
        assertEquals("satellite", ApiException(503, "503 X: {\"failed\":\"satellite\"}").failedPart)
        assertEquals(null, ApiException(502, "502 Bad Gateway: <html>oops</html>", body = "<html>oops</html>").failedPart)
        assertEquals(null, ApiException(502, "502 Bad Gateway: {\"failed\":7}").failedPart)
        assertEquals(null, ApiException(502, "no body at all").failedPart)
    }
}
