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
 * compiled-in screens on it: the Stations route renders only when some
 * installed plugin declares the `"stations"` capability; sidebar badges
 * for gated routes are skipped when the capability is absent. Unknown
 * capability strings are ignored (forward compat). The route table stays
 * a compiled-in enum — only *visibility* is data-driven.
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
     *  manifest's name and publisher, and the page colour per theme. Absent
     *  from an older server: the frame then shows the slug and keeps the
     *  app's own colours. */
    val name: String? = null,
    val publisher: String? = null,
    val accent: PluginAccent? = null,
    @SerialName("android_capabilities") val androidCapabilities: List<String> = emptyList(),
)

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
) {
    /** True when any installed plugin declares [capability]. */
    fun has(capability: String): Boolean =
        plugins.any { capability in it.androidCapabilities }

    /** The plugin that provides [capability] (the first, as [has] counts). */
    fun pluginFor(capability: String): CapabilityPlugin? =
        plugins.firstOrNull { capability in it.androidCapabilities }

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

/** Fetch the capability manifest for the active server. Errors decode to
 *  `data == null`; callers treat that as [Capabilities.EMPTY].
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
fun rememberCapabilities(): ApiState<Capabilities> =
    rememberApi {
        it.api.getUntracked("/api/capabilities").decode<Capabilities>()
    }
