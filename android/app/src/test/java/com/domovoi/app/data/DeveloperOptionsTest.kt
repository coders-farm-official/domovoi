package com.domovoi.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Developer options (owner, 2026-10-16): the benchmark and the assistant's
 * timings are for developers, so a household never sees them unless
 * someone taps the app version seven times. The tap count is Android's
 * (the countdown from the third tap, "on" on the seventh); the choice is
 * kept; a debug build starts with them on and a release build off; the
 * switch turns them off for good; a 32-bit install, with nothing to show
 * under them, never has them.
 */
class DeveloperOptionsTest {

    /** The DataStore's part, in memory: what was saved, and how often. */
    private class MemoryStore(var saved: Boolean? = null) : DeveloperOptions.Store {
        var writes = 0
        override fun loadDeveloperOptions() = saved
        override fun saveDeveloperOptions(on: Boolean) {
            saved = on
            writes++
        }
    }

    private fun release(store: DeveloperOptions.Store = MemoryStore()) = DeveloperOptions(store, debugBuild = false, available = true)
    private fun debug(store: DeveloperOptions.Store = MemoryStore()) = DeveloperOptions(store, debugBuild = true, available = true)

    // ── Visibility: release vs debug ───────────────────────────────────

    @Test fun aReleaseBuildStartsWithThemOffAndADebugBuildOn() {
        assertFalse(release().on.value)
        assertTrue(debug().on.value)
    }

    @Test fun whatThePhoneChoseBeatsTheBuildsDefault() {
        assertTrue("turned on in a release build", release(MemoryStore(saved = true)).on.value)
        assertFalse("turned off in a debug build", debug(MemoryStore(saved = false)).on.value)
    }

    @Test fun startingWritesNothing() {
        val store = MemoryStore()
        debug(store)
        release(store)
        assertNull("the build's default is not a choice", store.saved)
        assertEquals(0, store.writes)
    }

    // ── The tap counter ────────────────────────────────────────────────

    @Test fun sevenTapsTurnThemOnWithAndroidsCountdown() {
        val options = release()
        val taps = VersionTaps(options)
        val said = (1..7).map { taps.tap() }
        assertEquals(
            listOf(
                VersionTap.Quiet, VersionTap.Quiet,
                VersionTap.StepsAway(4), VersionTap.StepsAway(3), VersionTap.StepsAway(2), VersionTap.StepsAway(1),
                VersionTap.TurnedOn,
            ),
            said,
        )
        assertTrue(options.on.value)
        assertEquals(
            listOf(
                null, null,
                "You are now 4 steps away from developer options",
                "You are now 3 steps away from developer options",
                "You are now 2 steps away from developer options",
                "You are now 1 step away from developer options",
                "Developer options on",
            ),
            said.map { it.message },
        )
    }

    @Test fun sixTapsAreNotEnough() {
        val options = release()
        val taps = VersionTaps(options)
        repeat(6) { taps.tap() }
        assertFalse(options.on.value)
    }

    @Test fun onceOnEveryTapSaysSoAndChangesNothing() {
        val store = MemoryStore()
        val options = debug(store)
        val taps = VersionTaps(options)
        repeat(3) { assertEquals(VersionTap.AlreadyOn, taps.tap()) }
        assertEquals("Developer options are already on", VersionTap.AlreadyOn.message)
        assertTrue(options.on.value)
        assertEquals("nothing written", 0, store.writes)
    }

    /** The count belongs to one showing of the line: a new one (the screen
     *  shown again) starts from seven, as Android's does. */
    @Test fun aNewShowingStartsTheCountAgain() {
        val options = release()
        repeat(5) { VersionTaps(options).tap() }
        assertFalse("five taps on five showings are one tap each", options.on.value)
        val second = VersionTaps(options)
        repeat(5) { second.tap() }
        assertFalse(options.on.value)
        assertEquals(VersionTap.StepsAway(1), second.tap())
        assertEquals(VersionTap.TurnedOn, second.tap())
    }

    // ── Persistence ────────────────────────────────────────────────────

    @Test fun turnedOnStaysOnWhenTheAppStartsAgain() {
        val store = MemoryStore()
        val taps = VersionTaps(release(store))
        repeat(7) { taps.tap() }
        assertEquals(true, store.saved)
        assertTrue("the next start of the app", release(store).on.value)
    }

    // ── The off switch ─────────────────────────────────────────────────

    @Test fun theSwitchTurnsThemOffForGoodEvenInADebugBuild() {
        val store = MemoryStore()
        val options = debug(store)
        options.turnOff()
        assertFalse(options.on.value)
        assertEquals(false, store.saved)
        assertFalse("the next start of the app", debug(store).on.value)
    }

    @Test fun afterTheSwitchSevenTapsTurnThemBackOn() {
        val options = debug()
        val taps = VersionTaps(options)
        assertEquals(VersionTap.AlreadyOn, taps.tap())
        options.turnOff()
        val said = (1..7).map { taps.tap() }
        assertEquals(VersionTap.StepsAway(4), said[2])
        assertEquals(VersionTap.TurnedOn, said.last())
        assertTrue(options.on.value)
    }

    // ── Nothing to show: a 32-bit install ──────────────────────────────

    @Test fun withoutTheAssistantTheyAreNeverOn() {
        val store = MemoryStore(saved = true)
        val options = DeveloperOptions(store, debugBuild = true, available = false)
        assertFalse("a saved choice from a 64-bit install of the same data", options.on.value)
        val taps = VersionTaps(options)
        repeat(10) { assertEquals(VersionTap.Quiet, taps.tap()) }
        options.turnOn()
        assertFalse(options.on.value)
        assertEquals("nothing written", 0, store.writes)
    }

    // ── The version line's words ───────────────────────────────────────

    @Test fun theVersionLineNamesTheVersionAndADebugBuild() {
        assertEquals("app version 1.0.0-rc1", appVersionText("1.0.0-rc1", debugBuild = false))
        assertEquals("app version 1.0.0-rc1 (debug build)", appVersionText("1.0.0-rc1", debugBuild = true))
        assertEquals("app version unknown", appVersionText(null, debugBuild = false))
        assertEquals("app version unknown", appVersionText(" ", debugBuild = false))
    }
}
