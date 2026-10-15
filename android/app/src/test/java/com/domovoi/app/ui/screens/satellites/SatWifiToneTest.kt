package com.domovoi.app.ui.screens.satellites

import com.domovoi.app.ui.components.Tone
import org.junit.Assert.assertEquals
import org.junit.Test
import kotlinx.serialization.json.Json

/** Settings → Satellites colours a satellite's Wi-Fi by its received signal
 *  since 2026-10-12 (the web's wifiTone): an idle link reads the 1 Mbit/s
 *  beacon rate, which used to paint every idle satellite red. */
class SatWifiToneTest {
    @Test fun signalBands() {
        assertEquals(Tone.Ok, wifiTone(SatWifi(rx_mbits = 1.0, signal_dbm = -52.0)))
        assertEquals(Tone.Ok, wifiTone(SatWifi(signal_dbm = -60.0)))
        assertEquals(Tone.Warn, wifiTone(SatWifi(signal_dbm = -65.0)))
        assertEquals(Tone.Err, wifiTone(SatWifi(rx_mbits = 300.0, signal_dbm = -71.0)))
        assertEquals(Tone.Err, wifiTone(SatWifi(mode = "ap")))
    }

    @Test fun anOlderSatelliteKeepsTheRateTone() {
        assertEquals(Tone.Err, wifiTone(SatWifi(rx_mbits = 3.0)))
        assertEquals(Tone.Ok, wifiTone(SatWifi(rx_mbits = 54.0)))
        assertEquals(Tone.Idle, wifiTone(null as SatWifi?))
    }

    @Test fun rebootNeededComesFromTheHealthSample() {
        val doc = DomovoiJsonForTest.decode(
            """{"room_id":"den","latest":{"received_at":"x","sample":{"seq":1,
               "reboot_needed":"network settings are applied at boot: reboot this satellite once"}}}"""
        )
        assertEquals("network settings are applied at boot: reboot this satellite once", rebootNeeded(doc))
        assertEquals(null, rebootNeeded(DomovoiJsonForTest.decode("""{"latest":{"sample":{"seq":1}}}""")))
        assertEquals(null, rebootNeeded(DomovoiJsonForTest.decode("""{"latest":null}""")))
        assertEquals(null, rebootNeeded(null))
    }

    @Test fun theCardText() {
        assertEquals("-52 dBm" to "1 Mbit/s", wifiLinkText(SatWifi(rx_mbits = 1.0, signal_dbm = -52.0)))
        assertEquals("3 Mbit/s" to null, wifiLinkText(SatWifi(rx_mbits = 3.0)))
        assertEquals("setup hotspot", wifiLinkText(SatWifi(mode = "ap")).first)
    }
}

private object DomovoiJsonForTest {
    private val json = Json { ignoreUnknownKeys = true }
    fun decode(text: String): SatHealthDoc = json.decodeFromString(SatHealthDoc.serializer(), text)
}
