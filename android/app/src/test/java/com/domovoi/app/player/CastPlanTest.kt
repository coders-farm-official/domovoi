package com.domovoi.app.player

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

    @Test fun followRoomSkipsPhoneSongsWithTheSameTitle() {
        val q = listOf(phone(50, "Same"), lib(2, "Same"))
        assertEquals(1, CastPlanner.followRoom(q, 0, "Same"))
    }
}
