package com.domovoi.app.net

import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** The capability manifest (design section 8): decoding and the gates built on it. */
class CapabilitiesTest {

    private val manifest = """
        {
          "domovoi_api": "1",
          "server_version": "0.9.0",
          "plugins": [
            {"slug": "radio", "version": "1.2", "android_capabilities": ["stations"]},
            {"slug": "provider", "android_capabilities": []},
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

    /** The household "AI features" switch (`features.ai`): off only when the
     *  server says false. Absent (an older server; a manifest that failed to
     *  load) counts as on, and `features.chat` (voice chat mode) is not it. */
    @Test fun aiEnabled_isOffOnlyWhenTheServerSaysSo() {
        assertTrue(Capabilities.EMPTY.aiEnabled)
        assertTrue(DomovoiJson.parseToJsonElement(manifest).decode<Capabilities>().aiEnabled)
        assertTrue(Capabilities(features = mapOf(FEATURE_AI to true)).aiEnabled)
        assertFalse(Capabilities(features = mapOf(FEATURE_AI to false)).aiEnabled)
        assertTrue(Capabilities(features = mapOf("chat" to false)).aiEnabled)
        val off = DomovoiJson.parseToJsonElement("""{"features": {"chat": true, "ai": false}}""").decode<Capabilities>()
        assertFalse(off.aiEnabled)
        assertEquals("ai", FEATURE_AI)
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

    /**
     * The capability fetch must not clear the pairing screen.
     *
     * `/api/capabilities` is an Open route, so it answers 200 to a phone that
     * is not paired, and `rememberCapabilities` runs at connect and again on
     * every socket reconnect — exactly when a household read's 401 has just
     * raised the pairing screen. Through the tracked `get`, that 200 clears
     * `pairingRequired` and the screen vanishes with nothing paired.
     *
     * Read from the source because the call sits inside a `@Composable`, and
     * a unit test cannot run one; `NetworkSecurityConfigTest` reads the
     * module's files the same way.
     */
    @Test fun theCapabilityFetchDoesNotClearThePairingScreen() {
        val rel = "src/main/java/com/domovoi/app/net/Capabilities.kt"
        val source = (listOf(File(rel), File("app/$rel")).firstOrNull { it.isFile }
            ?: error("cannot find $rel from ${File(".").absolutePath}")).readText()
        assertTrue("rememberCapabilities must use getUntracked: /api/capabilities is Open, and a "
                   + "tracked 200 clears pairingRequired while the pairing screen is up",
                   source.contains("getUntracked(\"/api/capabilities\")"))
    }
}
