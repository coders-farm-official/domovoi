package com.domovoi.app.net

import com.domovoi.app.testing.ourCaps
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The capability manifest belongs to the server it came from (2026-11-16).
 * The shared fetch helper keeps its last answer across a change of server
 * address and through a failed fetch, so the shell reads it through
 * [manifestFor]: a server switch is [Capabilities.EMPTY] (gated screens
 * hidden) until the new server's manifest loads, and stays EMPTY if that
 * fetch fails; the same server's manifest outlives its own failed re-fetch.
 * And [dropsStations] fires only on a manifest that loaded for the active
 * server, never on that reset, so a switch doesn't wipe the queue.
 */
class ServerSwitchManifestTest {

    private val home = "http://192.168.1.20:6369"
    private val other = "http://192.168.1.30:6369"
    private val withRadio = LoadedManifest(home, ourCaps(CAP_STATIONS, CAP_IMAGEGEN))

    /** What the helper's `data` holds at each step, and what the shell shows. */
    @Test fun aSwitchIsEmptyUntilTheNewServersManifestLoads() {
        // On the home server, its manifest loaded: Stations shows.
        assertSame(withRadio.caps, manifestFor(withRadio, home))
        assertTrue(manifestFor(withRadio, home).has(CAP_STATIONS))

        // The app moves to another server. The helper still holds home's
        // answer while the new fetch runs: none of it counts.
        assertEquals(Capabilities.EMPTY, manifestFor(withRadio, other))
        assertFalse(manifestFor(withRadio, other).has(CAP_STATIONS))
        assertFalse(manifestFor(withRadio, other).has(CAP_IMAGEGEN))

        // The new server's fetch FAILS: the helper keeps home's answer, and
        // the shell stays EMPTY (fail closed).
        assertEquals(Capabilities.EMPTY, manifestFor(withRadio, other))

        // The new server's manifest loads: that is what holds.
        val theirs = LoadedManifest(other, ourCaps(CAP_IMAGEGEN))
        assertSame(theirs.caps, manifestFor(theirs, other))
        assertFalse(manifestFor(theirs, other).has(CAP_STATIONS))
        assertTrue(manifestFor(theirs, other).has(CAP_IMAGEGEN))
    }

    @Test fun nothingLoadedIsEmpty() {
        assertEquals(Capabilities.EMPTY, manifestFor(null, home))
        assertEquals(Capabilities.EMPTY, manifestFor(null, ""))
    }

    /** Kept as it was: a socket reconnect re-fetches, and a failed re-fetch
     *  leaves the helper's last answer, which is this server's own. */
    @Test fun theSameServersFailedRefetchKeepsItsManifest() {
        assertSame(withRadio.caps, manifestFor(withRadio, home))
    }

    /** Forgetting the server (no address) shows nothing of the old one's. */
    @Test fun forgettingTheServerIsEmpty() {
        assertEquals(Capabilities.EMPTY, manifestFor(withRadio, ""))
    }

    @Test fun stationsGoOnlyOnALoadedManifestForTheActiveServer() {
        val withoutRadio = LoadedManifest(home, ourCaps(CAP_IMAGEGEN))
        // The active server's manifest loaded without our Radio: they go.
        assertTrue(dropsStations(withoutRadio, home))
        assertTrue(dropsStations(LoadedManifest(home, Capabilities.EMPTY), home))
        // With it: they stay.
        assertFalse(dropsStations(withRadio, home))
        // Nothing loaded yet (or the first fetch failed): they stay.
        assertFalse(dropsStations(null, home))
        // The reset of a switch: the helper still holds the previous
        // server's answer — with Radio or without — and nothing goes.
        assertFalse(dropsStations(withRadio, other))
        assertFalse(dropsStations(withoutRadio, other))
        assertFalse(dropsStations(withoutRadio, ""))
    }

    /** The shell reads the manifest through these, and the fetch tags it
     *  with the server it asked. Read from the source: both sit inside
     *  composables a unit test can't run (CapabilitiesTest does the same). */
    @Test fun theShellAndTheFetchAreWiredThroughThem() {
        fun source(rel: String) = (listOf(File(rel), File("app/$rel")).firstOrNull { it.isFile }
            ?: error("cannot find $rel")).readText()
        val shell = source("src/main/java/com/domovoi/app/ui/shell/AppShell.kt")
        assertTrue(shell.contains("manifestFor(capsState.data, activeServer)"))
        assertTrue(shell.contains("dropsStations(capsState.data, activeServer)"))
        assertFalse("the raw last answer must not be the shell's manifest", shell.contains("capsState.data ?:"))
        val caps = source("src/main/java/com/domovoi/app/net/Capabilities.kt")
        assertTrue(caps.contains("val server = it.prefs.serverUrl.value"))
        assertTrue(caps.contains("LoadedManifest(server, it.api.getUntracked(\"/api/capabilities\")"))
    }
}
