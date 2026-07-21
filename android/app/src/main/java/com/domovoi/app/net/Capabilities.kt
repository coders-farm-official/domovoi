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
 * screens and renders neutral tones.
 */

/** Well-known Android capability slugs. */
const val CAP_STATIONS = "stations"

@Serializable
data class CapabilityPlugin(
    val slug: String = "",
    val version: String? = null,
    @SerialName("android_capabilities") val androidCapabilities: List<String> = emptyList(),
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

    /** Server-supplied tone slug for a handler/source name; `"neutral"`
     *  when the name is unknown (design §8). */
    fun toneFor(name: String?): String =
        handlerDisplay.firstOrNull { it.name == name }?.tone ?: "neutral"

    /** Server-supplied label for a handler/source name, else the raw name. */
    fun labelFor(name: String?): String? =
        handlerDisplay.firstOrNull { it.name == name }?.label ?: name

    companion object {
        val EMPTY = Capabilities()
    }
}

/** The manifest for the currently connected server. Defaults to
 *  [Capabilities.EMPTY] (gated screens hidden) until the fetch lands. */
val LocalCapabilities = staticCompositionLocalOf { Capabilities.EMPTY }

/** Fetch the capability manifest for the active server. Errors decode to
 *  `data == null`; callers treat that as [Capabilities.EMPTY]. */
@Composable
fun rememberCapabilities(): ApiState<Capabilities> =
    rememberApi {
        it.api.get("/api/capabilities").decode<Capabilities>()
    }
