package com.domovoi.app.ui.shell

import com.domovoi.app.net.CAP_IMAGEGEN
import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.CapabilityPlugin
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayKind
import com.domovoi.app.testing.ourCaps
import com.domovoi.app.testing.ourImagegen
import com.domovoi.app.testing.ourRadio
import com.domovoi.app.ui.screens.chat.DeviceAction
import com.domovoi.app.ui.screens.chat.parseDeviceActions
import com.domovoi.app.ui.screens.home.everythingTiles
import com.domovoi.app.ui.screens.manual.ManualDoc
import com.domovoi.app.ui.screens.manual.phrasesByHandler
import com.domovoi.app.ui.screens.manual.shownManualPieces
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Every place the app offers Stations or Images, or mentions them, follows
 * the one rule (net/Capabilities.kt AppScreenPlugins; PluginGateTest): on a
 * server without our plugin — none at all, someone else's declaring the
 * same capability, ours without a publisher (older server) — nothing shows.
 */
class PluginEntryPointsTest {

    private val thirdParty = Capabilities(
        plugins = listOf(
            CapabilityPlugin(slug = "fm-tuner", publisher = "Elsewhere Labs", androidCapabilities = listOf(CAP_STATIONS, CAP_IMAGEGEN)),
            ourRadio.copy(publisher = "Elsewhere Labs"),
            ourImagegen.copy(slug = "imagegen-plus"),
        ),
    )
    private val olderServer = Capabilities(plugins = listOf(ourRadio.copy(publisher = null), ourImagegen.copy(publisher = null)))
    private val withoutOurs = listOf(Capabilities.EMPTY, thirdParty, olderServer)
    private val ours = ourCaps(CAP_STATIONS, CAP_IMAGEGEN)
    private val gated = setOf(Route.Stations, Route.Images)

    private fun NavEntry.routes(): List<Route> = when (this) {
        is NavEntry.Item -> listOf(route)
        is NavEntry.Group -> routes
    }

    // ---- navigation: drawer, rail, groups, Home's grid -----------------------

    @Test fun theDrawerAndRailLeaveThemOff() {
        for (caps in withoutOurs) for (shared in listOf(false, true)) {
            val visible = WorkspaceRoutes.filter { it.visibleOn(caps, shared) }
            assertTrue(visible.none { it in gated })
            assertTrue(navTree(visible).flatMap { it.routes() }.none { it in gated })
            // A person's own sidebar naming them doesn't bring them back.
            val override = NavOverride(
                groups = listOf(NavOverrideGroup("media", "Media", listOf("stations", "imagegen", "music"))),
                top = listOf("stations", "imagegen"),
            )
            assertTrue(navTree(visible, override).flatMap { it.routes() }.none { it in gated })
        }
        val all = WorkspaceRoutes.filter { it.visibleOn(ours, false) }
        assertTrue(Route.Stations in all && Route.Images in all)
        val media = navTree(all).filterIsInstance<NavEntry.Group>().first { it.group.id == "media" }
        assertTrue(Route.Stations in media.routes && Route.Images in media.routes)
    }

    @Test fun homesEverythingGridLeavesThemOff() {
        for (caps in withoutOurs) {
            assertTrue(everythingTiles(caps, shared = false).none { it in gated })
            assertTrue(everythingTiles(caps, shared = true).none { it in gated })
        }
        assertTrue(everythingTiles(ours, shared = false).containsAll(gated))
    }

    // ---- the phone bar ---------------------------------------------------------

    @Test fun aPinnedStationsTabGoesWhenThePluginIsNotOurs() {
        val saved = listOf(Route.Home, Route.Stations, Route.Music, Route.Images)
        assertEquals(saved, PhoneBar.routes(saved, ours, shared = false))
        for (caps in withoutOurs) {
            // The others stay; the plugin tabs disappear.
            assertEquals(listOf(Route.Home, Route.Music), PhoneBar.routes(saved, caps, shared = false))
            // A bar left with too few falls back to the default.
            assertEquals(
                PhoneBar.routes(null, caps, shared = false),
                PhoneBar.routes(listOf(Route.Home, Route.Stations, Route.Images), caps, shared = false),
            )
            // Settings → App layout doesn't offer them to add.
            assertTrue(PhoneBar.Choices.filter { it.visibleOn(caps, false) }.none { it in gated })
        }
    }

