package com.domovoi.app.net

import androidx.compose.runtime.Composable
import androidx.compose.runtime.staticCompositionLocalOf
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Client for `GET /api/capabilities` — the Android capability manifest
 * (design §8). The web process serves it from data it already has, so it
 * answers even while the core is down.
 *
 * The app fetches it at connect (and again on WS reconnect) and gates
 * compiled-in screens on it. Two of the app's own screens, Stations and
 * Images, talk to two particular plugins of ours (a plugin can't ship app
 * code), so each shows only while the server runs THAT plugin: the slug
 * and publisher in [AppScreenPlugins], declaring the screen's capability
 * ([CapabilityPlugin.serves]). Another plugin declaring `"stations"` does
 * not light Stations up. Sidebar badges, Home's tiles, the phone bar, the
 * manual's pieces and the player's station items all follow [has]. The
 * server lists only enabled plugins that loaded (status ok or degraded;
 * web/backend/api/capabilities.py), so a disabled or failed plugin is
 * already absent. Unknown capability strings are ignored (forward
 * compat). The route table stays a compiled-in enum — only *visibility*
 * is data-driven.
 *
 * Everything is defensive: if the endpoint is missing or the fetch fails,
 * the app falls back to [Capabilities.EMPTY], which hides all gated
 * screens and renders neutral tones. The one gate that reads a server
 * switch rather than a plugin, Chat on `features.ai`, fails open: Chat
 * hides only when the server says AI is off.
 */

/** Well-known Android capability slugs. */
const val CAP_STATIONS = "stations"
const val CAP_IMAGEGEN = "imagegen"

/** The publisher our own plugins name in their manifests (`publisher =`
 *  in domovoi-plugin.toml). A manifest string, not a signature: enough to
 *  tell our plugins from someone else's that declares the same capability,
 *  which is all the app's screens need. */
const val OUR_PUBLISHER = "Coders Farm"

/** One of the app's own screens and the plugin of ours it talks to: shown
 *  only while the server lists a plugin with exactly this [slug] and
 *  [publisher] that declares [capability]. */
data class AppScreenPlugin(val capability: String, val slug: String, val publisher: String = OUR_PUBLISHER)

/** THE rule, in one place: which plugin each capability-gated screen needs.
 *  Stations talks to `/api/plugins/radio/`, Images to `/api/plugins/imagegen/`;
 *  a capability with no row here lights nothing up (fail closed). */
val AppScreenPlugins: Map<String, AppScreenPlugin> = listOf(
    AppScreenPlugin(CAP_STATIONS, slug = "radio"),
    AppScreenPlugin(CAP_IMAGEGEN, slug = "imagegen"),
).associateBy { it.capability }

/** The row for our plugin [slug], or null when no app screen needs it. */
fun appScreenPluginForSlug(slug: String): AppScreenPlugin? =
    AppScreenPlugins.values.firstOrNull { it.slug == slug }

/** `features.ai`: the household's "AI features" switch on the server (the
 *  chat models). `false` = turned off, so the server's Chat (and its chat
 *  mic) can't answer and the app hides it ([Capabilities.aiEnabled]). Not
 *  `features.chat`, which is the voice open-mic chat mode. */
const val FEATURE_AI = "ai"

@Serializable
data class CapabilityPlugin(
    val slug: String = "",
    val version: String? = null,
    /** For the plugin screen frame (ui/components/PluginFrame.kt): the
     *  manifest's name and publisher, and the page colour per theme. Every
     *  release from 1.0.0 sends name and publisher; the gate ([serves])
     *  needs the publisher. No accent (an older server): the frame keeps
     *  the app's own colours. */
    val name: String? = null,
    val publisher: String? = null,
    val accent: PluginAccent? = null,
    @SerialName("android_capabilities") val androidCapabilities: List<String> = emptyList(),
) {
    /** Whether this is the plugin of ours the app's [capability] screen
     *  talks to ([AppScreenPlugins]): the exact slug and publisher, and it
     *  declares the capability. No publisher (a server older than 1.0.0)
     *  is not ours: the screen stays hidden. */
    fun serves(capability: String): Boolean {
        val want = AppScreenPlugins[capability] ?: return false
        return slug == want.slug && publisher == want.publisher && capability in androidCapabilities
    }
}

/** A plugin's accent as the server derived it (plugins_runtime/accent.py):
 *  per theme, `brand`, `brand_hover`, `brand_press`, `brand_fg`,
 *  `brand_soft`, `brand_halo`, `brand_ring`, `brand_ink` as `#rrggbb` or
 *  `#rrggbbaa`, already contrast-checked against the dashboard's surfaces
 *  (the app's are the same tokens). `source` is "plugin" or "default"
 *  (the neutral plugin slate). */
@Serializable
data class PluginAccent(
    val source: String = "default",
    val light: Map<String, String> = emptyMap(),
    val dark: Map<String, String> = emptyMap(),
)

/** One handler's display metadata: server-supplied label + tone slug.
 *  Tone slugs are the open set neutral|media|device|info|comms; anything
 *  unknown renders as neutral. */
@Serializable
data class HandlerDisplay(
    val name: String = "",
    val label: String? = null,
    val tone: String = "neutral",
)

