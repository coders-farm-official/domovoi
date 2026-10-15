package com.domovoi.app.ui.screens.satellites

import com.domovoi.app.ui.components.Tone
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The satellite overview says what root's boot-time network step did on
 *  this boot (applied / skipped / failed / not run ...) and the unit's host
 *  name, as the web drawer does (2026-10-12 follow-up: three satellites kept
 *  `raspberrypi` on the router and nothing on screen said why). An older
 *  satellite sends neither and the screen reads as before. */
class SatNetStepTest {
    private val json = Json { ignoreUnknownKeys = true }
    private fun doc(sample: String): SatHealthDoc =
        json.decodeFromString(SatHealthDoc.serializer(), """{"latest":{"sample":$sample}}""")

    @Test fun anAppliedStepWithItsHostName() {
        val line = netStepLine(doc(
            """{"seq":1,"hostname":"domovoi-office","net_step":{"state":"applied","when":"boot",
               "at":1760000000.5,"version":2,"detail":"host name 'raspberrypi' -> 'domovoi-office'"}}"""
        ))!!
        assertEquals("applied", line.word)
        assertEquals(Tone.Ok, line.tone)
        assertEquals("domovoi-office", line.hostname)
        assertEquals("host name 'raspberrypi' -> 'domovoi-office'", line.detail)
    }

    @Test fun eachStateHasItsWordAndTone() {
        val cases = mapOf(
            "unchanged" to ("nothing to change" to Tone.Ok),
            "skipped" to ("skipped" to Tone.Warn),
            "failed" to ("failed" to Tone.Err),
            "not_run" to ("not run" to Tone.Warn),
            "not_installed" to ("not installed" to Tone.Idle),
            "running" to ("running" to Tone.Idle),
        )
        for ((state, expected) in cases) {
            val line = netStepLine(doc("""{"net_step":{"state":"$state"}}"""))!!
            assertEquals(state, expected.first, line.word)
            assertEquals(state, expected.second, line.tone)
            assertNull(line.detail)
        }
    }

    @Test fun anOlderSatelliteShowsNothing() {
        assertNull(netStepLine(doc("""{"seq":1,"reboot_needed":"reboot this satellite once"}""")))
        assertNull(netStepLine(json.decodeFromString(SatHealthDoc.serializer(), """{"latest":null}""")))
        assertNull(netStepLine(null))
    }

    @Test fun anOutOfRangeVersionDoesNotBreakTheDocument() {
        // The core clamps it now; an older core did not, and one bad number
        // used to fail the decoding of the whole health document.
        val line = netStepLine(doc(
            """{"hostname":"domovoi-den","reboot_needed":"reboot once",
               "net_step":{"state":"applied","version":1000000000000000}}"""
        ))!!
        assertEquals("applied", line.word)
        assertEquals("domovoi-den", line.hostname)
    }

    @Test fun theEarlyUnitNoteIsCarried() {
        val line = netStepLine(doc(
            """{"net_step":{"state":"unchanged","early":"early rename unit disabled by the operator; left alone"}}"""
        ))!!
        assertEquals("early rename unit disabled by the operator; left alone", line.early)
        assertNull(netStepLine(doc("""{"net_step":{"state":"unchanged"}}"""))!!.early)
    }

    @Test fun aHostNameAloneIsStillShown() {
        val line = netStepLine(doc("""{"hostname":"domovoi-den"}"""))!!
        assertEquals("", line.word)
        assertEquals("domovoi-den", line.hostname)
    }
}
