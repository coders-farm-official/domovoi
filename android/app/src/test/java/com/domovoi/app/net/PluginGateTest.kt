package com.domovoi.app.net

import com.domovoi.app.testing.ourImagegen
import com.domovoi.app.testing.ourRadio
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The app's Stations and Images screens are its own code talking to two
 * plugins of ours (2026-11-16): each shows only while GET /api/capabilities
 * lists THAT plugin — the exact slug and publisher, declaring the
 * capability (Capabilities.kt AppScreenPlugins). Anything else, including
 * another plugin declaring "stations", keeps the screen hidden.
 */
class PluginGateTest {

    private fun caps(vararg p: CapabilityPlugin) = Capabilities(plugins = p.toList())

    @Test fun theTableNamesOurTwoPlugins() {
        assertEquals("Coders Farm", OUR_PUBLISHER)
        assertEquals(AppScreenPlugin(CAP_STATIONS, "radio", "Coders Farm"), AppScreenPlugins[CAP_STATIONS])
        assertEquals(AppScreenPlugin(CAP_IMAGEGEN, "imagegen", "Coders Farm"), AppScreenPlugins[CAP_IMAGEGEN])
        assertEquals(setOf(CAP_STATIONS, CAP_IMAGEGEN), AppScreenPlugins.keys)
    }

    @Test fun oursPresentLightsItsScreen() {
        val c = caps(ourRadio, ourImagegen)
        assertTrue(c.has(CAP_STATIONS))
        assertTrue(c.has(CAP_IMAGEGEN))
        assertSame(ourRadio, c.pluginFor(CAP_STATIONS))
        assertSame(ourImagegen, c.pluginFor(CAP_IMAGEGEN))
        // Each only its own.
        assertFalse(caps(ourRadio).has(CAP_IMAGEGEN))
        assertFalse(caps(ourImagegen).has(CAP_STATIONS))
    }

    @Test fun noPluginAtAllHidesBoth() {
        assertFalse(Capabilities.EMPTY.has(CAP_STATIONS))
        assertFalse(Capabilities.EMPTY.has(CAP_IMAGEGEN))
        assertNull(Capabilities.EMPTY.pluginFor(CAP_STATIONS))
    }

    @Test fun theWrongPublisherIsNotOurs() {
        for (pub in listOf("Someone Else", "coders farm", "Coders Farm ", " Coders Farm", "CodersFarm", "")) {
            val c = caps(ourRadio.copy(publisher = pub), ourImagegen.copy(publisher = pub))
            assertFalse("publisher '$pub'", c.has(CAP_STATIONS))
            assertFalse("publisher '$pub'", c.has(CAP_IMAGEGEN))
        }
    }

    @Test fun theWrongSlugIsNotOurs() {
        for (slug in listOf("radio2", "Radio", "radio-plus", "fm", "")) {
            assertFalse("slug '$slug'", caps(ourRadio.copy(slug = slug)).has(CAP_STATIONS))
        }
        assertFalse(caps(ourImagegen.copy(slug = "image-gen")).has(CAP_IMAGEGEN))
        // Our own plugins, crossed: radio declaring imagegen is not Images.
        assertFalse(caps(ourRadio.copy(androidCapabilities = listOf(CAP_STATIONS, CAP_IMAGEGEN))).has(CAP_IMAGEGEN))
    }

    @Test fun aThirdPartyDeclaringStationsDoesNotLightStations() {
        val tuner = CapabilityPlugin(
            slug = "fm-tuner", name = "FM Tuner", publisher = "Elsewhere Labs",
            androidCapabilities = listOf(CAP_STATIONS, CAP_IMAGEGEN),
        )
        assertFalse(caps(tuner).has(CAP_STATIONS))
        assertFalse(caps(tuner).has(CAP_IMAGEGEN))
        // Listed beside ours (and first), the frame still names ours.
        assertSame(ourRadio, caps(tuner, ourRadio).pluginFor(CAP_STATIONS))
        // Someone else's plugin that took our slug.
        assertFalse(caps(ourRadio.copy(publisher = "Elsewhere Labs")).has(CAP_STATIONS))
    }

    @Test fun ourPluginNotDeclaringTheCapabilityIsHidden() {
        assertFalse(caps(ourRadio.copy(androidCapabilities = emptyList())).has(CAP_STATIONS))
    }

    /** A server older than 1.0.0 (before 2026-10-17) sent no publisher: we
     *  can't tell the plugin is ours, so the screen stays hidden. */
    @Test fun noPublisherFailsClosed() {
        assertFalse(caps(ourRadio.copy(publisher = null)).has(CAP_STATIONS))
        val old = DomovoiJson.parseToJsonElement(
            """{"plugins": [{"slug": "radio", "version": "1.0", "android_capabilities": ["stations"]},
                            {"slug": "imagegen", "android_capabilities": ["imagegen"]}]}""",
        ).decode<Capabilities>()
        assertFalse(old.has(CAP_STATIONS))
        assertFalse(old.has(CAP_IMAGEGEN))
        assertNull(old.pluginFor(CAP_STATIONS))
    }

    /** The server lists only enabled plugins that loaded (status ok or
     *  degraded; web/backend/api/capabilities.py): a disabled or failed one
     *  is simply absent, and absent is hidden. */
    @Test fun aDisabledPluginIsAbsentSoHidden() {
        val body = """{"plugins": [{"slug": "imagegen", "publisher": "Coders Farm", "android_capabilities": ["imagegen"]}]}"""
        val c = DomovoiJson.parseToJsonElement(body).decode<Capabilities>()
        assertFalse(c.has(CAP_STATIONS))   // radio disabled: not listed
        assertTrue(c.has(CAP_IMAGEGEN))
    }

    @Test fun ourCurrentServersManifestLightsBoth() {
        val body = """
            {"plugins": [
              {"slug": "imagegen", "version": "1.1.0", "name": "Image Generation", "publisher": "Coders Farm",
               "android_capabilities": ["imagegen"]},
              {"slug": "radio", "version": "1.3.1", "name": "Radio", "publisher": "Coders Farm",
               "android_capabilities": ["stations"]}
            ]}
        """.trimIndent()
        val c = DomovoiJson.parseToJsonElement(body).decode<Capabilities>()
        assertTrue(c.has(CAP_STATIONS))
        assertTrue(c.has(CAP_IMAGEGEN))
    }

    @Test fun anUnknownCapabilityLightsNothing() {
        val p = ourRadio.copy(androidCapabilities = listOf("holograms"))
        assertFalse(caps(p).has("holograms"))
        assertFalse(p.serves("holograms"))
    }

    @Test fun aSlugIsAskedAboutByTheSameRule() {
        assertEquals(true, caps(ourRadio).ourPluginPresent("radio"))
        assertEquals(false, Capabilities.EMPTY.ourPluginPresent("radio"))
        assertEquals(false, caps(ourRadio.copy(publisher = "Elsewhere Labs")).ourPluginPresent("radio"))
        assertEquals(true, caps(ourImagegen).ourPluginPresent("imagegen"))
        // A plugin no app screen uses: not this rule's to say.
        assertNull(caps(ourRadio).ourPluginPresent("weather"))
    }
}
