package com.domovoi.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The cached listener name a phone chat's prompt reads: kept
 *  per server, never mixed between servers. */
class HouseholdNamesTest {
    private val map = mutableMapOf<String, String>()
    private val names = HouseholdNames(object : HouseholdNames.KeyValues {
        override fun get(key: String) = map[key]
        override fun put(key: String, value: String?) {
            if (value == null) map.remove(key) else map[key] = value
        }
    })

    private val home = "http://192.168.1.20:6369"

    @Test fun theListenerIsNamedOnlyForTheIdSeen() {
        names.recordListener(home, "3", "Kamron Kennedy")
        assertEquals("Kamron Kennedy", names.listenerName(home, "3"))
        assertNull("another person picked", names.listenerName(home, "4"))
        assertNull("me (this device)", names.listenerName(home, null))
        assertNull("another server", names.listenerName("http://192.168.1.30:6369", "3"))
        names.recordListener("", "3", "x")
        names.recordListener(home, "3", null)
        assertEquals("Kamron Kennedy", names.listenerName(home, "3"))
    }
}
