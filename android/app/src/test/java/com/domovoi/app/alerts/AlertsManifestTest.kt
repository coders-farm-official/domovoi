package com.domovoi.app.alerts

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Document
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

/**
 * A9, read straight from the module's sources: the alarm permissions (exact
 * alarms per API level, boot re-arming), both receivers present and NOT
 * exported, POST_NOTIFICATIONS still asked for, backups still off (the
 * mirror's words live in app-private storage), and the status-bar icon.
 */
class AlertsManifestTest {
    private val android = "http://schemas.android.com/apk/res/android"

    private fun moduleFile(rel: String): File =
        listOf(File(rel), File("app/$rel")).firstOrNull { it.isFile }
            ?: error("cannot find $rel from ${File(".").absolutePath}")

    private val manifest: Document by lazy {
        DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }
            .newDocumentBuilder().parse(moduleFile("src/main/AndroidManifest.xml"))
    }

    private fun elements(tag: String): List<Element> {
        val nodes = manifest.getElementsByTagName(tag)
        return (0 until nodes.length).map { nodes.item(it) as Element }
    }

    private fun Element.attr(name: String): String? = getAttributeNodeNS(android, name)?.value

    private fun permission(name: String): Element? =
        elements("uses-permission").firstOrNull { it.attr("name") == "android.permission.$name" }

    @Test fun exactAlarmPermissionsMatchTheirApiLevels() {
        val schedule = permission("SCHEDULE_EXACT_ALARM")
        assertNotNull("SCHEDULE_EXACT_ALARM", schedule)
        assertEquals("32", schedule!!.attr("maxSdkVersion"))
        val use = permission("USE_EXACT_ALARM")
        assertNotNull("USE_EXACT_ALARM", use)
        assertEquals(null, use!!.attr("maxSdkVersion"))
    }

    @Test fun bootAndNotificationPermissionsAreAskedFor() {
        assertNotNull(permission("RECEIVE_BOOT_COMPLETED"))
        assertNotNull(permission("POST_NOTIFICATIONS"))
    }

    @Test fun theAlarmReceiverIsNotExported() {
        val r = elements("receiver").single { it.attr("name") == ".alerts.TimerAlarmReceiver" }
        assertEquals("false", r.attr("exported"))
        assertEquals("it needs no intent filter: only its own PendingIntents reach it",
            0, r.getElementsByTagName("intent-filter").length)
    }

    @Test fun theBootReceiverIsNotExportedAndHearsBootAndUpdate() {
        val r = elements("receiver").single { it.attr("name") == ".alerts.TimerBootReceiver" }
        assertEquals("false", r.attr("exported"))
        val actions = r.getElementsByTagName("action").let { n ->
            (0 until n.length).map { (n.item(it) as Element).attr("name") }
        }
        assertEquals(
            setOf("android.intent.action.BOOT_COMPLETED", "android.intent.action.MY_PACKAGE_REPLACED"),
            actions.toSet(),
        )
    }

    @Test fun backupsStayOffAndTheStatusIconExists() {
        assertEquals("false", elements("application").single().attr("allowBackup"))
        val icon = moduleFile("src/main/res/drawable/ic_stat_timer.xml").readText()
        assertTrue(icon.contains("<vector"))
    }
}
