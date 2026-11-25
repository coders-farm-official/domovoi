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

    // One Chat: the setup's explainer names chats, never "the assistant".
    @Test
    fun theExplainerSaysChatsAreLocked() {
        val stops = EXPLAINER.toMap().getValue("What stops working")
        assertTrue(stops, stops.contains("no files, browsing or chats, on the home server or on this phone"))
        assertTrue(EXPLAINER.none { (t, b) -> "assistant" in t || "assistant" in b })
    }

    // ── household words (1.5.0): a room is a place, this phone is in it ──

    private val kitchen = RoomProtocol.RoomPlace("kitchen", "Kitchen", shared = false)

    @Test
    fun theScreenSaysTheRoomsNameNeverItsId() {
        // The server's name wins, then the kept one, then the room code's.
        assertEquals("Kitchen", RoomStatusText.roomName(status().copy(place = kitchen), "Den", "den", "Kitchen 2"))
        assertEquals("Den", RoomStatusText.roomName(status(), "Den", "den", "Kitchen 2"))
        assertEquals("Kitchen 2", RoomStatusText.roomName(null, null, null, "Kitchen 2"))
        assertEquals("Kitchen 2", RoomStatusText.roomName(null, " ", null, "Kitchen 2"))
        assertNull(RoomStatusText.roomName(null, null, null, null))
    }

    /** satellite-fixtures.json rooms.name_about: the core sends a null name
     *  for a room it could not read. */
    @Test
    fun anUnnamedRoomKeepsTheLastNameForTheSameRoomOnly() {
        val unnamed = kitchen.copy(name = null)
        // The same room: the name last shown for it.
        assertEquals("Kitchen", RoomStatusText.roomName(status().copy(place = unnamed), "Kitchen", "kitchen", "Kitchen 2"))
        // Another room (a move), a name kept before 1.5.0 (no room), or none:
        // no name, and never the id or the room code's own name.
        val den = RoomProtocol.RoomPlace("den", null, shared = false)
        assertNull(RoomStatusText.roomName(status().copy(place = den), "Kitchen", "kitchen", "Kitchen 2"))
        assertNull(RoomStatusText.roomName(status().copy(place = unnamed), "Kitchen", null, "Kitchen 2"))
        assertNull(RoomStatusText.roomName(status().copy(place = unnamed), null, null, "Kitchen 2"))
        // The words then name no room.
        assertEquals("This phone is in this room", RoomStatusText.whereLine(status().copy(place = den), null, approved = true))
        assertEquals(
            "This phone is in this room, with other devices",
            RoomStatusText.whereLine(status().copy(place = den.copy(shared = true)), null, approved = true),
        )
        assertEquals(
            "This phone will join this room",
            RoomStatusText.whereLine(status(link = RoomEngine.Link.AwaitingApproval("305117")), null, approved = false),
        )
        assertEquals("This room", RoomStatusText.title(null))
        assertEquals("Kitchen", RoomStatusText.title("Kitchen"))
        assertEquals("this room", RoomStatusText.theRoom(null))
        assertEquals("this room", RoomStatusText.theRoom(" "))
    }

    @Test
    fun noNameNeverOverwritesTheKeptOne() {
        // A name is kept with its room.
        assertEquals("kitchen" to "Kitchen", RoomStore.nameToKeep(kitchen, null, null))
        assertEquals("den" to "Den", RoomStore.nameToKeep(RoomProtocol.RoomPlace("den", "Den", false), "Kitchen", "kitchen"))
        // The same name for the same room: nothing to write.
        assertNull(RoomStore.nameToKeep(kitchen, "Kitchen", "kitchen"))
        // The same name, now for another room (or a pre-1.5.0 kept name): kept with it.
        assertEquals("kitchen" to "Kitchen", RoomStore.nameToKeep(kitchen, "Kitchen", null))
        // No name: nothing written, the last name stays.
        assertNull(RoomStore.nameToKeep(kitchen.copy(name = null), "Kitchen", "kitchen"))
        assertNull(RoomStore.nameToKeep(RoomProtocol.RoomPlace("den", null, false), "Kitchen", "kitchen"))
        assertNull(RoomStore.nameToKeep(kitchen.copy(name = " "), "Kitchen", "kitchen"))
    }

    @Test
    fun thisPhoneIsInTheKitchen() {
        val here = status().copy(place = kitchen)
        assertEquals("This phone is in the Kitchen", RoomStatusText.whereLine(here, "Kitchen", approved = true))
        assertEquals(
            "This phone is in the Kitchen, with other devices",
            RoomStatusText.whereLine(status().copy(place = kitchen.copy(shared = true)), "Kitchen", approved = true),
        )
        // Not yet approved: where it is going (the room code's `in=`).
        val parked = status(link = RoomEngine.Link.AwaitingApproval("305117"))
        assertEquals("This phone will join the Kitchen", RoomStatusText.whereLine(parked, "Kitchen", approved = false))
        assertEquals("This phone is in the Kitchen", RoomStatusText.whereLine(null, "Kitchen", approved = true))
        assertEquals("This phone will join the Kitchen.", RoomStatusText.joinLine("Kitchen"))
        // A name that already reads as one keeps its own words.
        assertEquals("The Den", RoomStatusText.theRoom("The Den"))
        assertEquals("Alex's room", RoomStatusText.theRoom("Alex's room"))
        assertEquals("the Kids' Room", RoomStatusText.theRoom("Kids' Room"))
        assertEquals("the Theatre", RoomStatusText.theRoom("Theatre"))
    }

    @Test
    fun roomModeIsOfferedAsThisPhoneInARoom() {
        assertEquals("Use this phone in a room", RoomStatusText.ENTRY_TITLE)
        val waiting = RoomStatusText.detail(status(link = RoomEngine.Link.AwaitingApproval("305117")), true, "hey_jarvis")!!
        assertTrue(waiting, "approve this phone" in waiting)
    }

    /**
     * The phone never says "satellite" (the dashboard's admin pages do): no
     * text in room mode's sources with a space in it names one. Protocol
     * names (`satellite_id`, `satellite-sounds`) are words, not sentences.
     */
    @Test
    fun roomModeNeverSaysSatellite() {
        val repo = File(System.getProperty("domovoi.repo.dir") ?: "../..")
        val dir = File(repo, "android/app/src/main/java/com/domovoi/app/roommode")
        val literal = Regex("\"((?:[^\"\\\\]|\\\\.)*)\"")
        val said = mutableListOf<String>()
        for (f in dir.listFiles { x -> x.name.endsWith(".kt") }.orEmpty()) {
            for (line in f.readLines()) {
                val code = line.trimStart()
                if (code.startsWith("*") || code.startsWith("/*") || code.startsWith("//")) continue
                for (m in literal.findAll(code.substringBefore(" // "))) {
                    // A template's code (`${f.satelliteId}`) is not words.
                    val text = m.groupValues[1].replace(Regex("\\$\\{[^}]*\\}"), "")
                    if (' ' in text && "satellite" in text.lowercase()) said += "${f.name}: $text"
                }
            }
        }
        assertTrue(dir.path, dir.isDirectory)
        assertEquals(emptyList<String>(), said)
    }
}
