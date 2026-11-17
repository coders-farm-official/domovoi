package com.domovoi.app.ui.components

import androidx.compose.ui.graphics.Color
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.CapabilityPlugin
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.PluginAccent
import com.domovoi.app.net.decode
import com.domovoi.app.ui.theme.DomovoiDarkColors
import com.domovoi.app.ui.theme.DomovoiLightColors
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The plugin screen frame: its colours (the dashboard's plugin accent,
 *  2026-10-17) and its words. */
class PluginFrameTest {

    private val radio = CapabilityPlugin(
        slug = "radio", name = "Radio", publisher = "Coders Farm",
        androidCapabilities = listOf("stations"),
        accent = PluginAccent(
            source = "plugin",
            light = mapOf("brand" to "#2f7d6d", "brand_hover" to "#217162", "brand_press" to "#0f6656",
                          "brand_fg" to "#fdfcf9", "brand_soft" to "#2f7d6d1f", "brand_ink" to "#217162"),
            dark = mapOf("brand" to "#519d8c", "brand_fg" to "#021611", "brand_soft" to "#519d8c24",
                         "brand_ink" to "#54a08f"),
        ),
    )

    @Test fun parsesTheServersHexForms() {
        assertEquals(Color(0xFF2F7D6D), parseAccentHex("#2f7d6d"))
        assertEquals(Color(0x1F2F7D6D), parseAccentHex("#2f7d6d1f"))   // alpha last on the wire
        assertEquals(Color(0xFF2F7D6D), parseAccentHex(" #2F7D6D "))
        for (bad in listOf(null, "", "2f7d6d", "#2f7d6", "#2f7d6dz1", "red", "#fff")) {
            assertNull(bad, parseAccentHex(bad))
        }
    }

    @Test fun theLightThemeTakesTheLightTokens() {
        val t = pluginTint(DomovoiLightColors, radio)
        assertEquals(Color(0xFF2F7D6D), t.colors.brand)
        assertEquals(Color(0xFF0F6656), t.colors.brandPress)
        assertEquals(Color(0xFFFDFCF9), t.colors.brandFg)
        assertEquals(Color(0x1F2F7D6D), t.colors.brandSoft)
        assertEquals(Color(0xFF217162), t.ink)
        // Only the brand family changes: surfaces, text and status stay.
        assertEquals(DomovoiLightColors.card, t.colors.card)
        assertEquals(DomovoiLightColors.fg, t.colors.fg)
        assertEquals(DomovoiLightColors.err, t.colors.err)
    }

    @Test fun theDarkThemeTakesTheDarkTokensAndKeepsWhatIsMissing() {
        val t = pluginTint(DomovoiDarkColors, radio)
        assertEquals(Color(0xFF519D8C), t.colors.brand)
        assertEquals(Color(0xFF54A08F), t.ink)
        assertEquals(DomovoiDarkColors.brandHover, t.colors.brandHover)   // not sent: the app's own
    }

    @Test fun noAccentKeepsTheAppsColours() {
        val t = pluginTint(DomovoiLightColors, radio.copy(accent = null))
        assertEquals(DomovoiLightColors, t.colors)
        assertEquals(DomovoiLightColors.fg, t.ink)
        assertEquals(DomovoiLightColors, pluginTint(DomovoiLightColors, null).colors)
    }

    @Test fun decodesTheFrameFieldsFromCapabilities() {
        val body = """
            {"plugins": [
              {"slug": "radio", "version": "1.3.1", "name": "Radio", "publisher": "Coders Farm",
               "accent": {"source": "plugin", "light": {"brand": "#2f7d6d"}, "dark": {"brand": "#519d8c"}},
               "android_capabilities": ["stations"]},
              {"slug": "imagegen", "publisher": "Coders Farm", "android_capabilities": ["imagegen"]}
            ]}
        """.trimIndent()
        val caps = DomovoiJson.parseToJsonElement(body).decode<Capabilities>()
        val p = caps.pluginFor("stations")!!
        assertEquals("Coders Farm", p.publisher)
        assertEquals("#2f7d6d", p.accent!!.light["brand"])
        val plain = caps.pluginFor("imagegen")!!
        assertNull(plain.accent)               // no accent sent: the app's own colours
        assertNull(plain.name)
        assertEquals("Uses the imagegen plugin", pluginFrameLine(plain))   // no name: the slug
        assertNull(caps.pluginFor("holograms"))
    }

    /** The band says the screen is the app's and uses the plugin (2026-11-16):
     *  "Uses the Radio plugin", then "by Coders Farm"; no separate badge. */
    @Test fun theBandSaysTheScreenUsesThePlugin() {
        assertEquals("Uses the Radio plugin", pluginFrameLine(radio))
        assertEquals("Uses the Radio plugin, by Coders Farm", pluginFrameDescription(radio))
        val images = radio.copy(slug = "imagegen", name = "Image Generation")
        assertEquals("Uses the Image Generation plugin", pluginFrameLine(images))
        assertEquals("Uses the Radio plugin", pluginFrameDescription(radio.copy(publisher = " ")))
        assertEquals("Radio", pluginDisplayName(radio))
        assertEquals("radio", pluginDisplayName(radio.copy(name = "")))
    }

    /** No "plugin" badge any more: the words say it. Read from the source,
     *  as the band is a composable a unit test can't run. */
    @Test fun theBandHasNoSeparatePluginBadge() {
        val rel = "src/main/java/com/domovoi/app/ui/components/PluginFrame.kt"
        val source = (listOf(java.io.File(rel), java.io.File("app/$rel")).firstOrNull { it.isFile }
            ?: error("cannot find $rel")).readText()
        assertFalse(source.contains("\"plugin\","))
        assertFalse(Regex("""Text\(\s*"plugin"""").containsMatchIn(source))
        assertTrue(source.contains("pluginFrameLine(plugin)"))
    }
}
