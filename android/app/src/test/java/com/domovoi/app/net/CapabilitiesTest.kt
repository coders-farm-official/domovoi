package com.domovoi.app.net

import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The capability manifest (design section 8): decoding and the gates built on it. */
class CapabilitiesTest {

    private val manifest = """
        {
          "domovoi_api": "1",
          "server_version": "0.9.0",
          "plugins": [
            {"slug": "radio", "version": "1.2", "android_capabilities": ["stations"]},
            {"slug": "ytdlp", "android_capabilities": []},
            {"slug": "future", "android_capabilities": ["holograms"], "extra_field": true}
          ],
          "handler_display": [
            {"name": "radio", "label": "Radio", "tone": "media"},
            {"name": "weird", "tone": "sparkly"}
          ],
          "features": {"chat": true},
          "unknown_top_level": 42
        }
    """.trimIndent()

    @Test fun decodesTolerantly() {
        val caps = DomovoiJson.parseToJsonElement(manifest).decode<Capabilities>()
        assertEquals("0.9.0", caps.serverVersion)
        assertEquals(3, caps.plugins.size)
        assertEquals(listOf<String>(), caps.plugins[1].androidCapabilities)
        assertNull(caps.plugins[1].version)
        assertEquals(mapOf("chat" to true), caps.features)
    }

    @Test fun has_matchesAnyPluginDeclaringIt() {
        val caps = DomovoiJson.parseToJsonElement(manifest).decode<Capabilities>()
        assertTrue(caps.has(CAP_STATIONS))
        assertTrue(caps.has("holograms")) // unknown slugs are still data
        assertFalse(caps.has(CAP_IMAGEGEN))
        assertFalse(Capabilities.EMPTY.has(CAP_STATIONS))
    }

    @Test fun toneAndLabel_fallBackForUnknownHandlers() {
        val caps = DomovoiJson.parseToJsonElement(manifest).decode<Capabilities>()
        assertEquals("media", caps.toneFor("radio"))
        assertEquals("Radio", caps.labelFor("radio"))
        assertEquals("sparkly", caps.toneFor("weird")) // server value passes through; UI maps unknown to neutral
        assertEquals("weird", caps.labelFor("weird"))   // no label: raw name
        assertEquals("neutral", caps.toneFor("nope"))
        assertEquals("nope", caps.labelFor("nope"))
        assertNull(caps.labelFor(null))
        assertEquals("neutral", Capabilities.EMPTY.toneFor("radio"))
    }

    @Test fun emptyManifestDecodesFromEmptyObject() {
        val caps = DomovoiJson.parseToJsonElement("{}").jsonObject.decode<Capabilities>()
        assertEquals(Capabilities.EMPTY, caps)
    }
}
