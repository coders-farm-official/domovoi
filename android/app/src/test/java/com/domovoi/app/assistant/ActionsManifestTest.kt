package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

/**
 * N2's hide-when-no-app rule needs Android to say which apps take the Clock
 * app's intents, which from API 30 it does only for intents the manifest's
 * `<queries>` names; and sending them needs the normal SET_ALARM
 * permission. Read straight from the module's manifest.
 */
class ActionsManifestTest {
    private val android = "http://schemas.android.com/apk/res/android"

    private val manifest by lazy {
        val f = listOf(File("src/main/AndroidManifest.xml"), File("app/src/main/AndroidManifest.xml")).first { it.isFile }
        DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }.newDocumentBuilder().parse(f)
    }

    private fun elements(tag: String): List<Element> {
        val nodes = manifest.getElementsByTagName(tag)
        return (0 until nodes.length).map { nodes.item(it) as Element }
    }

    @Test fun theClockIntentsAreQueriedAndThePermissionDeclared() {
        val queried = elements("queries").flatMap { q ->
            val actions = q.getElementsByTagName("action")
            (0 until actions.length).map { (actions.item(it) as Element).getAttributeNS(android, "name") }
        }.toSet()
        listOf("SET_TIMER", "SET_ALARM", "SHOW_TIMERS", "SHOW_ALARMS").forEach {
            assertTrue("<queries> names android.intent.action.$it", "android.intent.action.$it" in queried)
        }
        val permissions = elements("uses-permission").map { it.getAttributeNS(android, "name") }
        assertEquals(1, permissions.count { it == "com.android.alarm.permission.SET_ALARM" })
        // The app never sets alarms itself for a phone chat: no new exact-alarm use.
        assertTrue(permissions.none { it == "android.permission.USE_EXACT_ALARM" })
    }

    @Test fun eachClockIntentHasItsAction() {
        // The four intents ClockIntents sends, one action each (the probe
        // resolveActivity asks about is the same action, extras aside).
        assertEquals(4, ClockIntent.entries.size)
    }
}