@Serializable
data class Capabilities(
    @SerialName("domovoi_api") val domovoiApi: String? = null,
    @SerialName("server_version") val serverVersion: String? = null,
    val plugins: List<CapabilityPlugin> = emptyList(),
    @SerialName("handler_display") val handlerDisplay: List<HandlerDisplay> = emptyList(),
    val features: Map<String, Boolean> = emptyMap(),
    /** Not part of the manifest: whether Friend Houses shows on this
     *  server, from `GET /api/friend-houses` (friendhouses/FhGate). The
     *  shell sets it on the manifest it provides, so every launcher and the
     *  screen router read one gate. False until that read says "on". */
    @kotlinx.serialization.Transient val friendHouses: Boolean = false,
) {
    /** True when the server runs the plugin of ours that the app's
     *  [capability] screen uses ([CapabilityPlugin.serves]). Every gate on
     *  Stations and Images asks this. */
    fun has(capability: String): Boolean = pluginFor(capability) != null

    /** The plugin of ours the [capability] screen uses, or null when the
     *  server doesn't run it ([has]). */
    fun pluginFor(capability: String): CapabilityPlugin? =
        plugins.firstOrNull { it.serves(capability) }

    /** For things that name a plugin by its slug (a manual piece): null
     *  when no app screen needs [slug], else whether the server runs that
     *  plugin of ours, by the same rule as [has]. */
    fun ourPluginPresent(slug: String): Boolean? =
        appScreenPluginForSlug(slug)?.let { has(it.capability) }

    /** Server-supplied tone slug for a handler/source name; `"neutral"`
     *  when the name is unknown (design §8). */
    fun toneFor(name: String?): String =
        handlerDisplay.firstOrNull { it.name == name }?.tone ?: "neutral"

    /** Server-supplied label for a handler/source name, else the raw name. */
    fun labelFor(name: String?): String? =
        handlerDisplay.firstOrNull { it.name == name }?.label ?: name

    /** False only when the server says its AI features are off
     *  (`features.ai == false`). Absent means on: an older server has no
     *  switch, and a manifest that failed to load ([EMPTY]) must not hide
     *  Chat on a server that has its models. */
    val aiEnabled: Boolean
        get() = features[FEATURE_AI] != false

    companion object {
        val EMPTY = Capabilities()
    }
}

/** The manifest for the currently connected server. Defaults to
 *  [Capabilities.EMPTY] (gated screens hidden) until the fetch lands. */
val LocalCapabilities = staticCompositionLocalOf { Capabilities.EMPTY }

/** A manifest as it loaded, with the server address it came from (Prefs'
 *  `serverUrl` when the fetch began). */
data class LoadedManifest(val server: String, val caps: Capabilities)

/**
 * The manifest that holds for [activeServer]: the last one loaded, but only
 * when it came from that server; [Capabilities.EMPTY] (gated screens hidden)
 * otherwise. The shared fetch helper ([rememberApi]) keeps its last answer
 * when its key (the server address) changes and while a new fetch runs or
 * after it fails, so on a server switch the PREVIOUS server's manifest would
 * otherwise stay in force: its Stations on a server without our Radio. Now
 * a switch is EMPTY until the new server's manifest loads, and stays EMPTY
 * if that fetch fails. The same server's own manifest outlives a failed
 * re-fetch (a socket reconnect), as before.
 */
fun manifestFor(loaded: LoadedManifest?, activeServer: String): Capabilities =
    loaded?.takeIf { it.server == activeServer }?.caps ?: Capabilities.EMPTY

/** Whether the phone's queued stations should go ([com.domovoi.app.player.PlayerController.dropStations]):
 *  only on a manifest that actually LOADED for [activeServer] and lacks our
 *  Radio. Never on the EMPTY of a server switch, a failed fetch or one not
 *  landed yet (a blip must not stop the radio; a move to the same server at
 *  another address must not wipe its queue). */
fun dropsStations(loaded: LoadedManifest?, activeServer: String): Boolean =
    loaded != null && loaded.server == activeServer && !loaded.caps.has(CAP_STATIONS)

/** Fetch the capability manifest for the active server, tagged with that
 *  server ([LoadedManifest]; read it through [manifestFor]). Errors leave
 *  `data` as it was (null at first); [manifestFor] treats a missing or
 *  other server's manifest as [Capabilities.EMPTY].
 *
 *  Through [ApiClient.getUntracked], not `get`: `/api/capabilities` is an
 *  **Open** route, so it answers 200 to an unpaired phone — and this fetch
 *  runs at connect and again on every socket reconnect, exactly when a
 *  household read's 401 has raised the pairing screen. A tracked 200 clears
 *  `pairingRequired`, so the person watching would see the pairing screen
 *  appear and then vanish with nothing paired. That is the flicker
 *  [ApiClient.getUntracked] was written for (found 2026-10-29 while porting
 *  this layer to iOS, which does it the other way round). */
@Composable
fun rememberCapabilities(): ApiState<LoadedManifest> =
    rememberApi {
        val server = it.prefs.serverUrl.value
        LoadedManifest(server, it.api.getUntracked("/api/capabilities").decode<Capabilities>())
    }
