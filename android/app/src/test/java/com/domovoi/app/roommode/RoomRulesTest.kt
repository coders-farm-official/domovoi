package com.domovoi.app.roommode

import com.domovoi.app.roommode.RoomStatusText.Ring
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The room's small rules: what the core's refusals mean and when it asks
 * again, what the ring shows, and that the app pins the same model digests
 * as the core.
 */
class RoomRulesTest {
    @Test
    fun parkingShowsTheCodeOnFile() {
        val v = RoomSessionRules.verdict("awaiting_approval", "305117", current = "111222")
        assertEquals(RoomEngine.Link.AwaitingApproval("305117"), v.link)
        assertEquals("305117", v.code)
        assertEquals("awaiting_approval", v.outcome)
        // A code that is not digits is not kept: the room's own stays.
        val w = RoomSessionRules.verdict("awaiting_approval", "12ab56", current = "111222")
        assertEquals(RoomEngine.Link.AwaitingApproval("111222"), w.link)
        assertNull(w.code)
    }

    @Test
    fun refusalsAreExplainedAndRetriedSlowly() {
        val rejected = RoomSessionRules.verdict("pairing_rejected", null, null)
        assertTrue(rejected.link is RoomEngine.Link.Rejected)
        assertTrue((rejected.link as RoomEngine.Link.Rejected).reason.contains("reset its pairing"))
        assertTrue(RoomSessionRules.verdict("approval_conflict", null, null).link is RoomEngine.Link.Conflict)
        assertEquals(RoomSessionRules.APPROVAL_RETRY_MS, RoomSessionRules.retryAfterMs("awaiting_approval", 1_000))
        assertEquals(RoomSessionRules.REFUSED_RETRY_MS, RoomSessionRules.retryAfterMs("pairing_rejected", 1_000))
        assertEquals(8_000, RoomSessionRules.retryAfterMs("closed", 8_000))
        // The core allows 20 approval requests per address in 10 minutes.
        assertTrue(600_000 / RoomSessionRules.APPROVAL_RETRY_MS < 20)
    }

    @Test
    fun anErrorDuringATurnIsNotAboutTheConnection() {
        val v = RoomSessionRules.verdict(null, null, null)
        assertNull(v.link)
        assertNull(v.outcome)
    }

    private fun status(
        link: RoomEngine.Link = RoomEngine.Link.Ready,
        phase: RoomEngine.Phase = RoomEngine.Phase.LISTENING,
        muted: Boolean = false,
        blocked: Boolean = false,
    ) = RoomEngine.Status(link = link, phase = phase, muted = muted, micBlocked = blocked, wakeReady = true)

    @Test
    fun theRingShowsTheOwnersSevenStates() {
        assertEquals(Ring.IDLE, RoomStatusText.ring(status(), running = true))
        assertEquals(Ring.LISTENING, RoomStatusText.ring(status(phase = RoomEngine.Phase.CAPTURING), true))
        assertEquals(Ring.THINKING, RoomStatusText.ring(status(phase = RoomEngine.Phase.THINKING), true))
        assertEquals(Ring.SPEAKING, RoomStatusText.ring(status(phase = RoomEngine.Phase.SPEAKING), true))
        assertEquals(Ring.MUTED, RoomStatusText.ring(status(muted = true, blocked = true), true))
        assertEquals(Ring.MIC_BLOCKED, RoomStatusText.ring(status(blocked = true, link = RoomEngine.Link.Connecting), true))
        assertEquals(Ring.OFFLINE, RoomStatusText.ring(status(link = RoomEngine.Link.Offline("x")), true))
        assertEquals(Ring.OFFLINE, RoomStatusText.ring(status(), running = false))
        assertEquals(Ring.OFFLINE, RoomStatusText.ring(null, running = true))
    }

    @Test
    fun theWordsUnderTheRing() {
        assertEquals("Say “Hey Jarvis”", RoomStatusText.detail(status(), true, "hey_jarvis"))
        assertTrue(RoomStatusText.detail(status(link = RoomEngine.Link.AwaitingApproval("305117")), true, "x")!!.contains("approve"))
        assertTrue(RoomStatusText.detail(status(blocked = true), true, "x")!!.contains("privacy toggle"))
        assertEquals("waiting for approval · code 305117", RoomStatusText.notificationLine(status(link = RoomEngine.Link.AwaitingApproval("305117"))))
        assertEquals("listening", RoomStatusText.notificationLine(status(phase = RoomEngine.Phase.CAPTURING)))
    }

    @Test
    fun theAppPinsTheCoresModelDigests() {
        val repo = File(System.getProperty("domovoi.repo.dir") ?: "../..")
        val fetchers = File(repo, "domovoi/satellite_media/fetchers.py").readText()
        val row = Regex("\"([a-z0-9_.]+\\.onnx)\": \\(\"([0-9a-f]{64})\", (\\d+)\\)")
        val core = row.findAll(fetchers).associate { it.groupValues[1] to (it.groupValues[2] to it.groupValues[3].toLong()) }
        assertEquals(core, RoomModels.PINS)
        for (name in RoomModels.BASE + "hey_jarvis_v0.1.onnx") assertTrue(name, name in RoomModels.PINS)
        assertEquals("hey_jarvis_v0.1.onnx", RoomModels.wakeFileName("hey_jarvis"))
        assertEquals("hey_house.onnx", RoomModels.wakeFileName("hey_house"))
        assertTrue(RoomModels.isSlug("hey_house"))
        assertTrue(!RoomModels.isSlug("../x") && !RoomModels.isSlug("Hey") && !RoomModels.isSlug(null))
    }

    @Test
    fun soundPathsStayInsideTheCache() {
        assertTrue(RoomSounds.safeRel("greetings/greet_ab.mp3"))
        for (bad in listOf("", "/etc/x.mp3", "../x.mp3", "a/../b.mp3", "a\\b.mp3", "a//b.mp3")) {
            assertTrue(bad, !RoomSounds.safeRel(bad))
        }
    }

    @Test
    fun aRoomMirrorsItsOwnVoicesClips() {
        // The server's own voice: no query, the record the room always kept.
        assertEquals("", RoomSounds.voiceQuery(null))
        assertEquals("", RoomSounds.voiceQuery(""))
        assertEquals("satellite-sounds@", RoomSounds.soundsRecord(null))
        // A voice the server keeps for the room: its list, its own serial
        // record (the Pi's sounds_record), the name encoded.
        assertEquals("?voice=Ryan", RoomSounds.voiceQuery("Ryan"))
        assertEquals("?voice=Lessac%20Low%26x", RoomSounds.voiceQuery("Lessac Low&x"))
        assertEquals("satellite-sounds@Ryan", RoomSounds.soundsRecord("Ryan"))
    }
}
