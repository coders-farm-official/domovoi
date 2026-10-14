package com.domovoi.app.roommode

import com.domovoi.app.assistant.AudioSource
import com.domovoi.app.roommode.RoomLockRules.Exit
import com.domovoi.app.roommode.RoomLockRules.Start
import com.domovoi.app.testing.Bytecode
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The room-mode guards the security review found untested (F8): mute closes
 * the recorder, a sticky restart never takes the mic, the drop-in needs an
 * echo canceller, the normal app redirects to the room screen, and a
 * household phone needs a screen lock (F3). Where the guard sits in an
 * Android entry point that cannot run on the JVM, the test reads the
 * compiled class (testing/Bytecode.kt) to pin that the entry point really
 * calls the tested rule.
 */
class RoomGuardsTest {
    /** A mic that records what was done to it. */
    private class FakeSource : AudioSource {
        var started = 0
        var stopped = 0
        override fun start() {
            started += 1
        }
        override fun read(buffer: ShortArray): Int {
            buffer.fill(200)
            return buffer.size
        }
        override fun stop() {
            stopped += 1
        }
    }

    @After
    fun tearDown() {
        RoomTestHooks.feed = null
    }

    // ── mute ────────────────────────────────────────────────────────────

    @Test
    fun muteClosesTheRecorderAtTheNextReadInWhateverState() {
        val source = FakeSource()
        RoomTestHooks.feed = source
        var muted = false
        val events = mutableListOf<Boolean>()
        val mic = RoomMic(muted = { muted }, onOpenChanged = { events += it })
        mic.open(call = false)
        assertTrue(mic.isOpen)
        val frame = ShortArray(RoomProtocol.FRAME_SAMPLES)
        assertEquals(frame.size, mic.read(frame))
        muted = true
        assertEquals(RoomMic.MUTED, mic.read(frame))
        assertFalse("the recorder is closed", mic.isOpen)
        assertEquals(1, source.stopped)
        assertEquals(listOf(true, false), events)
        // And it does not open again while muted.
        mic.open(call = false)
        assertFalse(mic.isOpen)
        assertEquals(1, source.started)
        assertEquals(RoomMic.MUTED, mic.read(frame))
        muted = false
        mic.open(call = false)
        assertTrue(mic.isOpen)
        assertEquals(listOf(true, false, true), events)
    }

    @Test
    fun theEngineReadsTheMicOnlyThroughTheMuteAwareRoomMic() {
        // The listening thread builds its RoomMic with the mute provider
        // (the two-function constructor), and nothing in the engine opens
        // the platform recorder itself.
        val calls = Bytecode.callers(
            "com.domovoi.app.roommode.RoomEngine", "com/domovoi/app/roommode/RoomMic", setOf("<init>"),
        )
        assertTrue(calls.toString(), calls.any { it.method == "listenLoop" })
        val direct = Bytecode.callers(
            "com.domovoi.app.roommode.RoomEngine", "com/domovoi/app/assistant/AndroidMic", setOf("<init>", "start", "read"),
        )
        assertEquals(emptyList<Bytecode.Call>(), direct)
    }

    // ── the service's start ─────────────────────────────────────────────

    private fun start(
        sticky: Boolean = false, active: Boolean = true, enrolled: Boolean = true, running: Boolean = true,
        mic: Boolean = true, household: Boolean = false, secure: Boolean = false,
    ) = RoomLockRules.start(sticky, active, enrolled, running, mic, household, secure)

    @Test
    fun aStickyRestartNeverTakesTheMicrophone() {
        assertEquals(Start.ASK_FOR_TAP, start(sticky = true))
        assertEquals(Start.NOTHING, start(sticky = true, running = false))
        assertEquals(Start.NOTHING, start(sticky = true, active = false))
        assertEquals(Start.RUN, start())
        assertEquals(Start.NOTHING, start(active = false))
        assertEquals(Start.NOTHING, start(enrolled = false))
        assertEquals(Start.ASK_FOR_TAP, start(mic = false))
        assertEquals(Start.NEEDS_LOCK, start(household = true, secure = false))
        assertEquals(Start.RUN, start(household = true, secure = true))
        val calls = Bytecode.callers("com.domovoi.app.roommode.RoomService", "com/domovoi/app/roommode/RoomLockRules", setOf("start"))
        assertTrue(calls.toString(), calls.any { it.method == "onStartCommand" })
    }

    // ── the screen lock (F3) ────────────────────────────────────────────

    @Test
    fun aHouseholdPhoneNeedsAScreenLockARoomOnlyPhoneDoesNot() {
        assertFalse(RoomLockRules.mayRun(holdsHouseholdToken = true, deviceSecure = false))
        assertTrue(RoomLockRules.mayRun(holdsHouseholdToken = true, deviceSecure = true))
        assertTrue(RoomLockRules.mayRun(holdsHouseholdToken = false, deviceSecure = false))
        assertEquals(Exit.DEVICE_CREDENTIAL, RoomLockRules.exit(holdsHouseholdToken = true, deviceSecure = true))
        assertEquals(Exit.DEVICE_CREDENTIAL, RoomLockRules.exit(holdsHouseholdToken = false, deviceSecure = true))
        assertEquals(Exit.SET_A_LOCK_FIRST, RoomLockRules.exit(holdsHouseholdToken = true, deviceSecure = false))
        assertEquals(Exit.CONFIRM_NO_LOCK, RoomLockRules.exit(holdsHouseholdToken = false, deviceSecure = false))
        for (screen in listOf("RoomModeActivity", "RoomSetupActivity")) {
            val calls = Bytecode.callers("com.domovoi.app.roommode.$screen", "com/domovoi/app/roommode/RoomLockRules", setOf("mayRun"))
            assertTrue("$screen checks the lock: $calls", calls.any { it.method == "onResume" || it.method == "checkLockAndStart" })
        }
    }

    // ── the drop-in ─────────────────────────────────────────────────────

    @Test
    fun aDropInNeedsAnEchoCancellerAndAFullDuplexCall() {
        assertTrue(RoomEngine.acceptsDropin(echoCanceller = true, fullDuplex = true))
        assertFalse(RoomEngine.acceptsDropin(echoCanceller = false, fullDuplex = true))
        assertFalse(RoomEngine.acceptsDropin(echoCanceller = true, fullDuplex = false))
        val calls = Bytecode.callers("com.domovoi.app.roommode.RoomEngine", "com/domovoi/app/roommode/RoomEngine\$Companion", setOf("acceptsDropin"))
        assertTrue(calls.toString(), calls.any { it.method == "onDropinStart" })
    }

    // ── the normal app's lock ───────────────────────────────────────────

    @Test
    fun theNormalAppSendsEveryoneToTheRoomScreen() {
        val redirect = Bytecode.callers("com.domovoi.app.MainActivity", "com/domovoi/app/MainActivity", setOf("toRoomIfLocked"))
        assertEquals(setOf("onCreate", "onResume"), redirect.map { it.method }.toSet())
        val check = Bytecode.callers("com.domovoi.app.MainActivity", "com/domovoi/app/roommode/RoomStore\$Companion", setOf("isActive"))
        assertTrue(check.toString(), check.any { it.method == "toRoomIfLocked" })
        val gone = Bytecode.callers("com.domovoi.app.MainActivity", "com/domovoi/app/MainActivity", setOf("finishAndRemoveTask"))
        assertTrue("it leaves Recents too: $gone", gone.any { it.method == "toRoomIfLocked" })
    }
}
