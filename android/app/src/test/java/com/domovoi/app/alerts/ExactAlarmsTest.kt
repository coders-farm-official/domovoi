package com.domovoi.app.alerts

import android.app.AlarmManager
import android.content.Intent
import android.provider.Settings
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

/**
 * Exact alarms under Google Play's rules (alerts/ExactAlarms.kt): the app
 * asks for SCHEDULE_EXACT_ALARM, the person's own grant, and never
 * USE_EXACT_ALARM (Play keeps it for alarm-clock, timer and calendar apps);
 * without the grant every alarm is inexact and Home says so once with a
 * way to give it; the system's "granted" broadcast re-arms everything.
 */
class ExactAlarmsTest {

    @Test fun theGrantIsNeededFromApi31Only() {
        assertFalse(ExactAlarms.needsGrant(26))
        assertFalse(ExactAlarms.needsGrant(30))
        assertTrue(ExactAlarms.needsGrant(31))
        assertTrue(ExactAlarms.needsGrant(36))
    }

    /** The two platform names, spelled out so the app needs no API 31
     *  constant on older phones, are the platform's own. */
    @Test fun theActionsAreThePlatformsOwn() {
        assertEquals(AlarmManager.ACTION_SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED, ExactAlarms.ACTION_GRANT_CHANGED)
        assertEquals(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, ExactAlarms.ACTION_REQUEST_GRANT)
    }

    /** Without the grant the alarms are inexact and the background tick
     *  plain (never exact, which would throw), on every version that asks. */
    @Test fun withoutTheGrantNothingIsArmedExact() {
        for (sdk in 31..36) {
            assertFalse("timer alarm, API $sdk", useExact(sdk, canScheduleExact = false))
            assertEquals("tick, API $sdk", ChainMode.PLAIN, chainMode(sdk, canExact = false))
            assertTrue("timer alarm with the grant, API $sdk", useExact(sdk, canScheduleExact = true))
            assertEquals("tick with the grant, API $sdk", ChainMode.EXACT, chainMode(sdk, canExact = true))
        }
    }

    @Test fun homeSaysOneThingAtMostNotificationsFirst() {
        // Everything fine: nothing.
        assertEquals(TimerAlertHint.NONE, timerAlertHint(canPost = true, canExact = true, false, false))
        // Notifications off: nothing rings at all, which outranks ringing late.
        assertEquals(TimerAlertHint.NOTIFICATIONS_OFF, timerAlertHint(canPost = false, canExact = false, false, false))
        assertEquals(TimerAlertHint.NOTIFICATIONS_OFF, timerAlertHint(canPost = false, canExact = true, false, false))
        // ...until "not now"; then the late ring is not said instead (it
        // would not ring at all, late or not).
        assertEquals(TimerAlertHint.NONE, timerAlertHint(canPost = false, canExact = false, true, false))
        // Notifications on, no exact alarms: may ring late, until its own "not now".
        assertEquals(TimerAlertHint.MAY_RING_LATE, timerAlertHint(canPost = true, canExact = false, false, false))
        assertEquals(TimerAlertHint.MAY_RING_LATE, timerAlertHint(canPost = true, canExact = false, true, false))
        assertEquals(TimerAlertHint.NONE, timerAlertHint(canPost = true, canExact = false, false, true))
    }

    // ── The manifest ────────────────────────────────────────────────────

    private val ns = "http://schemas.android.com/apk/res/android"

    private fun manifest(): org.w3c.dom.Document {
        val f = listOf(File("src/main/AndroidManifest.xml"), File("app/src/main/AndroidManifest.xml")).first { it.isFile }
        return DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }.newDocumentBuilder().parse(f)
    }

    private fun org.w3c.dom.Document.all(tag: String): List<Element> =
        getElementsByTagName(tag).let { n -> (0 until n.length).map { n.item(it) as Element } }

    @Test fun theAppAsksForTheGrantAndNeverForUseExactAlarm() {
        val perms = manifest().all("uses-permission").associateBy { it.getAttributeNS(ns, "name") }
        val schedule = perms["android.permission.SCHEDULE_EXACT_ALARM"]
        assertTrue("SCHEDULE_EXACT_ALARM is declared", schedule != null)
        assertEquals("on every API level, not only 31-32", "", schedule!!.getAttributeNS(ns, "maxSdkVersion"))
        assertFalse("USE_EXACT_ALARM is a Play-restricted permission", "android.permission.USE_EXACT_ALARM" in perms)
    }

    /** The re-arming receiver hears exactly what its code acts on. */
    @Test fun theRearmReceiverHearsTheGrant() {
        val r = manifest().all("receiver").single { it.getAttributeNS(ns, "name") == ".alerts.TimerBootReceiver" }
        assertEquals("false", r.getAttributeNS(ns, "exported"))
        val actions = r.getElementsByTagName("action").let { n -> (0 until n.length).map { (n.item(it) as Element).getAttributeNS(ns, "name") } }
        assertEquals(REARM_ACTIONS, actions.toSet())
        assertTrue(ExactAlarms.ACTION_GRANT_CHANGED in REARM_ACTIONS)
        assertTrue(Intent.ACTION_BOOT_COMPLETED in REARM_ACTIONS)
    }
}
