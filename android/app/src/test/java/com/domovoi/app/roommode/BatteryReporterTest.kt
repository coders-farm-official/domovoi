package com.domovoi.app.roommode

import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The room phone's battery between the minute's health frames
 * (domovoi/room_battery.py warns at 20 % and 10 %): the charger going on or
 * off sends at once, a 5 % move sends at most once per 15 s, a new session
 * sends at once, and anything smaller waits for the minute's frame. Also
 * the broadcast's extras -> the core's battery object.
 */
class BatteryReporterTest {
    private fun b(pct: Int?, charging: Boolean? = false, onPower: Boolean? = false, plugged: String? = null) =
        RoomDevice.Battery(pct, charging, onPower, 30.0, plugged)

    @Test fun aNewSessionSendsAtOnce() {
        val r = BatteryReporter()
        assertTrue(r.onChange(b(64), 0))
        r.sent(b(64), 0)
        assertFalse(r.onChange(b(63), 1_000))
        r.reset()
        assertTrue("reset: the next change sends", r.onChange(b(63), 2_000))
    }

    @Test fun fivePercentSendsButLessWaitsForTheMinute() {
        val r = BatteryReporter()
        r.sent(b(40), 0)
        for (p in 39 downTo 36) assertFalse("$p%", r.onChange(b(p), 60_000L * (40 - p)))
        assertTrue(r.onChange(b(35), 300_000))
        r.sent(b(35), 300_000)
        assertFalse(r.onChange(b(34), 310_000))
        // Up counts too (a charger that can't keep up, a level read again).
        assertTrue(r.onChange(b(40), 400_000))
    }

    @Test fun aFiveStepInsideTheGapWaits() {
        val r = BatteryReporter()
        r.sent(b(30), 0)
        assertFalse(r.onChange(b(25), BatteryReporter.MIN_GAP_MS - 1))
        assertTrue(r.onChange(b(25), BatteryReporter.MIN_GAP_MS))
    }

    @Test fun theChargerGoingOnOrOffSendsAtOnce() {
        val r = BatteryReporter()
        r.sent(b(18), 0)
        assertTrue("plugged in", r.onChange(b(18, charging = true, onPower = true, plugged = "usb"), 100))
        r.sent(b(18, charging = true, onPower = true), 100)
        assertFalse("still charging, 1 % up", r.onChange(b(19, charging = true, onPower = true), 200))
        // Held full on the charger is still on it: no change.
        assertFalse(r.onChange(b(19, charging = false, onPower = true), 300))
        assertTrue("unplugged", r.onChange(b(19, charging = false, onPower = false), 400))
    }

    @Test fun theMinutesFrameMovesTheStep() {
        val r = BatteryReporter()
        r.sent(b(50), 0)
        r.sent(b(47), 60_000) // the minute's frame carried 47
        assertFalse(r.onChange(b(45), 90_000))
        assertTrue(r.onChange(b(42), 120_000))
    }

    @Test fun nothingKnownSendsNothing() {
        val r = BatteryReporter()
        assertFalse(r.onChange(RoomDevice.Battery(null, null, null, null), 0))
    }

    @Test fun theBroadcastsExtrasBecomeTheCoresObject() {
        // level 15 of 100, discharging (3), unplugged (0), 31.5 °C
        val unplugged = RoomDevice.batteryFrom(capacity = null, level = 15, scale = 100, status = 3, plugged = 0, tempTenths = 315)
        assertEquals(RoomDevice.Battery(15, false, false, 31.5, null), unplugged)
        assertEquals(false, unplugged.onCharger)
        // On USB, charging (2); BatteryManager's capacity wins over level/scale.
        val usb = RoomDevice.batteryFrom(capacity = 16, level = 30, scale = 200, status = 2, plugged = 2, tempTenths = Int.MIN_VALUE)
        assertEquals(RoomDevice.Battery(16, true, true, null, "usb"), usb)
        // Full (5) on AC: on power, not charging.
        val full = RoomDevice.batteryFrom(capacity = Int.MIN_VALUE, level = 100, scale = 100, status = 5, plugged = 1, tempTenths = 300)
        assertEquals(100, full.percent)
        assertEquals(false, full.charging)
        assertEquals(true, full.onCharger)
        assertEquals("ac", full.plugged)
        assertEquals("wireless", RoomDevice.pluggedName(4))
        assertEquals("dock", RoomDevice.pluggedName(8))
        assertNull(RoomDevice.pluggedName(0))
        // Nothing said: nulls, and onCharger unknown.
        val none = RoomDevice.batteryFrom(capacity = null, level = -1, scale = -1, status = -1, plugged = -1, tempTenths = Int.MIN_VALUE)
        assertEquals(RoomDevice.Battery(null, null, null, null, null), none)
        assertNull(none.onCharger)
    }

    @Test fun theFrameSaysWhichCharger() {
        val f = HealthSample.Facts(
            seq = 1, ts = 1.0, rssKb = null, cpuPct = null, tempC = null, uptimeS = 1, procUptimeS = 1, coreOk = true,
            micExpected = true, micFps = 33.3, idle = true, wsUp = true, wsDownForS = 0, reconnects = 0,
            wifiRxMbits = null, wifiSsid = null, wifiSignalDbm = null, batteryPercent = 15, batteryCharging = true,
            onPower = true, micBlocked = false, wakeLoopAgeS = null, threads = null, plugged = "usb",
        )
        val battery = HealthSample.build(f)["battery"]!!.jsonObject
        assertEquals("usb", battery["plugged"]!!.jsonPrimitive.content)
        assertEquals("15", battery["percent"]!!.jsonPrimitive.content)
        assertNull(HealthSample.build(f.copy(plugged = null))["battery"]!!.jsonObject["plugged"])
    }
}