    // ---- the router and its placeholder -----------------------------------------

    @Test fun theRouterRefusesThemAndItsPlaceholderNamesNeither() {
        for (caps in withoutOurs) for (r in gated) {
            assertFalse(r.visibleWith(caps))
            val (title, body) = r.unavailableNote(caps)
            val words = "$title $body".lowercase()
            for (w in listOf("station", "radio", "image", "plugin")) assertFalse("$r: '$words' says $w", w in words)
        }
        assertTrue(Route.Stations.visibleWith(ours))
        assertTrue(Route.Images.visibleWith(ours))
        // Where the shell lands when a restored route is hidden.
        assertTrue(StartRoute.visibleWith(Capabilities.EMPTY))
    }

    // ---- the manual ------------------------------------------------------------

    private val doc: ManualDoc = ManualDoc.parse(
        listOf("../../web/static/manual-content.json", "../web/static/manual-content.json")
            .map(::File).first { it.isFile }.readText(),
    )!!

    @Test fun theManualsRadioPieceFollowsTheRule() {
        val radioPieces = doc.pieces.filter { it.plugin == "radio" }.map { it.id }
        assertTrue("the manual has a piece about our Radio plugin", radioPieces.isNotEmpty())
        // Running per /api/plugins, but not ours (or no publisher to tell):
        // hidden, as the Stations screen is.
        for (caps in withoutOurs) {
            val shown = shownManualPieces(doc, setOf("radio", "imagegen", "fm-tuner"), caps)
            assertTrue(radioPieces.none { it in shown })
        }
        assertTrue(shownManualPieces(doc, setOf("radio"), ourCaps(CAP_STATIONS)).containsAll(radioPieces))
        // Pieces about no plugin are always there.
        val core = doc.pieces.filter { it.plugin == null }.map { it.id }.toSet()
        assertEquals(core, shownManualPieces(doc, emptySet(), Capabilities.EMPTY))
    }

    @Test fun everythingItCanDoBorrowsNoRadioPhrasesWithoutOurs() {
        val radioPhrases = doc.pieces.filter { it.plugin == "radio" }
            .flatMap { it.blocks }.flatMap { it.phrases }.filter { it.plugin == "radio" }.map { it.text }.toSet()
        assertTrue(radioPhrases.isNotEmpty())
        for (caps in withoutOurs) {
            assertTrue(phrasesByHandler(doc, caps).values.flatten().none { it in radioPhrases })
        }
        assertTrue(phrasesByHandler(doc, ourCaps(CAP_STATIONS)).values.flatten().any { it in radioPhrases })
    }

    // ---- the player: a reply's station, the queue ----------------------------------

    private val playStation = (
        DomovoiJson.parseToJsonElement(
            """[{"type": "play", "items": [
                  {"kind": "station", "id": 4, "title": "Jazz FM"},
                  {"kind": "track", "id": 12, "title": "Glass Harbor"}]}]""",
        ) as JsonArray
        ).map { it.jsonObject }

    @Test fun aRepliesStationIsLeftOutWithoutOurRadio() {
        val without = parseDeviceActions(playStation, stations = false).single() as DeviceAction.Play
        assertEquals(listOf(PlayKind.Library), without.items.map { it.kind })
        // The default is closed too.
        assertTrue((parseDeviceActions(playStation).single() as DeviceAction.Play).items.none { it.kind == PlayKind.Radio })
        val with = parseDeviceActions(playStation, stations = true).single() as DeviceAction.Play
        assertEquals(PlayItem.fromStation(4, "Jazz FM"), with.items.first())
        // Only a station: no play at all.
        val onlyStation = playStation.map { a ->
            kotlinx.serialization.json.JsonObject(
                a + ("items" to JsonArray(listOf((a["items"] as JsonArray).first()))),
            )
        }
        assertTrue(parseDeviceActions(onlyStation, stations = false).isEmpty())
    }
}
