package com.domovoi.app.roommode

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale

/**
 * The room screen holds the screen on only while the phone is locked and
 * on its charger (security audit round 6, AND-15): an unlocked room phone
 * on a charger used to stay on forever, so it never locked and Home or
 * Recents opened the rest of the phone.
 */
class RoomScreenOnTest {
    private fun keep(locked: Boolean, plugged: Boolean, secure: Boolean = true) =
        RoomLockRules.keepScreenOn(keyguardLocked = locked, plugged = plugged, deviceSecure = secure)

    @Test fun lockedAndPluggedKeepsTheScreenOn() {
        assertTrue(keep(locked = true, plugged = true))
    }

    @Test fun unlockedLetsTheScreenTimeOut() {
        assertFalse("unlocked on the charger: the phone's own timeout, so it locks itself", keep(locked = false, plugged = true))
        assertFalse(keep(locked = false, plugged = false))
    }

    @Test fun unpluggedLetsTheScreenTimeOut() {
        assertFalse("on battery: the phone's own timeout, locked or not", keep(locked = true, plugged = false))
        assertFalse(keep(locked = false, plugged = false, secure = false))
    }

    @Test fun aPhoneWithNoSecureLockHasNothingToLockBehind() {
        assertTrue(keep(locked = false, plugged = true, secure = false))
        assertTrue(keep(locked = true, plugged = true, secure = false))
    }

    @Test fun theHintShowsOnlyWhileAPhoneWithALockIsUnlocked() {
        assertTrue(RoomLockRules.showLockHint(keyguardLocked = false, deviceSecure = true))
        assertFalse(RoomLockRules.showLockHint(keyguardLocked = true, deviceSecure = true))
        assertFalse("no lock to lock with", RoomLockRules.showLockHint(keyguardLocked = false, deviceSecure = false))
    }

    @Test fun theRoomScreenCarriesTheHintAsOneLine() {
        val zone = ZoneId.of("America/Chicago")
        fun build(lockHint: Boolean, needsLock: Boolean = false) = RoomDisplayModel.build(
            RoomDisplayModel.Input(
                status = RoomEngine.Status(link = RoomEngine.Link.Ready, wakeReady = true, micOpen = true),
                running = true, needsLock = needsLock, wakeWord = "hey_jarvis", approvalCode = null, feed = null, volume = 40,
                nowMs = ZonedDateTime.of(2026, 11, 16, 18, 42, 0, 0, zone).toInstant().toEpochMilli(), elapsedMs = 10_000L,
                zone = zone, locale = Locale.US, use24h = false, widthDp = 411, heightDp = 891, lockHint = lockHint,
            ),
        )
        assertEquals(RoomStatusText.LOCK_HINT, build(lockHint = true).lockHint)
        assertTrue("plain words: it names the power button", "power button" in RoomStatusText.LOCK_HINT)
        assertNull(build(lockHint = false).lockHint)
        assertNull("the set-a-lock screen says enough", build(lockHint = true, needsLock = true).lockHint)
    }

    /** FLAG_KEEP_SCREEN_ON is set in one function of the room screen, and
     *  only as RoomLockRules.keepScreenOn decides; nothing else in room
     *  mode holds the screen on. */
    @Test fun theFlagIsSetOnlyThroughTheDecision() {
        val repo = File(System.getProperty("domovoi.repo.dir") ?: error("domovoi.repo.dir is not set"))
        val dir = File(repo, "android/app/src/main/java/com/domovoi/app/roommode")
        val holders = dir.listFiles { f -> f.extension == "kt" }!!.filter { f ->
            val code = f.readLines().filterNot { it.trimStart().startsWith("*") || it.trimStart().startsWith("//") }.joinToString("\n")
            "LayoutParams.FLAG_KEEP_SCREEN_ON" in code || Regex("""keepScreenOn\s*=""").containsMatchIn(code) ||
                "SCREEN_BRIGHT_WAKE_LOCK" in code || "FULL_WAKE_LOCK" in code
        }.map { it.name }
        assertEquals(listOf("RoomModeActivity.kt"), holders)

        val src = File(dir, "RoomModeActivity.kt").readText()
        val start = src.indexOf("private fun applyScreenRule()")
        assertTrue("applyScreenRule() exists", start >= 0)
        val body = src.substring(start, src.indexOf("\n    }\n", start))
        val uses = Regex("""LayoutParams\.FLAG_KEEP_SCREEN_ON""").findAll(src).map { it.range.first }.toList()
        assertTrue("the flag is touched", uses.isNotEmpty())
        assertTrue("every use is inside applyScreenRule()", uses.all { it in start until start + body.length })
        assertTrue("it adds the flag only when the decision says so", "if (RoomLockRules.keepScreenOn(" in body)
        val add = body.indexOf("addFlags(")
        val decide = body.indexOf("RoomLockRules.keepScreenOn(")
        val otherwise = body.indexOf("} else {")
        assertTrue("addFlags sits in the decision's true branch", decide in 0 until add && add < otherwise)
        // The window's own setter (the old way in) is not used either.
        assertFalse(Regex("""window\.(setFlags|attributes)""").containsMatchIn(src))
    }
}
